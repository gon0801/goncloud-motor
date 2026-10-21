"""El programa siempre encendido de cada máquina. Su tick no usa ningún modelo.

    tick = conf -> ingerir (inbox + intentos terminados) -> fold -> decide -> ejecutar -> avisos

Cada máquina tiene el suyo y solo lleva los planes que se le entregaron ahí. Si muere o la
máquina se apaga, el arranque siguiente reconstruye todo del log: cada efecto revisa si ya
existe antes de repetirse, y los agentes corren aparte (runwrap).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import machine as m
from . import plataforma
from .conf import (
    RepoConf,
    load_conf,
    load_maquina,
    motivo_requiere,
    requisitos_faltantes,
)
from .effects import Contexto, Effects
from .github import para
from .notify import Notifier, render
from .plan import Plan, PlanError, modelos_desconocidos, revisa_sin_quien
from .store import Store
from .version import SALIDA_RELANZAR, info, pide_otra
from .workspace import Workspace, git


ESPERA_TRAS_FALLA_S = 300


def _bloque(a: m.Action) -> str:
    return getattr(a, "block", "") or getattr(getattr(a, "ticket", None), "block", "")


class Daemon:
    def __init__(self, home: Path, checks_cada_s: float | None = None):
        self.home = home
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
        self._sin_dormir: bool | None = (
            None  # None: aplicar en el primer tick (también tras un reinicio)
        )
        self._sin_dormir_reintento = 0.0
        self._espera_efecto: dict[
            tuple[str, str, str], float
        ] = {}  # efecto que falló -> no antes de

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
                ukey=f"confmala:{nueva}",
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

    def plans(self) -> list[Plan]:
        vistos: dict[str, Plan] = {}
        for e in self.store.events():
            if e.kind == "PlanLoaded":
                vistos[e.plan] = Plan.from_dict(e.data["plan"])
        return list(vistos.values())

    def ingest_inbox(self) -> None:
        inbox = self.home / "inbox"
        hechos = inbox / "procesados"
        hechos.mkdir(parents=True, exist_ok=True)
        for f in sorted(inbox.glob("*.json")):
            msg = json.loads(f.read_text(encoding="utf-8"))
            cmd_id, tipo = msg["cmd_id"], msg["tipo"]
            plan_id = msg.get("plan") if tipo != "plan" else msg["plan"]["id"]
            if tipo == "plan":
                try:
                    plan = Plan.from_dict(msg["plan"])
                    if malos := modelos_desconocidos(
                        plan, self.cx.conf.rutas
                    ) or revisa_sin_quien(plan, self.cx.conf.escalera("implementar")):
                        raise PlanError(malos)
                    for repo in dict.fromkeys(b.repo for b in plan.blocks):
                        self.cx.ws.espejo(
                            repo
                        )  # antes de cargarlo: un repo que no existe rechaza el plan
                        rc = RepoConf.parse(self.fx.conf_base(repo)[1])
                        if faltan := requisitos_faltantes(
                            rc.requiere, self.maquina.tiene
                        ):
                            raise PlanError(
                                [motivo_requiere(self.maquina.nombre, repo, faltan)]
                            )
                    self.store.append(
                        plan.id,
                        "",
                        "PlanLoaded",
                        {"plan": msg["plan"], "por": msg.get("por", "")},
                        ukey=f"plan:{plan.id}",
                    )
                    self.fx.pin_repos(plan)
                    resultado = "tomado"
                except (PlanError, RuntimeError, KeyError) as e:
                    resultado = f"rechazado: {e}"
                    # Quien entregó pudo irse con "encolado": el rechazo tiene que llegarle a David
                    # (MOTOR 01: INGESTA 01 se rechazó en la Mini y nadie se enteró).
                    titulo = str(
                        msg.get("plan", {}).get("titulo")
                        or msg.get("plan", {}).get("id")
                        or "un plan"
                    )
                    self.cx.notifier.encola(
                        f"rechazo:{cmd_id}",
                        render(
                            "rechazado",
                            {
                                "motivo": str(e).splitlines()[0][:300],
                                "por": msg.get("por") or "quien te lo dio",
                            },
                            self.maquina.nombre,
                            titulo,
                        ),
                    )
            elif tipo == "hecho":
                resultado = self._hecho(plan_id, msg)
            elif tipo == "nota":
                self.store.append(
                    plan_id,
                    msg["block"],
                    "Note",
                    {"texto": msg["texto"]},
                    ukey=f"nota:{cmd_id}",
                )
                resultado = "anotado"
            else:
                resultado = f"rechazado: tipo desconocido {tipo}"
            self.store.append(
                plan_id or "",
                "",
                "CommandReceived",
                {"cmd_id": cmd_id, "tipo": tipo, "resultado": resultado},
                ukey=f"cmd:{cmd_id}",
            )
            os.replace(f, hechos / f.name)

    def _hecho(self, plan_id: str, msg: dict) -> str:
        plan = next((p for p in self.plans() if p.id == plan_id), None)
        if plan is None or msg["block"] not in {b.id for b in plan.blocks}:
            return "rechazado: no conozco ese plan o ese paso"
        if (
            m.estado(m.fold(plan, self.store.events(plan.id)), msg["block"])
            is not m.Estado.HUMANO
        ):
            return "rechazado: ese paso todavía no te toca; te aviso cuando sí"
        self.store.append(
            plan_id,
            msg["block"],
            "HumanDone",
            {"por": msg.get("por", "")},
            ukey=f"humano:{plan_id}:{msg['block']}",
        )
        return "anotado"

    def ingest_attempts(self, plan: Plan) -> None:
        snap = m.fold(plan, self.store.events(plan.id))
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
                ukey=f"ended:{plan.id}:{bid}:{att.role}:{att.n}",
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
        self.trae_conf(now)
        self.recargar_conf()
        self.ingest_inbox()
        todo_hecho = True
        planes = self.plans()
        for plan in planes:
            self.ingest_attempts(plan)
        for plan in planes:
            snap = m.fold(plan, self.store.events(plan.id))
            cd_fuera, int_fuera = self._cuentas_fuera(planes, plan)
            for a in m.decide(
                snap,
                self.cx.policy,
                now,
                self._ocupados_fuera(planes, plan),
                cd_fuera,
                int_fuera,
            ):
                clave = (plan.id, _bloque(a), type(a).__name__)
                if now < self._espera_efecto.get(clave, 0):
                    continue  # falló hace poco: no se repite en cada tick (una prueba por ssh a la PC, p. ej.)
                try:
                    self.execute(plan, snap, a)
                    self._espera_efecto.pop(clave, None)
                except Exception as e:  # noqa: BLE001  red caída, repo borrado, disco lleno: se reintenta en 5 min
                    self._espera_efecto[clave] = now + ESPERA_TRAS_FALLA_S
                    self._falla_efecto(plan, a, e, now)
                snap = m.fold(plan, self.store.events(plan.id))
            hecho = all(m.estado(snap, b) is m.Estado.HECHO for b in snap.blocks)
            todo_hecho = (
                todo_hecho
                and hecho
                and f"terminado:{plan.id}" in snap.plan_notices
                and snap.limpio
            )
        self.ajusta_sueno(any(not self._terminado(p) for p in planes), now)
        self.fx.purga_registros(planes, now)
        self.cx.notifier.envia_pendientes()
        return todo_hecho and bool(planes)

    def _cuentas_fuera(
        self, planes: list[Plan], este: Plan
    ) -> tuple[dict[str, float], tuple]:
        """Pausas de cuota e intentos terminados de los otros planes de esta máquina."""
        cd: dict[str, float] = {}
        intentos: list = []
        for p in planes:
            if p.id == este.id:
                continue
            s = m.fold(p, self.store.events(p.id))
            for c, h in s.cooldowns.items():
                cd[c] = max(cd.get(c, 0), h)
            intentos += [a for bv in s.blocks.values() for a in bv.attempts if a.ended]
        return cd, tuple(intentos)

    def _ocupados_fuera(self, planes: list[Plan], este: Plan) -> dict[str, int]:
        """Agentes corriendo en los otros planes de esta máquina, por cuenta."""
        n: dict[str, int] = {}
        for p in planes:
            if p.id == este.id:
                continue
            for c, k in m._ocupados(
                m.fold(p, self.store.events(p.id)), self.cx.policy
            ).items():
                n[c] = n.get(c, 0) + k
        return n

    def _terminado(self, plan: Plan) -> bool:
        snap = m.fold(plan, self.store.events(plan.id))
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
        r = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        if r.returncode == 0:
            self._sin_dormir = hay_trabajo
        else:
            self._sin_dormir_reintento = now + 600

    def _falla_efecto(self, plan: Plan, a: m.Action, e: Exception, now: float) -> None:
        """Un efecto que revienta no tumba al motor. Se anota una vez por hora y el tick siguiente
        lo reintenta (los efectos son idempotentes); si sigue fallando, el aviso de 'atorado' llega solo."""
        bloque = _bloque(a)
        clave = f"falla:{plan.id}:{bloque}:{type(a).__name__}:{int(now // 3600)}"
        self.store.append(
            plan.id,
            bloque,
            "EffectFailed",
            {"accion": type(a).__name__, "error": str(e)[-500:]},
            ukey=clave,
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
            m.Limpia: self.fx.limpia,
        }[type(a)]
        f(plan, snap, a)

    def run(self, intervalo: float, hasta_terminar: bool, tope_s: float = 0) -> int:
        inicio = time.time()
        while True:
            self.recargar_conf()
            if otra := self.pide_otra_version():
                print(
                    f"motor: {otra}; salgo con 75 para que el supervisor me relance",
                    file=sys.stderr,
                    flush=True,
                )
                return SALIDA_RELANZAR
            if self.tick() and hasta_terminar:
                return 0
            if tope_s and time.time() - inicio > tope_s:
                return 3
            time.sleep(intervalo)
