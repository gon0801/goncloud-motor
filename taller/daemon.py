"""El programa siempre encendido. Su tick no usa ningún modelo: cero tokens por vigilar.

    tick = ingerir (inbox + intentos terminados) -> fold -> decide -> ejecutar

launchd lo mantiene vivo (KeepAlive). Si muere a la mitad, el siguiente arranque
reconstruye todo del log: cada efecto revisa si ya existe antes de repetirse, y
los agentes corren aparte (runwrap), así que su trabajo no se pierde.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
from pathlib import Path

from . import machine as m
from .effects import Config, Effects
from .plan import Plan
from .store import Store


def load_config(home: Path) -> Config:
    raw = tomllib.loads((home / "taller.toml").read_text())
    provs = {}
    for pid, p in raw["proveedores"].items():
        provs[pid] = m.Provider(id=pid, argv=tuple(p["argv"]), max_parallel=int(p.get("max_parallel", 1)),
                                env=tuple(sorted(p.get("env", {}).items())))
    t = raw.get("tiempos", {})
    pol = m.Policy(
        implementadores=tuple(raw["escaleras"]["implementadores"]),
        revisores=tuple(raw["escaleras"]["revisores"]),
        providers=provs,
        timeout_s=float(t.get("timeout_s", 4 * 3600)),
        sin_avance_s=float(t.get("sin_avance_s", 45 * 60)),
        cooldown_default_s=float(t.get("cooldown_default_s", 5 * 3600)),
        aviso_lento_s=float(t.get("aviso_lento_s", 6 * 3600)),
    )
    tg = raw.get("telegram", {})
    return Config(home=home, origin=raw["repo"]["origin"], principal=raw["repo"].get("principal", "main"),
                  ci=raw["repo"].get("ci", "./ci.sh"), prod_dir=Path(raw["produccion"]["dir"]), policy=pol,
                  telegram_token=os.environ.get(tg.get("token_env", "TALLER_TELEGRAM_TOKEN"), ""),
                  telegram_chat=str(tg.get("chat", "")))


class Daemon:
    def __init__(self, home: Path):
        self.home = home
        self.cfg = load_config(home)
        self.store = Store(home / "taller.db")
        self.fx = Effects(self.cfg, self.store)

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
            tipo = msg["tipo"]
            if tipo == "plan":
                plan = Plan.from_dict(msg["plan"])
                self.store.append(plan.id, "", "PlanLoaded", {"plan": msg["plan"]}, ukey=f"inbox:{f.name}")
            elif tipo == "hecho":
                self.store.append(msg["plan"], msg["block"], "HumanDone", {"por": "lead"},
                                  ukey=f"humano:{msg['plan']}:{msg['block']}")
            elif tipo == "nota":
                self.store.append(msg["plan"], msg["block"], "Note", {"texto": msg["texto"]}, ukey=f"inbox:{f.name}")
            os.replace(f, hechos / f.name)

    def ingest_attempts(self, plan: Plan) -> None:
        snap = m.fold(plan, self.store.events(plan.id))
        for bid, bv in snap.blocks.items():
            att = bv.running()
            if att is None:
                continue
            out = self.fx.collect(plan, bid, att)
            if out is not None:
                self.store.append(plan.id, bid, "AttemptEnded", {"n": att.n, "role": att.role, "outcome": out},
                                  ukey=f"ended:{plan.id}:{bid}:{att.role}:{att.n}")

    def tick(self, now: float | None = None) -> bool:
        """Un paso. Devuelve True si todos los planes cargados están hechos."""
        now = now if now is not None else time.time()
        self.ingest_inbox()
        todo_hecho = True
        for plan in self.plans():
            self.ingest_attempts(plan)
            snap = m.fold(plan, self.store.events(plan.id))
            acciones = m.decide(snap, self.cfg.policy, now)
            for a in acciones:
                self.execute(plan, a)
            snap = m.fold(plan, self.store.events(plan.id))
            hecho = all(m.estado(snap, b) is m.Estado.HECHO for b in snap.blocks)
            avisado = f"terminado:{plan.id}" in snap.plan_notices
            todo_hecho = todo_hecho and hecho and avisado
        self.fx.send_pending()
        return todo_hecho and bool(self.plans())

    def execute(self, plan: Plan, a: m.Action) -> None:
        match a:
            case m.StartAttempt():
                self.fx.start_attempt(plan, a)
            case m.Verify():
                self.fx.verify(plan, a)
            case m.RunCI():
                self.fx.run_ci(plan, a)
            case m.Merge():
                self.fx.merge(plan, a)
            case m.Deploy():
                self.fx.deploy(plan, a)
            case m.Prove():
                self.fx.prove(plan, a)
            case m.ProbeHuman():
                self.fx.probe_human(plan, a)
            case m.Notify():
                self.fx.notify(plan, a)

    def run(self, intervalo: float, hasta_terminar: bool, tope_s: float = 0) -> int:
        inicio = time.time()
        while True:
            if self.tick() and hasta_terminar:
                return 0
            if tope_s and time.time() - inicio > tope_s:
                return 3
            time.sleep(intervalo)
