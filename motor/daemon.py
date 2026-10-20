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
import time
from pathlib import Path

from . import machine as m
from . import plataforma
from .conf import load_conf, load_maquina
from .effects import Contexto, Effects
from .github import para
from .notify import Notifier
from .plan import Plan, PlanError, modelos_desconocidos
from .store import Store
from .workspace import Workspace


class Daemon:
    def __init__(self, home: Path, checks_cada_s: float | None = None):
        self.home = home
        self.maquina = load_maquina(home)
        self.store = Store(home / "motor.db")
        conf = load_conf(self.maquina.conf)
        av = conf.avisos
        self.cx = Contexto(
            home=home, maquina=self.maquina, conf=conf, policy=m.Policy.from_conf(conf), store=self.store,
            ws=Workspace(home, self.maquina.nombre), gh=para(self.maquina.github, dict(self.maquina.secretos)),
            notifier=Notifier(home, av.get("base_url", "https://api.telegram.org"),
                              dict(self.maquina.secretos).get(av.get("token_env", "MOTOR_TELEGRAM_TOKEN"), ""),
                              av.get("chat", "")),
            checks_cada_s=checks_cada_s if checks_cada_s is not None else conf.tiempos.get("checks_cada_s", 60.0))
        self.fx = Effects(self.cx)
        self._sin_dormir: bool | None = None    # None: aplicar en el primer tick (también tras un reinicio)
        self._sin_dormir_reintento = 0.0

    def recargar_conf(self) -> None:
        conf = load_conf(self.maquina.conf)
        if conf.sha != self.cx.conf.sha:
            self.cx.conf, self.cx.policy = conf, m.Policy.from_conf(conf)

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
            msg = json.loads(f.read_text())
            cmd_id, tipo = msg["cmd_id"], msg["tipo"]
            plan_id = msg.get("plan") if tipo != "plan" else msg["plan"]["id"]
            if tipo == "plan":
                try:
                    plan = Plan.from_dict(msg["plan"])
                    if malos := modelos_desconocidos(plan, self.cx.conf.rutas):
                        raise PlanError(malos)
                    self.store.append(plan.id, "", "PlanLoaded", {"plan": msg["plan"], "por": msg.get("por", "")},
                                      ukey=f"plan:{plan.id}")
                    self.fx.pin_repos(plan)
                    resultado = "tomado"
                except (PlanError, RuntimeError, KeyError) as e:
                    resultado = f"rechazado: {e}"
            elif tipo == "hecho":
                self.store.append(plan_id, msg["block"], "HumanDone", {"por": msg.get("por", "")},
                                  ukey=f"humano:{plan_id}:{msg['block']}")
                resultado = "anotado"
            elif tipo == "nota":
                self.store.append(plan_id, msg["block"], "Note", {"texto": msg["texto"]}, ukey=f"nota:{cmd_id}")
                resultado = "anotado"
            else:
                resultado = f"rechazado: tipo desconocido {tipo}"
            self.store.append(plan_id or "", "", "CommandReceived", {"cmd_id": cmd_id, "tipo": tipo, "resultado": resultado},
                              ukey=f"cmd:{cmd_id}")
            os.replace(f, hechos / f.name)

    def ingest_attempts(self, plan: Plan) -> None:
        snap = m.fold(plan, self.store.events(plan.id))
        for bid, bv in snap.blocks.items():
            att = bv.running()
            if att is None:
                continue
            out = self.fx.collect(plan, snap, bid, att)
            if out is None:
                continue
            self.store.append(plan.id, bid, "AttemptEnded", {"n": att.n, "role": att.role, "outcome": out},
                              ukey=f"ended:{plan.id}:{bid}:{att.role}:{att.n}")
            if out["kind"] == "corte":
                key = f"retome:{plataforma.boot_id()}"
                if not any(e.kind == "NoticeQueued" and e.data["key"] == key for e in self.store.events(plan.id)):
                    self.fx.notify(plan, snap, m.Notify(key, "", "retome", ()))

    def tick(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        self.recargar_conf()
        self.ingest_inbox()
        todo_hecho = True
        planes = self.plans()
        for plan in planes:
            self.ingest_attempts(plan)
            snap = m.fold(plan, self.store.events(plan.id))
            for a in m.decide(snap, self.cx.policy, now):
                self.execute(plan, snap, a)
                snap = m.fold(plan, self.store.events(plan.id))
            hecho = all(m.estado(snap, b) is m.Estado.HECHO for b in snap.blocks)
            todo_hecho = todo_hecho and hecho and f"terminado:{plan.id}" in snap.plan_notices
        self.ajusta_sueno(any(not self._terminado(p) for p in planes), now)
        self.cx.notifier.envia_pendientes()
        return todo_hecho and bool(planes)

    def _terminado(self, plan: Plan) -> bool:
        snap = m.fold(plan, self.store.events(plan.id))
        return all(m.estado(snap, b) is m.Estado.HECHO for b in snap.blocks)

    def ajusta_sueno(self, hay_trabajo: bool, now: float) -> None:
        """Con un plan en curso la máquina no se duerme; sin planes, vuelve a lo normal.
        Solo si `maquina.toml` trae [no_dormir] (la MacBook). Si el comando falla, reintenta en 10 min."""
        mq = self.maquina
        argv = mq.no_dormir_encender if hay_trabajo else mq.no_dormir_apagar
        if not argv or self._sin_dormir == hay_trabajo or now < self._sin_dormir_reintento:
            return
        r = subprocess.run(list(argv), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        if r.returncode == 0:
            self._sin_dormir = hay_trabajo
        else:
            self._sin_dormir_reintento = now + 600

    def execute(self, plan: Plan, snap: m.Snapshot, a: m.Action) -> None:
        f = {m.StartAttempt: self.fx.start_attempt, m.Verify: self.fx.verify, m.OpenPr: self.fx.open_pr,
             m.MarkReady: self.fx.mark_ready, m.PollChecks: self.fx.poll_checks, m.Merge: self.fx.merge,
             m.Deploy: self.fx.deploy, m.Prove: self.fx.prove, m.ProbeHuman: self.fx.probe_human,
             m.Notify: self.fx.notify}[type(a)]
        f(plan, snap, a)

    def run(self, intervalo: float, hasta_terminar: bool, tope_s: float = 0) -> int:
        inicio = time.time()
        while True:
            if self.tick() and hasta_terminar:
                return 0
            if tope_s and time.time() - inicio > tope_s:
                return 3
            time.sleep(intervalo)
