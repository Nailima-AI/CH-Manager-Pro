"""Optional real GTK smoke test, isolated from the user's app configuration."""
import os
from pathlib import Path
import tempfile
import time
import unittest

try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Gtk, GLib
    GTK_AVAILABLE = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')) and Gtk.get_major_version() == 4 and Gtk.get_minor_version() >= 10
except (ImportError, ValueError):
    GTK_AVAILABLE = False


@unittest.skipUnless(GTK_AVAILABLE, 'GTK 4.10+/libadwaita/display unavailable')
class GuiTests(unittest.TestCase):
    def test_config_library_duplicates_and_folder_move(self):
        with tempfile.TemporaryDirectory() as temporary:
            old_home = os.environ.get('HOME')
            os.environ['HOME'] = temporary
            self.addCleanup(lambda: os.environ.__setitem__('HOME', old_home) if old_home else os.environ.pop('HOME', None))
            import ch_manager_pro as manager
            app = manager.CHManagerApp()
            app.register(None)
            self.addCleanup(app.quit)
            songs_dir = Path(temporary) / 'Custom%Library'; songs_dir.mkdir()
            app.songs_dir = str(songs_dir); app.organization = 'author'; app.save_config()
            app.load_config()
            self.assertEqual(app.songs_dir, str(songs_dir))
            self.assertEqual(app.organization, 'author')
            for name in ('First', 'Copy'):
                song = songs_dir / name; song.mkdir()
                (song / 'notes.mid').write_bytes(b'identical notes')
                (song / 'song.ini').write_text('[song]\nname=Title\nartist=Band\ncharter=Mapper\nicon=gh3')
            owner = manager.MainWindow(app)
            self.addCleanup(owner.destroy)
            owner.show_toast('Smoke test')
            window = manager.LibraryWindow(owner)
            self.addCleanup(window.destroy)
            def wait_for(predicate):
                until = time.monotonic() + 5
                while not predicate() and time.monotonic() < until:
                    while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
                    time.sleep(.01)
                self.assertTrue(predicate(), 'Timed out waiting for GTK operation')
            wait_for(lambda: not window.busy)
            self.assertEqual(len(window.songs), 2)
            self.assertEqual(len(window.groups), 1)
            window.duplicates.set_active(True)
            window.sort.set_selected(2)
            window.select_visible(None)
            window.mode.set_selected(3); window.custom.set_text('Favorites')
            window.preview(None)
            preview = next(w for w in Gtk.Window.list_toplevels() if w.get_title() == 'Preview 2 moves')
            # Apply is directly before Cancel in the preview's vertical box.
            apply = preview.get_child().get_last_child().get_prev_sibling()
            apply.emit('clicked')
            wait_for(lambda: not window.busy and (songs_dir / 'Favorites/First/notes.mid').exists())
            self.assertTrue((songs_dir / 'Favorites/Copy/notes.mid').exists())
            self.assertEqual(len(window.songs), 2)
            app.load_config()
            self.assertEqual(app.songs_dir, str(songs_dir))
            import zipfile
            archive_path = Path(app.backup_dir) / 'Pack.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                for child in (songs_dir / 'Favorites/First').iterdir():
                    archive.write(child, 'Pack/First/' + child.name)
                archive.writestr('Pack/ArchiveOnly/notes.mid', b'new archived notes')
                archive.writestr('Pack/ArchiveOnly/song.ini', '[song]\nname=ArchiveOnly\nartist=Band\ncharter=Another')
            archived = manager.ArchiveWindow(owner)
            self.addCleanup(archived.close)
            wait_for(lambda: not archived.busy)
            self.assertEqual(len(archived.entries), 2)
            self.assertEqual(sum(bool(archived.copies.get(e.content_hash)) for e in archived.entries), 1)
            archived.filter.set_selected(2)
            archived.select_visible(None)
            self.assertEqual(len(archived.selected()), 1)
            archived.install_selected(None)
            wait_for(lambda: not archived.busy and len(archived.copies) == 2)
            self.assertTrue(archive_path.exists())
            archived.filter.set_selected(1)
            row = archived.listbox.get_first_child()
            while row:
                row.check.set_active(row.entry.song.name == 'ArchiveOnly')
                row = row.get_next_sibling()
            archived.remove_selected(None)
            confirmation = next(w for w in Gtk.Window.list_toplevels()
                                if isinstance(w, manager.Adw.MessageDialog) and 'Remove 1 song' in w.get_heading())
            # Keep the GUI smoke test's Trash local to its temporary fixture.
            from unittest.mock import patch
            from library_core import deactivate_archived
            import shutil
            local_trash = Path(temporary) / 'Trash'; local_trash.mkdir()
            def deactivate(entries, root, trash, expected_paths=None):
                return deactivate_archived(entries, root,
                    lambda path: bool(shutil.move(str(path), str(local_trash / path.name))), expected_paths)
            with patch.object(manager, 'deactivate_archived', deactivate):
                confirmation.emit('response', 'remove')
                wait_for(lambda: not archived.busy and len(archived.copies) == 1)
            self.assertTrue(archive_path.exists())
            self.assertTrue((local_trash / 'ArchiveOnly/notes.mid').exists())
            archived.filter.set_selected(2); archived.select_visible(None); archived.install_selected(None)
            wait_for(lambda: not archived.busy and len(archived.copies) == 2)
            self.assertTrue((songs_dir / 'Another/ArchiveOnly/notes.mid').exists())


if __name__ == '__main__': unittest.main()
