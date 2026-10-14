import shutil
import tempfile
import unittest
from pathlib import Path
import zipfile

from library_core import (ArchiveCatalog, activate_archived, archived_installations,
                          deactivate_archived, song_content_hash, archive_format, unique_path)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root / 'Archive'; self.backup.mkdir()
        self.game = self.root / 'Game'; self.game.mkdir()
        self.trash = self.root / 'Trash'; self.trash.mkdir()

    def archive(self, name='Pack.zip', audio=b'audio'):
        path = self.backup / name
        with zipfile.ZipFile(path, 'w') as archive:
            for title in ('First', 'Second'):
                archive.writestr(f'Pack/{title}/notes.mid', b'chart-' + title.encode())
                archive.writestr(f'Pack/{title}/song.ini', f'[song]\nname={title}\nartist=Band\ncharter=Mapper')
                archive.writestr(f'Pack/{title}/song.ogg', audio)
        return path

    def catalog(self):
        catalog = ArchiveCatalog(); self.addCleanup(catalog.close)
        entries, errors = catalog.scan(self.backup)
        self.assertFalse(errors)
        return catalog, entries

    def test_browse_nested_archives_without_installing(self):
        archive = self.archive()
        catalog, entries = self.catalog()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].song.author, 'Mapper')
        self.assertEqual(list(self.game.iterdir()), [])
        self.assertTrue(archive.exists())
        self.assertEqual(archived_installations(entries, self.game), ({}, []))

    def test_install_one_song_from_pack_and_skip_repeat(self):
        self.archive(); _, entries = self.catalog()
        installed = activate_archived(entries[:1], self.game, 'author')
        self.assertEqual(len(installed), 1)
        self.assertEqual(installed[0].parent.name, 'Mapper')
        self.assertEqual(activate_archived(entries[:1], self.game), [])
        copies, errors = archived_installations(entries, self.game)
        self.assertFalse(errors)
        self.assertTrue(copies.get(entries[0].content_hash))
        self.assertFalse(copies.get(entries[1].content_hash))

    def test_remove_and_reinstall_preserves_archive(self):
        archive = self.archive(); before = archive.read_bytes()
        _, entries = self.catalog()
        activate_archived(entries, self.game)
        def trash(path): shutil.move(str(path), self.trash / path.name); return True
        self.assertEqual(deactivate_archived(entries[:1], self.game, trash), 1)
        copies, _ = archived_installations(entries, self.game)
        self.assertFalse(copies.get(entries[0].content_hash))
        self.assertTrue(copies.get(entries[1].content_hash))
        self.assertEqual(archive.read_bytes(), before)
        self.assertEqual(len(activate_archived(entries[:1], self.game)), 1)

    def test_altered_audio_is_not_removed(self):
        self.archive(); _, entries = self.catalog()
        installed, = activate_archived(entries[:1], self.game)
        (installed / 'song.ogg').write_bytes(b'alternate audio')
        self.assertEqual(deactivate_archived(entries, self.game, lambda _: self.fail('Wrong copy removed')), 0)
        self.assertTrue(installed.exists())

    def test_moved_installed_folder_still_detected(self):
        self.archive(); _, entries = self.catalog()
        installed, = activate_archived(entries[:1], self.game)
        destination = self.game / 'Favorites'; destination.mkdir()
        installed.rename(destination / 'Renamed')
        copies, _ = archived_installations(entries, self.game)
        self.assertEqual(copies[entries[0].content_hash], [destination / 'Renamed'])

    def test_same_chart_with_different_audio_versions(self):
        self.archive('One.zip', b'one'); self.archive('Two.zip', b'two')
        _, entries = self.catalog()
        self.assertEqual(len(activate_archived(entries, self.game)), 4)
        self.assertEqual(len(archived_installations(entries, self.game)[0]), 4)

    def test_identical_archived_copies_install_once(self):
        self.archive('One.zip'); self.archive('Two.zip')
        _, entries = self.catalog()
        self.assertEqual(len(entries), 4)
        self.assertEqual(len(activate_archived(entries, self.game)), 2)

    def test_legacy_numbered_backups_and_new_collision_names(self):
        self.archive('Pack.zip (2)')
        _, entries = self.catalog()
        self.assertEqual(len(entries), 2)
        self.assertEqual(archive_format('file.RAR (3)'), 'rar')
        archive = self.archive('Pack.zip')
        self.assertEqual(unique_path(archive).name, 'Pack (2).zip')

    def test_bad_archive_does_not_hide_good_archives(self):
        self.archive(); (self.backup / 'Bad.zip').write_bytes(b'bad')
        catalog = ArchiveCatalog(); self.addCleanup(catalog.close)
        entries, errors = catalog.scan(self.backup)
        self.assertEqual(len(entries), 2); self.assertTrue(errors)

    def test_existing_unrelated_same_named_folder_is_not_overwritten(self):
        self.archive(); _, entries = self.catalog()
        folder = self.game / entries[0].song.path.name; folder.mkdir()
        (folder / 'keep.txt').write_text('keep')
        installed, = activate_archived(entries[:1], self.game)
        self.assertNotEqual(folder, installed)
        self.assertEqual((folder / 'keep.txt').read_text(), 'keep')

    def test_trash_failure_keeps_song(self):
        self.archive(); _, entries = self.catalog()
        installed, = activate_archived(entries[:1], self.game)
        with self.assertRaises(OSError): deactivate_archived(entries, self.game, lambda _: False)
        self.assertTrue(installed.exists())

    def test_changed_confirmation_targets_are_rejected(self):
        self.archive(); _, entries = self.catalog()
        activate_archived(entries[:1], self.game)
        with self.assertRaises(ValueError): deactivate_archived(entries, self.game, lambda _: True, expected_paths=[])

    def test_catalog_files_are_cleaned_up(self):
        self.archive(); catalog, entries = self.catalog()
        extracted = entries[0].song.path
        catalog.close()
        self.assertFalse(extracted.exists())
        self.assertTrue((self.backup / 'Pack.zip').exists())


if __name__ == '__main__': unittest.main()
