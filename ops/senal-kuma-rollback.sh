#!/usr/bin/env bash
# Deshace ops/senal-kuma.sh: deja los dos monitores con active 0 (Kuma corriendo, luego un
# `docker restart`) y quita MOTOR_KUMA_URL de secretos.env en la Mini y la PC. Primero los
# monitores: sin línea y con el monitor activo, Kuma avisaría de un silencio que ya es esperado.
set -uo pipefail
export LC_ALL=C
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DIR/lib.sh"
. "$DIR/lib-kuma.sh"

servidor_kuma baja >/dev/null || { echo "rollback: el servidor no apagó los monitores" >&2; exit 1; }
fallos=0
for m in mini pc; do
  if linea_en "$m" quitar </dev/null >/dev/null; then
    echo "$m: sin MOTOR_KUMA_URL"
  else
    echo "rollback: $m: no pude quitar la línea de secretos.env" >&2
    fallos=$((fallos + 1))
  fi
done
exit $((fallos > 0))
