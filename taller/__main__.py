"""CLI del lead. David no teclea nada: solo recibe avisos.

    taller valida <plan.md> [--home H]     revisa el plan antes de arrancar
    taller arranca <plan.md> --home H       lo encola para el daemon
    taller estado --home H                  una tabla corta por bloque
    taller historia <plan> [bloque] --home H
    taller hecho <plan> <bloque> --home H   David terminó un paso humano
    taller nota <plan> <bloque> <texto> --home H   llega al siguiente encargo del bloque
    taller daemon --home H [--intervalo S] [--hasta-terminar] [--tope S]
    taller doctor                           qué agentes reales pueden correr sin ventana
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import machine as m
from .daemon import Daemon, load_config
from .plan import PlanError, parse_plan
from .store import Store


def _encola(home: Path, msg: dict) -> None:
    inbox = home / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    tmp = inbox / f".{time.time_ns()}.tmp"
    tmp.write_text(json.dumps(msg, ensure_ascii=False))
    tmp.rename(inbox / f"{time.time_ns()}.json")


def _valida(path: Path, home: Path | None):
    impl = 0
    if home and (home / "taller.toml").exists():
        impl = len(load_config(home).policy.implementadores)
    return parse_plan(path.read_text(), min_implementadores=2 if home else 0, implementadores=impl)


AGENTES_REALES = {
    "claude": ["-p", "--permission-mode", "--output-format", "--json-schema", "--resume"],
    "muse": ["exec", "--yolo", "--json", "--output-schema", "--prompt-file"],
    "grok": ["--prompt-file", "--always-approve", "--output-format"],
    "codex": ["exec", "--dangerously-bypass-approvals-and-sandbox", "--json"],
    "cursor-agent": ["--yolo", "--trust", "--output-format"],
    "opencode": ["run", "--auto", "--format"],
    "kimi": ["--auto", "--output-format"],
}


def doctor() -> int:
    """Revisa, sin correr ningún modelo, que cada agente tenga sus banderas de trabajo sin ventana."""
    print(f"{'agente':<13} {'ruta':<52} banderas")
    for nombre, flags in AGENTES_REALES.items():
        ruta = shutil.which(nombre, path="/Users/dn/.local/bin:/Users/dn/.opencode/bin:/opt/homebrew/bin:/usr/local/bin")
        if not ruta:
            print(f"{nombre:<13} {'(no instalado)':<52}")
            continue
        ayuda = ""
        for args in (["--help"], ["exec", "--help"], ["run", "--help"]):
            try:
                p = subprocess.run([ruta, *args], capture_output=True, text=True, timeout=20,
                                   stdin=subprocess.DEVNULL)
                ayuda += p.stdout + p.stderr
            except (subprocess.TimeoutExpired, OSError):
                pass
        faltan = [f for f in flags if f not in ayuda]
        print(f"{nombre:<13} {ruta:<52} {'ok' if not faltan else 'faltan ' + ' '.join(faltan)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="taller")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("valida", "arranca"):
        s = sub.add_parser(c)
        s.add_argument("plan", type=Path)
        s.add_argument("--home", type=Path)
    s = sub.add_parser("estado"); s.add_argument("--home", type=Path, required=True)
    s = sub.add_parser("historia"); s.add_argument("plan"); s.add_argument("bloque", nargs="?")
    s.add_argument("--home", type=Path, required=True)
    s = sub.add_parser("hecho"); s.add_argument("plan"); s.add_argument("bloque")
    s.add_argument("--home", type=Path, required=True)
    s = sub.add_parser("nota"); s.add_argument("plan"); s.add_argument("bloque"); s.add_argument("texto")
    s.add_argument("--home", type=Path, required=True)
    s = sub.add_parser("daemon"); s.add_argument("--home", type=Path, required=True)
    s.add_argument("--intervalo", type=float, default=10.0)
    s.add_argument("--hasta-terminar", action="store_true")
    s.add_argument("--tope", type=float, default=0)
    sub.add_parser("doctor")
    a = ap.parse_args(argv)

    if a.cmd == "doctor":
        return doctor()
    if a.cmd in ("valida", "arranca"):
        try:
            plan = _valida(a.plan, a.home)
        except PlanError as e:
            print("El plan no está listo:")
            for x in e.errores:
                print(f"  - {x}")
            return 2
        print(f"ok  {plan.id}: {len(plan.blocks)} bloques " +
              ", ".join(f"{b.id}({b.lane})" for b in plan.blocks))
        if a.cmd == "arranca":
            if not a.home:
                print("arranca necesita --home"); return 2
            _encola(a.home, {"tipo": "plan", "plan": plan.to_dict()})
            print("encolado: el daemon lo toma en su siguiente paso")
        return 0
    if a.cmd == "hecho":
        _encola(a.home, {"tipo": "hecho", "plan": a.plan, "block": a.bloque}); return 0
    if a.cmd == "nota":
        _encola(a.home, {"tipo": "nota", "plan": a.plan, "block": a.bloque, "texto": a.texto}); return 0
    if a.cmd == "daemon":
        return Daemon(a.home).run(a.intervalo, a.hasta_terminar, a.tope)
    if a.cmd == "estado":
        d = Daemon(a.home)
        for plan in d.plans():
            snap = m.fold(plan, d.store.events(plan.id))
            print(f"{plan.id}: {plan.titulo}")
            for bid, bv in snap.blocks.items():
                run = bv.running()
                quien = f"{run.provider} ({run.role})" if run else ""
                print(f"  {bid:<4} {m.estado(snap, bid).value:<32} {quien}")
        return 0
    if a.cmd == "historia":
        st = Store(a.home / "taller.db")
        for e in st.events(a.plan):
            if a.bloque and e.block != a.bloque:
                continue
            t = dt.datetime.fromtimestamp(e.ts).strftime("%H:%M:%S")
            resumen = {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v)
                       for k, v in e.data.items() if k not in ("plan", "feedback", "tail")}
            print(f"{t} {e.block or '-':<4} {e.kind:<15} {json.dumps(resumen, ensure_ascii=False)[:160]}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
