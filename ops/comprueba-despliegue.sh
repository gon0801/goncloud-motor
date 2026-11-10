#!/usr/bin/env bash
# Comprueba, por máquina, que el despliegue del motor de encargos quedó en el SHA nuevo.
# Lee solo el recibo del motor de encargos (~/.motor-encargos/instalacion.json); no cae
# al recibo del motor viejo de planes (~/.motor). Tampoco despliega ni revierte nada.
# Para instalar el motor viejo de planes usa ops/instala-todo.sh; este comprobador no lo mira.
# Es idempotente: solo lee y reintenta la lectura.
#
# --sha SHA     SHA que debe estar instalado (por omisión, HEAD del repo).
# --maquina M   Una sola máquina: mac, mini o pc.
# --espera S    Segundos acotados a esperar una pasada nueva con ese SHA.
# --reinicio    El despliegue ya ordenó el reinicio: sin pasada nueva se dice
#               "el reinicio falló" por máquina.
# --ensayo      No toca ssh: imprime lo que comprobaría y sale 0.
# --recibo-viejo  Lee primero ~/.motor/instalacion.json. Solo lo usa instala-todo.sh
#               para el motor viejo de planes; el motor de encargos no lo toca.
set -uo pipefail
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

sha=""
espera=0
ensayo=0
reinicio=0
recibo_viejo=0
maquinas="$MAQUINAS"
while [ $# -gt 0 ]; do
  case "$1" in
    --sha) sha="${2:-}"; shift 2 ;;
    --maquina) maquinas="${2:-}"; shift 2 ;;
    --espera) espera="${2:-}"; shift 2 ;;
    --reinicio) reinicio=1; shift ;;
    --ensayo) ensayo=1; shift ;;
    --recibo-viejo) recibo_viejo=1; shift ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
case "$espera" in
  "" | *[!0-9]*) echo "espera inválida: $espera" >&2; exit 2 ;;
esac
if [ -z "$sha" ]; then
  sha="$(git -C "$aqui/.." rev-parse HEAD)" || exit 2
fi
case "$sha" in
  "" | *[!0-9a-fA-F]*) echo "SHA inválido: $sha" >&2; exit 2 ;;
esac

# El registro se pliega con el mismo código y el mismo intérprete que el supervisor,
# no con una copia de la regla. El intérprete sale del LaunchAgent (Mac y Mini) o es
# el de motor-runtime (PC).
PROGRAMA='import json,os,pathlib,subprocess,sys
home = pathlib.Path.home()
recibo_viejo = sys.argv[-1] == "1"

def leer(ruta):
    try:
        return json.loads(pathlib.Path(ruta).read_text(encoding="utf-8"))
    except Exception:
        return {}

# El motor de encargos usa su propio recibo. Solo instala-todo.sh pide el viejo.
recibo = "-"
rutas = [home / ".motor" / "instalacion.json"] if recibo_viejo else []
rutas.append(home / ".motor-encargos" / "instalacion.json")
for ruta in rutas:
    doc = leer(ruta)
    if isinstance(doc, dict):
        valor = str((doc.get("destino") or {}).get("codigo") or "")
        if valor:
            recibo = valor
            break

pasada = leer(home / ".motor-encargos" / "supervisor-pasada.json")
if not isinstance(pasada, dict):
    pasada = {}
arbol_ruta = str(pasada.get("ruta") or "")
arbol = "-"
if arbol_ruta:
    try:
        r = subprocess.run(
            ["git", "-C", arbol_ruta, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            arbol = r.stdout.strip()
    except Exception:
        pass
vivo = "no"
pid = pasada.get("pid")
if isinstance(pid, int) and pid > 0:
    if os.name == "nt":
        try:
            r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True, text=True, timeout=10)
            if str(pid) in (r.stdout or ""):
                vivo = "si"
        except Exception:
            pass
    else:
        try:
            os.kill(pid, 0)
            vivo = "si"
        except OSError:
            pass
if vivo == "no" and os.name != "nt":
    try:
        r = subprocess.run(["pgrep", "-f", "motor.*supervisor"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and (r.stdout or "").strip():
            vivo = "si"
    except Exception:
        pass

def interprete_supervisor():
    if os.name == "nt":
        return sys.executable
    try:
        import plistlib
        plist = home / "Library" / "LaunchAgents" / "ai.goncloud.motor-encargos.plist"
        datos = plistlib.loads(plist.read_bytes())
        argumentos = datos.get("ProgramArguments") if isinstance(datos, dict) else None
        if argumentos and isinstance(argumentos[0], str) and pathlib.Path(argumentos[0]).exists():
            return argumentos[0]
    except Exception:
        pass
    return sys.executable

PLIEGA = """import pathlib,sys
sys.path.insert(0, sys.argv[1])
from motor.encargos.registro import Registro
from motor.encargos.ciclo import plegar_aislado
registro = Registro(pathlib.Path.home() / ".motor-encargos")
ids = registro.ids()
fallan = sum(1 for ident in ids if plegar_aislado(registro, ident) is None)
print(len(ids), fallan)
"""
registro = ""
if arbol_ruta and (home / ".motor-encargos" / "registro.sqlite").is_file():
    try:
        r = subprocess.run(
            [interprete_supervisor(), "-B", "-c", PLIEGA, arbol_ruta],
            capture_output=True, text=True, timeout=120,
        )
        partes = (r.stdout or "").split()
        if r.returncode == 0 and len(partes) == 2 and partes[0].isdigit() and partes[1].isdigit():
            registro = partes[0] + " " + partes[1]
        else:
            causa = ((r.stderr or "") or "no se pudo leer el registro").strip().splitlines()[-1:]
            registro = "error " + (causa[0] if causa else "no se pudo leer el registro")
    except Exception as exc:
        registro = "error " + type(exc).__name__ + ": " + str(exc)
else:
    registro = "0 0"

print("recibo", recibo)
print("vivo", vivo)
print(
    "pasada",
    str(pasada.get("sha") or "-"),
    str(pasada.get("hora") or "-"),
    "completa" if pasada.get("completa") else "parcial",
)
print("ruta", arbol_ruta or "-")
print("arbol", arbol)
print("registro", registro)
'

PROGRAMA_PC_B64="$(printf '%s' "$PROGRAMA" | _pc_b64)"

lee_estado() {
  local m=$1
  if [ "$m" = pc ]; then
    # La PC contesta cada línea con \r\n: el \r no puede formar parte de ningún
    # campo, o el mismo SHA deja de ser igual a sí mismo.
    py_pc_runtime_prog "$PROGRAMA_PC_B64" "$recibo_viejo" | tr -d '\r'
  else
    en_posix "$m" "$m" "$sha" <<REMOTO | tr -d '\r'
set -euo pipefail
python3 - "$recibo_viejo" <<'PY'
$PROGRAMA
PY
REMOTO
  fi
}

causa_de() { # causa_de <estado> -> texto vacío si el despliegue está comprobado
  local estado=$1 recibo vivo pasada_sha pasada_completa ruta arbol registro registro_total registro_fallan causa=""
  recibo="$(printf '%s\n' "$estado" | awk '/^recibo /{print $2; exit}')"
  vivo="$(printf '%s\n' "$estado" | awk '/^vivo /{print $2; exit}')"
  pasada_sha="$(printf '%s\n' "$estado" | awk '/^pasada /{print $2; exit}')"
  pasada_completa="$(printf '%s\n' "$estado" | awk '/^pasada /{print $4; exit}')"
  ruta="$(printf '%s\n' "$estado" | awk '/^ruta /{print $2; exit}')"
  arbol="$(printf '%s\n' "$estado" | awk '/^arbol /{print $2; exit}')"
  registro="$(printf '%s\n' "$estado" | awk '/^registro /{ $1=""; sub(/^ /,""); print; exit}')"
  registro_total="$(printf '%s\n' "$registro" | awk '{print $1}')"
  registro_fallan="$(printf '%s\n' "$registro" | awk '{print $2}')"
  [ "$recibo" = "$sha" ] || causa="$causa recibo en ${recibo:0:12} (se esperaba ${sha:0:12});"
  [ "$vivo" = "si" ] || causa="$causa supervisor no vivo;"
  [ "$pasada_sha" = "$sha" ] || causa="$causa sin pasada con ${sha:0:12};"
  [ "$pasada_completa" = "completa" ] || causa="$causa la última pasada no fue completa;"
  if [ "$reinicio" = 1 ] && { [ "$vivo" != "si" ] || [ "$pasada_sha" != "$sha" ]; }; then
    causa="$causa el reinicio falló;"
  fi
  [ -n "$ruta" ] && [ "$ruta" != "-" ] || causa="$causa la pasada no dice qué árbol ejecuta el supervisor;"
  [ "$arbol" = "$sha" ] || causa="$causa el árbol del supervisor está en ${arbol:0:12} (se esperaba ${sha:0:12});"
  case "$registro" in
    error*) causa="$causa no se pudo plegar el registro: ${registro#error };" ;;
    "")
      causa="$causa la comprobación no dice si el registro pliega;"
      ;;
    *)
      [ "$registro_fallan" = "0" ] \
        || causa="$causa el registro no pliega entero ($registro_total encargos, $registro_fallan fallan);"
      ;;
  esac
  printf '%s' "$causa"
}

fallos=0
for m in $maquinas; do
  if [ "$ensayo" = 1 ]; then
    echo "ensayo $m: recibo con ${sha:0:12}, supervisor vivo, pasada completa y registro entero"
    continue
  fi
  fin=$(( $(date +%s) + espera ))
  causa=" no responde;"
  while :; do
    if estado="$(lee_estado "$m")"; then
      causa="$(causa_de "$estado")"
      [ -z "$causa" ] && break
    fi
    [ "$(date +%s)" -ge "$fin" ] && break
    sleep 2
  done
  if [ -n "$causa" ]; then
    echo "FALLA $m:$causa" >&2
    fallos=$((fallos + 1))
  else
    echo "OK $m: recibo ${sha:0:12}, supervisor vivo, pasada completa y registro entero"
  fi
done

if [ "$fallos" -gt 0 ]; then
  echo "$fallos máquina(s) no comprobaron el despliegue" >&2
  exit 1
fi
echo "despliegue comprobado en:$maquinas"
