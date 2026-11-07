"""Configuraciones del único doble de GitHub (`FakeGitHub`).

Cada antiguo doble ad hoc era un `FakeGitHub` con otra respuesta de DeepSeek o
de los checks. Aquí se configura el mismo doble; no hay una segunda
implementación del puerto.
"""

from __future__ import annotations

import json
from pathlib import Path

from motor.github import FakeGitHub, MergeRechazado, PrView


def _texto(*hallazgos: dict) -> str:
    return "DeepSeek: CHANGES_REQUESTED\nai-review:findings=" + json.dumps(
        list(hallazgos)
    )


def github_con_hallazgos(
    d: Path, titulos: tuple[str, ...] = ("Fuga de token en el log",)
) -> FakeGitHub:
    """DeepSeek pide cambios con un hallazgo por título; sin títulos aprueba."""
    gh = FakeGitHub(d)
    gh.titulos = list(titulos)
    gh.sufijo = ""

    def responder(numero: int, sha: str) -> str:
        del numero
        if not gh.titulos:
            return "DeepSeek: APPROVED" + gh.sufijo
        return _texto(
            *(
                {
                    "id": f"ds_{titulo}",
                    "severity": "high",
                    "title": titulo,
                    "file": "saludo.txt",
                }
                for titulo in gh.titulos
            )
        )

    gh.responde_deepseek = responder
    return gh


def github_por_numero(
    d: Path, defecto: str = "DeepSeek: APPROVED"
) -> FakeGitHub:
    """El texto de `gh.textos[numero]`; los PR sin texto usan `defecto`."""
    gh = FakeGitHub(d)

    def responder(numero: int, sha: str) -> str:
        del sha
        texto = gh.textos.get(numero, gh.textos.get(None, defecto))
        if callable(texto):
            texto = texto(len(gh.llamadas))
        return texto if isinstance(texto, str) else ""

    gh.responde_deepseek = responder
    return gh


def github_por_commit(d: Path) -> FakeGitHub:
    """`gh.textos[i]` para el i-ésimo commit consultado; el resto, sin revisión."""
    gh = FakeGitHub(d)

    def responder(numero: int, sha: str) -> str:
        del numero
        indice = gh.vistos.index(sha)
        return gh.textos[indice] if indice < len(gh.textos) else ""

    gh.responde_deepseek = responder
    return gh


def github_con_comentario(d: Path) -> FakeGitHub:
    """`gh.hallazgo`, `gh.texto` o nada; `gh.falla` simula GitHub caído."""
    gh = FakeGitHub(d)
    gh.hallazgo = None
    gh.texto = None

    def responder(numero: int, sha: str) -> str:
        del numero
        if gh.texto is not None:
            return gh.texto
        return _texto(gh.hallazgo) if gh.hallazgo is not None else ""

    gh.responde_deepseek = responder
    return gh


def github_con_hallazgo(
    d: Path,
    archivo: str = "motor/integraciones/cli.py",
    severidad: str = "medium",
    identificador: str = "F1",
) -> FakeGitHub:
    """Un hallazgo estructurado en `archivo`, como el que DeepSeek publica."""
    gh = FakeGitHub(d)
    gh.archivo = archivo
    gh.severidad = severidad
    gh.identificador = identificador

    def responder(numero: int, sha: str) -> str:
        del numero
        return _texto(
            {
                "id": gh.identificador,
                "severity": gh.severidad,
                "title": "El comando de integraciones filtra el token",
                "file": gh.archivo,
                "line": 176,
            }
        )

    gh.responde_deepseek = responder
    return gh


def github_con_corridas(d: Path) -> FakeGitHub:
    """DeepSeek aprueba; las corridas salen de la config de `repos.json`."""
    return github_por_numero(d, "DeepSeek: APPROVED")


def github_fijo(
    directorio: Path,
    *,
    estado: str = "abierto",
    motivo: str = "",
    sha: str = "a" * 40,
) -> FakeGitHub:
    """Un PR fijo para las unidades que solo usan `ver` y el rechazo del merge."""
    gh = FakeGitHub(directorio)
    gh.estado = estado
    gh.motivo = motivo
    gh.sha = sha

    def ver(repo: str, numero: int) -> PrView:
        del repo
        return PrView(numero, "x", "main", False, gh.sha, gh.estado, "c" * 40)

    def merge(*args) -> PrView:
        del args
        raise MergeRechazado(gh.motivo)

    gh.ver = ver
    gh.merge = merge
    return gh
