"""Regla pura para decidir qué procesos forman el árbol de un agente."""

from __future__ import annotations


def candidatos_por_ppid(padres: dict[int, int], raiz: int) -> list[int]:
    hijos = []
    for candidato, padre in padres.items():
        visto = {candidato}
        actual = padre
        for _ in range(64):
            if actual == raiz:
                hijos.append(candidato)
                break
            if actual in visto or actual == 0:
                break
            visto.add(actual)
            if actual not in padres:
                break
            actual = padres[actual]
    return hijos


def descendientes_validos(
    snapshot: dict[int, tuple[int, int | None]], raiz: int,
    fin: int | None = None,
) -> list[int]:
    """PIDs que cuelgan de `raiz` con cada eslabón nacido en o después de su padre.

    `snapshot` asocia cada PID con su PPID y su hora de creación (epoch). Una hora
    ausente no cuenta: la rama que pasa por ese eslabón no se adopta.
    """
    inicio_raiz = _creado(snapshot, raiz)
    if inicio_raiz is None:
        return []
    return sorted(
        pid
        for pid in snapshot
        if pid != raiz and _cadena_valida(snapshot, pid, raiz, inicio_raiz)
        and (fin is None or ((creado := _creado(snapshot, pid)) is not None and creado < fin))
    )


def _creado(
    snapshot: dict[int, tuple[int, int | None]], pid: int
) -> int | None:
    entrada = snapshot.get(pid)
    return None if entrada is None else entrada[1]


def _cadena_valida(
    snapshot: dict[int, tuple[int, int | None]],
    pid: int,
    raiz: int,
    inicio_raiz: int,
) -> bool:
    actual = pid
    visto: set[int] = set()
    while actual != raiz:
        if actual in visto:
            return False
        visto.add(actual)
        entrada = snapshot.get(actual)
        if entrada is None:
            return False
        padre, creado = entrada
        if creado is None:
            return False
        if padre == raiz:
            return creado >= inicio_raiz
        padre_creado = _creado(snapshot, padre)
        if padre_creado is None or creado < padre_creado:
            return False
        actual = padre
    return False
