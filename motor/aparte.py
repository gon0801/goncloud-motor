from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import machine as m
from .conf import load_conf, load_maquina
from .effects import Contexto, Effects
from .github import para
from .notify import Notifier
from .plan import Plan
from .store import Store
from .workspace import Workspace


def _contexto(home: Path) -> Contexto:
    maquina = load_maquina(home)
    os.environ.update(dict(maquina.env))
    if maquina.path_extra:
        os.environ["PATH"] = os.pathsep.join(
            [*maquina.path_extra, os.environ.get("PATH", "")]
        )
    conf = load_conf(maquina.conf)
    av = conf.avisos
    return Contexto(
        home=home,
        maquina=maquina,
        conf=conf,
        policy=m.Policy.from_conf(conf),
        store=Store(home / "motor.db"),
        ws=Workspace(home, maquina.nombre),
        gh=para(maquina.github, dict(maquina.secretos)),
        notifier=Notifier(
            home,
            av.get("base_url", "https://api.telegram.org"),
            dict(maquina.secretos).get(av.get("token_env", "MOTOR_TELEGRAM_TOKEN"), ""),
            av.get("chat", ""),
        ),
        checks_cada_s=conf.tiempos.get("checks_cada_s", 60.0),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--dir", required=True)
    a = ap.parse_args(argv)
    d = Path(a.dir)
    try:
        spec = json.loads((d / "spec.json").read_text(encoding="utf-8"))
        cx = _contexto(Path(a.home))
        fx = Effects(cx)
        fx._dir_aparte = d
        cx.programadas_sistema = spec.get("programadas_sistema", cx.programadas_sistema)
        plan = Plan.from_dict(spec["plan"])
        blocks = {}
        for b in plan.blocks:
            bv = m.BlockView(block=b)
            bv.merged = list(spec.get("merged", {}).get(b.id, []))
            bv.deploy_started = set(spec.get("deploy_started", {}).get(b.id, []))
            bv.deploy_marcados = set(spec.get("deploy_marcados", {}).get(b.id, []))
            bv.verify_started = set(spec.get("verify_started", {}).get(b.id, []))
            bv.prove_started = set(spec.get("prove_started", {}).get(b.id, []))
            blocks[b.id] = bv
        snap = m.Snapshot(
            plan=plan,
            blocks=blocks,
            cooldowns={},
            plan_notices=set(),
            repos=spec.get("repos", {}),
        )
        kind = spec["kind"]
        if kind == "verify":
            datos = fx._verify_data(plan, snap, m.Verify(spec["block"], spec["sha"]))
            terminal = "Verified"
        elif kind == "deploy":
            datos = fx._deploy_data(plan, snap, m.Deploy(spec["block"], spec["sha"]))
            terminal = "Deployed"
        else:
            datos = fx._prove_data(plan, snap, m.Prove(spec["block"], spec["sha"]))
            terminal = "Proved"
        if spec.get("huella") is not None:
            datos["huella"] = spec["huella"]
            datos["huella_maquina"] = spec["huella_maquina"]
        payload = {"kind": terminal, "data": datos}
    except Exception as e:  # noqa: BLE001
        payload = {"error": f"{type(e).__name__}: {e}"}
    tmp = d / "outcome.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "outcome.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
