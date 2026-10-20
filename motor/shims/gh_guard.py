"""`gh` para los agentes: todo pasa al `gh` real salvo mergear, cerrar o aprobar PRs.

El único que mergea es el motor, con su MergeTicket. MOTOR 01 (2026-10-20): GitHub rechazó un
merge porque faltaban pruebas, el agente recibió el rechazo como tarea y mergeó él mismo con
`gh pr merge`. Este filtro corta ese camino; la regla también va escrita en cada encargo.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
PROHIBIDO = {("pr", "merge"), ("pr", "close"), ("pr", "ready"), ("pr", "review")}


def bloqueado(args: list[str]) -> str:
    sub = tuple(a for a in args if not a.startswith("-"))[:2]
    if sub in PROHIBIDO:
        return f"gh {' '.join(sub)}"
    if sub[:1] == ("api",) and any("/merge" in a for a in args):
        return "gh api .../merge"
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
        p
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if os.path.abspath(p or ".") != AQUI
    )
    real = shutil.which("gh", path=path)
    if not real:
        print("gh no está instalado", file=sys.stderr)
        return 127
    return subprocess.call([real, *args], env={**os.environ, "PATH": path})


if __name__ == "__main__":
    sys.exit(main())
