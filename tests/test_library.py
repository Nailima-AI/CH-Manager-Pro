import os
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


if __name__ == '__main__': unittest.main()
