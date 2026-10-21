"""Resumen del plan canario en UNA máquina, para ops/canario/verifica.sh. Solo biblioteca estándar y
solo lectura: verifica.sh lo manda por stdin a la Mini y a la PC (`python - <home> <plan> <códigos> <humanos>`).

Imprime un JSON con lo que se exige: el plan terminó, un PR y un merge por bloque de código, el
paso humano hecho, los cortes (reinicios) anotados como corte y no como falla, y los avisos que
el bot de la marca ya envió (los que siguen en `pendientes` todavía no llegaron).
"""

import json
import re
import sqlite3
import sys
from pathlib import Path

home, plan = Path(sys.argv[1]), sys.argv[2]
codigo = [b for b in sys.argv[3].split(",") if b]
humano = [b for b in sys.argv[4].split(",") if b]

eventos = []
db = home / "motor.db"
if db.exists():
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for bloque, tipo, data, ukey in con.execute(
            "SELECT block, kind, data, ukey FROM events WHERE plan = ? ORDER BY id",
            (plan,),
        ):
            eventos.append((bloque, tipo, json.loads(data), ukey or ""))
    finally:
        con.close()


def de(bloque, tipo):
    return [d for b, t, d, _ in eventos if b == bloque and t == tipo]


bloques = {}
for b in codigo:
    probados = [d for d in de(b, "Proved") if d.get("ok")]
    bloques[b] = {
        "prs": len(de(b, "PrOpened")),
        "merges": len(de(b, "Merged")),
        # Merged sin PR: lo entregado ya estaba en la rama principal (otro plan lo mergeó antes).
        "ya_en_base": sum(1 for d in de(b, "Merged") if d.get("ya_en_base")),
        "probado": bool(probados),
    }
for b in humano:
    bloques[b] = {"humano_hecho": bool(de(b, "HumanDone"))}

# Intentos que terminaron en corte; un corte tratado como falla deja un Cooldown de su carpeta.
carpeta = {}
for _, t, d, _ in eventos:
    if t == "AttemptStarted":
        carpeta[(d.get("n"), d.get("role"))] = d.get("dir", "")
cortes, cortes_como_falla = 0, 0
cooldowns = {u for _, t, _, u in eventos if t == "Cooldown"}
for _, t, d, _ in eventos:
    if t == "AttemptEnded" and d.get("outcome", {}).get("kind") == "corte":
        cortes += 1
        if f"cooldown:{carpeta.get((d.get('n'), d.get('role')), '?')}" in cooldowns:
            cortes_como_falla += 1


def nombres(sub):
    p = home / "avisos" / sub
    return sorted(f.name for f in p.glob("*.txt")) if p.is_dir() else []


enviados, pendientes = nombres("enviados"), nombres("pendientes")


def llego(fragmento):
    # Solo los avisos de ESTE plan: el Notifier nombra cada archivo '<ms>-<plan>_<clave>.txt'.
    return any(re.search(rf"^\d+-{re.escape(plan)}_{fragmento}", n) for n in enviados)


avisos = {f"entregado_{b}": llego(rf"entregado_{re.escape(b)}\.txt$") for b in codigo}
avisos["terminado"] = llego(rf"terminado_{re.escape(plan)}\.txt$")
avisos["retome"] = llego(r"retome_")

print(
    json.dumps(
        {
            "plan_existe": bool(eventos),
            "terminado": any(
                t == "NoticeQueued" and d.get("key") == f"terminado:{plan}"
                for _, t, d, _ in eventos
            ),
            "bloques": bloques,
            "cortes": cortes,
            "cortes_como_falla": cortes_como_falla,
            "avisos": avisos,
            "avisos_pendientes": len(pendientes),
        }
    )
)
