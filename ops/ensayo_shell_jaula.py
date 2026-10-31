"""Corre python, el bash y el sh de Git y un git push local DENTRO de la jaula de Windows.

`aislar` solo describe la jaula; quien la crea es `jaula_windows.crear`. Un `subprocess.run`
sobre `comando.argv` corre sin jaula. Este ensayo usa `lanzar_en_jaula`, el mismo camino de la
sonda, y se lanza igual que el supervisor (tarea programada), no por SSH.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.encargos.aislamiento import (
    _herramientas_git,
    aislar,
    lanzar_en_jaula,
    sondear,
)

PUSH = (
    "rm -rf a b.git && git init -q a && git init -q --bare b.git && cd a && "
    "git -c user.email=x@x -c user.name=x commit -q --allow-empty -m x && "
    "git push -q ../b.git HEAD:main && echo ok"
)


def main() -> int:
    fallos = 0
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        ident = "enc_ensayo" + uuid.uuid4().hex[:8]
        espacio = base / "espacio" / ident
        runtime = base / "runtime" / ident
        espacio.mkdir(parents=True)
        runtime.mkdir(parents=True)
        variantes = [
            ("sonda", {}),
            ("agente claude", {"suscripcion": True, "agente": "claude"}),
            ("revisor claude", {"suscripcion": True, "agente": "claude", "lectura": True}),
        ]
        for nombre, opciones in variantes:
            herramientas = _herramientas_git(dict(os.environ))
            for caso in ("python", "bash de Git", "sh de Git", "git push a un bare local"):
                try:
                    cmd = {
                        "python": [sys.executable, "-c", "print('ok')"],
                        "bash de Git": [herramientas["MOTOR_SONDA_SHELL"], "-c", "echo ok"],
                        "sh de Git": [herramientas["MOTOR_SONDA_SH"], "-c", "echo ok"],
                        "git push a un bare local": [
                            herramientas["MOTOR_SONDA_SHELL"],
                            "-c",
                            PUSH,
                        ],
                    }[caso]
                    comando = aislar(cmd, espacio, runtime, **opciones)
                    # El revisor no escribe en el espacio: el push va al temporal del runtime.
                    cwd = Path(comando.entorno["TMPDIR"]) if "push" in caso else espacio
                    rc, salida = lanzar_en_jaula(
                        comando, cwd, dict(comando.entorno), plazo=60
                    )
                    veredicto = "ok" if rc == 0 and salida.split()[-1:] == ["ok"] else "FALLA"
                    detalle = f"rc={rc} salida={' '.join(salida.split())[:300]!r}"
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    veredicto, detalle = "FALLA", f"{type(exc).__name__}: {exc}"
                fallos += veredicto != "ok"
                print(f"- {nombre} / {caso}: {veredicto} {detalle}")
        resultado = sondear(espacio, runtime / "sonda", agente="claude")
        fallos += not resultado.ok
        print(f"- sondear(claude): ok={resultado.ok} causa={resultado.causa!r}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
