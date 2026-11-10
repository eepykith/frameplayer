#!/bin/sh
# Remove Frame Player. Safe to run more than once.
#
#   frameplayer-uninstall
#   ./scripts/uninstall.sh
#   ./scripts/uninstall.sh --purge
#
# Without --purge, favorites, history, and settings stay on disk.
# --purge also deletes those, and the Flatpak data directory.
set -eu

APP_ID=app.frameplayer.Player
PREFIX=${PREFIX:-"$HOME/.local/opt/frameplayer"}
BIN_DIR=${BIN_DIR:-"$HOME/.local/bin"}
PURGE=0
DRY=0

for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help)
      echo "Usage: frameplayer-uninstall [--purge] [--dry-run]"
      echo "Removes the user install in $PREFIX, the desktop entry, and the Flatpak."
      echo "--purge also deletes settings, history, and Flatpak app data."
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

run() {
  if [ "$DRY" -eq 1 ]; then
    echo "would: $*"
  else
    echo "$*"
    "$@"
  fi
}

case "$PREFIX" in
  */frameplayer) ;;
  *)
    echo "Refusing to delete an unexpected prefix: $PREFIX" >&2
    exit 2
    ;;
esac

echo "Uninstalling Frame Player"

launcher="$BIN_DIR/frameplayer"
if [ -f "$launcher" ] && grep -q "frameplayer-user-install" "$launcher"; then
  run rm -f "$launcher"
fi

desktop="$HOME/.local/share/applications/$APP_ID.desktop"
if [ -f "$desktop" ] && grep -q "X-FramePlayer-Install=user" "$desktop"; then
  run rm -f "$desktop"
fi

icon="$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"
if [ -f "$icon" ]; then
  run rm -f "$icon"
fi

if [ -d "$PREFIX" ]; then
  run rm -rf "$PREFIX"
fi

if command -v flatpak >/dev/null 2>&1; then
  if flatpak info --user "$APP_ID" >/dev/null 2>&1; then
    run flatpak uninstall --user -y "$APP_ID"
  fi
  if flatpak info --system "$APP_ID" >/dev/null 2>&1; then
    if [ "$DRY" -eq 1 ]; then
      echo "would: flatpak uninstall --system -y $APP_ID"
    elif ! flatpak uninstall --system -y "$APP_ID"; then
      echo "The system-wide Flatpak is still installed. Remove it with:"
      echo "  sudo flatpak uninstall -y $APP_ID"
    fi
  fi
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  run update-desktop-database "$HOME/.local/share/applications" || true
fi

if [ "$PURGE" -eq 1 ]; then
  echo "Deleting settings, history, and logs"
  run rm -rf "$HOME/.config/frameplayer"
  run rm -rf "$HOME/.local/share/frameplayer"
  run rm -rf "$HOME/.var/app/$APP_ID"
fi

case "$0" in
  /*) self=$0 ;;
  *)
    if command -v "$0" >/dev/null 2>&1; then
      self=$(command -v "$0")
    else
      self=$(CDPATH= cd -- "$(dirname "$0")" && pwd)/$(basename "$0")
    fi
    ;;
esac
installed="$BIN_DIR/frameplayer-uninstall"
if [ -f "$installed" ]; then
  # Leave the repo copy alone. Remove the copy install.sh placed on PATH.
  if [ "$self" != "$installed" ]; then
    run rm -f "$installed"
  elif [ "$DRY" -eq 0 ]; then
    # We are the installed copy. Remove it after this script is done.
    rm -f "$installed"
  else
    echo "would: rm -f $installed"
  fi
fi

echo "Frame Player is uninstalled."
if [ "$PURGE" -eq 0 ]; then
  echo "Settings and history are still in ~/.config/frameplayer and ~/.local/share/frameplayer."
  echo "Delete them too with: frameplayer-uninstall --purge"
fi
echo "If you added Frame Player in Steam, remove that non-Steam shortcut from the library."
