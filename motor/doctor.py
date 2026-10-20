"""`motor doctor --como-servicio`: ¿cada ruta responde en el entorno donde corre el servicio?

Lo que funciona en tu terminal puede fallar bajo launchd o la tarea programada: otro PATH, sin llavero,
sin variables de tu shell. Aquí cada ruta corre un encargo de prueba (de 1 minuto como máximo) con lo
mismo que verá el servicio: stdin nulo, el entorno de `maquina.toml` + `secretos.env`, y
PATH = `path_extra` + el PATH del servicio (no el de quien teclea).
"""

from __future__ import annotations

import os
import string
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import fallas, plataforma
from .conf import ConfError, Maquina, Ruta, load_conf, load_maquina
from .servicio import PATH_SERVICIO

ESPERA_S = 60.0
ENCARGO = (
    "Esto es una prueba de que respondes, no un encargo. Escribe en $MOTOR_RESULT el JSON "
    '{"ok": true} y contesta "OK". No toques nada más.'
)


def entorno_del_servicio(mq: Maquina, ruta: Ruta, result: Path) -> dict[str, str]:
    """Lo que ve un agente lanzado por el servicio: no hereda nada de la terminal de quien corre el doctor."""
    path = os.pathsep.join(
        [
            *mq.path_extra,
            PATH_SERVICIO if os.name == "posix" else os.environ.get("PATH", ""),
        ]
    )
    base = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": path,
        **dict(mq.env),
        **dict(mq.secretos),
    }
    de_ruta = {k: string.Template(v).safe_substitute(base) for k, v in ruta.env}
    return {
        **base,
        **de_ruta,
        "MOTOR_RESULT": str(result),
        "MOTOR_MODEL": ruta.modelo,
        "MOTOR_ROLE": "prueba",
    }


def prueba_de_ruta(mq: Maquina, ruta: Ruta, espera_s: float = ESPERA_S) -> dict:
    """Corre el encargo de prueba de una ruta. `responde` = salió bien dentro del plazo y dijo algo."""
    inicio = time.time()
    with tempfile.TemporaryDirectory(prefix="motor-doctor-") as t:
        d = Path(t)
        brief, result = d / "brief.md", d / "result.json"
        brief.write_text(ENCARGO + "\n")
        argv = [
            x.format(brief=str(brief), prompt=ENCARGO, result=str(result))
            for x in ruta.argv
        ]
        if ruta.cli in mq.wsl:
            argv = ["wsl.exe", "-e", *argv]
        env = entorno_del_servicio(mq, ruta, result)
        try:
            p = subprocess.Popen(
                argv,
                cwd=d,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                **plataforma.kwargs_grupo(),
            )
        except OSError as e:
            return {
                "responde": False,
                "segundos": 0.0,
                "motivo": f"no pude lanzarlo: {e}",
                "kind": "falla",
            }
        try:
            salida, _ = p.communicate(timeout=espera_s)
        except subprocess.TimeoutExpired:
            plataforma.matar_grupo(p)
            p.communicate()
            return {
                "responde": False,
                "segundos": round(time.time() - inicio, 1),
                "kind": "tiempo",
                "motivo": f"no contestó en {espera_s:g} s",
            }
        segundos = round(time.time() - inicio, 1)
        if p.returncode != 0:
            c = fallas.clasifica(_proveedor(ruta), "", salida)
            return {
                "responde": False,
                "segundos": segundos,
                "kind": c["kind"],
                "motivo": c["detalle"][-200:],
            }
        if not salida.strip() and not result.exists():
            return {
                "responde": False,
                "segundos": segundos,
                "kind": "falla",
                "motivo": "salió bien pero no dijo nada",
            }
        return {"responde": True, "segundos": segundos, "kind": "ok", "motivo": ""}


def _proveedor(ruta: Ruta) -> str:
    return ruta.cuenta if ruta.cuenta in fallas.PROVEEDORES else ruta.cli


def como_servicio(home: Path, espera_s: float = ESPERA_S) -> dict:
    """Prueba todas las rutas a la vez y dice cuáles responden. `ok` si cada papel tiene al menos una."""
    try:
        mq = load_maquina(home)
        conf = load_conf(mq.conf)
    except (ConfError, OSError, KeyError) as e:
        return {"ok": False, "problemas": [str(e)], "rutas": {}}
    modelos = list(conf.rutas)
    with ThreadPoolExecutor(max_workers=max(1, len(modelos))) as pool:
        pruebas = dict(
            zip(
                modelos,
                pool.map(
                    lambda mo: prueba_de_ruta(mq, conf.rutas[mo], espera_s), modelos
                ),
            )
        )
    out: dict = {
        "ok": True,
        "maquina": mq.nombre,
        "path": os.pathsep.join([*mq.path_extra, PATH_SERVICIO]),
        "rutas": pruebas,
        "problemas": [],
    }
    for mo, r in pruebas.items():
        if not r["responde"]:
            out["problemas"].append(f"{mo}: {r['kind']}: {r['motivo']}".strip())
    for papel in conf.papeles:
        if not any(pruebas[mo]["responde"] for mo in conf.escalera(papel)):
            out["ok"] = False
            out["problemas"].append(
                f"ningún modelo del papel {papel} responde como servicio"
            )
    return out


if __name__ == "__main__":
    print(como_servicio(Path(sys.argv[1])))
