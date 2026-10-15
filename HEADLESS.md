# CH Manager Pro — headless web branch

The `headless-web` branch adds a single-user, self-hosted web interface. The desktop version remains on `main`. Run this branch on a Linux server with Python **3.10+**; it needs **no GTK, desktop session, or third-party Python packages**. The Python standard-library HTTP service is intended for a trusted homelab, normally behind an HTTPS reverse proxy or an SSH tunnel.

## Start it

```sh
git clone --branch headless-web https://github.com/Nailima-AI/CH-Manager-Pro.git
cd CH-Manager-Pro
python3 ch_web.py
```

Open `http://127.0.0.1:8899` on the server, or tunnel from your computer:

```sh
ssh -L 8899:127.0.0.1:8899 user@your-server
```

Then open `http://127.0.0.1:8899` on your computer. Sign in using the token in:

```sh
cat ~/.local/share/ch_manager_web/access-token
```

The token is generated on first start, saved with owner-only permissions, and is not written to access logs. You can instead supply a secret of at least 24 characters using `CH_WEB_TOKEN`. The browser uses an HttpOnly session cookie; sessions expire after 12 hours or a server restart. Sign out revokes the session. Restart after changing a token. Protect the token like an administrator password.

For a different port or persistent data location:

```sh
python3 ch_web.py --data-dir /srv/ch-manager-data --port 8899
```

Use **Settings** to set the server's game-song, archive, and download-watch paths. Folders must be writable, separate, and not nested inside each other. Changing a path selects a different collection; it does not migrate old files. Desktop `config.ini` settings are separate: enter the same existing song/archive paths in the web settings to reuse your collection. Never run the GTK app and web service against the same library simultaneously.

## What the web interface supports

| Area | Actions |
| --- | --- |
| Game library | Browse/search/sort; charter and likely-game evidence; select songs; preview organization by artist, charter, source game, or custom subfolder; move songs to app Trash |
| Archived songs | Inspect songs inside packs; filter installed/not installed; install selected songs; remove matching game copies while keeping archives; reinstall later |
| Duplicate review | Compare installed and archived versions, filter by location/match type, inspect hashes/charter/game/path, install a preferred archived version, trash individual game copies, or review a whole source archive before trashing it |
| Archive files | Browse original downloads, move selected archives to Trash independently of installed songs |
| Trash & restore | Recover removed songs or archives; refuse restoration if the original path already exists |
| Import archives | Upload ZIP/RAR/7z/SNG from the browser; choose archive-only or archive-and-install; keep original archives |
| Settings | Game/archive/watch folders, automatic folder organization, download monitoring, and whether new imports install automatically |
| Activity | Background operation status and errors; the most recent 25 operations are shown (in-memory history) |

**There is no Clone Hero process detection.** Imports and monitoring never depend on the game being open. Turn off **Install new imports automatically** to build an archive first and select songs later. The watch folder must receive completed archives; it checks unchanged size/mtime across polling intervals. Failed monitored files retry after size/mtime changes or a server restart. Imports and filesystem mutations use a single worker queue. Long scans do not prevent the browser from showing current progress.

ZIP and SNG work with Python alone. RAR/7z need `7z` or `7zz`; some RAR codecs need an extra distribution package. On Ubuntu 24.04:

```sh
sudo apt install 7zip
```

Uploaded originals wait in the data directory's `incoming/` folder until imported. Failed imports remain there for administrator inspection/retry. The default upload limit is **2 GiB per archive**; use `--max-upload-mib 8192` for 8 GiB. Archive extraction can need considerably more temporary disk space than the compressed file; scanning uses the server's temporary directory. Only import trusted chart packs.

Full-content installed-state checks include audio and metadata and can take time on large collections. Edited installed songs may appear not installed and will not be removed as exact archive matches. Native `.sng` files are supported as described below. Game folders directly at the library root, nested songs-inside-songs, and symlinks require manual review, as in the desktop version.

## SNG songs and duplicate review

SNGPKG **version 1** files can be uploaded, monitored, archived, browsed, installed, organized, removed, and restored. Native `.sng` files stay packed and retain their bytes when installed or moved. SNG files inside ZIP/RAR/7z packs are recognized as individual songs. No converter or extra dependency is required. Metadata and chart hashes are read without extracting audio; full-content comparison streams decoded payloads in bounded chunks. The reader follows the [public SNG specification](https://github.com/mdsitton/SngFileFormat), rejects unsupported versions, truncated/overlapping offsets, unsafe paths, conflicting members, and malformed metadata. Root chart files above 32 MiB are rejected with a scan error. Actual Clone Hero playback remains untested here.

Duplicate review includes three deliberately separate match types:

- **Identical song content:** all embedded/loose files and their names match, including audio and metadata. SNG masking seeds do not affect this identity. Container metadata is represented as a deterministic `song.ini`; a loose folder with different INI formatting can therefore be a chart match rather than a full-content match.
- **Identical chart, different content:** the note files match but audio, metadata, or other files differ. Compare before choosing a version.
- **Possible duplicate:** normalized title/artist match but chart bytes differ. These may be legitimate arrangements, difficulties, or charters.

One installed song plus its single identical archive backup is excluded from full-content duplicate groups. Multiple game copies, multiple archived copies, and differing versions across those locations remain reviewable. A song can occur in several comparisons. Filter groups by game copies, archived copies, game-and-archive, or match type; search title, artist, charter, game, and location. Full chart/content SHA-256 values are shown for comparison. No preferred version is guessed or automatically deleted.

Use **Install this version** for an archived candidate or **Move game copy to Trash** for a particular installed copy. **Review source archive** explicitly lists every song affected before moving the entire original archive to recoverable Trash. Removing one member from inside a multi-song pack is not supported; the original pack is never rewritten. Installed game copies remain when their source archive is trashed. Use Archived songs to remove matching installed copies while retaining original downloads.

Full-content review reads the song payloads, so the initial scan of a large collection can take time. Operations run in the background. A transcoded audio file or re-encoded MIDI will not be treated as byte-identical; title/artist candidates remain available for manual review. Audio fingerprinting is not implemented.

## Where Clone Hero runs

The server manages files; it does not run Clone Hero. Share or mount the configured song folder on the gaming PC/Steam Deck (for example SMB/NFS), add that mounted folder in Clone Hero, then run **Scan Songs** after changes. A path such as `/srv/clonehero/Songs` on the server can be mounted at a different path on the gaming device. The archive folder can stay server-only. Set permissions so the service account can create, move, and remove song files.

Removed files go to `<data-dir>/trash/`, with original locations recorded in `trash.json`. Restores use those original locations, including paths from before a settings change. There is no permanent-delete control in the web UI. Back up the data directory and your song/archive folders. App Trash is recoverable storage, not a separate backup. Moves between filesystems can require copying; allow enough free space and review any failed operation before retrying.

## LAN or reverse proxy access

Default listening is **localhost only**. For trusted LAN use, explicitly bind and allow the hostname/IP used by the browser:

```sh
python3 ch_web.py --host 0.0.0.0 --allowed-hosts localhost,127.0.0.1,chmanager.lan,192.168.1.50
```

Use HTTPS for connections outside an SSH tunnel. For an existing Traefik/Nginx/Caddy proxy, keep the backend reachable only by the proxy, preserve the browser's `Host` header, configure that hostname in `--allowed-hosts`, and add `--secure-cookie` when the browser uses HTTPS. Proxy request-size and timeout settings must permit your intended archive uploads. The service checks Host, Origin, sessions, and CSRF tokens; it does not use forwarded headers to grant access. Do not expose the Python HTTP service directly to the public Internet. For an Authentik-protected proxy, keep the built-in token sign-in as an additional app login; this branch does not implement OIDC.

## Docker Compose

```sh
docker compose up -d --build
docker compose exec ch-manager cat /data/access-token
```

The included Compose file publishes `127.0.0.1:8899` and persists app data plus the default song/archive/watch folders in a named volume. To reuse host folders, enable the example volume mappings and then set the **container paths** (`/songs`, `/archives`, `/downloads`) in Settings. The image runs as UID/GID 1000; adjust host permissions or the container user to match your storage. For HTTPS proxy access, override the command with `--allowed-hosts your-hostname --secure-cookie` and connect through a private container network. Docker's included `p7zip-full` package may not support every RAR codec; supply a compatible codec package/image if required. GitHub Actions builds this image and runs a Compose smoke test on Ubuntu x86-64, covering startup as UID 1000, login, frontend assets, archive upload/import, native SNG imports, archive duplicate review, selective song installation, removal/restore, and persistence after container recreation. Other CPU architectures and optional host-folder bind mounts have not been tested.

## systemd

A sample is in `deploy/ch-manager-web.service`. Create a dedicated `chmanager` account, put the checkout at `/opt/CH-Manager-Pro`, create `/var/lib/ch-manager-web` owned by that account, grant access to your library mounts, and install/enable the unit after adjusting those paths. Use `RequiresMountsFor=` for external library mounts if needed. The unit listens on localhost; use the SSH tunnel or proxy described above. The service sends no desktop notifications; Activity is the operation log.

## Tests

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile ch_web.py library_core.py
```

The web tests use a real HTTP server and temporary directories to cover authentication, CSRF/Origin/Host rejection, browser uploads, archive-only imports, selective installs, removal/restore, folder previews, duplicate review, configuration persistence, and watching imports without any game-process dependency. Existing backend tests remain. GTK/7z tests run when those dependencies are available. This branch uses the same MIT license and AI-generated/AI-assisted disclaimer as the fork.
