#!/usr/bin/env bash
# Sale 0 solo si Kuma recibe de verdad la señal del motor de la MacBook, la Mini y la PC: Kuma corre,
# los monitores de planes de las tres máquinas y los de encargos que el script gestiona están activos
# y conectados al aviso "Server Alerts" (activo), cada uno tiene una señal UP en los últimos 3 min,
# y la dirección escrita en cada máquina responde desde esa máquina. Si el túnel de la Mini
# (ai.goncloud.motor-senal-tunel) es su camino y no responde, lo dice.
# Kuma guarda heartbeat.time en UTC, igual que datetime('now') de sqlite
# (medido en el servidor; ver el commit G.2), así que la comparación vive en la consulta, sin zonas de
# por medio. Espera hasta MOTOR_KUMA_TOPE s (8 min) por la primera señal. Además: `motor doctor` dice
# "configurada" en las tres máquinas y los dos secretos.env de la Mac y la Mini siguen en 600 con la
# línea puesta. Nunca lee config ni push_token.
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
for clave in ("mac", "mini", "pc"):
    activo, avisos, senales = vistos.get(clave, [0, 0, 0])
    if not activo:
        print(f"el monitor del motor de {clave} no existe o no está activo")
    if not avisos:
        print(f"el monitor de {clave} no está conectado a un aviso Server Alerts activo")
    if not senales:
        print(f"el monitor de {clave} no tiene señal UP en los últimos 3 min")
for clave in sorted(k for k in vistos if k.endswith("-encargos")):
    maquina = clave[: -len("-encargos")]
    activo, avisos, senales = vistos[clave]
    if not activo:
        print(f"el monitor de encargos de {maquina} no existe o no está activo")
    if not avisos:
        print(f"el monitor de encargos de {maquina} no está conectado a un aviso Server Alerts activo")
    if not senales:
        print(f"el monitor de encargos de {maquina} no tiene señal UP en los últimos 3 min")
'
}

while :; do
  fallas="$(fallas_de_kuma)"
  [ -z "$fallas" ] && break
  [ "$SECONDS" -lt "$hasta" ] || break
  sleep "${MOTOR_ESPERA_PASO:-15}"
done
[ -z "$fallas" ] || while IFS= read -r l; do problemas+=("$l"); done <<<"$fallas"

for m in mac mini pc; do
  senal_configurada "$m" || problemas+=("$m: motor doctor no dice '$SENAL_OK'")
done
for m in mac mini; do
  while IFS= read -r l; do
    [ -z "$l" ] || problemas+=("$m: $l")
  done < <(revisa_secretos "$m" </dev/null 2>/dev/null)
done
for m in mac mini pc; do
  while IFS= read -r l; do
    [ -z "$l" ] || problemas+=("$m: $l")
  done < <(problemas_direccion "$m")
done

if [ "${#problemas[@]}" -gt 0 ]; then
  printf 'checklist: %s\n' "${problemas[@]}" >&2
  exit 1
fi
echo "checklist: Kuma recibe la señal del motor de la MacBook, de la Mini y de la PC"
