#!/usr/bin/env bash
# Deshace ops/senal-kuma.sh: deja los monitores que gestiona con active 0 (Kuma corriendo, luego un
# `docker restart`) y quita MOTOR_KUMA_URL del secreto de planes de las tres máquinas. En el secreto
# de encargos solo borra la línea donde el script creó el monitor (la MacBook): en la Mini y la PC
# esa dirección es del operador y se conserva. Primero los monitores: sin línea y con el monitor
# activo, Kuma avisaría de un silencio que ya es esperado.
set -uo pipefail
export LC_ALL=C
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DIR/lib.sh"
. "$DIR/lib-kuma.sh"

encargos="$(servidor_kuma baja)" || { echo "rollback: el servidor no apagó los monitores" >&2; exit 1; }
fallos=0
for m in mac mini pc; do
  if linea_en "$m" quitar .motor </dev/null >/dev/null; then
    echo "$m: sin MOTOR_KUMA_URL en .motor"
  else
    echo "rollback: $m: no pude quitar la línea de .motor/secretos.env" >&2
    fallos=$((fallos + 1))
  fi
  if printf '%s\n' "$encargos" | grep -qx "$m-encargos"; then
    if linea_en "$m" quitar .motor-encargos </dev/null >/dev/null; then
      echo "$m: sin MOTOR_KUMA_URL en .motor-encargos"
    else
      echo "rollback: $m: no pude quitar la línea de .motor-encargos/secretos.env" >&2
      fallos=$((fallos + 1))
    fi
  fi
done
exit $((fallos > 0))
