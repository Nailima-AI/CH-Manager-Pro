"""Independent SNGPKG v1 fixtures based on the public binary specification."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from library_core import (SngReader, ArchiveCatalog, activate_archived, apply_moves,
    deactivate_archived, install_archive, plan_moves, read_sng_song, scan_library,
    song_content_hash)
from ch_web import Service, duplicate_review_groups


def sng_bytes(metadata=None, files=None, mask=bytes(range(16))):
    """Fixture encoder deliberately uses scalar XOR, independent of the reader."""
    metadata = metadata if metadata is not None else {'name':'SNG Song','artist':'Band','charter':'Author','icon':'gh3'}
    files = files if files is not None else {'notes.mid':b'chart', 'song.ogg':b'audio'}
    meta = struct.pack('<Q',len(metadata))
    for key,value in metadata.items():
        for text in (key,value):
            encoded=text.encode();meta+=struct.pack('<i',len(encoded))+encoded
    index_size=8+sum(1+len(name.encode())+16 for name in files)
    offset=26+8+len(meta)+8+index_size+8
    index=struct.pack('<Q',len(files));payload=b''
    for name,data in files.items():
        encoded=name.encode();index+=bytes([len(encoded)])+encoded+struct.pack('<QQ',len(data),offset)
        payload+=bytes(b ^ mask[i%16] ^ (i&255) for i,b in enumerate(data));offset+=len(data)
    return b'SNGPKG'+struct.pack('<I',1)+mask+struct.pack('<Q',len(meta))+meta+struct.pack('<Q',len(index))+index+struct.pack('<Q',len(payload))+payload


class SngTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.game=self.root/'Songs';self.game.mkdir()
        self.source=self.root/'Example.sng';self.source.write_bytes(sng_bytes())

    def test_metadata_and_chart_hash_match_loose_song(self):
        reader=SngReader(self.source)
        loose=self.game/'Loose';loose.mkdir()
        (loose/'song.ini').write_bytes(reader.ini_bytes())
        for name in reader.files: (loose/name).write_bytes(b''.join(reader.chunks(name)))
        shutil.copy2(self.source,self.game/'Packed.sng')
        songs,errors=scan_library(self.game)
        self.assertEqual(errors,[]);self.assertEqual(len(songs),2)
        self.assertEqual(songs[0].chart_hash,songs[1].chart_hash)
        self.assertEqual(song_content_hash(loose),song_content_hash(self.source))
        song=read_sng_song(self.source,self.root)
        self.assertEqual((song.name,song.artist,song.author,song.game),('SNG Song','Band','Author','Guitar Hero III'))

    def test_streaming_mask_across_chunks_and_random_seeds(self):
        data=bytes(range(256))*4097+b'last bytes'
        for mask in (bytes(range(16)), b'\xff'*16):
            self.source.write_bytes(sng_bytes(files={'notes.mid':b'chart','song.opus':data},mask=mask))
            reader=SngReader(self.source)
            chunks=list(reader.chunks('song.opus'))
            self.assertGreater(len(chunks),1)
            self.assertEqual(b''.join(chunks),data)
            self.assertEqual(reader.file_hash('song.opus'),hashlib.sha256(data).hexdigest())

    def test_native_import_organization_move_and_collision_preserve_extension(self):
        first=install_archive(self.source,self.game,'artist')[0]
        second=install_archive(self.source,self.game,'artist')[0]
        self.assertEqual(first.suffix,'.sng');self.assertEqual(second.suffix,'.sng')
        self.assertNotEqual(first,second);self.assertEqual(first.read_bytes(),self.source.read_bytes())
        songs,errors=scan_library(self.game);self.assertEqual(errors,[])
        self.assertEqual(apply_moves(plan_moves(songs,self.game,'custom','Favorites/Party'),self.game),2)
        self.assertEqual(len(list((self.game/'Favorites/Party').glob('*.sng'))),2)

    def test_direct_and_zip_archived_sng_install_remove(self):
        backups=self.root/'Archives';backups.mkdir()
        shutil.copy2(self.source,backups/'Direct.sng')
        with zipfile.ZipFile(backups/'Pack.zip','w') as z: z.write(self.source,'Nested/Packed.sng')
        catalog=ArchiveCatalog();self.addCleanup(catalog.close)
        entries,errors=catalog.scan(backups)
        self.assertEqual(errors,[]);self.assertEqual(len(entries),2)
        self.assertEqual(entries[0].content_hash,entries[1].content_hash)
        installed=activate_archived(entries,self.game)
        self.assertEqual(len(installed),1);self.assertEqual(installed[0].suffix,'.sng')
        trash=self.root/'Removed';trash.mkdir()
        def remove(path): shutil.move(path,trash/path.name);return True
        self.assertEqual(deactivate_archived(entries,self.game,remove,installed),1)
        self.assertEqual(len(list(backups.iterdir())),2)
        self.assertFalse(list(self.game.iterdir()))

    def test_different_container_masks_have_same_content_identity(self):
        one=song_content_hash(self.source)
        self.source.write_bytes(sng_bytes(mask=b'\x99'*16))
        self.assertEqual(song_content_hash(self.source),one)

    def test_numbered_legacy_sng_archive_installs_game_readable_name(self):
        backups=self.root/'Archives';backups.mkdir()
        shutil.copy2(self.source,backups/'Saved.sng (2)')
        catalog=ArchiveCatalog();self.addCleanup(catalog.close)
        entries,errors=catalog.scan(backups)
        self.assertEqual(errors,[])
        installed=activate_archived(entries,self.game)[0]
        self.assertEqual(installed.suffix,'.sng')

    def test_corrupt_headers_sections_offsets_versions_and_truncation_rejected(self):
        valid=self.source.read_bytes()
        variants=[b'not sng',valid[:25],valid[:-1],valid[:6]+struct.pack('<I',2)+valid[10:],valid[:26]+struct.pack('<Q',2**63)+valid[34:]]
        # First file offset points into the header instead of the payload.
        meta_len=struct.unpack_from('<Q',valid,26)[0];index_start=34+meta_len+8
        file_offset=index_start+8+1+len('notes.mid')+8
        invalid=bytearray(valid);struct.pack_into('<Q',invalid,file_offset,0);variants.append(bytes(invalid))
        for data in variants:
            with self.subTest(size=len(data)):
                self.source.write_bytes(data)
                with self.assertRaises(ValueError): SngReader(self.source)

    def test_unsafe_and_ambiguous_members_rejected(self):
        for name in ('../escape','/absolute','C:/outside','folder/../../escape','script.sh','a\\b'):
            with self.subTest(name=name):
                self.source.write_bytes(sng_bytes(files={'notes.mid':b'chart',name:b'x'}))
                with self.assertRaises(ValueError): SngReader(self.source)
        for files in ({'notes.mid':b'a','NOTES.MID':b'b'},{'notes.mid':b'a','notes.mid/sub':b'b'},{'song.ini':b'a','notes.mid':b'c'},{'song.ogg':b'only audio'}):
            self.source.write_bytes(sng_bytes(files=files))
            with self.assertRaises(ValueError): SngReader(self.source)

    def test_changed_audio_is_not_removed_as_identical_archive(self):
        backup=self.root/'Archives';backup.mkdir();shutil.copy2(self.source,backup/'Saved.sng')
        catalog=ArchiveCatalog();self.addCleanup(catalog.close);entries,_=catalog.scan(backup)
        installed=activate_archived(entries,self.game)[0]
        installed.write_bytes(sng_bytes(files={'notes.mid':b'chart','song.ogg':b'changed'}))
        self.assertEqual(deactivate_archived(entries,self.game,lambda p:self.fail('Must preserve changed audio'),[]),0)
        self.assertTrue(installed.exists())

    def test_invalid_sng_does_not_hide_valid_songs(self):
        shutil.copy2(self.source,self.game/'Valid.sng');(self.game/'Broken.sng').write_bytes(b'broken')
        songs,errors=scan_library(self.game)
        self.assertEqual(len(songs),1);self.assertEqual(len(errors),1)

    def test_sng_content_hash_nested_files_matches_folder(self):
        self.source.write_bytes(sng_bytes(files={'notes.mid':b'chart','root':b'root','a/z.bin':b'az','a/b/y.bin':b'aby','z/a.bin':b'za'}))
        reader=SngReader(self.source);folder=self.game/'Unpacked';folder.mkdir()
        (folder/'song.ini').write_bytes(reader.ini_bytes())
        for name in reader.files:
            target=folder/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b''.join(reader.chunks(name)))
        self.assertEqual(song_content_hash(folder),song_content_hash(self.source))

    def test_formatting_only_metadata_does_not_claim_byte_identical_folder(self):
        reader=SngReader(self.source);folder=self.game/'Loose';folder.mkdir()
        for name in reader.files:(folder/name).write_bytes(b''.join(reader.chunks(name)))
        (folder/'song.ini').write_bytes(reader.ini_bytes()+b'\n; local annotation\n')
        self.assertNotEqual(song_content_hash(folder),song_content_hash(self.source))


class DuplicateReviewTests(unittest.TestCase):
    def item(self,key,scope='installed',content='content',chart='chart',name='Song',artist='Band'):
        return dict(id=key,scope=scope,content_hash=content,chart_hash=chart,name=name,artist=artist)

    def test_normal_game_archive_pair_is_not_a_duplicate(self):
        self.assertEqual(duplicate_review_groups([self.item('game'),self.item('archive','archived')]),[])

    def test_two_archives_or_game_copies_are_reviewed(self):
        for scope in ('installed','archived'):
            groups=duplicate_review_groups([self.item('one',scope),self.item('two',scope)])
            self.assertEqual([g['category'] for g in groups],['exact'])
            self.assertEqual(set(groups[0]['ids']),{'one','two'})

    def test_audio_variants_and_title_matches_stay_separate(self):
        groups=duplicate_review_groups([self.item('one'),self.item('two','archived','different-audio')])
        self.assertEqual([g['category'] for g in groups],['chart'])
        groups=duplicate_review_groups([self.item('one'),self.item('two','archived','other','different-chart')])
        self.assertEqual([g['category'] for g in groups],['metadata'])

    def test_unknown_metadata_is_not_a_title_match(self):
        self.assertEqual(duplicate_review_groups([self.item('one',name='Unknown',chart='a'),self.item('two',name='Unknown',content='b',chart='b')]),[])

    def test_sng_archive_review_remove_restore_full_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            service=Service(Path(temporary)/'data');self.addCleanup(service.close)
            for name in ('First.sng','Second.sng'):
                (service.upload_dir/name).write_bytes(sng_bytes())
                service.execute('import',dict(upload=name,install=False))
            state=service.public_state()
            self.assertEqual(len(state['archived']),2)
            self.assertEqual(state['duplicates'][0]['category'],'exact')
            self.assertTrue(all(i['scope']=='archived' for i in state['duplicate_items']))
            service.execute('install',dict(ids=[state['archived'][0]['id']]))
            state=service.public_state();song=Path(service.config['songs_dir'])/state['songs'][0]['path']
            self.assertEqual(song.suffix,'.sng')
            service.execute('remove_songs',dict(ids=[state['songs'][0]['id']],confirm=True))
            service.execute('restore',dict(ids=[service.public_state()['trash'][0]['id']]))
            self.assertEqual(song.read_bytes(),sng_bytes())
            archive=service.public_state()['archives'][0]
            service.execute('remove_archives',dict(ids=[archive['id']],confirm=True))
            self.assertEqual(len(service.public_state()['archives']),1)
            service.execute('restore',dict(ids=[service.public_state()['trash'][0]['id']]))
            self.assertEqual(len(service.public_state()['archives']),2)


if __name__=='__main__': unittest.main()
