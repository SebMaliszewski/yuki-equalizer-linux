#!/usr/bin/env bash
# Yuki Equalizer for Linux - user-level installer (no root needed except for optional apt packages).
# Usage: ./install.sh [--autostart] [--yes] [--no-deps]
#   --autostart  also re-enable the equalizer after login (only if it was ON at shutdown; no window is opened)
#   --yes        install missing packages with apt without asking
#   --no-deps    skip the dependency check
set -euo pipefail

SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DEST="$DATA_HOME/yuki-equalizer"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$DATA_HOME/applications"
AUTOSTART_DIR="$CONFIG_HOME/autostart"

AUTOSTART=0; YES=0; DEPS=1
for a in "$@"; do
  case "$a" in
    --autostart) AUTOSTART=1 ;;
    --yes|-y) YES=1 ;;
    --no-deps) DEPS=0 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Unknown option: $a" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" -eq 0 ]; then
  echo "Please run this installer as your normal user, not with sudo." >&2; exit 1
fi

# ---------- dependency check ----------
APT_PKGS="python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-numpy python3-pil python3-cairo pipewire pipewire-pulse pipewire-bin pulseaudio-utils"
missing=()
if [ "$DEPS" -eq 1 ]; then
  echo "Checking dependencies..."
  command -v python3 >/dev/null || missing+=("python3")
  if command -v python3 >/dev/null; then
    python3 - <<'PY' 2>/dev/null || missing+=("python modules (GTK 3 / numpy / Pillow / pycairo)")
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, PangoCairo
import cairo, numpy, PIL
PY
  fi
  for c in pactl parec pw-cli pw-dump pipewire systemd-run; do
    command -v "$c" >/dev/null || missing+=("$c")
  done
  if ! pactl info 2>/dev/null | grep -qi "pipewire"; then
    echo "  ! The sound server does not look like PipeWire (pipewire-pulse). The equalizer needs PipeWire >= 0.3.60 (1.0+ recommended)."
  fi
  have_swh=0
  for d in /usr/lib/ladspa /usr/lib64/ladspa /usr/lib/x86_64-linux-gnu/ladspa /usr/local/lib/ladspa; do
    [ -e "$d/sc4m_1916.so" ] && [ -e "$d/fast_lookahead_limiter_1913.so" ] && have_swh=1
  done

  if [ ${#missing[@]} -gt 0 ] || [ "$have_swh" -eq 0 ]; then
    [ ${#missing[@]} -gt 0 ] && echo "  Missing: ${missing[*]}"
    [ "$have_swh" -eq 0 ] && echo "  Optional: swh-plugins (compressor for Speech/Night + output limiter) not found."
    if command -v apt-get >/dev/null; then
      pk="$APT_PKGS"; [ "$have_swh" -eq 0 ] && pk="$pk swh-plugins"
      ans=n
      if [ "$YES" -eq 1 ]; then ans=y
      elif [ -t 0 ]; then read -r -p "Install the packages with 'sudo apt-get install $pk'? [y/N] " ans; fi
      if [[ "$ans" =~ ^[YyTt] ]]; then
        sudo apt-get update && sudo apt-get install -y $pk
      else
        echo "  Skipped. Install later with: sudo apt install $pk"
      fi
    else
      echo "  Please install the equivalents with your package manager (see README.md, 'Dependencies')."
    fi
    if [ ${#missing[@]} -gt 0 ] && [ "$YES" -eq 0 ] && ! command -v apt-get >/dev/null; then
      echo "  Continuing with the file installation anyway."
    fi
  else
    echo "  All dependencies found."
  fi
fi

# ---------- files ----------
echo "Installing to $DEST"
mkdir -p "$DEST" "$BIN_DIR" "$APPS_DIR"
rm -rf "$DEST/assets" "$DEST/fonts"
cp -r "$SRC/assets" "$SRC/fonts" "$DEST/"
install -m 644 "$SRC/yuki_equalizer.py" "$SRC/yuki_audio.py" "$SRC/yuki_state.py" "$DEST/"
install -m 755 "$SRC/yuki-eq" "$DEST/yuki-eq"
install -m 755 "$SRC/uninstall.sh" "$DEST/uninstall.sh"
ln -sf "$DEST/yuki-eq" "$BIN_DIR/yuki-eq"

cat > "$APPS_DIR/yuki-equalizer.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Yuki Equalizer
Name[pl]=Korektor Yuki
Name[ja]=Yuki イコライザー
Comment=System-wide 10-band equalizer (PipeWire)
Comment[pl]=Korektor dźwięku całego systemu (PipeWire)
Exec="$DEST/yuki-eq"
Icon=$DEST/assets/ikona.png
Terminal=false
Categories=AudioVideo;Audio;
StartupWMClass=yuki-equalizer
DESK
echo "  Menu entry: $APPS_DIR/yuki-equalizer.desktop"

if [ "$AUTOSTART" -eq 1 ]; then
  mkdir -p "$AUTOSTART_DIR"
  cat > "$AUTOSTART_DIR/yuki-equalizer-autostart.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Yuki Equalizer (restore after login)
Comment=No window: if the equalizer was ON, turn it on again after login
Exec="$DEST/yuki-eq" --przywroc
Icon=$DEST/assets/ikona.png
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
DESK
  echo "  Autostart: $AUTOSTART_DIR/yuki-equalizer-autostart.desktop"
fi

command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS_DIR" 2>/dev/null || true

echo
echo "Done. Start 'Yuki Equalizer' from the application menu (Multimedia / Sound & Video) or run: yuki-eq"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "(Note: $BIN_DIR is not in your PATH yet; log out and in, or run $DEST/yuki-eq)";; esac
