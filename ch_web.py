#!/usr/bin/env python3
"""Single-user headless web frontend for CH Manager Pro; Python standard library only."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import shutil
import threading
import time
from urllib.parse import parse_qs, urlsplit

from library_core import (ArchiveCatalog, activate_archived, archive_format,
    archived_installations, apply_moves, deactivate_archived, duplicate_groups,
    install_archive, plan_moves, scan_library, unique_path, validate_source, inside,
    song_content_hash, normalized, UNKNOWN)

VERSION = '6.1-web'
ASSETS = Path(__file__).resolve().parent / 'web'


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def identifier(kind, path):
    return hashlib.sha256((kind + ':' + str(path)).encode()).hexdigest()[:32]


def duplicate_review_groups(items):
    """Compare all locations, excluding an ordinary one-game/one-backup pair."""
    from collections import defaultdict
    groups = []
    def add(category, key, members):
        if len(members) < 2: return
        if category == 'exact' and len(members) == 2 and {m['scope'] for m in members} == {'installed','archived'}:
            return
        if category == 'chart' and len({m['content_hash'] for m in members}) < 2: return
        if category == 'metadata' and len({m['chart_hash'] for m in members}) < 2: return
        groups.append(dict(id=identifier(category,key), category=category,
            kind={'exact':'Identical song content', 'chart':'Identical chart; other song content differs',
                  'metadata':'Possible duplicate: same title and artist, different charts'}[category],
            ids=[m['id'] for m in members]))
    for category, field in [('exact','content_hash'),('chart','chart_hash'),('metadata',None)]:
        buckets = defaultdict(list)
        for item in items:
            key = item[field] if field else (normalized(item['artist']),normalized(item['name']))
            if (field and not key) or (not field and (item['artist']==UNKNOWN or item['name']==UNKNOWN or not all(key))): continue
            buckets[key].append(item)
        for key, members in buckets.items(): add(category,str(key),members)
    return groups


class Service:
    def __init__(self, data_dir):
        self.data = Path(data_dir).expanduser().resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data, 0o700)
        self.config_path = self.data / 'web-config.json'
        defaults = dict(songs_dir=str(self.data / 'Songs'), backup_dir=str(self.data / 'Archives'),
                        downloads_dir=str(self.data / 'Downloads'), organization='none',
                        monitoring=False, auto_install=True)
        if self.config_path.exists(): defaults.update(json.loads(self.config_path.read_text()))
        self.config = self.validate_config(defaults)
        atomic_json(self.config_path, self.config)
        self.trash_dir = self.data / 'trash'; self.trash_dir.mkdir(exist_ok=True)
        self.upload_dir = self.data / 'incoming'; self.upload_dir.mkdir(exist_ok=True)
        self.trash_path = self.data / 'trash.json'
        self.trash = json.loads(self.trash_path.read_text()) if self.trash_path.exists() else {}
        self.catalog = None
        self.songs, self.entries, self.archives = {}, {}, {}
        self.plans = {}
        self.snapshot = dict(songs=[], archived=[], archives=[], duplicates=[], errors=[], config=self.config, trash=[])
        self.lock = threading.RLock()
        self.filesystem_lock = threading.RLock()
        self.upload_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ch-library')
        self.jobs = {}
        self.stop = threading.Event()
        self.watch_thread = None
        self.closed = False

    def validate_config(self, values):
        result = {key: values[key] for key in ('songs_dir', 'backup_dir', 'downloads_dir',
                  'organization', 'monitoring', 'auto_install')}
        if result['organization'] not in ('none', 'artist', 'author', 'game'):
            raise ValueError('Invalid organization setting')
        paths = []
        for key in ('songs_dir', 'backup_dir', 'downloads_dir'):
            path = Path(str(result[key])).expanduser()
            if not path.is_absolute(): raise ValueError('Use absolute server paths')
            path = path.resolve()
            if path == Path('/') or path == self.data or inside(self.data, path):
                raise ValueError('Choose a dedicated library folder, not the data folder or one of its parents')
            if any(inside(path, other) or inside(other, path) for other in paths):
                raise ValueError('Song, archive, and download folders must be separate and must not contain each other')
            for reserved in ('trash', 'incoming'):
                protected = self.data / reserved
                if inside(path, protected) or inside(protected, path):
                    raise ValueError('Library paths cannot overlap app trash or incoming files')
            path.mkdir(parents=True, exist_ok=True)
            if not os.access(path, os.W_OK | os.X_OK): raise ValueError(f'Folder is not writable: {path}')
            result[key] = str(path); paths.append(path)
        for key in ('monitoring', 'auto_install'):
            if type(result[key]) is not bool: raise ValueError(f'{key} must be true or false')
        return result

    def start(self):
        self.submit('refresh', {})
        self.watch_thread = threading.Thread(target=self.monitor, daemon=True)
        self.watch_thread.start()

    def close(self):
        self.stop.set()
        if self.watch_thread: self.watch_thread.join(timeout=6)
        self.executor.shutdown(wait=True)
        if self.catalog: self.catalog.close()
        self.closed = True

    def public_state(self):
        with self.lock:
            return copy.deepcopy({**self.snapshot, 'jobs': list(self.jobs.values())[-25:]})

    def submit(self, action, payload):
        with self.lock:
            if len([j for j in self.jobs.values() if j['status'] in ('queued', 'running')]) >= 10:
                raise ValueError('Operation queue is full; wait for current work to finish')
            job_id = secrets.token_hex(12)
            self.jobs[job_id] = dict(id=job_id, action=action, status='queued', message='Queued')
            if len(self.jobs) > 100:
                finished = [key for key, job in self.jobs.items() if job['status'] in ('done', 'error')]
                for key in finished[:len(self.jobs)-100]: self.jobs.pop(key)
        self.executor.submit(self.run_job, job_id, action, payload)
        return job_id

    def run_job(self, job_id, action, payload):
        with self.lock: self.jobs[job_id].update(status='running', message='Working…')
        try:
            with self.filesystem_lock:
                result = self.execute(action, payload)
            with self.lock: self.jobs[job_id].update(status='done', message=result or 'Done')
        except Exception as exc:
            with self.lock: self.jobs[job_id].update(status='error', message=str(exc))

    def selected(self, values, mapping):
        if not isinstance(values, list) or not values: raise ValueError('Select at least one item')
        if len(values) > 10000: raise ValueError('Too many selected items')
        try: return [mapping[key] for key in dict.fromkeys(values)]
        except (KeyError, TypeError): raise ValueError('Selection is stale. Refresh the library first.')

    def refresh(self, archives=False):
        root = Path(self.config['songs_dir'])
        songs, errors = scan_library(root)
        if archives or not self.catalog:
            catalog = ArchiveCatalog()
            try: entries, archive_errors = catalog.scan(self.config['backup_dir'])
            except Exception: catalog.close(); raise
            if self.catalog: self.catalog.close()
            self.catalog = catalog
            self.entries = {identifier('entry', str(e.archive) + ':' + e.relative_path): e for e in entries}
        entries = list(self.entries.values())
        content_hashes = {}
        copies, game_errors = archived_installations(entries, root, content_hashes)
        errors += self.catalog.errors + game_errors
        self.songs = {identifier('song', song.path): song for song in songs}
        self.archives = {}
        backup = Path(self.config['backup_dir']).resolve()
        for folder, dirs, files in os.walk(backup, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_symlink())
            for filename in sorted(files):
                path = Path(folder) / filename
                if path.is_symlink() or not archive_format(path): continue
                self.archives[identifier('archive', path)] = path
        def describe(song):
            return dict(name=song.name, artist=song.artist, author=song.author, game=song.game,
                        author_evidence=song.author_evidence, game_evidence=song.game_evidence,
                        warnings=song.warnings)
        duplicate_items = []
        for key, song in self.songs.items():
            try:
                fingerprint = content_hashes.get(song.path) or song_content_hash(song.path)
            except (OSError,ValueError) as exc:
                errors.append(f'{song.path}: {exc}'); fingerprint = ''
            duplicate_items.append(dict(id=key, scope='installed', path=str(song.path.relative_to(root.resolve())),
                chart_hash=song.chart_hash, content_hash=fingerprint,
                format='SNG' if song.path.is_file() else 'Folder', **describe(song)))
        for key, entry in self.entries.items():
            duplicate_items.append(dict(id=key, scope='archived', archive=str(entry.archive.relative_to(backup)),
                archive_path=entry.relative_path, archive_id=identifier('archive',entry.archive),
                chart_hash=entry.song.chart_hash, content_hash=entry.content_hash,
                installed=len(copies.get(entry.content_hash,[])),
                format='SNG' if entry.song.path.is_file() else 'Folder', **describe(entry.song)))
        groups = duplicate_review_groups(duplicate_items)
        with self.lock:
            self.snapshot = dict(config=dict(self.config), errors=list(dict.fromkeys(errors)), duplicates=groups, duplicate_items=duplicate_items,
                songs=[dict(id=key, path=str(s.path.relative_to(root.resolve())), **describe(s)) for key, s in self.songs.items()],
                archived=[dict(id=key, archive=str(e.archive.relative_to(backup)), archive_path=e.relative_path,
                    installed=len(copies.get(e.content_hash, [])), installed_paths=[str(p.relative_to(root.resolve())) for p in copies.get(e.content_hash, [])],
                    **describe(e.song)) for key, e in self.entries.items()],
                archives=[dict(id=key, path=str(path.relative_to(backup)), bytes=path.stat().st_size) for key, path in self.archives.items()],
                trash=[dict(id=key, original=item['original'], removed=item['removed']) for key, item in self.trash.items()])
        return f'Refreshed {len(songs)} installed songs and {len(entries)} archived song entries'

    def quarantine(self, source):
        source = Path(source)
        key = secrets.token_hex(16)
        target = self.trash_dir / key
        record = dict(original=str(source), removed=time.time())
        # Persist the destination record before moving, for recovery after interruption.
        self.trash[key] = record; atomic_json(self.trash_path, self.trash)
        try: shutil.move(str(source), str(target))
        except Exception:
            if not target.exists(): self.trash.pop(key); atomic_json(self.trash_path, self.trash)
            raise
        return True

    def import_file(self, source, install=None):
        install = self.config['auto_install'] if install is None else install
        if type(install) is not bool: raise ValueError('Install choice must be true or false')
        # Validate even archive-only imports; this also rejects unsupported/unsafe archives.
        if install:
            installed = install_archive(source, self.config['songs_dir'], self.config['organization'])
        else:
            from library_core import extract_archive
            import tempfile
            with tempfile.TemporaryDirectory(prefix='ch-web-validate-') as temporary:
                extract_archive(source, temporary)
                songs, errors = scan_library(temporary)
                if not songs or errors: raise ValueError('Archive has no readable supported songs')
            installed = []
        backup = Path(self.config['backup_dir'])
        target = unique_path(backup / Path(source).name)
        try: shutil.move(str(source), str(target))
        except Exception as exc:
            raise OSError(f'{len(installed)} songs installed, but moving the original archive failed: {exc}') from exc
        return len(installed)

    def execute(self, action, payload):
        if action == 'refresh': return self.refresh(bool(payload.get('archives', True)))
        if action == 'settings':
            candidate = dict(self.config); candidate.update(payload)
            validated = self.validate_config(candidate)
            atomic_json(self.config_path, validated)
            self.config = validated; self.plans.clear()
            return self.refresh(True)
        if action == 'install':
            entries = self.selected(payload.get('ids'), self.entries)
            count = len(activate_archived(entries, self.config['songs_dir'], self.config['organization']))
            self.refresh(); return f'Installed {count} song folders. Run Scan Songs in Clone Hero.'
        if action in ('remove_archived', 'remove_songs', 'remove_archives'):
            if payload.get('confirm') is not True: raise ValueError('Removal needs confirmation')
            if action == 'remove_archived':
                entries = self.selected(payload.get('ids'), self.entries)
                # Exact paths from the browser's reviewed snapshot are required.
                expected = payload.get('expected_paths')
                if not isinstance(expected, list): raise ValueError('Refresh and review matching paths first')
                root = Path(self.config['songs_dir']).resolve()
                paths = [root / p for p in expected]
                count = deactivate_archived(entries, root, self.quarantine, paths)
            elif action == 'remove_songs':
                songs = self.selected(payload.get('ids'), self.songs)
                root = Path(self.config['songs_dir']).resolve()
                for song in songs: validate_source(song.path, root)
                for song in songs: self.quarantine(song.path)
                count = len(songs)
            else:
                paths = self.selected(payload.get('ids'), self.archives)
                root = Path(self.config['backup_dir']).resolve()
                for path in paths:
                    if path.is_symlink() or not inside(path.resolve(), root) or not path.is_file():
                        raise ValueError('Archive changed; refresh first')
                for path in paths: self.quarantine(path)
                count = len(paths)
            self.refresh(action == 'remove_archives')
            return f'Moved {count} items to app Trash. Original song archives are kept when removing game copies.'
        if action == 'restore':
            keys = payload.get('ids')
            self.selected(keys, self.trash)
            for key in dict.fromkeys(keys):
                record = self.trash[key]; original = Path(record['original'])
                if original.exists() or original.is_symlink(): raise ValueError(f'Restore destination already exists: {original}')
                target = self.trash_dir / key
                if not target.exists(): raise ValueError('Trash payload is missing; inspect the data directory')
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(original)); self.trash.pop(key)
                atomic_json(self.trash_path, self.trash)
            self.refresh(True); return 'Restored selected files. Run Scan Songs in Clone Hero.'
        if action == 'apply_moves':
            key = payload.get('plan')
            plan = self.plans.pop(key, None)
            if not plan or time.time() - plan['created'] > 600: raise ValueError('Preview expired; create a new preview')
            if payload.get('confirm') is not True: raise ValueError('Review and confirm the move preview')
            count = apply_moves(plan['moves'], self.config['songs_dir'])
            self.refresh(); return f'Moved {count} songs. Run Scan Songs in Clone Hero.'
        if action == 'watch_import':
            source = Path(payload['path'])
            if source.is_symlink() or source.parent.resolve() != Path(self.config['downloads_dir']).resolve():
                raise ValueError('Download folder changed or source is a symbolic link')
            if not self.config['monitoring']: return 'Monitoring paused; import skipped'
            count = self.import_file(source)
            self.refresh(True); return f'{source.name}: archived; {count} songs installed'
        if action == 'import':
            filename = payload.get('upload')
            if not isinstance(filename, str) or Path(filename).name != filename: raise ValueError('Invalid upload')
            source = self.upload_dir / filename
            if source.is_symlink() or not source.is_file(): raise ValueError('Uploaded archive is missing')
            count = self.import_file(source, payload.get('install'))
            self.refresh(True); return f'Archive saved; {count} song folders installed.'
        raise ValueError('Unknown operation')

    def preview(self, payload):
        with self.filesystem_lock:
            songs = self.selected(payload.get('ids'), self.songs)
            moves = plan_moves(songs, self.config['songs_dir'], payload.get('mode', 'artist'), payload.get('custom', ''))
            key = secrets.token_hex(16)
            self.plans = {k: p for k, p in self.plans.items() if time.time() - p['created'] < 600}
            self.plans[key] = dict(created=time.time(), moves=moves)
            root = Path(self.config['songs_dir']).resolve()
            return dict(plan=key, moves=[dict(source=str(a.relative_to(root)), target=str(b.relative_to(root))) for a, b in moves])

    def monitor(self):
        observed, attempted = {}, {}
        while not self.stop.wait(5):
            with self.lock:
                config = dict(self.config)
                busy = any(job['status'] in ('queued', 'running') for job in self.jobs.values())
            if not config['monitoring'] or busy: continue
            try:
                current = {}
                for path in Path(config['downloads_dir']).iterdir():
                    if path.is_symlink() or not path.is_file() or not archive_format(path): continue
                    stat = path.stat(); stamp = (stat.st_size, stat.st_mtime_ns)
                    current[str(path)] = stamp
                    if stat.st_size and observed.get(str(path)) == stamp and attempted.get(str(path)) != stamp:
                        attempted[str(path)] = stamp
                        # Move only an observed file under the configured download folder.
                        with self.filesystem_lock:
                            if config['downloads_dir'] != self.config['downloads_dir'] or not self.config['monitoring']: break
                            if (path.stat().st_size, path.stat().st_mtime_ns) != stamp: continue
                            self.submit_watch(path)
                        break
                observed = current
            except OSError: continue

    def submit_watch(self, path):
        return self.submit('watch_import', {'path': str(path)})


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, service, token, allowed_hosts=None, secure_cookie=False, max_upload=2 * 1024**3):
        self.service, self.token = service, token
        self.sessions = {}; self.session_lock = threading.Lock()
        self.allowed_hosts = set(allowed_hosts or ['localhost', '127.0.0.1', '[::1]'])
        self.secure_cookie, self.max_upload = secure_cookie, max_upload
        self.login_attempts = {}
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'CHManagerWeb'
    def log_message(self, *_): pass  # Never log authorization material or uploaded filenames.

    def respond(self, status, content, kind='application/json', headers=None):
        if kind == 'application/json': content = json.dumps(content).encode()
        elif isinstance(content, str): content = content.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers()
        try: self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError): pass

    def safe_origin(self):
        host = self.headers.get('Host', '')
        parsed = urlsplit('//' + host)
        if not parsed.hostname or (parsed.hostname not in self.server.allowed_hosts and f'[{parsed.hostname}]' not in self.server.allowed_hosts):
            return False
        origin = self.headers.get('Origin')
        if origin:
            parsed_origin = urlsplit(origin)
            if parsed_origin.scheme not in ('http', 'https') or parsed_origin.netloc != host: return False
        return True

    def session(self):
        cookie = cookies.SimpleCookie()
        try: cookie.load(self.headers.get('Cookie', ''))
        except cookies.CookieError: return None
        sid = cookie.get('ch_session')
        with self.server.session_lock:
            now = time.time()
            self.server.sessions = {k:v for k,v in self.server.sessions.items() if v['expires'] > now}
            return self.server.sessions.get(sid.value) if sid else None

    def read_json(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 0 or length > 1024 * 1024: raise ValueError('Request body too large')
        if self.headers.get_content_type() != 'application/json': raise ValueError('Expected JSON')
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict): raise ValueError('Expected a JSON object')
        return body

    def do_GET(self):
        try:
            if not self.safe_origin(): return self.respond(403, {'error':'Host not allowed. Configure --allowed-hosts.'})
            path = urlsplit(self.path).path
            assets = {'/':('index.html', 'text/html; charset=utf-8'), '/app.js':('app.js','text/javascript; charset=utf-8'), '/style.css':('style.css','text/css; charset=utf-8')}
            if path in assets:
                filename, kind = assets[path]
                return self.respond(200, (ASSETS / filename).read_bytes(), kind)
            session = self.session()
            if not session: return self.respond(401, {'error':'Sign in with the server access token'})
            if path == '/api/state':
                return self.respond(200, {**self.server.service.public_state(), 'csrf':session['csrf'], 'version':VERSION})
            return self.respond(404, {'error':'Not found'})
        except (OSError, ValueError) as exc: self.respond(400, {'error':str(exc)})

    def do_POST(self):
        self.connection.settimeout(120)
        try:
            if not self.safe_origin(): return self.respond(403, {'error':'Untrusted Host or Origin'})
            path = urlsplit(self.path).path
            if path == '/api/login':
                peer = self.client_address[0]
                with self.server.session_lock:
                    now = time.time()
                    attempts = [t for t in self.server.login_attempts.get(peer, []) if now-t < 60]
                    if len(attempts) >= 10: return self.respond(429, {'error':'Too many attempts; wait one minute'})
                    attempts.append(now); self.server.login_attempts[peer] = attempts
                body = self.read_json()
                if not secrets.compare_digest(str(body.get('token','')), self.server.token): return self.respond(401, {'error':'Invalid access token'})
                sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                with self.server.session_lock:
                    self.server.sessions[sid] = dict(csrf=csrf, expires=time.time()+12*3600)
                secure = '; Secure' if self.server.secure_cookie else ''
                return self.respond(200, {'ok':True}, headers={'Set-Cookie':f'ch_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}'})
            session = self.session()
            if not session: return self.respond(401, {'error':'Authentication required'})
            if not secrets.compare_digest(self.headers.get('X-CSRF-Token',''), session['csrf']): return self.respond(403, {'error':'Invalid CSRF token'})
            if path == '/api/logout':
                with self.server.session_lock:
                    self.server.sessions = {k:v for k,v in self.server.sessions.items() if v is not session}
                return self.respond(200, {'ok':True}, headers={'Set-Cookie':'ch_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict'})
            if path == '/api/upload': return self.upload()
            body = self.read_json()
            if path == '/api/preview': return self.respond(200, self.server.service.preview(body))
            if path == '/api/action':
                action = body.pop('action', '')
                allowed = {'refresh','settings','install','remove_archived','remove_songs','remove_archives','restore','apply_moves','import'}
                if action not in allowed: raise ValueError('Unknown operation')
                return self.respond(202, {'job':self.server.service.submit(action, body)})
            return self.respond(404, {'error':'Not found'})
        except (ValueError, OSError, json.JSONDecodeError) as exc: self.respond(400, {'error':str(exc)})
        except Exception: self.respond(500, {'error':'Unexpected server error. Refresh and retry.'})

    def upload(self):
        length = int(self.headers.get('Content-Length','0'))
        if length <= 0 or length > self.server.max_upload: return self.respond(413, {'error':'Archive exceeds configured upload limit'})
        params = parse_qs(urlsplit(self.path).query)
        filename = params.get('name',[''])[0]
        if Path(filename).name != filename or '\\' in filename or not archive_format(filename): raise ValueError('Choose a ZIP, RAR, 7z or SNG file')
        with self.server.service.upload_lock:
            path = unique_path(self.server.service.upload_dir / filename)
            try:
                with path.open('xb') as stream:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024*1024, remaining))
                        if not chunk: raise ValueError('Incomplete upload')
                        stream.write(chunk); remaining -= len(chunk)
            except Exception:
                path.unlink(missing_ok=True); raise
        return self.respond(201, {'upload':path.name})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=os.environ.get('CH_WEB_DATA', '~/.local/share/ch_manager_web'))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8899, type=int)
    parser.add_argument('--allowed-hosts', default='localhost,127.0.0.1,[::1]')
    parser.add_argument('--secure-cookie', action='store_true', help='Enable for HTTPS reverse proxy access')
    parser.add_argument('--max-upload-mib', default=2048, type=int)
    args = parser.parse_args()
    service = Service(args.data_dir)
    token_path = service.data / 'access-token'
    token = os.environ.get('CH_WEB_TOKEN')
    if not token:
        if token_path.exists(): token = token_path.read_text().strip()
        else:
            token = secrets.token_urlsafe(32)
            token_path.write_text(token + '\n'); os.chmod(token_path, 0o600)
    if len(token) < 24: raise SystemExit('CH_WEB_TOKEN must contain at least 24 characters')
    server = WebServer((args.host, args.port), service, token,
        args.allowed_hosts.split(','), args.secure_cookie, args.max_upload_mib * 1024**2)
    print(f'CH Manager Web {VERSION}: http://{args.host}:{args.port}', flush=True)
    print(f'Access token: CH_WEB_TOKEN environment variable or {token_path}', flush=True)
    service.start()
    def stop_server(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_server)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close(); service.close()


if __name__ == '__main__': main()
