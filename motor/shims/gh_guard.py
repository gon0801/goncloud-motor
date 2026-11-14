"""`gh` para los agentes: todo pasa al `gh` real salvo abrir, mergear, cerrar, marcar listo o
revisar PRs, mover, borrar o escribir una rama por la API sin PR o renombrar comandos con un alias.

El único que abre y mergea es el motor, con su MergeTicket. MOTOR 01 (2026-10-20): GitHub
rechazó un merge porque faltaban pruebas, el agente recibió el rechazo como tarea y mergeó él
mismo con `gh pr merge`. Este filtro corta ese camino; la regla también va escrita en cada
encargo.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

AQUI = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class Rest:
    metodo: str
    ruta: str
    campo: str = ""


@dataclass(frozen=True)
class Accion:
    nombre: str
    cli: tuple[tuple[str, str], ...]
    rest: tuple[Rest, ...] = ()
    graphql: tuple[str, ...] = ()


ACCIONES = (
    Accion(
        "gh pr merge",
        (("pr", "merge"),),
        (Rest("PUT", r"pulls/\d+/merge"),),
        ("mergePullRequest", "enablePullRequestAutoMerge", "enqueuePullRequest"),
    ),
    Accion(
        "escribir en una rama por la API, sin PR",
        (),
        (
            Rest("POST", r"merges"),
            Rest("POST", r"git/refs"),
            Rest("PATCH", r"git/refs/.+"),
            Rest("DELETE", r"git/refs/.+"),
            Rest("PUT", r"contents/.+"),
            Rest("DELETE", r"contents/.+"),
        ),
        (
            "mergeBranch",
            "createRef",
            "updateRef",
            "updateRefs",
            "deleteRef",
            "createCommitOnBranch",
        ),
    ),
    Accion(
        "gh pr close",
        (("pr", "close"),),
        (Rest("PATCH", r"pulls/\d+", "state=closed"),),
        ("closePullRequest",),
    ),
    Accion("gh pr ready", (("pr", "ready"),), graphql=("markPullRequestReadyForReview",)),
    Accion(
        "gh pr create",
        (("pr", "create"), ("pr", "new")),
        (Rest("POST", r"pulls"),),
        ("createPullRequest",),
    ),
    Accion(
        "gh pr review",
        (("pr", "review"),),
        (Rest("POST", r"pulls/\d+/reviews"), Rest("POST", r"pulls/\d+/reviews/\d+/events")),
        ("addPullRequestReview", "submitPullRequestReview"),
    ),
    Accion("crear un alias de gh", (("alias", "set"), ("alias", "import"))),
)


def _comando(args: list[str]) -> tuple[str, ...]:
    """Las dos primeras palabras del comando, leídas como cobra: una bandera sin `=` se lleva el valor
    siguiente (`-R o/r`, `--repo o/r`)."""
    palabras: list[str] = []
    salta = False
    for a in args:
        if salta:
            salta = False
        elif not a.startswith("-"):
            palabras.append(a)
        else:
            salta = "=" not in a and (a.startswith("--") or len(a) == 2)
    return tuple(palabras[:2])


def _valores(args: list[str], corta: str, larga: str) -> list[str]:
    out = []
    for i, a in enumerate(args):
        if a in (corta, larga):
            out.append(args[i + 1] if i + 1 < len(args) else "")
        elif a.startswith(f"{larga}="):
            out.append(a[len(larga) + 1 :])
        elif a.startswith(corta) and not a.startswith("--"):
            out.append(a[len(corta) :].removeprefix("="))
    return out


def _oculto(args: list[str]) -> bool:
    """El cuerpo viene de un archivo o de stdin (`--input`, `-F campo=@archivo`): la guardia no
    puede leer qué pide."""
    return any(a == "--input" or a.startswith("--input=") for a in args) or any(
        v.partition("=")[2].startswith("@") for v in _valores(args, "-F", "--field")
    )


def _es_graphql(args: list[str]) -> bool:
    return any(re.search(r"(^|/)graphql/?(\?.*)?$", a) for a in args)


def _por_api(accion: Accion, args: list[str]) -> bool:
    campos = _valores(args, "-f", "--raw-field") + _valores(args, "-F", "--field")
    metodos = _valores(args, "-X", "--method")
    oculto = _oculto(args)
    metodo = metodos[-1].upper() if metodos else ("POST" if campos or oculto else "GET")
    rest = any(
        metodo == r.metodo
        and any(re.search(rf"(^|/)repos/[^/]+/[^/]+/{r.ruta}/?(\?.*)?$", a) for a in args)
        and (not r.campo or r.campo in campos or oculto)
        for r in accion.rest
    )
    graphql = _es_graphql(args) and any(
        re.search(rf"\b{m}\b", a) for a in args for m in accion.graphql
    )
    return rest or graphql


def bloqueado(args: list[str]) -> str:
    comando = _comando(args)
    api = comando[:1] == ("api",)
    if api and _es_graphql(args) and _oculto(args):
        return "gh api graphql con la consulta en un archivo o en stdin"
    for accion in ACCIONES:
        if comando in accion.cli or (api and _por_api(accion, args)):
            return accion.nombre
    return ""


def main() -> int:
    args = sys.argv[1:]
    que = bloqueado(args)
    if que:
        print(
            f"{que} está prohibido para los agentes: solo el motor mergea, abre, marca listo o "
            "cierra PRs y mueve la rama base. "
            "Si el merge falló por conflicto, actualiza tu rama con la principal y vuelve a entregar.",
            file=sys.stderr,
        )
        return 3
    path = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep) if os.path.abspath(p or ".") != AQUI
    )
    real = shutil.which("gh", path=path)
    if not real:
        print("gh no está instalado", file=sys.stderr)
        return 127
    return subprocess.call([real, *args], env={**os.environ, "PATH": path})


if __name__ == "__main__":
    sys.exit(main())
