#!/bin/bash

# --- CONFIGURAZIONE ---
set -e
set -o pipefail
APP_NAME="CH Manager Pro"
NEW_VERSION_TAG="5.1"

# PERCORSI
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SOURCE_ICONS="$SCRIPT_DIR/icons"
INSTALL_DIR="$HOME/.local/share/ch_manager"
DEST_ICONS="$INSTALL_DIR/icons"
SCRIPT_FILE="$INSTALL_DIR/ch_manager_pro.py"
VERSION_FILE="$INSTALL_DIR/version.txt"
CONFIG_FILE="$INSTALL_DIR/config.ini"
DESKTOP_FILE="$HOME/.local/share/applications/ch-manager-pro.desktop"
LOCAL_BIN="$HOME/.local/bin"

# CHECK ZENITY
if ! command -v zenity &> /dev/null; then echo "Errore: Zenity mancante."; exit 1; fi

# --- 1. VERIFICA ASSET ---
if [ ! -d "$SOURCE_ICONS" ] || [ ! -f "$SCRIPT_DIR/ch_manager_pro.py" ] || [ ! -f "$SCRIPT_DIR/library_core.py" ]; then
    zenity --error --title="Errore" --text="Missing icons or Python source files. Extract the complete source archive." --width=400
    exit 1
fi

# Validate runtime before changing an existing installation.
if ! python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); from gi.repository import Gtk, Adw; assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 10); assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 4)"; then
    zenity --error --text="Python3, PyGObject, GTK 4.10+ and libadwaita 1.4+ are required. See README.md."
    exit 1
fi

# --- 2. RILEVAMENTO VERSIONE ---
MODE="INSTALL"
CURRENT_VER="Nessuna"

if [ -d "$INSTALL_DIR" ]; then
    if [ -f "$VERSION_FILE" ]; then CURRENT_VER=$(cat "$VERSION_FILE"); else CURRENT_VER="Legacy"; fi

    ACTION=$(zenity --list --radiolist \
        --title="Aggiornamento $APP_NAME" \
        --text="Versione attuale: <b>$CURRENT_VER</b>\nRipristino versione: <b>$NEW_VERSION_TAG</b> (Stable)" \
        --column="" --column="Azione" --column="Descrizione" \
        TRUE "AGGIORNA" "Mantiene impostazioni" \
        FALSE "PULISCI" "Reinstalla da zero" \
        --width=500 --height=280)

    if [ $? -ne 0 ]; then exit 1; fi
    if [ "$ACTION" == "AGGIORNA" ]; then MODE="UPDATE"; elif [ "$ACTION" == "PULISCI" ]; then MODE="CLEAN"; fi
else
    zenity --info --title="Installazione" --text="Installazione $APP_NAME V$NEW_VERSION_TAG\n(Versione Stabile)" --width=350
fi

# --- 3. INSTALLAZIONE ---
(
    echo "10"; echo "# ⚙️ Preparazione..."

    if [ "$MODE" == "CLEAN" ]; then
        rm -rf "$INSTALL_DIR"; mkdir -p "$INSTALL_DIR"
    elif [ "$MODE" == "UPDATE" ]; then
        rm -f "$SCRIPT_FILE" "$DESKTOP_FILE" "$VERSION_FILE"
        rm -rf "$DEST_ICONS"
        mkdir -p "$INSTALL_DIR"
    else
        mkdir -p "$INSTALL_DIR"
    fi
    mkdir -p "$LOCAL_BIN" "$(dirname "$DESKTOP_FILE")"
    sleep 1

    echo "30"; echo "# 📦 Dipendenze..."

    echo "50"; echo "# 🎨 Copia risorse..."
    cp -r "$SOURCE_ICONS" "$INSTALL_DIR/"

    DESKTOP_ICON_PATH=""
    if [ -f "$DEST_ICONS/icon.png" ]; then DESKTOP_ICON_PATH="$DEST_ICONS/icon.png";
    elif [ -f "$DEST_ICONS/icon.webp" ]; then DESKTOP_ICON_PATH="$DEST_ICONS/icon.webp";
    else DESKTOP_ICON_PATH="input-gaming"; fi

    echo "70"; echo "# 📝 Scrittura Codice V$NEW_VERSION_TAG..."
    cp "$SCRIPT_DIR/ch_manager_pro.py" "$SCRIPT_FILE"
    cp "$SCRIPT_DIR/library_core.py" "$INSTALL_DIR/library_core.py"

    echo "$NEW_VERSION_TAG" > "$VERSION_FILE"

    echo "90"; echo "# 🚀 Finalizzazione..."
cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Name=$APP_NAME
Comment=Gestore Libreria per Clone Hero
Exec=python3 "$SCRIPT_FILE"
Icon=$DESKTOP_ICON_PATH
Terminal=false
Type=Application
Categories=Game;Utility;
StartupNotify=true
StartupWMClass=com.clonehero.manager
EOF

    chmod +x "$SCRIPT_FILE"
    chmod +x "$DESKTOP_FILE"
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

    echo "100"; echo "# ✅ Completato!"
    sleep 2

) | zenity --progress \
  --title="Installazione $APP_NAME" \
  --text="Avvio..." \
  --percentage=0 \
  --auto-close \
  --width=350

if [ $? -eq 0 ]; then
    zenity --info --title="Successo" --text="$APP_NAME V$NEW_VERSION_TAG installato!" --width=300
fi
