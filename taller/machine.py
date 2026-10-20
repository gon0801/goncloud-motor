"""Lógica pura: eventos -> estado -> acciones. Sin procesos, sin red, sin reloj propio.

`fold` pliega el log en una vista por bloque; `decide` devuelve las acciones que
tocan ahora. No existe un estado FALLADO ni PAUSADO: un fallo solo produce otra
acción (reintento, relevo, arreglo). La única espera es la de un paso humano
declarado en el plan.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field

from .plan import Block, Plan
from .store import Event


# --------------------------------------------------------------------------- política

@dataclass(frozen=True)
class Provider:
    id: str
    argv: tuple[str, ...]          # plantilla; {brief}, {prompt}, {result} se reemplazan
    max_parallel: int = 1
    env: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Policy:
    implementadores: tuple[str, ...]   # escalera de relevo, en orden
    revisores: tuple[str, ...]
    providers: dict[str, Provider]
    timeout_s: float = 4 * 3600
    sin_avance_s: float = 45 * 60
    fallas_seguidas_max: int = 2       # misma falla del mismo proveedor -> siguiente de la escalera
    rondas_max_por_proveedor: int = 5
    cooldown_default_s: float = 5 * 3600
    aviso_lento_s: float = 6 * 3600


# --------------------------------------------------------------------------- vista

class Estado(enum.Enum):
    ESPERANDO = "esperando dependencias"
    HUMANO = "espera un paso de David"
    IMPLEMENTANDO = "implementando"
    REVISANDO = "revisando"
    VERIFICAR = "por verificar"
    REVISAR = "por revisar"
    ARREGLAR = "por arreglar"
    CI = "por correr CI"
    MERGE = "por mergear"
    DESPLEGAR = "por desplegar"
    PROBAR = "por probar con datos reales"
    SIN_AGENTE = "sin agente disponible"
    HECHO = "hecho"


@dataclass
class Attempt:
    n: int
    role: str                 # implementar | revisar
    provider: str
    started: float
    pid: int
    dir: str
    sha: str = ""             # revisar: el SHA revisado
    ended: dict | None = None # outcome clasificado


@dataclass
class BlockView:
    block: Block
    attempts: list[Attempt] = field(default_factory=list)
    verified: dict[str, dict] = field(default_factory=dict)
    reviews: list[dict] = field(default_factory=list)
    ci: dict[str, dict] = field(default_factory=dict)
    merged: list[str] = field(default_factory=list)
    deployed: dict[str, dict] = field(default_factory=dict)
    proved: dict[str, dict] = field(default_factory=dict)
    human_done: bool = False
    notes: list[str] = field(default_factory=list)
    notices: set[str] = field(default_factory=set)

    # ----- derivados
    def implement_attempts(self) -> list[Attempt]:
        return [a for a in self.attempts if a.role == "implementar"]

    def running(self) -> Attempt | None:
        for a in self.attempts:
            if a.ended is None:
                return a
        return None

    def head(self) -> str:
        """SHA de la última entrega verificada como existente (vacío si nunca entregó)."""
        for a in reversed(self.implement_attempts()):
            if a.ended and a.ended.get("kind") == "entregado":
                return a.ended["sha"]
        return ""

    def autores(self) -> set[str]:
        """Todo proveedor que trabajó en el código del bloque (aunque su intento fallara a medias)."""
        return {a.provider for a in self.implement_attempts()}

    def last_implement_outcome(self) -> dict | None:
        imp = self.implement_attempts()
        return imp[-1].ended if imp else None

    def review_for(self, sha: str) -> dict | None:
        rs = [r for r in self.reviews if r["sha"] == sha]
        return rs[-1] if rs else None


@dataclass
class Snapshot:
    plan: Plan
    blocks: dict[str, BlockView]
    cooldowns: dict[str, float]           # proveedor -> hasta (epoch)
    plan_notices: set[str]


def fold(plan: Plan, events: list[Event]) -> Snapshot:
    blocks = {b.id: BlockView(block=b) for b in plan.blocks}
    cooldowns: dict[str, float] = {}
    plan_notices: set[str] = set()
    for e in events:
        d = e.data
        bv = blocks.get(e.block)
        if e.kind == "Cooldown":
            cooldowns[d["provider"]] = max(cooldowns.get(d["provider"], 0), d["hasta"])
        elif e.kind == "NoticeQueued":
            (bv.notices if bv else plan_notices).add(d["key"])
        elif bv is None:
            continue
        elif e.kind == "AttemptStarted":
            bv.attempts.append(Attempt(n=d["n"], role=d["role"], provider=d["provider"],
                                       started=e.ts, pid=d["pid"], dir=d["dir"], sha=d.get("sha", "")))
        elif e.kind == "AttemptEnded":
            for a in bv.attempts:
                if a.n == d["n"] and a.role == d["role"] and a.ended is None:
                    a.ended = d["outcome"]
        elif e.kind == "Verified":
            bv.verified[d["sha"]] = d
        elif e.kind == "Reviewed":
            bv.reviews.append(d)
        elif e.kind == "CiResult":
            bv.ci[d["sha"]] = d
        elif e.kind == "Merged":
            bv.merged.append(d["sha"])
        elif e.kind == "Deployed":
            bv.deployed[d["sha"]] = d
        elif e.kind == "Proved":
            bv.proved[d["sha"]] = d
        elif e.kind == "HumanDone":
            bv.human_done = True
        elif e.kind == "Note":
            bv.notes.append(d["texto"])
    return Snapshot(plan=plan, blocks=blocks, cooldowns=cooldowns, plan_notices=plan_notices)


# --------------------------------------------------------------------------- estado

def _bloqueantes(review: dict) -> list[dict]:
    """Regla 4 de quality-kit: solo bloquea lo que trae comando que lo reproduce."""
    return [b for b in review.get("bloqueantes", []) if b.get("repro", "").strip()]


def _aprobado(review: dict) -> bool:
    return review["veredicto"] == "APROBADO" or not _bloqueantes(review)


def estado(snap: Snapshot, bid: str) -> Estado:
    bv = snap.blocks[bid]
    b = bv.block
    if bv.merged and bv.proved.get(bv.merged[-1], {}).get("ok"):
        return Estado.HECHO
    if b.human and not bv.human_done:
        return Estado.HUMANO
    if b.human and not b.rows:
        return Estado.HECHO
    if any(estado(snap, dep) is not Estado.HECHO for dep in b.depende):
        return Estado.ESPERANDO
    run = bv.running()
    if run:
        return Estado.IMPLEMENTANDO if run.role == "implementar" else Estado.REVISANDO
    head = bv.head()
    if bv.merged and head == bv.merged[-1]:
        m = bv.merged[-1]
        if b.release and not bv.deployed.get(m, {}).get("ok"):
            return Estado.ARREGLAR if m in bv.deployed else Estado.DESPLEGAR
        if m in bv.proved:
            return Estado.ARREGLAR   # la prueba real falló
        return Estado.PROBAR
    last = bv.last_implement_outcome()
    if not head or (last and last.get("kind") != "entregado"):
        return Estado.ARREGLAR
    v = bv.verified.get(head)
    if v is None:
        return Estado.VERIFICAR
    if not v["ok"]:
        return Estado.ARREGLAR
    r = bv.review_for(head)
    if r is None:
        return Estado.REVISAR
    if not _aprobado(r):
        return Estado.ARREGLAR
    c = bv.ci.get(head)
    if c is None:
        return Estado.CI
    if not c["green"]:
        return Estado.ARREGLAR
    return Estado.MERGE


# --------------------------------------------------------------------------- acciones

@dataclass(frozen=True)
class StartAttempt:
    block: str
    role: str
    provider: str
    n: int
    sha: str
    feedback: tuple[str, ...]


@dataclass(frozen=True)
class Verify:
    block: str
    sha: str


@dataclass(frozen=True)
class RunCI:
    block: str
    sha: str


@dataclass(frozen=True)
class MergeTicket:
    """Solo `merge_gate` lo construye. Sin ticket no hay merge (ver effects.merge)."""
    block: str
    sha: str


@dataclass(frozen=True)
class Merge:
    ticket: MergeTicket


@dataclass(frozen=True)
class Deploy:
    block: str
    sha: str


@dataclass(frozen=True)
class Prove:
    block: str
    sha: str


@dataclass(frozen=True)
class ProbeHuman:
    block: str


@dataclass(frozen=True)
class Notify:
    key: str
    block: str
    template: str
    campos: tuple[tuple[str, str], ...]


Action = StartAttempt | Verify | RunCI | Merge | Deploy | Prove | ProbeHuman | Notify


def merge_gate(bv: BlockView) -> MergeTicket | str:
    """El único camino a un MergeTicket: todo sobre el MISMO SHA de cabeza."""
    head = bv.head()
    if not head:
        return "sin entrega"
    if bv.running():
        return "hay un intento corriendo"
    if not bv.verified.get(head, {}).get("ok"):
        return "la entrega no está verificada"
    r = bv.review_for(head)
    if r is None or not _aprobado(r):
        return "sin veredicto APROBADO sobre ese SHA"
    if not bv.ci.get(head, {}).get("green"):
        return "CI no está en verde sobre ese SHA"
    return MergeTicket(block=bv.block.id, sha=head)


def _feedback(bv: BlockView) -> tuple[str, ...]:
    """Qué le decimos al siguiente intento del implementador, en orden."""
    out: list[str] = []
    last = bv.last_implement_outcome()
    if last and last.get("kind") != "entregado":
        out.append(f"El intento anterior terminó por: {last.get('kind')} ({last.get('detalle', '')[:300]}). "
                   "Sigue desde los commits que haya en la rama.")
    head = bv.head()
    if head:
        v = bv.verified.get(head)
        if v and not v["ok"]:
            out.append(f"La verificación falló: {v.get('detalle', '')[:800]}")
        r = bv.review_for(head)
        if r and not _aprobado(r):
            for blq in _bloqueantes(r):
                out.append(f"BLOQUEANTE: {blq['texto']} | repro: {blq['repro']}"
                           + (f" | arreglo: {blq['arreglo']}" if blq.get("arreglo") else ""))
        c = bv.ci.get(head)
        if c and not c["green"]:
            out.append(f"CI en rojo: {c.get('detalle', '')[:800]}")
        if bv.merged and bv.merged[-1] == head:
            m = head
            if m in bv.deployed and not bv.deployed[m]["ok"]:
                out.append(f"El despliegue falló y se revirtió: {bv.deployed[m].get('detalle', '')[:800]}")
            if m in bv.proved and not bv.proved[m]["ok"]:
                out.append(f"La prueba con datos reales falló: {bv.proved[m].get('detalle', '')[:800]}")
    out.extend(f"Nota del lead: {n}" for n in bv.notes)
    return tuple(out)


def _ocupados(snap: Snapshot) -> dict[str, int]:
    n: dict[str, int] = {}
    for bv in snap.blocks.values():
        a = bv.running()
        if a:
            n[a.provider] = n.get(a.provider, 0) + 1
    return n


def _elige(ladder: tuple[str, ...], snap: Snapshot, policy: Policy, now: float,
           bv: BlockView, role: str, extra_ocupados: dict[str, int]) -> str | None:
    ocupados = _ocupados(snap)
    for k, v in extra_ocupados.items():
        ocupados[k] = ocupados.get(k, 0) + v
    mios = [a for a in bv.attempts if a.role == role]
    for p in ladder:
        if snap.cooldowns.get(p, 0) > now:
            continue
        if ocupados.get(p, 0) >= policy.providers[p].max_parallel:
            continue
        # fallas de este proveedor en este bloque desde la última entrega buena de cualquiera
        # -> que pase al siguiente. Contar por proveedor (no en la secuencia global) evita
        # que dos agentes que fallan se turnen para siempre.
        fallas = 0
        for a in reversed(mios):
            if a.ended and a.ended.get("kind") in ("entregado", "veredicto"):
                break
            if a.provider == p and a.ended:
                fallas += 1
        if fallas >= policy.fallas_seguidas_max:
            continue
        if role == "implementar":
            rondas = sum(1 for a in mios if a.provider == p and a.ended and a.ended.get("kind") == "entregado")
            if rondas >= policy.rondas_max_por_proveedor:
                continue
            if _mismo_bloqueante_dos_veces(bv) and mios and mios[-1].provider == p and len(ladder) > 1:
                continue   # ojos nuevos: el mismo bloqueante volvió, cambia de implementador
        return p
    return None


def revisores_para(bv: BlockView, policy: Policy) -> tuple[str, ...]:
    """Orden de David (2026-10-20): revisa el revisor de la política (Claude); si no está
    disponible, cualquier otro agente que NO haya trabajado en ese bloque."""
    autores = bv.autores()
    vistos: list[str] = []
    for p in (*policy.revisores, *policy.implementadores):
        if p not in autores and p not in vistos:
            vistos.append(p)
    return tuple(vistos)


def _mismo_bloqueante_dos_veces(bv: BlockView) -> bool:
    textos = [tuple(sorted(b["texto"] for b in _bloqueantes(r))) for r in bv.reviews if not _aprobado(r)]
    return len(textos) >= 2 and textos[-1] == textos[-2] and bool(textos[-1])


def decide(snap: Snapshot, policy: Policy, now: float) -> list[Action]:
    acciones: list[Action] = []
    reservas: dict[str, int] = {}
    todos_hechos = True
    for bid, bv in snap.blocks.items():
        b = bv.block
        e = estado(snap, bid)
        if e is not Estado.HECHO:
            todos_hechos = False
        if e is Estado.HUMANO:
            if b.human and b.human.sonda:
                acciones.append(ProbeHuman(bid))
            key = f"humano:{bid}"
            if key not in bv.notices:
                acciones.append(Notify(key, bid, "humano", (("que_hacer", b.human.que_hacer),)))
        elif e is Estado.ARREGLAR:
            p = _elige(policy.implementadores, snap, policy, now, bv, "implementar", reservas)
            if p is None:
                acciones.extend(_sin_agente(snap, policy, now, bid, policy.implementadores, bv))
            else:
                reservas[p] = reservas.get(p, 0) + 1
                n = len(bv.implement_attempts()) + 1
                acciones.append(StartAttempt(bid, "implementar", p, n, bv.head(), _feedback(bv)))
        elif e is Estado.VERIFICAR:
            acciones.append(Verify(bid, bv.head()))
        elif e is Estado.REVISAR:
            escalera = revisores_para(bv, policy)
            p = _elige(escalera, snap, policy, now, bv, "revisar", reservas)
            if p is None:
                acciones.extend(_sin_agente(snap, policy, now, bid, escalera, bv))
            else:
                reservas[p] = reservas.get(p, 0) + 1
                n = len([a for a in bv.attempts if a.role == "revisar"]) + 1
                acciones.append(StartAttempt(bid, "revisar", p, n, bv.head(), ()))
        elif e is Estado.CI:
            acciones.append(RunCI(bid, bv.head()))
        elif e is Estado.MERGE:
            t = merge_gate(bv)
            if isinstance(t, MergeTicket):
                acciones.append(Merge(t))
        elif e is Estado.DESPLEGAR:
            acciones.append(Deploy(bid, bv.merged[-1]))
        elif e is Estado.PROBAR:
            acciones.append(Prove(bid, bv.merged[-1]))
        elif e is Estado.HECHO:
            key = f"entregado:{bid}"
            if key not in bv.notices and (b.rows or not b.human):
                hechos = sum(1 for x in snap.blocks if estado(snap, x) is Estado.HECHO)
                acciones.append(Notify(key, bid, "entregado", (
                    ("aviso", b.aviso), ("n", str(hechos)), ("total", str(len(snap.blocks))),
                    ("prueba", "pasó" if b.prueba_real else "sin prueba real declarada"))))
    if todos_hechos and f"terminado:{snap.plan.id}" not in snap.plan_notices:
        acciones.append(Notify(f"terminado:{snap.plan.id}", "", "terminado",
                               (("total", str(len(snap.blocks))),)))
    return acciones


def _sin_agente(snap: Snapshot, policy: Policy, now: float, bid: str,
                ladder: tuple[str, ...], bv: BlockView) -> list[Action]:
    """Nadie de la escalera puede tomar el bloque ahora.

    - Todos en cooldown: no es una pausa; el reloj los devuelve solos. Aviso solo si
      la espera pasa de `aviso_lento_s`.
    - Ocupados: el siguiente tick lo toma.
    - Todos agotaron sus intentos en este bloque: se escala al lead (único aviso);
      el resto del plan sigue.
    """
    hasta = [snap.cooldowns.get(p, 0) for p in ladder]
    if all(h > now for h in hasta):
        primero = min(hasta)
        if primero - now >= policy.aviso_lento_s:
            key = f"lento:{int(primero)}"
            if key not in snap.plan_notices:
                return [Notify(key, "", "lento", (("hasta", str(int(primero))),))]
        return []
    ocupados = _ocupados(snap)
    if any(ocupados.get(p, 0) >= policy.providers[p].max_parallel for p in ladder):
        return []
    key = f"atorado:{bid}"
    if key in bv.notices:
        return []
    return [Notify(key, bid, "atorado", (("bloque", bv.block.aviso),))]


# --------------------------------------------------------------------------- clasificación

_QUOTA = re.compile(r"\b429\b|rate.?limit|usage limit|quota|weekly limit|limit reached|too many requests", re.I)
_AUTH = re.compile(r"\b401\b|login required|not logged in|authentication failed|unauthori[sz]ed|token expired", re.I)
_TRANSIENT = re.compile(r"ECONNRESET|ETIMEDOUT|\b50[234]\b|overloaded|temporarily unavailable", re.I)
_RESET = re.compile(r"reset[s]?\s*(?:at|on)?\s*(\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?Z?)", re.I)


def classify(exit_code: int, timed_out: bool, no_progress: bool, tail: str) -> dict:
    """Qué pasó con un proceso de agente, sin leer su texto como verdad (salvo errores)."""
    if timed_out:
        return {"kind": "tiempo", "detalle": "se pasó del tiempo máximo"}
    if no_progress:
        return {"kind": "sin_avance", "detalle": "no mostró avance; se detuvo"}
    if exit_code != 0:
        if _QUOTA.search(tail):
            m = _RESET.search(tail)
            return {"kind": "cuota", "reset_at": m.group(1) if m else "", "detalle": tail[-300:]}
        if _AUTH.search(tail):
            return {"kind": "auth", "detalle": tail[-300:]}
        if _TRANSIENT.search(tail):
            return {"kind": "transitorio", "detalle": tail[-300:]}
        return {"kind": "falla", "detalle": tail[-300:]}
    return {"kind": "terminado", "detalle": ""}
