#!/bin/sh
# Build and install the Frame Player Flatpak for this machine's architecture.
#
# From the repository root, on Steam Frame (ash) or a normal Linux shell:
#   sh scripts/build-flatpak.sh
#
# ./scripts/build-flatpak.sh fails with "No such file or directory" when /bin/sh
# is missing, even though the script is there. Invoking it with sh avoids that.
set -eu

script=$0
case "$script" in
  */*) ;;
  *) script=$(CDPATH= cd -- . && pwd)/$script ;;
esac
root=$(CDPATH= cd -- "$(dirname "$script")/.." && pwd)
manifest="$root/packaging/flatpak/app.frameplayer.Player.yml"
runtime=org.freedesktop.Platform//26.08
sdk=org.freedesktop.Sdk//26.08

if [ ! -f "$manifest" ]; then
  echo "Cannot find $manifest" >&2
  echo "Run this from a checkout of the frameplayer repository:" >&2
  echo "  cd /path/to/frameplayer" >&2
  echo "  sh scripts/build-flatpak.sh" >&2
  exit 1
fi

if ! command -v flatpak >/dev/null 2>&1; then
  echo "flatpak is not installed, so the app cannot be built on this system." >&2
  echo "On a PC, use: sh scripts/install.sh" >&2
  exit 1
fi

arch=$(flatpak --default-arch 2>/dev/null || uname -m)
echo "Building Frame Player for $arch"

flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo

if command -v flatpak-builder >/dev/null 2>&1; then
  flatpak install -y --user flathub "$runtime" "$sdk"
  use_host_builder=1
else
  echo "No flatpak-builder binary on the host. Using the org.flatpak.Builder app."
  flatpak install -y --user flathub "$runtime" "$sdk" org.flatpak.Builder
  use_host_builder=0
fi

mkdir -p "${HOME:-/tmp}/.cache"
stage=$(mktemp -d "${HOME:-/tmp}/.cache/frameplayer-flatpak.XXXXXX")
trap 'rm -rf "$stage"' EXIT

# Copy the source into the home cache so the Builder sandbox can read it.
# Busybox tar and GNU tar both accept this exclude form.
tar -C "$root" \
  --exclude .git \
  --exclude .venv \
  --exclude __pycache__ \
  --exclude build-dir \
  --exclude .flatpak-builder \
  --exclude '*.mp4' \
  -cf - . | tar -C "$stage" -xf -

if [ "$use_host_builder" -eq 1 ]; then
  flatpak-builder --user --install --force-clean --disable-rofiles-fuse \
    "$stage/build" \
    "$stage/packaging/flatpak/app.frameplayer.Player.yml"
else
  flatpak run --filesystem=host --share=network org.flatpak.Builder \
    --user --install --force-clean --disable-rofiles-fuse \
    "$stage/build" \
    "$stage/packaging/flatpak/app.frameplayer.Player.yml"
fi

mkdir -p "${HOME}/.local/bin"
cp "$root/scripts/uninstall.sh" "${HOME}/.local/bin/frameplayer-uninstall"
chmod 755 "${HOME}/.local/bin/frameplayer-uninstall"

echo
echo "Installed app.frameplayer.Player ($arch)."
echo "  Run:       flatpak run app.frameplayer.Player"
echo "  Uninstall: ${HOME}/.local/bin/frameplayer-uninstall"
echo
echo "Until this script finishes, flatpak run reports:"
echo "  app/app.frameplayer.Player/$arch/master not installed"
