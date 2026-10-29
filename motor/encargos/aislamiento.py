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

from .autenticacion_kimi import acceso_kimi
from .catalogo_agentes import ADAPTADORES, cliente_para
from .suscripciones import CuentaNoDisponible, leer_cuenta


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
    if os.name == "nt":
        env["PATH"] = (
            str(Path(sys.executable).resolve().parent)
            + os.pathsep
            + env.get("PATH", "")
        )
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
        if nombre in ("claude", "opencode", "qwen", "grok"):
            from .suscripciones import preparar

            preparar(nombre, runtime, cmd, env)
            return env
        if nombre == "muse":
            cuenta = leer_cuenta(nombre)
            documento = json.loads(
                (Path.home() / ".config/muse/auth.json").read_text(encoding="utf-8")
            )
            destino = runtime / ".config/muse/auth.json"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(
                json.dumps(
                    {
                        "schema_version": documento.get("schema_version", 1),
                        "providers": {"meta": cuenta},
                    }
                ),
                encoding="utf-8",
            )
            destino.chmod(0o600)
            return env
        if nombre == "kimi":
            expira = _preparar_kimi(runtime, cmd)
            env.update(
                KIMI_CODE_HOME=str(runtime / ".kimi-code"),
                MOTOR_KIMI_ACCESS_EXPIRES_AT=str(expira),
                KIMI_CODE_NO_AUTO_UPDATE="1",
                KIMI_DISABLE_TELEMETRY="1",
                KIMI_CODE_MODEL_CATALOG_REFRESH_ON_START="false",
            )
            if cliente_para(nombre) == "opencode":
                from .suscripciones import preparar_kimi_opencode

                preparar_kimi_opencode(runtime, env)
            return env
        if nombre == "codex":
            origen = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            destino = runtime / ".codex"
        else:
            return env
        auth = origen / "auth.json"
        if auth.is_file():
            if nombre == "codex":
                try:
                    datos = json.loads(auth.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raise CuentaNoDisponible(
                        nombre, "no se pudo leer el login de suscripcion"
                    ) from None
                if datos.get("auth_mode") != "chatgpt" or datos.get("OPENAI_API_KEY"):
                    raise CuentaNoDisponible(
                        nombre,
                        "el encargo necesita autenticacion por suscripcion de ChatGPT",
                    )
            destino.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(auth, destino / "auth.json")
            (destino / "auth.json").chmod(0o600)
    return env


def _preparar_kimi(runtime: Path, cmd: list[str]) -> float:
    origen = Path(os.environ.get("KIMI_CODE_HOME", str(Path.home() / ".kimi-code")))
    try:
        config = tomllib.loads((origen / "config.toml").read_text(encoding="utf-8"))
        alias = (
            cmd[cmd.index("--model") + 1]
            if "--model" in cmd
            else config["default_model"]
        )
        if alias.startswith("motor-kimi/"):
            alias = "kimi-code/" + alias.partition("/")[2]
        if alias == "kimi-code/k3" and alias not in config["models"]:
            modelo = {
                "provider": "managed:kimi-code",
                "model": "k3",
                "max_context_size": 1048576,
                "capabilities": [
                    "thinking",
                    "always_thinking",
                    "image_in",
                    "video_in",
                    "tool_use",
                ],
                "support_efforts": ["low", "high", "max"],
                "default_effort": "high",
            }
        else:
            modelo = config["models"][alias]
        proveedor = config["providers"][modelo["provider"]]
    except (OSError, ValueError, TypeError, KeyError):
        raise CuentaNoDisponible(
            "kimi", "no se pudo leer el modelo de la suscripcion configurada"
        ) from None
    oauth = proveedor.get("oauth", {})
    base_url = proveedor.get("base_url", "").rstrip("/")
    oauth_host = oauth.get("oauth_host", "https://auth.kimi.com").rstrip("/")
    pares = {
        "https://api.kimi.com/coding/v1": "https://auth.kimi.com",
        "https://api.kimi.ai/coding/v1": "https://auth.kimi.ai",
    }
    clave = oauth.get("key", "")
    if (
        modelo["provider"] != "managed:kimi-code"
        or proveedor.get("api_key")
        or proveedor.get("api_key_env")
        or pares.get(base_url) != oauth_host
        or not isinstance(clave, str)
        or not clave.startswith("oauth/")
    ):
        raise CuentaNoDisponible(
            "kimi", "el encargo necesita la suscripcion OAuth de Kimi Code"
        )
    destino = runtime / ".kimi-code"
    try:
        acceso = acceso_kimi(
            origen, credencial=clave.removeprefix("oauth/"), oauth_host=oauth_host
        )
    except RuntimeError as exc:
        raise CuentaNoDisponible("kimi", str(exc)) from None
    destino.mkdir(parents=True, exist_ok=True)
    for relativo in ("device_id", "region"):
        fuente = origen / relativo
        if fuente.is_file():
            shutil.copyfile(fuente, destino / relativo)
            (destino / relativo).chmod(0o600)
    texto = [
        f"default_model = {json.dumps(alias)}",
        '[providers."managed:kimi-code"]',
        'type = "kimi"',
        "api_key = " + json.dumps(acceso.token),
        "base_url = " + json.dumps(base_url),
        "[models." + json.dumps(alias) + "]",
        'provider = "managed:kimi-code"',
    ]
    for clave in (
        "model",
        "max_context_size",
        "max_output_size",
        "capabilities",
        "support_efforts",
        "default_effort",
    ):
        if clave in modelo:
            texto.append(clave + " = " + json.dumps(modelo[clave]))
    (destino / "config.toml").write_text("\n".join(texto) + "\n", encoding="utf-8")
    (destino / "config.toml").chmod(0o600)
    return acceso.expira


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
        if nombre in ADAPTADORES and ADAPTADORES[nombre].psec:
            raices.append(Path(argv[0]).parent)
            env["NODE_OPTIONS"] = "--preserve-symlinks --preserve-symlinks-main"
        raices.extend(
            Path(os.environ[k])
            for k in ("ProgramFiles", "ProgramFiles(x86)")
            if k in os.environ
        )
        if suscripcion and nombre not in ADAPTADORES:
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
                    for host in ADAPTADORES[nombre].dominios
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
        if nombre in ADAPTADORES and ADAPTADORES[nombre].psec:
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
    if nombre == "muse":
        from .autenticacion_muse import ejecutable_muse

        nativo = ejecutable_muse()
        cmd = [str(nativo), *cmd[1:]]
        perfil.append(f"(allow file-read* (literal {json.dumps(str(nativo))}))")
        procedencia = {
            "origen": str(nativo),
            "ejecutable": str(nativo),
            "sha256": hashlib.sha256(nativo.read_bytes()).hexdigest(),
            "tipo": "instalacion-local-firmada",
        }
    for path in (espacio, runtime, *(p.resolve() for p in lecturas_extra)):
        perfil.append(f"(allow file-read* (subpath {json.dumps(str(path))}))")
    for path in (runtime,) if lectura else (espacio, runtime):
        perfil.append(f"(allow file-write* (subpath {json.dumps(str(path))}))")
    for path in ("/dev/null", "/dev/tty"):
        perfil.append(f'(allow file-write* (literal "{path}"))')
    path = runtime / "jaula.sb"
    path.write_text("\n".join(perfil), encoding="utf-8")
    return ComandoAislado(
        [str(sandbox), "-f", str(path), *cmd], env, "seatbelt", ejecutable=procedencia
    )
