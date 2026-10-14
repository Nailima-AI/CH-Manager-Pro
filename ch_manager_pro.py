import sys
import os
import time
import threading
import shutil
import subprocess
import configparser
import locale
from pathlib import Path
from library_core import scan_library, duplicate_groups, plan_moves, apply_moves, install_archive, unique_path
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Gio, Adw, GdkPixbuf

# --- VERSIONE E TRADUZIONI ---
APP_VERSION = "5.1"

LANG_DATA = {
    "en": {
        "title": "CH Manager",
        "lbl_ver": "Version {}",
        "tab_auto": "Automation",
        "tab_lib": "Library",
        "grp_mon": "Monitoring",
        "grp_tools": "Tools",
        "mon_title": "Download Monitor",
        "mon_sub_on": "Active - Watching folder...",
        "mon_sub_off": "Paused",
        "game_title": "In-Game Mode",
        "game_sub": "Install while playing",
        "btn_import": "Import Files",
        "btn_backup": "Open Backup",
        "btn_clean_songs": "Manage Songs",
        "btn_clean_arch": "Manage Archives",
        "btn_path": "Backup Path",
        "toast_imported": "Imported {} files",
        "toast_removed": "Removed {} items",
        "toast_path": "Path updated",
        "notif_install": "Installed",
        "notif_scan": "Run 'Scan Songs' in game!",
        "notif_threat": "Threat Detected",
        "notif_exe": "Executable removed.",
        "search_ph": "Filter...",
        "btn_all": "Select All",
        "btn_del": "Delete",
        "dialog_conf": "Confirm",
        "dialog_del_body": "Delete {} items?",
        "btn_cancel": "Cancel",
        "btn_lib_only": "Library Only",
        "btn_lib_back": "Library + Backup"
    },
    "it": {
        "title": "CH Manager",
        "lbl_ver": "Versione {}",
        "tab_auto": "Automazione",
        "tab_lib": "Libreria",
        "grp_mon": "Monitoraggio",
        "grp_tools": "Strumenti",
        "mon_title": "Monitor Download",
        "mon_sub_on": "Attivo - In attesa...",
        "mon_sub_off": "In Pausa",
        "game_title": "Modalità In-Game",
        "game_sub": "Installa a gioco aperto",
        "btn_import": "Importa File",
        "btn_backup": "Apri Backup",
        "btn_clean_songs": "Gestisci Canzoni",
        "btn_clean_arch": "Gestisci Archivio",
        "btn_path": "Percorso Backup",
        "toast_imported": "Importati {} file",
        "toast_removed": "Rimossi {} elementi",
        "toast_path": "Percorso aggiornato",
        "notif_install": "Installato",
        "notif_scan": "Esegui 'Scan Songs' nel gioco!",
        "notif_threat": "Minaccia Rilevata",
        "notif_exe": "File eseguibile rimosso.",
        "search_ph": "Filtra...",
        "btn_all": "Seleziona Tutto",
        "btn_del": "Elimina",
        "dialog_conf": "Conferma",
        "dialog_del_body": "Eliminare {} elementi?",
        "btn_cancel": "Annulla",
        "btn_lib_only": "Solo Libreria",
        "btn_lib_back": "Libreria + Backup"
    }
}

try:
    SYS_LANG = locale.getdefaultlocale()[0]
    CURRENT_LANG = "it" if "it" in str(SYS_LANG).lower() else "en"
except: CURRENT_LANG = "en"

def tr(key): return LANG_DATA[CURRENT_LANG].get(key, f"[{key}]")

# --- CONFIGURAZIONE ---
APP_ID = "com.clonehero.manager"
BASE_DIR = os.path.expanduser("~/.local/share/ch_manager")
ICONS_DIR = os.path.join(BASE_DIR, "icons")
CONFIG_FILE = os.path.join(BASE_DIR, "config.ini")
GAME_DIR = os.path.expanduser("~/.clonehero/Songs")
SANDBOX_DIR = f"/tmp/ch_sandbox_{os.environ.get('USER')}"
SYSTEM_DL_DIR = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~/Downloads")

SPLASH_IMG = os.path.join(ICONS_DIR, "splash.png")
if not os.path.exists(SPLASH_IMG): SPLASH_IMG = os.path.join(ICONS_DIR, "splash.webp")

local_bin = os.path.expanduser("~/.local/bin")
if local_bin not in os.environ["PATH"]: os.environ["PATH"] += os.pathsep + local_bin

os.makedirs(BASE_DIR, exist_ok=True)


class CHManagerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.config = configparser.ConfigParser(interpolation=None)
        self.monitoring = False
        self.backup_dir = ""
        self.install_while_open = True
        self.library_lock = threading.Lock()
        self.load_config()
        self.connect('startup', self.on_startup)
        self.connect('activate', self.on_activate)

    def load_config(self):
        self.config.read(CONFIG_FILE)
        self.backup_dir = self.config.get("DEFAULT", "BackupDir", fallback=os.path.expanduser("~/CloneHero_Archive"))
        self.install_while_open = self.config.getboolean("DEFAULT", "InstallWhileOpen", fallback=True)
        if not os.path.exists(self.backup_dir): os.makedirs(self.backup_dir, exist_ok=True)
        self.songs_dir = os.path.abspath(os.path.expanduser(self.config.get("DEFAULT", "SongsDir", fallback=GAME_DIR)))
        self.organization = self.config.get("DEFAULT", "Organization", fallback="none")
        if self.organization not in ('none', 'artist', 'author', 'game'): self.organization = 'none'
        os.makedirs(self.songs_dir, exist_ok=True)
        self.save_config()

    def save_config(self):
        self.config['DEFAULT'].update({"BackupDir": self.backup_dir, "InstallWhileOpen": str(self.install_while_open),
                                      "SongsDir": self.songs_dir, "Organization": self.organization})
        with open(CONFIG_FILE, 'w') as f: self.config.write(f)

    def on_startup(self, app):
        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)

    def on_activate(self, app):
        if not app.get_active_window():
            self.splash = SplashScreen(self)
            self.splash.present()
            GLib.timeout_add(2500, self.switch_to_main)

    def switch_to_main(self):
        if self.splash: self.splash.close()
        win = MainWindow(self)
        win.present()
        return False

    def is_game_running(self):
        try: return bool(subprocess.check_output(["pgrep", "-f", "-i", "clonehero"], stderr=subprocess.DEVNULL).strip())
        except: return False

    def send_notification(self, title, body):
        for cmd in [["kdialog", "--title", title, "--passivepopup", body, "4"], ["notify-send", "-i", "input-gaming", title, body], ["zenity", "--notification", "--text", f"{title}: {body}"]]:
            if shutil.which(cmd[0]): subprocess.Popen(cmd, stderr=subprocess.DEVNULL); break

class SplashScreen(Gtk.Window):
    def __init__(self, app):
        super().__init__(application=app, title="Splash")
        self.set_decorated(False)
        self.set_default_size(500, 320)
        self.set_modal(True)

        picture = Gtk.Picture()
        if os.path.exists(SPLASH_IMG):
            picture.set_filename(SPLASH_IMG)
        else:
            picture = Gtk.Image.new_from_icon_name("image-missing-symbolic")

        if isinstance(picture, Gtk.Picture): picture.set_content_fit(Gtk.ContentFit.COVER)

        self.set_child(picture)

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=tr("title"))
        self.app = app
        self.set_default_size(480, 680)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()

        # FORZA ESPANSIONE
        self.stack.set_vexpand(True)
        self.stack.set_valign(Gtk.Align.FILL)

        switcher = Adw.ViewSwitcherTitle(stack=self.stack, title=tr("title"))
        header.set_title_widget(switcher)
        main_box.append(header)

        self.setup_automation_tab(); self.setup_library_tab()
        main_box.append(self.stack)

        # FOOTER
        ver_label = Gtk.Label(label=tr("lbl_ver").format(APP_VERSION))
        ver_label.add_css_class("caption")
        ver_label.add_css_class("dim-label")
        ver_label.set_halign(Gtk.Align.START)
        ver_label.set_valign(Gtk.Align.END)
        ver_label.set_margin_start(20)
        ver_label.set_margin_bottom(15)
        main_box.append(ver_label)

        self.toast_overlay = Adw.ToastOverlay(child=main_box)
        self.set_content(self.toast_overlay)

    def setup_automation_tab(self):
        page = Adw.PreferencesPage()
        grp = Adw.PreferencesGroup(title=tr("grp_mon"))
        page.add(grp)
        self.status_row = Adw.ActionRow(title=tr("mon_title"), subtitle=tr("mon_sub_off"))
        self.status_row.set_icon_name("folder-download-symbolic")
        self.switch = Gtk.Switch(); self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.connect("state-set", self.on_monitor_toggle)
        self.status_row.add_suffix(self.switch); grp.add(self.status_row)
        self.game_row = Adw.ActionRow(title=tr("game_title"), subtitle=tr("game_sub"))
        self.game_row.set_icon_name("input-gaming-symbolic")
        self.sw_game = Gtk.Switch(); self.sw_game.set_active(self.app.install_while_open); self.sw_game.set_valign(Gtk.Align.CENTER)
        self.sw_game.connect("state-set", self.on_game_config_toggle)
        self.game_row.add_suffix(self.sw_game); grp.add(self.game_row)
        p = self.stack.add_titled(page, "auto", tr("tab_auto")); p.set_icon_name("preferences-system-time-symbolic")

    def setup_library_tab(self):
        page = Adw.PreferencesPage()
        grp = Adw.PreferencesGroup(title=tr("grp_tools"))
        page.add(grp)
        def add_row(t, i, cb):
            r = Adw.ActionRow(title=t); r.set_icon_name(i); r.set_activatable(True)
            r.connect("activated", cb); r.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic")); grp.add(r)
        add_row(tr("btn_import"), "list-add-symbolic", self.on_import_clicked)
        add_row(tr("btn_backup"), "folder-documents-symbolic", self.on_open_backup)
        add_row(tr("btn_clean_songs"), "edit-clear-symbolic", self.on_clean_songs)
        add_row(tr("btn_clean_arch"), "user-trash-symbolic", self.on_clean_archive)
        add_row(tr("btn_path"), "drive-harddisk-symbolic", self.on_change_backup)
        self.song_path_row = Adw.ActionRow(title="Chart install folder", subtitle=self.app.songs_dir)
        self.song_path_row.set_activatable(True)
        self.song_path_row.connect('activated', self.on_change_songs)
        self.song_path_row.add_suffix(Gtk.Image.new_from_icon_name('folder-symbolic'))
        grp.add(self.song_path_row)
        organization = Adw.ComboRow(title="Organize new imports", model=Gtk.StringList.new(
            ['Keep song folders', 'Artist', 'Chart author', 'Likely source game']))
        organization.set_selected(['none', 'artist', 'author', 'game'].index(self.app.organization))
        organization.connect('notify::selected', self.on_organization_changed)
        grp.add(organization)
        p = self.stack.add_titled(page, "lib", tr("tab_lib")); p.set_icon_name("library-music-symbolic")

    def on_game_config_toggle(self, switch, state):
        with self.app.library_lock:
            self.app.install_while_open = state; self.app.save_config()
        return False
    def on_monitor_toggle(self, switch, state):
        self.app.monitoring = state
        self.monitor_generation = getattr(self, "monitor_generation", 0) + 1
        if state:
            self.status_row.set_subtitle(tr("mon_sub_on")); subprocess.Popen(["xdg-open", "https://enchor.us/"], stderr=subprocess.DEVNULL)
            threading.Thread(target=self.monitor_loop, args=(self.monitor_generation,), daemon=True).start()
        else: self.status_row.set_subtitle(tr("mon_sub_off"))
        return False

    def monitor_loop(self, generation):
        last_check_game = 0; game_running = False
        attempted = {}
        while self.app.monitoring and generation == self.monitor_generation:
            if time.time() - last_check_game > 5: game_running = self.app.is_game_running(); last_check_game = time.time()
            try:
                files = [f for f in os.listdir(SYSTEM_DL_DIR) if f.lower().endswith(('.zip', '.rar', '.7z'))]
                for f in files:
                    if not self.app.monitoring or generation != self.monitor_generation: break
                    fp = os.path.join(SYSTEM_DL_DIR, f)
                    if ".part" in f or ".crdownload" in f: continue
                    if game_running and not self.app.install_while_open: continue
                    try:
                        s1 = os.path.getsize(fp); time.sleep(1); s2 = os.path.getsize(fp)
                        stamp = (s2, os.stat(fp).st_mtime_ns)
                        if s1 == s2 and s1 > 0 and attempted.get(fp) != stamp:
                            attempted[fp] = stamp
                            if self.secure_install(fp):
                                if game_running: GLib.idle_add(self.app.send_notification, tr("notif_install"), tr("notif_scan"))
                                else: GLib.idle_add(self.app.send_notification, tr("notif_install"), f"{f}")
                                GLib.idle_add(self.show_toast, tr("notif_install"))
                    except: pass
            except: pass
            time.sleep(2)

    def secure_install(self, source_file):
        try:
            with self.app.library_lock:
                if not os.path.isfile(source_file): return False
                install_archive(source_file, self.app.songs_dir, self.app.organization)
                try: self.archive_source(source_file)
                except OSError as exc:
                    GLib.idle_add(self.show_error, f"Songs installed, but archive backup failed: {exc}")
            return True
        except Exception as exc:
            GLib.idle_add(self.show_error, str(exc))
            return False

    def archive_source(self, source):
        source = Path(source)
        target = unique_path(Path(self.app.backup_dir) / source.name)
        if source.resolve() != target.resolve(): shutil.move(str(source), str(target))

    def show_toast(self, message): self.toast_overlay.add_toast(Adw.Toast(title=message))
    def show_error(self, message):
        dialog = Adw.MessageDialog(transient_for=self, heading="Operation failed", body=message)
        dialog.add_response('close', 'Close'); dialog.present()

    def on_change_songs(self, _):
        dialog = Gtk.FileDialog(title="Choose chart install folder")
        dialog.set_initial_folder(Gio.File.new_for_path(self.app.songs_dir))
        dialog.select_folder(self, None, self.on_songs_selected)

    def on_songs_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if not folder or not folder.get_path(): return
        try:
            with self.app.library_lock:
                path = Path(folder.get_path()).resolve(strict=True)
                if not os.access(path, os.W_OK | os.X_OK): raise ValueError("Folder is not writable")
                self.app.songs_dir = str(path)
                self.app.save_config()
            self.song_path_row.set_subtitle(str(path))
            self.show_toast("Chart folder updated. Add this folder in Clone Hero and scan songs. Existing songs were not moved.")
        except Exception as exc: self.show_error(str(exc))

    def on_organization_changed(self, row, _):
        with self.app.library_lock:
            self.app.organization = ['none', 'artist', 'author', 'game'][row.get_selected()]
            self.app.save_config()

    def on_open_backup(self, _): subprocess.Popen(["xdg-open", self.app.backup_dir])
    def on_import_clicked(self, _): d = Gtk.FileDialog(title=tr("btn_import")); f = Gtk.FileFilter(); f.set_name("Archivi"); f.add_pattern("*.zip"); f.add_pattern("*.rar"); f.add_pattern("*.7z"); d.set_default_filter(f); d.open_multiple(self, None, self.on_files_selected)
    def on_files_selected(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
            paths = [f.get_path() for f in files if f.get_path()]
        except GLib.Error: return
        def run():
            count = 0
            for path in paths:
                if self.secure_install(path):
                    count += 1
            GLib.idle_add(self.show_toast, tr('toast_imported').format(count))
        threading.Thread(target=run, daemon=True).start()
    def on_change_backup(self, _): d = Gtk.FileDialog(); d.select_folder(self, None, self.on_folder_selected)
    def on_folder_selected(self, d, res):
        try:
            f = d.select_folder_finish(res)
            if f:
                with self.app.library_lock:
                    self.app.backup_dir = f.get_path(); self.app.save_config()
                self.show_toast(tr("toast_path"))
        except: pass
    def on_clean_songs(self, _): LibraryWindow(self).present()
    def on_clean_archive(self, _): self.show_checklist(tr("btn_clean_arch"), self.app.backup_dir, False, False)
    def show_checklist(self, title, base_path, is_dir, can_delete_backup=False):
        win = Gtk.Window(title=title, transient_for=self, modal=True, default_width=450, default_height=600)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10); box.set_margin_top(10); box.set_margin_bottom(10); box.set_margin_start(10); box.set_margin_end(10)
        search_entry = Gtk.SearchEntry(placeholder_text=tr("search_ph")); box.append(search_entry)
        btn_select_all = Gtk.Button(label=tr("btn_all")); box.append(btn_select_all)
        lb = Gtk.ListBox(); lb.set_selection_mode(Gtk.SelectionMode.NONE); lb.add_css_class("boxed-list")
        try:
            for item in sorted(os.listdir(base_path)):
                fp = os.path.join(base_path, item)
                if (is_dir and os.path.isdir(fp)) or (not is_dir and os.path.isfile(fp)):
                    row = Gtk.ListBoxRow(); chk = Gtk.CheckButton(label=item); chk.set_margin_start(10); row.set_child(chk); row.item_name = item; row.checkbox = chk; lb.append(row)
        except: pass
        scroll = Gtk.ScrolledWindow(child=lb, vexpand=True); box.append(scroll)
        def on_search_changed(entry):
            query = entry.get_text().strip().lower(); c = lb.get_first_child()
            while c:
                if hasattr(c, 'item_name'): c.set_visible(query in c.item_name.lower())
                c = c.get_next_sibling()
        search_entry.connect('search-changed', on_search_changed)
        def on_toggle_all(btn):
            c = lb.get_first_child()
            while c:
                if c.get_visible(): c.checkbox.set_active(True)
                c = c.get_next_sibling()
        btn_select_all.connect("clicked", on_toggle_all)
        btn_del = Gtk.Button(label=tr("btn_del")); btn_del.add_css_class("destructive-action")
        def execute_delete(delete_backup_also):
            c = lb.get_first_child(); rem = []; count = 0
            while c:
                if hasattr(c, 'checkbox') and c.checkbox.get_active() and c.get_visible():
                    fp = os.path.join(base_path, c.item_name)
                    try:
                        if os.path.isdir(fp): shutil.rmtree(fp)
                        else: os.remove(fp)
                        rem.append(c); count += 1
                        if delete_backup_also:
                            for ext in ['.zip', '.rar', '.7z']:
                                ap = os.path.join(self.app.backup_dir, c.item_name + ext);
                                if os.path.exists(ap): os.remove(ap)
                    except: pass
                c = c.get_next_sibling()
            for r in rem: lb.remove(r)
            self.show_toast(tr("toast_removed").format(count));
            if count > 0: win.close()
        def on_delete_clicked(_):
            cnt = 0; c = lb.get_first_child()
            while c:
                if hasattr(c, 'checkbox') and c.checkbox.get_active() and c.get_visible(): cnt += 1
                c = c.get_next_sibling()
            if cnt == 0: return
            if can_delete_backup:
                d = Adw.MessageDialog(transient_for=win, heading=tr("dialog_conf"), body=tr("dialog_del_body").format(cnt))
                d.add_response("cancel", tr("btn_cancel")); d.add_response("lib_only", tr("btn_lib_only")); d.add_response("all", tr("btn_lib_back"))
                d.set_response_appearance("all", Adw.ResponseAppearance.DESTRUCTIVE)
                d.connect("response", lambda d, r: (execute_delete(r=="all") if r!="cancel" else None, d.close()))
                d.present()
            else: execute_delete(False)
        btn_del.connect("clicked", on_delete_clicked)
        box.append(btn_del); win.set_child(box); win.present()

class LibraryWindow(Gtk.Window):
    def __init__(self, owner):
        super().__init__(title='Song library', transient_for=owner, default_width=720, default_height=680)
        self.owner, self.app = owner, owner.app
        self.root = self.app.songs_dir
        self.songs, self.errors, self.groups = [], [], []
        self.busy = False
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ('top', 'bottom', 'start', 'end'): getattr(box, 'set_margin_' + side)(12)
        self.status = Gtk.Label(label='Scanning…', wrap=True, xalign=0)
        box.append(self.status)
        self.search = Gtk.SearchEntry(placeholder_text='Search title, artist, chart author, game or folder')
        self.search.connect('search-changed', lambda _: self.render())
        box.append(self.search)
        controls = Gtk.Box(spacing=8)
        self.sort = Gtk.DropDown.new_from_strings(['Title', 'Artist', 'Chart author', 'Source game', 'Folder'])
        self.sort.connect('notify::selected', lambda *_: self.render())
        controls.append(self.sort)
        self.duplicates = Gtk.CheckButton(label='Duplicates only')
        self.duplicates.connect('toggled', lambda _: self.render())
        controls.append(self.duplicates)
        refresh = Gtk.Button(label='Rescan')
        refresh.connect('clicked', lambda _: self.scan())
        controls.append(refresh); box.append(controls)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        box.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True))
        self.mode = Gtk.DropDown.new_from_strings(['Artist folders', 'Chart author folders', 'Source game folders', 'Custom folder'])
        box.append(self.mode)
        self.custom = Gtk.Entry(placeholder_text='Custom folder inside library, e.g. Favorites/Party')
        box.append(self.custom)
        footer = Gtk.Box(spacing=8)
        select = Gtk.Button(label='Select visible'); select.connect('clicked', self.select_visible)
        footer.append(select)
        self.move_button = Gtk.Button(label='Preview moves…'); self.move_button.connect('clicked', self.preview)
        footer.append(self.move_button)
        trash = Gtk.Button(label='Trash selected…'); trash.connect('clicked', self.trash_selected)
        footer.append(trash); box.append(footer)
        self.set_child(box)
        self.scan()

    def scan(self):
        if self.busy: return
        self.busy = True
        self.move_button.set_sensitive(False)
        self.status.set_text('Scanning chart metadata and hashes…')
        def worker():
            try:
                with self.app.library_lock:
                    songs, errors = scan_library(self.root)
                groups = duplicate_groups(songs)
                GLib.idle_add(self.scanned, songs, errors, groups)
            except Exception as exc: GLib.idle_add(self.failed, str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def failed(self, message):
        self.busy = False
        self.status.set_text(message)
        self.move_button.set_sensitive(True)

    def scanned(self, songs, errors, groups):
        self.songs, self.errors, self.groups = songs, errors, groups
        self.busy = False
        self.move_button.set_sensitive(True)
        self.status.set_text(f'{len(songs)} songs · {len(groups)} duplicate groups · {len(errors)} scan errors\n'
                             f'{self.root}\n' + ('\n'.join(errors[:3]) if errors else
                             'Author means chart creator. Source-game guesses are labeled Likely. Duplicate matches require review.'))
        self.render()

    def render(self):
        if not hasattr(self, 'listbox'): return
        child = self.listbox.get_first_child()
        while child:
            following = child.get_next_sibling(); self.listbox.remove(child); child = following
        duplicate_labels = {}
        for index, (kind, songs) in enumerate(self.groups, 1):
            for song in songs: duplicate_labels.setdefault(song.path, []).append(f'Group {index}: {kind}')
        field = ['name', 'artist', 'author', 'game', 'path'][self.sort.get_selected()]
        query = self.search.get_text().strip().casefold()
        for song in sorted(self.songs, key=lambda s: str(getattr(s, field)).casefold()):
            text = f'{song.name} {song.artist} {song.author} {song.game} {song.path}'
            if query not in text.casefold() or (self.duplicates.get_active() and song.path not in duplicate_labels): continue
            row = Gtk.ListBoxRow(); content = Gtk.Box(spacing=8)
            check = Gtk.CheckButton(); content.append(check)
            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
            title = Gtk.Label(label=f'{song.artist} — {song.name}', xalign=0, wrap=True)
            title.add_css_class('heading'); details.append(title)
            info = (f'Chart author: {song.author} · {song.author_evidence}\n'
                    f'Source: {song.game} · {song.game_evidence}\n'
                    f'{song.path.relative_to(Path(self.root).resolve())}')
            if song.path in duplicate_labels: info += '\n' + '\n'.join(duplicate_labels[song.path])
            if song.warnings: info += '\n' + '\n'.join(song.warnings)
            details.append(Gtk.Label(label=info, xalign=0, wrap=True, selectable=True))
            content.append(details)
            for side in ('top', 'bottom', 'start', 'end'): getattr(content, 'set_margin_' + side)(8)
            row.set_child(content); row.song = song; row.check = check; self.listbox.append(row)

    def select_visible(self, _):
        row = self.listbox.get_first_child()
        while row: row.check.set_active(True); row = row.get_next_sibling()

    def preview(self, _):
        if self.busy: return
        songs = []; row = self.listbox.get_first_child()
        while row:
            if row.check.get_active(): songs.append(row.song)
            row = row.get_next_sibling()
        if not songs:
            self.owner.show_toast('Select songs first'); return
        try:
            with self.app.library_lock:
                moves = plan_moves(songs, self.root, ['artist', 'author', 'game', 'custom'][self.mode.get_selected()], self.custom.get_text())
        except Exception as exc: self.owner.show_error(str(exc)); return
        if not moves: self.owner.show_toast('Songs are already in those folders'); return
        win = Gtk.Window(title=f'Preview {len(moves)} moves', transient_for=self, modal=True,
                         default_width=640, default_height=500)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(Gtk.Label(label='Review folder changes. Name collisions get a numbered suffix.\n'
                                   'Backups stay unchanged. Rescan songs in Clone Hero afterwards.', wrap=True))
        view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text('\n\n'.join(f'{a.relative_to(Path(self.root).resolve())}\n → {b.relative_to(Path(self.root).resolve())}' for a, b in moves))
        box.append(Gtk.ScrolledWindow(child=view, vexpand=True))
        apply = Gtk.Button(label=f'Move {len(moves)} songs'); apply.add_css_class('suggested-action')
        def commit(_):
            apply.set_sensitive(False); win.close(); self.busy = True
            self.move_button.set_sensitive(False); self.status.set_text('Moving songs…')
            def worker():
                try:
                    with self.app.library_lock: count = apply_moves(moves, self.root)
                    GLib.idle_add(self.moved, count)
                except Exception as exc: GLib.idle_add(self.failed, str(exc))
            threading.Thread(target=worker, daemon=True).start()
        apply.connect('clicked', commit); box.append(apply)
        cancel = Gtk.Button(label='Cancel'); cancel.connect('clicked', lambda _: win.close()); box.append(cancel)
        win.set_child(box); win.present()

    def trash_selected(self, _):
        if self.busy: return
        songs = []; row = self.listbox.get_first_child()
        while row:
            if row.check.get_active(): songs.append(row.song)
            row = row.get_next_sibling()
        if not songs: return
        dialog = Adw.MessageDialog(transient_for=self, heading=f'Trash {len(songs)} song folders?',
                                  body='Review duplicates before removing them. This moves selected songs to the desktop Trash. Archive backups stay unchanged.')
        dialog.add_response('cancel', 'Cancel'); dialog.add_response('trash', 'Move to Trash')
        dialog.set_default_response('cancel'); dialog.set_close_response('cancel')
        dialog.set_response_appearance('trash', Adw.ResponseAppearance.DESTRUCTIVE)
        def response(dialog, result):
            dialog.close()
            if result != 'trash': return
            self.busy = True; self.move_button.set_sensitive(False)
            def worker():
                try:
                    with self.app.library_lock:
                        # Reuse the same nested-song and link validation used by organization.
                        from library_core import validate_source
                        for song in songs: validate_source(song.path, Path(self.root).resolve())
                        count = 0
                        for song in songs:
                            Gio.File.new_for_path(str(song.path)).trash(None); count += 1
                    GLib.idle_add(self.moved, count)
                except Exception as exc:
                    GLib.idle_add(self.failed, f'Trash failed (earlier selections may already be in Trash): {exc}. Rescan to refresh.')
            threading.Thread(target=worker, daemon=True).start()
        dialog.connect('response', response); dialog.present()

    def moved(self, count):
        self.busy = False
        self.owner.show_toast(f'Updated {count} songs. Run Scan Songs in Clone Hero.')
        self.scan()


if __name__ == "__main__":
    app = CHManagerApp()
    app.run(sys.argv)
