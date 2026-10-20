"""Plan en Markdown -> PlanSpec. Frontera: todo lo que entra se valida aquí.

Formato (compatible con las tablas de Orbit, más campos por bloque):

    # Título
    autopiloto: motor
    plan: demo
    repo: git@github.com:dueño/repo.git      # repo por omisión de los bloques

    ## Bloque A — qué entrega
    lane: gate                      # fast | gate | release
    depende: -                      # ids de bloques, separados por coma
    aviso: ya puedes saludar        # frase llana para el aviso de entrega
    prueba-real: test -f x          # se corre contra producción después del merge/deploy
    repo: <url>                     # opcional: este bloque va en otro repo
    despliegue: desplegar=ruta checklist=ruta rollback=ruta   # opcional: si no, la receta del .motor.toml del repo
    humano: login Entra a Seller Central y acepta el permiso  # paso que solo David puede hacer
    sonda-humano: test -f ~/.login-ok                          # opcional: detecta el paso hecho
    | Fila | Contenido | Acepta |
    | --- | --- | --- |
    | A.1 | qué hacer | comando que debe salir 0 en el SHA entregado |
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LANES = ("fast", "gate", "release")
HUMAN_KINDS = ("login", "2fa", "captcha", "pago")


@dataclass(frozen=True)
class Row:
    id: str
    contenido: str
    acepta: str  # "" si la fila no trae comando


@dataclass(frozen=True)
class Release:
    desplegar: str
    checklist: str
    rollback: str


@dataclass(frozen=True)
class Human:
    kind: str
    que_hacer: str
    sonda: str = ""


@dataclass(frozen=True)
class Block:
    id: str
    titulo: str
    repo: str
    lane: str
    depende: tuple[str, ...]
    rows: tuple[Row, ...]
    aviso: str
    prueba_real: str
    release: Release | None
    human: Human | None
    checks: tuple[
        str, ...
    ] = ()  # checks que el plan exige además de la protección de la rama


@dataclass(frozen=True)
class Plan:
    id: str
    titulo: str
    blocks: tuple[Block, ...]
    # Pedido de David para ESTE plan ("implementa: qwen", "revisa: deepseek"). Vacío = la tabla.
    implementa: tuple[str, ...] = ()
    revisa: tuple[str, ...] = ()

    def block(self, bid: str) -> Block:
        for b in self.blocks:
            if b.id == bid:
                return b
        raise KeyError(bid)

    def to_dict(self) -> dict:
        def blk(b: Block) -> dict:
            return {
                "id": b.id,
                "titulo": b.titulo,
                "repo": b.repo,
                "lane": b.lane,
                "depende": list(b.depende),
                "rows": [r.__dict__ for r in b.rows],
                "aviso": b.aviso,
                "prueba_real": b.prueba_real,
                "release": b.release.__dict__ if b.release else None,
                "human": b.human.__dict__ if b.human else None,
                "checks": list(b.checks),
            }

        return {
            "id": self.id,
            "titulo": self.titulo,
            "blocks": [blk(b) for b in self.blocks],
            "implementa": list(self.implementa),
            "revisa": list(self.revisa),
        }

    @staticmethod
    def from_dict(d: dict) -> Plan:
        blocks = []
        for b in d["blocks"]:
            blocks.append(
                Block(
                    id=b["id"],
                    titulo=b["titulo"],
                    repo=b["repo"],
                    lane=b["lane"],
                    depende=tuple(b["depende"]),
                    rows=tuple(Row(**r) for r in b["rows"]),
                    aviso=b["aviso"],
                    prueba_real=b["prueba_real"],
                    release=Release(**b["release"]) if b["release"] else None,
                    human=Human(**b["human"]) if b["human"] else None,
                    checks=tuple(b.get("checks", ())),
                )
            )
        return Plan(
            id=d["id"],
            titulo=d["titulo"],
            blocks=tuple(blocks),
            implementa=tuple(d.get("implementa", ())),
            revisa=tuple(d.get("revisa", ())),
        )


@dataclass
class _Draft:
    id: str
    titulo: str
    fields: dict[str, str] = field(default_factory=dict)
    rows: list[Row] = field(default_factory=list)


class PlanError(ValueError):
    def __init__(self, errores: list[str]):
        super().__init__("\n".join(errores))
        self.errores = errores


_BLOQUE = re.compile(r"^##\s+Bloque\s+(\S+)\s*(?:[—-]+\s*(.*))?$")
_CAMPO = re.compile(r"^([a-z-]+):\s*(.*)$")


def _celdas(linea: str) -> list[str]:
    """Separa por `|`, salvo `\\|` (un pipe dentro de la celda, como en Markdown)."""
    partes = re.split(r"(?<!\\)\|", linea.strip().strip("|"))
    return [c.strip().replace("\\|", "|") for c in partes]


def _modelos(valor: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(x.strip().lower() for x in valor.split(",") if x.strip())
    )


def modelos_desconocidos(plan: Plan, conocidos) -> list[str]:
    conocidos = set(conocidos)
    malos = [mo for mo in (*plan.implementa, *plan.revisa) if mo not in conocidos]
    if not malos:
        return []
    return [
        f"no conozco el modelo {', '.join(dict.fromkeys(malos))}; los que hay: {', '.join(sorted(conocidos))}"
    ]


def parse_plan(
    texto: str,
    *,
    min_implementadores: int = 0,
    implementadores: int = 0,
    modelos: set[str] | None = None,
) -> Plan:
    """Lee el plan y lo valida entero. Devuelve Plan o lanza PlanError con TODOS los errores.
    Con `modelos` (los de la conf) también revisa que existan los que pide `implementa:`/`revisa:`."""
    titulo, plan_id, autopiloto, repo_plan = "", "", "", ""
    implementa: tuple[str, ...] = ()
    revisa: tuple[str, ...] = ()
    drafts: list[_Draft] = []
    errores: list[str] = []
    for linea in texto.splitlines():
        if linea.startswith("# ") and not titulo:
            titulo = linea[2:].strip()
            continue
        m = _BLOQUE.match(linea)
        if m:
            drafts.append(_Draft(id=m.group(1), titulo=(m.group(2) or "").strip()))
            continue
        if linea.startswith("|"):
            if not drafts:
                continue
            celdas = _celdas(linea)
            if celdas and (
                celdas[0].lower() in ("fila", "task") or set(celdas[0]) <= set("-: ")
            ):
                continue
            if len(celdas) < 2:
                errores.append(f"fila mal formada: {linea!r}")
                continue
            acepta = celdas[2] if len(celdas) > 2 and celdas[2] not in ("-", "") else ""
            drafts[-1].rows.append(
                Row(id=celdas[0], contenido=celdas[1], acepta=acepta)
            )
            continue
        m = _CAMPO.match(linea.strip())
        if m:
            clave, valor = m.group(1), m.group(2).strip()
            if drafts:
                drafts[-1].fields[clave] = valor
            elif clave == "plan":
                plan_id = valor
            elif clave == "autopiloto":
                autopiloto = valor
            elif clave == "repo":
                repo_plan = valor
            elif clave == "implementa":
                implementa = _modelos(valor)
            elif clave == "revisa":
                revisa = _modelos(valor)

    if autopiloto != "motor":
        errores.append("falta la línea 'autopiloto: motor'")
    if not plan_id:
        errores.append("falta la línea 'plan: <id>'")
    if not drafts:
        errores.append("el plan no tiene bloques ('## Bloque X — ...')")

    ids = [d.id for d in drafts]
    if len(set(ids)) != len(ids):
        errores.append("hay ids de bloque repetidos")
    blocks: list[Block] = []
    for d in drafts:
        f = d.fields
        lane = f.get("lane", "")
        if lane not in LANES:
            errores.append(f"bloque {d.id}: lane debe ser una de {LANES}, no {lane!r}")
        dep = tuple(
            x.strip()
            for x in f.get("depende", "-").split(",")
            if x.strip() not in ("", "-")
        )
        for x in dep:
            if x not in ids:
                errores.append(f"bloque {d.id}: depende de {x}, que no existe")
        release = None
        if "despliegue" in f:
            partes = dict(p.split("=", 1) for p in f["despliegue"].split() if "=" in p)
            faltan = {"desplegar", "checklist", "rollback"} - partes.keys()
            if faltan:
                errores.append(
                    f"bloque {d.id}: al despliegue le falta {sorted(faltan)}"
                )
            else:
                release = Release(
                    partes["desplegar"], partes["checklist"], partes["rollback"]
                )
        repo = f.get("repo", repo_plan)
        if not repo:
            errores.append(
                f"bloque {d.id}: sin repo (pon 'repo:' en el plan o en el bloque)"
            )
        human = None
        if "humano" in f:
            kind, _, que = f["humano"].partition(" ")
            if kind not in HUMAN_KINDS:
                errores.append(
                    f"bloque {d.id}: 'humano: {kind}' no vale; solo {HUMAN_KINDS}. "
                    "Lo demás lo resuelve el sistema sin preguntar"
                )
            human = Human(
                kind=kind, que_hacer=que.strip(), sonda=f.get("sonda-humano", "")
            )
        if lane in ("gate", "release") and human is None:
            if not d.rows:
                errores.append(f"bloque {d.id}: un bloque de código necesita filas")
            if not any(r.acepta for r in d.rows):
                errores.append(
                    f"bloque {d.id}: ninguna fila trae 'Acepta' (comando que pruebe la entrega)"
                )
            if not f.get("prueba-real"):
                errores.append(
                    f"bloque {d.id}: falta 'prueba-real:' (datos reales antes de decir listo)"
                )
        blocks.append(
            Block(
                id=d.id,
                titulo=d.titulo,
                repo=repo,
                lane=lane,
                depende=dep,
                rows=tuple(d.rows),
                aviso=f.get("aviso", d.titulo),
                prueba_real=f.get("prueba-real", ""),
                release=release,
                human=human,
                checks=tuple(
                    x.strip() for x in f.get("checks", "").split(",") if x.strip()
                ),
            )
        )

    # ciclos
    grafo = {b.id: b.depende for b in blocks}
    visit: dict[str, int] = {}

    def dfs(n: str) -> bool:
        visit[n] = 1
        for m in grafo.get(n, ()):
            if visit.get(m) == 1 or (visit.get(m) is None and m in grafo and dfs(m)):
                return True
        visit[n] = 2
        return False

    if any(visit.get(n) is None and dfs(n) for n in grafo):
        errores.append("las dependencias entre bloques forman un ciclo")
    if revisa and implementa and set(revisa) <= set(implementa):
        errores.append(
            "'revisa:' necesita al menos un modelo que no esté en 'implementa:'; "
            "nadie revisa su propio trabajo"
        )
    if not implementa and min_implementadores and implementadores < min_implementadores:
        errores.append(
            f"la política tiene {implementadores} implementador(es); se exigen {min_implementadores} "
            "para que una cuota agotada no detenga el plan"
        )
    plan = Plan(
        id=plan_id,
        titulo=titulo,
        blocks=tuple(blocks),
        implementa=implementa,
        revisa=revisa,
    )
    if modelos is not None:
        errores += modelos_desconocidos(plan, modelos)
    if errores:
        raise PlanError(errores)
    return plan
