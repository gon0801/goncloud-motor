"""Lógica pura: eventos -> estado -> acciones. Sin procesos, sin red, sin reloj propio.

No existe un estado FALLADO ni PAUSADO: un fallo solo produce otra acción (reintento,
relevo, arreglo). La única espera es un paso humano declarado en el plan.

Flujo de un bloque:
  ARREGLAR/implementar -> VERIFICAR -> PR (borrador) -> REVISAR -> LISTO (ready) -> CHECKS
  -> MERGE -> [DESPLEGAR] -> PROBAR -> HECHO
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass, field

from . import fallas, relevo
from .conf import Conf
from .plan import Block, Plan
from .store import Event


# --------------------------------------------------------------------------- política


@dataclass(frozen=True)
class Policy:
    implementadores: tuple[str, ...]  # modelos, en orden de relevo
    revisores: tuple[str, ...]
    cuenta: dict[str, str]  # modelo -> cuenta (la cuota es por cuenta)
    max_parallel: dict[str, int]  # cuenta -> intentos simultáneos en esta máquina
    timeout_s: float = 4 * 3600
    sin_avance_s: float = 45 * 60
    wip_cada_s: float = 300
    fallas_seguidas_max: int = 2
    rondas_max_por_modelo: int = 5
    cooldown_default_s: float = 5 * 3600
    aviso_lento_s: float = 6 * 3600
    aviso_incoherente_s: float = (
        30 * 60
    )  # GitHub ya terminó y el motor sigue esperando una prueba
    repeticiones_max: int = (
        3  # la misma falla tantas veces seguidas: algo no cuadra, se avisa
    )
    agentes_por_maquina: int = 0  # tope de agentes a la vez en esta máquina, sumando todos los planes (0 = sin tope)
    avance_cada_s: float = (
        3600  # resumen a David mientras el plan corre (orden de David, 2026-10-20)
    )
    revision_fija: bool = (
        False  # el plan nombró a sus revisores: no se sale de esa lista
    )

    def para(self, plan: Plan) -> Policy:
        """La tabla, salvo lo que el plan pidió con 'implementa:' / 'revisa:'."""
        return dataclasses.replace(
            self,
            implementadores=plan.implementa or self.implementadores,
            revisores=plan.revisa or self.revisores,
            revision_fija=bool(plan.revisa),
        )

    @staticmethod
    def from_conf(conf: Conf) -> Policy:
        cuentas: dict[str, int] = {}
        for r in conf.rutas.values():
            cuentas[r.cuenta] = max(cuentas.get(r.cuenta, 0), r.max_parallel)
        t = conf.tiempos
        return Policy(
            implementadores=conf.escalera("implementar"),
            revisores=conf.escalera("revisar"),
            cuenta={m: r.cuenta for m, r in conf.rutas.items()},
            max_parallel=cuentas,
            timeout_s=t.get("timeout_s", 4 * 3600),
            sin_avance_s=t.get("sin_avance_s", 45 * 60),
            wip_cada_s=t.get("wip_cada_s", 300),
            cooldown_default_s=t.get("cooldown_default_s", 5 * 3600),
            aviso_lento_s=t.get("aviso_lento_s", 6 * 3600),
            aviso_incoherente_s=t.get("aviso_incoherente_s", 30 * 60),
            repeticiones_max=int(t.get("repeticiones_max", 3)),
            avance_cada_s=t.get("avance_cada_s", 3600),
            agentes_por_maquina=int(t.get("agentes_por_maquina", 0)),
        )


# --------------------------------------------------------------------------- vista


class Estado(enum.Enum):
    ESPERANDO = "esperando dependencias"
    HUMANO = "espera un paso de David"
    IMPLEMENTANDO = "implementando"
    REVISANDO = "revisando"
    ARREGLAR = "por arreglar"
    VERIFICAR = "por verificar"
    PR = "por abrir el PR"
    REVISAR = "por revisar"
    LISTO = "por marcar listo"
    CHECKS = "esperando pruebas automáticas"
    MERGE = "por mergear"
    DESPLEGAR = "por desplegar"
    PROBAR = "por probar con datos reales"
    HECHO = "hecho"


NO_ES_FALLA = ("entregado", "veredicto", "corte")  # un apagón no es culpa del agente


@dataclass
class Attempt:
    n: int
    role: str  # implementar | revisar
    modelo: str
    started: float
    proc: dict  # {"pid", "boot_id"}
    dir: str
    sha: str = ""  # SHA al empezar (implementar) o SHA revisado (revisar)
    ended: dict | None = None
    ended_at: float = (
        0.0  # cuándo terminó (del log): alimenta la cuarentena de la cuenta
    )


@dataclass
class BlockView:
    block: Block
    attempts: list[Attempt] = field(default_factory=list)
    verified: dict[str, dict] = field(default_factory=dict)
    pr: dict | None = None
    reviews: list[dict] = field(default_factory=list)
    ready: set[str] = field(default_factory=set)
    checks: dict[str, dict] = field(default_factory=dict)
    merged: list[dict] = field(default_factory=list)
    deploy_started: set[str] = field(default_factory=set)
    deployed: dict[str, dict] = field(default_factory=dict)
    proved: dict[str, dict] = field(default_factory=dict)
    human_done: bool = False
    notes: list[str] = field(default_factory=list)
    notices: set[str] = field(default_factory=set)
    listo_desde: dict[str, float] = field(
        default_factory=dict
    )  # sha -> cuándo se marcó listo
    fallas_efecto: list[float] = field(
        default_factory=list
    )  # cuándo falló un efecto (red, GitHub, disco)
    esperando: dict[str, dict] = field(
        default_factory=dict
    )  # sha -> {"faltan", "github_termino", "desde"}: qué pruebas de GitHub se esperan

    def implement_attempts(self) -> list[Attempt]:
        return [a for a in self.attempts if a.role == "implementar"]

    def running(self) -> Attempt | None:
        return next((a for a in self.attempts if a.ended is None), None)

    def head(self) -> str:
        for a in reversed(self.implement_attempts()):
            if a.ended and a.ended.get("kind") == "entregado":
                return a.ended["sha"]
        return ""

    def autores(self) -> set[str]:
        """Todo modelo que trabajó el código del bloque (aunque su intento terminara a medias)."""
        return {a.modelo for a in self.implement_attempts()}

    def last_implement_outcome(self) -> dict | None:
        imp = self.implement_attempts()
        return imp[-1].ended if imp else None

    def review_for(self, sha: str) -> dict | None:
        rs = [r for r in self.reviews if r["sha"] == sha]
        return rs[-1] if rs else None

    def merged_sha(self) -> str:
        return self.merged[-1]["sha"] if self.merged else ""


@dataclass
class Snapshot:
    plan: Plan
    blocks: dict[str, BlockView]
    cooldowns: dict[str, float]  # cuenta -> hasta (epoch)
    plan_notices: set[str]
    repos: dict[str, dict]  # repo -> .motor.toml fijado {"sha", "texto"}
    inicio: float = 0.0  # cuándo se cargó el plan (para el resumen de cada hora)
    intentos_fuera: tuple = ()  # intentos terminados de OTROS planes de la máquina (cuarentena por cuenta)
    limpio: bool = False  # ya se borraron sus ramas y copias de trabajo


def fold(plan: Plan, events: list[Event]) -> Snapshot:
    blocks = {b.id: BlockView(block=b) for b in plan.blocks}
    cooldowns: dict[str, float] = {}
    plan_notices: set[str] = set()
    repos: dict[str, dict] = {}
    inicio = events[0].ts if events else 0.0
    limpio = any(e.kind == "Limpiado" for e in events)
    for e in events:
        d = e.data
        bv = blocks.get(e.block)
        if e.kind == "Cooldown":
            cooldowns[d["cuenta"]] = max(cooldowns.get(d["cuenta"], 0), d["hasta"])
        elif e.kind == "RepoPinned":
            repos[d["repo"]] = d
        elif e.kind == "NoticeQueued":
            (bv.notices if bv else plan_notices).add(d["key"])
        elif bv is None:
            continue
        elif e.kind == "AttemptStarted":
            bv.attempts.append(
                Attempt(
                    n=d["n"],
                    role=d["role"],
                    modelo=d["modelo"],
                    started=e.ts,
                    proc=d["proc"],
                    dir=d["dir"],
                    sha=d.get("sha", ""),
                )
            )
        elif e.kind == "AttemptEnded":
            for a in bv.attempts:
                if a.n == d["n"] and a.role == d["role"] and a.ended is None:
                    a.ended, a.ended_at = d["outcome"], e.ts
        elif e.kind == "Verified":
            bv.verified[d["sha"]] = d
        elif e.kind == "PrOpened":
            bv.pr = d
        elif e.kind == "Reviewed":
            bv.reviews.append(d)
        elif e.kind == "MarkedReady":
            bv.ready.add(d["sha"])
            bv.listo_desde.setdefault(d["sha"], e.ts)
        elif e.kind == "EffectFailed":
            bv.fallas_efecto.append(e.ts)
        elif e.kind == "ChecksResult":
            bv.checks[d["sha"]] = d
        elif e.kind == "ChecksEsperando":
            bv.esperando[d["sha"]] = {**d, "desde": e.ts}
        elif e.kind == "Merged":
            bv.merged.append(d)
            bv.pr = None  # ese PR ya se usó: un arreglo posterior (p. ej. tras un despliegue fallido) va en uno nuevo
        elif e.kind == "PrCerrado":
            bv.pr = None
        elif e.kind == "DeployStarted":
            bv.deploy_started.add(d["sha"])
        elif e.kind == "Deployed":
            bv.deployed[d["sha"]] = d
        elif e.kind == "Proved":
            bv.proved[d["sha"]] = d
        elif e.kind == "HumanDone":
            bv.human_done = True
        elif e.kind == "Note":
            bv.notes.append(d["texto"])
    return Snapshot(
        plan=plan,
        blocks=blocks,
        cooldowns=cooldowns,
        plan_notices=plan_notices,
        repos=repos,
        inicio=inicio,
        limpio=limpio,
    )


# --------------------------------------------------------------------------- estado


def _bloqueantes(review: dict) -> list[dict]:
    """Regla 4 de quality-kit: solo bloquea lo que trae comando que lo reproduce."""
    return [b for b in review.get("bloqueantes", []) if b.get("repro", "").strip()]


def _aprobado(review: dict) -> bool:
    return review["veredicto"] == "APROBADO" or not _bloqueantes(review)


def estado(snap: Snapshot, bid: str) -> Estado:
    bv = snap.blocks[bid]
    b = bv.block
    m = bv.merged_sha()
    if m and bv.proved.get(m, {}).get("ok"):
        return Estado.HECHO
    if any(estado(snap, dep) is not Estado.HECHO for dep in b.depende):
        return (
            Estado.ESPERANDO
        )  # antes que el paso humano: no se le pide nada a David antes de tiempo
    if b.human and not bv.human_done:
        return Estado.HUMANO
    if b.human and not b.rows:
        return Estado.HECHO
    run = bv.running()
    if run:
        return Estado.IMPLEMENTANDO if run.role == "implementar" else Estado.REVISANDO
    head = bv.head()
    if m and head == m:
        if b.lane == "release" and not bv.deployed.get(m, {}).get("ok"):
            return Estado.ARREGLAR if m in bv.deployed else Estado.DESPLEGAR
        if m in bv.proved:
            return Estado.ARREGLAR
        return Estado.PROBAR
    last = bv.last_implement_outcome()
    if not head or (last and last.get("kind") != "entregado"):
        return Estado.ARREGLAR
    v = bv.verified.get(head)
    if v is None:
        return Estado.VERIFICAR
    if not v["ok"]:
        return Estado.ARREGLAR
    if bv.pr is None:
        return Estado.PR
    r = bv.review_for(head)
    if r is None:
        return Estado.REVISAR
    if not _aprobado(r):
        return Estado.ARREGLAR
    if head not in bv.ready:
        return Estado.LISTO
    c = bv.checks.get(head)
    if c is None:
        return Estado.CHECKS
    if not c["green"]:
        return Estado.ARREGLAR
    return Estado.MERGE


# --------------------------------------------------------------------------- acciones


@dataclass(frozen=True)
class StartAttempt:
    block: str
    role: str
    modelo: str
    n: int
    sha: str
    feedback: tuple[str, ...]


@dataclass(frozen=True)
class Verify:
    block: str
    sha: str


@dataclass(frozen=True)
class OpenPr:
    block: str
    sha: str


@dataclass(frozen=True)
class MarkReady:
    block: str
    sha: str


@dataclass(frozen=True)
class PollChecks:
    block: str
    sha: str


@dataclass(frozen=True)
class MergeTicket:
    """Solo `merge_gate` lo construye. Sin ticket no hay merge (effects lo exige por tipo)."""

    block: str
    sha: str
    pr: int


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


@dataclass(frozen=True)
class Limpia:
    """Plan terminado y avisado: borrar sus ramas en GitHub y sus copias de trabajo."""

    plan: str


Action = (
    StartAttempt
    | Verify
    | OpenPr
    | MarkReady
    | PollChecks
    | Merge
    | Deploy
    | Prove
    | ProbeHuman
    | Notify
    | Limpia
)


def merge_gate(bv: BlockView) -> MergeTicket | str:
    """El único camino a un MergeTicket: todo sobre el MISMO SHA de cabeza."""
    head = bv.head()
    if not head:
        return "sin entrega"
    if bv.running():
        return "hay un intento corriendo"
    if not bv.verified.get(head, {}).get("ok"):
        return "la entrega no está verificada"
    if bv.pr is None:
        return "sin PR"
    r = bv.review_for(head)
    if r is None or not _aprobado(r):
        return "sin veredicto APROBADO sobre ese SHA"
    if head not in bv.ready:
        return "el PR no está marcado listo sobre ese SHA"
    if not bv.checks.get(head, {}).get("green"):
        return "las pruebas automáticas no están en verde sobre ese SHA"
    return MergeTicket(block=bv.block.id, sha=head, pr=int(bv.pr["numero"]))


def _feedback(bv: BlockView) -> tuple[str, ...]:
    out: list[str] = []
    last = bv.last_implement_outcome()
    if last and last.get("kind") != "entregado":
        motivo = (
            "un corte de luz o reinicio"
            if last.get("kind") == "corte"
            else last.get("kind")
        )
        out.append(
            f"El intento anterior terminó por: {motivo} ({str(last.get('detalle', ''))[:300]}). "
            "Sigue desde lo que haya en la rama y lo que quedó sin commitear en esta carpeta."
        )
    head = bv.head()
    if head:
        v = bv.verified.get(head)
        if v and not v["ok"]:
            out.append(f"La verificación falló: {v.get('detalle', '')[:800]}")
        r = bv.review_for(head)
        if r and not _aprobado(r):
            for blq in _bloqueantes(r):
                out.append(
                    f"BLOQUEANTE: {blq['texto']} | repro: {blq['repro']}"
                    + (f" | arreglo: {blq['arreglo']}" if blq.get("arreglo") else "")
                )
        c = bv.checks.get(head)
        if c and not c["green"]:
            out.append(f"Pruebas automáticas en rojo: {c.get('detalle', '')[:800]}")
        m = bv.merged_sha()
        if m and m == head:
            if m in bv.deployed and not bv.deployed[m]["ok"]:
                out.append(
                    f"El despliegue falló y se revirtió: {bv.deployed[m].get('detalle', '')[:800]}"
                )
            if m in bv.proved and not bv.proved[m]["ok"]:
                out.append(
                    f"La prueba con datos reales falló: {bv.proved[m].get('detalle', '')[:800]}"
                )
    out.extend(f"Nota del lead: {n}" for n in bv.notes)
    return tuple(out)


def _ocupados(snap: Snapshot, policy: Policy) -> dict[str, int]:
    """Intentos corriendo por CUENTA (Sonnet y Opus comparten la de anthropic)."""
    n: dict[str, int] = {}
    for bv in snap.blocks.values():
        a = bv.running()
        if a:
            c = policy.cuenta.get(a.modelo, a.modelo)
            n[c] = n.get(c, 0) + 1
    return n


def revisores_para(bv: BlockView, policy: Policy) -> tuple[str, ...]:
    """Orden de David (2026-10-20): revisa el carril de revisión (Claude primero); si nadie de
    ahí puede, cualquier otro modelo de los carriles (Muse, Qwen o DeepSeek solo si el plan los pide
    en `revisa:`; revisión de Grok a MOTOR 01). Nunca alguien que trabajó en el bloque."""
    autores = bv.autores()
    vistos: list[str] = []
    candidatos = (
        policy.revisores
        if policy.revision_fija
        else (*policy.revisores, *policy.implementadores)
    )
    for mo in candidatos:
        if mo not in autores and mo not in vistos:
            vistos.append(mo)
    return tuple(vistos)


def _cuarentena(snap: Snapshot, policy: Policy, cuenta: str, now: float) -> bool:
    """Credenciales caídas = cuenta fuera una hora, y luego se prueba otra vez. Solo un éxito real la
    levanta; otra falla (salvo un freno corto) rearma el reloj. Todo sale del log (relevo.py)."""
    desde = None
    for a in sorted(
        (
            a
            for a in (
                *(a for bv in snap.blocks.values() for a in bv.attempts),
                *snap.intentos_fuera,
            )
            if a.ended and policy.cuenta.get(a.modelo, a.modelo) == cuenta
        ),
        key=lambda a: a.ended_at,
    ):
        kind = a.ended.get("kind", "")
        falla = relevo.desde_kind(kind)
        if kind in ("entregado", "veredicto"):
            desde = None
        elif kind in NO_ES_FALLA:
            continue  # un corte de luz no rearma nada (revisión de Grok a MOTOR 01)
        elif falla is relevo.Falla.AUTH or (
            desde is not None and relevo.rearma_cuarentena(falla)
        ):
            desde = a.ended_at
    return relevo.cuarentena_activa(desde is not None, desde, now)


def _avanzo_de(mios: list[Attempt], mo: str) -> bool:
    """Su último intento de este papel fue de este modelo y terminó en una falla que, por regla,
    pasa el encargo al siguiente (no a reintentar con el mismo). Cuota, freno y credenciales no:
    de esas se encarga el reloj (pausa o cuarentena de la cuenta)."""
    if not mios or mios[-1].modelo != mo or not mios[-1].ended:
        return False
    falla = relevo.desde_kind(mios[-1].ended.get("kind", ""))
    return (
        falla is not None
        and falla not in relevo.LAS_RESUELVE_EL_RELOJ
        and relevo.avanza(falla)
    )


def _elige(
    ladder: tuple[str, ...],
    snap: Snapshot,
    policy: Policy,
    now: float,
    bv: BlockView,
    role: str,
    reservas: dict[str, int],
) -> str | None:
    ocupados = _ocupados(snap, policy)
    for k, v in reservas.items():
        ocupados[k] = ocupados.get(k, 0) + v
    mios = [a for a in bv.attempts if a.role == role]
    # Primera pasada: salta al que acaba de fallar si otro puede correr; si nadie, lo admite.
    for saltar in (True, False):
        mo = _elige_pasada(ladder, snap, policy, now, bv, role, ocupados, mios, saltar)
        if mo is not None:
            return mo
    return None


def _elige_pasada(
    ladder: tuple[str, ...],
    snap: Snapshot,
    policy: Policy,
    now: float,
    bv: BlockView,
    role: str,
    ocupados: dict[str, int],
    mios: list[Attempt],
    saltar_avanzo: bool,
) -> str | None:
    for mo in ladder:
        cuenta = policy.cuenta.get(mo, mo)
        if snap.cooldowns.get(cuenta, 0) > now or _cuarentena(
            snap, policy, cuenta, now
        ):
            continue
        if ocupados.get(cuenta, 0) >= policy.max_parallel.get(cuenta, 1):
            continue
        if (
            policy.agentes_por_maquina
            and sum(ocupados.values()) >= policy.agentes_por_maquina
        ):
            return None  # la máquina ya tiene su tope de agentes: espera turno
        fallas = 0
        for a in reversed(mios):
            if a.ended and a.ended.get("kind") in ("entregado", "veredicto"):
                break
            if a.modelo == mo and a.ended and a.ended.get("kind") not in NO_ES_FALLA:
                if (
                    relevo.desde_kind(a.ended.get("kind", ""))
                    not in relevo.LAS_RESUELVE_EL_RELOJ
                ):
                    fallas += 1
        if fallas >= policy.fallas_seguidas_max:
            continue
        if saltar_avanzo and _avanzo_de(mios, mo):
            continue
        if role == "implementar":
            rondas = sum(
                1
                for a in mios
                if a.modelo == mo and a.ended and a.ended.get("kind") == "entregado"
            )
            if rondas >= policy.rondas_max_por_modelo:
                continue
            if (
                _mismo_bloqueante_dos_veces(bv)
                and mios
                and mios[-1].modelo == mo
                and len(ladder) > 1
            ):
                continue
        return mo
    return None


def _mismo_bloqueante_dos_veces(bv: BlockView) -> bool:
    textos = [
        tuple(sorted(b["texto"] for b in _bloqueantes(r)))
        for r in bv.reviews
        if not _aprobado(r)
    ]
    return len(textos) >= 2 and textos[-1] == textos[-2] and bool(textos[-1])


def decide(
    snap: Snapshot,
    policy: Policy,
    now: float,
    externos: dict[str, int] | None = None,
    cooldowns_fuera: dict[str, float] | None = None,
    intentos_fuera: tuple = (),
) -> list[Action]:
    """`externos`: agentes corriendo en OTROS planes de esta máquina, por cuenta. Los topes son por
    máquina, no por plan (con dos planes en la Mini, cada uno creía libre la cuenta de Claude).
    `cooldowns_fuera` e `intentos_fuera`: la cuota y las credenciales también son de la cuenta, no
    del plan; si otro plan ya la dejó sin cupo o en cuarentena, este tampoco la usa."""
    if cooldowns_fuera or intentos_fuera:
        cd = dict(snap.cooldowns)
        for c, h in (cooldowns_fuera or {}).items():
            cd[c] = max(cd.get(c, 0), h)
        snap = dataclasses.replace(
            snap, cooldowns=cd, intentos_fuera=tuple(intentos_fuera)
        )
    policy = policy.para(snap.plan)
    acciones: list[Action] = []
    reservas: dict[str, int] = dict(externos or {})
    todos_hechos = True
    for bid, bv in snap.blocks.items():
        b = bv.block
        e = estado(snap, bid)
        if e is not Estado.HECHO:
            todos_hechos = False
        atasco = _sin_avance_propio(bv, e, policy, now)
        if atasco and atasco[0] not in bv.notices:
            acciones.append(
                Notify(
                    atasco[0],
                    bid,
                    "atorado",
                    (("bloque", b.aviso), ("motivo", atasco[1])),
                )
            )
        if e is Estado.HUMANO:
            if b.human and b.human.sonda:
                acciones.append(ProbeHuman(bid))
            if f"humano:{bid}" not in bv.notices:
                acciones.append(
                    Notify(
                        f"humano:{bid}",
                        bid,
                        "humano",
                        (("que_hacer", b.human.que_hacer),),
                    )
                )
        elif e in (Estado.ARREGLAR, Estado.REVISAR):
            role = "implementar" if e is Estado.ARREGLAR else "revisar"
            ladder = (
                policy.implementadores
                if role == "implementar"
                else revisores_para(bv, policy)
            )
            mo = _elige(ladder, snap, policy, now, bv, role, reservas)
            if mo is None:
                acciones.extend(_sin_agente(snap, policy, now, ladder, bv, reservas))
            else:
                c = policy.cuenta.get(mo, mo)
                reservas[c] = reservas.get(c, 0) + 1
                n = len([a for a in bv.attempts if a.role == role]) + 1
                fb = _feedback(bv) if role == "implementar" else ()
                acciones.append(StartAttempt(bid, role, mo, n, bv.head(), fb))
        elif e is Estado.VERIFICAR:
            acciones.append(Verify(bid, bv.head()))
        elif e is Estado.PR:
            acciones.append(OpenPr(bid, bv.head()))
        elif e is Estado.LISTO:
            acciones.append(MarkReady(bid, bv.head()))
        elif e is Estado.CHECKS:
            acciones.append(PollChecks(bid, bv.head()))
        elif e is Estado.MERGE:
            t = merge_gate(bv)
            if isinstance(t, MergeTicket):
                acciones.append(Merge(t))
        elif e is Estado.DESPLEGAR:
            acciones.append(Deploy(bid, bv.merged_sha()))
        elif e is Estado.PROBAR:
            acciones.append(Prove(bid, bv.merged_sha()))
        elif e is Estado.HECHO:
            key = f"entregado:{bid}"
            if key not in bv.notices and (b.rows or not b.human):
                hechos = sum(1 for x in snap.blocks if estado(snap, x) is Estado.HECHO)
                acciones.append(
                    Notify(
                        key,
                        bid,
                        "entregado",
                        (
                            ("aviso", b.aviso),
                            ("n", str(hechos)),
                            ("total", str(len(snap.blocks))),
                            (
                                "prueba",
                                "pasó"
                                if b.prueba_real
                                else "sin prueba real declarada",
                            ),
                        ),
                    )
                )
    if not todos_hechos and snap.inicio and policy.avance_cada_s > 0:
        n = int((now - snap.inicio) // policy.avance_cada_s)
        if n >= 1 and f"avance:{n}" not in snap.plan_notices:
            acciones.append(
                Notify(f"avance:{n}", "", "avance", _resumen(snap, now, policy))
            )
    if (
        todos_hechos
        and f"terminado:{snap.plan.id}" in snap.plan_notices
        and not snap.limpio
    ):
        acciones.append(Limpia(snap.plan.id))
    if todos_hechos and f"terminado:{snap.plan.id}" not in snap.plan_notices:
        acciones.append(
            Notify(
                f"terminado:{snap.plan.id}",
                "",
                "terminado",
                (("total", str(len(snap.blocks))),),
            )
        )
    return acciones


NOMBRES = {
    "sonnet": "Sonnet",
    "opus": "Opus",
    "k3": "Kimi",
    "glm": "GLM",
    "grok": "Grok",
    "qwen": "Qwen",
    "deepseek": "DeepSeek",
    "muse": "Muse",
    "haiku": "Haiku",
    "fable": "Fable",
}


def _resumen(snap: Snapshot, now: float, policy: Policy) -> tuple[tuple[str, str], ...]:
    """El latido de cada hora: cuántas partes van, qué se está haciendo y si algo está atorado.
    Si deja de llegar, David sabe que el motor o la máquina se cayeron."""
    estados = {bid: estado(snap, bid) for bid in snap.blocks}
    listas = sum(e is Estado.HECHO for e in estados.values())
    partes = []
    for bid, bv in snap.blocks.items():
        if estados[bid] in (Estado.HECHO, Estado.ESPERANDO):
            continue
        run = bv.running()
        if run:
            que = "reviso" if run.role == "revisar" else "trabajo en"
            frase = (
                f"{que} «{bv.block.titulo}» ({NOMBRES.get(run.modelo, run.modelo.capitalize())}, "
                f"{_dura(now - run.started)})"
            )
            falla = _ultima_falla(bv) if run.role == "implementar" else ""
            partes.append(frase + (f" porque {falla}" if falla else ""))
        else:
            partes.append(f"«{bv.block.titulo}»: {motivo(snap, bid, now)}")
    if partes:
        ahora = "Ahora " + "; ".join(partes) + "."
    else:
        ahora = "Ahora estoy entre un paso y otro."
    atorados = []
    for bid, bv in snap.blocks.items():
        causa = (
            None
            if estados[bid] is Estado.HECHO
            else _sin_avance_propio(bv, estados[bid], policy, now)
        )
        if causa:
            atorados.append(f"«{bv.block.titulo}» ({causa[1]})")
    atoron = ("Atorado: " + "; ".join(atorados) + ".") if atorados else "Nada atorado."
    return (
        ("listas", str(listas)),
        ("total", str(len(snap.blocks))),
        ("ahora", ahora),
        ("atoron", atoron),
    )


def _sin_avance_propio(
    bv: BlockView, e: Estado, policy: Policy, now: float
) -> tuple[str, str] | None:
    """Atascos que no dependen de un agente, como (clave del aviso, motivo para David). Se avisa una
    vez por causa y el motor sigue intentando:
    - GitHub ya terminó todas sus pruebas y falta una requerida (INGESTA 01 esperó horas así);
    - pruebas que no terminan nunca (un nombre que no existe);
    - la misma falla varias veces seguidas (la prueba real de una sonda que solo corre de noche);
    - un efecto que lleva una hora fallando (red, GitHub, disco)."""
    bid = bv.block.id
    if e is Estado.CHECKS:
        head = bv.head()
        esp = bv.esperando.get(head) or {}
        if (
            esp.get("github_termino")
            and esp.get("faltan")
            and now - esp["desde"] > policy.aviso_incoherente_s
        ):
            return (
                f"atorado:{bid}:incoherente:{head[:12]}",
                (
                    f"GitHub ya terminó sus pruebas, pero sigo esperando {_lista(esp['faltan'])}, "
                    f"que no llegó a correr ({_dura(now - esp['desde'])})"
                ),
            )
        desde = bv.listo_desde.get(head)
        if desde is not None and now - desde > policy.aviso_lento_s:
            return (
                f"atorado:{bid}",
                f"las pruebas de GitHub llevan {_dura(now - desde)} sin terminar",
            )
    head = bv.head()
    falta = (bv.verified.get(head) or {}).get("falta_comando") if head else None
    if falta:
        # El agente no puede arreglar la máquina ni la Acepta del plan: rebotaría sin fin.
        return (
            f"atorado:{bid}:comando:{','.join(falta)}",  # uno por comando, no uno por ronda
            (
                f"la verificación del plan usa {', '.join(falta)}, que esta máquina no tiene; "
                "hay que corregir el plan o instalarlo en la máquina"
            ),
        )
    for tipo, d, campo, que in (
        ("verificacion", bv.verified, "ok", "falló la verificación de las filas"),
        ("prueba", bv.proved, "ok", "falló la prueba con datos reales"),
        ("despliegue", bv.deployed, "ok", "falló el despliegue"),
        ("checks", bv.checks, "green", "las pruebas de GitHub salieron en rojo"),
    ):
        n = _seguidas(d, campo)
        # Solo mientras no haya un arreglo nuevo: con otra entrega en camino ya no es atasco.
        if n >= policy.repeticiones_max and list(d)[-1] == head:
            antes = (
                len(d) - n
            )  # fija la clave mientras dura la racha: un aviso por racha
            return (
                f"atorado:{bid}:repite:{tipo}:{antes}",
                f"{que}, {n} veces seguidas",
            )
    racha = _racha(bv.fallas_efecto)
    if racha and now - racha[1] < 15 * 60 and racha[1] - racha[0] >= 3600:
        return (
            f"atorado:{bid}:efecto:{int(racha[0])}",
            "un paso del motor lleva una hora fallando (red o GitHub); sigo reintentando",
        )
    return None


def _racha(fallas: list[float], hueco: float = 15 * 60) -> tuple[float, float] | None:
    """(inicio, fin) de las últimas fallas seguidas, sin huecos de más de `hueco` entre ellas. Dos
    fallas sueltas separadas por una hora no son "una hora fallando" (PSTACK 01 en la PC)."""
    if not fallas:
        return None
    ini = fallas[-1]
    for t in reversed(fallas[:-1]):
        if ini - t > hueco:
            break
        ini = t
    return ini, fallas[-1]


def _seguidas(d: dict[str, dict], campo: str) -> int:
    n = 0
    for v in reversed(list(d.values())):
        if v.get(campo):
            break
        n += 1
    return n


def _dura(seg: float) -> str:
    m = int(seg // 60)
    if m < 60:
        return f"{max(m, 1)} min"
    h, m = divmod(m, 60)
    return f"{h} h" + (f" {m} min" if m else "")


def _lista(nombres) -> str:
    q = [f"la prueba «{n}»" for n in nombres]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " y " + q[-1]


def _ultima_falla(bv: BlockView) -> str:
    """Por qué el bloque volvió a un agente, en palabras para David (sin rutas ni detalles técnicos:
    el detalle completo va en el encargo del agente y en `motor estado`)."""
    head, m = bv.head(), bv.merged_sha()
    if m and m == head:
        if m in bv.proved and not bv.proved[m]["ok"]:
            return "falló la prueba con datos reales"
        if m in bv.deployed and not bv.deployed[m]["ok"]:
            return "falló el despliegue y se revirtió"
    c = bv.checks.get(head)
    if c and not c["green"]:
        if str(c.get("detalle", "")).startswith("el merge falló"):
            return "GitHub no dejó integrarlo; hay que traer lo último de la rama principal"
        return "las pruebas automáticas de GitHub salieron en rojo"
    r = bv.review_for(head) if head else None
    if r and not _aprobado(r):
        return "el revisor pidió cambios"
    v = bv.verified.get(head) if head else None
    if v and not v["ok"]:
        return "falló la verificación de las filas"
    last = bv.last_implement_outcome()
    if last and last.get("kind") == "corte":
        return "el intento anterior se cortó por un reinicio o apagón"
    if last and last.get("kind") != "entregado":
        return "el intento anterior terminó sin entregar"
    return ""


def motivo(snap: Snapshot, bid: str, now: float) -> str:
    """Qué hace o qué espera una parte, en una frase para David."""
    bv, e = snap.blocks[bid], estado(snap, bid)
    head = bv.head()
    if e is Estado.CHECKS:
        faltan = (bv.esperando.get(head) or {}).get("faltan")
        desde = bv.listo_desde.get(head, now)
        que = _lista(faltan) + " de GitHub" if faltan else "las pruebas de GitHub"
        return f"espero {que} desde hace {_dura(now - desde)}"
    if e in (Estado.ARREGLAR, Estado.IMPLEMENTANDO):
        falla = _ultima_falla(bv)
        return f"toca arreglarla porque {falla}" if falla else "toca hacerla"
    return {
        Estado.ESPERANDO: "espera a que terminen las partes de las que depende",
        Estado.HUMANO: "espera tu paso",
        Estado.REVISAR: "toca revisarla",
        Estado.REVISANDO: "la están revisando",
        Estado.VERIFICAR: "la verifico",
        Estado.PR: "abro su propuesta de cambio en GitHub",
        Estado.LISTO: "la marco lista en GitHub",
        Estado.MERGE: "la integro",
        Estado.DESPLEGAR: "la despliego a producción",
        Estado.PROBAR: "la pruebo con datos reales",
        Estado.HECHO: "terminada",
    }.get(e, e.value)


def _sin_agente(
    snap: Snapshot,
    policy: Policy,
    now: float,
    ladder: tuple[str, ...],
    bv: BlockView,
    reservas: dict[str, int] | None = None,
) -> list[Action]:
    """Nadie de la escalera puede ahora. En cuota: el reloj los devuelve solos (aviso solo si
    tarda más de `aviso_lento_s`). Ocupados: el siguiente tick. Agotados: un aviso y sigue el resto."""
    hasta = [snap.cooldowns.get(policy.cuenta.get(m, m), 0) for m in ladder]
    if hasta and all(h > now for h in hasta):
        primero = min(hasta)
        if (
            primero - now >= policy.aviso_lento_s
            and f"lento:{int(primero)}" not in snap.plan_notices
        ):
            return [
                Notify(
                    f"lento:{int(primero)}",
                    "",
                    "lento",
                    (("hasta", str(int(primero))),),
                )
            ]
        return []
    if any(
        snap.cooldowns.get(policy.cuenta.get(m, m), 0) > now
        or _cuarentena(snap, policy, policy.cuenta.get(m, m), now)
        for m in ladder
    ):
        return []  # una cuenta de la escalera vuelve sola con el reloj: es espera, no atoro
    ocupados = _ocupados(snap, policy)
    for k, v in (reservas or {}).items():
        ocupados[k] = ocupados.get(k, 0) + v
    if (
        policy.agentes_por_maquina
        and sum(ocupados.values()) >= policy.agentes_por_maquina
    ):
        return []  # todos los lugares de la máquina están ocupados: es turno, no atoro
    if any(
        ocupados.get(policy.cuenta.get(m, m), 0)
        >= policy.max_parallel.get(policy.cuenta.get(m, m), 1)
        for m in ladder
    ):
        return []
    if f"atorado:{bv.block.id}" in bv.notices:
        return []
    return [
        Notify(
            f"atorado:{bv.block.id}",
            bv.block.id,
            "atorado",
            (
                ("bloque", bv.block.aviso),
                ("motivo", "ningún agente disponible pudo tomarla"),
            ),
        )
    ]


# --------------------------------------------------------------------------- clasificación

retry_after_s = fallas.retry_after_s


def classify(
    exit_code: int, timed_out: bool, no_progress: bool, tail: str, proveedor: str = ""
) -> dict:
    """Qué le pasó a un intento. El tiempo agotado y la falta de avance salen del envoltorio; el resto
    se lee de la salida con las reglas de ese proveedor (fallas.py)."""
    if timed_out:
        return {"kind": "tiempo", "detalle": "se pasó del tiempo máximo"}
    if no_progress:
        return {"kind": "sin_avance", "detalle": "no mostró avance; se detuvo"}
    if exit_code != 0:
        return fallas.clasifica(proveedor, tail)
    return {"kind": "terminado", "detalle": ""}
