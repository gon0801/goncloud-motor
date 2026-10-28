"""Capacidades de un comando y sus hijos. El Job administra vida, no permisos."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import tomllib


@dataclass(frozen=True)
class ComandoAislado:
    argv: list[str]
    entorno: dict[str, str]
    mecanismo: str
    jaula: dict | None = None
    ejecutable: dict | None = None


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


def _entorno(
    runtime: Path, suscripcion: bool, cmd: list[str], *, agente: str | None = None
) -> dict[str, str]:
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
        XDG_DATA_HOME=str(runtime / ".local" / "share"),
        TMPDIR=str(temporal),
        TEMP=str(temporal),
        TMP=str(temporal),
        PYTHONDONTWRITEBYTECODE="1",
        CODEX_HOME=str(runtime / ".codex"),
    )
    for path in (
        env["APPDATA"],
        env["LOCALAPPDATA"],
        env["CODEX_HOME"],
        env["XDG_CONFIG_HOME"],
        env["XDG_DATA_HOME"],
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
    if suscripcion:
        nombre = agente or Path(cmd[0]).stem.lower()
        if nombre == "muse":
            origen = Path.home() / ".config/muse/auth.json"
            datos = json.loads(origen.read_text(encoding="utf-8"))
            cuenta = datos.get("providers", {}).get("meta", {})
            if (
                not isinstance(cuenta, dict)
                or cuenta.get("subs_active") is not True
                or not cuenta.get("access_token")
            ):
                raise RuntimeError("el encargo necesita la suscripcion Meta de Muse")
            if (
                cuenta.get("api_base_url", "https://api.meta.ai/v1").rstrip("/")
                != "https://api.meta.ai/v1"
            ):
                raise RuntimeError(
                    "la suscripcion Muse no usa el destino Meta autorizado"
                )
            destino = runtime / ".config/muse/auth.json"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(
                json.dumps(
                    {
                        "schema_version": datos.get("schema_version", 1),
                        "providers": {"meta": cuenta},
                    }
                ),
                encoding="utf-8",
            )
            destino.chmod(0o600)
            return env
        if nombre == "kimi":
            _preparar_kimi(runtime, cmd)
            env.update(
                KIMI_CODE_HOME=str(runtime / ".kimi-code"),
                KIMI_CODE_NO_AUTO_UPDATE="1",
                KIMI_DISABLE_TELEMETRY="1",
                KIMI_CODE_MODEL_CATALOG_REFRESH_ON_START="false",
            )
            return env
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


def _preparar_kimi(runtime: Path, cmd: list[str]) -> None:
    origen = Path(os.environ.get("KIMI_CODE_HOME", str(Path.home() / ".kimi-code")))
    config = tomllib.loads((origen / "config.toml").read_text(encoding="utf-8"))
    alias = (
        cmd[cmd.index("--model") + 1] if "--model" in cmd else config["default_model"]
    )
    modelo = config["models"][alias]
    proveedor = config["providers"][modelo["provider"]]
    if (
        modelo["provider"] != "managed:kimi-code"
        or proveedor.get("api_key")
        or proveedor.get("base_url", "").rstrip("/") != "https://api.kimi.com/coding/v1"
        or proveedor.get("oauth", {}).get("key") != "oauth/kimi-code"
    ):
        raise RuntimeError("el encargo necesita la suscripcion OAuth de Kimi Code")
    destino = runtime / ".kimi-code"
    (destino / "credentials").mkdir(parents=True, exist_ok=True)
    for relativo in ("credentials/kimi-code.json", "device_id", "region"):
        fuente = origen / relativo
        if fuente.is_file():
            shutil.copyfile(fuente, destino / relativo)
            (destino / relativo).chmod(0o600)
        elif relativo.startswith("credentials/"):
            raise RuntimeError("falta el acceso OAuth de Kimi Code")
    texto = [
        f"default_model = {json.dumps(alias)}",
        '[providers."managed:kimi-code"]',
        'type = "kimi"',
        'api_key = ""',
        'base_url = "https://api.kimi.com/coding/v1"',
        '[providers."managed:kimi-code".oauth]',
        'storage = "file"',
        'key = "oauth/kimi-code"',
        "[models." + json.dumps(alias) + "]",
        'provider = "managed:kimi-code"',
    ]
    for clave in ("model", "max_context_size", "capabilities"):
        if clave in modelo:
            texto.append(clave + " = " + json.dumps(modelo[clave]))
    (destino / "config.toml").write_text("\n".join(texto) + "\n", encoding="utf-8")


def _muse_local(
    argv: list[str], runtime: Path, entorno: dict[str, str]
) -> tuple[list[str], dict]:
    origen = Path(argv[0]).resolve()
    destino = runtime / "cli" / origen.name
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origen, destino)
    shutil.copymode(origen, destino)
    with destino.open("rb") as fuente:
        huella = hashlib.file_digest(fuente, "sha256").hexdigest()
    try:
        version = subprocess.run(
            [str(destino), "--version"],
            cwd=runtime,
            env=entorno,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "no se pudo comprobar la version de Muse: " + type(exc).__name__
        ) from exc
    procedencia = {
        "origen": str(origen),
        "ejecutable": str(destino),
        "sha256": huella,
        "version": version,
        "tipo": "instalacion-local-copiada",
    }
    return [str(destino), *argv[1:]], procedencia


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
    nombre = agente or Path(cmd[0]).stem.lower()
    procedencia = None
    if os.name == "nt":
        raices = [
            Path(sys.executable).resolve().parent,
            Path(os.environ["SystemRoot"]),
        ]
        if Path(cmd[0]).stem.lower() == "codex":
            ejecutable = Path(cmd[0])
            broker = (
                ejecutable.resolve() if ejecutable.is_absolute() else _codex_windows()
            )
            if broker.is_relative_to(espacio) or broker.is_relative_to(runtime):
                raise RuntimeError("el broker de aislamiento pertenece al trabajador")
            raices.append(broker.parent)
            argv = [str(broker), *cmd[1:]]
        else:
            from motor.plataforma.windows import argv_windows

            argv = argv_windows(cmd, env)
            if not Path(argv[0]).is_absolute():
                ejecutable = shutil.which(argv[0], path=env.get("PATH"))
                if not ejecutable:
                    raise FileNotFoundError(argv[0])
                argv = [str(Path(ejecutable).resolve()), *argv[1:]]
        if nombre == "muse":
            argv, procedencia = _muse_local(argv, runtime, env)
        raices.extend(path.resolve() for path in lecturas_extra)
        if nombre == "kimi":
            raices.append(Path(argv[0]).parent)
            env["NODE_OPTIONS"] = "--preserve-symlinks --preserve-symlinks-main"
        raices.extend(
            Path(os.environ[k])
            for k in ("ProgramFiles", "ProgramFiles(x86)")
            if k in os.environ
        )
        if suscripcion and nombre not in ("codex", "muse", "kimi"):
            raise RuntimeError("el agente no tiene adaptador Windows de suscripcion")
        if suscripcion and nombre == "codex":
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
                    for host in {
                        "codex": ("chatgpt.com", "auth.openai.com"),
                        "muse": ("api.meta.ai",),
                        "kimi": ("auth.kimi.com", "api.kimi.com"),
                    }[nombre]
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
        if nombre in ("muse", "kimi"):
            from motor.plataforma.psec_windows import enumeracion_para

            limites["enumeracion"] = enumeracion_para(limites)
        return ComandoAislado(argv, env, "appcontainer", limites, procedencia)
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
