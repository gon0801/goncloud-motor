"""CLI del motor. Lo usan igual el lead (Mac), claw (Mini) y Hermi (PC). David no teclea nada.

    motor valida <plan.md> [--home H]
    motor entrega <plan.md> --home H [--json] [--espera S]   devuelve recibo: tomado | encolado | rechazado
    motor estado --home H [--json]
    motor recibo <cmd_id> --home H [--json]
    motor hecho <plan> <bloque> --home H                    David terminó un paso humano
    motor nota <plan> <bloque> <texto> --home H
    motor doctor --home H [--json]                          qué puede correr aquí, sin modelo
    motor doctor --home H --como-servicio [--espera S]      cada ruta con un encargo de prueba, en el entorno del servicio
    motor instala --maquina mac|mini|pc --usuario U --home H --imprime
    motor version [--json]                                   versión del paquete y SHA del código que corre
    motor daemon --home H [--intervalo S] [--hasta-terminar] [--tope S]
    motor servicio --home H                                  supervisor (lo lanza launchd / la tarea)

Los comandos dejan un archivo en `inbox/` y leen la base en solo lectura: funcionan aunque el
daemon esté caído (el pedido queda encolado y se toma al volver).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from . import machine as m
from .conf import ConfError, load_conf, load_maquina, valida_maquina
from .plan import PlanError, parse_plan, revisa_sin_quien
from .workspace import SECRETO as SECRETO_CONF


def _db(home: Path) -> Path:
    return home / "motor.db"


def _encola(home: Path, msg: dict, buzon: Path | None = None) -> str:
    """Deja la orden en la bandeja del motor, o en `buzon` si quien entrega solo puede escribir en su
    carpeta (OpenBot): el motor también recoge de los buzones que declara maquina.toml."""
    cmd_id = msg.setdefault("cmd_id", uuid.uuid4().hex[:12])
    inbox = buzon or home / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    tmp = inbox / f".{cmd_id}.tmp"
    tmp.write_text(json.dumps(msg, ensure_ascii=False), encoding="utf-8")
    tmp.rename(inbox / f"{time.time_ns()}-{cmd_id}.json")
    return cmd_id


def _recibo(home: Path, cmd_id: str) -> dict:
    if _db(home).exists():
        con = sqlite3.connect(f"file:{_db(home)}?mode=ro", uri=True)
        try:
            for (data,) in con.execute(
                "SELECT data FROM events WHERE kind='CommandReceived'"
            ):
                d = json.loads(data)
                if d["cmd_id"] == cmd_id:
                    return {
                        "cmd_id": cmd_id,
                        "estado": "tomado"
                        if not d["resultado"].startswith("rechazado")
                        else "rechazado",
                        "detalle": d["resultado"],
                    }
        finally:
            con.close()
    return {
        "cmd_id": cmd_id,
        "estado": "encolado",
        "detalle": "el motor lo toma en cuanto esté encendido",
    }


def _valida(path: Path, home: Path | None):
    impl, modelos, tabla = 0, None, ()
    if home and (home / "maquina.toml").exists():
        conf = load_conf(load_maquina(home).conf)
        impl, modelos, tabla = (
            len(conf.escalera("implementar")),
            set(conf.rutas),
            conf.escalera("implementar"),
        )
    plan = parse_plan(
        path.read_text(encoding="utf-8"),
        min_implementadores=2 if home else 0,
        implementadores=impl,
        modelos=modelos,
    )
    if tabla and (malos := revisa_sin_quien(plan, tabla)):
        raise PlanError(malos)
    return plan


def _estado(home: Path) -> list[dict]:
    from .daemon import Daemon

    d = Daemon(home)
    out = []
    for plan in d.plans():
        snap = m.fold(plan, d.store.events(plan.id))
        bloques = []
        for bid, bv in snap.blocks.items():
            run = bv.running()
            bloques.append(
                {
                    "bloque": bid,
                    "estado": m.estado(snap, bid).value,
                    "agente": run.modelo if run else "",
                    "papel": run.role if run else "",
                    "motivo": m.motivo(snap, bid, time.time()),
                }
            )
        out.append(
            {
                "plan": plan.id,
                "titulo": plan.titulo,
                "bloques": bloques,
                "hecho": all(b["estado"] == "hecho" for b in bloques),
            }
        )
    return out


def doctor(home: Path) -> dict:
    """Corre en el contexto donde corre el servicio (no por ssh): ahí es donde importa."""
    out: dict = {"ok": True, "problemas": [], "rutas": {}}
    try:
        mq = load_maquina(home)
        conf = load_conf(mq.conf)
    except (ConfError, OSError, KeyError) as e:
        return {"ok": False, "problemas": [str(e)], "rutas": {}}
    out["maquina"], out["conf_sha"] = mq.nombre, conf.sha
    path = os.pathsep.join([*mq.path_extra, os.environ.get("PATH", "")])
    for modelo, r in conf.rutas.items():
        exe = r.argv[0]
        ok = Path(exe).exists() or bool(shutil.which(exe, path=path))
        out["rutas"][modelo] = {"cuenta": r.cuenta, "cli": r.cli, "ejecutable": ok}
        if not ok:
            out["problemas"].append(f"{modelo}: no encuentro {exe}")
    for papel in conf.papeles:
        if not any(out["rutas"][mo]["ejecutable"] for mo in conf.escalera(papel)):
            out["ok"] = False
            out["problemas"].append(
                f"ningún modelo del papel {papel} puede correr aquí"
            )
    return out


def conf_valida(d: Path) -> list[str]:
    """Lo que tiene que cumplir la conf compartida antes de llegar a las tres máquinas."""
    import tomllib

    try:
        conf = load_conf(d)
    except (ConfError, OSError, KeyError, tomllib.TOMLDecodeError) as e:
        return [f"conf: {e}"]
    errores = []
    for mo, r in conf.rutas.items():
        if not any("{prompt}" in x or "{brief}" in x for x in r.argv):
            errores.append(
                f"ruta {mo}: el comando no recibe el encargo ({{prompt}} o {{brief}})"
            )
    for papel in conf.papeles:
        if not conf.escalera(papel):
            errores.append(f"papel {papel}: sin modelos")
    for f in (
        sorted((d / "maquinas").glob("*.toml")) if (d / "maquinas").is_dir() else []
    ):
        try:
            valida_maquina(tomllib.loads(f.read_text(encoding="utf-8")))
        except (ConfError, tomllib.TOMLDecodeError) as e:
            errores.append(f"maquinas/{f.name}: {e}")
    if SECRETO_CONF.search(
        "\n".join(
            p.read_text(errors="replace", encoding="utf-8") for p in d.rglob("*.toml")
        )
    ):
        errores.append(
            "hay algo con forma de llave en la conf; las llaves van en secretos.env de cada máquina"
        )
    return errores


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="motor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("valida")
    s.add_argument("plan", type=Path)
    s.add_argument("--home", type=Path)
    s = sub.add_parser("entrega")
    s.add_argument("plan", type=Path)
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--json", action="store_true")
    s.add_argument("--espera", type=float, default=10)
    s.add_argument("--por", default="")
    s.add_argument(
        "--buzon",
        type=Path,
        help="carpeta propia donde dejar el plan (agentes con acceso solo a su carpeta)",
    )
    s = sub.add_parser("estado")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("recibo")
    s.add_argument("cmd_id")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("hecho")
    s.add_argument("plan")
    s.add_argument("bloque")
    s.add_argument("--home", type=Path, required=True)
    s = sub.add_parser("nota")
    s.add_argument("plan")
    s.add_argument("bloque")
    s.add_argument("texto")
    s.add_argument("--home", type=Path, required=True)
    s = sub.add_parser("doctor")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--json", action="store_true")
    s.add_argument("--como-servicio", action="store_true")
    s.add_argument("--espera", type=float, default=60)
    s = sub.add_parser("instala")
    s.add_argument("--maquina", required=True)
    s.add_argument("--usuario", required=True)
    s.add_argument("--home", required=True)
    s.add_argument("--imprime", action="store_true")
    s = sub.add_parser("daemon")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--intervalo", type=float, default=10.0)
    s.add_argument("--hasta-terminar", action="store_true")
    s.add_argument("--tope", type=float, default=0)
    s.add_argument("--checks-cada", type=float)
    s = sub.add_parser("version")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("servicio")
    s.add_argument("--home", type=Path, required=True)
    s = sub.add_parser("conf")
    s.add_argument("accion", choices=["valida"])
    s.add_argument("dir", type=Path)
    a = ap.parse_args(argv)

    if a.cmd == "version":
        from .version import info

        i = info()
        print(json.dumps(i) if a.json else f"motor {i['version']} ({i['sha'][:12]})")
        return 0

    if a.cmd == "conf":
        errores = conf_valida(a.dir)
        print(
            "ok"
            if not errores
            else "La conf no está lista:\n" + "\n".join(f"  - {e}" for e in errores)
        )
        return 0 if not errores else 2

    if a.cmd in ("valida", "entrega"):
        try:
            plan = _valida(a.plan, a.home)
        except (PlanError, ConfError) as e:
            errores = getattr(e, "errores", [str(e)])
            if getattr(a, "json", False):
                print(
                    json.dumps(
                        {"estado": "rechazado", "errores": errores}, ensure_ascii=False
                    )
                )
            else:
                print("El plan no está listo:")
                for x in errores:
                    print(f"  - {x}")
            return 2
        if a.cmd == "valida":
            print(
                f"ok  {plan.id}: {len(plan.blocks)} bloques "
                + ", ".join(f"{b.id}({b.lane})" for b in plan.blocks)
            )
            return 0
        cmd_id = _encola(
            a.home,
            {"tipo": "plan", "plan": plan.to_dict(), "por": a.por},
            getattr(a, "buzon", None),
        )
        fin = time.time() + a.espera
        rec = _recibo(a.home, cmd_id)
        while rec["estado"] == "encolado" and time.time() < fin:
            time.sleep(0.3)
            rec = _recibo(a.home, cmd_id)
        rec["plan"] = plan.id
        print(
            json.dumps(rec, ensure_ascii=False)
            if a.json
            else f"{rec['estado']}: {rec['detalle']} (plan {plan.id}, recibo {cmd_id})"
        )
        return 0 if rec["estado"] != "rechazado" else 2
    if a.cmd == "recibo":
        rec = _recibo(a.home, a.cmd_id)
        print(
            json.dumps(rec, ensure_ascii=False)
            if a.json
            else f"{rec['estado']}: {rec['detalle']}"
        )
        return 0
    if a.cmd == "hecho":
        print(_encola(a.home, {"tipo": "hecho", "plan": a.plan, "block": a.bloque}))
        return 0
    if a.cmd == "nota":
        print(
            _encola(
                a.home,
                {"tipo": "nota", "plan": a.plan, "block": a.bloque, "texto": a.texto},
            )
        )
        return 0
    if a.cmd == "estado":
        est = _estado(a.home)
        if a.json:
            print(json.dumps(est, ensure_ascii=False))
        else:
            for p in est:
                print(f"{p['plan']}: {p['titulo']}")
                for b in p["bloques"]:
                    print(
                        f"  {b['bloque']:<4} {b['estado']:<32} {b['agente']:<8} {b['motivo']}"
                    )
        return 0
    if a.cmd == "doctor":
        if a.como_servicio:
            from .doctor import como_servicio

            d = como_servicio(a.home, a.espera)
            print(json.dumps(d, ensure_ascii=False, indent=None if a.json else 1))
            return 0 if d["ok"] else 1
        d = doctor(a.home)
        print(json.dumps(d, ensure_ascii=False, indent=None if a.json else 1))
        return 0 if d["ok"] else 1
    if a.cmd == "instala":
        from .servicio import instalable

        print(
            instalable(
                a.maquina,
                a.usuario,
                sys.executable,
                a.home,
                str(Path(__file__).resolve().parent.parent),
            )
        )
        return 0
    if a.cmd == "daemon":
        from .daemon import Daemon

        return Daemon(a.home, a.checks_cada).run(a.intervalo, a.hasta_terminar, a.tope)
    if a.cmd == "servicio":
        from .servicio import supervisor

        return supervisor(a.home)
    return 1


if __name__ == "__main__":
    sys.exit(main())
