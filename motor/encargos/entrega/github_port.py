"""Operaciones de GitHub para la entrega, compartidas por puerto real y doble."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from motor.workspace import git

if TYPE_CHECKING:
    from motor.github import PrView


@dataclass(frozen=True)
class CapacidadRemota:
    """Lo que una mirada de solo lectura al remoto ya deja comprobado.

    `integrar_bloqueado`: True solo con una regla fija y legible que impide
    mergear (revisiones humanas requeridas o el actor sin permiso de push);
    False cuando todas las fuentes consultadas salieron limpias; None cuando
    algo quedó sin leer (red, 403 ambiguo, respuesta ilegible). None nunca
    baja un nivel: produce espera o reintento en el flujo normal.
    """

    repo_ok: bool
    base_ok: bool | None
    integrar_bloqueado: bool | None
    detalle: str = ""


class EntregaGh:
    @staticmethod
    def nombre(repo: str) -> str:
        raise NotImplementedError

    def _gh(self, *args: str) -> str:
        raise NotImplementedError

    def _vista(self, repo: str, numero: int) -> PrView:
        raise NotImplementedError

    @staticmethod
    def _paginas(texto: str) -> list:
        out, dec, i = [], json.JSONDecoder(), 0
        texto = texto.strip()
        while i < len(texto):
            while i < len(texto) and texto[i].isspace():
                i += 1
            if i >= len(texto):
                break
            obj, j = dec.raw_decode(texto, i)
            out.append(obj)
            i = j
        return out

    def _es_404(self, e: RuntimeError) -> bool:
        t = str(e)
        return "HTTP 404" in t or " 404" in t

    def cerrar_pr(self, repo: str, numero: int) -> PrView:
        vista = self._vista(repo, numero)
        if vista.estado == "abierto":
            self._gh("pr", "close", str(numero), "-R", self.nombre(repo))
            vista = self._vista(repo, numero)
        return vista

    def rama_existe(self, repo: str, rama: str) -> bool:
        try:
            self._gh("api", f"repos/{self.nombre(repo)}/git/ref/heads/"
                     f"{quote(rama, safe='/')}")
        except RuntimeError as exc:
            if self._es_404(exc):
                return False
            raise
        return True

    def borrar_rama(self, repo: str, rama: str) -> None:
        if not rama.startswith("encargo/"):
            raise ValueError("solo se borran ramas encargo/*")
        if self.rama_existe(repo, rama):
            self._gh("api", "-X", "DELETE",
                     f"repos/{self.nombre(repo)}/git/refs/heads/"
                     f"{quote(rama, safe='/')}")

    def prs_con_base(self, repo: str, base: str) -> tuple[int, ...]:
        numeros = self._gh(
            "api", "--paginate",
            f"repos/{self.nombre(repo)}/pulls?state=open&base="
            f"{quote(base, safe='')}&per_page=100",
            "--jq", ".[].number",
        )
        return tuple(int(n) for n in numeros.split())

    def prs_con_head(self, repo: str, rama: str) -> tuple[int, ...]:
        nombre = self.nombre(repo)
        numeros = self._gh(
            "api", "--paginate",
            f"repos/{nombre}/pulls?state=open&head="
            f"{quote(nombre.split('/')[0] + ':' + rama, safe='')}&per_page=100",
            "--jq", ".[].number",
        )
        return tuple(int(n) for n in numeros.split())


class EntregaFake:
    def _cargar(self) -> dict:
        raise NotImplementedError

    def _guardar(self, estado: dict) -> None:
        raise NotImplementedError

    def _pr(self, estado: dict, repo: str, numero: int) -> dict:
        raise NotImplementedError

    def _vista(self, repo: str, pr: dict) -> PrView:
        raise NotImplementedError

    def cerrar_pr(self, repo: str, numero: int) -> PrView:
        estado = self._cargar()
        pr = self._pr(estado, repo, numero)
        if pr["estado"] == "abierto":
            pr["estado"] = "cerrado"
            self._guardar(estado)
        return self._vista(repo, pr)

    def rama_existe(self, repo: str, rama: str) -> bool:
        resultado = git("ls-remote", repo, f"refs/heads/{rama}")
        if resultado.returncode != 0:
            raise RuntimeError("no se pudo leer el remoto GitHub del doble")
        return bool(resultado.stdout.strip())

    def borrar_rama(self, repo: str, rama: str) -> None:
        if not rama.startswith("encargo/"):
            raise ValueError("solo se borran ramas encargo/*")
        if self.rama_existe(repo, rama):
            resultado = git("push", "-q", repo, f":refs/heads/{rama}")
            if resultado.returncode != 0:
                raise RuntimeError("no se pudo borrar la rama del doble")

    def prs_con_base(self, repo: str, base: str) -> tuple[int, ...]:
        estado = self._cargar()
        return tuple(sorted(
            pr["numero"] for clave, pr in estado["prs"].items()
            if clave.startswith(repo + "#") and pr["base"] == base
            and pr["estado"] == "abierto"
        ))

    def prs_con_head(self, repo: str, rama: str) -> tuple[int, ...]:
        estado = self._cargar()
        return tuple(sorted(
            pr["numero"] for clave, pr in estado["prs"].items()
            if clave.startswith(repo + "#") and pr["rama"] == rama
            and pr["estado"] == "abierto"
        ))
