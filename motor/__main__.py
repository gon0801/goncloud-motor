"""CLI del motor. Lo usan igual el lead (Mac), claw (Mini) y Hermi (PC). David no teclea nada.

    motor valida <plan.md> [--home H]
    motor entrega <plan.md> --home H [--json] [--espera S]   devuelve recibo: tomado | encolado | rechazado
    motor estado --home H [--json]
    motor recibo <cmd_id> --home H [--json]
    motor hecho <plan> <bloque> --home H                    David terminó un paso humano
    motor nota <plan> <bloque> <texto> --home H
    motor pausa <plan> --home H
    motor reanuda <plan> --home H
    motor cancela <plan> --home H
    motor mantenimiento activar --home H [--motivo TEXTO]
    motor mantenimiento liberar --home H --generacion G
    motor doctor --home H [--json]                          qué puede correr aquí, sin modelo
    motor doctor --home H --como-servicio [--espera S]      cada ruta con un encargo de prueba, en el entorno del servicio
    motor instala --maquina mac|mini|pc --usuario U --home H --imprime
    motor programadas --home H [--json]                      tareas que apuntan a carpetas que el motor borra (sale 3 si hay)
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
import tomllib
import uuid
from pathlib import Path

from . import efimeras, huella, senal
from . import machine as m
from .conf import (
    DATOS_DE_RUTA,
    ConfError,
    datos_desconocidos,
    load_conf,
    load_maquina,
    valida_maquina,
)
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


def _estado_recibo(resultado: str) -> str:
    if resultado.startswith("rechazado"):
        return "rechazado"
    if resultado.startswith(("pausa", "reanud", "cancel", "liberad")):
        return "aplicada"
    return "tomado"


def _en_bandeja(home: Path, cmd_id: str) -> bool:
    inbox = home / "inbox"
    if not inbox.is_dir():
        return False
    for f in inbox.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("cmd_id") == cmd_id:
            return True
    return False


def _recibo(home: Path, cmd_id: str) -> dict:
    local = home / "recibos" / f"{cmd_id}.json"
    if local.is_file():
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and data.get("cmd_id") == cmd_id:
            data.setdefault("aviso", _aviso_de(home, cmd_id))
            return data
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
                        "estado": _estado_recibo(str(d["resultado"])),
                        "detalle": d["resultado"],
                        "aviso": _aviso_de(home, cmd_id),
                    }
        finally:
            con.close()
    if _en_bandeja(home, cmd_id):
        return {
            "cmd_id": cmd_id,
            "estado": "encolado",
            "detalle": "el motor lo toma en cuanto esté encendido",
            "aviso": _aviso_de(home, cmd_id),
        }
    return {
        "cmd_id": cmd_id,
        "estado": "desconocido",
        "detalle": "no hay una orden con ese id",
        "aviso": "sin_aviso",
    }


def _guarda_recibo(home: Path, cmd_id: str, estado: str, detalle: str, extra: dict) -> None:
    dest = home / "recibos"
    dest.mkdir(parents=True, exist_ok=True)
    payload = {"cmd_id": cmd_id, "estado": estado, "detalle": detalle, **extra}
    tmp = dest / f".{cmd_id}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, dest / f"{cmd_id}.json")


def _aviso_de(home: Path, cmd_id: str) -> str:
    """Estado del aviso de esa orden. Sin archivo no se dice entregado."""
    from .notify import estado_aviso

    visto = estado_aviso(home, [f"cmd:{cmd_id}", f"rechazo:{cmd_id}"])
    if visto:
        return visto
    if not _db(home).exists():
        return "sin_aviso"
    con = sqlite3.connect(f"file:{_db(home)}?mode=ro", uri=True)
    try:
        for (raw,) in con.execute(
            "SELECT data FROM events WHERE kind = 'NoticeRequested'"
        ):
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if data.get("notice_id") == f"cmd:{cmd_id}" or data.get("key") == f"rechazo:{cmd_id}":
                return "desconocido"
    except sqlite3.Error:
        return "desconocido"
    finally:
        con.close()
    return "sin_aviso"


def _cuenta(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for p in path.glob("*.txt") if p.is_file())


def _avisos(home: Path) -> dict:
    from .notify import salud
    from .store import Store

    eventos = []
    if _db(home).exists():
        try:
            eventos = Store.solo_lectura(home / "motor.db").events(
                kind=("NoticeQueued", "NoticeRequested")
            )
        except sqlite3.Error:
            eventos = []
    return salud(home, eventos)


def _por_conciliar(eventos) -> list[str]:
    ultimo: dict[str, str | None] = {}
    for e in eventos:
        if e.kind == "Intencion":
            ident = str(e.data.get("id") or "")
            if ident:
                ultimo.setdefault(ident, None)
        elif e.kind == "EfectoObservado":
            ident = str(e.data.get("intencion") or "")
            if ident:
                ultimo[ident] = str(e.data.get("resultado") or "")
    return [ident for ident, res in ultimo.items() if res in (None, "", "incierto")]


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


def _retencion(home: Path) -> dict | None:
    from .control import leer

    hold = leer(home)
    if hold is None:
        return None
    return {
        "retenido": True,
        "generacion": hold.generation,
        "solicitud": hold.request_id,
        "motivo": hold.reason,
        "desde": hold.created_at,
        "causa": hold.cause,
    }


def _arboles(snap, retenido: bool) -> list[dict]:
    from .effects import observar_arbol

    parada = retenido or snap.control in (
        "PausaSolicitada",
        "CancelacionSolicitada",
    )
    pares = [
        (bid, att)
        for bid, bv in snap.blocks.items()
        for att in bv.attempts
    ]
    pares += [(bid, att) for (bid, _n, _role), att in snap.huerfanos]
    out = []
    for bid, att in pares:
        if att.ended is not None:
            continue
        obs = observar_arbol(att)
        if obs == "ausente" and not parada:
            continue
        out.append(
            {
                "bloque": bid,
                "papel": att.role,
                "n": att.n,
                "pid": int((att.proc or {}).get("pid") or 0),
                "vivo": obs == "vivo",
                "observacion": obs,
            }
        )
    return out


def _resumen(retencion: dict, avisos: dict) -> dict:
    return {
        "plan": "",
        "titulo": "",
        "bloques": [],
        "hecho": False,
        "control": "",
        "retencion": retencion,
        "arboles_pendientes": [],
        "efectos_por_conciliar": [],
        "avisos": avisos,
    }


def _estado(home: Path) -> list[dict]:
    from .daemon import planes_de
    from .diagnostico import diagnostico
    from .store import Store

    retencion = _retencion(home)
    avisos = _avisos(home)
    if not (home / "motor.db").exists():
        if retencion is None:
            return []
        return [_resumen(retencion, avisos)]
    try:
        policy = m.Policy.from_conf(load_conf(load_maquina(home).conf))
    except (ConfError, OSError, KeyError):
        policy = m.Policy(
            (), (), {}, {}
        )  # sin conf legible: el motivo sale sin escalera
    store = Store.solo_lectura(
        home / "motor.db"
    )  # sin construir un Daemon: ni candado ni escritura
    try:
        maquina = huella.de_maquina(load_maquina(home))
    except (ConfError, OSError, KeyError):
        maquina = None
    out = []
    for plan in planes_de(store):
        snap = m.fold(plan, store.events(plan.id), maquina)
        bloques = []
        for bid, bv in snap.blocks.items():
            run = bv.running_productivo() or bv.running()
            bloques.append(
                {
                    "bloque": bid,
                    "estado": m.estado(snap, bid).value,
                    "agente": run.modelo if run else "",
                    "papel": run.role if run else "",
                    "motivo": diagnostico(snap, bid, policy, time.time()).motivo,
                }
            )
        out.append(
            {
                "plan": plan.id,
                "titulo": plan.titulo,
                "bloques": bloques,
                "hecho": all(b["estado"] == "hecho" for b in bloques),
                "control": snap.control,
                "retencion": retencion,
                "arboles_pendientes": _arboles(snap, retencion is not None),
                "efectos_por_conciliar": _por_conciliar(store.events(plan.id)),
                "avisos": avisos,
            }
        )
    if not out and retencion is not None:
        return [_resumen(retencion, avisos)]
    return out


def _lee_programadas(home: Path, sistema: bool = True):
    """(lectura de efimeras.lee_tareas, hallazgos): las tareas que contienen una carpeta del motor."""
    # Sin resolve(): una tarea guarda la ruta tal como se escribió; resolver un enlace del home
    # (/var -> /private/var en macOS) haría que nunca matchee.
    home = Path(os.path.abspath(os.path.expanduser(home)))
    usuario = Path.home()
    error = ""
    try:
        mq = load_maquina(home)
        nombre, conf_dir, bash = mq.nombre, mq.conf, mq.bash
    except (ConfError, OSError, KeyError, tomllib.TOMLDecodeError) as e:
        nombre, conf_dir, bash = "", None, ""
        if (home / "maquina.toml").exists():
            error = f"no pude leer maquina.toml: {e!r}"
    try:
        lectura = efimeras.lee_tareas(
            nombre, conf_dir, sistema, home=usuario, bash=bash
        )
    except Exception as e:  # noqa: BLE001 - un plist roto no puede dar exit 1: nada se pudo asegurar
        lectura = ({}, [], {"lectura": f"{type(e).__name__}: {e}"}, {})
    if error:
        lectura[2]["maquina.toml"] = error
    tareas = lectura[0]
    hallazgos = []
    for carpeta in efimeras.CARPETAS_DEL_MOTOR:
        ruta = home / carpeta
        for tarea in efimeras.tareas_con(tareas, ruta, usuario):
            hallazgos.append({"tarea": tarea, "carpeta": str(ruta)})
    return lectura, hallazgos


def programadas(home: Path, sistema: bool = True) -> tuple[dict, int]:
    """Qué tareas programadas apuntan a carpetas que el motor borra, y el código de salida:
    0 limpio, 3 con hallazgos, 4 si ninguna fuente se pudo leer."""
    (_, leidas, sin_leer, negados), hallazgos = _lee_programadas(home, sistema)
    out = {
        "leidas": leidas,
        "sin_leer": [f"{n}: {motivo}" for n, motivo in sin_leer.items()],
        "hallazgos": hallazgos,
    }
    if negados:
        out["negados"] = [f"{n}: {motivo}" for n, motivo in negados.items()]
    return out, 4 if not leidas else 3 if hallazgos else 0


def doctor(home: Path, programadas_sistema: bool = True) -> dict:
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
    # Sin token o sin chat ningún aviso sale: el motor se atora en silencio.
    token_env = conf.avisos.get("token_env", "MOTOR_TELEGRAM_TOKEN")
    if not dict(mq.secretos).get(token_env):
        out["ok"] = False
        out["problemas"].append(f"avisos: falta el token de Telegram ({token_env})")
    if not conf.avisos.get("chat"):
        out["ok"] = False
        out["problemas"].append("avisos: falta el chat de Telegram ([avisos] chat)")
    out["avisos"] = _avisos(home)
    if out["avisos"]["causa"]:
        out["problemas"].append(f"avisos: {out['avisos']['causa']}")
    if out["avisos"]["fallidos"]:
        out["ok"] = False
        out["problemas"].append(f"avisos: {out['avisos']['fallidos']} fallidos")
    if out["avisos"]["desconocidos"]:
        out["problemas"].append(
            f"avisos: {len(out['avisos']['desconocidos'])} desconocidos"
        )
    out["senal"] = "señal a Kuma: " + (
        "configurada" if dict(mq.secretos).get(senal.VARIABLE) else "sin configurar"
    )
    prog, codigo = programadas(home, programadas_sistema)
    if programadas_sistema:
        del_sistema = set(efimeras.nombres_del_sistema(Path.home()))
        ciegas = [x for x in prog["sin_leer"] if x.split(": ", 1)[0] in del_sistema]
        if codigo == 4 or ciegas:
            out["ok"] = False
            out["problemas"].append(
                "no pude leer las tareas programadas del sistema: "
                + "; ".join(ciegas or prog["sin_leer"] or ["ninguna fuente"])
            )
    nombres = sorted({h["tarea"] for h in prog["hallazgos"]})
    out["programadas"] = {"en_carpetas_del_motor": len(nombres), "tareas": nombres}
    if nombres:
        out["ok"] = False
        out["problemas"].append(
            f"{len(nombres)} tareas programadas apuntan a carpetas que el motor borra: "
            + ", ".join(nombres)
        )
    return out


def _errores_programadas(d: Path) -> list[str]:
    f = d / efimeras.ARCHIVO_EXTRAS
    if not f.exists():
        return []
    try:
        raw = tomllib.loads(f.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        return [f"{efimeras.ARCHIVO_EXTRAS}: {e}"]
    maquinas: set[str] = set()
    for m_ in sorted((d / "maquinas").glob("*.toml")):
        maquinas.add(m_.stem)
        try:
            maquinas.add(
                tomllib.loads(m_.read_text(encoding="utf-8")).get("nombre", "")
            )
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return efimeras.valida_programadas(raw, maquinas - {""})


def _falla(paso: str, e: Exception) -> str:
    """Una frase con el motivo; sin el tipo si ya es un error de conf con mensaje."""
    if isinstance(e, ConfError):
        return f"{paso}: {e}"
    return f"{paso}: {type(e).__name__}: {e}"


def _construye(d: Path) -> list[str]:
    """Lo mismo que construye el daemon al cargar la conf: si algo truena, es un error con mensaje."""
    try:
        conf = load_conf(d)
    except Exception as e:  # noqa: BLE001  cualquier excepción de una conf mala es un mensaje, no un traceback
        return [_falla("conf", e)]
    errores = []
    try:
        m.Policy.from_conf(conf)
    except Exception as e:  # noqa: BLE001
        errores.append(_falla("conf (políticas)", e))
    for mo, r in conf.rutas.items():
        if malos := datos_desconocidos(r.argv):
            errores.append(
                f"ruta {mo}: la línea usa {', '.join('{' + x + '}' for x in malos)}, que no es un dato del motor "
                f"({', '.join(DATOS_DE_RUTA)})"
            )
        if not any("{prompt}" in x or "{brief}" in x for x in r.argv):
            errores.append(
                f"ruta {mo}: el comando no recibe el encargo ({{prompt}} o {{brief}})"
            )
    for papel in conf.papeles:
        try:
            if not conf.escalera(papel):
                errores.append(f"papel {papel}: sin modelos")
        except Exception as e:  # noqa: BLE001
            errores.append(_falla(f"papel {papel}", e))
    from .effects import errores_pstack

    for paso, fn in (
        ("pstack", lambda: errores_pstack(d, conf)),
        (efimeras.ARCHIVO_EXTRAS, lambda: _errores_programadas(d)),
    ):
        try:
            errores += fn()
        except Exception as e:  # noqa: BLE001
            errores.append(_falla(paso, e))
    return errores


def conf_valida(d: Path) -> list[str]:
    """Lo que tiene que cumplir la conf compartida antes de llegar a las tres máquinas."""
    errores = _construye(d)
    try:
        for f in (
            sorted((d / "maquinas").glob("*.toml")) if (d / "maquinas").is_dir() else []
        ):
            try:
                valida_maquina(tomllib.loads(f.read_text(encoding="utf-8")))
            except (ConfError, OSError, tomllib.TOMLDecodeError) as e:
                errores.append(f"maquinas/{f.name}: {e}")
        if SECRETO_CONF.search(
            "\n".join(
                p.read_text(errors="replace", encoding="utf-8")
                for p in d.rglob("*.toml")
            )
        ):
            errores.append(
                "hay algo con forma de llave en la conf; las llaves van en secretos.env de cada máquina"
            )
    except Exception as e:  # noqa: BLE001
        errores.append(_falla("conf", e))
    return errores


ESPERA_CONF_S = 60


def _carga_conf(home: Path) -> Path:
    """La carpeta de la conf compartida, ya cargada y con sus políticas armadas, o la excepción."""
    d = load_maquina(home).conf
    m.Policy.from_conf(load_conf(d))
    return d


def _espera(s: float) -> None:
    time.sleep(s)


def _sin_donde_volver(motivo: str) -> int:
    """Deja el error en el log, espera y sale: así el supervisor no relanza un motor roto cada pocos segundos."""
    print(f"motor: {motivo}", file=sys.stderr, flush=True)
    _espera(ESPERA_CONF_S)
    return 1


def _recupera_conf(home: Path) -> tuple[int | None, tuple[str, list[str]] | None]:
    """Antes de crear el Daemon: si la conf compartida no carga, vuelve a la última buena.
    Devuelve (código de salida si no hay a dónde volver, (sha malo, errores) si se volvió)."""
    try:
        load_maquina(home)
    except Exception as e:  # noqa: BLE001  maquina.toml y secretos.env no vienen de la conf compartida
        return _sin_donde_volver(
            f"no pude leer maquina.toml o secretos.env, no hay a dónde volver: {type(e).__name__}: {e}"
        ), None
    try:
        _carga_conf(home)
        return None, None
    except Exception as e:  # noqa: BLE001
        error = _falla("conf", e)
    d = load_maquina(home).conf
    if not (d / ".git").exists():
        return _sin_donde_volver(
            f"la conf no carga y {d} no es un clon de git, no hay a dónde volver: {error}"
        ), None
    try:
        return _vuelve_a_la_buena(home, d, error)
    except Exception as e:  # noqa: BLE001  git o disco fallan: sin dónde volver, no un traceback en bucle
        return _sin_donde_volver(
            f"la conf no carga y no pude volver a la buena ({type(e).__name__}: {e}): {error}"
        ), None


def _vuelve_a_la_buena(home: Path, d: Path, error: str):
    from .workspace import git

    buena = home / "conf-buena"
    sha = buena.read_text(encoding="utf-8").strip() if buena.is_file() else ""
    if not sha:
        return _sin_donde_volver(
            f"la conf no carga y no hay conf-buena a dónde volver: {error}"
        ), None
    malo = git("rev-parse", "HEAD", cwd=d).stdout.strip()
    sucio = git("status", "--porcelain", "--untracked-files=no", cwd=d).stdout.strip()
    if sucio:
        return _sin_donde_volver(
            f"la conf no carga y tiene cambios sin commitear, no la toco: {error}"
        ), None
    if git("reset", "-q", "--hard", sha, cwd=d).returncode != 0:
        return _sin_donde_volver(
            f"la conf no carga y no pude volver a {sha[:12]}: {error}"
        ), None
    try:
        _carga_conf(home)
    except Exception as e:  # noqa: BLE001
        git("reset", "-q", "--hard", malo, cwd=d)
        return _sin_donde_volver(
            f"la conf buena {sha[:12]} tampoco carga: {_falla('conf', e)}"
        ), None
    print(
        f"motor: la conf no cargaba ({error}); volví a la última buena {sha[:12]}",
        file=sys.stderr,
        flush=True,
    )
    return None, (malo, [error])


_CMDS_ENCARGO = {
    "encargar",
    "ver",
    "parar",
    "continuar",
    "cancelar",
    "corregir",
    "efecto",
    "espera",
    "pregunta",
    "afirmacion",
    "parada-local",
    "supervisor",
    "motor06",
    "encargos-aplicar",
}


def main(argv: list[str] | None = None) -> int:
    argv_in = list(sys.argv[1:] if argv is None else argv)
    if argv_in and argv_in[0] in _CMDS_ENCARGO:
        from .encargos.cli import main as encargos_main

        return encargos_main(argv_in)
    ap = argparse.ArgumentParser(prog="motor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("valida")
    s.add_argument("plan", type=Path)
    s.add_argument("--home", type=Path)
    s.add_argument("--previa", action="store_true")
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
    for nombre in ("pausa", "reanuda", "cancela"):
        s = sub.add_parser(nombre)
        s.add_argument("plan")
        s.add_argument("--home", type=Path, required=True)
    s = sub.add_parser("mantenimiento")
    acc = s.add_subparsers(dest="accion", required=True)
    s = acc.add_parser("activar")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--motivo", default="recuperacion")
    s = acc.add_parser("liberar")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--generacion", required=True)
    s = sub.add_parser("doctor")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--json", action="store_true")
    s.add_argument("--como-servicio", action="store_true")
    s.add_argument("--espera", type=float, default=60)
    s = sub.add_parser("programadas")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("instala")
    s.add_argument("--maquina", required=True)
    s.add_argument("--usuario", required=True)
    s.add_argument("--home", required=True)
    s.add_argument("--imprime", action="store_true")
    s = sub.add_parser("formato")
    s.add_argument("--home", type=Path, required=True)
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
        if a.cmd == "entrega" or getattr(a, "previa", False):
            from .previa import corre_previa

            errores_previa = corre_previa(plan, a.home)
            if errores_previa:
                if getattr(a, "json", False):
                    print(
                        json.dumps(
                            {"estado": "rechazado", "errores": errores_previa},
                            ensure_ascii=False,
                        )
                    )
                else:
                    for x in errores_previa:
                        print(x)
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
    if a.cmd in ("pausa", "reanuda", "cancela"):
        print(_encola(a.home, {"tipo": a.cmd, "plan": a.plan}))
        return 0
    if a.cmd == "mantenimiento":
        if a.accion == "activar":
            from .control import activar

            solicitud = uuid.uuid4().hex
            hold = activar(a.home, solicitud, a.motivo)
            detalle = hold.cause or f"retenido {hold.generation}"
            _guarda_recibo(
                a.home,
                solicitud,
                "aplicada",
                detalle,
                {"generacion": hold.generation},
            )
            print(f"{detalle} (recibo {solicitud})")
            return 0
        print(_encola(a.home, {"tipo": "liberar", "generacion": a.generacion}))
        return 0
    if a.cmd == "estado":
        est = _estado(a.home)
        if a.json:
            print(json.dumps(est, ensure_ascii=False))
        else:
            for p in est:
                if not p["plan"]:
                    continue
                marca = ""
                if p.get("control") not in (None, "", "Activo"):
                    marca = f" [{p['control']}]"
                print(f"{p['plan']}: {p['titulo']}{marca}")
                for b in p["bloques"]:
                    print(
                        f"  {b['bloque']:<4} {b['estado']:<32} {b['agente']:<8} {b['motivo']}"
                    )
            retenidos = [p["retencion"] for p in est if p.get("retencion")]
            if retenidos:
                r = retenidos[0]
                causa = f" ({r['causa']})" if r.get("causa") else ""
                print(f"retención {r['generacion']}{causa}")
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
    if a.cmd == "programadas":
        out, codigo = programadas(a.home)
        if a.json:
            print(json.dumps(out, ensure_ascii=False))
        else:
            for h in out["hallazgos"]:
                print(f"{h['tarea']} apunta a {h['carpeta']}")
            for x in out["sin_leer"]:
                print(f"sin leer: {x}")
            for x in out.get("negados", []):
                print(f"negado: {x}")
            print(
                f"{len(out['hallazgos'])} tareas en carpetas del motor; "
                f"{len(out['leidas'])} fuentes leídas"
            )
        return codigo
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
    if a.cmd == "formato":
        from .formato import puede_volver
        from .store import Store
        db = a.home / "motor.db"
        eventos = list(Store.solo_lectura(db).events()) if db.exists() else []
        ok, motivo = puede_volver(a.home, eventos)
        print(motivo)
        return 0 if ok else 1
    if a.cmd == "daemon":
        from .control import leer
        from .daemon import Daemon, OtroMotor
        from .formato import FormatoIncompatible

        hold = leer(a.home)
        salida, vuelta = _recupera_conf(a.home)
        if salida is not None:
            if hold is not None:
                print(
                    "motor: el home sigue retenido; la configuración no alcanzó para arrancar el daemon",
                    file=sys.stderr,
                )
            return salida
        try:
            d = Daemon(a.home, a.checks_cada)
        except OtroMotor as e:
            print(f"motor: {e}", file=sys.stderr)
            return 1
        except FormatoIncompatible as e:
            print(f"motor: {e}", file=sys.stderr)
            return 1
        except Exception as e:  # noqa: BLE001  un fallo al armar el Daemon no debe relanzarse cada pocos segundos
            return _sin_donde_volver(f"no pude armar el motor: {_falla('motor', e)}")
        if vuelta:
            d.avisa_conf_rechazada(*vuelta)
        return d.run(a.intervalo, a.hasta_terminar, a.tope)
    if a.cmd == "servicio":
        from .servicio import supervisor

        return supervisor(a.home)
    return 1


if __name__ == "__main__":
    sys.exit(main())
