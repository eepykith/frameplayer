#!/bin/sh
# Install Frame Player for the current user, then leave an uninstall command on PATH.
#
#   ./scripts/install.sh
#
# The app lands in ~/.local/opt/frameplayer. Afterward:
#   frameplayer
#   frameplayer-uninstall
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PREFIX=${PREFIX:-"$HOME/.local/opt/frameplayer"}
BIN_DIR=${BIN_DIR:-"$HOME/.local/bin"}
APP_ID=app.frameplayer.Player

case "$PREFIX" in
  */frameplayer) ;;
  *)
    echo "PREFIX must end in /frameplayer (got $PREFIX)" >&2
    exit 2
    ;;
esac

python3 -m venv "$PREFIX"
"$PREFIX/bin/pip" install --upgrade pip
"$PREFIX/bin/pip" install "$root"

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/frameplayer" << EOF
#!/bin/sh
# frameplayer-user-install
exec "$PREFIX/bin/frameplayer" "\$@"
EOF
chmod 755 "$BIN_DIR/frameplayer"

# Installed copy. The repo script stays where it is.
cp "$root/scripts/uninstall.sh" "$BIN_DIR/frameplayer-uninstall"
chmod 755 "$BIN_DIR/frameplayer-uninstall"

mkdir -p "$HOME/.local/share/applications"
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$root/frameplayer/assets/frameplayer.svg" \
  "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"
cat > "$HOME/.local/share/applications/$APP_ID.desktop" << EOF
[Desktop Entry]
Name=Frame Player
GenericName=Video Player
Comment=Play 8K, 3D, and VR streams with hardware decoding on Steam Frame
Exec=$BIN_DIR/frameplayer %u
Icon=$APP_ID
Terminal=false
Type=Application
Categories=AudioVideo;Video;Player;
Keywords=video;vr;8k;youtube;hls;twitch;steam;
StartupWMClass=frameplayer
X-FramePlayer-Install=user
EOF

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications" || true
fi

echo
echo "Installed Frame Player."
echo "  Play:       $BIN_DIR/frameplayer"
echo "  Uninstall:  $BIN_DIR/frameplayer-uninstall"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo
    echo "$BIN_DIR is not on PATH. Add it, or run the commands by full path."
    ;;
esac
echo
echo "Favorites and settings survive uninstall. Remove those too with:"
echo "  $BIN_DIR/frameplayer-uninstall --purge"
