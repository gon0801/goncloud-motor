"""Registro falso de un agente que usa (o no) poteto-mode, con la forma del stream-json de Claude.

Lo usan fake_agent.py y fake_reviewer.py cuando su modo empieza con `pstack`:

  pstack_ok            carga poteto-mode en su primera acción
  pstack_no            hace 3 acciones y jamás la carga
  pstack_no_primero    la primera vez no la carga; las siguientes sí
  pstack_lento         acciones sin cargarla, una cada 0.3 s, para que la puerta lo corte en vivo
  pstack_playbook      carga poteto-mode y abre un playbook, con Read
  pstack_real:<intento>  repite el registro de tests/datos/pstack-terminal/<intento>, con su raíz cambiada
                       por la copia fija que dice el encargo
  pstack_real_pausa:<intento>  igual, y al entregar deja la cuenta en pausa (lo hace fake_agent.py)
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

DATOS = Path(__file__).resolve().parent.parent / "tests" / "datos" / "pstack-terminal"


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


def _raiz(encargo: str) -> str:
    return re.search(r"en la copia fija `([^`]+)`", encargo).group(1)


def emite(modo: str, primera: bool, encargo: str = "") -> None:
    """Imprime el registro que dejaría el agente según `modo` (ver arriba)."""
    print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
    if modo.startswith(("pstack_real:", "pstack_real_pausa:")):
        carpeta = DATOS / modo.split(":", 1)[1]
        antes = json.loads((carpeta / "meta.json").read_text("utf-8"))["raiz"]
        texto = (carpeta / "registro.jsonl").read_text("utf-8")
        print(texto.replace(antes, _raiz(encargo)), end="", flush=True)
        return
    if modo == "pstack_playbook":
        raiz = _raiz(encargo)
        _linea("Skill", {"skill": "pstack:poteto-mode"})
        _linea(
            "Read",
            {"file_path": f"{raiz}/pstack/skills/poteto-mode/playbooks/docs.md"},
        )
        return
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
