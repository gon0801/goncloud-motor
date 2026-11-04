"""`claude`, `kimi`, `opencode`, `grok`, `muse` y `qwen` para los agentes del motor.

    llamada_guard.py <cli> [args del CLI...]

Deja pasar la llamada al CLI real, pero contada (ver motor/llamadas.py): ocupa un lugar en los topes de
la cuenta y de la máquina, respeta las pausas de cuota, queda registrada en la carpeta del intento y no
se puede hacer desde dentro de otra llamada. Fuera de un encargo (sin MOTOR_ATTEMPT_DIR) no cuenta nada.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(AQUI))
)  # la raíz del repo: `import motor`

from motor import llamadas  # noqa: E402


def cli_real(cli: str) -> str | None:
    path = os.pathsep.join(
        p
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if os.path.abspath(p or ".") != AQUI
    )
    return shutil.which(cli, path=path)


def main() -> int:
    cli, args = sys.argv[1], sys.argv[2:]
    if os.environ.get("MOTOR_LLAMADA"):
        print(llamadas.ANIDADA, file=sys.stderr)
        return 3
    real = cli_real(cli)
    if not real:
        print(f"{cli} no está instalado", file=sys.stderr)
        return 127
    intento = os.environ.get("MOTOR_ATTEMPT_DIR", "")
    cupos_f = Path(intento) / "cupos.json"
    entorno = {**os.environ, "MOTOR_LLAMADA": "1"}
    entorno.pop("MOTOR_RESULT", None)  # solo el agente principal escribe la entrega
    if not (intento and cupos_f.is_file()):
        return subprocess.call(
            [real, *args], env=entorno
        )  # fuera de un encargo: pasa sin contar
    cupos = json.loads(cupos_f.read_text(encoding="utf-8"))
    home = Path(cupos["home"])
    modelo, cuenta = llamadas.elige_ruta(cli, args, cupos["rutas"])
    clave = f"llamada-{uuid.uuid4().hex[:12]}"
    inicio = time.time()
    with llamadas.cerrojo(home):
        motivo = llamadas.rechazo(home, cupos, cuenta)
        if not motivo:
            llamadas.registra(
                home,
                clave,
                {
                    "tipo": "llamada",
                    "cuenta": cuenta,
                    "pid": os.getpid(),
                    "inicio": inicio,
                },
            )
    if motivo:
        print(motivo, file=sys.stderr)
        return 3
    reg = Path(intento) / "llamadas"
    reg.mkdir(exist_ok=True)
    rec = {
        "modelo": modelo,
        "cuenta": cuenta,
        "cli": cli,
        "inicio": inicio,
        "pid": os.getpid(),
        "fin": None,
    }
    (reg / f"{clave}.json").write_text(json.dumps(rec), encoding="utf-8")
    try:
        salida = subprocess.call([real, *args], env=entorno)
    finally:
        llamadas.quita(home, clave)
        rec["fin"] = time.time()
        (reg / f"{clave}.json").write_text(json.dumps(rec), encoding="utf-8")
    return salida


if __name__ == "__main__":
    sys.exit(main())
