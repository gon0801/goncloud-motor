#!/usr/bin/env bash
# Despliega el motor de encargos en la Mac, la Mini y/o la PC.
# Toca solo el árbol del supervisor de encargos (~/motor-sucesor o el WorkingDirectory
# del LaunchAgent ai.goncloud.motor-encargos) y su recibo en ~/.motor-encargos.
# No instala el motor viejo de planes (para eso, ops/instala-todo.sh) y nunca toca
# ~/motor, ~/motor-conf ni ~/.motor. Tampoco necesita ~/motor ni ~/.motor: el supervisor
# lee maquina.toml y secretos.env de ~/.motor-encargos (python -m motor.encargos.retiro
# migrar los trae). No crea etiquetas ni escribe en el remoto.
# El SHA tiene que estar ya en origin/main. Es idempotente por máquina.
# Antes de mover ningún árbol, el SHA pliega el registro de cada máquina
# (ops/compuerta-plegado.sh); si en una no pliega, no se mueve ninguna.
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
      sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$sha" ]; then
  sha="origin/$rama"
fi

# El remoto primero: si el SHA no está publicado, no se escribe ni un recibo.
reintenta 3 git -C "$raiz" fetch -q --force origin \
  || { echo "no pude traer origin" >&2; exit 3; }
commit="$(git -C "$raiz" rev-parse --verify "$sha^{commit}" 2>/dev/null)" \
  || { echo "el SHA '$sha' no existe en $raiz" >&2; exit 3; }
sha="$commit"
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
ORDENA_B64="$(printf '%s' 'import pathlib,sys
sys.path.insert(0, sys.argv[1])
from motor.encargos.cli import main
base = pathlib.Path.home() / ".motor-encargos"
raise SystemExit(main(["reiniciar-supervisor", "--base", str(base), "--clave", sys.argv[2], "--tope", sys.argv[3]]))
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

pc_esperar_arranque() { # <ruta> <tope>
  local ruta=$1 fin=$((SECONDS + $2)) salida
  while :; do
    salida="$(pc_iniciar "$ruta" --estado 2>/dev/null)" \
      && pc_estado_valido "$salida" && return 0
    [ "$SECONDS" -ge "$fin" ] && return 1
    sleep 2
  done
}

preflight_falla() { # <maquina> <causa>
  echo "FALLA $1 en leer el árbol del supervisor: $2" >&2
  echo "No moví ninguna máquina: la lectura de todas falló antes de tocar los árboles." >&2
  exit 1
}

# reinicia_ordenado <maquina> <runtime> <clave> <tope>: deja la orden de #432 y
# deja que el supervisor drene los turnos y salga. Si no hay supervisor, lo
# arranca: no hay turnos que drenar.
reinicia_ordenado() {
  local m=$1 runtime=$2 clave=$3 tope=$4
  if [ "$m" = pc ]; then
    if pc_estado_valido "$(pc_iniciar "$runtime" --estado 2>/dev/null)"; then
      _pc_programa_stdin "$PC_PYTHON -B" "$ORDENA_B64" "$runtime" "$clave" "$tope" >/dev/null
    else
      pc_iniciar "$runtime" >/dev/null 2>&1 || return 1
      pc_esperar_arranque "$runtime" "${MOTOR_ESPERA_ARRANQUE_PC:-90}" || return 1
    fi
    return $?
  fi
  if servicio_cargado "$m"; then
    # El python3 del PATH de ssh es el del sistema (3.9 en la Mini) y no puede
    # importar tomllib; el del LaunchAgent sí. Se lee de ProgramArguments, como
    # ya hace comprueba-despliegue.sh, para no ejecutar motor con el intérprete
    # equivocado.
    en_posix "$m" "$m" "$runtime" "$clave" "$tope" <<'REMOTO' >/dev/null
set -euo pipefail
runtime=$2 clave=$3 tope=$4
label="ai.goncloud.motor-encargos"
plist="$HOME/Library/LaunchAgents/$label.plist"
py=python3
if [ -f "$plist" ]; then
  leido="$(python3 - "$plist" 2>/dev/null <<'PY'
import plistlib, sys
try:
    with open(sys.argv[1], "rb") as f:
        datos = plistlib.load(f)
    args = datos.get("ProgramArguments") if isinstance(datos, dict) else None
    if args and isinstance(args[0], str):
        print(args[0])
except Exception:
    pass
PY
)" || true
  if [ -n "$leido" ] && [ -x "$leido" ]; then py="$leido"; fi
fi
cd "$runtime"
"$py" -B -m motor reiniciar-supervisor --base "$HOME/.motor-encargos" --clave "$clave" --tope "$tope"
REMOTO
  else
    en_posix "$m" "$m" <<'REMOTO' >/dev/null
set -euo pipefail
label="ai.goncloud.motor-encargos"
plist="$HOME/Library/LaunchAgents/$label.plist"
[ -f "$plist" ] || { echo "el servicio $label no está instalado: $plist" >&2; exit 1; }
launchctl bootstrap "gui/$(id -u)" "$plist"
REMOTO
  fi
}

# compensar_movidas <movidas>: las que ya tienen el árbol nuevo reciben la orden
# de reinicio y se comprueban antes de salir. Se reinicia, no se regresa: el
# árbol ya está en el SHA pedido y volver atrás obligaría a mover dos veces.
compensar_movidas() {
  local movidas=$1 m runtime
  [ -n "$movidas" ] || return 0
  echo "Las máquinas ya movidas pasan a ${sha:0:12} ahora, para no dejar el código viejo sobre el árbol nuevo:" >&2
  for m in $movidas; do
    runtime="$(cat "$tmp/$m.runtime" 2>/dev/null)"
    [ -n "$runtime" ] || runtime="$(ruta_runtime_encargos_de "$m" 2>/dev/null)"
    if ! reinicia_ordenado "$m" "$runtime" "$clave_despliegue" "$tope_reinicio"; then
      echo "FALLA $m en reiniciar el supervisor: no pude pedir el reinicio ordenado" >&2
    fi
  done
  if bash "$aqui/comprueba-despliegue.sh" --sha "$sha" --maquina "$movidas" --espera "$espera" --reinicio; then
    for m in $movidas; do
      echo "$m: reiniciado y comprobado en ${sha:0:12}" >&2
    done
  else
    echo "Las máquinas movidas no confirmaron el reinicio; su pendiente conserva el anterior para volver." >&2
  fi
}

tmp="$(mktemp -d "${TMPDIR:-/tmp}/despliega.XXXXXX")" || exit 1
trap 'rm -rf "$tmp"' EXIT

# Primero comprobar: ssh, árbol del supervisor, recibo y que el SHA pliegue el
# registro de todas. Si una falla aquí no se mueve ninguna.
for m in $maquinas; do
  estado="$(lee_estado_encargos "$m" 2>/dev/null)" \
    || preflight_falla "$m" "no pude leer el estado del supervisor"
  runtime="$(ruta_runtime_encargos_de "$m" 2>/dev/null)" \
    || preflight_falla "$m" "no pude leer el árbol del supervisor"
  if [ -z "$runtime" ]; then
    preflight_falla "$m" "no hay un árbol de encargos distinto de ~/motor"
  fi
  cabeza="$(sha_del_arbol "$m" "$runtime" 2>/dev/null)" \
    || preflight_falla "$m" "no pude leer el árbol del supervisor"
  printf '%s\n' "$estado" >"$tmp/$m.estado"
  printf '%s\n' "$runtime" >"$tmp/$m.runtime"
  printf '%s\n' "$cabeza" >"$tmp/$m.cabeza"
done

if ! bash "$aqui/compuerta-plegado.sh" --sha "$sha" --maquina "$maquinas"; then
  echo "No moví ninguna máquina: ${sha:0:12} no pasa la compuerta de plegado en todas." >&2
  exit 1
fi

# La comprobación espera a que el supervisor viejo drene (hasta el tope), launchd
# relance y el supervisor nuevo complete una pasada. Ese plazo es el tope más un
# margen, no el tope solo: si fueran iguales, un turno que agote el tope deja la
# comprobación sin tiempo para el relanzamiento ni para la primera pasada.
margen="${MOTOR_MARGEN_DESPLEGUE:-120}"
case "$margen" in
  "" | *[!0-9]*) margen=120 ;;
esac
if [ "$margen" -gt 900 ]; then margen=900; fi
tope_reinicio="${MOTOR_ESPERA_REINICIO:-120}"
case "$tope_reinicio" in
  "" | *[!0-9]*) tope_reinicio=120 ;;
esac
if [ "$tope_reinicio" -gt 900 ]; then tope_reinicio=900; fi
espera="${MOTOR_ESPERA_DESPLIEGUE:-$((tope_reinicio + margen))}"
case "$espera" in
  "" | *[!0-9]*) echo "MOTOR_ESPERA_DESPLIEGUE inválida: $espera" >&2; exit 2 ;;
esac
# Si el operador fija la espera, el drenaje se recorta para que el margen del
# relanzamiento y la primera pasada nunca se lo coma el tope.
if [ "$espera" -lt $((tope_reinicio + margen)) ]; then
  if [ "$espera" -gt "$margen" ]; then
    tope_reinicio=$((espera - margen))
  else
    tope_reinicio=0
  fi
fi
clave_despliegue="despliegue-${sha:0:12}-$$"

necesitan=""
for m in $maquinas; do
  estado="$(cat "$tmp/$m.estado")"
  runtime="$(cat "$tmp/$m.runtime")"
  cabeza="$(cat "$tmp/$m.cabeza")"
  pasada_sha="$(printf '%s\n' "$estado" | sed -n '1p')"
  pasada_completa="$(printf '%s\n' "$estado" | sed -n '2p')"
  recibo_sha="$(printf '%s\n' "$estado" | sed -n '3p')"
  pasada_vivo="$(printf '%s\n' "$estado" | sed -n '4p')"
  pendiente_anterior="$(printf '%s\n' "$estado" | sed -n '5p')"
  pendiente_destino="$(printf '%s\n' "$estado" | sed -n '6p')"
  recibo_anterior="$(printf '%s\n' "$estado" | sed -n '7p')"
  # El pendiente que ya hubiera manda sobre el HEAD del árbol: si el reintento
  # falla antes de leer el árbol, el anterior original no se pierde.
  anterior_previo=""
  if [ "$pendiente_destino" = "$sha" ] && [ -n "$pendiente_anterior" ]; then
    anterior_previo="$pendiente_anterior"
  fi
  anterior="$cabeza"
  if [ -n "$anterior_previo" ]; then
    anterior="$anterior_previo"
  elif [ "$recibo_sha" = "$sha" ] && [ -n "$recibo_anterior" ]; then
    anterior="$recibo_anterior"
  fi
  printf '%s\n' "$anterior" >"$tmp/$m.anterior"
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
    necesitan="$necesitan $m"
  fi
done

# Mover todos los árboles primero. El reinicio va después, en su propio bucle.
movidas=""
for m in $necesitan; do
  runtime="$(cat "$tmp/$m.runtime")"
  anterior="$(cat "$tmp/$m.anterior")"
  if ! escribe_recibo "$m" instalacion-pendiente.json \
    "$(json_de_recibo pendiente "$anterior" "$sha" "$m" "inicio")" >/dev/null 2>&1; then
    echo "FALLA $m en escribir el recibo pendiente: no pude escribirlo en ~/.motor-encargos" >&2
    compensar_movidas "$movidas"
    echo "No sigo con las demás. El pendiente de $m conserva el anterior (${anterior:0:12}) para volver." >&2
    exit 1
  fi
  if ! mover_arbol "$m" "$runtime" "$sha"; then
    echo "FALLA $m en mover el árbol a ${sha:0:12}: git no dejó el árbol en ${sha:0:12}" >&2
    compensar_movidas "$movidas"
    echo "No sigo con las demás. El pendiente de $m conserva el anterior (${anterior:0:12}) para volver." >&2
    exit 1
  fi
  movidas="$movidas $m"
  if ! escribe_recibo "$m" instalacion.json \
    "$(json_de_recibo instalado "$anterior" "$sha" "$m")" >/dev/null 2>&1; then
    echo "FALLA $m en escribir el recibo: no pude escribir ~/.motor-encargos/instalacion.json" >&2
    compensar_movidas "$movidas"
    echo "No sigo con las demás. El pendiente de $m conserva el anterior (${anterior:0:12}) para volver." >&2
    exit 1
  fi
done

# Segundo bucle: reiniciar todas las movidas, sin parar en la primera que falle,
# para que ninguna quede con el código viejo sobre el árbol nuevo.
for m in $movidas; do
  runtime="$(cat "$tmp/$m.runtime")"
  anterior="$(cat "$tmp/$m.anterior")"
  if ! reinicia_ordenado "$m" "$runtime" "$clave_despliegue" "$tope_reinicio"; then
    echo "FALLA $m en reiniciar el supervisor: no pude pedir el reinicio ordenado; el pendiente conserva el anterior (${anterior:0:12})" >&2
  fi
done

echo "Comprobando el despliegue en:$maquinas (drenaje hasta ${tope_reinicio}s, espera hasta ${espera}s para la pasada con ${sha:0:12})"
if ! bash "$aqui/comprueba-despliegue.sh" --sha "$sha" --maquina "$maquinas" --espera "$espera" --reinicio; then
  echo "El código quedó en ${sha:0:12}, pero la comprobación no pasa en todas. Los pendientes conservan el anterior para volver." >&2
  exit 1
fi
for m in $maquinas; do
  borra_recibo "$m" instalacion-pendiente.json >/dev/null 2>&1 || true
done
echo "desplegado en:$maquinas en ${sha:0:12}"
