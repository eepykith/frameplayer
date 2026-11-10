#!/bin/sh
# One install command, from the repository root:
#   sh install.sh
#
# SteamOS / Steam Frame: builds and installs the Flatpak.
# A PC without flatpak: installs into ~/.local/opt/frameplayer.
set -eu

cd "$(dirname "$0")"
root=$(pwd)

if [ -z "${HOME:-}" ] || [ ! -d "$HOME" ] || [ ! -w "$HOME" ]; then
  echo "HOME (${HOME:-unset}) is missing or not writable." >&2
  exit 2
fi

mkdir -p "$HOME/.local/opt" "$HOME/.local/bin" \
  "$HOME/.local/share/applications" \
  "$HOME/.local/share/icons/hicolor/scalable/apps"

if command -v flatpak >/dev/null 2>&1; then
  echo "Installing the Flatpak. The first run downloads a runtime and builds; it takes a while."
  flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
  if command -v flatpak-builder >/dev/null 2>&1; then
    flatpak install -y --user flathub org.freedesktop.Platform//26.08 org.freedesktop.Sdk//26.08
    flatpak-builder --user --install --force-clean --disable-rofiles-fuse \
      "$HOME/.cache/frameplayer-build" \
      "$root/packaging/flatpak/app.frameplayer.Player.yml"
  else
    flatpak install -y --user flathub \
      org.freedesktop.Platform//26.08 org.freedesktop.Sdk//26.08 org.flatpak.Builder
    mkdir -p "$HOME/.cache"
    flatpak run --filesystem=host --share=network org.flatpak.Builder \
      --user --install --force-clean --disable-rofiles-fuse \
      "$HOME/.cache/frameplayer-build" \
      "$root/packaging/flatpak/app.frameplayer.Player.yml"
  fi
  cp "$root/scripts/uninstall.sh" "$HOME/.local/bin/frameplayer-uninstall"
  chmod 755 "$HOME/.local/bin/frameplayer-uninstall"
  echo
  echo "Installed. Start it with:"
  echo "  flatpak run app.frameplayer.Player"
  echo "Remove it with:"
  echo "  sh $root/uninstall.sh"
  exit 0
fi

echo "No flatpak command. Installing into $HOME/.local/opt/frameplayer"
prefix="$HOME/.local/opt/frameplayer"
python3 -m venv "$prefix"
"$prefix/bin/pip" install --upgrade pip
"$prefix/bin/pip" install "$root"

cat > "$HOME/.local/bin/frameplayer" << EOF
#!/bin/sh
# frameplayer-user-install
exec "$prefix/bin/frameplayer" "\$@"
EOF
chmod 755 "$HOME/.local/bin/frameplayer"
cp "$root/scripts/uninstall.sh" "$HOME/.local/bin/frameplayer-uninstall"
chmod 755 "$HOME/.local/bin/frameplayer-uninstall"
cp "$root/frameplayer/assets/frameplayer.svg" \
  "$HOME/.local/share/icons/hicolor/scalable/apps/app.frameplayer.Player.svg"
cat > "$HOME/.local/share/applications/app.frameplayer.Player.desktop" << EOF
[Desktop Entry]
Name=Frame Player
GenericName=Video Player
Comment=Play 8K, 3D, and VR streams with hardware decoding on Steam Frame
Exec=$HOME/.local/bin/frameplayer %u
Icon=app.frameplayer.Player
Terminal=false
Type=Application
Categories=AudioVideo;Video;Player;
Keywords=video;vr;8k;youtube;hls;twitch;steam;
StartupWMClass=frameplayer
X-FramePlayer-Install=user
EOF

echo
echo "Installed. Start it with:"
echo "  $HOME/.local/bin/frameplayer"
echo "Remove it with:"
echo "  sh $root/uninstall.sh"
