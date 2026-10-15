import http.client
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
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


if __name__ == '__main__': unittest.main()
