#!/usr/bin/env bash
# Canario de encargos en la flota: corre ops/canario_lanzador.py en la Mac, la Mini y la PC
# a la vez, cada una desde el árbol de su supervisor de encargos y con el intérprete de su
# servicio, y junta los informes en una tabla por máquina y una línea final.
# Antes lee en qué commit está el árbol de cada una: si no es el mismo, no corre en ninguna,
# porque el canario sobre una flota mezclada no prueba nada. Una máquina que no contesta
# falla y las demás corren igual.
# Cada máquina corre dos encargos: el que termina y el que falla a propósito (su checklist de
# despliegue sale rojo), que mide la fila «se detiene». Cada uno deja un PR mergeado en el
# repo del canario; el lanzador borra lo demás.
# Si la sesión de una máquina se cae, su lanzador termina solo y deja su informe en
# ~/.motor-canario/canario-informe.json.
# La salida trae ids, ramas y el repo del canario: no va a un log público.
# Sale 0 si en todas coincide con lo esperado y quedó limpio, 1 si no, 2 si la orden está mal.
#
# --repo R       El repo del canario, dueño/nombre (o MOTOR_CANARIO_REPO). No va en el repo.
# --maquina M    mac, mini o pc, o varias separadas por espacios. Sin ella, las tres.
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

maquinas="$MAQUINAS"
repo="${MOTOR_CANARIO_REPO:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)
      [ $# -ge 2 ] || { echo "--repo necesita dueño/nombre" >&2; exit 2; }
      repo="$2"; shift 2 ;;
    --maquina)
      [ $# -ge 2 ] || { echo "--maquina necesita mac, mini o pc" >&2; exit 2; }
      maquinas="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,/^[^#]/p' "$0" | grep '^#'; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
# Viaja en la línea de cmd.exe de la PC: sin espacios ni caracteres que cmd interprete.
[[ "$repo" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] \
  || { echo "falta el repo del canario como dueño/nombre: --repo o MOTOR_CANARIO_REPO" >&2; exit 2; }
# Dos lanzadores en la misma máquina comparten base y encargo: uno limpiaría lo del otro.
vistas=" "
for m in $maquinas; do
  case "$m" in
    mac|mini|pc) ;;
    *) echo "--maquina es mac, mini o pc, no '$m'" >&2; exit 2 ;;
  esac
  case "$vistas" in
    *" $m "*) echo "--maquina repite '$m'" >&2; exit 2 ;;
  esac
  vistas="$vistas$m "
done
# shellcheck disable=SC2086
set -- $maquinas
[ $# -gt 0 ] || { echo "--maquina necesita mac, mini o pc" >&2; exit 2; }
maquinas="$*"
corrida="flota-$(date -u +%Y%m%dT%H%M%SZ)"

# Una corrida tarda más de media hora y pasa minutos callada: ssh aguanta 15 min sin
# respuesta antes de dar la conexión por muerta.
VIVA=(-o ServerAliveInterval=60 -o ServerAliveCountMax=15)

# canario_de <maquina> <arbol>: el informe JSON del lanzador por stdout y sus avances por stderr.
canario_de() {
  local m=$1 arbol=$2
  if [ "$m" = pc ]; then
    ssh_a "${VIVA[@]}" "$(host_de pc)" \
      "cd /d %USERPROFILE% && \"$PC_PYTHON\" -B \"$arbol/ops/canario_lanzador.py\" --repo $repo --corrida $corrida"
    return
  fi
  ssh_a "${VIVA[@]}" "$(host_de "$m")" \
    "$(printf '%q ' bash -s -- "$m" "$arbol" "$repo" "$corrida")" <<'REMOTO'
set -uo pipefail
arbol=$2 repo=$3 corrida=$4
plist="$HOME/Library/LaunchAgents/ai.goncloud.motor-encargos.plist"
# El python3 de ssh puede ser el del sistema (3.9 en la Mini): solo lee el plist.
servicio="$(python3 - "$plist" <<'PY'
import plistlib, sys
with open(sys.argv[1], "rb") as f:
    datos = plistlib.load(f)
print(datos["ProgramArguments"][0])
print((datos.get("EnvironmentVariables") or {}).get("PATH", ""))
PY
)" || { echo "no pude leer el intérprete del servicio en $plist" >&2; exit 2; }
py="$(printf '%s\n' "$servicio" | sed -n 1p)"
ruta="$(printf '%s\n' "$servicio" | sed -n 2p)"
if [ -n "$ruta" ]; then export PATH="$ruta"; fi
# Por ssh el llavero de macOS no se abre y el ayudante osxkeychain de git falla (-25308);
# gh guarda su credencial fuera del llavero.
export GIT_CONFIG_COUNT=2 \
  GIT_CONFIG_KEY_0=credential.https://github.com.helper GIT_CONFIG_VALUE_0= \
  GIT_CONFIG_KEY_1=credential.https://github.com.helper GIT_CONFIG_VALUE_1='!gh auth git-credential'
cd "$HOME"
exec "$py" -B "$arbol/ops/canario_lanzador.py" --repo "$repo" --corrida "$corrida"
REMOTO
}

tmp="$(mktemp -d "${TMPDIR:-/tmp}/canario.XXXXXX")" || exit 2
trap 'rm -rf "$tmp"' EXIT

for m in $maquinas; do
  {
    if arbol="$(ruta_runtime_encargos_de "$m" 2>/dev/null)" && [ -n "$arbol" ] \
      && sha="$(sha_del_arbol "$m" "$arbol")"; then
      printf '%s\n' "$arbol" >"$tmp/$m.arbol"
      printf '%s\n' "$sha" >"$tmp/$m.sha"
    fi
  } &
done
wait

corren="" versiones="" sha=""
for m in $maquinas; do
  if [ -s "$tmp/$m.sha" ]; then
    corren="$corren $m"
    versiones="$versiones, $m en $(cut -c1-12 "$tmp/$m.sha")"
    sha="$(cat "$tmp/$m.sha")"
  else
    echo "FALLA $m: no contestó al leer en qué commit está el árbol de su supervisor" >&2
  fi
done
[ -n "$corren" ] || { echo "el canario no corrió: ninguna máquina contestó" >&2; exit 1; }
if [ "$(cat "$tmp"/*.sha | sort -u | wc -l | tr -d ' ')" != 1 ]; then
  echo "FALLA versión: ${versiones#, }. El canario sobre una flota mezclada no prueba nada: no lo corro." >&2
  exit 1
fi
echo "versión: ${corren# } en ${sha:0:12}; corrida $corrida"

inicio=$SECONDS
for m in $corren; do
  {
    canario_de "$m" "$(cat "$tmp/$m.arbol")" 2>&1 >"$tmp/$m.json" </dev/null \
      | while IFS= read -r linea; do printf '  %s | %s\n' "$m" "${linea%$'\r'}"; done >&2
    echo "${PIPESTATUS[0]}" >"$tmp/$m.rc"
  } &
done
wait

python3 - "$tmp" "$sha" "$((SECONDS - inicio))" "$maquinas" "$corren" <<'PY'
import json, sys
from pathlib import Path

tmp, sha, segundos, todas, corren = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4].split(), sys.argv[5].split()
BIEN = "coincide"
SIN_INFORME = "sin informe"


def veredicto(rc, informe):
    """Solo «coincide» cuenta como bien: con 1, la tabla puede coincidir y la limpieza no."""
    if not isinstance(informe, dict) or informe.get("codigo") != rc or rc not in (0, 1, 2):
        return f"{SIN_INFORME} (salió {rc})"
    if rc == 2:
        return "no pudo correr"
    version = str(informe.get("version") or "")
    if version != sha:
        return f"corrió en {version[:12] or 'una versión que no dijo'}, no en {sha[:12]}"
    if not informe.get("filas"):
        return f"sin tabla (salió {rc})"
    if not informe.get("coincide"):
        return "no coincide"
    return BIEN if rc == 0 else f"{BIEN}, pero no quedó limpio"


def leer(m):
    """El veredicto de la máquina y su informe, o un informe vacío si no lo dio."""
    if m not in corren:
        return "no contestó", {}
    try:
        rc = int((tmp / f"{m}.rc").read_text().strip())
    except (OSError, ValueError):
        rc = -1
    try:
        informe = json.loads((tmp / f"{m}.json").read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        informe = None
    juicio = veredicto(rc, informe)
    return juicio, ({} if juicio.startswith(SIN_INFORME) else informe)


def detalle(veredicto, informe):
    if veredicto.startswith(SIN_INFORME):
        return ["si su lanzador siguió solo, su informe queda en ~/.motor-canario/canario-informe.json"]
    if not informe:
        return []
    if "encargo" not in informe:
        return [f"no pudo {informe.get('paso') or 'arrancar'}: {informe.get('error')}"]
    lineas = [
        f"encargo {informe['encargo']}: {informe.get('fin') or 'sin fin'}"
        f" en {informe.get('segundos', 0) // 60} min {informe.get('segundos', 0) % 60} s"
    ]
    cierre = informe.get("cierre") or {}
    if cierre.get("vivos") or cierre.get("fallas") or cierre.get("supervisor") != "ausente":
        lineas.append(f"cierre sin terminar: {json.dumps(cierre, ensure_ascii=False)}")
    detencion = informe.get("detencion") or {}
    if detencion:
        lineas.append(
            f"encargo que falla a propósito {detencion.get('encargo')}: {detencion.get('fin') or 'sin fin'}"
        )
        cierre = detencion.get("cierre") or {}
        if cierre.get("vivos") or cierre.get("fallas") or cierre.get("supervisor") != "ausente":
            lineas.append(f"su cierre sin terminar: {json.dumps(cierre, ensure_ascii=False)}")
    if informe.get("error"):
        lineas.append(f"no pudo {informe.get('paso') or 'seguir'}: {informe['error']}")
    lineas += [
        f"{que}: {b['resto']}: {b['resultado']}" + (f" ({b['causa']})" if b.get("causa") else "")
        for que in ("limpieza", "recogida")
        for b in informe.get(que) or []
    ]
    return lineas


resultados = {m: leer(m) for m in todas}
for m, (juicio, informe) in resultados.items():
    print(f"\n## {m}: {juicio}")
    for linea in detalle(juicio, informe):
        print(linea)
    if informe.get("tabla"):
        print()
        print(informe["tabla"])

fallan = [m for m, (juicio, _) in resultados.items() if juicio != BIEN]
duracion = f"{segundos // 60} min {segundos % 60} s"
if fallan:
    print(f"\nel canario con {sha[:12]} falla en: {' '.join(fallan)} (tardó {duracion})")
    sys.exit(1)
print(f"\nel canario con {sha[:12]} coincide en: {' '.join(todas)} (tardó {duracion})")
PY
