#!/bin/sh
# Paste this into Konsole on Steam Frame:
#   curl -fsSL https://raw.githubusercontent.com/eepykith/frameplayer/main/install.sh | sh
#
# Files land in /home/steamos/frameplayer. A PC without that home uses $HOME.
set -eu

arch=$(uname -m)
if [ "$arch" = "aarch64" ] || [ "$arch" = "arm64" ]; then
  if [ -d /home/steamos ] && [ -w /home/steamos ]; then
    HOME=/home/steamos
    export HOME
  fi
  if [ -z "${HOME:-}" ] || [ ! -d "$HOME" ] || [ ! -w "$HOME" ]; then
    echo "HOME (${HOME:-unset}) is missing or not writable." >&2
    exit 2
  fi
  if ! command -v flatpak >/dev/null 2>&1; then
    echo "flatpak is not installed." >&2
    exit 1
  fi
  prefix="$HOME/frameplayer"
  mkdir -p "$prefix"
  echo "Downloading the prebuilt Steam Frame package (aarch64). Nothing is compiled on this device."
  bundle="$prefix/frameplayer-aarch64.flatpak"
  url="https://github.com/eepykith/frameplayer/releases/download/v0.1.3/frameplayer-aarch64.flatpak"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$bundle" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$bundle" "$url"
  else
    echo "Install curl or wget, then run this again." >&2
    exit 1
  fi
  flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
  flatpak install -y --user "$bundle"
  cat > "$prefix/frameplayer" << EOF
#!/bin/sh
exec flatpak run app.frameplayer.Player "\$@"
EOF
  chmod 755 "$prefix/frameplayer"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$prefix/uninstall" https://raw.githubusercontent.com/eepykith/frameplayer/main/scripts/uninstall.sh
  else
    wget -q -O "$prefix/uninstall" https://raw.githubusercontent.com/eepykith/frameplayer/main/scripts/uninstall.sh
  fi
  chmod 755 "$prefix/uninstall"
  echo
  echo "Installed in $prefix"
  echo "  Start:   $prefix/frameplayer"
  echo "  Remove:  $prefix/uninstall"
  exit 0
fi

script_dir=$(CDPATH= cd -- "$(dirname "$0")" 2>/dev/null && pwd || true)
if [ ! -f "${script_dir}/packaging/flatpak/app.frameplayer.Player.yml" ]; then
  echo "Downloading Frame Player..."
  if [ -d /home/steamos ] && [ -w /home/steamos ]; then
    src=/home/steamos/frameplayer-src
  else
    src=${HOME:-/tmp}/frameplayer-src
  fi
  mkdir -p "$src"
  archive=$(mktemp)
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$archive" https://github.com/eepykith/frameplayer/archive/refs/heads/main.tar.gz
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$archive" https://github.com/eepykith/frameplayer/archive/refs/heads/main.tar.gz
  else
    echo "Install curl or wget, then run this again." >&2
    exit 1
  fi
  tar -xzf "$archive" -C "$src" --strip-components=1
  rm -f "$archive"
  exec sh "$src/install.sh"
fi

cd "$script_dir"
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
