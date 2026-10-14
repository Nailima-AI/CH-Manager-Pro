# 🎸 CH Manager Pro — Library Management Fork

A Linux/Steam Deck GTK4 application that watches Downloads, extracts Clone Hero charts, and backs up imported archives.

Forked from [aledibe94/CH-Manager-Pro](https://github.com/aledibe94/CH-Manager-Pro), under the original MIT license. This fork's changes are AI-generated and AI-assisted code; review and test them before using them with your main library.

![Splash screen](icons/splash.png)

## New in this fork (5.1)

- **Change chart installation folder** in Library → Chart install folder, including an SD card or another mounted drive. The setting survives restarts and updates. Existing configurations default to `~/.clonehero/Songs`.
- **Browse nested libraries** with title, artist, chart author, source game, and folder details. Search all those fields and sort by any of them.
- **Organize songs** into artist, chart author, source-game, or custom folders such as `Favorites/Party`. Select songs in Manage Songs, choose a mode, then review the complete move preview before applying. New imports can also be organized automatically.
- **Find duplicates** using SHA-256 of chart files and normalized title/artist matches. The duplicate filter displays group numbers and paths. Identical chart bytes can still have different audio/metadata; title/artist matches may be different arrangements, difficulties, or charters. Nothing is automatically deleted or skipped.
- **Inspect chart author and likely source game**, with evidence labels. Author means the charter, separate from the musical artist. Unknown values remain `Unknown`.
- **Trash selected songs** after confirmation, leaving archive backups intact. Trash failures are reported; there is no permanent-delete fallback.

Changing the install folder selects the library for future imports and management; it does **not** migrate your old library. Add the selected folder in Clone Hero's song-path settings and run **Scan Songs** after importing, moving, or removing songs. Moving songs can change their setlist paths.

Folder collisions receive a numbered suffix; folders are never merged or overwritten. Operations are serialized within the app. Do not run another organizer against the same library at the same time. Move plans are checked again before application. Symlink folders, a chart directly at the library root, and nested song-inside-song layouts require manual review. Moves use filesystem renames and cannot span filesystems; a failed move attempts rollback.

## How metadata detection works

| Field | Sources, in priority order |
| --- | --- |
| Title / musical artist | `song.ini` `[song]` fields, then `notes.chart` `[Song]` fields |
| Chart author | `charter`, `frets`, then `author` |
| Source game | Explicit `game`, `source_game`, or `source` fields (unverified); known Guitar Hero/Rock Band `icon` values; an exact recognizable parent pack-folder name |

Icon and folder matches are labeled **Likely**, not verified provenance. A custom chart can reuse an official-game icon. Generic source fields may name a pack rather than a game. This is local metadata detection, not audio recognition, an online catalog lookup, or proof that a chart came from an official game. It cannot identify arbitrary videogame music when that information is missing.

Scans recognize directories containing `notes.chart`, `notes.mid`, or `notes.midi`, case-insensitively. `song.ini` overrides text-chart metadata. MIDI-only charts need `song.ini` for descriptive metadata. Packed `.sng` files are not supported. Hash matching compares the complete set of note-file bytes; equivalent charts encoded differently need manual review. Scan errors and malformed metadata are reported.

## Installation / upgrade

Download this fork's **Code → Download ZIP**, or clone it. Keep `ch_manager_pro.py`, `library_core.py`, the installer, and `icons/` together. Use Python 3.10+, PyGObject, GTK **4.10+**, libadwaita **1.4+**, and Zenity. ZIP extraction uses Python; RAR/7z extraction requires `7z` or `7zz` with support for the archive codec. Your distribution may package the RAR codec separately.

On Ubuntu 24.04, the GUI packages are:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 zenity 7zip
```

On SteamOS, check the installed GTK/PyGObject/Zenity and 7z availability first; use packages appropriate to your SteamOS version. The installer does not disable the read-only OS or install system packages.

```sh
chmod +x ch_manager_pro_installer_v5.sh
./ch_manager_pro_installer_v5.sh
```

Choose **AGGIORNA** to keep your configuration. **PULISCI** resets app settings. Installed application files and `config.ini` live in `~/.local/share/ch_manager/`; the desktop launcher appears as CH Manager Pro. `SongsDir` and `Organization` are added alongside the existing backup and in-game settings. New controls are currently English; the original English/Italian controls remain.

To run directly from source:

```sh
python3 ch_manager_pro.py
```

Archive imports retain each song's files and back up the original archive. Name collisions also get numbered backup names. Executable files, traversal paths, and links are rejected. Invalid archives remain at their original location and errors are displayed; monitoring retries a failed file only if its size/mtime changes or monitoring restarts. Extraction is not a malware sandbox: only import archives you trust.

## Verification

The backend has no GUI or network dependencies:

```sh
python3 -m unittest discover -s tests -v
bash -n ch_manager_pro_installer_v5.sh
python3 -m py_compile ch_manager_pro.py library_core.py
```

The tests cover metadata precedence, missing/malformed metadata, exact and possible duplicates, recursive scanning, collision handling, custom paths, stale previews, rollback, symlinks, unsafe archives, root/nested ZIP imports, and 7z imports when 7z is installed. Optional GUI smoke tests need the GTK dependencies and a display:

```sh
xvfb-run -a /usr/bin/python3 -m unittest discover -s tests -p 'test_gui.py' -v
```

Before organizing your main library, try a copy of a few charts, switch the install folder to that copy, import an archive, review duplicate groups, and preview/apply a folder move. Confirm the results in Clone Hero. Actual Steam Deck/Clone Hero validation is still needed.

## License

[MIT](LICENSE). Original copyright and upstream attribution are retained.
