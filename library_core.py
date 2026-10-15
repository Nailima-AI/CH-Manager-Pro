"""Clone Hero library operations. No GTK or network dependencies."""
from __future__ import annotations

import configparser
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass

UNKNOWN = 'Unknown'
CHARTS = {'notes.chart', 'notes.mid', 'notes.midi'}
ICON_GAMES = {
    'gh1': 'Guitar Hero', 'gh2': 'Guitar Hero II', 'gh3': 'Guitar Hero III',
    'gh80s': 'Guitar Hero Encore: Rocks the 80s', 'gha': 'Guitar Hero: Aerosmith',
    'ghwt': 'Guitar Hero World Tour', 'ghm': 'Guitar Hero: Metallica',
    'ghsh': 'Guitar Hero Smash Hits', 'gh5': 'Guitar Hero 5',
    'ghvh': 'Guitar Hero: Van Halen', 'ghwor': 'Guitar Hero: Warriors of Rock',
    'rb1': 'Rock Band', 'rb2': 'Rock Band 2', 'rb3': 'Rock Band 3',
    'rb4': 'Rock Band 4', 'tbrb': 'The Beatles: Rock Band',
    'gdrb': 'Green Day: Rock Band', 'lrb': 'LEGO Rock Band',
}


def clean(value):
    return re.sub(r'<[^>]*>', '', str(value)).strip().strip('"')


def normalized(value):
    return re.sub(r'[^\w]+', '', unicodedata.normalize('NFKC', clean(value)).casefold())


def component(value):
    value = re.sub(r'[\x00-\x1f/\\:*?"<>|]', '_', clean(value)).strip(' .')
    return value[:120] or UNKNOWN


@dataclass(frozen=True)
class Song:
    path: Path
    name: str
    artist: str
    author: str
    author_evidence: str
    game: str
    game_evidence: str
    chart_hash: str
    warnings: tuple = ()


class SngReader:
    """Read SNGPKG v1 using the public mdsitton/SngFileFormat specification.

    Indexes are bounded before allocation; payloads are decoded in streaming chunks.
    This reader never executes or extracts the embedded files.
    """
    TABLES = tuple(bytes(b ^ key for b in range(256)) for key in range(256))

    def __init__(self, path):
        self.path = Path(path)
        self.metadata, self.files = {}, {}
        size = self.path.stat().st_size
        with self.path.open('rb') as stream:
            def take(count, end=size):
                if count < 0 or stream.tell() + count > end:
                    raise ValueError('Truncated or invalid SNG section')
                data = stream.read(count)
                if len(data) != count: raise ValueError('Truncated SNG file')
                return data
            def number(count=8, end=size):
                return int.from_bytes(take(count, end), 'little')
            def section(limit):
                length = number()
                if length > limit or stream.tell() + length > size:
                    raise ValueError('Invalid or oversized SNG index section')
                return stream.tell() + length
            def text(count, end):
                try: value = take(count, end).decode('utf-8')
                except UnicodeDecodeError as exc: raise ValueError('Invalid UTF-8 in SNG') from exc
                if '\x00' in value: raise ValueError('NUL in SNG text')
                return value
            if take(6) != b'SNGPKG': raise ValueError('Not a Clone Hero SNGPKG file')
            if number(4) != 1: raise ValueError('Unsupported SNG version (supported: 1)')
            self.mask = take(16)
            end = section(16 * 1024**2)
            count = number(end=end)
            if count > 10000: raise ValueError('Too many SNG metadata fields')
            for _ in range(count):
                key = text(number(4, end), end).lower()
                value = text(number(4, end), end)
                if not key or any(c in key for c in '\r\n=;[]') or any(c in value for c in '\r\n'):
                    raise ValueError('SNG metadata cannot safely represent song.ini')
                if key in self.metadata: raise ValueError('Duplicate SNG metadata key')
                self.metadata[key] = value
            if stream.tell() != end: raise ValueError('Invalid SNG metadata length')
            end = section(16 * 1024**2)
            count = number(end=end)
            if count > 100000: raise ValueError('Too many SNG file entries')
            seen = set()
            for _ in range(count):
                name = text(number(1, end), end)
                safe_member(name)
                if not name or '\\' in name or any(ord(c) < 32 for c in name):
                    raise ValueError('Invalid SNG member name')
                parts = PurePosixPath(name).parts
                if any(p in ('', '.', '..') for p in name.split('/')) or any(':' in p for p in parts):
                    raise ValueError('Invalid SNG member path')
                if name.casefold() in seen:
                    raise ValueError('Duplicate SNG file entry')
                if Path(name).suffix.lower() in ('.exe','.bat','.sh','.cmd','.com','.ps1'):
                    raise ValueError('Unsafe file in SNG')
                seen.add(name.casefold())
                length, offset = number(end=end), number(end=end)
                self.files[name] = (offset, length)
            if stream.tell() != end: raise ValueError('Invalid SNG file-index length')
            data_length = number()
            data_start = stream.tell()
            if data_start + data_length != size: raise ValueError('Invalid SNG data section length')
            ranges = sorted((offset, offset+length) for offset,length in self.files.values())
            previous = data_start
            for start, finish in ranges:
                if start < previous or finish > size: raise ValueError('Overlapping or out-of-range SNG data')
                previous = finish
            names = {name.casefold() for name in self.files}
            if not CHARTS & names: raise ValueError('SNG has no supported root chart')
            if 'song.ini' in names: raise ValueError('SNG song.ini conflicts with container metadata')
            for name in names:
                if any('/'.join(name.split('/')[:i]) in names for i in range(1,len(name.split('/')))):
                    raise ValueError('Conflicting SNG member paths')

    def ini_bytes(self):
        return ('[song]\n' + ''.join(f'{k} = {v}\n' for k,v in sorted(self.metadata.items()))).encode('utf-8')

    def chunks(self, name):
        offset, remaining = self.files[name]
        position = 0
        with self.path.open('rb') as stream:
            stream.seek(offset)
            while remaining:
                data = stream.read(min(1024*1024, remaining))
                if not data: raise ValueError('SNG changed or was truncated during reading')
                decoded = bytearray(len(data))
                for i in range(min(256, len(data))):
                    key = self.mask[(position+i) % 16] ^ ((position+i) & 255)
                    decoded[i::256] = data[i::256].translate(self.TABLES[key])
                yield bytes(decoded)
                position += len(data); remaining -= len(data)

    def file_hash(self, name):
        digest = hashlib.sha256()
        for chunk in self.chunks(name): digest.update(chunk)
        return digest.hexdigest()

    def content_hash(self):
        # Match the existing folder hash ordering, including generated song.ini.
        names = list(self.files) + ['song.ini']
        digest = hashlib.sha256()
        # os.walk visits root files first, then subdirectories depth-first.
        def order(prefix=''):
            direct = sorted(n for n in names if n.rpartition('/')[0] == prefix)
            yield from direct
            children = sorted({n[len(prefix)+1:].split('/')[0] if prefix else n.split('/')[0]
                               for n in names if (n.startswith(prefix+'/') if prefix else True)
                               and '/' in (n[len(prefix)+1:] if prefix else n)})
            for child in children: yield from order(prefix+'/'+child if prefix else child)
        for name in order():
            relative = name.encode('utf-8')
            file_hash = hashlib.sha256(self.ini_bytes()).hexdigest() if name == 'song.ini' else self.file_hash(name)
            digest.update(len(relative).to_bytes(8,'big')); digest.update(relative); digest.update(bytes.fromhex(file_hash))
        return digest.hexdigest()


def read_sng_song(path, root):
    reader = SngReader(path)
    # Reuse chart/INI precedence and source-game evidence without unpacking audio.
    with tempfile.TemporaryDirectory(prefix='ch-sng-metadata-') as temporary:
        folder = Path(temporary)
        (folder/'song.ini').write_bytes(reader.ini_bytes())
        for name in reader.files:
            if name.lower() in CHARTS:
                if reader.files[name][1] > 32 * 1024**2: raise ValueError('SNG chart exceeds 32 MiB metadata-scan limit')
                with (folder/name).open('wb') as target:
                    for chunk in reader.chunks(name): target.write(chunk)
        parsed = read_song(folder, folder)
    # Parent-folder evidence must come from the real file location.
    game, evidence = parsed.game, parsed.game_evidence
    if game == UNKNOWN:
        for parent in reversed(Path(path).relative_to(root).parts[:-1]):
            matches = [v for k,v in ICON_GAMES.items() if normalized(parent) in (normalized(k),normalized(v))]
            if len(matches) == 1: game,evidence=matches[0],f'Likely: parent folder {parent}'; break
    name = clean(reader.metadata.get('name','')) or (parsed.name if parsed.name != folder.name else Path(path).stem)
    return Song(Path(path), name, parsed.artist,
                parsed.author, parsed.author_evidence, game, evidence, parsed.chart_hash, parsed.warnings)


def read_song(path, root):
    files = {p.name.lower(): p for p in path.iterdir() if p.is_file() and not p.is_symlink()}
    meta, warnings = {}, []
    chart = files.get('notes.chart')
    if chart:
        # Only [Song] contains metadata; do not mistake event/note sections for fields.
        text = chart.read_text(encoding='utf-8-sig', errors='replace')
        match = re.search(r'^\[Song\]\s*\{(.*?)^\s*\}', text, re.M | re.S | re.I)
        if match:
            for line in match[1].splitlines():
                if '=' in line:
                    key, value = line.split('=', 1)
                    meta[key.strip().lower()] = clean(value)
    ini = files.get('song.ini')
    if ini:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(ini.read_text(encoding='utf-8-sig', errors='replace'))
            section = next((s for s in parser.sections() if s.lower() == 'song'), None)
            if section:
                meta.update({k.lower(): clean(v) for k, v in parser.items(section) if clean(v)})
        except configparser.Error as exc:
            warnings.append(f'Cannot parse song.ini: {exc}')
    author = meta.get('charter') or meta.get('frets') or meta.get('author') or UNKNOWN
    author_key = next((k for k in ('charter', 'frets', 'author') if meta.get(k)), None)
    game, evidence = UNKNOWN, 'No source metadata'
    for key in ('game', 'source_game', 'source'):
        if meta.get(key):
            game, evidence = meta[key], f'Metadata: {key} (unverified)'
            break
    if game == UNKNOWN:
        icon = meta.get('icon', '').lower()
        if icon in ICON_GAMES:
            game, evidence = ICON_GAMES[icon], f'Likely: icon={icon}'
    if game == UNKNOWN:
        # A pack folder is evidence of provenance, not proof of an official chart.
        for folder in reversed(path.relative_to(root).parts[:-1]):
            key = normalized(folder)
            candidates = [(code, title) for code, title in ICON_GAMES.items()
                          if key in (normalized(code), normalized(title))]
            if len(candidates) == 1:
                game, evidence = candidates[0][1], f'Likely: parent folder {folder}'
                break
    hashes = []
    for name in sorted(CHARTS & files.keys()):
        digest = hashlib.sha256()
        with files[name].open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        hashes.append(name + ':' + digest.hexdigest())
    fingerprint = hashlib.sha256('\n'.join(hashes).encode()).hexdigest() if hashes else ''
    return Song(path, meta.get('name') or path.name, meta.get('artist') or UNKNOWN,
                author, f'Metadata: {author_key}' if author_key else 'No charter metadata',
                game, evidence, fingerprint, tuple(warnings))


def scan_library(root):
    """Return recursive songs and per-path errors; never follow symlink directories."""
    root = Path(root).expanduser().resolve(strict=True)
    songs, errors = [], []
    def walk_error(exc):
        errors.append(str(exc))
    for folder, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        path = Path(folder)
        dirs[:] = sorted(d for d in dirs if not (path / d).is_symlink())
        lowered = {n.lower() for n in names if not (path / n).is_symlink()}
        for name in sorted(names):
            child = path / name
            if not child.is_symlink() and archive_format(child) == 'sng':
                try: songs.append(read_sng_song(child, root))
                except (OSError, ValueError) as exc: errors.append(f'{child}: {exc}')
        if CHARTS & lowered:
            try:
                songs.append(read_song(path, root))
            except (OSError, ValueError) as exc:
                errors.append(f'{path}: {exc}')
    return songs, errors


def duplicate_groups(songs):
    """Exact means chart bytes only; metadata matches are candidates, never deletions."""
    exact, possible = defaultdict(list), defaultdict(list)
    for song in songs:
        if song.chart_hash:
            exact[song.chart_hash].append(song)
        if song.artist != UNKNOWN and normalized(song.name) and normalized(song.artist):
            possible[normalized(song.artist), normalized(song.name)].append(song)
    groups = [('Identical chart files (audio/metadata may differ)', group)
              for group in exact.values() if len(group) > 1]
    groups += [('Possible duplicate: same title and artist (may be different charts)', group)
               for group in possible.values()
               if len(group) > 1 and len({s.chart_hash for s in group}) > 1]
    return groups


def unique_path(path, reserved=None):
    reserved = reserved if reserved is not None else set()
    result, index = path, 2
    while result.exists() or result.is_symlink() or result in reserved:
        result = path.with_name(f'{path.stem} ({index}){path.suffix}') if path.suffix.lower() in ('.zip', '.rar', '.7z', '.sng') else path.with_name(f'{path.name} ({index})')
        index += 1
    reserved.add(result)
    return result


def inside(path, root):
    return path == root or root in path.parents


def validate_source(path, root):
    path = Path(path)
    if path.is_symlink() or not inside(path.resolve(), root) or path.resolve() == root:
        raise ValueError(f'Not a movable song: {path}')
    if path.is_file():
        if archive_format(path) != 'sng': raise ValueError('Not a supported song file')
        return
    if not path.is_dir(): raise ValueError('Song is missing')
    for folder, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            child = Path(folder) / name
            if child.is_symlink():
                raise ValueError(f'Symbolic links cannot be moved: {child}')
        if (Path(folder) != path and CHARTS & {f.lower() for f in files}) or any(archive_format(f) == 'sng' for f in files):
            raise ValueError(f'Nested song folders require manual review: {path}')


def plan_moves(songs, root, mode='artist', custom=''):
    root = Path(root).expanduser().resolve(strict=True)
    if mode not in ('artist', 'author', 'game', 'custom'):
        raise ValueError('Unsupported organization mode')
    if mode == 'custom':
        relative = Path(custom.strip())
        if not custom.strip() or relative.is_absolute() or '..' in relative.parts or '\\' in custom:
            raise ValueError('Enter a relative folder inside the song library, without ..')
    moves, reserved = [], set()
    for song in songs:
        validate_source(song.path, root)
        group = relative if mode == 'custom' else Path(component(getattr(song, mode)))
        parent = root / group
        destination = parent / song.path.name
        if not inside(parent.resolve(), root):
            raise ValueError('Destination escapes library')
        if song.path.parent == parent:
            continue
        if inside(parent.resolve(), song.path.resolve()):
            raise ValueError('Cannot move a song inside itself')
        moves.append((song.path, unique_path(destination, reserved)))
    return moves


def apply_moves(moves, root):
    """Recheck the entire plan before moving; rename only, with rollback on failure."""
    root = Path(root).expanduser().resolve(strict=True)
    sources = [Path(a) for a, _ in moves]
    if len(set(sources)) != len(sources) or len({Path(b) for _, b in moves}) != len(moves):
        raise ValueError('Repeated source or destination')
    for source, target in moves:
        source, target = Path(source), Path(target)
        validate_source(source, root)
        if not inside(target.parent.resolve(), root) or target.exists() or target.is_symlink():
            raise ValueError(f'Destination changed; refresh preview: {target}')
        if any(inside(target.parent.resolve(), s.resolve()) for s in sources):
            raise ValueError('Destination overlaps a source folder')
    completed = []
    try:
        for source, target in moves:
            source, target = Path(source), Path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_file():
                # Reserve the target exclusively before replacing it with the native SNG.
                with target.open('xb'): pass
                try: source.rename(target)
                except Exception: target.unlink(); raise
                completed.append((source,target)); continue
            # mkdir, unlike shutil.move, refuses an existing destination, even empty.
            target.mkdir()
            try:
                for child in source.iterdir():
                    child.rename(target / child.name)
                source.rmdir()
            except Exception:
                for child in target.iterdir():
                    child.rename(source / child.name)
                target.rmdir()
                raise
            completed.append((source, target))
    except Exception:
        for source, target in reversed(completed):
            target.rename(source)
        raise
    return len(completed)


def safe_member(name):
    name = name.replace('\\', '/')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or re.match(r'^[A-Za-z]:', name):
        raise ValueError(f'Unsafe archive member: {name}')


def extract_archive(source, destination):
    source, destination = Path(source), Path(destination)
    if archive_format(source) == 'sng':
        SngReader(source)
        name = re.sub(r'(?i)\.sng(?: \(\d+\))?$', '', source.name) + '.sng'
        shutil.copy2(source, Path(destination) / name)
    elif archive_format(source) == 'zip':
        with zipfile.ZipFile(source) as archive:
            for entry in archive.infolist():
                safe_member(entry.filename)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError('Archive contains symbolic links')
            archive.extractall(destination)
    else:
        executable = shutil.which('7z') or shutil.which('7zz')
        if not executable:
            raise ValueError('Install 7z/7zz to import RAR and 7z archives')
        listing = subprocess.run([executable, 'l', '-slt', '--', str(source)],
                                 check=True, capture_output=True, text=True).stdout
        if '----------' not in listing:
            raise ValueError('Cannot validate archive listing')
        for block in listing.split('----------', 1)[1].split('\n\n'):
            fields = dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
            if 'Path' in fields:
                safe_member(fields['Path'])
            if any('link' in k.lower() for k in fields) or fields.get('Attributes', '').startswith('l'):
                raise ValueError('Archive contains links')
        subprocess.run([executable, 'x', '-y', f'-o{destination}', '--', str(source)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for folder, dirs, files in os.walk(destination):
        for name in dirs + files:
            item = Path(folder) / name
            if item.is_symlink() or item.suffix.lower() in ('.exe', '.bat', '.sh', '.cmd', '.com', '.ps1'):
                raise ValueError(f'Unsafe file in archive: {name}')


def copy_song(source, target):
    if Path(source).is_dir(): return shutil.copytree(source, target)
    with Path(source).open('rb') as stream, Path(target).open('xb') as output:
        shutil.copyfileobj(stream, output)
    return target


def remove_song(path):
    if Path(path).is_dir(): shutil.rmtree(path)
    else: Path(path).unlink()


def install_archive(source, root, mode='none'):
    root = Path(root).expanduser().resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='ch-manager-') as temporary:
        sandbox = Path(temporary)
        extract_archive(source, sandbox)
        songs, errors = scan_library(sandbox)
        if errors:
            raise ValueError('\n'.join(errors))
        if not songs:
            raise ValueError('Archive contains no notes.chart/notes.mid/notes.midi songs')
        installed = []
        try:
            for song in songs:
                if song.path != sandbox:
                    validate_source(song.path, sandbox)
                elif any(s.path != sandbox for s in songs):
                    raise ValueError('Archive mixes root and nested songs')
                parent = root if mode == 'none' else root / component(getattr(song, mode))
                parent.mkdir(parents=True, exist_ok=True)
                if not inside(parent.resolve(), root):
                    raise ValueError('Install destination escapes library')
                name = Path(source).stem if song.path == sandbox else song.path.name
                target = unique_path(parent / component(name))
                # copytree refuses collisions and leaves the archive available on failure.
                try:
                    copy_song(song.path, target)
                except Exception:
                    if target.exists():
                        remove_song(target)
                    raise
                installed.append(target)
        except Exception:
            for target in installed:
                remove_song(target)
            raise
    return installed


@dataclass(frozen=True)
class ArchivedSong:
    archive: Path
    relative_path: str
    song: Song
    content_hash: str


def archive_format(path):
    """Include numbered backups written by earlier versions (pack.zip (2))."""
    match = re.search(r'\.(zip|rar|7z|sng)(?: \(\d+\))?$', str(path), re.I)
    return match[1].lower() if match else None


def song_content_hash(path):
    """Fingerprint every file and relative filename, independent of song-folder location."""
    path = Path(path)
    if path.is_file(): return SngReader(path).content_hash()
    digest = hashlib.sha256()
    for folder, dirs, files in os.walk(path, followlinks=False):
        dirs.sort()
        for name in dirs + files:
            if (Path(folder) / name).is_symlink():
                raise ValueError(f'Cannot fingerprint symbolic link: {name}')
        for name in sorted(files):
            child = Path(folder) / name
            relative = child.relative_to(path).as_posix().encode('utf-8')
            file_digest = hashlib.sha256()
            with child.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    file_digest.update(chunk)
            digest.update(len(relative).to_bytes(8, 'big'))
            digest.update(relative)
            digest.update(file_digest.digest())
    return digest.hexdigest()


class ArchiveCatalog:
    """Window-scoped extracted catalog; source archives are always kept untouched."""
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ch-archive-library-')
        self.entries = []
        self.errors = []

    def close(self):
        self.temporary.cleanup()

    def scan(self, backup_root):
        root = Path(backup_root).expanduser().resolve(strict=True)
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_symlink())
            for name in sorted(files):
                archive = Path(folder) / name
                if archive.is_symlink() or not archive_format(archive): continue
                if archive_format(archive) == 'sng':
                    try:
                        song = read_sng_song(archive, root)
                        self.entries.append(ArchivedSong(archive, '.', song, song_content_hash(archive)))
                    except (OSError, ValueError) as exc: self.errors.append(f'{archive.name}: {exc}')
                    continue
                sandbox = Path(self.temporary.name) / str(len(list(Path(self.temporary.name).iterdir())))
                sandbox.mkdir()
                try:
                    extract_archive(archive, sandbox)
                    songs, errors = scan_library(sandbox)
                    self.errors.extend(f'{archive.name}: {error}' for error in errors)
                    if not songs: self.errors.append(f'{archive.name}: No supported song charts found')
                    for song in songs:
                        if song.path == sandbox:
                            if len(songs) != 1: raise ValueError('Root and nested songs are mixed')
                        else:
                            validate_source(song.path, sandbox)
                        self.entries.append(ArchivedSong(archive, song.path.relative_to(sandbox).as_posix(),
                                                         song, song_content_hash(song.path)))
                except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile, RuntimeError) as exc:
                    self.errors.append(f'{archive.name}: {exc}')
        return self.entries, self.errors


def archived_installations(entries, game_root, content_hashes=None):
    songs, errors = scan_library(game_root)
    relevant_charts = {entry.song.chart_hash for entry in entries}
    copies = defaultdict(list)
    for song in songs:
        if song.chart_hash not in relevant_charts: continue
        try:
            validate_source(song.path, Path(game_root).expanduser().resolve(strict=True))
            fingerprint = song_content_hash(song.path)
            if content_hashes is not None: content_hashes[song.path] = fingerprint
            copies[fingerprint].append(song.path)
        except (OSError, ValueError) as exc:
            errors.append(f'{song.path}: {exc}')
    return dict(copies), errors


def activate_archived(entries, game_root, mode='none'):
    """Install individual archived songs; existing byte-identical copies are skipped."""
    if mode not in ('none', 'artist', 'author', 'game'): raise ValueError('Unsupported organization mode')
    root = Path(game_root).expanduser().resolve(strict=True)
    copies, errors = archived_installations(entries, root)
    if errors: raise ValueError('Cannot safely determine installed state:\n' + '\n'.join(errors))
    installed = []
    try:
        for entry in entries:
            if copies.get(entry.content_hash): continue
            if song_content_hash(entry.song.path) != entry.content_hash:
                raise ValueError('Archive catalog changed. Rescan archives first.')
            parent = root if mode == 'none' else root / component(getattr(entry.song, mode))
            if not inside(parent.resolve(), root): raise ValueError('Destination escapes game library')
            parent.mkdir(parents=True, exist_ok=True)
            name = entry.song.path.name if entry.relative_path != '.' or entry.song.path.is_file() else re.sub(r'\.(zip|rar|7z|sng)(?: \(\d+\))?$', '', entry.archive.name, flags=re.I)
            if entry.song.path.is_file(): name = re.sub(r'(?i)\.sng(?: \(\d+\))?$', '', name) + '.sng'
            target = unique_path(parent / component(name))
            try:
                copy_song(entry.song.path, target)
            except Exception:
                if target.exists(): remove_song(target)
                raise
            installed.append(target)
            copies.setdefault(entry.content_hash, []).append(target)
    except Exception:
        for target in installed: remove_song(target)
        raise
    return installed


def deactivate_archived(entries, game_root, trash, expected_paths=None):
    """Remove only current, full-content matches through a recoverable Trash callback."""
    root = Path(game_root).expanduser().resolve(strict=True)
    copies, errors = archived_installations(entries, root)
    if errors: raise ValueError('Cannot safely determine installed state:\n' + '\n'.join(errors))
    expected = {entry.content_hash for entry in entries}
    targets = [(path, fingerprint) for fingerprint in expected for path in copies.get(fingerprint, [])]
    if expected_paths is not None and {p for p, _ in targets} != set(expected_paths):
        raise ValueError('Installed copies changed. Refresh and review removal again.')
    for path, fingerprint in targets:
        validate_source(path, root)
        if song_content_hash(path) != fingerprint: raise ValueError('Song changed; refresh installed state')
    count = 0
    for path, _ in targets:
        try:
            if trash(path) is False: raise OSError('Desktop Trash rejected the operation')
        except Exception as exc:
            raise OSError(f'{count} song folders moved to Trash before failure: {exc}') from exc
        count += 1
    return count
