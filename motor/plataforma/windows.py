"""Windows (la PC de Hermes). Portado de goncloud-hermes subscriptions/process.py:
Job object con "matar al cerrar" para el árbol del agente, terminación del árbol con taskkill,
shims .cmd de npm, grupo de procesos propio que se separa del Job del servicio y el guardián
que corre dentro de WSL (un Job de Windows no contiene procesos de Linux).

En la Mac se prueban las partes puras (`resolve_shim`, `flags_creacion`, `tarea_xml`,
`argv_wsl`, `GUARDIAN_WSL`); lo demás lo prueba `tests/test_windows.py` en la PC
(`ops/prueba-pc.sh`).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
CREATE_SUSPENDED = 0x00000004
DETACHED_PROCESS = 0x00000008


def flags_creacion() -> int:
    """Grupo propio, sin ventana, y fuera del Job del servicio (si la tarea lo permite)."""
    return CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB


def resolve_shim(
    nombre: str, path: str, pathext: str = ".COM;.EXE;.BAT;.CMD"
) -> list[str]:
    """Los CLIs de npm en Windows son `x.cmd`: CreateProcess no los ejecuta directo.
    Devuelve el prefijo de argv que sí funciona."""
    exts = [e.lower() for e in pathext.split(";") if e]
    for d in path.split(";"):
        if not d:
            continue
        # primero con extensión: npm deja junto a `x.cmd` un `x` sin extensión (script de bash)
        # que Windows no puede ejecutar (MOTOR 01, PC: GLM y Qwen no arrancaban)
        for ext in tuple(exts) + ("",):
            cand = Path(d) / f"{nombre}{ext}"
            if cand.is_file():
                if cand.suffix.lower() in (".cmd", ".bat"):
                    return [
                        os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"),
                        "/d",
                        "/s",
                        "/c",
                        str(cand),
                    ]
                return [str(cand)]
    return [nombre]


BASH_GIT_POR_OMISION = r"C:\Program Files\Git\bin\bash.exe"


def resuelve_bash(configurado: str = "") -> str:
    return (
        configurado.strip()
        if configurado and configurado.strip()
        else BASH_GIT_POR_OMISION
    )


def es_bash_de_wsl(ruta: str) -> bool:
    baja = (ruta or "").lower().replace("/", "\\")
    return baja.endswith(r"\system32\bash.exe") or r"\windowsapps\bash.exe" in baja


def argv_shell(cmd: str, configurado: str = "") -> tuple:
    bash = resuelve_bash(configurado)
    if re.match(r"\s*schtasks\b", cmd, re.IGNORECASE):
        cmd = (
            "MSYS_NO_PATHCONV=1 " + cmd
        )  # sin esto Git Bash vuelve /query una ruta de Git
    if es_bash_de_wsl(bash):
        bash = BASH_GIT_POR_OMISION
    return ([bash, "-c", cmd], False, None)


def es_stub_tienda(ruta: str) -> bool:
    return "windowsapps" in (ruta or "").lower().replace("/", "\\")


def path_sin_stub_tienda(path: str, sep: str = ";") -> str:
    return sep.join(p for p in (path or "").split(sep) if p and not es_stub_tienda(p))


def _ruta_git_bash(ruta: str) -> str:
    if len(ruta) > 2 and ruta[1] == ":" and ruta[0].isalpha():
        return "/" + ruta[0].lower() + ruta[2:].replace("\\", "/")
    return ruta.replace("\\", "/")


def carpeta_python_real() -> Path | None:
    d = Path(tempfile.gettempdir()) / "motor-python"
    exe = _ruta_git_bash(sys.executable)
    try:
        d.mkdir(parents=True, exist_ok=True)
        for nombre in ("python", "python3"):
            p = d / nombre
            contenido = f'#!/bin/sh\nexec "{exe}" "$@"\n'
            try:
                actual = p.read_text(encoding="utf-8")
            except OSError:
                actual = ""
            if actual != contenido:
                p.write_text(contenido, encoding="utf-8")
                p.chmod(0o755)
    except OSError:
        return None
    return d


def path_con_python_real(path: str, sep: str = ";") -> str:
    limpio = path_sin_stub_tienda(path, sep)
    carpeta = carpeta_python_real()
    if carpeta is None:
        return limpio
    partes = [p for p in limpio.split(sep) if p] if limpio else []
    if str(carpeta) in partes:
        return limpio
    return sep.join([str(carpeta), *partes])


def argv_windows(argv: list[str], env: dict) -> list[str]:
    """argv listo para CreateProcess: resuelve `.cmd`/`.bat` de npm por el PATH del agente."""
    if not argv or os.path.isabs(argv[0]):
        return argv
    return (
        resolve_shim(
            argv[0], env.get("PATH", ""), env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        )
        + argv[1:]
    )


# Lo mínimo de Windows sin lo que muchos CLIs no arrancan (Bun pide SystemRoot para la red; node
# revienta sin APPDATA/USERPROFILE). El servicio real los hereda de la sesión de la tarea.
ENV_WINDOWS = (
    "SystemRoot",
    "windir",
    "SystemDrive",
    "PATHEXT",
    "ComSpec",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "USERNAME",
    "USERDOMAIN",
    "HOMEDRIVE",
    "HOMEPATH",
    "TEMP",
    "TMP",
    "ProgramData",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "CommonProgramFiles",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
)


def env_minimo_windows(origen: dict) -> dict:
    return {k: origen[k] for k in ENV_WINDOWS if k in origen}


def tarea_xml(usuario: str, python: str, home: str, raiz: str = "") -> str:
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
  <Actions Context="Author"><Exec><Command>{python}</Command><Arguments>-m motor servicio --home "{home}"</Arguments>{f"<WorkingDirectory>{raiz}</WorkingDirectory>" if raiz else ""}</Exec></Actions>
</Task>
"""


# Un Job de Windows no contiene procesos de Linux. Este guardián corre DENTRO de WSL: es dueño
# de sus descendientes (aunque abran otra sesión) y los mata al vencer el plazo o si el padre
# (el motor en Windows) desaparece. Sin plazo, vive exactamente lo que viva el padre.
# Salidas: 124 = plazo o padre muerto, 125 = no pudo contener.
GUARDIAN_WSL = """
import ctypes, os, pathlib, signal, subprocess, sys, time


def morir_con_el_padre():
    try:
        return ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
    except Exception:
        return False


def padre_ido(padre):
    return os.getppid() != padre or not pathlib.Path('/proc/' + str(padre)).exists()


def hijos_propios():
    f = pathlib.Path('/proc/self/task') / str(os.getpid()) / 'children'
    try:
        return [int(x) for x in f.read_text(encoding="utf-8").split()]
    except OSError:
        return []


def correr(plazo, cmd, parado):
    shims = os.environ.get('MOTOR_SHIMS', '')
    if shims:
        os.environ['PATH'] = shims + ':' + os.environ.get('PATH', '')
    padre = os.getppid()
    sigue = (lambda: True) if plazo is None else (lambda: time.time() < plazo)
    code = 124
    try:
        if sigue() and not parado():
            hijo = subprocess.Popen(cmd, start_new_session=True)
            while sigue() and not parado() and not padre_ido(padre):
                code = hijo.poll()
                if code is not None:
                    break
                time.sleep(0.02)
            else:
                code = 124
    finally:
        limite = time.monotonic() + 0.9
        while True:
            propios = hijos_propios()
            if not propios:
                break
            for pid in propios:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            while True:
                try:
                    if os.waitpid(-1, os.WNOHANG)[0] == 0:
                        break
                except ChildProcessError:
                    break
            if time.monotonic() >= limite:
                code = 125
                break
            time.sleep(0.01)
    return code


def main():
    plazo = None if sys.argv[1] == '--parent' else float(sys.argv[1])
    if not morir_con_el_padre():
        raise SystemExit(125)
    parado = False

    def parar(signum, frame):
        nonlocal parado
        parado = True

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, parar)
    raise SystemExit(correr(plazo, sys.argv[2:], lambda: parado))


main()
"""


def argv_wsl(
    distro: str,
    cmd: list[str],
    timeout: float | None = None,
    ahora: float | None = None,
) -> list[str]:
    plazo = (
        "--parent"
        if timeout is None
        else str((time.time() if ahora is None else ahora) + timeout)
    )
    base = ["wsl.exe"]
    if distro:
        base += ["-d", distro]
    return [
        *base,
        "--exec",
        "/usr/bin/python3",
        "-I",
        "-S",
        "-c",
        GUARDIAN_WSL,
        plazo,
        *cmd,
    ]


def ruta_wsl(x: str) -> str:
    """`C:\\Users\\x\\brief.md` -> `/mnt/c/Users/x/brief.md`; lo demás queda igual."""
    if len(x) > 2 and x[1] == ":" and x[0].isalpha() and x[2] in "\\/":
        return f"/mnt/{x[0].lower()}" + x[2:].replace("\\", "/")
    return x


def argv_en_wsl(argv: list[str]) -> list[str]:
    """Comando que corre DENTRO de WSL: las rutas de Windows del argv se traducen (MOTOR 01, PC:
    Muse no encontraba `C:\\...\\brief.md`). El directorio de trabajo lo traduce wsl.exe solo."""
    return ["wsl.exe", "-e", *(ruta_wsl(x) for x in argv)]


def variables_wsl(env: dict) -> list[str]:
    nombres = [
        f"{k}/p" if k in ("MOTOR_RESULT", "MOTOR_SHIMS") else k
        for k in env
        if k.startswith("MOTOR_")
    ]
    if "META_API_KEY" in env and "META_API_KEY" not in nombres:
        nombres.append("META_API_KEY")
    return nombres


def env_wsl(env: dict, nombres: list[str] | None = None) -> dict:
    if nombres is None:
        nombres = variables_wsl(env)
    previas = [n for n in env.get("WSLENV", "").split(":") if n]
    return {
        **env,
        "WSLENV": ":".join(previas + [n for n in nombres if n not in previas]),
    }


def lanzamiento_wsl(
    cmd: list[str],
    env: dict,
    timeout: float | None = None,
    distro: str = "",
    ahora: float | None = None,
) -> tuple[list[str], dict]:
    shims = str(Path(__file__).resolve().parent.parent / "shims")
    env2 = dict(env)
    env2["MOTOR_SHIMS"] = shims
    argv = argv_wsl(distro, [ruta_wsl(x) for x in cmd], timeout=timeout, ahora=ahora)
    return argv, env_wsl(env2)


def boot_id_desde_last_boot(valor) -> str:
    if valor is None:
        return "desconocido"
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    texto = str(valor).strip()
    return texto or "desconocido"


def _last_boot_up_time() -> str:  # pragma: no cover
    powershell = str(
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    try:
        r = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o')",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip()


_cache_boot_id: list = [None]


def boot_id_sistema(consulta=None) -> str:
    if consulta is not None:
        return boot_id_desde_last_boot(consulta())
    if _cache_boot_id[0] is None:  # pragma: no cover
        _cache_boot_id[0] = boot_id_desde_last_boot(_last_boot_up_time())
    return _cache_boot_id[0]  # pragma: no cover


def kwargs_grupo() -> dict:  # pragma: no cover
    return {"creationflags": flags_creacion()}


def pid_vivo(pid: int) -> bool:  # pragma: no cover
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = wintypes.DWORD()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def sincroniza_directorio(path) -> None:  # pragma: no cover
    """Deja durable el directorio. os.open(O_RDONLY) falla con PermissionError 13.

    Hace falta FILE_FLAG_BACKUP_SEMANTICS y acceso de escritura. Con solo lectura
    el handle abre y FlushFileBuffers responde WinError 5. Ese error sube si también
    falla el acceso de escritura.
    """
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel.FlushFileBuffers.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    invalido = wintypes.HANDLE(-1).value

    def abre(acceso: int):
        handle = kernel.CreateFileW(
            os.fspath(path),
            acceso,
            0x1 | 0x2 | 0x4,
            None,
            3,
            0x02000000,
            None,
        )
        if handle is None or handle == invalido:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    ultimo = None
    for acceso in (0x40000000, 0xC0000000):
        try:
            handle = abre(acceso)
        except OSError as e:
            ultimo = e
            continue
        try:
            if not kernel.FlushFileBuffers(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            return
        finally:
            kernel.CloseHandle(handle)
    if ultimo is not None:
        raise ultimo
    raise OSError("no pude abrir el directorio para sincronizarlo")


def hora_inicio(pid: int) -> str:  # pragma: no cover
    """Hora de creación del proceso (FILETIME como texto, "" si no existe). Distingue un pid reusado."""
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME)
    ] * 4
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    ft = [wintypes.FILETIME() for _ in range(4)]
    ok = k.GetProcessTimes(h, *[ctypes.byref(x) for x in ft])
    k.CloseHandle(h)
    if not ok:
        return ""
    return str((ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime)


def _consulta_pid(pid: int) -> str:  # pragma: no cover
    """`vivo`, `muerto` o `incierto`. Un acceso denegado no es una muerte."""
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return "muerto" if ctypes.get_last_error() == 87 else "incierto"
    code = wintypes.DWORD()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    if not ok:
        return "incierto"
    return "vivo" if code.value == 259 else "muerto"


def _reanudar(pid: int) -> None:  # pragma: no cover
    """Suelta el hilo que CreateProcess dejó suspendido. Si no puede, el proceso no sigue vivo."""
    import ctypes
    from ctypes import wintypes

    class Hilo(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
        ]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(Hilo)]
    k.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(Hilo)]
    k.OpenThread.restype = wintypes.HANDLE
    k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.ResumeThread.argtypes = [wintypes.HANDLE]
    k.ResumeThread.restype = wintypes.DWORD
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k.CreateToolhelp32Snapshot(0x00000004, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        raise OSError("no pude ver los hilos para reanudar")
    ent = Hilo()
    ent.dwSize = ctypes.sizeof(Hilo)
    ok = k.Thread32First(snap, ctypes.byref(ent))
    n = 0
    try:
        while ok:
            if ent.th32OwnerProcessID == pid:
                h = k.OpenThread(0x0002, False, ent.th32ThreadID)
                if h:
                    r = k.ResumeThread(h)
                    k.CloseHandle(h)
                    if r != 0xFFFFFFFF:
                        n += 1
            ok = k.Thread32Next(snap, ctypes.byref(ent))
    finally:
        k.CloseHandle(snap)
    if n == 0:
        raise OSError("no pude reanudar el proceso suspendido")


def _terminar(p: subprocess.Popen) -> None:  # pragma: no cover
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    try:
        k.TerminateProcess(int(p._handle), 1)
    except OSError:
        pass
    try:
        p.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _flags(breakaway: bool, suspended: bool) -> int:
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    if breakaway:
        flags |= CREATE_BREAKAWAY_FROM_JOB
    if suspended:
        flags |= CREATE_SUSPENDED
    return flags


def crear_proceso(cmd, propiedad, **kwargs):  # pragma: no cover
    """Crea el proceso. Solo un comando contenido, y solo con `winerror == 5`, se reintenta sin `BREAKAWAY`.

    Un trabajador separado que no puede salir del Job no se crea: el entorno queda rechazado.
    Un comando contenido queda suspendido hasta `liberar`, y solo si ya está en su Job.
    """
    from . import ProcesoCreado, Propiedad

    opciones = dict(kwargs)
    opciones.pop("creationflags", None)
    suspender = propiedad == Propiedad.COMANDO_CONTENIDO
    try:
        p = subprocess.Popen(cmd, creationflags=_flags(True, suspender), **opciones)
        separacion = "breakaway"
    except OSError as e:
        if getattr(e, "winerror", None) != 5:
            raise
        if not suspender:
            raise OSError(
                getattr(e, "errno", None),
                "el entorno no deja separar el trabajador del Job",
            ) from e
        p = subprocess.Popen(cmd, creationflags=_flags(False, suspender), **opciones)
        separacion = "sin_breakaway"
    if not suspender:
        return ProcesoCreado(p, separacion, "")
    nombre = "Local\\goncloud-motor-" + uuid.uuid4().hex
    try:
        job = JobMatarAlCerrar(nombre)
        job.asignar(p)
    except OSError as e:
        _terminar(p)
        raise OSError(
            getattr(e, "errno", None),
            f"no pude contener el arbol: winerror {getattr(e, 'winerror', None)}",
        ) from e
    _jobs[p.pid] = job

    def _liberar(proceso: subprocess.Popen = p) -> None:
        try:
            _reanudar(proceso.pid)
        except OSError:
            _terminar(proceso)
            raise

    p._motor_liberar = _liberar
    return ProcesoCreado(p, separacion, nombre)


def _pids_job(handle) -> list[int] | None:  # pragma: no cover
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    k.QueryInformationJobObject.restype = wintypes.BOOL
    capacidad = 16
    for _ in range(6):

        class Lista(ctypes.Structure):
            _fields_ = [
                ("asignados", wintypes.DWORD),
                ("en_lista", wintypes.DWORD),
                ("pids", ctypes.c_size_t * capacidad),
            ]

        info = Lista()
        if k.QueryInformationJobObject(
            handle, 3, ctypes.byref(info), ctypes.sizeof(info), None
        ):
            return [int(info.pids[i]) for i in range(int(info.en_lista))]
        if ctypes.get_last_error() != 234:
            return None
        capacidad *= 4
    return None


def _abrir_job(nombre: str):  # pragma: no cover
    """(handle | None, 'ok' | 'ausente' | 'incierto'). El nombre que ya no existe no es un error de acceso."""
    import ctypes
    from ctypes import wintypes

    if not nombre:
        return None, "ausente"
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenJobObjectW.restype = wintypes.HANDLE
    k.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    h = k.OpenJobObjectW(0x0004 | 0x0008, False, nombre)
    if h:
        return h, "ok"
    return None, "ausente" if ctypes.get_last_error() in (2, 3) else "incierto"


def _descendientes(pid: int) -> list[int] | None:  # pragma: no cover
    import ctypes
    from ctypes import wintypes

    class Proc(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Proc)]
    k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Proc)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return None
    ent = Proc()
    ent.dwSize = ctypes.sizeof(Proc)
    padres: dict[int, int] = {}
    ok = k.Process32FirstW(snap, ctypes.byref(ent))
    try:
        while ok:
            padres[int(ent.th32ProcessID)] = int(ent.th32ParentProcessID)
            ok = k.Process32NextW(snap, ctypes.byref(ent))
    finally:
        k.CloseHandle(snap)
    if not padres:
        return None
    hijos = []
    for candidato, padre in padres.items():
        visto = {candidato}
        actual = padre
        for _ in range(32):
            if actual == pid:
                hijos.append(candidato)
                break
            if actual in visto or actual == 0:
                break
            visto.add(actual)
            if actual not in padres:
                break
            actual = padres[actual]
    return hijos


def presencia_grupo(pid: int, inicio: str, job: str = "") -> str:  # pragma: no cover
    """`vivo`, `ausente` o `incierto`. Un error al mirar el Job o el árbol no es ausencia."""
    if not inicio or pid <= 0:
        return "incierto"
    estado = _consulta_pid(pid)
    if estado == "incierto":
        return "incierto"
    if estado == "vivo":
        actual = hora_inicio(pid)
        if not actual or actual != inicio:
            return "incierto"
        return "vivo"
    propio = _jobs.get(pid)
    if propio is not None and propio._h:
        pids = propio.pids()
        if pids is None:
            return "incierto"
        return "vivo" if pids else "ausente"
    handle, visto = _abrir_job(job)
    if visto == "incierto":
        return "incierto"
    if handle:
        try:
            pids = _pids_job(handle)
        finally:
            import ctypes

            ctypes.WinDLL("kernel32").CloseHandle(handle)
        if pids is None:
            return "incierto"
        return "vivo" if pids else "ausente"
    hijos = _descendientes(pid)
    if hijos is None:
        return "incierto"
    return "vivo" if hijos else "ausente"


def cerrar_huerfanos(pid: int, inicio: str, job: str = "") -> None:  # pragma: no cover
    """Termina descendientes cuando el líder ya no es el proceso lanzado. No mata a ciegas."""
    if not inicio or pid <= 1:
        return
    estado = _consulta_pid(pid)
    if estado != "muerto":
        if estado == "vivo" and hora_inicio(pid) == inicio:
            return
        if estado != "muerto":
            return
    propio = _jobs.pop(pid, None)
    if propio is not None:
        propio.cerrar()
        return
    handle, visto = _abrir_job(job)
    if handle:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.TerminateJobObject(handle, 1)
        k.CloseHandle(handle)
        return
    if visto == "incierto":
        return
    for hijo in _descendientes(pid) or ():
        subprocess.run(
            ["taskkill", "/PID", str(hijo), "/T", "/F"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )


def matar_grupo_pid(pid: int, inicio: str = "") -> None:  # pragma: no cover
    """Termina el árbol de `pid` (taskkill /T /F)."""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )


def candado_exclusivo(f) -> bool:  # pragma: no cover
    """Toma el candado exclusivo de un archivo abierto, sin esperar. False si otro lo tiene."""
    import msvcrt

    f.seek(0)
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    return True


class JobMatarAlCerrar:  # pragma: no cover
    """Job Object con JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: al cerrar el handle (o al morir quien
    lo tiene) Windows mata a todo el árbol asignado."""

    def __init__(self, nombre: str | None = None, *, matar_al_cerrar: bool = True) -> None:
        import ctypes
        from ctypes import wintypes

        class Basico(ctypes.Structure):
            _fields_ = [
                ("tiempo_proceso", ctypes.c_longlong),
                ("tiempo_job", ctypes.c_longlong),
                ("flags", wintypes.DWORD),
                ("ws_min", ctypes.c_size_t),
                ("ws_max", ctypes.c_size_t),
                ("procesos_activos", wintypes.DWORD),
                ("afinidad", ctypes.c_size_t),
                ("prioridad", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Io(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_ulonglong)
                for n in (
                    "lecturas",
                    "escrituras",
                    "otras",
                    "bytes_leidos",
                    "bytes_escritos",
                    "bytes_otros",
                )
            ]

        class Extendido(ctypes.Structure):
            _fields_ = [
                ("basico", Basico),
                ("io", Io),
                ("mem_proceso", ctypes.c_size_t),
                ("mem_job", ctypes.c_size_t),
                ("pico_proceso", ctypes.c_size_t),
                ("pico_job", ctypes.c_size_t),
            ]

        self._ctypes = ctypes
        k = self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.INT,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.nombre = nombre or ""
        self._h = k.CreateJobObjectW(None, nombre)
        if not self._h:
            raise ctypes.WinError(ctypes.get_last_error())
        if matar_al_cerrar:
            lim = Extendido()
            lim.basico.flags = 0x00002000  # KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(
                self._h, 9, ctypes.byref(lim), ctypes.sizeof(lim)
            ):
                self.cerrar()
                raise ctypes.WinError(ctypes.get_last_error())

    def asignar(self, p: subprocess.Popen) -> None:
        if not self._k.AssignProcessToJobObject(self._h, int(p._handle)):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def pids(self) -> list[int] | None:
        if not self._h:
            return None
        return _pids_job(self._h)

    def cerrar(self) -> None:
        if self._h:
            self._k.CloseHandle(self._h)
            self._h = None


_jobs: dict[int, JobMatarAlCerrar] = {}


def contener(p: subprocess.Popen) -> None:  # pragma: no cover
    """Mete al proceso (ya lanzado) en un Job kill-on-close propio. Si no se puede (p. ej. ya
    está en un Job que no lo permite), sigue sin Job: `matar_grupo` usa taskkill de todos modos."""
    try:
        j = JobMatarAlCerrar()
    except OSError:
        return
    try:
        j.asignar(p)
    except OSError:
        j.cerrar()
        return
    _jobs[p.pid] = j


def matar_grupo(p: subprocess.Popen) -> None:  # pragma: no cover
    """Termina el árbol completo: cierra el Job (mata al árbol) y taskkill /T /F de respaldo."""
    j = _jobs.pop(p.pid, None)
    if j is not None:
        j.cerrar()
    try:
        subprocess.run(
            ["taskkill", "/PID", str(p.pid), "/T", "/F"],
            capture_output=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if p.poll() is None:
            p.kill()
    except OSError:
        pass
    try:
        p.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass
