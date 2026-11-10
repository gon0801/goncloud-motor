"""La configuración de máquina del motor de encargos: `maquina.toml` y `secretos.env` de su base."""

from __future__ import annotations

import tomllib
from pathlib import Path

from motor.conf import ConfError, Maquina, load_maquina


def de_base(base: Path) -> Maquina:
    base = Path(base)
    if not (base / "maquina.toml").is_file():
        raise ValueError(
            f"falta maquina.toml en la base {base}: "
            "corre `python -m motor.encargos.retiro migrar` en esta máquina"
        )
    try:
        return load_maquina(base)
    except (ConfError, OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"maquina.toml o secretos.env ilegibles en la base {base}: {exc}") from exc
