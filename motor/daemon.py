"""El programa siempre encendido de cada máquina. Su tick no usa ningún modelo.

    tick = conf -> ingerir (inbox + intentos terminados) -> fold -> decide -> ejecutar -> avisos

Cada máquina tiene el suyo y solo lleva los planes que se le entregaron ahí. Si muere o la
máquina se apaga, el arranque siguiente reconstruye todo del log: cada efecto revisa si ya
existe antes de repetirse, y los agentes corren aparte (runwrap).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import claves, huella, llamadas
from . import machine as m
from . import plataforma, version
from .conf import (
    RepoConf,
    load_conf,
    load_maquina,
    motivo_requiere,
    requisitos_faltantes,
)
from .diagnostico import HUECO_EFECTO_S
from .effects import Contexto, Effects
from .github import para
from .notify import Notifier, render
from .plan import Comando, Plan, PlanError, modelos_desconocidos, revisa_sin_quien
from .store import Store
from .version import SALIDA_RELANZAR, info, pide_otra
from .workspace import Workspace, git


ESPERA_TRAS_FALLA_S = 300


def _bloque(a: m.Action) -> str:
    return getattr(a, "block", "") or getattr(getattr(a, "ticket", None), "block", "")


def _huella(plan: dict) -> str:
    return hashlib.sha256(
        json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


class OtroMotor(RuntimeError):
    """Ya hay un daemon sobre este home."""


def planes_de(store: Store) -> list[Plan]:
    ultima_limpieza: dict[str, int] = {}
    for e in store.events(kind="Limpiado"):
        ultima_limpieza[e.plan] = e.id
    vistos: dict[str, Plan] = {}
    ultima_carga: dict[str, int] = {}
    for e in store.events(kind="PlanLoaded"):
        ultima_carga[e.plan] = e.id
        vistos[e.plan] = Plan.from_dict(e.data["plan"])
    return [
        p
        for pid, p in vistos.items()
        if ultima_limpieza.get(pid, 0) <= ultima_carga.get(pid, 0)
    ]


class Daemon:
    def __init__(self, home: Path, checks_cada_s: float | None = None):
        self.home = home
        # un solo escritor de la base por home: dos daemons duplicarían lanzamientos y efectos
        self._candado = plataforma.candado(home / "motor.lock")
        if self._candado is None:
            raise OtroMotor(
                f"ya hay un motor corriendo sobre {home} (candado {home / 'motor.lock'}); "
                "un segundo no puede arrancar"
            )
        self.maquina = load_maquina(home)
        # El env de la máquina vale también para el propio motor (git, verificar, desplegar), no solo
        # para los agentes. MOTOR 01: la Mini entra a GitHub por el alias github-claw y su motor no
        # podía clonar; su maquina.toml lo resuelve con GIT_CONFIG_* (url.<alias>.insteadOf).
        os.environ.update(dict(self.maquina.env))
        # Igual con path_extra: el motor también usa programas de la máquina (gh, git). MOTOR 01: en
        # la Mini `gh` solo vive en ~/.openclaw/tools/node/bin y el motor no podía abrir PRs.
        if self.maquina.path_extra:
            os.environ["PATH"] = os.pathsep.join(
                [*self.maquina.path_extra, os.environ.get("PATH", "")]
            )
        self.store = Store(home / "motor.db")
        conf = load_conf(self.maquina.conf)
        av = conf.avisos
        self.cx = Contexto(
            home=home,
            maquina=self.maquina,
            conf=conf,
            policy=m.Policy.from_conf(conf),
            store=self.store,
            ws=Workspace(home, self.maquina.nombre),
            gh=para(self.maquina.github, dict(self.maquina.secretos)),
            notifier=Notifier(
                home,
                av.get("base_url", "https://api.telegram.org"),
                dict(self.maquina.secretos).get(
                    av.get("token_env", "MOTOR_TELEGRAM_TOKEN"), ""
                ),
                av.get("chat", ""),
            ),
            checks_cada_s=checks_cada_s
            if checks_cada_s is not None
            else conf.tiempos.get("checks_cada_s", 60.0),
        )
        self.fx = Effects(self.cx)
        self._sha_arranque = version.sha_actual()
        self._sin_dormir: bool | None = (
            None  # None: aplicar en el primer tick (también tras un reinicio)
        )
        self._sin_dormir_reintento = 0.0
        self._espera_efecto: dict[
            tuple[str, str, str], float
        ] = {}  # efecto que falló -> no antes de
        self._fallas_n: dict[
            tuple[str, str, str], int
        ] = {}  # fallas ya anotadas por (plan, bloque, acción): numera su clave
        self._rachas = self._rachas_abiertas()

    def trae_conf(self, now: float) -> None:
        """La conf compartida llega sola: cada `conf_cada_s` (15 min) se trae de su repo. Solo se
        aplica si pasa `motor conf valida`; si no, se queda la anterior y se avisa una vez.
        Así agregar, quitar o reemplazar un modelo es un cambio en ese repo y nada más."""
        if now < getattr(self, "_proxima_conf", 0):
            return
        self._proxima_conf = now + float(self.cx.conf.tiempos.get("conf_cada_s", 900))
        d = self.maquina.conf
        if (
            not (d / ".git").exists()
            or git("fetch", "-q", "origin", cwd=d).returncode != 0
        ):
            return
        rama = git("rev-parse", "--abbrev-ref", "HEAD", cwd=d).stdout.strip()
        antes = git("rev-parse", "HEAD", cwd=d).stdout.strip()
        nueva = git(
            "rev-parse", "-q", "--verify", f"origin/{rama}", cwd=d
        ).stdout.strip()
        if (
            not nueva
            or nueva == antes
            or git("merge-base", "--is-ancestor", nueva, antes, cwd=d).returncode == 0
        ):
            return
        if git("merge", "-q", "--ff-only", f"origin/{rama}", cwd=d).returncode != 0:
            return  # la copia de esta máquina tiene cambios propios: no se pisa
        from .__main__ import conf_valida

        errores = conf_valida(d)
        if errores:
            git("reset", "-q", "--hard", antes, cwd=d)
            if self.store.append(
                "",
                "",
                "ConfRechazada",
                {"sha": nueva, "errores": errores[:5]},
                ukey=claves.conf_rechazada(nueva),
            ):
                self.cx.notifier.encola(
                    f"confmala:{nueva[:12]}",
                    render("conf_mala", {}, self.maquina.nombre, "Motor de planes"),
                )

    def recargar_conf(self) -> None:
        conf = load_conf(self.maquina.conf)
        if conf.sha != self.cx.conf.sha:
            self.cx.conf, self.cx.policy = conf, m.Policy.from_conf(conf)

    def pide_otra_version(self) -> str:
        """Si la conf fija una versión o un commit distinto del que corre, la frase de la diferencia."""
        return pide_otra(self.cx.conf.motor, info())

    def avisa_version_faltante(self) -> None:
        """Si la conf fija una versión que no está instalada en disco, no hay nada que relanzar: se
        avisa una vez y el motor sigue con la que tiene."""
        if not pide_otra(self.cx.conf.motor, version.instalada()):
            return
        fijada = self.cx.conf.motor
        pedida = fijada.get("version") or fijada.get("sha", "")[:12]
        if self.store.append(
            "",
            "",
            "VersionFaltante",
            {"pedida": pedida},
            ukey=claves.version_faltante(self.maquina.nombre, pedida),
        ):
            self.cx.notifier.encola(
                f"version:{pedida}",
                render(
                    "version",
                    {"version": pedida},
                    self.maquina.nombre,
                    "Motor de planes",
                ),
            )

    def _fold(self, plan: Plan) -> m.Snapshot:
        return m.fold(plan, self.store.events(plan.id), huella.de_maquina(self.maquina))

    def plans(self) -> list[Plan]:
        return planes_de(self.store)

    def _bandejas(self) -> list[Path]:
        """La bandeja del motor y los buzones de la máquina (agentes que solo escriben en su carpeta)."""
        import glob

        extra = [
            Path(x)
            for patron in self.maquina.buzones
            for x in sorted(glob.glob(os.path.expanduser(patron)))
        ]
        return [self.home / "inbox", *(b for b in extra if b.is_dir())]

    def ingest_inbox(self) -> None:
        inbox = self.home / "inbox"
        hechos, malos = inbox / "procesados", inbox / "rechazados"
        hechos.mkdir(parents=True, exist_ok=True)
        archivos = sorted(
            (f for b in self._bandejas() for f in b.glob("*.json")),
            key=lambda f: f.name,
        )
        for f in archivos:
            try:
                cmd = Comando.from_dict(json.loads(f.read_text(encoding="utf-8")))
                resultado = self._procesa(cmd)
                self.store.append(
                    cmd.plan_id,
                    "",
                    "CommandReceived",
                    {"cmd_id": cmd.cmd_id, "tipo": cmd.tipo, "resultado": resultado},
                    ukey=claves.comando(cmd.cmd_id),
                )
                destino = hechos
            except Exception as e:  # noqa: BLE001  un archivo malo no frena a los demás de la bandeja
                self._rechaza(f, e)
                destino = malos
            destino.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(f, destino / f.name)
            except OSError:
                pass  # se queda en la bandeja: lo hecho es idempotente (ukey) y el aviso no se repite

    def _rechaza(self, f: Path, e: Exception) -> None:
        """Quien entregó pudo irse con "encolado": el rechazo tiene que llegarle a David
        (MOTOR 01: INGESTA 01 se rechazó en la Mini y nadie se enteró)."""
        motivo = (str(e).strip() or type(e).__name__).splitlines()[0][:300]
        msg: dict = {}
        try:
            leido = json.loads(f.read_text(encoding="utf-8"))
            msg = leido if isinstance(leido, dict) else {}
        except (OSError, ValueError):
            pass
        cmd_id = msg["cmd_id"] if isinstance(msg.get("cmd_id"), str) else f.stem
        plan = msg.get("plan") if isinstance(msg.get("plan"), dict) else {}
        titulo = str(plan.get("titulo") or plan.get("id") or "un plan")
        por = msg["por"] if isinstance(msg.get("por"), str) and msg["por"] else ""
        nuevo = self.store.append(
            "",
            "",
            "CommandReceived",
            {
                "cmd_id": cmd_id,
                "tipo": str(msg.get("tipo", "")),
                "resultado": f"rechazado: {motivo}",
            },
            ukey=claves.comando(cmd_id),
        )
        if nuevo:
            self.cx.notifier.encola(
                f"rechazo:{cmd_id}",
                render(
                    "rechazado",
                    {"motivo": motivo, "por": por or "quien te lo dio"},
                    self.maquina.nombre,
                    titulo,
                ),
            )

    def _procesa(self, cmd: Comando) -> str:
        if cmd.tipo == "hecho":
            return self._hecho(cmd.plan_id, cmd)
        if cmd.tipo == "nota":
            self.store.append(
                cmd.plan_id,
                cmd.block,
                "Note",
                {"texto": cmd.texto},
                ukey=claves.nota(cmd.cmd_id),
            )
            return "anotado"
        plan = Plan.from_dict(cmd.plan)
        if malos := modelos_desconocidos(plan, self.cx.conf.rutas) or revisa_sin_quien(
            plan, self.cx.conf.escalera("implementar")
        ):
            raise PlanError(malos)
        for repo in dict.fromkeys(b.repo for b in plan.blocks):
            self.cx.ws.espejo(
                repo
            )  # antes de cargarlo: un repo que no existe rechaza el plan
            rc = RepoConf.parse(self.fx.conf_base(repo)[1])
            if faltan := requisitos_faltantes(rc.requiere, self.maquina.tiene):
                raise PlanError([motivo_requiere(self.maquina.nombre, repo, faltan)])
            for b in plan.blocks:
                if (
                    b.repo == repo
                    and b.lane == "release"
                    and not b.release
                    and not (rc.desplegar and rc.checklist and rc.rollback)
                ):
                    raise PlanError(
                        [
                            f"el bloque {b.id} es de lane release y no trae 'despliegue:' "
                            "ni el repo tiene su receta de despliegue en .motor.toml"
                        ]
                    )
        # El plan vigente es el de la última entrega: un texto distinto del vigente es una versión
        # nueva (también si es uno anterior, que así se reactiva); el mismo texto otra vez no hace
        # nada. Lo hecho sigue hecho salvo lo que el cambio toque (machine.fold).
        cargas = [e for e in self.store.events(plan.id) if e.kind == "PlanLoaded"]
        version = max(
            (e.data.get("version", i) for i, e in enumerate(cargas, 1)), default=0
        )
        if not cargas or _huella(cargas[-1].data["plan"]) != _huella(cmd.plan):
            version += 1
            self.store.append(
                plan.id,
                "",
                "PlanLoaded",
                {"plan": cmd.plan, "por": cmd.por, "version": version},
                ukey=claves.plan_cargado(plan.id, version),
            )
        self.fx.pin_repos(plan)
        return f"tomado (versión {version})"

    def _hecho(self, plan_id: str, cmd: Comando) -> str:
        plan = next((p for p in self.plans() if p.id == plan_id), None)
        if plan is None or cmd.block not in {b.id for b in plan.blocks}:
            return "rechazado: no conozco ese plan o ese paso"
        if m.estado(self._fold(plan), cmd.block) is not m.Estado.HUMANO:
            return "rechazado: ese paso todavía no te toca; te aviso cuando sí"
        self.store.append(
            plan_id,
            cmd.block,
            "HumanDone",
            {"por": cmd.por},
            ukey=claves.humano_hecho(plan_id, cmd.block),
        )
        return "anotado"

    def ingest_attempts(self, plan: Plan) -> None:
        snap = self._fold(plan)
        for bid, bv in snap.blocks.items():
            att = bv.running()
            if att is None:
                continue
            out = self.fx.collect(plan, snap, bid, att)
            if out is None:
                continue
            self.store.append(
                plan.id,
                bid,
                "AttemptEnded",
                {"n": att.n, "role": att.role, "outcome": out},
                ukey=claves.intento_terminado(plan.id, bid, att.role, att.n),
            )
            if out["kind"] == "corte":
                key = f"retome:{plataforma.boot_id()}"
                if not any(
                    e.kind == "NoticeQueued" and e.data["key"] == key
                    for e in self.store.events(plan.id)
                ):
                    self.fx.notify(plan, snap, m.Notify(key, "", "retome", ()))

    def tick(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        self._fase("trae_conf", lambda: self.trae_conf(now), now)
        self._fase("recargar_conf", self.recargar_conf, now)
        self._fase("ingest_inbox", self.ingest_inbox, now)
        todo_hecho = True
        planes = self.plans()
        self._folds_fuera: dict[str, m.Snapshot] | None = {}
        self._llamadas_fuera: dict[str, int] | None = None
        for plan in planes:
            self._fase(
                "ingest_attempts", lambda: self.ingest_attempts(plan), now, plan.id
            )
        for plan in planes:
            try:
                self._folds_fuera[plan.id] = self._fold(plan)
            except Exception:  # noqa: BLE001  el plan roto lo anota su propio tick
                continue
        for plan in planes:
            todo_hecho = self._tick_plan(plan, planes, now) and todo_hecho
        self._fase(
            "ajusta_sueno",
            lambda: self.ajusta_sueno(self._hay_trabajo(planes), now),
            now,
        )
        self._fase("purga_registros", lambda: self.fx.purga_registros(planes, now), now)
        self._fase("envia_pendientes", self.cx.notifier.envia_pendientes, now)
        self._folds_fuera = None
        self._llamadas_fuera = None
        return todo_hecho and bool(planes)

    def _fase(self, accion: str, fn, now: float, plan_id: str = "") -> bool:
        """Una fase del tick que revienta se anota como EffectFailed y el tick sigue con lo demás."""
        try:
            fn()
        except Exception as e:  # noqa: BLE001  una fase mala no tumba al motor ni a los otros planes
            self._falla_fase(plan_id, accion, e, now)
            return False
        self._cierra_racha(plan_id, "", accion)
        return True

    def _falla_fase(self, plan_id: str, accion: str, e: Exception, now: float) -> None:
        try:
            self._anota_falla(plan_id, "", accion, e, now)
        except Exception:  # noqa: BLE001  ni siquiera un log que no escribe tumba al tick
            print(f"motor: {accion} falló: {e!r}", file=sys.stderr, flush=True)

    def _tick_plan(self, plan: Plan, planes: list[Plan], now: float) -> bool:
        """fold, decide y ejecutar de un plan. Devuelve si el plan quedó entregado y limpio."""
        try:
            snap = self._fold(plan)
        except Exception as e:  # noqa: BLE001
            self._falla_fase(plan.id, "fold", e, now)
            return False
        try:
            cd_fuera, int_fuera = self._cuentas_fuera(planes, plan)
            acciones = list(
                m.decide(
                    snap,
                    self.cx.policy,
                    now,
                    self._ocupados_fuera(planes, plan),
                    cd_fuera,
                    int_fuera,
                )
            )
        except Exception as e:  # noqa: BLE001
            self._falla_fase(plan.id, "decide", e, now)
            return False
        for a in acciones:
            clave = (plan.id, _bloque(a), type(a).__name__)
            if now < self._espera_efecto.get(clave, 0):
                continue  # falló hace poco: no se repite en cada tick (una prueba por ssh a la PC, p. ej.)
            try:
                self.execute(plan, snap, a)
                self._espera_efecto.pop(clave, None)
                self._cierra_racha(plan.id, clave[1], clave[2])
            except Exception as e:  # noqa: BLE001  red caída, repo borrado, disco lleno: se reintenta en 5 min
                self._espera_efecto[clave] = now + ESPERA_TRAS_FALLA_S
                self._falla_efecto(plan, a, e, now)
            try:
                snap = self._fold(plan)
            except Exception as e:  # noqa: BLE001
                self._falla_fase(plan.id, "fold", e, now)
                return False
        if getattr(self, "_folds_fuera", None) is not None:
            self._folds_fuera[plan.id] = snap
        hecho = all(m.estado(snap, b) is m.Estado.HECHO for b in snap.blocks)
        return hecho and f"terminado:{plan.id}" in snap.plan_notices and snap.limpio

    def _hay_trabajo(self, planes: list[Plan]) -> bool:
        return any(not self._terminado(p) for p in planes)

    def _cuentas_fuera(
        self, planes: list[Plan], este: Plan
    ) -> tuple[dict[str, float], tuple]:
        """Pausas de cuota e intentos terminados de los otros planes de esta máquina."""
        cd: dict[str, float] = {}
        intentos: list = []
        cache = getattr(self, "_folds_fuera", None)
        for p in planes:
            if p.id == este.id:
                continue
            if cache is not None:
                s = cache.get(p.id)
                if s is None:
                    continue
            else:
                try:
                    s = self._fold(p)
                except Exception:  # noqa: BLE001  el plan roto lo anota su propio tick
                    continue
            for c, h in s.cooldowns.items():
                cd[c] = max(cd.get(c, 0), h)
            intentos += [a for bv in s.blocks.values() for a in bv.attempts if a.ended]
        return cd, tuple(intentos)

    def _ocupados_fuera(self, planes: list[Plan], este: Plan) -> dict[str, int]:
        """Agentes corriendo en los otros planes de esta máquina, por cuenta, más las llamadas a otros
        modelos que están haciendo los agentes de cualquier plan (shims de motor/llamadas.py)."""
        if getattr(self, "_llamadas_fuera", None) is None:
            self._llamadas_fuera = dict(
                llamadas.por_cuenta(self.home, solo_llamadas=True)
            )
        n: dict[str, int] = dict(self._llamadas_fuera)
        cache = getattr(self, "_folds_fuera", None)
        for p in planes:
            if p.id == este.id:
                continue
            if cache is not None:
                s = cache.get(p.id)
                if s is None:
                    continue
            else:
                try:
                    s = self._fold(p)
                except Exception:  # noqa: BLE001  el plan roto lo anota su propio tick
                    continue
            ocupados = m._ocupados(s, self.cx.policy)
            for c, k in ocupados.items():
                n[c] = n.get(c, 0) + k
        return n

    def _terminado(self, plan: Plan) -> bool:
        try:
            snap = self._fold(plan)
        except Exception:  # noqa: BLE001  un plan que no se puede leer no mantiene despierta la máquina
            return True
        return all(m.estado(snap, b) is m.Estado.HECHO for b in snap.blocks)

    def ajusta_sueno(self, hay_trabajo: bool, now: float) -> None:
        """Con un plan en curso la máquina no se duerme; sin planes, vuelve a lo normal.
        Solo si `maquina.toml` trae [no_dormir] (la MacBook). Si el comando falla, reintenta en 10 min."""
        mq = self.maquina
        argv = mq.no_dormir_encender if hay_trabajo else mq.no_dormir_apagar
        if (
            not argv
            or self._sin_dormir == hay_trabajo
            or now < self._sin_dormir_reintento
        ):
            return
        try:
            r = subprocess.run(
                list(argv),
                check=False,
                capture_output=True,
                text=True,
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError):
            self._sin_dormir_reintento = now + 600
            raise
        if r.returncode == 0:
            self._sin_dormir = hay_trabajo
        else:
            self._sin_dormir_reintento = now + 600

    def _falla_efecto(self, plan: Plan, a: m.Action, e: Exception, now: float) -> None:
        """Un efecto que revienta no tumba al motor. Se anota cada vez, con el `desde` de su racha, y el
        tick siguiente lo reintenta (los efectos son idempotentes); si sigue una hora, `diagnostico`
        lo vuelve atasco y avisa una vez."""
        self._anota_falla(plan.id, _bloque(a), type(a).__name__, e, now)

    def _rachas_abiertas(self) -> dict[tuple[str, str, str], tuple[float, float]]:
        """(plan, bloque, acción) -> (desde, última falla) de lo que sigue fallando, leído del log
        para que un reinicio del motor no parta una racha en dos."""
        abiertas: dict[tuple[str, str, str], tuple[float, float]] = {}
        try:
            eventos = self.store.events(kind=("EffectFailed", "EffectOk"))
        except Exception:  # noqa: BLE001  sin log legible no hay rachas que recordar
            return abiertas
        for ev in eventos:
            clave = (ev.plan, ev.block, ev.data.get("accion", ""))
            if ev.kind == "EffectFailed":
                abiertas[clave] = (ev.data.get("desde", ev.ts), ev.ts)
                self._fallas_n[clave] = self._fallas_n.get(clave, 0) + 1
            elif ev.kind == "EffectOk":
                abiertas.pop(clave, None)
        return abiertas

    def _anota_falla(
        self, plan_id: str, bloque: str, accion: str, e: Exception, now: float
    ) -> None:
        clave = (plan_id, bloque, accion)
        desde, ultima = self._rachas.get(clave, (now, now))
        if now - ultima > HUECO_EFECTO_S:
            desde = (
                now  # dos fallas sueltas, sin reintentos entre ellas, no son una racha
            )
        n = self._fallas_n.get(clave, 0) + 1
        self.store.append(
            plan_id,
            bloque,
            "EffectFailed",
            {"accion": accion, "error": str(e)[-500:], "desde": desde},
            ukey=claves.falla_de_efecto(plan_id, bloque, accion, n),
            ts=now,
        )
        self._fallas_n[clave] = n
        self._rachas[clave] = (desde, now)

    def _cierra_racha(self, plan_id: str, bloque: str, accion: str) -> None:
        """El primer éxito de la acción termina su racha; se anota una sola vez."""
        racha = self._rachas.pop((plan_id, bloque, accion), None)
        if racha is not None:
            self.store.append(
                plan_id,
                bloque,
                "EffectOk",
                {"accion": accion, "desde": racha[0]},
                ukey=claves.efecto_ok(
                    plan_id,
                    bloque,
                    accion,
                    self._fallas_n.get((plan_id, bloque, accion), 0),
                ),
            )

    def execute(self, plan: Plan, snap: m.Snapshot, a: m.Action) -> None:
        f = {
            m.StartAttempt: self.fx.start_attempt,
            m.Verify: self.fx.verify,
            m.OpenPr: self.fx.open_pr,
            m.MarkReady: self.fx.mark_ready,
            m.PollChecks: self.fx.poll_checks,
            m.Merge: self.fx.merge,
            m.Deploy: self.fx.deploy,
            m.Prove: self.fx.prove,
            m.ProbeHuman: self.fx.probe_human,
            m.Notify: self.fx.notify,
            m.Interrumpe: self.fx.interrumpe,
            m.Limpia: self.fx.limpia,
        }[type(a)]
        f(plan, snap, a)

    def run(self, intervalo: float, hasta_terminar: bool, tope_s: float = 0) -> int:
        inicio = time.time()
        while True:
            try:
                terminado = self.tick()
            except Exception:  # noqa: BLE001  un tick malo se escribe en el log del servicio y sigue el siguiente
                print(
                    f"motor: el tick falló, sigo con el siguiente:\n{traceback.format_exc()}",
                    file=sys.stderr,
                    flush=True,
                )
                terminado = False
            try:
                self.avisa_version_faltante()
                cambio = version.sha_actual() != self._sha_arranque
            except Exception:  # noqa: BLE001  mirar la versión nunca tumba al motor
                cambio = False
            if cambio:
                print(
                    "motor: el código instalado en disco cambió desde que arranqué; "
                    "salgo con 75 para que el supervisor me relance con el nuevo",
                    file=sys.stderr,
                    flush=True,
                )
                return SALIDA_RELANZAR
            if terminado and hasta_terminar:
                return 0
            if tope_s and time.time() - inicio > tope_s:
                return 3
            time.sleep(intervalo)
