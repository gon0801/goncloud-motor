"""Supervisor falso para las pruebas del lanzador del canario.

Barre la base como el banco de `tests/test_compuerta_e2e.py`: el lanzador de
producción con la ruta "ensayo", un reloj lógico que salta cada espera vencida
y el GitHub falso de `github.json` con DeepSeek que aprueba. Deja la marca de
pasada del supervisor real. Al arrancar deja un hijo en su grupo, como los
`git` y `gh` del supervisor real, y escribe su pid en `--hijo`.

    python tests/canario_supervisor_falso.py --base B --hijo H [--quieto | --muere-tras N]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.encargos.ciclo import barrer  # noqa: E402
from motor.encargos.cli import _marcar_pasada, lanzar_de_ruta, marcar_ruta  # noqa: E402
from motor.encargos.registro import Registro  # noqa: E402
from tests.test_compuerta_e2e import _DeepSeekAprueba, _iso  # noqa: E402

RUTA = ("ensayo",)


def _siguiente(reg: Registro, hora: datetime) -> datetime:
    esperas = [
        datetime.fromisoformat(e.reanuda_en.replace("Z", "+00:00")).replace(tzinfo=None)
        for e in (reg.fold(i) for i in reg.ids())
        if e is not None and e.estado == "esperando_reintento" and e.reanuda_en
    ]
    vencida = min((x for x in esperas if x > hora), default=None)
    return vencida + timedelta(seconds=1) if vencida else hora + timedelta(seconds=61)


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--base", type=Path, required=True)
    a.add_argument("--hijo", type=Path, required=True)
    a.add_argument("--quieto", action="store_true")
    a.add_argument("--muere-tras", type=int, default=0)
    args = a.parse_args()
    hijo = subprocess.Popen(["sleep", "600"])
    args.hijo.write_text(str(hijo.pid), encoding="utf-8")
    if args.quieto:
        time.sleep(600)
        return 0
    reg = Registro(args.base)
    falso = json.loads((args.base / "github.json").read_text(encoding="utf-8"))["falso"]
    gh = _DeepSeekAprueba(Path(falso))
    hora = datetime.now(timezone.utc).replace(tzinfo=None)
    pasadas = 0
    while not args.muere_tras or pasadas < args.muere_tras:
        hora = _siguiente(reg, hora)
        marcar_ruta(reg, RUTA, _iso(hora))
        informes = barrer(
            reg, _iso(hora), hora.replace(tzinfo=timezone.utc).timestamp(),
            lanzar_de_ruta(reg, RUTA), umbral_s=30.0, gh=gh,
        )
        _marcar_pasada(args.base, _iso(hora), informes)
        pasadas += 1
        time.sleep(0.12)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
