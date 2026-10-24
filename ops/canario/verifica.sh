#!/usr/bin/env bash
# Espera el final de la prueba de fuego (tope de 3 h) y sale 0 solo si, en CADA máquina del
# canario (Mini y PC menos la máquina en la que corre; nunca la Mac), el plan canario
# terminó; hay un PR y un merge por bloque de código (un Merged `ya_en_base`
# solo vale si la otra máquina sí hizo el PR y el merge de ese bloque) y su prueba real;
# el paso humano quedó hecho; el reinicio quedó anotado como corte (no como falla); y llegaron por
# el bot de esa marca el aviso de cada bloque, el de «retomé» y el de «terminé». La Mac no cuenta.
# Si una máquina está reiniciándose, el ssh falla y simplemente se vuelve a mirar.
#   MOTOR_CANARIO_PLAN       el plan (por omisión, plan.md de esta carpeta)
#   MOTOR_CANARIO_TOPE_S     cuánto espera como máximo (10800 = 3 h)
#   MOTOR_CANARIO_SONDEO_S   cada cuánto mira (60)
set -uo pipefail
export LC_ALL=C
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/../lib.sh"

PLAN="${MOTOR_CANARIO_PLAN:-$aqui/plan.md}"
TOPE_S="${MOTOR_CANARIO_TOPE_S:-10800}"
SONDEO_S="${MOTOR_CANARIO_SONDEO_S:-60}"
if ! CANARIO_MAQUINAS="$(maquinas_canario)"; then
  echo "me niego: no pude identificar las máquinas del canario" >&2
  exit 2
fi
if [ -z "$(printf '%s' "$CANARIO_MAQUINAS" | tr -d '[:space:]')" ]; then
  echo "me niego: no queda ninguna máquina que reiniciar" >&2
  exit 2
fi

d="$(mktemp -d)"
trap 'rm -rf "$d"' EXIT

nombres_canario=""
for m in $CANARIO_MAQUINAS; do
  case "$m" in mini) _n=claw ;; pc) _n=Hermi ;; *) _n=$m ;; esac
  nombres_canario="${nombres_canario:+$nombres_canario y }$_n"
done

# Los bloques del plan: ids de código y ids de paso humano (del mismo analizador que usa el motor).
listas="$(PYTHONPATH="$aqui/../.." python3 -I - "$PLAN" <<'PY'
import sys
sys.path.insert(0, __import__("os").environ["PYTHONPATH"])
from motor.plan import parse_plan

plan = parse_plan(open(sys.argv[1], encoding="utf-8").read())
print(plan.id)
print(",".join(b.id for b in plan.blocks if b.human is None))
print(",".join(b.id for b in plan.blocks if b.human is not None))
PY
)" || { echo "no pude leer el plan: $PLAN" >&2; exit 2; }
PLAN_ID="$(sed -n 1p <<<"$listas")" CODIGO="$(sed -n 2p <<<"$listas")" HUMANO="$(sed -n 3p <<<"$listas")"

py='if python3 -c "import sqlite3" 2>/dev/null; then py=python3; else py="uv run --python 3.12 python"; fi; $py -'
pc_py="$PC_UV run --python 3.12 python -"

# toma <m>: el resumen de esa máquina en $d/<m>.json (vacío si no contesta).
toma() {
  local m=$1
  if [ "$m" = pc ]; then
    ssh_a "$(host_de pc)" "$pc_py %USERPROFILE%\\.motor $PLAN_ID \"$CODIGO\" \"$HUMANO\"" <"$aqui/resumen.py" 2>"$d/$m.err" | tr -d '\r' >"$d/$m.json"
  else
    ssh_a "$(host_de "$m")" "$(printf '%q ' bash -c "$py \"\$HOME/.motor\" $PLAN_ID \"$CODIGO\" \"$HUMANO\"")" <"$aqui/resumen.py" 2>"$d/$m.err" >"$d/$m.json"
  fi
}

# juzga: lee los resúmenes, imprime la tabla y sale 0 (todo bien), 1 (falta algo) o 3 (falla definitiva).
juzga() {
  python3 -I - "$d" "$CANARIO_MAQUINAS" "$CODIGO" <<'PY'
import json
import sys
from pathlib import Path

d, maquinas = Path(sys.argv[1]), sys.argv[2].split()
codigo = [b for b in sys.argv[3].split(",") if b]
NOMBRE = {"mini": "claw (Mini)", "pc": "Hermi (PC)"}
faltan, definitivas, notas = [], [], []
resumenes = {}
for m in maquinas:
    txt = (d / f"{m}.json").read_text(errors="replace").strip()
    try:
        r = json.loads(txt.splitlines()[-1]) if txt else None
    except ValueError:
        r = None
    if r is None:
        err = (d / f"{m}.err").read_text(errors="replace").strip().splitlines()
        faltan.append(f"{NOMBRE[m]}: no contestó ({err[-1] if err else 'sin salida'})")
        continue
    resumenes[m] = r

# Un Merged ya_en_base (sin PR) solo vale si la otra máquina sí hizo el PR y el merge del bloque.
pr_real = {
    b: any(
        r["bloques"].get(b, {}).get("prs", 0) >= 1 and r["bloques"].get(b, {}).get("merges", 0) >= 1
        for r in resumenes.values()
    )
    for b in codigo
}
for m, r in resumenes.items():
    n = NOMBRE[m]
    if not r["plan_existe"]:
        faltan.append(f"{n}: el motor no tiene el plan")
        continue
    if not r["terminado"]:
        faltan.append(f"{n}: el plan no ha terminado")
    for b, v in r["bloques"].items():
        if "humano_hecho" in v:
            if not v["humano_hecho"]:
                faltan.append(f"{n}: el paso humano {b} no se detectó")
            continue
        if v["prs"] < 1:
            if v["ya_en_base"] and pr_real.get(b):
                notas.append(f"{n}: el bloque {b} ya estaba en la rama principal (su PR y su merge los hizo la otra máquina)")
            elif v["ya_en_base"]:
                faltan.append(f"{b} no tiene PR en ninguna máquina (lo entregado ya estaba en base: resetea el repo canario)")
            else:
                faltan.append(f"{n}: el bloque {b} no tiene PR")
        if v["merges"] < 1:
            faltan.append(f"{n}: el bloque {b} no tiene merge")
        if not v["probado"]:
            faltan.append(f"{n}: el bloque {b} no pasó su prueba real")
    if r["cortes_como_falla"]:
        definitivas.append(f"{n}: un corte se trató como falla (agente castigado por un reinicio)")
    if r["cortes"] < 1:
        faltan.append(f"{n}: no hay ningún corte anotado (¿se reinició a media corrida?)")
    for aviso, llego in r["avisos"].items():
        if not llego:
            faltan.append(f"{n}: no llegó el aviso «{aviso}» por su bot")
    if r["avisos_pendientes"]:
        faltan.append(f"{n}: {r['avisos_pendientes']} aviso(s) siguen sin enviarse")
for linea in dict.fromkeys(definitivas + faltan):
    print(f"  - {linea}")
if definitivas or faltan:
    sys.exit(3 if definitivas else 1)
for linea in dict.fromkeys(notas):
    print(f"  - {linea}")
sys.exit(0)
PY
}

fin=$((SECONDS + TOPE_S))
while :; do
  for m in $CANARIO_MAQUINAS; do toma "$m" & done
  wait
  falta="$(juzga)"
  rc=$?
  case $rc in
    0)
      if [ -n "$falta" ]; then
        echo "Prueba de fuego aprobada: $nombres_canario terminaron el plan, con el corte anotado y los avisos recibidos."
        echo "$falta"
      else
        echo "Prueba de fuego aprobada: $nombres_canario terminaron el plan, con un PR y un merge por bloque, el corte anotado y los avisos recibidos."
      fi
      exit 0 ;;
    3) echo "La prueba de fuego FALLÓ de forma definitiva:" >&2; echo "$falta" >&2; exit 1 ;;
  esac
  if [ $SECONDS -ge $fin ]; then
    echo "La prueba de fuego no terminó en ${TOPE_S}s. Falta:" >&2; echo "$falta" >&2; exit 1
  fi
  echo "[$((SECONDS))s] todavía falta:"; echo "$falta"
  sleep "$SONDEO_S"
done
