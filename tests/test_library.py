# AI:X | 0RWX AI MADE | https://0rwx.com
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from library_core import (scan_library, duplicate_groups, plan_moves, apply_moves,
                          install_archive, component, UNKNOWN)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'Songs'
        self.root.mkdir()

    def song(self, name='Track', notes=b'chart', meta='name=Song\nartist=Artist\ncharter=Mapper\nicon=gh3', root=None):
        path = (root or self.root) / name
        path.mkdir(parents=True)
        (path / 'notes.mid').write_bytes(notes)
        (path / 'song.ini').write_text('[song]\n' + meta)
        return path

    def scan(self):
        songs, errors = scan_library(self.root)
        self.assertFalse(errors)
        return songs

    def archive(self, entries, name='pack.ZIP'):
        path = Path(self.temp.name) / name
        with zipfile.ZipFile(path, 'w') as archive:
            for key, value in entries.items(): archive.writestr(key, value)
        return path

    def test_scan_cache_reuses_unchanged_songs_and_notices_changes(self):
        from test_sng import sng_bytes
        cache = {}
        path = self.song('Track')
        (self.root / 'One.sng').write_bytes(sng_bytes())
        first, errors = scan_library(self.root, cache)
        self.assertEqual(errors, [])
        with patch('library_core.read_song') as read_song, patch('library_core.read_sng_song') as read_sng:
            again, _ = scan_library(self.root, cache)
            read_song.assert_not_called(); read_sng.assert_not_called()
        self.assertEqual({s.path: s for s in again}, {s.path: s for s in first})
        # Edited metadata, a new chart file, a moved folder and a deleted song are all noticed.
        (path / 'song.ini').write_text('[song]\nname=Edited\nartist=Artist\n')
        self.assertIn('Edited', [s.name for s in scan_library(self.root, cache)[0]])
        (path / 'notes.chart').write_text('[Song]\n{\n}\n')
        chart = next(s for s in scan_library(self.root, cache)[0] if s.path == path).chart_hash
        self.assertNotEqual(chart, next(s for s in first if s.path == path).chart_hash)
        moved = self.root / 'Folder' / 'Track'; moved.parent.mkdir(); path.rename(moved)
        songs, _ = scan_library(self.root, cache)
        self.assertIn(moved, [s.path for s in songs]); self.assertNotIn(path, [s.path for s in songs])
        (self.root / 'One.sng').unlink()
        songs, _ = scan_library(self.root, cache)
        self.assertEqual([s.path for s in songs], [moved])
        self.assertEqual(len(cache), 1)

    def test_organizing_never_nests_inside_an_existing_song(self):
        from library_core import ArchivedSong, activate_archived, song_content_hash
        self.song('Band', meta='name=Band\nartist=Someone')  # a song folder named like an artist
        other = self.song('Incoming/Track', meta='name=Track\nartist=Band')
        song = next(s for s in self.scan() if s.path == other)
        with self.assertRaisesRegex(ValueError, 'existing song'):
            plan_moves([song], self.root, 'artist')
        with self.assertRaisesRegex(ValueError, 'existing song'):
            install_archive(self.archive({'T/notes.mid': b'new', 'T/song.ini': '[song]\nname=T\nartist=Band'}), self.root, 'artist')
        source = self.song('Src/T', notes=b'other', meta='name=T\nartist=Band', root=Path(self.temp.name))
        entry = ArchivedSong(Path('a.zip'), 'T', scan_library(source.parent)[0][0], song_content_hash(source))
        with self.assertRaisesRegex(ValueError, 'existing song'):
            activate_archived([entry], self.root, 'artist')
        self.assertEqual(sorted(p.name for p in (self.root / 'Band').iterdir()), ['notes.mid', 'song.ini'])

    def test_archive_names_with_repeated_numbering(self):
        from library_core import archive_format, archive_stem
        self.assertEqual(archive_format('Pack.tar.gz (2) (2)'), 'tar')
        for name, stem in [('Song.tar.gz', 'Song'), ('Song.tgz (3)', 'Song'), ('Song.tar.xz', 'Song'),
                           ('Song (2).zip', 'Song (2)'), ('Song.zip (2)', 'Song'), ('Live (1999).7z', 'Live (1999)')]:
            self.assertEqual(archive_stem(name), stem)
        import io, tarfile
        pack = Path(self.temp.name) / 'Root.tar.gz'
        with tarfile.open(pack, 'w:gz') as archive:
            for name, data in (('notes.mid', b'root'), ('song.ini', b'[song]\nname=R')):
                info = tarfile.TarInfo(name); info.size = len(data); archive.addfile(info, io.BytesIO(data))
        self.assertEqual(install_archive(pack, self.root)[0].name, 'Root')  # not "Root.tar"

    def test_nested_metadata(self):
        self.song('Pack/Track', meta='name=100% Song\nartist=Band\nfrets=<color=red>Mapper</color>\nicon=rb2')
        song, = self.scan()
        self.assertEqual(song.author, 'Mapper')
        self.assertEqual(song.game, 'Rock Band 2')
        self.assertIn('Likely', song.game_evidence)
        self.assertEqual(song.name, '100% Song')

    def test_chart_metadata_and_ini_precedence(self):
        path = self.song(meta='name=INI Title')
        (path / 'notes.chart').write_text('[Song]\n{\n Name = "Chart Title"\n Artist = "Band"\n Charter = "Mapper"\n  }\n[Events]\n{\n Name = "Wrong"\n}')
        song, = self.scan()
        self.assertEqual((song.name, song.artist, song.author), ('INI Title', 'Band', 'Mapper'))

    def test_unknown_is_not_artist(self):
        self.song(meta='artist=Band')
        song, = self.scan()
        self.assertEqual(song.author, UNKNOWN)
        self.assertEqual(song.game, UNKNOWN)

    def test_game_metadata_over_icon(self):
        self.song(meta='source_game=Custom Game\nicon=gh3')
        self.assertEqual(self.scan()[0].game, 'Custom Game')

    def test_parent_folder_guess(self):
        self.song('Guitar Hero III/Track', meta='')
        self.assertEqual(self.scan()[0].game, 'Guitar Hero III')

    def test_no_guess_from_title(self):
        self.song(meta='name=Guitar Hero III')
        self.assertEqual(self.scan()[0].game, UNKNOWN)

    def test_duplicates(self):
        self.song('First'); self.song('Copy'); self.song('Different chart', notes=b'different')
        groups = duplicate_groups(self.scan())
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0][1]), 2)
        self.assertEqual(len(groups[1][1]), 3)
        self.assertTrue(all(s.path.exists() for s in self.scan()))

    def test_unknown_names_not_duplicate_candidates(self):
        self.song('A', notes=b'A', meta=''); self.song('B', notes=b'B', meta='')
        self.assertEqual(duplicate_groups(self.scan()), [])

    def test_organize_and_rescan(self):
        source = self.song()
        moves = plan_moves(self.scan(), self.root, 'author')
        self.assertEqual(apply_moves(moves, self.root), 1)
        self.assertFalse(source.exists())
        self.assertTrue((self.root / 'Mapper/Track/notes.mid').exists())
        self.assertEqual(plan_moves(self.scan(), self.root, 'author'), [])

    def test_custom_folder_and_collision(self):
        self.song('Pack1/Track'); self.song('Pack2/Track')
        moves = plan_moves(self.scan(), self.root, 'custom', 'Favorites/Party')
        self.assertEqual(len({b for a,b in moves}), 2)
        apply_moves(moves, self.root)
        self.assertEqual(len(self.scan()), 2)

    def test_traversal_and_self_move_rejected(self):
        self.song()
        for folder in ('../Outside', '/tmp/Outside', 'Track/Inside', ''):
            with self.assertRaises(ValueError): plan_moves(self.scan(), self.root, 'custom', folder)

    def test_symlink_destination_rejected(self):
        self.song()
        outside = Path(self.temp.name) / 'outside'; outside.mkdir()
        (self.root / 'Artist').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError): plan_moves(self.scan(), self.root)

    def test_symlink_song_not_scanned(self):
        path = self.song()
        (self.root / 'Link').symlink_to(path, target_is_directory=True)
        self.assertEqual(len(self.scan()), 1)

    def test_nested_song_cannot_be_moved_as_pack(self):
        self.song('Parent'); self.song('Parent/Child')
        with self.assertRaises(ValueError): plan_moves(self.scan(), self.root)

    def test_stale_preview_does_not_overwrite(self):
        source = self.song()
        moves = plan_moves(self.scan(), self.root)
        moves[0][1].mkdir(parents=True)
        with self.assertRaises(ValueError): apply_moves(moves, self.root)
        self.assertTrue(source.exists())

    def test_rollback_on_second_move_failure(self):
        a = self.song('A'); b = self.song('B')
        moves = plan_moves(self.scan(), self.root)
        rename = Path.rename
        def fail(path, dest):
            if path == b / 'notes.mid': raise OSError('simulated disk failure')
            return rename(path, dest)
        with patch.object(Path, 'rename', fail):
            with self.assertRaises(OSError): apply_moves(moves, self.root)
        self.assertTrue((a / 'notes.mid').exists()); self.assertTrue((b / 'notes.mid').exists())

    def test_installs_uppercase_zip_and_collisions(self):
        archive = self.archive({'Pack/Track/notes.mid': b'chart', 'Pack/Track/song.ini': '[song]\ncharter=Mapper'})
        install_archive(archive, self.root, 'author'); install_archive(archive, self.root, 'author')
        self.assertEqual(len(self.scan()), 2)
        self.assertTrue(archive.exists())

    def test_root_song_archive(self):
        archive = self.archive({'notes.mid': b'chart', 'song.ogg': b'audio'})
        install_archive(archive, self.root)
        self.assertTrue((self.root / 'pack/song.ogg').exists())

    def test_bad_zip_has_no_side_effects(self):
        for entries in ({'../escape': 'bad', 'notes.mid': 'chart'}, {'notes.mid': 'chart', 'EVIL.EXE': 'bad'}):
            archive = self.archive(entries)
            with self.assertRaises(ValueError): install_archive(archive, self.root)
            self.assertEqual(list(self.root.iterdir()), [])
            self.assertFalse((Path(self.temp.name) / 'escape').exists())

    def test_malformed_ini_does_not_abort_scan(self):
        path = self.song(); (path / 'song.ini').write_text('broken metadata')
        song, = self.scan()
        self.assertTrue(song.warnings)

    def test_folder_names_sanitized(self):
        self.assertNotIn('/', component('../AC/DC'))
        self.assertEqual(component('..'), UNKNOWN)

    @unittest.skipUnless(shutil.which('7z') or shutil.which('7zz'), '7z unavailable')
    def test_7z_import(self):
        import subprocess
        path = self.song()
        archive = Path(self.temp.name) / 'songs.7z'
        subprocess.run([shutil.which('7z') or shutil.which('7zz'), 'a', str(archive), str(path)], check=True, stdout=subprocess.DEVNULL)
        target = Path(self.temp.name) / 'New'; target.mkdir()
        install_archive(archive, target)
        self.assertEqual(len(scan_library(target)[0]), 1)




class CopyOwnershipTests(unittest.TestCase):
    def test_existing_destination_is_preserved_for_files_and_folders(self):
        from library_core import copy_song
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for directory in (False,True):
                source=root/('folder' if directory else 'song.sng')
                if directory:source.mkdir();(source/'notes.mid').write_bytes(b'chart')
                else:source.write_bytes(b'new')
                target=root/('existing-folder' if directory else 'existing.sng')
                if directory:target.mkdir();(target/'keep').write_bytes(b'old')
                else:target.write_bytes(b'old')
                with self.assertRaises(FileExistsError):copy_song(source,target)
                self.assertEqual((target/'keep').read_bytes() if directory else target.read_bytes(),b'old')

    def test_partial_owned_copy_is_cleaned_up(self):
        from library_core import copy_song
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';source.mkdir();(source/'notes.mid').write_bytes(b'chart')
            target=root/'copy'
            def fail_copy(src,dst,**kwargs):
                (Path(dst)/'partial').write_bytes(b'partial')
                raise OSError('Simulated full disk')
            with patch('library_core.shutil.copytree',side_effect=fail_copy):
                with self.assertRaises(OSError):copy_song(source,target)
            self.assertFalse(target.exists());self.assertTrue((source/'notes.mid').exists())
            native=root/'file.sng';native.write_bytes(b'original')
            with patch('library_core.shutil.copyfileobj',side_effect=OSError('Simulated full disk')):
                with self.assertRaises(OSError):copy_song(native,root/'file-copy.sng')
            self.assertFalse((root/'file-copy.sng').exists());self.assertEqual(native.read_bytes(),b'original')

    def test_import_and_selective_install_preserve_concurrent_destination(self):
        from test_sng import sng_bytes
        from library_core import ArchiveCatalog, activate_archived
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);game=root/'game';game.mkdir();backups=root/'backups';backups.mkdir()
            source=backups/'Original.sng';source.write_bytes(sng_bytes())
            occupied=game/'Raced.txt';occupied.write_bytes(b'Unrelated concurrent file')
            catalog=ArchiveCatalog()
            try:
                entries,errors=catalog.scan(backups);self.assertEqual(errors,[])
                for install in (lambda:install_archive(source,game),lambda:activate_archived(entries,game)):
                    with patch('library_core.unique_path',return_value=occupied):
                        with self.assertRaises(FileExistsError):install()
                    self.assertEqual(occupied.read_bytes(),b'Unrelated concurrent file')
            finally:catalog.close()


if __name__ == '__main__': unittest.main()
