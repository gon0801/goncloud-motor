"""El ejecutor manda una operación a la autoridad. Si no hay conexión, no sale la escritura."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from .frontera import anotar_local


def enviar(
    host: str,
    argv_remoto: list[str],
    mensaje: dict,
    buzon: Path,
    puerto: int | None = None,
    timeout: float = 30,
) -> dict:
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if puerto is not None:
        cmd.extend(["-p", str(puerto)])
    cmd.append(host)
    cmd.extend(argv_remoto)
    if os.name == "nt":
        return _enviar_archivos(cmd, mensaje, buzon, timeout)
    try:
        corrida = subprocess.run(
            cmd,
            input=json.dumps(mensaje),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        anotar_local(buzon, {"op": mensaje.get("op"), "motivo": "autoridad inalcanzable", "error": str(exc)})
        return {"alcanzada": False, "motivo": str(exc)}
    return _respuesta(corrida.returncode, corrida.stdout or "", corrida.stderr or "", mensaje, buzon)


def _enviar_archivos(cmd: list[str], mensaje: dict, buzon: Path, timeout: float) -> dict:
    # ssh.exe en Windows no termina si su salida es un pipe, ni cierra el stdin de uno.
    # Un archivo sí entrega EOF y el proceso acaba.
    buzon.mkdir(parents=True, exist_ok=True)
    marca = uuid.uuid4().hex[:12]
    entrada = buzon / f"envio-{marca}.json"
    salida = buzon / f"envio-{marca}.out"
    error = buzon / f"envio-{marca}.err"
    try:
        entrada.write_bytes(json.dumps(mensaje).encode("utf-8"))
        orden = (
            f"{subprocess.list2cmdline(cmd)} < {_comilla(entrada)} > {_comilla(salida)} 2> {_comilla(error)}"
        )
        proc = subprocess.Popen(orden, shell=True, stdin=subprocess.DEVNULL)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                text=True,
                check=False,
            )
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            anotar_local(
                buzon,
                {"op": mensaje.get("op"), "motivo": "autoridad inalcanzable", "error": "TimeoutExpired"},
            )
            return {"alcanzada": False, "motivo": "TimeoutExpired"}
        texto = salida.read_text(encoding="utf-8", errors="replace") if salida.exists() else ""
        err = error.read_text(encoding="utf-8", errors="replace") if error.exists() else ""
        return _respuesta(proc.returncode or 0, texto, err, mensaje, buzon)
    except OSError as exc:
        anotar_local(buzon, {"op": mensaje.get("op"), "motivo": "autoridad inalcanzable", "error": str(exc)})
        return {"alcanzada": False, "motivo": str(exc)}
    finally:
        for path in (entrada, salida, error):
            path.unlink(missing_ok=True)


def _comilla(path: Path) -> str:
    return '"' + str(path) + '"'


def _respuesta(rc: int, texto: str, err: str, mensaje: dict, buzon: Path) -> dict:
    salida = texto.strip()
    if rc != 0 and not salida:
        motivo = (err + texto)[-400:]
        anotar_local(buzon, {"op": mensaje.get("op"), "motivo": "autoridad inalcanzable", "error": motivo})
        return {"alcanzada": False, "motivo": motivo}
    try:
        datos = json.loads(salida)
    except json.JSONDecodeError:
        anotar_local(buzon, {"op": mensaje.get("op"), "motivo": "respuesta no json", "error": salida[-400:]})
        return {"alcanzada": False, "motivo": salida[-400:]}
    return {"alcanzada": True, "salida": datos, "rc": rc}
