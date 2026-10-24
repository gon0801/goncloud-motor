#!/usr/bin/env bash
# Deja a Kuma vigilando el motor de la Mini y el de la PC: crea (o reusa) sus dos monitores push en el
# servidor y escribe MOTOR_KUMA_URL en secretos.env de cada máquina. No reinicia ningún motor: la señal
# lee el archivo en cada vuelta. Sin argumentos; se puede repetir. Nunca imprime un token.
set -uo pipefail
export LC_ALL=C
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DIR/lib.sh"
. "$DIR/lib-kuma.sh"

tokens="$(servidor_kuma alta)" || { echo "senal-kuma: el servidor no dejó los monitores" >&2; exit 1; }

fallos=0
for m in mini pc; do
  token="$(printf '%s\n' "$tokens" | awk -v m="$m" '$1 == m {print $2}')"
  if ! printf '%s' "$token" | grep -Eq '^[A-Za-z0-9]{32}$'; then
    echo "senal-kuma: $m: el servidor no devolvió un token válido" >&2
    fallos=$((fallos + 1))
    continue
  fi
  if ! printf '%s\n' "$token" | linea_en "$m" poner >/dev/null; then
    echo "senal-kuma: $m: no pude escribir secretos.env" >&2
    fallos=$((fallos + 1))
    continue
  fi
  if senal_configurada "$m"; then
    echo "$m: $SENAL_OK"
  else
    echo "senal-kuma: $m: motor doctor no dice '$SENAL_OK'" >&2
    fallos=$((fallos + 1))
  fi
done
exit $((fallos > 0))
