"""Windows (la PC de Hermes). Portado de las ideas de goncloud-hermes subscriptions/process.py:
Job object con "matar al cerrar" para el árbol del agente, resolución de shims .cmd de npm y
grupo de procesos propio que se separa del Job del servicio.

SIN PROBAR EN WINDOWS. En la Mac solo se prueban las partes puras: `resolve_shim`,
`flags_creacion` y `tarea_xml`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def flags_creacion() -> int:
    """Grupo propio, sin ventana, y fuera del Job del servicio (si la tarea lo permite)."""
    return CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB


def resolve_shim(nombre: str, path: str, pathext: str = ".COM;.EXE;.BAT;.CMD") -> list[str]:
    """Los CLIs de npm en Windows son `x.cmd`: CreateProcess no los ejecuta directo.
    Devuelve el prefijo de argv que sí funciona."""
    exts = [e.lower() for e in pathext.split(";") if e]
    for d in path.split(";"):
        if not d:
            continue
        for ext in ("",) + tuple(exts):
            cand = Path(d) / f"{nombre}{ext}"
            if cand.is_file():
                if cand.suffix.lower() in (".cmd", ".bat"):
                    return [os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"), "/d", "/s", "/c", str(cand)]
                return [str(cand)]
    return [nombre]


def tarea_xml(usuario: str, python: str, home: str) -> str:
    """Tarea programada: al ARRANCAR, aunque nadie inicie sesión (contraseña guardada en
    Windows), sin límite de tiempo, reintentar si falla."""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>motor de planes (siempre encendido)</Description></RegistrationInfo>
  <Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{usuario}</UserId><LogonType>Password</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <AllowStartOnDemand>true</AllowStartOnDemand>
  </Settings>
  <Actions Context="Author"><Exec><Command>{python}</Command><Arguments>-m motor servicio --home "{home}"</Arguments></Exec></Actions>
</Task>
"""


def boot_id_sistema() -> str:  # pragma: no cover
    import ctypes
    import time
    ms = ctypes.windll.kernel32.GetTickCount64()
    return str(int(time.time() - ms / 1000) // 10)   # hora de arranque, redondeada a 10 s


def kwargs_grupo() -> dict:  # pragma: no cover
    return {"creationflags": flags_creacion()}


def pid_vivo(pid: int) -> bool:  # pragma: no cover
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def matar_grupo(p: subprocess.Popen) -> None:  # pragma: no cover
    """Termina el árbol completo (taskkill /T); el Job kill-on-close es el respaldo."""
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    p.wait()
