# AI:X | 0RWX AI MADE | https://0rwx.com
import http.client
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from ch_web import Service, WebServer


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.service = Service(Path(self.temp.name) / 'data'); self.addCleanup(self.service.close)
        self.service.refresh(True)

    def upload_fixture(self, name='Pack.zip'):
        path = self.service.upload_dir / name
        with zipfile.ZipFile(path, 'w') as archive:
            for song in ('One', 'Two'):
                archive.writestr(f'{song}/notes.mid', b'chart-' + song.encode())
                archive.writestr(f'{song}/song.ini', f'[song]\nname={song}\nartist=Band\ncharter=Author\nicon=gh3')
                archive.writestr(f'{song}/song.ogg', b'audio')
        return path

    def archived_fixture(self):
        path = self.upload_fixture()
        self.service.execute('import', {'upload':path.name, 'install':False})
        return self.service.public_state()['archived']

    def test_archive_first_install_remove_restore_and_reinstall(self):
        entries = self.archived_fixture()
        self.assertEqual(len(entries), 2)
        self.assertEqual(self.service.public_state()['songs'], [])
        self.service.execute('install', {'ids':[entries[0]['id']]})
        current = self.service.public_state()
        self.assertEqual(len(current['songs']), 1)
        active = next(e for e in current['archived'] if e['installed'])
        self.service.execute('remove_archived', dict(ids=[active['id']], confirm=True, expected_paths=active['installed_paths']))
        current = self.service.public_state()
        self.assertFalse(current['songs']); self.assertEqual(len(current['archives']), 1)
        self.assertEqual(len(current['trash']), 1)
        self.service.execute('restore', {'ids':[current['trash'][0]['id']]})
        self.assertEqual(len(self.service.public_state()['songs']), 1)
        self.assertEqual(self.service.public_state()['trash'], [])
        self.service.execute('install', {'ids':[entries[0]['id']]})
        self.assertEqual(len(self.service.public_state()['songs']), 1)

    def test_moves_preview_apply_and_duplicate_review(self):
        entries = self.archived_fixture()
        self.service.execute('install', {'ids':[e['id'] for e in entries]})
        songs = self.service.public_state()['songs']
        original = Path(self.service.config['songs_dir']) / songs[0]['path']
        shutil.copytree(original, original.with_name('Copy'))
        self.service.refresh()
        self.assertTrue(self.service.public_state()['duplicates'])
        preview = self.service.preview(dict(ids=[s['id'] for s in self.service.public_state()['songs']], mode='custom', custom='Favorites'))
        self.assertEqual(len(preview['moves']), 3)
        self.service.execute('apply_moves', {'plan':preview['plan'], 'confirm':True})
        self.assertTrue(all(s['path'].startswith('Favorites/') for s in self.service.public_state()['songs']))
        with self.assertRaises(ValueError): self.service.execute('apply_moves', {'plan':preview['plan'], 'confirm':True})

    def test_archive_file_trash_restore_leaves_game_songs(self):
        path = self.upload_fixture(); self.service.execute('import', dict(upload=path.name, install=True))
        archive = self.service.public_state()['archives'][0]
        self.service.execute('remove_archives', dict(ids=[archive['id']], confirm=True))
        current = self.service.public_state()
        self.assertEqual(len(current['songs']), 2); self.assertEqual(current['archives'], [])
        self.service.execute('restore', {'ids':[current['trash'][0]['id']]})
        self.assertEqual(len(self.service.public_state()['archives']), 1)

    def test_settings_persist_and_no_process_check_setting(self):
        self.service.execute('settings', dict(monitoring=True, auto_install=False, organization='author'))
        config = json.loads(self.service.config_path.read_text())
        self.assertTrue(config['monitoring']); self.assertFalse(config['auto_install'])
        self.assertNotIn('install_while_open', config)
        clone = Service(self.service.data)
        self.addCleanup(clone.close)
        self.assertEqual(clone.config, self.service.config)

    def test_overlapping_and_reserved_paths_rejected(self):
        for target in (self.service.data, self.service.data / 'trash/nested', Path(self.service.config['backup_dir']) / 'nested'):
            with self.assertRaises(ValueError): self.service.execute('settings', {'songs_dir':str(target)})

    def test_unconfirmed_removal_and_arbitrary_selection_rejected(self):
        self.archived_fixture()
        with self.assertRaises(ValueError): self.service.execute('remove_archived', {'ids':['fake']})
        with self.assertRaises(ValueError): self.service.execute('install', {'ids':['../../sensitive']})
        with self.assertRaises(ValueError): self.service.execute('import', {'upload':'../outside.zip'})

    def test_conditional_state_skips_payload_copy_and_updates_after_jobs(self):
        server=self.start_http();auth=self.login(server)
        code,_,body=self.http(server,'GET','/api/state',headers=auth)
        original=json.loads(body);revision=original['revision']
        with patch('ch_web.copy.deepcopy',side_effect=AssertionError('Unchanged polls must not copy the library')):
            code,_,body=self.http(server,'GET',f'/api/state?since={revision}',headers=auth)
        result=json.loads(body)
        self.assertEqual(code,200);self.assertTrue(result['unchanged'])
        self.assertNotIn('songs',result);self.assertEqual(result['csrf'],original['csrf'])
        self.assertEqual(self.http(server,'GET',f'/api/state?since={revision}')[0],401)
        self.assertEqual(self.http(server,'GET','/api/state?since=invalid',headers=auth)[0],400)
        job=self.service.submit('settings',{'organization':'artist'})
        until=time.monotonic()+3
        while self.service.jobs[job]['status'] in ('queued','running') and time.monotonic()<until:time.sleep(.01)
        code,_,body=self.http(server,'GET',f'/api/state?since={revision}',headers=auth)
        changed=json.loads(body)
        self.assertGreater(changed['revision'],revision);self.assertNotIn('unchanged',changed)
        self.assertEqual(changed['config']['organization'],'artist')

    def test_refresh_reuses_installed_song_scan(self):
        from library_core import scan_library
        entries=self.archived_fixture()
        self.service.execute('install',{'ids':[entries[0]['id']]})
        with patch('ch_web.scan_library',wraps=scan_library) as main_scan, patch('library_core.scan_library',wraps=scan_library) as repeated_scan:
            self.service.refresh(False)
        self.assertEqual(main_scan.call_count,1)
        self.assertEqual(repeated_scan.call_count,0)
        self.assertEqual(len(self.service.public_state()['songs']),1)

    def test_async_job_records_failure(self):
        job = self.service.submit('install', {'ids':['missing']})
        until = time.monotonic()+3
        while self.service.jobs[job]['status'] in ('queued','running') and time.monotonic()<until: time.sleep(.01)
        self.assertEqual(self.service.jobs[job]['status'], 'error')

    def test_monitor_import_without_game_process_dependencies(self):
        source = self.upload_fixture()
        destination = Path(self.service.config['downloads_dir']) / source.name
        shutil.move(source, destination)
        self.service.config.update(monitoring=True, auto_install=False)
        self.service.execute('watch_import', {'path':str(destination)})
        self.assertFalse(destination.exists())
        self.assertEqual(len(self.service.public_state()['archived']), 2)
        self.assertEqual(self.service.public_state()['songs'], [])

    def start_http(self):
        server = WebServer(('127.0.0.1',0), self.service, 'test-token-with-at-least-24-chars', max_upload=1024*1024)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        return server

    def http(self, server, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        if isinstance(body, dict): body=json.dumps(body).encode(); headers={'Content-Type':'application/json', **(headers or {})}
        connection.request(method,path,body,headers or {})
        response=connection.getresponse(); data=response.read(); result=(response.status,dict(response.getheaders()),data)
        connection.close(); return result

    def login(self, server):
        code,headers,data=self.http(server,'POST','/api/login',{'token':server.token})
        self.assertEqual(code,200)
        cookie=headers['Set-Cookie'].split(';')[0]
        code,_,data=self.http(server,'GET','/api/state',headers={'Cookie':cookie})
        return {'Cookie':cookie, 'X-CSRF-Token':json.loads(data)['csrf']}

    def test_http_auth_csrf_origin_and_static_assets(self):
        server=self.start_http()
        code,headers,body=self.http(server,'GET','/')
        self.assertEqual(code,200); self.assertIn(b'Archived songs',body)
        self.assertIn('frame-ancestors',headers['Content-Security-Policy'])
        self.assertEqual(self.http(server,'GET','/api/state')[0],401)
        self.assertEqual(self.http(server,'POST','/api/login',{'token':'wrong'})[0],401)
        auth=self.login(server)
        self.assertEqual(self.http(server,'POST','/api/action',{'action':'refresh'}, {'Cookie':auth['Cookie']})[0],403)
        self.assertEqual(self.http(server,'POST','/api/action',{'action':'refresh'},{**auth,'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.http(server,'GET','/api/state',headers={**auth,'Host':'evil.example'})[0],403)
        self.assertEqual(self.http(server,'POST','/api/action',{'action':'refresh'},auth)[0],202)
        self.assertEqual(self.http(server,'POST','/api/logout',{},auth)[0],200)
        self.assertEqual(self.http(server,'GET','/api/state',headers=auth)[0],401)

    def test_http_sng_upload_review_and_selective_removal(self):
        from test_sng import sng_bytes
        server=self.start_http(); auth=self.login(server)
        def call(action, **payload):
            code,_,body=self.http(server,'POST','/api/action',dict(action=action,**payload),auth)
            self.assertEqual(code,202)
            job=json.loads(body)['job']; until=time.monotonic()+3
            while self.service.jobs[job]['status'] in ('queued','running') and time.monotonic()<until: time.sleep(.01)
            self.assertEqual(self.service.jobs[job]['status'],'done',self.service.jobs[job])
        for name in ('One.sng','Two.sng'):
            code,_,body=self.http(server,'POST','/api/upload?name='+name,sng_bytes(),auth)
            self.assertEqual(code,201)
            call('import',upload=json.loads(body)['upload'],install=False)
        code,_,body=self.http(server,'GET','/api/state',headers=auth)
        current=json.loads(body)
        self.assertEqual(code,200);self.assertEqual(current['duplicates'][0]['category'],'exact')
        self.assertEqual({i['scope'] for i in current['duplicate_items']},{'archived'})
        call('install',ids=[current['archived'][0]['id']])
        current=self.service.public_state();active=next(e for e in current['archived'] if e['installed'])
        call('remove_archived',ids=[active['id']],confirm=True,expected_paths=active['installed_paths'])
        self.assertFalse(self.service.public_state()['songs'])
        self.assertEqual(len(self.service.public_state()['archives']),2)
        self.assertEqual(self.http(server,'GET','/',headers=auth)[0],200)

    def test_http_upload_and_import(self):
        server=self.start_http(); auth=self.login(server)
        source=self.upload_fixture('Original.zip'); data=source.read_bytes(); source.unlink()
        code,_,body=self.http(server,'POST','/api/upload?name=Browser.zip',data,auth)
        self.assertEqual(code,201)
        upload=json.loads(body)['upload']
        code,_,body=self.http(server,'POST','/api/action',dict(action='import',upload=upload,install=False),auth)
        self.assertEqual(code,202); job=json.loads(body)['job']
        until=time.monotonic()+3
        while self.service.jobs[job]['status'] in ('queued','running') and time.monotonic()<until: time.sleep(.01)
        self.assertEqual(self.service.jobs[job]['status'],'done')
        self.assertEqual(len(self.service.public_state()['archived']),2)
        self.assertEqual(self.http(server,'POST','/api/upload?name=../escape.zip',b'bad',auth)[0],400)
        self.assertEqual(self.http(server,'POST','/api/upload?name=bad.exe',b'bad',auth)[0],400)
        self.assertEqual(self.http(server,'GET','/../../etc/passwd',headers=auth)[0],404)

    def test_reimporting_same_pack_archives_again_without_duplicate_game_copies(self):
        self.service.execute('import', dict(upload=self.upload_fixture().name, install=True))
        self.assertEqual(len(self.service.public_state()['songs']), 2)
        result = self.service.execute('import', dict(upload=self.upload_fixture('Again.zip').name, install=True))
        current = self.service.public_state()
        self.assertIn('0 song folders installed', result)
        self.assertEqual(len(current['songs']), 2)
        self.assertEqual(len(current['archives']), 2)
        self.assertTrue(any(g['category'] == 'exact' for g in current['duplicates']))

    def test_import_rescans_only_the_new_archive(self):
        from unittest.mock import patch
        import library_core
        self.service.execute('import', dict(upload=self.upload_fixture().name, install=False))
        with patch('library_core.extract_archive', wraps=library_core.extract_archive) as extract:
            self.service.execute('import', dict(upload=self.upload_fixture('Second.zip').name, install=False))
        # One validation extraction plus one catalog extraction for the new pack only.
        self.assertEqual(extract.call_count, 2)
        self.assertEqual(len(self.service.public_state()['archived']), 4)

    def test_job_only_updates_omit_library_payload(self):
        server=self.start_http();auth=self.login(server)
        original=json.loads(self.http(server,'GET','/api/state',headers=auth)[2])
        job=self.service.submit('install',{'ids':['missing']})
        until=time.monotonic()+3
        while self.service.jobs[job]['status'] in ('queued','running') and time.monotonic()<until:time.sleep(.01)
        code,_,body=self.http(server,'GET',f"/api/state?since={original['revision']}&data={original['data_revision']}",headers=auth)
        result=json.loads(body)
        self.assertEqual(code,200);self.assertTrue(result['partial'])
        self.assertNotIn('songs',result);self.assertEqual(result['jobs'][-1]['status'],'error')
        self.assertEqual(result['data_revision'],original['data_revision'])
        self.assertEqual(self.http(server,'GET','/api/state?since=1&data=x',headers=auth)[0],400)

    def test_allowed_hosts_are_case_insensitive(self):
        server=WebServer(('127.0.0.1',0), self.service, 'test-token-with-at-least-24-chars', allowed_hosts=['ChManager.LAN ', ''])
        self.addCleanup(server.server_close)
        self.assertEqual(server.allowed_hosts, {'chmanager.lan'})

    def test_preview_reports_busy_instead_of_hanging(self):
        entries=self.archived_fixture();self.service.execute('install',{'ids':[entries[0]['id']]})
        song=self.service.public_state()['songs'][0]['id']
        held=threading.Event();release=threading.Event()
        def hold():
            with self.service.filesystem_lock: held.set();release.wait(10)
        threading.Thread(target=hold,daemon=True).start();held.wait(2)
        try:
            self.service.lock_timeout=.05
            with self.assertRaisesRegex(ValueError,'busy'): self.service.preview(dict(ids=[song],mode='artist'))
        finally: release.set()


    def test_large_responses_are_gzipped_only_when_accepted(self):
        import gzip
        server=self.start_http();auth=self.login(server)
        code,headers,plain=self.http(server,'GET','/app.js')
        self.assertEqual(code,200);self.assertNotIn('Content-Encoding',headers);self.assertEqual(headers['Vary'],'Accept-Encoding')
        code,headers,packed=self.http(server,'GET','/app.js',headers={'Accept-Encoding':'br, gzip;q=0.8'})
        self.assertEqual(headers['Content-Encoding'],'gzip');self.assertEqual(gzip.decompress(packed),plain)
        self.assertEqual(int(headers['Content-Length']),len(packed))
        self.assertNotIn('Content-Encoding',self.http(server,'GET','/app.js',headers={'Accept-Encoding':'gzip;q=0'})[1])
        code,headers,body=self.http(server,'GET','/api/state',headers={**auth,'Accept-Encoding':'gzip'})
        state=json.loads(gzip.decompress(body) if headers.get('Content-Encoding')=='gzip' else body)
        self.assertIn('songs',state)
        # Tiny responses stay uncompressed.
        self.assertNotIn('Content-Encoding',self.http(server,'GET','/api/state?since=%d'%state['revision'],headers={**auth,'Accept-Encoding':'gzip'})[1])

    def test_cached_scan_never_hides_edited_installed_copy(self):
        """The scan cache narrows candidates; installed state still uses fresh content hashes."""
        entries=self.archived_fixture();self.service.execute('install',{'ids':[entries[0]['id']]})
        song=Path(self.service.config['songs_dir'])/self.service.public_state()['songs'][0]['path']
        self.assertTrue(self.service.song_cache)
        # Audio edit: chart and song.ini stamps are unchanged, so the cached Song is reused.
        (song/'song.ogg').write_bytes(b'edited audio')
        self.service.execute('install',{'ids':[entries[0]['id']]})
        self.assertEqual(len(self.service.public_state()['songs']),2)
        edited=next(e for e in self.service.public_state()['archived'] if e['id']==entries[0]['id'])
        self.assertEqual(len(edited['installed_paths']),1)
        # Removal only takes the unedited copy and keeps the edited one.
        self.service.execute('remove_archived',dict(ids=[edited['id']],confirm=True,expected_paths=edited['installed_paths']))
        self.assertTrue((song/'song.ogg').is_file())
        self.assertEqual(len(self.service.public_state()['songs']),1)

    def test_damaged_backup_archive_is_reported_not_fatal(self):
        import io, tarfile
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as archive:
            info=tarfile.TarInfo('Song/notes.mid');payload=__import__('os').urandom(50000);info.size=len(payload);archive.addfile(info,io.BytesIO(payload))
        (Path(self.service.config['backup_dir'])/'Bad.tar.gz').write_bytes(data.getvalue()[:300])
        self.upload_fixture();self.service.execute('import',{'upload':'Pack.zip','install':True})
        state=self.service.public_state()
        self.assertEqual(len(state['songs']),2);self.assertTrue(any('Bad.tar.gz' in e for e in state['errors']))

    def test_unrelated_broken_song_does_not_block_install(self):
        broken=Path(self.service.config['songs_dir'])/'Elsewhere';broken.mkdir()
        (broken/'download.sng').write_bytes(b'SNGPKG\x01\x00\x00\x00 truncated')
        entries=self.archived_fixture()
        self.service.execute('install',{'ids':[entries[0]['id']]})
        self.assertEqual(len(self.service.public_state()['songs']),1)

    def test_failed_restore_moves_nothing_and_failed_remove_refreshes(self):
        entries=self.archived_fixture();self.service.execute('install',{'ids':[e['id'] for e in entries]})
        songs=self.service.public_state()['songs']
        self.service.execute('remove_songs',{'ids':[s['id'] for s in songs],'confirm':True})
        root=Path(self.service.config['songs_dir']);blocked=root/songs[1]['path'];blocked.mkdir()
        trash=self.service.public_state()['trash']
        with self.assertRaisesRegex(ValueError,'already exists'): self.service.execute('restore',{'ids':[t['id'] for t in trash]})
        self.assertEqual(len(self.service.public_state()['trash']),2);self.assertFalse((root/songs[0]['path']).exists())
        blocked.rmdir();self.service.execute('restore',{'ids':[t['id'] for t in trash]})
        songs=self.service.public_state()['songs'];calls=[]
        def failing_second(path):
            calls.append(path)
            if len(calls)==2: raise OSError('disk full')
            return original(path)
        original=self.service.quarantine
        with patch.object(self.service,'quarantine',failing_second):
            with self.assertRaisesRegex(OSError,'disk full'): self.service.execute('remove_songs',{'ids':[s['id'] for s in songs],'confirm':True})
        # The first song really moved, and the snapshot says so.
        self.assertEqual(len(self.service.public_state()['songs']),1);self.assertEqual(len(self.service.public_state()['trash']),1)

    def test_trash_record_from_interrupted_move_is_dropped(self):
        song=Path(self.service.config['songs_dir'])/'Kept';song.mkdir()
        self.service.trash['abc']=dict(original=str(song),removed=0);self.service.trash_path.write_text(json.dumps(self.service.trash))
        self.assertEqual(Service(self.service.data).trash,{})

    def test_upload_is_refused_while_queue_is_full(self):
        server=self.start_http();auth=self.login(server)
        with patch.object(self.service,'queue_full',return_value=True):
            code,_,body=self.http(server,'POST','/api/upload?name=Pack.zip',b'x'*5000,auth)
        self.assertEqual(code,429);self.assertEqual(list(self.service.upload_dir.iterdir()),[])

    def test_unicode_tokens_and_malformed_json_are_client_errors(self):
        server=WebServer(('127.0.0.1',0), self.service, 'jeton-d’accès-très-secret-0123456789', max_upload=1024)
        threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.assertEqual(self.http(server,'POST','/api/login',{'token':'falsch-ä'*4})[0],401)
        auth=self.login(server)
        self.assertEqual(self.http(server,'POST','/api/action',{'action':'refresh'},{**auth,'X-CSRF-Token':'caf\xe9'.encode('latin-1').decode('latin-1')})[0],403)
        self.assertEqual(self.http(server,'POST','/api/action',{'action':['refresh']},auth)[0],400)
        self.assertEqual(self.http(server,'POST','/api/preview',{'ids':['x'],'mode':'custom','custom':123},auth)[0],400)

    def proxied_server(self, **options):
        server=WebServer(('127.0.0.1',0), self.service, 'test-token-with-at-least-24-chars', **options)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return server

    def test_any_host_when_allowed_hosts_is_star(self):
        server=self.proxied_server(allowed_hosts=['*'])
        self.assertEqual(self.http(server,'GET','/',headers={'Host':'192.168.1.50:8899'})[0],200)
        code,headers,_=self.http(server,'POST','/api/login',{'token':server.token},{'Host':'chmanager.lan:8899','Origin':'http://chmanager.lan:8899'})
        self.assertEqual(code,200);self.assertNotIn('Secure',headers['Set-Cookie'])
        # Cross-site requests are still refused.
        self.assertEqual(self.http(server,'POST','/api/login',{'token':server.token},{'Host':'chmanager.lan','Origin':'https://evil.example'})[0],403)

    def test_loopback_always_allowed_for_health_checks(self):
        server=self.proxied_server(allowed_hosts=['chmanager.lan'])
        self.assertEqual(self.http(server,'GET','/',headers={'Host':'127.0.0.1:8899'})[0],200)
        self.assertEqual(self.http(server,'GET','/',headers={'Host':'other.lan'})[0],403)

    def test_trusted_proxy_accepts_any_host_and_forwarded_headers(self):
        server=self.proxied_server(allowed_hosts=['localhost'],trusted_proxies=['127.0.0.0/8'])
        forwarded={'Host':'backend:8899','X-Forwarded-Host':'music.example.com','X-Forwarded-Proto':'https',
                   'X-Forwarded-For':'203.0.113.9','Origin':'https://music.example.com'}
        self.assertEqual(self.http(server,'GET','/',headers={'Host':'anything.internal'})[0],200)
        code,headers,_=self.http(server,'POST','/api/login',{'token':server.token},forwarded)
        self.assertEqual(code,200);self.assertIn('Secure',headers['Set-Cookie'])  # HTTPS seen through the proxy
        self.assertIn('203.0.113.9',server.login_attempts)
        # The Origin must still match the host the browser used.
        self.assertEqual(self.http(server,'POST','/api/login',{'token':server.token},{**forwarded,'Origin':'https://evil.example'})[0],403)

    def test_forwarded_headers_ignored_from_untrusted_peers(self):
        server=self.proxied_server(allowed_hosts=['localhost'],trusted_proxies=['10.0.0.0/8'])
        spoofed={'Host':'evil.example','X-Forwarded-Host':'localhost','X-Forwarded-For':'198.51.100.7'}
        self.assertEqual(self.http(server,'GET','/',headers=spoofed)[0],403)
        self.http(server,'POST','/api/login',{'token':'wrong'},{'Host':'localhost','X-Forwarded-For':'198.51.100.7','X-Forwarded-Proto':'https'})
        self.assertNotIn('198.51.100.7',server.login_attempts);self.assertIn('127.0.0.1',server.login_attempts)

    def test_client_ip_skips_trusted_hops(self):
        server=self.proxied_server(trusted_proxies=['127.0.0.0/8','10.0.0.0/8'])
        self.http(server,'POST','/api/login',{'token':'wrong'},{'Host':'localhost','X-Forwarded-For':'1.2.3.4, 203.0.113.5, 10.1.1.1'})
        self.assertEqual(set(server.login_attempts),{'203.0.113.5'})  # 1.2.3.4 could be forged by the client

    def test_invalid_trusted_proxy_is_rejected(self):
        with self.assertRaises(ValueError): WebServer(('127.0.0.1',0), self.service, 'x'*24, trusted_proxies=['not-an-ip'])

    def test_first_start_folders_from_environment(self):
        root=Path(self.temp.name)
        with patch.dict('os.environ',{'CH_WEB_SONGS_DIR':str(root/'s'),'CH_WEB_ARCHIVES_DIR':str(root/'a'),'CH_WEB_DOWNLOADS_DIR':str(root/'d')}):
            fresh=Service(root/'fresh-data');self.addCleanup(fresh.close)
            self.assertEqual(fresh.config['songs_dir'],str((root/'s').resolve()))
            # Saved settings win over the environment afterwards.
            fresh.execute('settings',{'songs_dir':str(root/'other')})
            reopened=Service(root/'fresh-data');self.addCleanup(reopened.close)
            self.assertEqual(reopened.config['songs_dir'],str((root/'other').resolve()))

    def test_favicons_are_served_without_sign_in(self):
        server=self.start_http()
        web=Path(__file__).resolve().parents[1]/'web'
        for path,kind in (('/favicon.svg','image/svg+xml'),('/favicon.ico','image/x-icon'),('/apple-touch-icon.png','image/png')):
            code,headers,body=self.http(server,'GET',path)
            self.assertEqual((code,headers['Content-Type']),(200,kind),path)
            self.assertEqual(body,(web/path.lstrip('/')).read_bytes())
        page=self.http(server,'GET','/')[2]
        for link in (b'href="/favicon.svg"',b'href="/favicon.ico"',b'href="/apple-touch-icon.png"'): self.assertIn(link,page)

if __name__ == '__main__': unittest.main()
