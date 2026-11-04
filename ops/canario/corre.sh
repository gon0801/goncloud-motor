#!/usr/bin/env bash
# Prueba de fuego (MOTOR 01, bloque I): le entrega ops/canario/plan.md a cada máquina del
# canario (Mini y PC menos la máquina en la que corre; nunca la Mac) por su canal normal
# -un turno de agente con la skill `motor`- y, cuando el motor de cada una lleva un bloque
# a medias, reinicia ESA máquina, solo después de ver su servicio arriba. Cada máquina se
# reinicia una sola vez (marca en $ESTADO), así que
# repetir el script no vuelve a reiniciar nada. Después, `ops/canario/verifica.sh` espera el final.
#   MOTOR_CANARIO_PLAN        el plan a entregar (por omisión, plan.md de esta carpeta)
#   MOTOR_CANARIO_ESTADO      carpeta de marcas de reinicio (por omisión ~/.motor-arranque/canario)
#   MOTOR_CANARIO_SONDEO_S    cada cuánto mira el estado (20)
#   MOTOR_CANARIO_TOPE_ENTREGA_S / _BLOQUE_S / _SERVICIO_S   cuánto espera cada paso (600 / 7200 / 600)
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/../lib.sh"

PLAN="${MOTOR_CANARIO_PLAN:-$aqui/plan.md}"
ESTADO="${MOTOR_CANARIO_ESTADO:-$HOME/.motor-arranque/canario}"
SONDEO_S="${MOTOR_CANARIO_SONDEO_S:-20}"
TOPE_ENTREGA_S="${MOTOR_CANARIO_TOPE_ENTREGA_S:-600}"
TOPE_BLOQUE_S="${MOTOR_CANARIO_TOPE_BLOQUE_S:-7200}"
TOPE_SERVICIO_S="${MOTOR_CANARIO_TOPE_SERVICIO_S:-600}"
# Mini y PC menos la máquina en la que corre (nunca la Mac): la lista sale de maquinas_canario.
if ! CANARIO_MAQUINAS="$(maquinas_canario)"; then
  echo "me niego: no pude identificar las máquinas del canario" >&2
  exit 2
fi
if [ -z "$(printf '%s' "$CANARIO_MAQUINAS" | tr -d '[:space:]')" ]; then
  echo "me niego: no queda ninguna máquina que reiniciar" >&2
  exit 2
fi

[ -f "$PLAN" ] || { echo "no encuentro el plan: $PLAN" >&2; exit 2; }
python3 -I - "$PLAN" <<'PY' || exit 2
import sys
sys.exit(0 if "autopiloto: motor" in open(sys.argv[1], encoding="utf-8").read() else 1)
PY
PLAN_ID="$(sed -n 's/^plan: *//p' "$PLAN" | head -1)"
mkdir -p "$ESTADO"
d="$(mktemp -d)"
trap 'rm -rf "$d"' EXIT

marca() { case "$1" in mini) echo claw ;; pc) echo Hermi ;; esac; }

posix='export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; if python3 -c "import tomllib" 2>/dev/null; then py=python3; else py="uv run --python 3.12 python"; fi; cd "$HOME/motor" && PYTHONPATH=. $py -m motor'
pc="cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python -m motor"

# estado_de <m>: JSON de `motor estado` en $d/<m>.estado (vacío si el motor no contesta).
estado_de() {
  local m=$1
  corre "$m" "$posix estado --home \"\$HOME/.motor\" --json" "$pc estado --home %USERPROFILE%\\.motor --json" \
    2>"$d/$m.estado.err" | tr -d '\r' >"$d/$m.estado"
}

# fase_de <m>: ausente | corriendo | a_medias | hecho   (del plan canario, según `motor estado`)
fase_de() {
  estado_de "$1"
  python3 -I - "$d/$1.estado" <<'PY'
import json, sys

txt = open(sys.argv[1], encoding="utf-8", errors="replace").read()
datos = None
for i, linea in enumerate(txt.splitlines()):
    if linea.lstrip().startswith("["):
        try:
            datos = json.loads("\n".join(txt.splitlines()[i:]))
            break
        except ValueError:
            pass
plan = next((p for p in datos or [] if p.get("plan") == "canario"), None)
if plan is None:
    print("ausente")
elif plan.get("hecho"):
    print("hecho")
elif any(b["estado"] in ("implementando", "revisando") for b in plan["bloques"]):
    print("a_medias")
else:
    print("corriendo")
PY
}

espera_fase() { # espera_fase <m> <tope_s> <fase...>: imprime la fase alcanzada o sale 1 al vencer el tope
  local m=$1 tope=$2 fin f
  shift 2
  fin=$((SECONDS + tope))
  while :; do
    f="$(fase_de "$m")"
    for q in "$@"; do [ "$f" = "$q" ] && { echo "$f"; return 0; }; done
    [ "$f" = hecho ] && { echo hecho; return 1; }
    [ $SECONDS -ge $fin ] && { echo "$f"; return 1; }
    sleep "$SONDEO_S"
  done
}

mensaje() { # mensaje <m> <carpeta del plan en esa máquina> <comando motor> <home> <nombre>
  cat <<MSG
Tengo un plan para el motor de esta máquina. Usa tu skill «motor»: no lo implementes tú.
1. Valida el plan: $3 valida $2 --home $4
2. Entrégalo: $3 entrega $2 --home $4 --json --por $5
3. Contéstame con el recibo (tomado, encolado o rechazado). Si lo rechazan, di los errores tal cual.
El plan está en $2. No lo cambies.
MSG
}

entrega() { # entrega <m>: manda plan.md + mensaje por tar y le habla al agente de esa marca
  local m=$1 nombre dir cmd
  nombre="$(marca "$m")"
  mkdir -p "$d/$m.envio"
  cp "$PLAN" "$d/$m.envio/plan.md"
  if [ "$m" = pc ]; then
    mensaje "$m" "%USERPROFILE%\\canario\\plan.md" "$pc" "%USERPROFILE%\\.motor" "$nombre" >"$d/$m.envio/mensaje.txt"
    tar -c -C "$d/$m.envio" plan.md mensaje.txt |
      ssh_a "$(host_de pc)" "(if not exist %USERPROFILE%\\canario mkdir %USERPROFILE%\\canario) & tar -x -C %USERPROFILE%\\canario" || return 3
    ssh_a "$(host_de pc)" "hermes chat --query-file %USERPROFILE%\\canario\\mensaje.txt --oneshot" >"$d/$m.canal" 2>&1
  else
    mensaje "$m" '$HOME/canario/plan.md' "$posix" '$HOME/.motor' "$nombre" >"$d/$m.envio/mensaje.txt"
    tar -c -C "$d/$m.envio" plan.md mensaje.txt |
      ssh_a "$(host_de "$m")" "$(printf '%q ' bash -c 'mkdir -p "$HOME/canario" && tar -x -C "$HOME/canario"')" || return 3
    ssh_a "$(host_de "$m")" "$(printf '%q ' bash -c '"$HOME/oc-forja.sh" agent --message-file "$HOME/canario/mensaje.txt" --json --timeout 900')" >"$d/$m.canal" 2>&1
  fi
  return 0
}

entrega_directa() { # entrega_directa <m>: si el agente no la dejó tomada, el mismo `motor entrega` de su skill, sin el agente
  local m=$1 nombre
  nombre="$(marca "$m")"
  corre "$m" "$posix entrega \"\$HOME/canario/plan.md\" --home \"\$HOME/.motor\" --json --por $nombre" \
    "$pc entrega %USERPROFILE%\\canario\\plan.md --home %USERPROFILE%\\.motor --json --por $nombre" >"$d/$m.directa" 2>&1
}

cortes_de() { # cortes_de <m>: cuántos cortes (reinicios a media corrida) anotó el motor de esa máquina para el plan
  local py='if python3 -c "import sqlite3" 2>/dev/null; then py=python3; else py="uv run --python 3.12 python"; fi; $py -'
  if [ "$1" = pc ]; then
    ssh_a "$(host_de pc)" "$PC_UV run --python 3.12 python - %USERPROFILE%\\.motor $PLAN_ID \"\" \"\"" <"$aqui/resumen.py" 2>/dev/null
  else
    ssh_a "$(host_de "$1")" "$(printf '%q ' bash -c "$py \"\$HOME/.motor\" $PLAN_ID \"\" \"\"")" <"$aqui/resumen.py" 2>/dev/null
  fi | tr -d '\r' | python3 -I -c 'import json,sys; l=sys.stdin.read().splitlines(); print(json.loads(l[-1]).get("cortes", 0) if l else 0)' 2>/dev/null || echo 0
}

servicio_arriba() { # servicio_arriba <m>
  corre "$1" "launchctl print system/$SERVICIO_MAC" "schtasks /query /tn $TAREA_PC" >/dev/null 2>&1
}

puede_reiniciar() { # la Mini necesita `sudo -n` sin contraseña; en la PC `shutdown` va sin más
  [ "$1" = pc ] || ssh_a "$(host_de "$1")" "sudo -n -l shutdown" >/dev/null 2>&1
}

reinicia() { # reinicia <m>: la orden de reinicio de esa máquina; el ssh se corta al caer, no importa el código
  if [ "$1" = pc ]; then
    ssh_a "$(host_de pc)" "shutdown /r /t 0"
  else
    ssh_a "$(host_de "$1")" "sudo -n shutdown -r now"
  fi
}

lleva() { # lleva <m>: todo el recorrido de una máquina; 0 = quedó reiniciada a media corrida
  local m=$1 nombre fase fin
  nombre="$(marca "$m")"
  if [ -e "$ESTADO/$m.reiniciada" ]; then
    echo "$nombre: ya la reinicié antes ($(cat "$ESTADO/$m.reiniciada")); no la toco otra vez"
    return 0
  fi
  fase="$(fase_de "$m")"
  if [ "$fase" = hecho ] && [ "$(cortes_de "$m")" -ge 1 ] 2>/dev/null; then
    # Ya terminó y su motor anotó el corte: el reinicio ocurrió aunque se perdiera la marca.
    date -u +%Y-%m-%dT%H:%M:%SZ >"$ESTADO/$m.reiniciada"
    echo "$nombre: el canario ya terminó y hay un corte anotado; cuenta como reiniciada, no toco nada"
    return 0
  fi
  if [ "$fase" = ausente ]; then
    echo "$nombre: entregando el plan por su canal normal"
    entrega "$m" || { echo "$nombre: no pude copiarle el plan (ssh)" >&2; return 3; }
    fase="$(espera_fase "$m" "$TOPE_ENTREGA_S" corriendo a_medias)" || {
      echo "$nombre: el agente no dejó el plan tomado en ${TOPE_ENTREGA_S}s (fase: $fase); corro su mismo \`motor entrega\`. Contestó:" >&2
      tail -n 15 "$d/$m.canal" 2>/dev/null | sed 's/^/    | /' >&2
      entrega_directa "$m"
      fase="$(espera_fase "$m" "$TOPE_ENTREGA_S" corriendo a_medias)" || {
        echo "$nombre: el motor no tomó el plan en ${TOPE_ENTREGA_S}s (fase: $fase). \`motor entrega\` dijo:" >&2
        tail -n 15 "$d/$m.directa" 2>/dev/null | sed 's/^/    | /' >&2
        return 4
      }
    }
  else
    echo "$nombre: el motor ya tiene el plan (fase: $fase); no lo entrego de nuevo"
  fi
  if [ "$fase" != a_medias ]; then
    fase="$(espera_fase "$m" "$TOPE_BLOQUE_S" a_medias)" || {
      echo "$nombre: no vi un bloque a medias (fase: $fase); no reinicio" >&2; return 5
    }
  fi
  echo "$nombre: hay un bloque a medias; compruebo que su servicio esté arriba"
  fin=$((SECONDS + TOPE_SERVICIO_S))
  until servicio_arriba "$m"; do
    [ $SECONDS -ge $fin ] && { echo "$nombre: el servicio no está arriba; no reinicio" >&2; return 6; }
    sleep "$SONDEO_S"
  done
  puede_reiniciar "$m" || { echo "$nombre: no puede reiniciarse sin contraseña (solo una persona: sudoers NOPASSWD para shutdown); no reinicio" >&2; return 7; }
  date -u +%Y-%m-%dT%H:%M:%SZ >"$ESTADO/$m.reiniciada"   # antes de reiniciar: nunca dos veces
  echo "$nombre: reiniciando $m"
  reinicia "$m" >"$d/$m.reinicio" 2>&1
  return 0
}

pids=() fallas=0
for m in $CANARIO_MAQUINAS; do
  ( lleva "$m" ) &
  pids+=("$!")
done
for p in "${pids[@]}"; do wait "$p" || fallas=$((fallas + 1)); done
if [ "$fallas" -gt 0 ]; then echo "$fallas máquina(s) no quedaron reiniciadas a media corrida" >&2; exit 1; fi
echo "reiniciadas a media corrida: $CANARIO_MAQUINAS; sigue ops/canario/verifica.sh"
