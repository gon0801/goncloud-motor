"""¿Tiene este repo el revisor DeepSeek en este SHA? Una sola respuesta.

La leen el disparo de recuperación y la espera de la entrega. La revisión llega
por `ai-review-publish.yml` en `pull_request_target`, que GitHub corre con el
workflow de la base del PR, y por el disparo del motor, que lee el del SHA: se
miran los dos árboles. Solo es «sin revisor» si los dos se leyeron y ningún
workflow lo nombra; lo que no se pudo leer cuenta como revisor y espera.

El texto de la omisión se escribe y se lee aquí: el cierre y el check de merge
dicen su causa a partir del motivo registrado, sin otro campo en el registro.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .politica import ESPERA_SIN_REVISOR_MIN, TOPE_DEEPSEEK_MIN

WORKFLOWS = ".github/workflows"
# El revisor instalado con otro nombre de archivo se delata en su texto.
SENALES = ("ai-review", "goncloud-pr-review", "deepseek")
CAUSA_SIN_REVISOR = "sin revisor en el repo"
CAUSA_TRAS_EL_TOPE = "sin revisión tras el tope"


@dataclass(frozen=True)
class ConRevisor:
    """La revisión se espera hasta el tope de la política."""

    # (workflow, texto) que despacha la recuperación; None si el SHA no trae
    # el worker y no hay a quién despachar.
    disparo: tuple[str, str] | None
    espera_min: int = field(default=TOPE_DEEPSEEK_MIN, init=False)
    nota: str = field(default="", init=False)


@dataclass(frozen=True)
class SinRevisor:
    """No hay revisión que esperar: la entrega sigue como si llegara vacía."""

    motivo: str
    espera_min: int = field(default=ESPERA_SIN_REVISOR_MIN, init=False)
    disparo: None = field(default=None, init=False)

    @property
    def nota(self) -> str:
        return f" ({CAUSA_SIN_REVISOR}: {self.motivo})"


def revisor_deepseek(espacio: Path, sha: str, base: str) -> ConRevisor | SinRevisor:
    if _puede_tenerlo(espacio, sha):
        return ConRevisor(_disparo(espacio, sha))
    # El clon pudo quedarse atrás de la base con la que GitHub corre el PR.
    traida = _git(espacio, "fetch", "--quiet", "origin", base).returncode == 0
    if not traida or _puede_tenerlo(espacio, f"origin/{base}"):
        return ConRevisor(None)
    return SinRevisor(
        f"ni el SHA {sha[:12]} ni origin/{base} traen un workflow de ai-review"
    )


def texto_de_omision(
    revisor: ConRevisor | SinRevisor, sha: str, minutos: float, detalle: str = ""
) -> str:
    return (
        f"sin revisión de DeepSeek para el SHA {sha[:12]} tras {int(minutos)} min"
        f"{detalle}{revisor.nota}; la fase sigue como si no hubiera hallazgos"
    )


def omision_por_lectura_fallida(sha: str, minutos: float, causa: str) -> str | None:
    """Una lectura fallida no prueba que falte el revisor: espera el tope con o
    sin él. None mientras no pasa; después, el texto de la omisión."""
    if minutos < TOPE_DEEPSEEK_MIN:
        return None
    return (
        f"sin revisión de DeepSeek para el SHA {sha[:12]} tras {int(minutos)} min"
        f" (no se pudo leer: {causa[:200]}); la fase sigue como si no hubiera hallazgos"
    )


def causa_de_la_omision(motivo: str) -> str:
    """La causa que dice el motivo de una omisión registrada."""
    if f"({CAUSA_SIN_REVISOR}:" in motivo:
        return CAUSA_SIN_REVISOR
    return CAUSA_TRAS_EL_TOPE


def _puede_tenerlo(espacio: Path, arbol: str) -> bool:
    """False solo si el árbol se leyó y ningún workflow nombra al revisor."""
    nombres = _git(espacio, "ls-tree", "-r", "--name-only", arbol, "--", WORKFLOWS)
    if nombres.returncode != 0 or any(s in nombres.stdout.lower() for s in SENALES):
        return True
    patrones = [arg for senal in SENALES for arg in ("-e", senal)]
    texto = _git(espacio, "grep", "-l", "-i", *patrones, arbol, "--", WORKFLOWS)
    return texto.returncode != 1


def _disparo(espacio: Path, sha: str) -> tuple[str, str] | None:
    worker = _git(espacio, "show", f"{sha}:{WORKFLOWS}/ai-review-worker.yml")
    if worker.returncode != 0:
        return None
    publish = _git(espacio, "show", f"{sha}:{WORKFLOWS}/ai-review-publish.yml")
    if publish.returncode == 0:
        return "ai-review-publish.yml", publish.stdout
    return "ai-review-worker.yml", worker.stdout


def _git(espacio: Path, *args: str) -> subprocess.CompletedProcess:
    comando = ["git", "-C", str(espacio), *args]
    try:
        return subprocess.run(
            comando,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Un git que no corre o no termina es un árbol sin leer: cuenta como duda.
        return subprocess.CompletedProcess(comando, 128, "", str(exc))
