"""Registro falso de un agente que lanza (o no) subagentes, con la forma del registro de su CLI.

Lo usan fake_agent.py y fake_reviewer.py cuando su modo empieza con `pstack`:

  pstack_ok              lanza 2 subagentes
  pstack_uno             lanza 1
  pstack_no              no lanza ninguno
  pstack_falla_primero   la primera vez no lanza ninguno; las siguientes lanza 2

El formato sale del CLI del modelo del intento (`cupos.json` en MOTOR_ATTEMPT_DIR): kimi emite
`tool_calls`, el resto el `tool_use` de claude.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SUBAGENTES = {
    "pstack_ok": 2,
    "pstack_uno": 1,
    "pstack_no": 0,
}


def _cli() -> str:
    try:
        cupos = json.loads(
            (Path(os.environ["MOTOR_ATTEMPT_DIR"]) / "cupos.json").read_text(
                encoding="utf-8"
            )
        )
        return cupos["rutas"][os.environ["MOTOR_MODEL"]]["cli"]
    except (OSError, ValueError, KeyError):
        return "claude"


def _linea(cli: str, nombre: str, args: dict) -> None:
    if cli == "kimi":
        o = {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": nombre, "arguments": json.dumps(args)}}
            ],
        }
    else:
        o = {
            "type": "assistant",
            "parent_tool_use_id": None,
            "message": {
                "content": [{"type": "tool_use", "name": nombre, "input": args}]
            },
        }
    print(json.dumps(o), flush=True)


def emite(modo: str, primera: bool) -> None:
    """Imprime el registro que dejaría el agente según `modo` (ver arriba)."""
    cli = _cli()
    if cli == "kimi":
        print(json.dumps({"role": "system"}), flush=True)
    else:
        print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
    n = SUBAGENTES.get(modo, 0)
    if modo == "pstack_falla_primero":
        n = 0 if primera else 2
    for i in range(n):
        _linea(cli, "Agent", {"description": f"subagente {i}"})
    for i in range(3):
        _linea(cli, "Bash", {"command": f"echo paso {i}"})
