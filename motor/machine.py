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

from . import fallas, huella, relevo
from .conf import Conf
from .plan import Block, Plan, cambios_de
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
    cli: dict[str, str] = field(default_factory=dict)  # modelo -> CLI (qué login falta)

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
            cli={m: r.cli for m, r in conf.rutas.items()},
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


NO_ES_FALLA = (
    "entregado",
    "veredicto",
    "corte",
    "interrumpido",
    "bloqueado",
)  # un apagón, una orden del motor o lo que no es el código no son culpa del agente


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
    cambios: list[str] = field(
        default_factory=list
    )  # notas y cambios del plan que llegaron mientras corría: el motor lo detiene
    exento_maquina: bool = False  # terminó en sin_cambios justo tras una falla de máquina: no gasta escalera


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
    verify_started: set[str] = field(default_factory=set)
    prove_started: set[str] = field(default_factory=set)
    deploy_started: set[str] = field(default_factory=set)
    deploy_marcados: set[str] = field(default_factory=set)
    deployed: dict[str, dict] = field(default_factory=dict)
    proved: dict[str, dict] = field(default_factory=dict)
    human_done: bool = False
    notes: list[str] = field(default_factory=list)
    hecho_ts: float = (
        0.0  # último hecho nuevo (Verified, Proved, Deployed o nota del lead)
    )
    notices: set[str] = field(default_factory=set)
    listo_desde: dict[str, float] = field(
        default_factory=dict
    )  # sha -> cuándo se marcó listo
    rachas: dict[str, tuple[float, float]] = field(
        default_factory=dict
    )  # acción -> (desde, última falla): efectos que siguen fallando (red, GitHub, disco)
    esperando: dict[str, dict] = field(
        default_factory=dict
    )  # sha -> {"faltan", "github_termino", "desde"}: qué pruebas de GitHub se esperan
    obsoletos: set[str] = field(
        default_factory=set
    )  # SHAs cuya verificación ya no vale (cambió su Acepta o la máquina) y no se ha repetido
    checks_ts: dict[str, float] = field(default_factory=dict)
    verified_ts: dict[str, float] = field(default_factory=dict)
    nota_ts: float = 0.0
    pruebas_sin_marca: tuple[str, ...] = ()
    relanzadas_saltadas: set[str] = field(default_factory=set)
    politica_bloquea: dict[str, dict] = field(default_factory=dict)
    temporales: dict[str, tuple[float, float]] = field(
        default_factory=dict
    )  # "deployed:<sha>" | "proved:<sha>" -> (desde, último) de salidas 75 seguidas
    bloqueo: dict | None = (
        None  # {"motivo", "huella_maquina"}: el agente dijo que no es el código; espera nota, plan o máquina
    )
    reabierto: dict | None = (
        None  # {"version", "cambios", "agente", "desde"}: una versión nueva del plan tocó un bloque ya hecho
    )

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
        """Todo modelo que trabajó el código del bloque (aunque su intento terminara a medias). Uno que
        ni tocó la carpeta (sin lugar, sin credenciales, un CLI que no arrancó) no cuenta, salvo que
        haya entregado; un resultado sin `toco_carpeta` cuenta."""
        return {
            a.modelo
            for a in self.implement_attempts()
            if a.ended is None
            or a.ended.get("kind") == "entregado"
            or a.ended.get("toco_carpeta") is not False
        }

    def last_implement_outcome(self) -> dict | None:
        imp = self.implement_attempts()
        return imp[-1].ended if imp else None

    def entrega_ts(self, sha: str) -> float:
        return max(
            (
                a.ended_at
                for a in self.implement_attempts()
                if a.ended
                and a.ended.get("kind") == "entregado"
                and a.ended.get("sha") == sha
            ),
            default=0.0,
        )

    def review_for(self, sha: str) -> dict | None:
        rs = [r for r in self.reviews if r["sha"] == sha]
        return rs[-1] if rs else None

    def merged_sha(self) -> str:
        return self.merged[-1]["sha"] if self.merged else ""

    def reapertura(self) -> dict | None:
        """La reapertura por un cambio del plan sigue abierta hasta que un agente entregue algo nuevo."""
        r = self.reabierto
        if r is None:
            return None
        nuevas = self.implement_attempts()[r["desde"] :]
        if any(a.ended and a.ended.get("kind") == "entregado" for a in nuevas):
            return None
        return r


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
    version: int = 0  # la del plan vigente: el de la última entrega
    huerfanos: tuple = ()  # intentos en curso de bloques que la versión vigente ya no tiene


RESULTADOS = {"Verified": "verified", "Proved": "proved", "Deployed": "deployed"}


def fold(plan: Plan, events: list[Event], maquina: str | None = None) -> Snapshot:
    """`maquina`: la huella de la máquina que corre (huella.de_maquina). Sin ella, los resultados no se
    comparan contra la máquina (pruebas puras); con ella, uno medido en otra configuración se ignora."""
    blocks = {b.id: BlockView(block=b) for b in plan.blocks}
    resultados: list[tuple[BlockView, Event]] = []
    cooldowns: dict[str, float] = {}
    plan_notices: set[str] = set()
    repos: dict[str, dict] = {}
    inicio = events[0].ts if events else 0.0
    limpio = any(e.kind == "Limpiado" for e in events)
    version, cargas, previo = 0, 0, None
    huerfanos: dict[tuple[str, int, str], Attempt] = {}
    ultimos: dict[tuple[str, str], dict] = {}  # (bloque, campo) -> último resultado visto hasta aquí
    crudo_merged: dict[str, str] = {}  # bloque -> SHA del último Merged
    crudo_probados: dict[
        str, set[str]
    ] = {}  # bloque -> SHAs con Proved ok (sin mirar la huella)
    for e in events:
        d = e.data
        bv = blocks.get(e.block)
        if e.kind == "PlanLoaded":
            cargas += 1
            version = d.get("version", cargas)
            try:
                ahora = Plan.from_dict(d["plan"])
            except (KeyError, TypeError):
                continue  # una carga sin plan legible cuenta como versión, no como cambio
            for b in ahora.blocks:
                antes = previo.get(b.id) if previo else None
                if antes is None or b.id not in blocks:
                    continue
                todos = cambios_de(antes, b)
                cambios = [c for c in todos if c.tipo != "bloque"]
                hecho = crudo_merged.get(b.id, "") in crudo_probados.get(b.id, ())
                if cambios and hecho:
                    blocks[b.id].reabierto = {
                        "version": version,
                        "cambios": [c.texto for c in cambios],
                        "agente": any(c.tipo == "fila" for c in cambios),
                        "desde": len(blocks[b.id].implement_attempts()),
                    }
                if todos:
                    blocks[b.id].bloqueo = None  # el plan corregido destraba al bloque
                for a in blocks[b.id].attempts:
                    if a.role == "implementar" and a.ended is None and todos:
                        a.cambios += [f"el plan cambió: {c.texto}" for c in todos]
            previo = {b.id: b for b in ahora.blocks}
        elif bv is None and e.kind == "AttemptStarted":
            huerfanos[(e.block, d["n"], d["role"])] = Attempt(
                n=d["n"],
                role=d["role"],
                modelo=d["modelo"],
                started=e.ts,
                proc=d.get("proc") or {},
                dir=d["dir"],
                sha=d.get("sha", ""),
            )
        elif bv is None and e.kind == "AttemptEnded":
            huerfanos.pop((e.block, d["n"], d["role"]), None)
        elif e.kind == "Cooldown":
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
                    proc=d.get("proc") or {},
                    dir=d["dir"],
                    sha=d.get("sha", ""),
                )
            )
        elif e.kind == "AttemptEnded":
            for a in bv.attempts:
                if a.n == d["n"] and a.role == d["role"] and a.ended is None:
                    a.ended, a.ended_at = d["outcome"], e.ts
                    if (
                        a.role == "implementar"
                        and d["outcome"].get("kind") == "sin_cambios"
                    ):
                        head = bv.head()
                        v = ultimos.get((e.block, "verified"))
                        p = ultimos.get((e.block, "proved"))
                        dep = ultimos.get((e.block, "deployed"))
                        mg = crudo_merged.get(e.block, "")
                        a.exento_maquina = bool(
                            (v and v.get("sha") == head and _es_falla_maquina(v))
                            or (
                                mg
                                and mg == head
                                and p
                                and p.get("sha") == mg
                                and not p.get("ok")
                                and p.get("codigo") != 75
                            )
                            or (
                                mg
                                and mg == head
                                and dep
                                and dep.get("sha") == mg
                                and not dep.get("ok")
                                and dep.get("codigo") != 75
                            )
                        )
                    if a.role == "implementar" and d["outcome"].get(
                        "pruebas_sin_marca"
                    ):
                        bv.pruebas_sin_marca = tuple(d["outcome"]["pruebas_sin_marca"])
                    if a.role == "implementar":
                        bv.bloqueo = (
                            {
                                "motivo": d["outcome"].get("detalle", ""),
                                "huella_maquina": d["outcome"].get(
                                    "huella_maquina", ""
                                ),
                            }
                            if d["outcome"].get("kind") == "bloqueado"
                            else None
                        )
        elif e.kind in RESULTADOS:
            ultimos[(e.block, RESULTADOS[e.kind])] = d
            resultados.append((bv, e))
            if e.kind == "Proved" and d.get("ok"):
                crudo_probados.setdefault(e.block, set()).add(d["sha"])
        elif e.kind == "PrOpened":
            bv.pr = d
        elif e.kind == "Reviewed":
            bv.reviews.append(d)
        elif e.kind == "MarkedReady":
            bv.ready.add(d["sha"])
            bv.listo_desde.setdefault(d["sha"], e.ts)
        elif e.kind == "EffectFailed":
            bv.rachas[d["accion"]] = (d.get("desde", e.ts), e.ts)
        elif e.kind == "EffectOk":
            bv.rachas.pop(d["accion"], None)
        elif e.kind == "ChecksResult":
            bv.checks[d["sha"]] = d
            bv.checks_ts[d["sha"]] = e.ts
        elif e.kind == "ChecksRelanzados":
            if d.get("motivo") == "saltada":
                bv.relanzadas_saltadas.add(d["sha"])
        elif e.kind == "ChecksInvalidados":
            bv.checks.pop(d["sha"], None)
            bv.checks_ts.pop(d["sha"], None)
            bv.esperando.pop(d["sha"], None)
            previa = bv.politica_bloquea.get(d["sha"])
            if previa is None or previa.get("detalle") != d.get("detalle", ""):
                bv.politica_bloquea[d["sha"]] = {
                    "desde": e.ts,
                    "detalle": d.get("detalle", ""),
                }
        elif e.kind == "ChecksEsperando":
            bv.esperando[d["sha"]] = {**d, "desde": e.ts}
        elif e.kind == "Merged":
            crudo_merged[e.block] = d["sha"]
            bv.merged.append(d)
            bv.pr = None  # ese PR ya se usó: un arreglo posterior (p. ej. tras un despliegue fallido) va en uno nuevo
        elif e.kind == "PrCerrado":
            bv.pr = None
            sha = bv.head()  # el PR nuevo vuelve a pasar por "listo" y por las pruebas
            bv.ready.discard(sha)
            bv.listo_desde.pop(sha, None)
            bv.checks.pop(sha, None)
            bv.esperando.pop(sha, None)
        elif e.kind == "VerifyStarted":
            bv.verify_started.add(d["sha"])
        elif e.kind == "ProveStarted":
            bv.prove_started.add(d["sha"])
        elif e.kind == "DeployStarted":
            bv.deploy_started.add(d["sha"])
        elif e.kind == "HumanDone":
            bv.human_done = True
        elif e.kind == "Note":
            bv.notes.append(d["texto"])
            bv.hecho_ts = e.ts
            bv.nota_ts = e.ts
            for a in bv.attempts:
                if a.role == "implementar" and a.ended is None:
                    a.cambios.append(f"llegó una nota: {d['texto']}")
            bv.bloqueo = None  # una nota destraba al bloque que esperaba
    for bv in blocks.values():
        b = bv.bloqueo
        if b and maquina is not None and b["huella_maquina"] not in ("", maquina):
            bv.bloqueo = None  # la máquina corregida también
    viejos: dict[str, set[str]] = {}
    for bv, e in resultados:
        campo = RESULTADOS[e.kind]
        if huella.vigente(e.data, huella.insumo_de(campo, bv.block, repos), maquina):
            getattr(bv, campo)[e.data["sha"]] = e.data
            if campo == "verified":
                bv.verified_ts[e.data["sha"]] = e.ts
            bv.hecho_ts = max(bv.hecho_ts, e.ts)
            clave_t = f"{campo}:{e.data['sha']}"
            if e.data.get("codigo") == 75 and not e.data.get("ok"):
                desde = bv.temporales.get(clave_t, (e.ts, e.ts))[0]
                bv.temporales[clave_t] = (desde, e.ts)
            else:
                bv.temporales.pop(clave_t, None)
        elif campo == "verified":
            viejos.setdefault(bv.block.id, set()).add(e.data["sha"])
    for bid, shas in viejos.items():
        blocks[bid].obsoletos = shas - blocks[bid].verified.keys()
    return Snapshot(
        plan=plan,
        blocks=blocks,
        cooldowns=cooldowns,
        plan_notices=plan_notices,
        repos=repos,
        inicio=inicio,
        limpio=limpio,
        version=version,
        huerfanos=tuple((k, a) for k, a in huerfanos.items()),
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
    reap = bv.reapertura()
    pide_agente = bool(reap and reap["agente"])
    if (
        m
        and bv.proved.get(m, {}).get("ok")
        and m not in bv.obsoletos
        and bv.verified.get(m, {}).get("ok", True)
        and not pide_agente
        and (bv.reabierto is None or bv.head() == m)
    ):
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
    if pide_agente:
        return (
            Estado.ARREGLAR
        )  # el plan cambió lo que pide una fila: un agente tiene que tocarlo
    head = bv.head()
    if m and head == m:
        if head in bv.obsoletos:
            return (
                Estado.VERIFICAR
            )  # ya mergeada, pero su Acepta o la máquina cambiaron
        if not bv.verified.get(head, {}).get("ok", True):
            return (
                Estado.ARREGLAR
            )  # la nueva Acepta no pasa sobre lo que ya está mergeado
        if b.lane == "release" and not bv.deployed.get(m, {}).get("ok"):
            if m in bv.deployed and bv.deployed[m].get("codigo") != 75:
                return Estado.ARREGLAR
            return Estado.DESPLEGAR
        if m in bv.proved:
            return (
                Estado.PROBAR if bv.proved[m].get("codigo") == 75 else Estado.ARREGLAR
            )
        return Estado.PROBAR
    last = bv.last_implement_outcome()
    v = bv.verified.get(head) if head else None
    if not (v and v.get("ok")):
        if not head or (last and last.get("kind") != "entregado"):
            return Estado.ARREGLAR
    if v is None:
        return Estado.VERIFICAR
    if not v["ok"]:
        return Estado.ARREGLAR
    if bv.pr is None:
        return Estado.PR
    if b.lane == "fast":
        c = bv.checks.get(head)
        if c is not None and not c.get("green"):
            if bv.entrega_ts(head) > bv.checks_ts.get(head, 0.0):
                return Estado.MERGE  # reentrega del mismo SHA: se vuelve a intentar el merge
            return Estado.ARREGLAR  # el merge fue rechazado: fast salta los checks, no este arreglo
        return Estado.MERGE
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
        if bv.entrega_ts(head) > bv.checks_ts.get(head, 0.0):
            return (
                Estado.CHECKS
            )  # reentrega del mismo SHA: se vuelve a mirar antes de contar la falla
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
    correccion: bool  # bloque de bug o una revisión de este bloque no aprobada


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
class Interrumpe:
    """Detener un intento en curso porque lo que le pidieron ya no vale (el bloque salió del plan)."""

    block: str
    role: str
    n: int
    motivo: str


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
    | Interrumpe
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
    if bv.block.lane == "fast":
        c = bv.checks.get(head)
        if (
            c is not None
            and not c.get("green")
            and bv.entrega_ts(head) <= bv.checks_ts.get(head, 0.0)
        ):
            return "el merge fue rechazado sobre ese SHA"
        return MergeTicket(block=bv.block.id, sha=head, pr=int(bv.pr["numero"]))
    r = bv.review_for(head)
    if r is None or not _aprobado(r):
        return "sin veredicto APROBADO sobre ese SHA"
    if head not in bv.ready:
        return "el PR no está marcado listo sobre ese SHA"
    if not bv.checks.get(head, {}).get("green"):
        return "las pruebas automáticas no están en verde sobre ese SHA"
    return MergeTicket(block=bv.block.id, sha=head, pr=int(bv.pr["numero"]))


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
    return _sesion(snap, policy, cuenta, now)[0] is not None


def _sesion(
    snap: Snapshot, policy: Policy, cuenta: str, now: float
) -> tuple[float | None, float | None]:
    """Credenciales caídas = cuenta fuera una hora, y luego se prueba otra vez. Solo un éxito real la
    levanta; otra falla (salvo un freno corto) rearma el reloj. Todo sale del log (relevo.py).
    Devuelve (desde, racha): cuándo empezó la cuarentena vigente (None si no hay) y cuándo falló la
    primera sesión desde el último éxito, que no cambia mientras la sesión siga caída."""
    desde = racha = None
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
            desde = racha = None
        elif kind in NO_ES_FALLA:
            continue  # un corte de luz no rearma nada (revisión de Grok a MOTOR 01)
        elif falla is relevo.Falla.AUTH or (
            desde is not None and relevo.rearma_cuarentena(falla)
        ):
            desde = a.ended_at
            if falla is relevo.Falla.AUTH and racha is None:
                racha = a.ended_at
    if not relevo.cuarentena_activa(desde is not None, desde, now):
        return None, None
    return desde, racha


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


_PATRONES_MAQUINA = (
    "command not found",
    "is not recognized",
    "No module named",
    "ModuleNotFoundError",
    "cannot import name 'tomllib'",
    "Could not resolve host",
    "Permission denied (publickey)",
)

REVERIFICA_CADA_S = 600.0


def _es_falla_maquina(d: dict | None) -> bool:
    """El resultado falló porque no puede correr en esta máquina, no por el código."""
    if not d or d.get("ok"):
        return False
    if d.get("falta_comando"):
        return True
    if d.get("codigo") in (126, 127):
        return True
    detalle = str(d.get("detalle", ""))
    return any(p in detalle for p in _PATRONES_MAQUINA)


def _toca_reverificar(bv: BlockView, now: float) -> bool:
    """Mismo SHA, sin agente: al llegar una nota o cada 10 minutos si la previa ya puede pasar."""
    head = bv.head()
    v = bv.verified.get(head) if head else None
    if not _es_falla_maquina(v):
        return False
    ts = bv.verified_ts.get(head, 0.0)
    return bv.nota_ts > ts or (ts > 0 and now - ts >= REVERIFICA_CADA_S)


def _cuenta_falla(a: Attempt, bv: BlockView) -> bool:
    """¿Este intento terminado gasta la escalera del modelo? No: entregar, un apagón, lo que resuelve el
    reloj (cuota, freno, sesión), las fallas pasajeras (tienen su pausa) y un `sin_cambios` cuando el
    bloque falló por algo que el agente no controla (comando que falta, prueba real, despliegue)."""
    kind = a.ended.get("kind", "")
    if kind in ("entregado", "veredicto") or kind in NO_ES_FALLA:
        return False
    if (
        kind in relevo.PASAJERAS
        or relevo.desde_kind(kind) in relevo.LAS_RESUELVE_EL_RELOJ
    ):
        return False
    return not (
        kind == "sin_cambios" and (a.exento_maquina or _fuera_de_control(bv))
    )


def espera_hecho(bv: BlockView, role: str) -> bool:
    """El último intento del agente terminó en `sin_cambios` y el bloque sigue fallando por algo que
    él no controla: otro agente repetiría lo mismo, así que nadie toma el bloque hasta que llegue un
    hecho nuevo (Verified, Proved, Deployed o una nota del lead)."""
    mios = [a for a in bv.attempts if a.role == role]
    ult = mios[-1] if mios else None
    return bool(
        role == "implementar"
        and ult
        and ult.ended
        and ult.ended.get("kind") == "sin_cambios"
        and ult.ended_at > bv.hecho_ts
        and _fuera_de_control(bv)
    )


def _fuera_de_control(bv: BlockView) -> bool:
    head, merged = bv.head(), bv.merged_sha()
    return bool(
        _es_falla_maquina(bv.verified.get(head))
        or (
            merged == head
            and merged in bv.proved
            and not bv.proved[merged]["ok"]
            and bv.proved[merged].get("codigo") != 75
        )
        or (
            merged == head
            and merged in bv.deployed
            and not bv.deployed[merged]["ok"]
            and bv.deployed[merged].get("codigo") != 75
        )
    )


def faltan_comandos(bv: BlockView) -> tuple[str, list[str]]:
    """(qué paso, comandos) si el último paso del motor falló por un comando que esta máquina no tiene:
    la verificación de las filas, la prueba real o el despliegue. El agente no puede arreglarlo."""
    head, mg = bv.head(), bv.merged_sha()
    pasos = (
        ("la prueba real", bv.proved.get(mg) if mg and mg == head else None),
        ("el despliegue", bv.deployed.get(mg) if mg and mg == head else None),
        ("la verificación", bv.verified.get(head) if head else None),
    )
    for paso, r in pasos:
        if r and not r.get("ok") and r.get("falta_comando"):
            return paso, list(r["falta_comando"])
        if r and _es_falla_maquina(r):
            detalle = str(r.get("detalle", ""))
            encontrados = [
                x
                for x in ("tomllib",)
                if x in detalle
            ]
            return paso, encontrados or ["la máquina"]
    return "", []


def espera_cambio(bv: BlockView) -> bool:
    """El bloque no vuelve a un agente: lo que falla no es su código y solo cambia con otro plan o
    con otra máquina (F.1). El diagnóstico lo dice y avisa una vez."""
    return bool(faltan_comandos(bv)[1]) or bv.bloqueo is not None


def bloqueo(
    snap: Snapshot,
    policy: Policy,
    now: float,
    bv: BlockView,
    role: str,
    mo: str,
    ladder: tuple[str, ...],
) -> tuple[str, float]:
    """Por qué este modelo no puede tomar el bloque ahora, y hasta cuándo ('' si puede):
    fuera (espera un hecho nuevo), agotado (su escalera, sus rondas o el mismo bloqueante), cupo (pausa de cuota), sesion
    (cuarentena de credenciales) o pausa (falla pasajera reciente)."""
    if espera_hecho(bv, role):
        return "fuera", 0.0
    mios = [a for a in bv.attempts if a.role == role]
    ventana = []  # lo que pasó desde el último éxito
    for a in reversed(mios):
        if a.ended and a.ended.get("kind") in ("entregado", "veredicto"):
            break
        ventana.append(a)
    suyos = [a for a in reversed(ventana) if a.modelo == mo and a.ended]
    if sum(_cuenta_falla(a, bv) for a in suyos) >= policy.fallas_seguidas_max:
        return "agotado", 0.0
    if role == "implementar":
        rondas = sum(
            1
            for a in mios
            if a.modelo == mo and a.ended and a.ended.get("kind") == "entregado"
        )
        if rondas >= policy.rondas_max_por_modelo:
            return "agotado", 0.0
        if (
            _mismo_bloqueante_dos_veces(bv)
            and mios
            and mios[-1].modelo == mo
            and len(ladder) > 1
        ):
            return "agotado", 0.0
    cuenta = policy.cuenta.get(mo, mo)
    if snap.cooldowns.get(cuenta, 0) > now:
        return "cupo", snap.cooldowns[cuenta]
    desde, racha = _sesion(snap, policy, cuenta, now)
    if desde is not None:
        return "sesion", racha or desde
    seguidas = sum(a.ended.get("kind") in relevo.PASAJERAS for a in suyos)
    if suyos and suyos[-1].ended.get("kind") in relevo.PASAJERAS:
        hasta = suyos[-1].ended_at + relevo.pausa_pasajera_s(seguidas)
        if hasta > now:
            return "pausa", hasta
    return "", 0.0


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
        if bloqueo(snap, policy, now, bv, role, mo, ladder)[0]:
            continue
        if ocupados.get(cuenta, 0) >= policy.max_parallel.get(cuenta, 1):
            continue
        if (
            policy.agentes_por_maquina
            and sum(ocupados.values()) >= policy.agentes_por_maquina
        ):
            return None  # la máquina ya tiene su tope de agentes: espera turno
        if saltar_avanzo and _avanzo_de(mios, mo):
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


# Cómo termina un revisor sin que su prompt tenga la culpa: otro revisor con las mismas
# instrucciones no lo puede corregir, así que no se le cuenta.
FINES_AJENOS_AL_REVISOR = frozenset(
    {"cuota", "rate_limit", "auth", "corte", "perdido", "interrumpido"}
)


def _feedback_revisor(bv: BlockView) -> tuple[str, ...]:
    """Por qué terminó el revisor anterior, si no fue con veredicto: el siguiente lo sabe y lo corrige."""
    revs = [a for a in bv.attempts if a.role == "revisar"]
    if not revs or not revs[-1].ended:
        return ()
    o = revs[-1].ended
    if o.get("kind") == "veredicto" or o.get("kind") in FINES_AJENOS_AL_REVISOR:
        return ()
    return (
        f"El revisor anterior terminó por: {o.get('kind')} ({str(o.get('detalle', ''))[:300]}).",
    )


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
    from . import avisos, diagnostico  # se importan aquí: ambos dependen de este módulo

    policy = policy.para(snap.plan)
    acciones: list[Action] = []
    reservas: dict[str, int] = dict(externos or {})
    todos_hechos = True
    ya: set[str] = set()
    for bid, bv in snap.blocks.items():
        b = bv.block
        e = estado(snap, bid)
        if e is not Estado.HECHO:
            todos_hechos = False
        if e is Estado.HUMANO and b.human and b.human.sonda:
            acciones.append(
                ProbeHuman(bid)
            )  # antes del aviso: si ya lo hizo, no se molesta
        run = bv.running()
        if run and run.role == "implementar" and run.cambios:
            acciones.append(
                Interrumpe(bid, run.role, run.n, "; ".join(run.cambios)[:500])
            )
        d = diagnostico.diagnostico(snap, bid, policy, now)
        for n in avisos.avisos_de(snap, bid, policy, now, d):
            if n.key not in bv.notices | snap.plan_notices | ya:
                ya.add(n.key)
                acciones.append(n)
        if e is Estado.ARREGLAR and _toca_reverificar(bv, now):
            acciones.append(Verify(bid, bv.head()))
        elif e in (Estado.ARREGLAR, Estado.REVISAR) and not (
            e is Estado.ARREGLAR and espera_cambio(bv)
        ):
            role = "implementar" if e is Estado.ARREGLAR else "revisar"
            ladder = (
                policy.implementadores
                if role == "implementar"
                else revisores_para(bv, policy)
            )
            mo = _elige(ladder, snap, policy, now, bv, role, reservas)
            if mo is not None:
                c = policy.cuenta.get(mo, mo)
                reservas[c] = reservas.get(c, 0) + 1
                n = len([a for a in bv.attempts if a.role == role]) + 1
                fb = (
                    diagnostico.encargo(snap, bid, policy, now)
                    if role == "implementar"
                    else _feedback_revisor(bv)
                )
                # nunca del texto del feedback: un cambio de plan o una Acepta fallida también lo traen
                correccion = b.es_bug or any(not _aprobado(r) for r in bv.reviews)
                acciones.append(
                    StartAttempt(bid, role, mo, n, bv.head(), fb, correccion)
                )
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
            t = bv.temporales.get(f"deployed:{bv.merged_sha()}")
            if not (t and now - t[1] < 900):
                acciones.append(Deploy(bid, bv.merged_sha()))
        elif e is Estado.PROBAR:
            t = bv.temporales.get(f"proved:{bv.merged_sha()}")
            if not (t and now - t[1] < 900):
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
                            (
                                "aviso",
                                b.aviso
                                + (
                                    ". Agregó archivos de prueba sin la marca de pruebas"
                                    if bv.pruebas_sin_marca
                                    else ""
                                ),
                            ),
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
    for (bid, n, role), _ in snap.huerfanos:
        acciones.append(Interrumpe(bid, role, n, "el bloque salió del plan"))
    if not todos_hechos and snap.inicio and policy.avance_cada_s > 0:
        n = int((now - snap.inicio) // policy.avance_cada_s)
        if n >= 1 and f"avance:{n}" not in snap.plan_notices:
            acciones.append(
                Notify(f"avance:{n}", "", "avance", avisos._resumen(snap, now, policy))
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


# --------------------------------------------------------------------------- clasificación


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
