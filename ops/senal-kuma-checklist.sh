#!/usr/bin/env bash
# Sale 0 solo si Kuma recibe de verdad la señal del motor de la Mini y de la PC: Kuma corre, los dos
# monitores están activos y conectados al aviso "Server Alerts" (activo), y cada uno tiene una señal UP
# en los últimos 3 min. Kuma guarda heartbeat.time en UTC, igual que datetime('now') de sqlite (medido
# en el servidor; ver el commit G.2), así que la comparación vive en la consulta, sin zonas de por medio.
# Espera hasta MOTOR_KUMA_TOPE s (8 min) por la primera señal. Además: `motor doctor` dice "configurada"
# en las dos máquinas y secretos.env de la Mini sigue en 600. Nunca lee config ni push_token.
set -uo pipefail
export LC_ALL=C
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DIR/lib.sh"
. "$DIR/lib-kuma.sh"

tope="${MOTOR_KUMA_TOPE:-480}"
hasta=$((SECONDS + tope))
problemas=()

kuma_corre() {
  [ "$(ssh_a "$KUMA_SSH" docker inspect -f '{{.State.Running}}' uptime-kuma </dev/null 2>/dev/null | tr -d '[:space:]')" = true ]
}

# fallas_de_kuma: una línea por cosa que falta (vacío si todo bien).
fallas_de_kuma() {
  local estado
  kuma_corre || { echo "Kuma no corre"; return; }
  estado="$(servidor_kuma estado 2>/dev/null)" || { echo "no pude consultar kuma.db"; return; }
  printf '%s\n' "$estado" | python3 -I -c '
import sys
vistos = {}
for linea in sys.stdin:
    p = linea.split()
    if len(p) == 4:
        vistos[p[0]] = [int(x) for x in p[1:]]
for clave in ("mini", "pc"):
    activo, avisos, senales = vistos.get(clave, [0, 0, 0])
    if not activo:
        print(f"el monitor del motor de {clave} no existe o no está activo")
    if not avisos:
        print(f"el monitor de {clave} no está conectado a un aviso Server Alerts activo")
    if not senales:
        print(f"el monitor de {clave} no tiene señal UP en los últimos 3 min")
'
}

while :; do
  fallas="$(fallas_de_kuma)"
  [ -z "$fallas" ] && break
  [ "$SECONDS" -lt "$hasta" ] || break
  sleep "${MOTOR_ESPERA_PASO:-15}"
done
[ -z "$fallas" ] || while IFS= read -r l; do problemas+=("$l"); done <<<"$fallas"

for m in mini pc; do
  senal_configurada "$m" || problemas+=("$m: motor doctor no dice '$SENAL_OK'")
done
modo="$(corre mini 'stat -f %Lp "$HOME/.motor/secretos.env" 2>/dev/null || stat -c %a "$HOME/.motor/secretos.env"' x </dev/null 2>/dev/null | tr -d '[:space:]')"
[ "$modo" = 600 ] || problemas+=("mini: secretos.env no está en 600 (modo: ${modo:-desconocido})")

if [ "${#problemas[@]}" -gt 0 ]; then
  printf 'checklist: %s\n' "${problemas[@]}" >&2
  exit 1
fi
echo "checklist: Kuma recibe la señal del motor de la Mini y de la PC"
