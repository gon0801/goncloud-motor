#!/usr/bin/env bash
# Comprueba, por máquina, que un despliegue quedó de verdad en el SHA nuevo.
# Reutiliza el recibo de ops/instala-todo.sh y la pasada del supervisor; no
# despliega ni revierte nada. Es idempotente: solo lee y reintenta la lectura.
#
# --sha SHA     SHA que debe estar instalado (por omisión, HEAD del repo).
# --maquina M   Una sola máquina: mac, mini o pc.
# --espera S    Segundos acotados a esperar una pasada nueva con ese SHA.
# --reinicio    El despliegue ya ordenó el reinicio: sin pasada nueva se dice
#               "el reinicio falló" por máquina.
# --ensayo      No toca ssh: imprime lo que comprobaría y sale 0.
set -uo pipefail
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

sha=""
espera=0
ensayo=0
reinicio=0
maquinas="$MAQUINAS"
while [ $# -gt 0 ]; do
  case "$1" in
    --sha) sha="${2:-}"; shift 2 ;;
    --maquina) maquinas="${2:-}"; shift 2 ;;
    --espera) espera="${2:-}"; shift 2 ;;
    --reinicio) reinicio=1; shift ;;
    --ensayo) ensayo=1; shift ;;
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

PROGRAMA='import json,os,pathlib,subprocess
home = pathlib.Path.home()

def leer(ruta):
    try:
        return json.loads(pathlib.Path(ruta).read_text(encoding="utf-8"))
    except Exception:
        return {}

recibo = "-"
for ruta in (home / ".motor-encargos" / "instalacion.json", home / ".motor" / "instalacion.json"):
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
'

PROGRAMA_PC_B64="$(printf '%s' "$PROGRAMA" | _pc_b64)"

lee_estado() {
  local m=$1
  if [ "$m" = pc ]; then
    py_pc_prog "$PROGRAMA_PC_B64"
  else
    en_posix "$m" "$m" "$sha" <<REMOTO
set -euo pipefail
python3 - <<'PY'
$PROGRAMA
PY
REMOTO
  fi
}

causa_de() { # causa_de <estado> -> texto vacío si el despliegue está comprobado
  local estado=$1 recibo vivo pasada_sha pasada_completa ruta arbol causa=""
  recibo="$(printf '%s\n' "$estado" | awk '/^recibo /{print $2; exit}')"
  vivo="$(printf '%s\n' "$estado" | awk '/^vivo /{print $2; exit}')"
  pasada_sha="$(printf '%s\n' "$estado" | awk '/^pasada /{print $2; exit}')"
  pasada_completa="$(printf '%s\n' "$estado" | awk '/^pasada /{print $4; exit}')"
  ruta="$(printf '%s\n' "$estado" | awk '/^ruta /{print $2; exit}')"
  arbol="$(printf '%s\n' "$estado" | awk '/^arbol /{print $2; exit}')"
  [ "$recibo" = "$sha" ] || causa="$causa recibo en ${recibo:0:12} (se esperaba ${sha:0:12});"
  [ "$vivo" = "si" ] || causa="$causa supervisor no vivo;"
  [ "$pasada_sha" = "$sha" ] || causa="$causa sin pasada con ${sha:0:12};"
  [ "$pasada_completa" = "completa" ] || causa="$causa la última pasada no fue completa;"
  if [ "$reinicio" = 1 ] && { [ "$vivo" != "si" ] || [ "$pasada_sha" != "$sha" ]; }; then
    causa="$causa el reinicio falló;"
  fi
  [ -n "$ruta" ] && [ "$ruta" != "-" ] || causa="$causa la pasada no dice qué árbol ejecuta el supervisor;"
  [ "$arbol" = "$sha" ] || causa="$causa el árbol del supervisor está en ${arbol:0:12} (se esperaba ${sha:0:12});"
  printf '%s' "$causa"
}

fallos=0
for m in $maquinas; do
  if [ "$ensayo" = 1 ]; then
    echo "ensayo $m: recibo con ${sha:0:12}, supervisor vivo y pasada completa con ese SHA"
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
    echo "OK $m: recibo ${sha:0:12}, supervisor vivo y pasada completa"
  fi
done

if [ "$fallos" -gt 0 ]; then
  echo "$fallos máquina(s) no comprobaron el despliegue" >&2
  exit 1
fi
echo "despliegue comprobado en:$maquinas"
