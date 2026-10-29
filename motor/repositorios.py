"""La fuente de un clon puede ser local; su publicación conserva el remoto real."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname


@dataclass(frozen=True)
class Repositorio:
    fuente: str
    remoto: str
    github: str | None


def _local(valor: str, relativo: Path | None = None) -> Path | None:
    if valor.startswith("file://"):
        url = urlsplit(valor)
        valor = ("//" + url.netloc if url.netloc else "") + url2pathname(url.path)
    elif "://" in valor or re.match(r"(?:[^/@]+@)?[^/:]+:[^/\\]", valor):
        return None
    path = Path(valor).expanduser()
    if not (
        path.is_absolute()
        or relativo is not None
        or valor.startswith((".", "~"))
        or re.match(r"^[A-Za-z]:[/\\]", valor)
        or (relativo or Path.cwd()).joinpath(path).exists()
    ):
        return None
    return (
        (relativo / path) if relativo is not None and not path.is_absolute() else path
    ).resolve()


def _nombre(remoto: str) -> str:
    remoto = remoto.rstrip("/").removesuffix(".git")
    if "://" in remoto:
        url = urlsplit(remoto)
        ruta = url.path.strip("/")
        return ruta if url.hostname == "github.com" else f"{url.hostname}/{ruta}"
    if ":" in remoto:
        return remoto.split(":", 1)[1]
    return remoto


def resolver_repositorio(repo: str) -> Repositorio:
    local = _local(repo)
    fuente = (
        str(local)
        if local is not None
        else (
            repo if "://" in repo or ":" in repo else f"https://github.com/{repo}.git"
        )
    )
    remoto, relativo = fuente, None
    visitados: set[Path] = set()
    while (local := _local(remoto, relativo)) is not None:
        if local in visitados:
            raise ValueError("los remotos locales del repositorio forman un ciclo")
        visitados.add(local)
        if not local.is_dir():
            return Repositorio(fuente, str(local), None)
        resultado = subprocess.run(
            [
                "git",
                "-C",
                str(local),
                "config",
                "--local",
                "--get",
                "remote.origin.url",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        if resultado.returncode not in (0, 1):
            raise ValueError("no se pudo leer el remoto del repositorio local")
        if resultado.returncode == 1 or not resultado.stdout.strip():
            return Repositorio(fuente, str(local), None)
        remoto, relativo = resultado.stdout.strip(), local
    return Repositorio(fuente, remoto, _nombre(remoto))
