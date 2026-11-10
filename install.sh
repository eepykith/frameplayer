#!/bin/sh
# One install command, from the repository root:
#   sh install.sh
#
# SteamOS / Steam Frame: files go in /home/steamos/frameplayer, and the Flatpak
# is installed for that user. A PC without that home directory uses $HOME.
set -eu

cd "$(dirname "$0")"
root=$(pwd)

if [ -d /home/steamos ] && [ -w /home/steamos ]; then
  HOME=/home/steamos
  export HOME
fi

if [ -z "${HOME:-}" ] || [ ! -d "$HOME" ] || [ ! -w "$HOME" ]; then
  echo "HOME (${HOME:-unset}) is missing or not writable." >&2
  exit 2
fi

prefix="$HOME/frameplayer"
mkdir -p "$prefix" "$HOME/.local/share/applications" \
  "$HOME/.local/share/icons/hicolor/scalable/apps"
echo "Installing into $prefix"

if command -v flatpak >/dev/null 2>&1; then
  echo "Installing the Flatpak. The first run downloads a runtime and builds; it takes a while."
  flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
  if command -v flatpak-builder >/dev/null 2>&1; then
    flatpak install -y --user flathub org.freedesktop.Platform//26.08 org.freedesktop.Sdk//26.08
    flatpak-builder --user --install --force-clean --disable-rofiles-fuse \
      "$HOME/frameplayer-build" \
      "$root/packaging/flatpak/app.frameplayer.Player.yml"
  else
    flatpak install -y --user flathub \
      org.freedesktop.Platform//26.08 org.freedesktop.Sdk//26.08 org.flatpak.Builder
    flatpak run --filesystem=host --share=network org.flatpak.Builder \
      --user --install --force-clean --disable-rofiles-fuse \
      "$HOME/frameplayer-build" \
      "$root/packaging/flatpak/app.frameplayer.Player.yml"
  fi
  cat > "$prefix/frameplayer" << EOF
#!/bin/sh
exec flatpak run app.frameplayer.Player "\$@"
EOF
  chmod 755 "$prefix/frameplayer"
  cp "$root/scripts/uninstall.sh" "$prefix/uninstall"
  chmod 755 "$prefix/uninstall"
  echo
  echo "Installed in $prefix"
  echo "  Start:     $prefix/frameplayer"
  echo "  Remove:    $prefix/uninstall"
  exit 0
fi

echo "No flatpak command. Installing into $prefix"
python3 -m venv "$prefix"
"$prefix/bin/pip" install --upgrade pip
"$prefix/bin/pip" install "$root"

cp "$root/scripts/uninstall.sh" "$prefix/uninstall"
chmod 755 "$prefix/uninstall"
cp "$root/frameplayer/assets/frameplayer.svg" \
  "$HOME/.local/share/icons/hicolor/scalable/apps/app.frameplayer.Player.svg"
cat > "$HOME/.local/share/applications/app.frameplayer.Player.desktop" << EOF
[Desktop Entry]
Name=Frame Player
GenericName=Video Player
Comment=Play 8K, 3D, and VR streams with hardware decoding on Steam Frame
Exec=$prefix/bin/frameplayer %u
Icon=app.frameplayer.Player
Terminal=false
Type=Application
Categories=AudioVideo;Video;Player;
Keywords=video;vr;8k;youtube;hls;twitch;steam;
StartupWMClass=frameplayer
X-FramePlayer-Install=user
EOF

echo
echo "Installed in $prefix"
echo "  Start:   $prefix/bin/frameplayer"
echo "  Remove:  $prefix/uninstall"
