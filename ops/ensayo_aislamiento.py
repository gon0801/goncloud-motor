"""Comprueba permisos, herencia y cierre reales en una base propia de Windows."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor import plataforma
from motor.encargos.aislamiento import aislar

CODIGO = r"""
import json, pathlib, socket, subprocess, sys, time
b, runtime = map(pathlib.Path, sys.argv[1:3])
r = {}
if sys.platform == 'win32':
    import ctypes as c
    from ctypes import wintypes as w
    u, k = c.WinDLL('user32', use_last_error=True), c.WinDLL('kernel32')
    u.GetProcessWindowStation.restype = w.HANDLE
    u.GetThreadDesktop.argtypes, u.GetThreadDesktop.restype = [w.DWORD], w.HANDLE
    u.GetUserObjectInformationW.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    def nombre_gui(handle):
        texto, necesario = c.create_unicode_buffer(512), w.DWORD()
        assert u.GetUserObjectInformationW(handle, 2, texto, c.sizeof(texto), c.byref(necesario))
        return texto.value
    r['gui'] = [nombre_gui(u.GetProcessWindowStation()), nombre_gui(u.GetThreadDesktop(k.GetCurrentThreadId()))]
    assert r['gui'][0].casefold() != 'winsta0'
    k.QueryInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    k.QueryInformationJobObject.restype = w.BOOL
    ui, tamano = w.DWORD(), w.DWORD()
    r['ui_inmediata_consultada'] = bool(k.QueryInformationJobObject(None, 4, c.byref(ui), c.sizeof(ui), c.byref(tamano)))
    r['ui_inmediata_restricciones'] = ui.value
    c.set_last_error(0)
    abierto = u.OpenClipboard(None)
    try:
        denegado = not abierto or not u.EmptyClipboard()
        r['clipboard_error'] = c.get_last_error()
        r['clipboard_escribir_bloqueado'] = denegado and r['clipboard_error'] == 5
    finally:
        if abierto: u.CloseClipboard()
for nombre, path in [
    ('producto', b/'espacio'/'probe'), ('runtime', runtime),
    ('registro', b/'registro'), ('destino', b/'destino'),
    ('credenciales', b/'credenciales'), ('otro', b/'espacio'/'otro'),
]:
    for operacion in ('leer', 'escribir'):
        try:
            if operacion == 'leer': (path/'sentinel').read_text()
            else: (path/'probe').write_text('escrito')
            r[nombre+'-'+operacion] = 'permitido'
        except OSError as e: r[nombre+'-'+operacion] = type(e).__name__
for destino in json.loads(sys.argv[3]):
    try:
        with socket.create_connection(tuple(destino), timeout=2): pass
        r['tcp-'+str(destino)] = 'permitido'
    except OSError as e: r['tcp-'+str(destino)] = type(e).__name__
if sys.argv[4] == 'padre':
    p = subprocess.run([sys.executable, __file__, *sys.argv[1:4], 'hijo'], capture_output=True, text=True)
    r['hijo'] = {'rc':p.returncode,'observacion':json.loads(p.stdout),'stderr':p.stderr}
print(json.dumps(r), flush=True)
if sys.argv[4] == 'arbol':
    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    (runtime/'hijo.pid').write_text(str(p.pid))
    time.sleep(120)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path)
    ap.add_argument("--tcp", action="append", default=[], help="host:puerto propio")
    ap.add_argument(
        "--internet-suscripcion",
        action="store_true",
        help="comprueba tambien el perfil de red del agente, sin invocar el modelo",
    )
    args = ap.parse_args()
    if os.name != "nt":
        ap.error("este instrumento comprueba la jaula nativa de Windows")
    from motor.plataforma.jaula_windows import crear

    base = (args.base or Path(tempfile.mkdtemp(prefix="motor-jaula-"))).resolve()
    espacio = base / "espacio" / "probe"
    for path in (
        espacio,
        base / "registro",
        base / "destino",
        base / "credenciales",
        base / "espacio" / "otro",
    ):
        path.mkdir(parents=True, exist_ok=True)
        (path / "sentinel").write_text("SENUELO", encoding="utf-8")
    script = espacio / "sonda.py"
    script.write_text(CODIGO, encoding="utf-8")
    with socket.socket() as servidor:
        servidor.bind(("127.0.0.1", 0))
        servidor.listen()
        destinos = [["127.0.0.1", servidor.getsockname()[1]]]
        destinos.extend(
            [host, int(puerto)] for host, puerto in (x.rsplit(":", 1) for x in args.tcp)
        )
        informe = {"base": str(base), "roles": {}}
        correcto = True
        for rol in ("responsable", "revisor"):
            runtime = base / "runtime" / rol
            runtime.mkdir(parents=True, exist_ok=True)
            (runtime / "sentinel").write_text("PROPIO", encoding="utf-8")
            cmd = [
                sys.executable,
                str(script),
                str(base),
                str(runtime),
                json.dumps(destinos),
                "padre",
            ]
            comando = aislar(
                ["codex", "exec"] if args.internet_suscripcion else cmd,
                espacio,
                runtime,
                lectura=rol == "revisor",
                suscripcion=args.internet_suscripcion,
            )
            creado = crear(
                cmd if args.internet_suscripcion else comando.argv,
                comando.jaula,
                cwd=espacio,
                env=comando.entorno,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            creado.liberar()
            stdout, stderr = creado.proceso.communicate(timeout=20)
            observado = json.loads(stdout)
            informe["roles"][rol] = {
                "rc": creado.proceso.returncode,
                "observacion": observado,
                "stderr": stderr,
            }
            for r in (observado, observado["hijo"]["observacion"]):
                esperado = "PermissionError" if rol == "revisor" else "permitido"
                correcto &= (
                    r["producto-escribir"] == esperado
                    and r["runtime-escribir"] == "permitido"
                )
                correcto &= all(
                    r[n + "-" + op] == "PermissionError"
                    for n in ("registro", "destino", "credenciales", "otro")
                    for op in ("leer", "escribir")
                )
                correcto &= all(
                    v != "permitido" for k, v in r.items() if k.startswith("tcp-")
                )
                correcto &= r["clipboard_escribir_bloqueado"]
            plataforma.matar_grupo(creado.proceso)
        escritorios = [
            informe["roles"][rol]["observacion"]["gui"]
            for rol in ("responsable", "revisor")
        ]
        informe["gui_separada"] = escritorios[0] != escritorios[1] and all(
            nombre[0].casefold() != "winsta0" for nombre in escritorios
        )
        correcto &= informe["gui_separada"]
        runtime = base / "runtime" / "arbol"
        comando = aislar(
            [sys.executable, str(script), str(base), str(runtime), "[]", "arbol"],
            espacio,
            runtime,
        )
        creado = crear(
            comando.argv,
            comando.jaula,
            cwd=espacio,
            env=comando.entorno,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.2)
        suspendido = (
            not (runtime / "hijo.pid").exists() and creado.proceso.poll() is None
        )
        creado.liberar()
        fin = time.time() + 10
        while not (runtime / "hijo.pid").exists() and time.time() < fin:
            time.sleep(0.05)
        hijo = int((runtime / "hijo.pid").read_text())
        plataforma.matar_grupo(creado.proceso)
        creado.proceso.communicate(timeout=5)
        hijo_cerrado = not plataforma.pid_vivo(hijo)
        informe["arbol"] = {
            "suspendido_hasta_concesion": suspendido,
            "job": creado.job,
            "padre_cerrado": creado.proceso.poll() is not None,
            "hijo_cerrado": hijo_cerrado,
        }
        correcto &= suspendido and hijo_cerrado
    informe["correcto"] = bool(correcto)
    (base / "informe-aislamiento.json").write_text(
        json.dumps(informe, indent=2), encoding="utf-8"
    )
    print(json.dumps(informe), flush=True)
    return 0 if correcto else 1


if __name__ == "__main__":
    raise SystemExit(main())
