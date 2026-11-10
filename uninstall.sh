#!/bin/sh
#   sh uninstall.sh
#   sh uninstall.sh --purge
cd "$(dirname "$0")"
exec sh scripts/uninstall.sh "$@"
