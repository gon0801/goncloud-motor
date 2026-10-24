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

# El sha que debe correr el daemon: el del despliegue; fuera de uno, el HEAD de esta copia.
sha_esperado="${MOTOR_SHA:-}"
[ -n "$sha_esperado" ] || sha_esperado="$(git -C "$aqui/.." rev-parse HEAD 2>/dev/null)" || sha_esperado=""
if [ -z "$sha_esperado" ]; then
  echo 'falta MOTOR_SHA: corre MOTOR_SHA=$(git rev-parse HEAD) bash ops/huellas.sh' >&2
  exit 2
fi
tope_daemon="${MOTOR_TOPE_DAEMON:-180}"

# La etiqueta del despliegue tiene que apuntar a ese commit: instala-todo.sh respeta una que ya existía.
problema_etiqueta=""
if ! sha_etiqueta="$(sha_publicado "$aqui/..")"; then
  problema_etiqueta="no pude consultar la etiqueta $ETIQUETA en origin"
elif [ -z "$sha_etiqueta" ]; then
  problema_etiqueta="la etiqueta $ETIQUETA no existe en origin"
elif [ "$sha_etiqueta" != "$sha_esperado" ]; then
  problema_etiqueta="la etiqueta $ETIQUETA apunta a ${sha_etiqueta:0:12} y el despliegue es ${sha_esperado:0:12}"
fi

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
  toma "$m" programadas "$posix programadas --home \"\$HOME/.motor\" --json" "$pc programadas --home %USERPROFILE%\\.motor --json"
  espera_daemon_en_sha "$m" "$sha_esperado" "$tope_daemon"
  echo $? >"$d/$m.daemon.rc"
  if [ "$servicio" = 1 ]; then
    toma "$m" servicio "launchctl print system/$SERVICIO_MAC" "schtasks /query /tn $TAREA_PC"
  fi
  if [ "$rutas" = 1 ]; then
    toma "$m" rutas "$posix doctor --home \"\$HOME/.motor\" --como-servicio --json" "$pc doctor --home %USERPROFILE%\\.motor --como-servicio --json"
  fi
}

for m in $MAQUINAS; do huella "$m" & done
wait

python3 -I - "$d" "$MAQUINAS" "$servicio" "$rutas" "$sha_esperado" "$tope_daemon" "$problema_etiqueta" <<'PY'
import json, sys
from pathlib import Path

d, maquinas, servicio, rutas = Path(sys.argv[1]), sys.argv[2].split(), sys.argv[3] == "1", sys.argv[4] == "1"
sha_esperado, tope, problema_etiqueta = sys.argv[5], sys.argv[6], sys.argv[7]


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
if problema_etiqueta:
    problemas.append(problema_etiqueta)
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
    if (d / f"{m}.daemon.rc").read_text().strip() != "0":
        problemas.append(f"{m}: el motor daemon no arrancó con el código de {sha_esperado[:12]} en {tope} s")
        estado.append("daemon NO")
    else:
        estado.append("daemon ok")
    prog, p = lee(m, "programadas")
    if prog is None or not prog.get("leidas"):
        problemas.append(f"{m}: programadas: {p or 'no leyó ninguna tarea programada'}")
        estado.append("programadas MAL")
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
