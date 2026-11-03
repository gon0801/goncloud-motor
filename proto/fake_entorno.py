"""Lo que un agente falso anota de su entorno para las pruebas del vigilante instalado.

Lo usan fake_agent.py y fake_reviewer.py con el modo `entorno`: deja en `FAKE_STATE/entorno-<modelo>.json`
si heredó `MOTOR_LLAMADA`, qué entradas tenía el libro de cuentas mientras corría y qué contestó una
llamada a `kimi` por el shim del motor.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def anota() -> None:
    cupos = json.loads(
        (Path(os.environ["MOTOR_ATTEMPT_DIR"]) / "cupos.json").read_text(
            encoding="utf-8"
        )
    )
    libro = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted((Path(cupos["home"]) / "llamadas").glob("*.json"))
    ]
    r = llama_a_kimi()
    destino = (
        Path(os.environ["FAKE_STATE"]) / f"entorno-{os.environ['MOTOR_MODEL']}.json"
    )
    destino.write_text(
        json.dumps(
            {
                "MOTOR_LLAMADA": os.environ.get("MOTOR_LLAMADA"),
                "libro": libro,
                "rc": r.returncode,
                "out": r.stdout,
            }
        ),
        encoding="utf-8",
    )


def llama_a_kimi() -> subprocess.CompletedProcess:
    return subprocess.run(
        [shutil.which("kimi") or "kimi", "--model", "k3", "-p", "hola"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
