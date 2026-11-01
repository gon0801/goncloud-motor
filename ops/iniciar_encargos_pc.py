"""Inicia el supervisor instalado en la sesión del mismo usuario, fuera del Job SSH.

--sonda ejecuta únicamente una comprobación inocua. No instala servicios ni tareas.
Explorer COM selecciona la sesión; un puente usa CREATE_BREAKAWAY_FROM_JOB.
CreateProcessAsUserW necesita un privilegio ausente en SSH y WithTokenW usa sesión 0.

Sin --sonda deja un vigilante (también fuera del Job) que vuelve a lanzar el supervisor
cuando termina, con espera creciente. --estado lo consulta y --parar lo detiene; véase
docs/supervisor-pc.md.
"""

import argparse
import base64
import ctypes as C
import json
import os
import subprocess
import sys
import time
import uuid
from ctypes import wintypes as W
from pathlib import Path


class PROCESSENTRY32(C.Structure):
    _fields_ = [
        ("dwSize", W.DWORD),
        ("cntUsage", W.DWORD),
        ("th32ProcessID", W.DWORD),
        ("th32DefaultHeapID", C.c_size_t),
        ("th32ModuleID", W.DWORD),
        ("cntThreads", W.DWORD),
        ("th32ParentProcessID", W.DWORD),
        ("pcPriClassBase", W.LONG),
        ("dwFlags", W.DWORD),
        ("szExeFile", W.WCHAR * 260),
    ]


class Windows:
    def __init__(self):
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.advapi = C.WinDLL("advapi32", use_last_error=True)
        self.close = self.api(self.kernel, "CloseHandle", [W.HANDLE])
        self.current = self.api(self.kernel, "GetCurrentProcess", [], W.HANDLE)
        self.open = self.api(
            self.kernel, "OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
        )
        self.open_token = self.api(
            self.advapi, "OpenProcessToken", [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
        )
        self.token_info = self.api(
            self.advapi,
            "GetTokenInformation",
            [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)],
        )
        self.sid_string = self.api(
            self.advapi, "ConvertSidToStringSidW", [W.LPVOID, C.POINTER(W.LPWSTR)]
        )
        self.local_free = self.api(self.kernel, "LocalFree", [W.LPVOID], W.LPVOID)
        self.session = self.api(
            self.kernel, "ProcessIdToSessionId", [W.DWORD, C.POINTER(W.DWORD)]
        )
        self.in_job = self.api(
            self.kernel, "IsProcessInJob", [W.HANDLE, W.HANDLE, C.POINTER(W.BOOL)]
        )

    @staticmethod
    def api(dll, name, parameters, result=W.BOOL):
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = parameters, result
        return fn

    @staticmethod
    def check(ok):
        if not ok:
            raise C.WinError(C.get_last_error())

    def identity(self, pid=None):
        pid = pid or os.getpid()
        process = self.open(0x1000, False, pid)
        self.check(process)
        token = W.HANDLE()
        try:
            self.check(self.open_token(process, 8, C.byref(token)))
            size = W.DWORD()
            self.token_info(token, 1, None, 0, C.byref(size))
            buf = C.create_string_buffer(size.value)
            self.check(self.token_info(token, 1, buf, size, C.byref(size)))
            sid = W.LPWSTR()
            self.check(
                self.sid_string(C.cast(buf, C.POINTER(W.LPVOID))[0], C.byref(sid))
            )
            try:
                sid_text = sid.value
            finally:
                self.local_free(C.cast(sid, W.LPVOID))
            session, job = W.DWORD(), W.BOOL()
            self.check(self.session(pid, C.byref(session)))
            self.check(self.in_job(process, None, C.byref(job)))
            return {
                "pid": pid,
                "sid": sid_text,
                "session": session.value,
                "in_job": bool(job.value),
            }
        finally:
            if token:
                self.close(token)
            self.close(process)

    def explorer(self, sid):
        snapshot = self.api(
            self.kernel, "CreateToolhelp32Snapshot", [W.DWORD, W.DWORD], W.HANDLE
        )(2, 0)
        if snapshot == W.HANDLE(-1).value:
            self.check(False)
        first = self.api(
            self.kernel, "Process32FirstW", [W.HANDLE, C.POINTER(PROCESSENTRY32)]
        )
        next_process = self.api(
            self.kernel, "Process32NextW", [W.HANDLE, C.POINTER(PROCESSENTRY32)]
        )
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = C.sizeof(entry)
            more = first(snapshot, C.byref(entry))
            candidates = []
            while more:
                if entry.szExeFile.lower() == "explorer.exe":
                    try:
                        identity = self.identity(entry.th32ProcessID)
                    except OSError:
                        pass  # Another user's inaccessible Explorer is not a candidate.
                    else:
                        if identity["sid"] == sid and identity["session"]:
                            candidates.append(identity)
                more = next_process(snapshot, C.byref(entry))
            if len({item["session"] for item in candidates}) != 1:
                raise RuntimeError(
                    "Se requiere exactamente una sesión Explorer del mismo usuario"
                )
            return candidates[0]
        finally:
            self.close(snapshot)


CARPETA = ".motor-encargos"
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
ESPERA_BASE_S = 5
ESPERA_TOPE_S = 300
ESTABLE_S = 600
ESPERA_ARRANQUE_S = 10
CODIGO_YA_ACTIVO = 3


def espera_creciente(fallos, base=ESPERA_BASE_S, tope=ESPERA_TOPE_S):
    """Segundos antes del intento número `fallos` seguido: 5, 10, 20, ... hasta el tope."""
    return min(base * 2 ** max(fallos - 1, 0), tope)


def identidad_valida(identidad, sid, sesion):
    return (
        identidad["sid"] == sid
        and identidad["session"] == sesion
        and bool(sesion)
        and not identidad["in_job"]
    )


def vigilar(
    lanzar,
    parar_pedido,
    reloj,
    dormir,
    registrar,
    terminar,
    estable_s=ESTABLE_S,
    base=ESPERA_BASE_S,
    tope=ESPERA_TOPE_S,
):
    """Mantiene vivo el supervisor: cuando termina, lo vuelve a lanzar con una espera
    que crece (5, 10, 20 ... 300 s). Un supervisor que vivió `estable_s` o más vuelve a
    empezar la cuenta en la espera base. Devuelve cuántas veces lo lanzó.

    `lanzar()` devuelve un proceso con `pid` y `wait(timeout)`; `parar_pedido()` es la
    petición del operador; `terminar(proceso)` cierra el árbol del supervisor."""
    fallos = lanzamientos = 0
    while not parar_pedido():
        inicio = reloj()
        try:
            proceso = lanzar()
        except Exception as exc:
            registrar(f"el supervisor no arrancó: {type(exc).__name__}: {exc}")
        else:
            lanzamientos += 1
            registrar(f"supervisor en marcha, pid {proceso.pid}")
            while True:
                try:
                    codigo = proceso.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    if parar_pedido():
                        terminar(proceso)
                        registrar("parada pedida: supervisor terminado")
                        return lanzamientos
                    continue
                registrar(f"el supervisor terminó con código {codigo}")
                break
        fallos = 1 if reloj() - inicio >= estable_s else fallos + 1
        espera = espera_creciente(fallos, base, tope)
        registrar(f"se vuelve a levantar en {espera} s (intento seguido {fallos})")
        for _ in range(espera):
            if parar_pedido():
                break
            dormir(1)
    registrar("parada pedida")
    return lanzamientos


def adquirir_exclusivo(ruta):
    """Un solo vigilante por usuario: el candado lo suelta el sistema si el proceso muere."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    archivo = open(ruta, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            archivo.seek(0)
            msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        archivo.close()
        return None
    return archivo


def escribir_estado(ruta, datos):
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    temporal.replace(ruta)


def leer_estado(ruta):
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def limpiar_huerfano(previo, imagen_de, matar_arbol, registrar):
    """Si un vigilante anterior murió sin cerrar su supervisor, ese PowerShell sigue vivo y
    un segundo supervisor duplicaría el barrido. Solo se cierra si el PID sigue siendo un
    powershell (un PID reutilizado por otro programa no se toca)."""
    pid = previo.get("supervisor_pid")
    if not isinstance(pid, int):
        return False
    if (imagen_de(pid) or "").lower() != "powershell.exe":
        return False
    registrar(f"se cierra el supervisor huérfano {pid} de un vigilante anterior")
    matar_arbol(pid)
    return True


def imagen_de(pid):
    salida = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW,
    ).stdout.strip()
    return salida.split('","')[0].strip('"') if salida.startswith('"') else ""


def matar_arbol(pid):
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW,
    )


def comando_supervisor(powershell, supervisor):
    return [
        str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(supervisor),
    ]


def correr_vigilante(carpeta, comando, runtime, sid, sesion, windows):
    """Proceso del vigilante: nace fuera del Job de SSH (lo comprueba `main`), y sus hijos,
    el supervisor incluido, heredan esa condición."""
    candado = adquirir_exclusivo(carpeta / "vigilante.lock")
    if candado is None:
        return CODIGO_YA_ACTIVO
    ruta_estado, ruta_parar, ruta_log = (
        carpeta / "vigilante.json", carpeta / "vigilante.parar", carpeta / "vigilante.log",
    )
    previo = leer_estado(ruta_estado)

    def registrar(texto):
        if ruta_log.exists() and ruta_log.stat().st_size > 1_000_000:
            ruta_log.replace(ruta_log.with_suffix(".log.1"))
        with open(ruta_log, "a", encoding="utf-8") as log:
            log.write(time.strftime("%Y-%m-%dT%H:%M:%S") + " " + texto + "\n")

    estado = {"vigilante_pid": os.getpid(), "inicio": time.time(), "lanzamientos": 0}

    def lanzar():
        proceso = subprocess.Popen(comando, cwd=runtime, creationflags=CREATE_NO_WINDOW)
        identidad = windows.identity(proceso.pid)
        if not identidad_valida(identidad, sid, sesion):
            matar_arbol(proceso.pid)
            raise RuntimeError("Supervisor rechazado: identidad, sesión o Job incorrectos")
        estado.update(
            supervisor=identidad, supervisor_pid=proceso.pid,
            lanzamientos=estado["lanzamientos"] + 1,
        )
        escribir_estado(ruta_estado, estado)
        return proceso

    def terminar(proceso):
        matar_arbol(proceso.pid)

    limpiar_huerfano(previo, imagen_de, matar_arbol, registrar)
    registrar(f"vigilante {os.getpid()} en marcha")
    try:
        vigilar(lanzar, ruta_parar.exists, time.monotonic, time.sleep, registrar, terminar)
    finally:
        estado.update(parado=True, supervisor_pid=None)
        escribir_estado(ruta_estado, estado)
        ruta_parar.unlink(missing_ok=True)
        candado.close()
    return 0


def esperar_vigilante(ruta_estado, proceso, segundos=ESPERA_ARRANQUE_S, reloj=time.monotonic, dormir=time.sleep):
    """Espera a que el vigilante, o el que ya había, tenga un supervisor con identidad válida."""
    limite = reloj() + segundos
    while reloj() < limite:
        estado = leer_estado(ruta_estado)
        codigo = proceso.poll()
        if estado.get("supervisor") and not estado.get("parado"):
            if estado.get("vigilante_pid") == proceso.pid or codigo == CODIGO_YA_ACTIVO:
                return {**estado, "ya_activo": codigo == CODIGO_YA_ACTIVO}
        if codigo not in (None, CODIGO_YA_ACTIVO):
            raise RuntimeError(f"El vigilante terminó con código {codigo}")
        dormir(0.2)
    proceso.terminate()
    raise RuntimeError("El vigilante no dejó un supervisor válido a tiempo; véase vigilante.log")


def fixed_paths():
    home = Path.home()
    runtime = home / "motor-sucesor"
    supervisor = home / ".motor-encargos" / "supervisor.ps1"
    powershell = (
        Path(os.environ["SystemRoot"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    if not runtime.is_dir() or not supervisor.is_file() or not powershell.is_file():
        raise RuntimeError("Falta el runtime, el supervisor o PowerShell instalado")
    return runtime, supervisor, powershell


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sonda", action="store_true")
    parser.add_argument(
        "--parar", action="store_true",
        help="pide al vigilante que cierre el supervisor y deje de relanzarlo",
    )
    parser.add_argument(
        "--estado", action="store_true", help="muestra el estado del vigilante (vigilante.json)"
    )
    parser.add_argument(
        "--fase", choices=["puente", "libre", "vigilante"], help=argparse.SUPPRESS
    )
    parser.add_argument("--salida", help=argparse.SUPPRESS)
    parser.add_argument("--sid", help=argparse.SUPPRESS)
    parser.add_argument("--sesion", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.parar or args.estado:
        carpeta = Path.home() / CARPETA
        if args.parar:
            carpeta.mkdir(parents=True, exist_ok=True)
            (carpeta / "vigilante.parar").write_text("parar\n", encoding="utf-8")
        print(json.dumps(leer_estado(carpeta / "vigilante.json"), ensure_ascii=False))
        return
    if os.name != "nt":
        parser.error("Esta utilidad requiere Windows")
    windows = Windows()
    own = windows.identity()
    runtime, supervisor, powershell = fixed_paths()
    folder = Path.home() / ".motor-resguardo/cierre-flujo-20261028"
    if args.fase == "vigilante":
        if not identidad_valida(own, args.sid, args.sesion):
            raise RuntimeError("Vigilante rechazado: usuario, sesión o Job incorrectos")
        sys.exit(
            correr_vigilante(
                Path.home() / CARPETA,
                comando_supervisor(powershell, supervisor),
                runtime, args.sid, args.sesion, windows,
            )
        )
    if args.fase:
        output = Path(args.salida or "").resolve()
        if (
            output.parent != folder.resolve()
            or not output.name.startswith("sonda-arranque-")
            or output.suffix != ".json"
        ):
            parser.error("Ruta interna de evidencia inválida")
        if own["sid"] != args.sid or own["session"] != args.sesion or not args.sesion:
            raise RuntimeError("Hijo rechazado: usuario o sesión incorrectos")
        if args.fase == "puente":
            final = [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--fase",
                "libre",
                "--salida",
                str(output),
                "--sid",
                args.sid,
                "--sesion",
                str(args.sesion),
            ]
            if args.sonda:
                final.append("--sonda")
            # The COM bridge belongs to a Job; only its own child leaves it.
            subprocess.run(
                final,
                cwd=runtime,
                creationflags=0x01000000 | 0x08000000,
                check=True,
                timeout=15,
            )
            report = json.loads(output.read_text(encoding="utf-8"))
            report["bridge"] = own
            report["bridge_exit_code"] = 0
            completed = output.with_suffix(".completo.json")
            temporary = completed.with_suffix(".tmp")
            temporary.write_text(json.dumps(report), encoding="utf-8")
            temporary.replace(completed)
            return
        if own["in_job"]:
            raise RuntimeError("Hijo rechazado: Job heredado")
        report = {
            "mode": "sonda" if args.sonda else "supervisor",
            "child": own,
            "exit_code": 0,
        }
        if not args.sonda:
            # Este proceso sale en segundos (el puente lo espera 15 s); el vigilante que
            # deja atrás es quien mantiene vivo al supervisor.
            carpeta = Path.home() / CARPETA
            carpeta.mkdir(parents=True, exist_ok=True)
            (carpeta / "vigilante.parar").unlink(missing_ok=True)
            proceso = subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).resolve()), "--fase", "vigilante",
                 "--sid", args.sid, "--sesion", str(args.sesion)],
                cwd=runtime,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            report["vigilante"] = esperar_vigilante(carpeta / "vigilante.json", proceso)
            report["supervisor"] = report["vigilante"]["supervisor"]
        output.write_text(json.dumps(report), encoding="utf-8")
        return
    target = windows.explorer(own["sid"])
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / ("sonda-arranque-" + uuid.uuid4().hex + ".json")
    command = [
        "-I",
        str(Path(__file__).resolve()),
        "--fase",
        "puente",
        "--salida",
        str(output),
        "--sid",
        own["sid"],
        "--sesion",
        str(target["session"]),
    ]
    if args.sonda:
        command.append("--sonda")

    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    # Fixed COM class and method; all paths/arguments are PowerShell string literals.
    script = f"""$ErrorActionPreference='Stop'
$s=[Runtime.InteropServices.Marshal]::BindToMoniker('session:{target["session"]}!new:{{9BA05972-F6A8-11CF-A442-00A0C90A8F39}}')
$loc=0; $root=0; $hwnd=0
$desktop=$s.FindWindowSW([ref]$loc,[ref]$root,8,[ref]$hwnd,1)
if (-not $desktop -or -not $hwnd) {{ throw 'No desktop in target session' }}
$desktop.Document.Application.ShellExecute({literal(sys.executable)},{literal(subprocess.list2cmdline(command))},{literal(runtime)},'open',0)
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        check=True,
        timeout=15,
        capture_output=True,
    )
    completed = output.with_suffix(".completo.json")
    deadline = time.monotonic() + 20
    while not completed.is_file():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Sin confirmación del puente: {output}")
        time.sleep(0.1)
    report = json.loads(completed.read_text(encoding="utf-8"))
    # The final probe has already exited; also wait for the short-lived COM bridge.
    bridge = windows.open(0x100000, False, report["bridge"]["pid"])
    if bridge:
        try:
            wait = windows.api(
                windows.kernel, "WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD
            )
            if wait(bridge, 5000) != 0:
                raise RuntimeError(
                    "El puente no terminó después de confirmar el arranque"
                )
        finally:
            windows.close(bridge)
    elif C.get_last_error() != 87:  # ERROR_INVALID_PARAMETER: process already gone.
        windows.check(False)
    report["probe_processes_finished"] = True
    report.update(
        parent=own,
        explorer=target,
        output=str(completed),
        api="ExplorerCOM+CREATE_BREAKAWAY_FROM_JOB",
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
