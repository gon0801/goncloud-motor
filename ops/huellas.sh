#!/usr/bin/env bash
# Huellas del motor en las tres máquinas: `motor version`, sha de la conf y `motor doctor --json`.
# Sale 0 solo si las tres huellas coinciden y conf y máquina están ok.
#   --servicio  además exige el servicio cargado (LaunchDaemon / tarea programada)
#   --rutas     además `doctor --como-servicio`: al menos un modelo por papel responde
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

servicio=0 rutas=0
for a in "$@"; do
  case "$a" in
    --servicio) servicio=1 ;;
    --rutas) rutas=1 ;;
    *) echo "uso: ops/huellas.sh [--servicio] [--rutas]" >&2; exit 2 ;;
  esac
done

d="$(mktemp -d)"
trap 'rm -rf "$d"' EXIT

posix='export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; if python3 -c "import tomllib" 2>/dev/null; then py=python3; else py="uv run --python 3.12 python"; fi; cd "$HOME/motor" && PYTHONPATH=. $py -m motor'
pc="cd /d %USERPROFILE%\\motor && $PC_UV run --python 3.12 python -m motor"

# toma <m> <nombre> <comando posix> <comando pc>: stdout en $d/<m>.<nombre>, código en $d/<m>.<nombre>.rc
toma() {
  local m=$1 n=$2
  corre "$m" "$3" "$4" >"$d/$m.$n" 2>"$d/$m.$n.err"
  echo $? >"$d/$m.$n.rc"
  tr -d '\r' <"$d/$m.$n" >"$d/$m.$n.tmp" && mv "$d/$m.$n.tmp" "$d/$m.$n"
}

huella() {
  local m=$1
  toma "$m" version "$posix version --json" "$pc version --json"
  toma "$m" doctor "$posix doctor --home \"\$HOME/.motor\" --json" "$pc doctor --home %USERPROFILE%\\.motor --json"
  if [ "$servicio" = 1 ]; then
    toma "$m" servicio "launchctl print system/$SERVICIO_MAC" "schtasks /query /tn $TAREA_PC"
  fi
  if [ "$rutas" = 1 ]; then
    toma "$m" rutas "$posix doctor --home \"\$HOME/.motor\" --como-servicio --json" "$pc doctor --home %USERPROFILE%\\.motor --como-servicio --json"
  fi
}

for m in $MAQUINAS; do huella "$m" & done
wait

python3 -I - "$d" "$MAQUINAS" "$servicio" "$rutas" <<'PY'
import json, sys
from pathlib import Path

d, maquinas, servicio, rutas = Path(sys.argv[1]), sys.argv[2].split(), sys.argv[3] == "1", sys.argv[4] == "1"


def lee(m, n):
    """(json | None, motivo): ssh caído, salida que no es JSON o código raro se dicen en llano."""
    rc = (d / f"{m}.{n}.rc").read_text(errors="replace").strip()
    out = (d / f"{m}.{n}").read_text(errors="replace")
    err = (d / f"{m}.{n}.err").read_text(errors="replace").strip().splitlines()
    try:
        return json.loads(out), ""
    except ValueError:
        pass
    return None, f"{n} no respondió (código {rc}): {err[-1] if err else out.strip()[-120:] or 'sin salida'}"


problemas, filas, huellas = [], [], {}
for m in maquinas:
    ver, p1 = lee(m, "version")
    doc, p2 = lee(m, "doctor")
    if ver is None or doc is None:
        problemas += [f"{m}: {p}" for p in (p1, p2) if p]
        filas.append(f"{m:<5} SIN HUELLA")
        continue
    sha, conf = ver.get("sha", ""), doc.get("conf_sha", "")
    huellas[m] = (ver.get("version", ""), sha, conf)
    estado = []
    if not doc.get("ok") or not conf:
        problemas.append(f"{m}: doctor no está ok: {'; '.join(doc.get('problemas') or ['sin detalle'])}")
        estado.append("doctor MAL")
    elif doc.get("maquina") != m:
        problemas.append(f"{m}: la conf dice que esta máquina es {doc.get('maquina')!r}")
        estado.append("maquina.toml MAL")
    else:
        estado.append("doctor ok")
    if servicio:
        rc = (d / f"{m}.servicio.rc").read_text(errors="replace").strip()
        if rc != "0":
            problemas.append(f"{m}: el servicio no está cargado")
        estado.append("servicio " + ("ok" if rc == "0" else "NO"))
    if rutas:
        r, p = lee(m, "rutas")
        responden = [k for k, v in (r or {}).get("rutas", {}).items() if v.get("responde")]
        if r is None or not r.get("ok") or not responden:
            problemas.append(f"{m}: rutas: {p or '; '.join(r.get('problemas') or ['ningún modelo responde'])}")
            estado.append("rutas MAL")
        else:
            estado.append(f"rutas ok ({len(responden)})")
    filas.append(f"{m:<5} motor {ver.get('version')} {sha[:12]}  conf {conf[:12]}  " + ", ".join(estado))

print("\n".join(filas))
if len(set(huellas.values())) > 1:
    problemas.append("las huellas no coinciden: " + "; ".join(f"{m}={v[1][:12]}/{v[2][:12]}" for m, v in huellas.items()))
if problemas:
    print("\nNO coinciden o no están ok:\n" + "\n".join(f"  - {p}" for p in problemas), file=sys.stderr)
    sys.exit(1)
print("\nLas tres huellas coinciden.")
PY
