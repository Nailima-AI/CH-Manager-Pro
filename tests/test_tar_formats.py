import io
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from library_core import ArchiveCatalog, archive_format, extract_archive, install_archive, unique_path


def add(archive, name, data):
    info = tarfile.TarInfo(name); info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


class TarFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root / 'Archive'; self.backup.mkdir()
        self.game = self.root / 'Game'; self.game.mkdir()

    def pack(self, name, mode):
        path = self.backup / name
        with tarfile.open(path, mode) as archive:
            for title in ('First', 'Second'):
                add(archive, f'Pack/{title}/notes.chart', b'[Song]\n' + title.encode())
                add(archive, f'Pack/{title}/song.ini', f'[song]\nname={title}\nartist=Band\n'.encode())
                add(archive, f'Pack/{title}/song.ogg', b'audio-' + title.encode())
        return path

    def test_format_detection(self):
        for name in ('a.tar', 'a.tar.gz', 'a.TGZ', 'a.tar.bz2', 'a.tbz2', 'a.tbz', 'a.tar.xz', 'a.txz', 'a.tar.gz (2)', 'a (2).tgz'):
            self.assertEqual(archive_format(name), 'tar', name)
        for name, kind in (('a.zip', 'zip'), ('a.RAR', 'rar'), ('a.7z', '7z'), ('a.sng', 'sng'), ('a.zip (3)', 'zip')):
            self.assertEqual(archive_format(name), kind)
        for name in ('a.gz', 'a.txt', 'a.tar.zip.bak', 'notes.chart'):
            self.assertIsNone(archive_format(name), name)

    def test_every_tar_compression_installs_and_catalogs(self):
        for name, mode in (('p.tar', 'w'), ('p.tar.gz', 'w:gz'), ('p.tgz', 'w:gz'), ('p.tar.bz2', 'w:bz2'), ('p.tar.xz', 'w:xz'), ('p.txz', 'w:xz')):
            with self.subTest(name=name):
                game = self.root / ('Game-' + name); game.mkdir()
                count = len(install_archive(self.pack(name, mode), game))
                self.assertEqual(count, 2)
                self.assertTrue((game / 'First' / 'notes.chart').is_file())
        catalog = ArchiveCatalog(); self.addCleanup(catalog.close)
        entries, errors = catalog.scan(self.backup)
        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 12)

    def test_numbered_duplicate_names_stay_recognized(self):
        for name in ('Pack.tar.gz', 'Pack.tgz', 'Pack.tar'):
            original = self.backup / name; original.write_bytes(b'x')
            copy = unique_path(original)
            self.assertNotEqual(copy, original)
            self.assertEqual(archive_format(copy), 'tar', copy.name)

    def unsafe(self, build):
        path = self.backup / 'bad.tar'
        with tarfile.open(path, 'w') as archive: build(archive)
        out = self.root / 'out'; out.mkdir(exist_ok=True)
        with self.assertRaises(ValueError): extract_archive(path, out)
        self.assertFalse((self.root / 'escape').exists())

    def test_rejects_traversal_links_and_devices(self):
        self.unsafe(lambda a: add(a, '../escape', b'x'))
        self.unsafe(lambda a: add(a, '/abs/escape', b'x'))
        def symlink(a):
            info = tarfile.TarInfo('Song/link'); info.type = tarfile.SYMTYPE; info.linkname = '/etc/passwd'; a.addfile(info)
        self.unsafe(symlink)
        def hardlink(a):
            info = tarfile.TarInfo('Song/hard'); info.type = tarfile.LNKTYPE; info.linkname = '../escape'; a.addfile(info)
        self.unsafe(hardlink)
        def fifo(a):
            info = tarfile.TarInfo('Song/pipe'); info.type = tarfile.FIFOTYPE; a.addfile(info)
        self.unsafe(fifo)

    def test_corrupt_tar_is_reported_not_raised(self):
        (self.backup / 'broken.tar.gz').write_bytes(b'not a tarball')
        catalog = ArchiveCatalog(); self.addCleanup(catalog.close)
        entries, errors = catalog.scan(self.backup)
        self.assertEqual(entries, [])
        self.assertTrue(any('broken.tar.gz' in e for e in errors))


@unittest.skipUnless(shutil.which('7z') or shutil.which('7zz'), '7z/7zz not installed')
class SevenZipFormatTests(unittest.TestCase):
    """Round-trip real 7z (and RAR when the codec can create it) through the importer."""
    def test_7z_pack_installs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); src = root / 'src' / 'Song'; src.mkdir(parents=True)
            (src / 'notes.chart').write_text('[Song]'); (src / 'song.ini').write_text('[song]\nname=S\nartist=A\n')
            exe = shutil.which('7z') or shutil.which('7zz')
            subprocess.run([exe, 'a', str(root / 'pack.7z'), 'Song'], cwd=root / 'src', check=True, capture_output=True)
            game = root / 'Game'; game.mkdir()
            self.assertEqual(len(install_archive(root / "pack.7z", game)), 1)
            self.assertTrue((game / 'Song' / 'notes.chart').is_file())

    def test_7z_symlink_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); src = root / 'src' / 'Song'; src.mkdir(parents=True)
            (src / 'notes.chart').write_text('[Song]')
            (root / 'victim').mkdir(); (src / 'esc').symlink_to(root / 'victim')
            exe = shutil.which('7z') or shutil.which('7zz')
            subprocess.run([exe, 'a', '-snl', str(root / 'evil.7z'), 'Song'], cwd=root / 'src', check=True, capture_output=True)
            out = root / 'out'; out.mkdir()
            with self.assertRaisesRegex(ValueError, 'links'):
                extract_archive(root / 'evil.7z', out)
            self.assertEqual(list(out.iterdir()), [])

    def test_encrypted_7z_fails_instead_of_prompting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); src = root / 'Song'; src.mkdir(); (src / 'notes.chart').write_text('[Song]')
            exe = shutil.which('7z') or shutil.which('7zz')
            subprocess.run([exe, 'a', '-psecret', '-mhe=on', str(root / 'enc.7z'), 'Song'], cwd=root, check=True, capture_output=True)
            real_run = subprocess.run
            def no_terminal(*args, **kwargs):
                self.assertIs(kwargs.get('stdin'), subprocess.DEVNULL)  # a terminal stdin would hang on the password prompt
                return real_run(*args, **kwargs)
            out = root / 'out'; out.mkdir()
            with unittest.mock.patch('library_core.subprocess.run', no_terminal):
                with self.assertRaisesRegex(ValueError, '7z could not read'):
                    extract_archive(root / 'enc.7z', out)

    def test_corrupt_7z_reports_value_error(self):
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / 'bad.7z'; bad.write_bytes(b'not an archive')
            with self.assertRaisesRegex(ValueError, '7z could not read'):
                extract_archive(bad, temp)


@unittest.skipUnless((shutil.which('7z') or shutil.which('7zz')) and shutil.which('rar'), '7z and rar needed')
class RarFormatTests(unittest.TestCase):
    """RAR listings always contain empty link fields; only real links may be rejected."""
    def make(self, root, link=False):
        src = root / 'src' / 'Song'; src.mkdir(parents=True)
        (src / 'notes.chart').write_text('[Song]'); (src / 'song.ini').write_text('[song]\nname=R\nartist=A\n')
        if link: (src / 'esc').symlink_to('/etc')
        subprocess.run(['rar', 'a', '-inul', *(['-ol'] if link else []), str(root / 'pack.rar'), 'Song'],
                       cwd=root / 'src', check=True)
        return root / 'pack.rar'

    def test_rar_pack_installs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); game = root / 'Game'; game.mkdir()
            self.assertEqual(len(install_archive(self.make(root), game)), 1)
            self.assertTrue((game / 'Song' / 'notes.chart').is_file())

    def test_rar_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); game = root / 'Game'; game.mkdir()
            with self.assertRaisesRegex(ValueError, 'links'):
                install_archive(self.make(root, link=True), game)


class SevenZipListingTests(unittest.TestCase):
    """Field layouts copied from real `7z l -slt` output (7-Zip 23-26)."""
    def test_regular_entries_with_empty_link_fields_are_allowed(self):
        from library_core import listed_link
        rar = {'Path': 'Song/notes.mid', 'Attributes': ' -rw-r--r--', 'Symbolic Link': '', 'Hard Link': '', 'Copy Link': ''}
        self.assertFalse(listed_link(rar))
        self.assertFalse(listed_link({'Path': 'Song', 'Attributes': 'D drwxr-xr-x'}))
        self.assertFalse(listed_link({'Path': 'a', 'Attributes': 'A'}))

    def test_links_are_detected(self):
        from library_core import listed_link
        self.assertTrue(listed_link({'Path': 'S/esc', 'Attributes': 'A lrwxrwxrwx'}))  # 7z
        self.assertTrue(listed_link({'Path': 'S/esc', 'Attributes': ' lrwxrwxrwx', 'Symbolic Link': '/etc'}))  # RAR
        self.assertTrue(listed_link({'Path': 'S/l', 'Mode': 'lrwxrwxrwx', 'Hard Link': ''}))  # tar via 7z
        self.assertTrue(listed_link({'Path': 'S/h', 'Attributes': 'A', 'Hard Link': 'S/x'}))


if __name__ == '__main__':
    unittest.main()
