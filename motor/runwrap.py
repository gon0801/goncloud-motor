"""Corre UN agente sin ventana y deja su resultado en disco. Vive aparte del daemon.

    python -m motor.runwrap --dir <intento> --cwd <carpeta> --timeout S --sin-avance S
        [--wip-rama <rama> --wip-cada S] [--home H --cuenta C] -- <argv del agente...>

- stdin cerrado y grupo de procesos propio: ningún prompt de permiso puede colgarlo.
- Falta de avance: ni el log ni la carpeta cambian en `--sin-avance` s -> se detiene.
- Ya entregó: si escribió una entrega válida (`motor.entrega`) y en `--cierre` s no vuelve a
  tocar nada, se cierra y cuenta como terminado. Un `result.json` que no es entrega válida no
  lo cierra: sigue trabajando. La última entrega válida se copia a `entrega.json`; un
  `result.json` que no es válido queda en `result.ajeno.json`. Con `pstack.json`, también
  cuenta como avance cualquier cambio en sus `registros` y en los de los subagentes de la
  sesión (`<sesion>/subagents/*.jsonl`), y no cierra mientras una llamada de `llamadas/`
  tenga `fin` nulo: con pid, si su shim sigue vivo; sin pid o con pid inválido, siempre
  (una abierta cuenta como avance: el cierre llega `--cierre` s después de llenarse `fin`).
  Esas fuentes solo retienen el cierre: si `--sin-avance` o `--timeout` vencen con una
  entrega válida ya congelada, el corte se anota tal cual en `outcome.json` y es
  `effects._juzga_intento` el que rescata la entrega congelada, para que el veredicto no
  se pierda. Grok dejaba su resultado y el proceso no salía; a los 45 min se mataba "sin
  avance" y la entrega se perdía (INGESTA 01, bloque D, dos veces).
- Con `--wip-rama`: cada `--wip-cada` s y al terminar, sube una foto del trabajo a medias
  (ver workspace.foto_wip). Un corte de luz pierde a lo mucho esos segundos.
- Con `--home` y `--cuenta`: anota al agente en el libro de cuentas (motor/llamadas.py), para que
  los shims de los CLIs de modelos cuenten su lugar en los topes.
- Escribe `outcome.json` con rename atómico. Si el daemon muere, este proceso sigue; si la
  máquina se apaga, el daemon nuevo ve un arranque distinto y lo marca "corte".
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import entrega, llamadas, plataforma
from .plataforma import matar_grupo
from .workspace import foto_wip

_SALTA = {".git", ".venv", "node_modules", "__pycache__"}


def _mtime_arbol(raiz: Path) -> float:
    m = 0.0
    for dirpath, dirnames, filenames in os.walk(raiz):
        dirnames[:] = [d for d in dirnames if d not in _SALTA]
        for f in filenames:
            try:
                m = max(m, os.stat(os.path.join(dirpath, f)).st_mtime)
            except OSError:
                pass
    return m


def _huella_arbol(raiz: Path) -> dict[str, tuple[int, int]]:
    """Archivo -> (mtime en ns, tamaño): con dos fotos se ve si algo cambió, aunque haya sido en 1 s."""
    huella: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(raiz):
        dirnames[:] = [d for d in dirnames if d not in _SALTA]
        for f in filenames:
            ruta = os.path.join(dirpath, f)
            try:
                st = os.stat(ruta)
            except OSError:
                continue
            huella[os.path.relpath(ruta, raiz)] = (st.st_mtime_ns, st.st_size)
    return huella


def _huella_pstack(d: Path) -> tuple[dict[str, tuple[int, int]], bool]:
    """(archivo -> (mtime en ns, tamaño), hay una llamada sin `fin`: sin pid o con pid inválido
    cuenta siempre; con pid, solo si su shim sigue vivo) según el `pstack.json` del intento.
    Se mira cada archivo, no su carpeta: agregar líneas a un archivo no mueve la hora de la carpeta."""
    try:
        registros = json.loads((d / "pstack.json").read_text(encoding="utf-8"))[
            "registros"
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return {}, False
    archivos: list[str] = []
    for r in registros if isinstance(registros, list) else []:
        if not isinstance(r, str):
            continue
        archivos.append(r)
        sesion = r[: -len(".jsonl")] if r.endswith(".jsonl") else ""
        if sesion:
            for dirpath, _, nombres in os.walk(sesion):
                archivos.extend(os.path.join(dirpath, n) for n in nombres)
    huella: dict[str, tuple[int, int]] = {}
    for ruta in archivos:
        try:
            st = os.stat(ruta)
        except OSError:
            continue
        huella[ruta] = (st.st_mtime_ns, st.st_size)
    abierta = False
    for f in (d / "llamadas").glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not (isinstance(rec, dict) and "fin" in rec and rec["fin"] is None):
            continue
        pid = rec.get("pid")
        if not isinstance(pid, int) or pid <= 0 or plataforma.pid_vivo(pid):
            abierta = True
    return huella, abierta


def _sin_shims(cmd: list[str], env: dict) -> list[str]:
    """El agente se lanza con su CLI real: con los shims primero en el PATH, `claude` caería en el shim.
    Se quita toda carpeta de shims del motor, no solo la de este runwrap: el agente hereda el PATH
    del motor instalado."""
    path = os.pathsep.join(
        p
        for p in env.get("PATH", "").split(os.pathsep)
        if not os.path.isfile(os.path.join(p or ".", "llamada_guard.py"))
    )
    env_real = {**env, "PATH": path}
    if (
        os.name == "nt"
    ):  # los CLIs de npm son `.cmd`: CreateProcess no los ejecuta directo
        from .plataforma.windows import argv_windows

        return argv_windows(cmd, env_real)
    real = shutil.which(cmd[0], path=path) if cmd else None
    return [real, *cmd[1:]] if real else cmd


def _publica(ruta: Path, texto: str) -> None:
    tmp = ruta.with_name(ruta.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)


def _outcome(d: Path, datos: dict) -> None:
    _publica(d / "outcome.json", json.dumps(datos, ensure_ascii=False))
    plataforma.sincroniza_directorio(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--timeout", type=float, required=True)
    ap.add_argument("--sin-avance", type=float, required=True)
    ap.add_argument("--cierre", type=float, default=120)
    ap.add_argument("--wip-rama", default="")
    ap.add_argument("--wip-cada", type=float, default=300)
    ap.add_argument(
        "--wip-local",
        action="store_true",
        help="repo público: la foto no sale de esta máquina",
    )
    ap.add_argument("--home", default="")
    ap.add_argument("--cuenta", default="")
    ap.add_argument("--entrega-rama", default="", help="rama del bloque en origin")
    ap.add_argument("--entrega-sha", default="", help="sha del encargo, papel revisor")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if a.home:
        from .formato import FormatoIncompatible, activa, admite_escritor

        try:
            admite_escritor(Path(a.home))
            activa(Path(a.home))
        except FormatoIncompatible as e:
            print(f"motor: {e}", file=sys.stderr)
            return 1
    d, cwd = Path(a.dir), Path(a.cwd)
    # Un segundo envoltorio del mismo intento no escribe resultado ni arranca otro agente.
    candado = plataforma.candado(d / "intento.lock")
    if candado is None:
        return 0
    log = d / "log.txt"
    errores = d / "stderr.txt"
    fotos: list[str] = []
    antes = _huella_arbol(cwd)
    inicio = time.time()
    _publica(
        d / "wrapper.json",
        json.dumps(
            {
                "pid": os.getpid(),
                "inicio": plataforma.hora_inicio(os.getpid()),
                "boot_id": plataforma.boot_id(),
                "token": os.environ.get("MOTOR_LAUNCH_TOKEN", ""),
                "job": "",
            }
        ),
    )
    plataforma.sincroniza_directorio(d)
    fallo_creacion: BaseException | None = None
    p = None
    with open(log, "ab") as out, open(errores, "ab") as err:
        env_agente = {**os.environ, "MOTOR_ATTEMPT_DIR": str(d)}
        cmd = _sin_shims(cmd, env_agente)
        try:
            creado = plataforma.crear_proceso(
                cmd,
                plataforma.Propiedad.COMANDO_CONTENIDO,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                env=env_agente,
            )
            p = creado.proceso
            # Identidad antes de soltar el proceso: en Windows sigue suspendido, ya dentro del Job.
            _publica(d / "agent.inicio", plataforma.hora_inicio(p.pid))
            _publica(d / "agent.pid", str(p.pid))
            if creado.job:
                _publica(d / "agent.job", creado.job)
            plataforma.sincroniza_directorio(d)
            creado.liberar()
        except OSError as e:
            fallo_creacion = e
        if fallo_creacion is None and p is not None:
            clave = f"agente-{os.getpid()}"
            if a.home and a.cuenta:
                llamadas.registra(
                    Path(a.home),
                    clave,
                    {
                        "tipo": "agente",
                        "cuenta": a.cuenta,
                        "pid": os.getpid(),
                        "inicio": inicio,
                    },
                )
            ultimo = (log.stat().st_size, errores.stat().st_size, _mtime_arbol(cwd))
            ultimo_cambio = ultimo_escaneo = time.time()
            huella_p, _ = _huella_pstack(d)
            ultimo_cambio_p = 0.0
            abierta_antes = False
            timed_out = no_progress = cerrado = False
            espera = entrega.Espera(rama=a.entrega_rama, sha=a.entrega_sha)
            foto_lock = threading.Lock()
            foto_stop = threading.Event()

            def _foto() -> None:
                with foto_lock:
                    try:
                        fotos.append(foto_wip(cwd, a.wip_rama, a.wip_local))
                    except Exception as e:  # noqa: BLE001
                        fotos.append(f"error: {e}")

            def _fotos_periodicas() -> None:
                while not foto_stop.wait(a.wip_cada):
                    _foto()

            if a.wip_rama:
                threading.Thread(target=_fotos_periodicas, daemon=True).start()
            while p.poll() is None:
                time.sleep(0.2)
                if time.time() - ultimo_escaneo >= 10:
                    ultimo_escaneo = time.time()
                    ahora = (log.stat().st_size, errores.stat().st_size, _mtime_arbol(cwd))
                    if ahora != ultimo:
                        ultimo, ultimo_cambio = ahora, time.time()
                entrego = espera.vigila(d, cwd)
                nueva_p, llamada_abierta = _huella_pstack(d)
                if nueva_p != huella_p or llamada_abierta or abierta_antes:
                    huella_p, ultimo_cambio_p = nueva_p, time.time()
                abierta_antes = llamada_abierta
                if (
                    entrego >= inicio
                    and time.time() - max(entrego, ultimo_cambio, ultimo_cambio_p)
                    > a.cierre
                ):
                    cerrado = True
                    matar_grupo(p)
                    break
                if time.time() - inicio > a.timeout:
                    timed_out = True
                elif time.time() - ultimo_cambio > a.sin_avance:
                    no_progress = True
                if timed_out or no_progress:
                    matar_grupo(p)
                    break
            espera.vigila(d, cwd)
            if a.home and a.cuenta:
                llamadas.quita(Path(a.home), clave)
            if a.wip_rama:
                foto_stop.set()
                hilo_final = threading.Thread(target=_foto, daemon=True)
                hilo_final.start()
                hilo_final.join(timeout=5)
            _outcome(
                d,
                {
                    "exit": 0 if cerrado else p.returncode,
                    "cerrado_tras_resultado": cerrado,
                    "timed_out": timed_out,
                    "no_progress": no_progress,
                    "tail": log.read_text(errors="replace", encoding="utf-8")[-2000:],
                    "stderr_tail": errores.read_text(errors="replace", encoding="utf-8")[-2000:],
                    "fotos": fotos[-5:],
                    "segundos": round(time.time() - inicio, 1),
                    "toco_carpeta": _huella_arbol(cwd) != antes,
                },
            )
            return 0
    linea = (str(fallo_creacion).splitlines() or ["no pude crear el agente"])[0][:240]
    _outcome(
        d,
        {
            "creacion_agente": "fallida",
            "exit": None,
            "cerrado_tras_resultado": False,
            "timed_out": False,
            "no_progress": False,
            "tail": "",
            "stderr_tail": linea,
            "fotos": [],
            "segundos": round(time.time() - inicio, 1),
            "toco_carpeta": False,
            "error": {
                "tipo": type(fallo_creacion).__name__ if fallo_creacion else "OSError",
                "winerror": getattr(fallo_creacion, "winerror", None),
                "errno": getattr(fallo_creacion, "errno", None),
                "mensaje": linea,
            },
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
