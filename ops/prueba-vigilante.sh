#!/usr/bin/env bash
# Prueba del vigilante instalado en la Mac, la Mini y la PC: corre ops/prueba_vigilante.py desde
# el ~/motor de cada una (el motor instalado, no esta copia), con el mismo Python que usa
# ops/huellas.sh en cada máquina. Sale 0 solo si pasa en las tres.
# Necesita ya instalada en las tres máquinas la versión que trae esta prueba: el vigilante bajo
# prueba es el del ~/motor de cada una. El ~/.motor real no se toca: la prueba usa un home temporal.
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

posix='export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; if python3 -c "import tomllib" 2>/dev/null; then py=python3; else py="uv run --python 3.12 python"; fi; cd "$HOME/motor" && $py ops/prueba_vigilante.py'
pc="cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python ops\\prueba_vigilante.py"

fallaron=""
for m in $MAQUINAS; do
  echo "== $m"
  if ! corre "$m" "$posix" "$pc"; then
    echo "FALLÓ $m" >&2
    fallaron="$fallaron $m"
  fi
done
if [ -n "$fallaron" ]; then
  echo "No pasó en:$fallaron" >&2
  exit 1
fi
echo "El vigilante pasó en las tres."
