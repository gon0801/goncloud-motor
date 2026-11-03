"""Turno real y corto de cada cliente de suscripción sobre una candidata de flota.

Antes de activar una generación, el controlador corre en ella el mismo camino
del motor: el adaptador de agentes.py, el lanzamiento directo y la suscripción
del usuario, con un prompt que solo pide responder una palabra. Un agente sin
sesión en la máquina se reporta y no bloquea; uno que funciona en la vigente y
falla en la candidata bloquea la activación y avisa al usuario.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from pathlib import Path
from shutil import rmtree
from types import SimpleNamespace

from motor.encargos.agentes import (
    _lanzador,
    _modelos_para,
    _sesion_presente,
    resolver_lanzador,
)
from motor.encargos.aislamiento import aislar
from motor.flota.control import _atomico, asignar
from motor.flota.runtime import vincular

TOPE_TURNO_S = 150
PROMPT_TURNO = "Responde con una sola palabra: listo.\n"
_RECORTE = 500


def _gen_vigente(home: Path) -> Path | None:
    """La generación activa, o None si el home todavía no tiene una."""
    try:
        gen = Path(home) / "generaciones" / asignar(Path(home))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return None
    return gen if gen.is_dir() else None


def _recorte(texto: str) -> str:
    return " ".join(str(texto or "").split())[:_RECORTE]


def _turno_agente(
    agente: str, gen: Path, raiz: Path, tope_s: int = TOPE_TURNO_S
) -> dict:
    """Un turno de una palabra con el mismo camino de lanzamiento del motor.

    El espacio vive en <raiz>/corriendo/<agente>-<marca>/espacio/<agente> para
    cumplir el acceso de aislar, y el runtime aparte guarda la copia de la
    suscripción; los dos se borran al terminar el turno.
    """
    marca = uuid.uuid4().hex[:8]
    scratch = Path(raiz) / "corriendo" / f"{agente}-{marca}"
    espacio = scratch / "espacio" / agente
    runtime = scratch / "runtime"
    espacio.mkdir(parents=True, exist_ok=True)
    inicio = time.time()
    try:
        prompt = espacio / "prompt.txt"
        prompt.write_text(PROMPT_TURNO, encoding="utf-8")
        enc = SimpleNamespace(
            rol="responsable", sesion_responsable="", sesion_revisor="", modelos=[]
        )
        modelos = _modelos_para(agente, "responsable", gen)
        cmd = _lanzador(agente, modelos[0], "nueva")(
            enc, prompt, espacio, str(uuid.uuid4())
        )
        cmd = vincular(gen, cmd, funcion="codigo")
        comando = aislar(
            cmd,
            espacio,
            runtime,
            suscripcion=True,
            lecturas_extra=(gen,),
            agente=agente,
            interprete=True,
        )
        hecho = subprocess.run(
            comando.argv,
            cwd=str(espacio),
            env=comando.entorno,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=tope_s,
            check=False,
        )
        salida, error, rc = hecho.stdout or "", hecho.stderr or "", hecho.returncode
    except subprocess.TimeoutExpired as exc:
        salida, error, rc = "", f"el turno excedio el tope de {tope_s}s: {exc}", 124
    except OSError as exc:
        salida, error, rc = "", f"no se pudo lanzar el turno: {exc}", 127
    finally:
        rmtree(scratch, ignore_errors=True)
    segundos = round(time.time() - inicio, 1)
    texto = salida.strip() or error.strip()
    combinado = "\n".join(x for x in (salida.strip(), error.strip()) if x)
    resultado: dict = {"segundos": segundos, "rc": rc}
    if rc == 0 and texto:
        resultado.update({"estado": "ok", "salida": _recorte(texto)})
    else:
        # El error literal puede estar en cualquier stream: codex imprime su
        # causa en stdout y el aviso de stdin en stderr.
        literal = combinado[:_RECORTE]
        resultado.update(
            {
                "estado": "fallo",
                "error": literal or f"el turno termino sin salida (rc {rc})",
            }
        )
    return resultado


def turnos_candidata(
    gen: Path,
    home: Path,
    tope_s: int = TOPE_TURNO_S,
    agentes=None,
) -> dict:
    """El informe de turnos reales de la candidata.

    Cada agente del catálogo con sesión en esta máquina corre un turno real en
    `gen`. Un fallo se contrasta con la generación vigente: solo bloquea si el
    turno pasa en la vigente y falla en la candidata; si falla en ambas queda
    reportado como fallo común de la máquina o de la cuenta. El informe queda
    en home/turnos/<generacion>.json.
    """
    from motor.encargos.catalogo_agentes import ADAPTADORES

    gen, home = Path(gen).resolve(), Path(home).resolve()
    nombres = list(agentes) if agentes else sorted(ADAPTADORES)
    vigente = _gen_vigente(home)
    turnos: dict[str, dict] = {}
    for agente in nombres:
        if agente not in ADAPTADORES:
            continue
        if not _sesion_presente(agente):
            turnos[agente] = {"estado": "sin_sesion"}
            continue
        argv_candidata = resolver_lanzador(agente, gen)
        argv_vigente = resolver_lanzador(agente, vigente)
        if argv_candidata is None:
            if vigente is None or argv_vigente is None:
                turnos[agente] = {
                    "estado": "sin_ejecutable",
                    "error": (
                        f"{agente} no tiene ejecutable ni en la candidata "
                        "ni en la vigente"
                    ),
                }
            else:
                turnos[agente] = {
                    "estado": "fallo",
                    "error": (
                        f"la candidata retira el ejecutable de {agente} "
                        "que la generación vigente sí trae"
                    ),
                }
            continue
        resultado = _turno_agente(agente, gen, home / "turnos", tope_s)
        comparable = vigente is not None and argv_vigente is not None
        if not comparable and resultado.get("estado") == "fallo":
            resultado["estado"] = "fallo_comun"
            resultado["nota"] = (
                "sin vigente con ejecutable para este agente no hay contraste: "
                "no funcionaba en ella"
            )
        if resultado.get("estado") != "fallo":
            turnos[agente] = resultado
            continue
        control = _turno_agente(agente, vigente, home / "turnos", tope_s)
        if control.get("estado") == "ok":
            turnos[agente] = resultado
        else:
            turnos[agente] = {
                "estado": "fallo_comun",
                "error": resultado.get("error", ""),
                "nota": "el mismo turno también falla en la generación vigente",
                "vigente": control.get("error", ""),
                "segundos": resultado.get("segundos", 0),
            }
    fallidos = [a for a, t in turnos.items() if t.get("estado") == "fallo"]
    informe = {
        "generacion": gen.name,
        "hora": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "maquina": socket.gethostname(),
        "turnos": turnos,
        "fallidos": fallidos,
    }
    _atomico(home / "turnos" / f"{gen.name}.json", informe)
    return informe


def avisar_rechazo(destino: str, informe: dict, home_motor: Path | None = None) -> None:
    """El aviso del motor con el agente y el error literal de cada turno fallido.

    La cola es la del motor instalado (Notifier la drena y envía por Telegram);
    escribir la cola no necesita credenciales y un fallo aquí no cambia el
    rechazo: la causa ya quedó en candidata.json.
    """
    base = Path(home_motor) if home_motor is not None else _home_motor()
    try:
        from motor.notify import Notifier

        avisos = Notifier(base, "", "", "")
        for agente in informe.get("fallidos", []):
            turno = (informe.get("turnos") or {}).get(agente) or {}
            error = str(turno.get("error") or "")[:700]
            texto = (
                "En la flota de esta máquina no se activó una actualización.\n"
                f"El agente {agente} no pasó su turno de prueba: {error}\n"
                "Sigo trabajando con la generación anterior; la candidata quedó preparada."
            )[:4000]
            ident = f"flota-turno-{str(destino)[:12]}-{agente}"
            avisos.materializa(ident, texto, ident)
    except OSError:
        pass


def _home_motor() -> Path:
    """El home del motor instalado de esta máquina, si se puede resolver."""
    entorno = os.environ.get("MOTOR_HOME", "")
    if entorno.strip():
        return Path(entorno).expanduser()
    return Path.home() / ".motor"
