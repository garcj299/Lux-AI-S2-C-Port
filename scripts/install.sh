#!/usr/bin/env bash
# Makes `lux` available from any folder by linking ~/.local/bin/lux to scripts/lux.
# Safe to run again; never replaces a different `lux`.
set -euo pipefail
target="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)/lux"
link="$HOME/.local/bin/lux"
if [[ -e "$link" || -L "$link" ]] && [[ "$(readlink -f "$link")" != "$target" ]]; then
    echo "install.sh: $link already exists and is not this repository's lux; left it alone." >&2
    exit 1
fi
mkdir -p "$(dirname "$link")"
ln -sfn "$target" "$link"
echo "Linked $link -> $target"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "Note: $HOME/.local/bin is not in your PATH; add it to run lux from anywhere." ;;
esac
