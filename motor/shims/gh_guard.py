"""`gh` para los agentes: todo pasa al `gh` real salvo mergear, cerrar, marcar listo o revisar PRs.

El único que mergea es el motor, con su MergeTicket. MOTOR 01 (2026-10-20): GitHub rechazó un
merge porque faltaban pruebas, el agente recibió el rechazo como tarea y mergeó él mismo con
`gh pr merge`. Este filtro corta ese camino; la regla también va escrita en cada encargo.
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
    subcomando: str
    rest: tuple[Rest, ...]
    graphql: tuple[str, ...]


ACCIONES = (
    Accion(
        "merge",
        (Rest("PUT", r"pulls/\d+/merge"),),
        ("mergePullRequest", "enablePullRequestAutoMerge", "enqueuePullRequest"),
    ),
    Accion("close", (Rest("PATCH", r"pulls/\d+", "state=closed"),), ("closePullRequest",)),
    Accion("ready", (), ("markPullRequestReadyForReview",)),
    Accion(
        "review",
        (Rest("POST", r"pulls/\d+/reviews"), Rest("POST", r"pulls/\d+/reviews/\d+/events")),
        ("addPullRequestReview", "submitPullRequestReview"),
    ),
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
            out.append(a[len(corta) :])
    return out


def _por_api(accion: Accion, args: list[str]) -> bool:
    campos = _valores(args, "-f", "--raw-field") + _valores(args, "-F", "--field")
    metodos = _valores(args, "-X", "--method")
    cuerpo = campos or any(a == "--input" or a.startswith("--input=") for a in args)
    metodo = metodos[-1].upper() if metodos else ("POST" if cuerpo else "GET")
    rest = any(
        metodo == r.metodo
        and any(re.search(rf"(^|/)repos/[^/]+/[^/]+/{r.ruta}/?$", a) for a in args)
        and (not r.campo or r.campo in campos)
        for r in accion.rest
    )
    graphql = "graphql" in args and any(
        re.search(rf"\b{m}\b", a) for a in args for m in accion.graphql
    )
    return rest or graphql


def bloqueado(args: list[str]) -> str:
    comando = _comando(args)
    for accion in ACCIONES:
        if comando == ("pr", accion.subcomando) or (
            comando[:1] == ("api",) and _por_api(accion, args)
        ):
            return f"gh pr {accion.subcomando}"
    return ""


def main() -> int:
    args = sys.argv[1:]
    que = bloqueado(args)
    if que:
        print(
            f"{que} está prohibido para los agentes: solo el motor mergea, marca listo o cierra PRs. "
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
