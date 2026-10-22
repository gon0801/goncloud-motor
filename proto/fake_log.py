"""Registro falso de un agente que usa (o no) poteto-mode, con la forma del stream-json de Claude.

Lo usan fake_agent.py y fake_reviewer.py cuando su modo empieza con `pstack`:

  pstack_ok            carga poteto-mode en su primera acción
  pstack_no            hace 3 acciones y jamás la carga
  pstack_no_primero    la primera vez no la carga; las siguientes sí
  pstack_lento         acciones sin cargarla, una cada 0.3 s, para que la puerta lo corte en vivo
"""

from __future__ import annotations

import json
import time


def _linea(nombre: str, args: dict) -> None:
    print(
        json.dumps(
            {
                "type": "assistant",
                "parent_tool_use_id": None,
                "message": {
                    "content": [{"type": "tool_use", "name": nombre, "input": args}]
                },
            }
        ),
        flush=True,
    )


def emite(modo: str, primera: bool) -> None:
    """Imprime el registro que dejaría el agente según `modo` (ver arriba)."""
    print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
    carga = modo == "pstack_ok" or (modo == "pstack_no_primero" and not primera)
    if carga:
        _linea("Skill", {"skill": "pstack:poteto-mode"})
    if modo == "pstack_lento":
        for i in range(60):
            _linea("Bash", {"command": f"echo paso {i}"})
            time.sleep(0.3)
        return
    for i in range(3):
        _linea("Bash", {"command": f"echo paso {i}"})
