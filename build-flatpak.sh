#!/bin/sh
# Repo-root wrapper. Prefer: sh build-flatpak.sh
cd "$(dirname "$0")"
exec sh scripts/build-flatpak.sh "$@"
