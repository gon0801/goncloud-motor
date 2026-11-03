"""La huella de lo que un resultado midió: Verified la de las Acepta del bloque, Proved la de su
`prueba-real` y Deployed la de la receta, más la de la máquina (path_extra, env y tiene).

`machine.fold` ignora un resultado cuya huella ya no coincide con el plan y la máquina vigentes: así
reentregar el plan corregido, o cambiar la máquina, vuelve a correr el paso sobre el mismo SHA sin
lanzar un agente. Un resultado sin huella (anterior a este cambio) vale igual que antes.
"""

from __future__ import annotations

import hashlib
import json

from .conf import Maquina, RepoConf
from .plan import Block


def _h(obj: object) -> str:
    texto = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texto.encode()).hexdigest()[:16]


def de_maquina(mq: Maquina) -> str:
    return _h([list(mq.path_extra), sorted(map(list, mq.env)), sorted(mq.tiene)])


def receta_de(block: Block, repos: dict[str, dict]) -> tuple[str, str, str]:
    """(desplegar, checklist, rollback): la del bloque, o la del `.motor.toml` fijado de su repo."""
    if block.release:
        r = block.release
        return r.desplegar, r.checklist, r.rollback
    rc = RepoConf.parse(repos.get(block.repo, {}).get("texto", ""))
    return rc.desplegar, rc.checklist, rc.rollback


def insumo_de(campo: str, block: Block, repos: dict[str, dict]) -> str:
    """Huella del insumo que mide el resultado de `campo` (verified | proved | deployed)."""
    if campo == "verified":
        return _h([r.acepta for r in block.rows if r.acepta])
    if campo == "proved":
        return _h(block.prueba_real)
    return _h(list(receta_de(block, repos)))


def vigente(resultado: dict, insumo: str, maquina: str | None) -> bool:
    """¿Este resultado midió lo que el plan y la máquina piden hoy? Sin huella, sí."""
    if resultado.get("huella", insumo) != insumo:
        return False
    return maquina is None or resultado.get("huella_maquina", maquina) == maquina
