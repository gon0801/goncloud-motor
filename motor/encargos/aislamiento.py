"""Capacidades de un comando y sus hijos. El Job administra vida, no permisos."""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ComandoAislado:
    argv: list[str]
    entorno: dict[str, str]
    mecanismo: str
    jaula: dict | None = None


def _codex_windows() -> Path:
    directo = shutil.which("codex.exe")
    if directo:
        return Path(directo).resolve()
    shim = shutil.which("codex.cmd")
    if shim:
        paquete = Path(shim).parent / "node_modules" / "@openai" / "codex"
        for arquitectura in ("x64", "arm64"):
            raiz = paquete / "node_modules" / "@openai" / f"codex-win32-{arquitectura}"
            candidatos = list(raiz.glob("vendor/*/bin/codex.exe"))
            if len(candidatos) == 1:
                return candidatos[0].resolve()
    raise RuntimeError("no se encontro el ejecutable nativo instalado de Codex/MXC")


def _entorno(runtime: Path, suscripcion: bool, cmd: list[str], *, agente: str | None = None) -> dict[str, str]:
    permitidas = {
        "PATH",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "TERM",
        "TZ",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in permitidas or k.upper().startswith("LC_")
    }
    temporal = runtime / "tmp"
    temporal.mkdir(parents=True, exist_ok=True)
    env.update(
        HOME=str(runtime),
        USERPROFILE=str(runtime),
        APPDATA=str(runtime / "AppData" / "Roaming"),
        LOCALAPPDATA=str(runtime / "AppData" / "Local"),
        XDG_CONFIG_HOME=str(runtime / ".config"),
        TMPDIR=str(temporal),
        TEMP=str(temporal),
        TMP=str(temporal),
        PYTHONDONTWRITEBYTECODE="1",
        CODEX_HOME=str(runtime / ".codex"),
    )
    for path in (env["APPDATA"], env["LOCALAPPDATA"], env["CODEX_HOME"]):
        Path(path).mkdir(parents=True, exist_ok=True)
    if suscripcion:
        nombre = agente or Path(cmd[0]).stem.lower()
        if nombre == "codex":
            origen = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            destino = runtime / ".codex"
        elif nombre == "grok":
            origen = Path.home() / ".grok"
            destino = runtime / ".grok"
        else:
            return env
        auth = origen / "auth.json"
        if auth.is_file():
            if nombre == "codex":
                datos = json.loads(auth.read_text(encoding="utf-8"))
                if datos.get("auth_mode") != "chatgpt" or datos.get("OPENAI_API_KEY"):
                    raise RuntimeError(
                        "el encargo necesita autenticacion Codex por suscripcion"
                    )
            destino.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(auth, destino / "auth.json")
            (destino / "auth.json").chmod(0o600)
    return env


def aislar(
    cmd: list[str],
    espacio: Path,
    runtime: Path,
    *,
    lectura: bool = False,
    suscripcion: bool = False,
    lecturas_extra: tuple[Path, ...] = (),
    agente: str | None = None,
) -> ComandoAislado:
    espacio, runtime = espacio.resolve(), runtime.resolve()
    if espacio.parent.name != "espacio" or runtime.is_relative_to(espacio):
        raise RuntimeError("la jaula necesita espacio concedido y runtime separado")
    runtime.mkdir(parents=True, exist_ok=True)
    env = _entorno(runtime, suscripcion, cmd, agente=agente)
    if os.name == "nt":
        ejecutable = Path(cmd[0])
        broker = (
            ejecutable.resolve()
            if ejecutable.is_absolute() and ejecutable.name.lower() == "codex.exe"
            else _codex_windows()
        )
        if broker.is_relative_to(espacio) or broker.is_relative_to(runtime):
            raise RuntimeError("el broker de aislamiento pertenece al trabajador")
        if Path(cmd[0]).stem.lower() == "codex":
            argv = [str(broker), *cmd[1:]]
        else:
            from motor.plataforma.windows import argv_windows

            argv = argv_windows(cmd, env)
            if not Path(argv[0]).is_absolute():
                ejecutable = shutil.which(argv[0], path=env.get("PATH"))
                if not ejecutable:
                    raise FileNotFoundError(argv[0])
                argv = [str(Path(ejecutable).resolve()), *argv[1:]]
        raices = [
            Path(sys.executable).resolve().parent,
            broker.parent,
            Path(os.environ["SystemRoot"]),
        ]
        raices.extend(path.resolve() for path in lecturas_extra)
        raices.extend(
            Path(os.environ[k])
            for k in ("ProgramFiles", "ProgramFiles(x86)")
            if k in os.environ
        )
        if suscripcion:
            if Path(cmd[0]).stem.lower() != "codex":
                raise RuntimeError(
                    "el agente Windows administrado requiere Codex instalado"
                )
            capacidades = [
                'web_search="disabled"',
                "projects." + json.dumps(str(espacio)) + '.trust_level="untrusted"',
                *(
                    "features." + nombre + "=false"
                    for nombre in (
                        "apps",
                        "plugins",
                        "hooks",
                        "multi_agent",
                        "image_generation",
                        "standalone_web_search",
                    )
                ),
            ]
            argv = [argv[0], *(x for c in capacidades for x in ("-c", c)), *argv[1:]]
        limites = {
            "lectura": [str(x) for x in raices] + ([str(espacio)] if lectura else []),
            "escritura": [str(runtime)] + ([] if lectura else [str(espacio)]),
            "internet": suscripcion,
            "proveedor": sorted(
                {
                    str(ipaddress.ip_address(fila[4][0]))
                    for host in ("chatgpt.com", "auth.openai.com")
                    for fila in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                    if ipaddress.ip_address(fila[4][0]).is_global
                }
            )
            if suscripcion
            else [],
            "runtime": str(runtime),
        }
        if suscripcion and not limites["proveedor"]:
            raise RuntimeError(
                "no hay direcciones publicas del proveedor para la suscripcion"
            )
        return ComandoAislado(argv, env, "appcontainer", limites)
    sandbox = Path("/usr/bin/sandbox-exec")
    if not sandbox.is_file():
        raise RuntimeError(
            "sin aislamiento efectivo; trabajador no habilitado en este host"
        )
    perfil = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        f"(deny file-read* (subpath {json.dumps(str(Path.home()))}))",
        "(allow file-read-metadata)",
    ]
    for path in (espacio, runtime, *(p.resolve() for p in lecturas_extra)):
        perfil.append(f"(allow file-read* (subpath {json.dumps(str(path))}))")
    for path in (runtime,) if lectura else (espacio, runtime):
        perfil.append(f"(allow file-write* (subpath {json.dumps(str(path))}))")
    for path in ("/dev/null", "/dev/tty"):
        perfil.append(f'(allow file-write* (literal "{path}"))')
    path = runtime / "jaula.sb"
    path.write_text("\n".join(perfil), encoding="utf-8")
    return ComandoAislado([str(sandbox), "-f", str(path), *cmd], env, "seatbelt")
