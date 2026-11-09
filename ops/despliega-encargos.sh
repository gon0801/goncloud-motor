#!/usr/bin/env bash
# Despliega el motor de encargos en la Mac, la Mini y/o la PC.
# Toca solo el árbol del supervisor de encargos (~/motor-sucesor o el WorkingDirectory
# del LaunchAgent ai.goncloud.motor-encargos) y su recibo en ~/.motor-encargos.
# No instala el motor viejo de planes (para eso, ops/instala-todo.sh) y nunca toca
# ~/motor, ~/motor-conf ni ~/.motor. No crea etiquetas ni escribe en el remoto.
# El SHA tiene que estar ya en origin/main. Es idempotente por máquina.
#
# --sha SHA      SHA a desplegar (por omisión, origin/main).
# --maquina M    Solo mac, mini o pc. Sin ella, las tres.
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"
raiz="${MOTOR_DESPLEGAR_RAIZ:-$(cd "$aqui/.." && pwd)}"
rama="${MOTOR_RAMA:-main}"

maquinas="$MAQUINAS"
sha=""
while [ $# -gt 0 ]; do
  case "$1" in
    --sha)
      [ $# -ge 2 ] || { echo "--sha necesita un SHA" >&2; exit 2; }
      sha="$2"; shift 2 ;;
    --maquina)
      [ $# -ge 2 ] || { echo "--maquina necesita mac, mini o pc" >&2; exit 2; }
      case "$2" in
        mac|mini|pc) maquinas="$2" ;;
        *) echo "--maquina es mac, mini o pc, no '$2'" >&2; exit 2 ;;
      esac
      shift 2 ;;
    -h|--help)
      sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$sha" ]; then
  sha="origin/$rama"
fi

# El remoto primero: si el SHA no está publicado, no se escribe ni un recibo.
reintenta 3 git -C "$raiz" fetch -q --force origin \
  || { echo "no pude traer origin" >&2; exit 3; }
sha="$(git -C "$raiz" rev-parse --verify "$sha^{commit}" 2>/dev/null)" \
  || { echo "el SHA '${sha}' no existe en $raiz" >&2; exit 3; }
if ! git -C "$raiz" merge-base --is-ancestor "$sha" "origin/$rama"; then
  echo "el SHA ${sha:0:12} no está en origin/$rama: no despliego nada" >&2
  exit 3
fi

# Programas remotos: el recibo vive en el HOME de cada máquina, nunca en el repo.
RECIBO_B64="$(printf '%s' 'import base64,json,os,pathlib,sys
base = pathlib.Path.home() / ".motor-encargos"
base.mkdir(parents=True, exist_ok=True)
nombre = sys.argv[-2]
datos = json.loads(base64.b64decode(sys.argv[-1]).decode("utf-8"))
p = base / nombre
t = p.with_suffix(p.suffix + ".tmp")
t.write_text(json.dumps(datos, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
os.replace(t, p)
' | _pc_b64)"
BORRA_B64="$(printf '%s' 'import pathlib,sys
(pathlib.Path.home() / ".motor-encargos" / sys.argv[-1]).unlink(missing_ok=True)
' | _pc_b64)"
ESTADO_B64="$(printf '%s' 'import json,os,pathlib,subprocess,sys
home = pathlib.Path.home()
def leer(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
pasada = leer(home / ".motor-encargos" / "supervisor-pasada.json")
recibo = leer(home / ".motor-encargos" / "instalacion.json")
pendiente = leer(home / ".motor-encargos" / "instalacion-pendiente.json")
vivo = "si" if os.name == "nt" else "no"
pid = pasada.get("pid")
if os.name != "nt" and isinstance(pid, int) and pid > 0:
    try:
        os.kill(pid, 0)
        vivo = "si"
    except OSError:
        pass
if os.name != "nt" and vivo == "no":
    try:
        r = subprocess.run(["pgrep", "-f", "motor.*supervisor"], capture_output=True, text=True)
        if r.returncode == 0 and (r.stdout or "").strip():
            vivo = "si"
    except Exception:
        pass
sys.stdout.write(str(pasada.get("sha") or "") + "\n")
sys.stdout.write(("si" if pasada.get("completa") else "no") + "\n")
sys.stdout.write(str((recibo.get("destino") or {}).get("codigo") or "") + "\n")
sys.stdout.write(vivo + "\n")
sys.stdout.write(str((pendiente.get("anterior") or {}).get("codigo") or "") + "\n")
sys.stdout.write(str((pendiente.get("destino") or {}).get("codigo") or "") + "\n")
sys.stdout.write(str((recibo.get("anterior") or {}).get("codigo") or "") + "\n")
' | _pc_b64)"

json_de_recibo() { # <tipo> <anterior> <destino> <maquina> [paso] [causa]
  python3 - "$1" "$2" "$3" "$4" "${5:-}" "${6:-}" <<'PY'
import datetime,json,sys
tipo, anterior, destino, maquina, paso, causa = sys.argv[1:7]
d = {
    "anterior": {"codigo": anterior},
    "destino": {"codigo": destino},
    "maquina": maquina,
    "hora": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
if tipo == "pendiente":
    d["paso"] = paso
    if causa:
        d["causa"] = causa
json.dump(d, sys.stdout, ensure_ascii=False, sort_keys=True)
PY
}

escribe_remoto() { # <maquina> <programa_b64> <args...>
  local m=$1 programa=$2
  shift 2
  if [ "$m" = pc ]; then
    py_pc_runtime_prog "$programa" "$@"
  else
    en_posix "$m" "$m" "$programa" "$@" <<'REMOTO'
set -euo pipefail
programa=$2
shift 2
python3 -c 'import base64,sys; programa=base64.b64decode(sys.argv.pop(1)); exec(programa)' "$programa" "$@"
REMOTO
  fi
}

escribe_recibo() { # <maquina> <nombre> <json>
  local b64
  b64="$(printf '%s' "$3" | _pc_b64)" || return 1
  escribe_remoto "$1" "$RECIBO_B64" "$2" "$b64"
}

borra_recibo() { # <maquina> <nombre>
  escribe_remoto "$1" "$BORRA_B64" "$2"
}

lee_estado_encargos() { # <maquina> -> sha de pasada, completa, recibo
  escribe_remoto "$1" "$ESTADO_B64" | tr -d '\r'
}

sha_del_arbol() { # <maquina> <ruta>
  local m=$1 ruta=$2 salida
  [ -n "$ruta" ] || return 1
  if [ "$m" = pc ]; then
    salida="$(ssh_a "$(host_de pc)" "git -C \"$ruta\" rev-parse HEAD" 2>/dev/null)" || return 1
  else
    salida="$(en_posix "$m" "$m" "$ruta" <<'REMOTO' 2>/dev/null
set -euo pipefail
git -C "$2" rev-parse HEAD
REMOTO
)" || return 1
  fi
  salida="$(printf '%s' "$salida" | tr -d '\r\n')"
  [ -n "$salida" ] || return 1
  printf '%s' "$salida"
}

mover_arbol() { # <maquina> <ruta> <sha>
  local m=$1 ruta=$2 sha=$3
  if [ "$m" = pc ]; then
    ssh_a "$(host_de pc)" "git -C \"$ruta\" fetch -q origin" || return 1
    ssh_a "$(host_de pc)" "git -C \"$ruta\" checkout -q --detach $sha" || return 1
    ssh_a "$(host_de pc)" "git -C \"$ruta\" cat-file -e $sha" || return 1
  else
    en_posix "$m" "$m" "$ruta" "$sha" <<'REMOTO'
set -euo pipefail
git -C "$2" fetch -q origin
git -C "$2" checkout -q --detach "$3"
git -C "$2" cat-file -e "$3"
REMOTO
  fi
}

servicio_cargado() { # <mac|mini>
  en_posix "$1" "$1" <<'REMOTO' >/dev/null 2>&1
set -euo pipefail
launchctl print "gui/$(id -u)/ai.goncloud.motor-encargos" >/dev/null
REMOTO
}

pc_iniciar() { # <ruta> [args...]
  local ruta=$1
  shift
  ssh_a "$(host_de pc)" "\"$PC_PYTHON\" -B \"$ruta/ops/iniciar_encargos_pc.py\" $*"
}

pc_estado_valido() { # <json>
  printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
s = d.get("supervisor") or {}
sys.exit(0 if not d.get("parado") and s.get("session") == 1 and s.get("in_job") is False else 1)'
}

pc_estado_parado() { # <json>
  printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
sys.exit(0 if d.get("parado") else 1)'
}

pc_esperar_parado() { # <ruta> <tope>
  local ruta=$1 tope=$2 fin=$((SECONDS + $2)) salida
  while :; do
    salida="$(pc_iniciar "$ruta" --estado 2>/dev/null)" \
      && pc_estado_parado "$salida" && return 0
    [ "$SECONDS" -ge "$fin" ] && return 1
    sleep 2
  done
}

pc_esperar_arranque() { # <ruta> <tope>
  local ruta=$1 fin=$((SECONDS + $2)) salida
  while :; do
    salida="$(pc_iniciar "$ruta" --estado 2>/dev/null)" \
      && pc_estado_valido "$salida" && return 0
    [ "$SECONDS" -ge "$fin" ] && return 1
    sleep 2
  done
}

falla() { # <maquina> <paso> <causa> <anterior>
  local m=$1 paso=$2 causa=$3 anterior=$4 escrito=1
  escribe_recibo "$m" instalacion-pendiente.json \
    "$(json_de_recibo pendiente "$anterior" "$sha" "$m" "$paso" "$causa")" >/dev/null 2>&1 \
    || escrito=0
  echo "FALLA $m en $paso: $causa" >&2
  if [ "$escrito" = 1 ]; then
    echo "No sigo con las demás. El pendiente de $m conserva el anterior (${anterior:0:12}) para volver." >&2
  else
    echo "No sigo con las demás. No pude escribir el pendiente de $m; el anterior era (${anterior:0:12})." >&2
  fi
  exit 1
}

for m in $maquinas; do
  paso="leer el árbol del supervisor"
  runtime="$(ruta_runtime_encargos_de "$m" 2>/dev/null)" \
    || falla "$m" "$paso" "no pude leer el árbol del supervisor" ""
  if [ -z "$runtime" ]; then
    falla "$m" "$paso" "no hay un árbol de encargos distinto de ~/motor" ""
  fi
  # Un reintento tras una falla a mitad no pierde el anterior: si ya hay un
  # pendiente o un recibo para el mismo destino, ese anterior es el que se
  # conserva para volver.
  estado="$(lee_estado_encargos "$m" 2>/dev/null)" || estado=""
  pasada_sha="$(printf '%s\n' "$estado" | sed -n '1p')"
  pasada_completa="$(printf '%s\n' "$estado" | sed -n '2p')"
  recibo_sha="$(printf '%s\n' "$estado" | sed -n '3p')"
  pasada_vivo="$(printf '%s\n' "$estado" | sed -n '4p')"
  pendiente_anterior="$(printf '%s\n' "$estado" | sed -n '5p')"
  pendiente_destino="$(printf '%s\n' "$estado" | sed -n '6p')"
  recibo_anterior="$(printf '%s\n' "$estado" | sed -n '7p')"

  cabeza="$(sha_del_arbol "$m" "$runtime" 2>/dev/null)" || cabeza=""
  anterior="$cabeza"
  if [ "$pendiente_destino" = "$sha" ] && [ -n "$pendiente_anterior" ]; then
    anterior="$pendiente_anterior"
  elif [ "$recibo_sha" = "$sha" ] && [ -n "$recibo_anterior" ]; then
    anterior="$recibo_anterior"
  fi
  escribe_recibo "$m" instalacion-pendiente.json \
    "$(json_de_recibo pendiente "$anterior" "$sha" "$m" "inicio")" \
    || falla "$m" "escribir el recibo pendiente" "no pude escribirlo en ~/.motor-encargos" "$anterior"

  sano=1
  if [ "$m" = pc ]; then
    pc_estado_valido "$(pc_iniciar "$runtime" --estado 2>/dev/null)" || sano=0
  else
    servicio_cargado "$m" && [ "$pasada_vivo" = "si" ] || sano=0
  fi
  if [ "$cabeza" = "$sha" ] && [ "$pasada_sha" = "$sha" ] \
    && [ "$pasada_completa" = "si" ] && [ "$recibo_sha" = "$sha" ] && [ "$sano" = 1 ]; then
    echo "$m: ya estaba en ${sha:0:12}; no reinicio"
  else
    if [ "$m" = pc ]; then
      paso="detener el supervisor"
      pc_iniciar "$runtime" --parar >/dev/null 2>&1 \
        || falla "$m" "$paso" "iniciar_encargos_pc.py --parar falló" "$anterior"
      pc_esperar_parado "$runtime" "${MOTOR_ESPERA_PARADA_PC:-30}" \
        || falla "$m" "$paso" "el vigilante no confirmó la parada" "$anterior"
    fi
    paso="mover el árbol a ${sha:0:12}"
    mover_arbol "$m" "$runtime" "$sha" \
      || falla "$m" "$paso" "git no dejó el árbol en ${sha:0:12}" "$anterior"
    paso="reiniciar el supervisor"
    if [ "$m" = pc ]; then
      pc_iniciar "$runtime" >/dev/null 2>&1 \
        || falla "$m" "$paso" "iniciar_encargos_pc.py no arrancó" "$anterior"
      pc_esperar_arranque "$runtime" "${MOTOR_ESPERA_ARRANQUE_PC:-90}" \
        || falla "$m" "$paso" "el supervisor no quedó en session 1 con in_job false" "$anterior"
    else
      reinicia_supervisor "$m" \
        || falla "$m" "$paso" "launchctl no relanzó ai.goncloud.motor-encargos" "$anterior"
    fi
  fi
  escribe_recibo "$m" instalacion.json \
    "$(json_de_recibo instalado "$anterior" "$sha" "$m")" \
    || falla "$m" "escribir el recibo" "no pude escribir ~/.motor-encargos/instalacion.json" "$anterior"
done

espera="${MOTOR_ESPERA_DESPLIEGUE:-120}"
case "$espera" in
  "" | *[!0-9]*) espera=120 ;;
esac
echo "Comprobando el despliegue en:$maquinas (hasta ${espera}s para la pasada con ${sha:0:12})"
if ! bash "$aqui/comprueba-despliegue.sh" --sha "$sha" --maquina "$maquinas" --espera "$espera" --reinicio; then
  echo "El código quedó en ${sha:0:12}, pero la comprobación no pasa en todas. Los pendientes conservan el anterior para volver." >&2
  exit 1
fi
for m in $maquinas; do
  borra_recibo "$m" instalacion-pendiente.json >/dev/null 2>&1 || true
done
echo "desplegado en:$maquinas en ${sha:0:12}"
