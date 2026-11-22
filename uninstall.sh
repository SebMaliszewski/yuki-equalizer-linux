#!/usr/bin/env bash
# Yuki Equalizer for Linux - uninstaller.
# Usage: ./uninstall.sh [--purge]    (--purge also removes your settings in ~/.config/yuki-equalizer)
set -u
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DEST="$DATA_HOME/yuki-equalizer"
CONF="$CONFIG_HOME/yuki-equalizer"

# 1) If the equalizer is running, give the sound back to the previous output, then stop the filter.
if systemctl --user is-active --quiet yuki-equalizer 2>/dev/null; then
  if [ "$(pactl get-default-sink 2>/dev/null)" = "yuki_equalizer" ]; then
    back="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("out_sink") or "")' "$CONF/state.json" 2>/dev/null || true)"
    if [ -n "$back" ] && pactl list short sinks | awk '{print $2}' | grep -qx "$back"; then
      pactl set-default-sink "$back" && echo "Default output restored to: $back"
    fi
  fi
  systemctl --user stop yuki-equalizer && echo "Equalizer filter stopped."
fi
pkill -f "$DEST/yuki_equalizer.py" 2>/dev/null || true

# 2) Remove files.
rm -f "$DATA_HOME/applications/yuki-equalizer.desktop" "$CONFIG_HOME/autostart/yuki-equalizer-autostart.desktop"
[ -L "$HOME/.local/bin/yuki-eq" ] && rm -f "$HOME/.local/bin/yuki-eq"
rm -rf "$DEST"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$DATA_HOME/applications" 2>/dev/null || true

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$CONF" && echo "Settings removed ($CONF)."
else
  echo "Settings kept in $CONF (use --purge to remove them)."
fi
echo "Yuki Equalizer has been uninstalled."
