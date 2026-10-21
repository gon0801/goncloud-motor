"""Sketch c3: pstack obligatorio. Tipos y firmas; cuerpos NotImplementedError.

Pieza nueva: motor/rastro.py (puro). Cambios chicos: conf.py (PstackConf), effects.py (brief, collect,
start_attempt), machine.py (NO_ES_FALLA no cambia; solo el kind nuevo cuenta como falla), relevo.py (DE_KIND).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


# ------------------------------------------------------------------ conf (goncloud-motor-conf/pstack.toml)
class Carril(enum.Enum):
    FAST = "fast"      # solo docs/chore/cierre
    CODIGO = "codigo"


@dataclass(frozen=True)
class Exigencia:
    skill: bool = True
    min_subagentes: int = 0     # arranca en 0 en todo; sube a 1 en codigo/implementar tras medir


@dataclass(frozen=True)
class PstackConf:
    sha: str                                     # commit fijado de cursor/plugins
    exigir: bool                                 # false mientras no estén instaladas las tres máquinas
    exigencia: dict[tuple[Carril, str], Exigencia]  # (carril, "implementar"|"revisar")

    def para(self, carril: Carril, rol: str) -> Exigencia: raise NotImplementedError


# maquina.toml: pstack_dir = "/Users/dn/.agents"  (Mini y PC lo ponen en el suyo; WSL en el de WSL)


# ------------------------------------------------------------------ rastro.py (puro)
class FuenteRastro(enum.Enum):
    CLAUDE_TRANSCRIPT = "claude"   # ~/.claude/projects/<cwd>/<--session-id>.jsonl
    STREAM_LOG = "stream-log"      # kimi, opencode --format json: <intento>/log.txt
    GROK_UPDATES = "grok"          # ~/.grok/sessions/<cwd>/<sessionId>/updates.jsonl
    MUSE_SESSION = "muse"          # session.jsonl por --session-id
    QWEN_JSON = "qwen"             # --json-file {rastro}


@dataclass(frozen=True)
class Evidencia:
    """Lo que el rastro prueba. `reconocido=False` = no había ni un evento entendible: no verificable."""
    reconocido: bool
    cargo_skill: bool = False       # Skill/skill/read_skill 'poteto-mode' O lectura de .../poteto-mode/SKILL.md
    subagentes: int = 0
    comandos: int = 0
    fuente: str = ""


def leer(fuente: FuenteRastro, texto: str, sesion: str) -> Evidencia:
    """Un lector por fuente, solo mira la `sesion` del intento (el log.txt es append: hay varias)."""
    raise NotImplementedError


def veredicto(ev: Evidencia, exige: Exigencia) -> str | None:
    """None = cumple o no verificable; texto = por qué se rechaza (va al feedback del siguiente brief)."""
    raise NotImplementedError


def ruta_rastro(fuente: FuenteRastro, home: Path, cwd: str, sesion: str) -> Path:
    raise NotImplementedError


# ------------------------------------------------------------------ cambios en effects.py / machine.py
def bloque_metodo(pstack_dir: Path) -> str:
    """Texto 'Método obligatorio' con la ruta absoluta del SKILL.md; va al inicio de ambos briefs."""
    raise NotImplementedError


def pstack_listo(pstack_dir: Path, conf: PstackConf) -> bool:
    """ORIGEN de la instalación == conf.sha. start_attempt no lanza si es False y exigir=True."""
    raise NotImplementedError


def carril_de(files_changed: tuple[str, ...]) -> Carril:
    """Solo documentos => FAST; la misma regla que ya decide el carril del PR."""
    raise NotImplementedError


# En Collect (effects.py) antes de leer result.json, tras classify == 'terminado':
#   ev = leer(fuente, texto_del_rastro, att.sesion)
#   if (motivo := veredicto(ev, conf.pstack.para(carril, att.role))) and conf.pstack.exigir:
#       return {"kind": "sin_pstack", "detalle": motivo}
# Kind 'sin_pstack': deja ARREGLAR, cuenta como falla (no está en NO_ES_FALLA), relevo.DE_KIND lo marca
# como falla de obediencia. Además Attempt guarda `sesion` (uuid generado por el motor).
