#!/bin/sh
# Build and install the Flatpak from a clean copy of this tree.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
tar -C "$root" \
  --exclude .git \
  --exclude .venv \
  --exclude __pycache__ \
  --exclude build-dir \
  --exclude .flatpak-builder \
  --exclude '*.mp4' \
  -cf - . | tar -C "$stage" -xf -
flatpak-builder --user --install --force-clean \
  "$stage/build" \
  "$stage/packaging/flatpak/app.frameplayer.Player.yml"
mkdir -p "${HOME}/.local/bin"
cp "$root/scripts/uninstall.sh" "${HOME}/.local/bin/frameplayer-uninstall"
chmod 755 "${HOME}/.local/bin/frameplayer-uninstall"
echo
echo "Installed. Run: flatpak run app.frameplayer.Player"
echo "Uninstall:     ${HOME}/.local/bin/frameplayer-uninstall"
