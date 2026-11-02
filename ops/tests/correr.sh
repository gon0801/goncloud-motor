#!/usr/bin/env bash
# Corre todas las pruebas de shell de ops/tests en el CI.
# Una prueba que no pueda correr en Linux se omite en omitidas.txt con su motivo; no se quita.
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OMITIDAS="$AQUI/omitidas.txt"

motivo_de() {
  [ -f "$OMITIDAS" ] || return 0
  while IFS='|' read -r nombre motivo; do
    case "$nombre" in ''|\#*) continue ;; esac
    if [ "$nombre" = "$1" ]; then
      printf '%s\n' "$motivo"
      return 0
    fi
  done <"$OMITIDAS"
}

fallos=0
omitidas=0
for prueba in "$AQUI"/test-*.sh; do
  nombre="$(basename "$prueba")"
  motivo="$(motivo_de "$nombre")"
  if [ -n "$motivo" ]; then
    echo "OMITIDA $nombre: $motivo"
    omitidas=$((omitidas + 1))
    continue
  fi
  echo "=== $nombre"
  if ! bash "$prueba"; then
    echo "FALLA $nombre" >&2
    fallos=$((fallos + 1))
  fi
done

if [ "$fallos" -ne 0 ]; then
  echo "$fallos prueba(s) de ops fallaron" >&2
  exit 1
fi
echo "todo bien ($omitidas omitida(s))"
