"""Inicia el supervisor instalado en la sesión del mismo usuario, fuera del Job SSH.

--sonda ejecuta únicamente una comprobación inocua. No instala servicios ni tareas.
Explorer COM selecciona la sesión; un puente usa CREATE_BREAKAWAY_FROM_JOB.
CreateProcessAsUserW necesita un privilegio ausente en SSH y WithTokenW usa sesión 0.
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
    parser.add_argument("--fase", choices=["puente", "libre"], help=argparse.SUPPRESS)
    parser.add_argument("--salida", help=argparse.SUPPRESS)
    parser.add_argument("--sid", help=argparse.SUPPRESS)
    parser.add_argument("--sesion", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Esta utilidad requiere Windows")
    windows = Windows()
    own = windows.identity()
    runtime, supervisor, powershell = fixed_paths()
    folder = Path.home() / ".motor-resguardo/cierre-flujo-20261028"
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
            command = [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(supervisor),
            ]
            process = subprocess.Popen(command, cwd=runtime, creationflags=0x08000000)
            report["supervisor"] = windows.identity(process.pid)
            if (
                report["supervisor"]["session"] != args.sesion
                or report["supervisor"]["in_job"]
                or report["supervisor"]["sid"] != args.sid
            ):
                process.terminate()
                process.wait(timeout=5)
                raise RuntimeError(
                    "Supervisor rechazado: identidad, sesión o Job incorrectos"
                )
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
