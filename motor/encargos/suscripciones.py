"""Entrega al runtime únicamente las cuentas de suscripción autorizadas."""

from __future__ import annotations
from motor.io import escribir_texto, leer_json, leer_texto

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import tomllib

from .agentes import SIN_SEGUNDO_PLANO
from .catalogo_agentes import DEEPSEEK_API_PROVEEDOR
from .lectura import Error as ErrorLectura
from .lectura import Leido, Pendiente, Lectura

_TOKEN_PLAN = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
_OPENCODE_PLANES = ("opencode-go", "zai-coding-plan")
_OPENCODE_CUENTAS = (*_OPENCODE_PLANES, DEEPSEEK_API_PROVEEDOR)
_GROK_CUENTA = "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828"


class CuentaNoDisponible(RuntimeError):
    def __init__(self, agente: str, motivo: str):
        self.agente = agente
        super().__init__(f"Cuenta de {agente}: {motivo}")


def _guardar(path: Path, datos: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    escribir_texto(path, json.dumps(datos, ensure_ascii=False))
    path.chmod(0o600)


def _config_opencode(runtime: Path, **campos) -> str:
    """La configuración de OpenCode del intento. Deja leer sin preguntar la guía poteto-mode que
    `exigir_poteto` copia al runtime, también a los ayudantes: `opencode run --auto` contesta
    solo a la sesión raíz hasta la v1.17 y una pregunta sin respuesta deja el turno colgado.
    Lo demás del runtime, que guarda las credenciales del intento, sigue preguntando."""
    guia = {(runtime / c).as_posix() + "/**": "allow" for c in ("poteto", "skills")}
    return json.dumps(
        {
            **campos,
            "share": "disabled",
            "autoupdate": False,
            "permission": {"external_directory": guia},
        }
    )


def _sin_elemento(detalle: str) -> bool:
    bajo = detalle.lower()
    return "could not be found" in bajo or "not be found" in bajo


def _claude_existe() -> Lectura:
    """Que la sesión de Claude exista, sin leer el secreto.

    macOS revoca el acceso al secreto cada vez que el CLI renueva la sesión;
    preguntar por los atributos del elemento del Llavero no pide ese permiso.
    Así la disponibilidad no depende de una lectura que macOS puede negar.
    """
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if token:
        return (
            Leido(True)
            if token.startswith("sk-ant-oat")
            else ErrorLectura("Claude necesita un token OAuth de suscripcion")
        )
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    if (home / ".credentials.json").is_file():
        return Leido(True)
    if sys.platform != "darwin":
        return Pendiente("")
    try:
        resultado = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ErrorLectura(f"no se pudo consultar el Llavero: {exc}")
    detalle = (resultado.stderr or resultado.stdout or "").strip()[-300:]
    if resultado.returncode == 0:
        return Leido(True)
    if _sin_elemento(detalle):
        return Pendiente("")
    return ErrorLectura(
        "no se pudo consultar el Llavero"
        + (f": {detalle}" if detalle else "")
    )


def _qwen() -> tuple[dict, str]:
    home = Path(os.environ.get("QWEN_HOME", str(Path.home() / ".qwen")))
    datos = leer_json((home / "settings.json"))
    model = datos.get("model", {})
    if (
        datos.get("security", {}).get("auth", {}).get("selectedType") != "openai"
        or model.get("baseUrl", "").rstrip("/") != _TOKEN_PLAN
    ):
        raise RuntimeError("Qwen necesita la suscripcion Alibaba Token Plan autorizada")
    clave = datos.get("env", {}).get("BAILIAN_TOKEN_PLAN_API_KEY") or os.environ.get(
        "BAILIAN_TOKEN_PLAN_API_KEY"
    )
    if not isinstance(clave, str) or not clave:
        raise RuntimeError("falta la cuenta Alibaba Token Plan de Qwen")
    return datos, clave


def _opencode() -> dict:
    home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    archivo = home / "opencode/auth.json"
    datos = leer_json(archivo) if archivo.is_file() else {}
    cuentas = {
        p: datos[p]
        for p in _OPENCODE_CUENTAS
        if isinstance(datos.get(p), dict)
        and datos[p].get("type") == "api"
        and datos[p].get("key")
    }
    db = home / "opencode/opencode.db"
    if db.is_file():
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conexion:
            columnas = {
                fila[1] for fila in conexion.execute("PRAGMA table_info(credential)")
            }
            if {"integration_id", "value", "active", "time_updated"} <= columnas:
                for proveedor in _OPENCODE_CUENTAS:
                    fila = conexion.execute(
                        "SELECT value FROM credential WHERE integration_id=? AND active=1 ORDER BY time_updated DESC LIMIT 1",
                        (proveedor,),
                    ).fetchone()
                    if not fila:
                        registros, seleccionadas = conexion.execute(
                            "SELECT COUNT(*),COUNT(active) FROM credential WHERE integration_id=?",
                            (proveedor,),
                        ).fetchone()
                        if registros and seleccionadas:
                            cuentas.pop(proveedor, None)
                            continue
                        if registros:
                            fila = conexion.execute(
                                "SELECT value FROM credential WHERE integration_id=? ORDER BY time_updated DESC LIMIT 1",
                                (proveedor,),
                            ).fetchone()
                    if fila:
                        cuenta = json.loads(fila[0])
                        if cuenta.get("type") not in ("key", "api") or not cuenta.get(
                            "key"
                        ):
                            raise RuntimeError(
                                "credencial de suscripcion OpenCode invalida"
                            )
                        cuentas[proveedor] = {"type": "api", "key": cuenta["key"]}
    return cuentas


def _grok() -> dict:
    home = Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok")))
    cuenta = leer_json((home / "auth.json")).get(
        _GROK_CUENTA, {}
    )
    if (
        cuenta.get("auth_mode") != "oidc"
        or cuenta.get("oidc_issuer") != "https://auth.x.ai"
        or not cuenta.get("key")
    ):
        raise RuntimeError("Grok necesita el login OAuth de la suscripcion")
    _expira_grok(cuenta)
    return cuenta


def _expira_grok(cuenta: dict) -> float:
    return datetime.fromisoformat(
        cuenta["expires_at"].replace("Z", "+00:00")
    ).timestamp()


def disponible(agente: str) -> Lectura:
    """La sesión del agente en este host, como lectura tipada.

    Para Claude no se lee el secreto: basta con saber que la sesión existe. Un
    fallo de lectura conserva su causa; una sesión que de verdad falta queda
    como pendiente, no como error.
    """
    if agente == "claude":
        return _claude_existe()
    lector = {"qwen": _qwen, "opencode": _opencode, "grok": _grok}.get(agente)
    if lector is None:
        return Pendiente("")
    try:
        return Leido(lector())
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        KeyError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as exc:
        return ErrorLectura(str(exc) or type(exc).__name__)


def leer_cuenta(agente: str):
    try:
        if agente == "muse":
            # El lanzamiento de Muse ya no llama a esta exportación: el CLI
            # lee el llavero del usuario y el motor no exporta su token.
            from .autenticacion_muse import cuenta_muse

            return cuenta_muse(exportar=True)
        return {"qwen": _qwen, "opencode": _opencode, "grok": _grok}[
            agente
        ]()
    except RuntimeError as exc:
        raise CuentaNoDisponible(agente, str(exc)) from None
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ):
        raise CuentaNoDisponible(
            agente,
            "no se pudo leer la credencial de suscripcion del host; revisa el login de esa cuenta",
        ) from None


def preparar(agente: str, runtime: Path, cmd: list[str], env: dict[str, str]) -> None:
    if agente == "grok":
        cuenta = leer_cuenta(agente)
        if _expira_grok(cuenta) <= time.time() + 60:
            prefijo = cmd[: cmd.index("--model")]
            try:
                resultado = subprocess.run(
                    [*prefijo, "models"],
                    capture_output=True,
                    text=True,
                    timeout=45,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                raise CuentaNoDisponible(
                    agente, "no se pudo renovar la suscripcion en el host"
                ) from None
            if resultado.returncode:
                raise CuentaNoDisponible(
                    agente, "no se pudo renovar la suscripcion en el host"
                )
            cuenta = leer_cuenta(agente)
        expira = _expira_grok(cuenta)
        if expira <= time.time():
            raise CuentaNoDisponible(agente, "el acceso necesita renovarse en el host")
        _guardar(
            runtime / ".grok/auth.json",
            {_GROK_CUENTA: {k: v for k, v in cuenta.items() if k != "refresh_token"}},
        )
        env.update(
            GROK_HOME=str(runtime / ".grok"), MOTOR_GROK_ACCESS_EXPIRES_AT=str(expira)
        )
    elif agente == "claude":
        # El CLI lee su propia sesión del Llavero. Cuando macOS renueva la
        # llave, la lectura del secreto desde el motor (`security -w`) deja de
        # estar permitida, pero el CLI sí puede usarla. Por eso aquí no se lee
        # el secreto ni se exporta: solo se copia la copia en archivo si existe.
        home = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        if home.resolve() == (runtime / ".claude").resolve():
            home = Path.home() / ".claude"
        archivo = home / ".credentials.json"
        destino = runtime / ".claude/.credentials.json"
        if archivo.is_file() and archivo.resolve() != destino.resolve():
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archivo, destino)
            destino.chmod(0o600)
        env.update(
            CLAUDE_CONFIG_DIR=str(runtime / ".claude"),
            # El CLI deriva el nombre del ítem del Llavero de esta variable:
            # con un valor no vacío añade un sufijo que el login del usuario
            # nunca creó; vacío usa el mismo ítem sin sufijo que _claude_existe.
            CLAUDE_SECURESTORAGE_CONFIG_DIR="",
            DISABLE_AUTOUPDATER="1",
            DISABLE_TELEMETRY="1",
            DISABLE_ERROR_REPORTING="1",
            # El lanzamiento corre con --print: nada en segundo plano puede
            # sobrevivir al turno. El argv trae --disallowedTools para las
            # herramientas de espera y agendado; esto quita run_in_background
            # del esquema de Bash y del subagente
            # (motor/encargos/agentes.py, SIN_SEGUNDO_PLANO).
            **SIN_SEGUNDO_PLANO,
        )
        _guardar(
            runtime / ".claude/settings.json",
            {"skipDangerousModePermissionPrompt": True},
        )
    elif agente == "qwen":
        datos, clave = leer_cuenta(agente)
        modelos = [
            {
                k: m[k]
                for k in (
                    "id",
                    "name",
                    "baseUrl",
                    "envKey",
                    "generationConfig",
                    "capabilities",
                )
                if k in m
            }
            for m in datos.get("modelProviders", {}).get("openai", [])
            if m.get("baseUrl", "").rstrip("/") == _TOKEN_PLAN
            and m.get("envKey") == "BAILIAN_TOKEN_PLAN_API_KEY"
        ]
        _guardar(
            runtime / ".qwen/settings.json",
            {
                "security": {"auth": {"selectedType": "openai"}},
                "model": {
                    k: datos["model"][k]
                    for k in ("name", "baseUrl", "reasoningEffort")
                    if k in datos["model"]
                },
                "modelProviders": {"openai": modelos},
                "ui": {"autoModeAcknowledged": True},
            },
        )
        env.update(
            QWEN_HOME=str(runtime / ".qwen"),
            BAILIAN_TOKEN_PLAN_API_KEY=clave,
            QWEN_CODE_NO_RELAUNCH="true",
        )
    elif agente == "opencode":
        proveedor = cmd[cmd.index("--model") + 1].partition("/")[0]
        cuentas = leer_cuenta(agente)
        if proveedor not in cuentas:
            motivo = (
                "falta la credencial propia de deepseek; este encargo no usa OpenCode Go"
                if proveedor == DEEPSEEK_API_PROVEEDOR
                else "falta la cuenta de suscripcion solicitada"
            )
            raise CuentaNoDisponible(agente, motivo)
        _guardar(
            runtime / ".local/share/opencode/auth.json", {proveedor: cuentas[proveedor]}
        )
        env.update(
            OPENCODE_DISABLE_AUTOUPDATE="1",
            OPENCODE_DISABLE_SHARE="1",
            OPENCODE_DISABLE_LSP_DOWNLOAD="1",
            OPENCODE_DISABLE_MCP="1",
        )
        env["OPENCODE_CONFIG_CONTENT"] = _config_opencode(runtime, enabled_providers=[proveedor])


def preparar_kimi_opencode(runtime: Path, env: dict[str, str]) -> None:
    (runtime / ".local/share/opencode").mkdir(parents=True, exist_ok=True)
    config = tomllib.loads(
        leer_texto((runtime / ".kimi-code/config.toml"))
    )
    alias = config["default_model"]
    proveedor = config["providers"]["managed:kimi-code"]
    modelo = config["models"][alias]
    api_modelo = alias.partition("/")[2]
    if modelo["model"] != api_modelo:
        raise CuentaNoDisponible(
            "kimi", "el modelo solicitado no coincide con la identidad del Coding Plan"
        )
    device = leer_texto((runtime / ".kimi-code/device_id")).strip()
    modelo_cliente = {
        "name": api_modelo,
        "tool_call": True,
        "variants": {"high": {"reasoningEffort": "high"}},
    }
    if "max_context_size" in modelo:
        modelo_cliente["limit"] = {
            "context": modelo["max_context_size"],
            "output": modelo.get(
                "max_output_size", 131072 if api_modelo == "k3" else 0
            ),
        }
    env["OPENCODE_CONFIG_CONTENT"] = _config_opencode(
        runtime,
        enabled_providers=["motor-kimi"],
        provider={
            "motor-kimi": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Kimi Code OAuth",
                "options": {
                    "baseURL": proveedor["base_url"],
                    "apiKey": proveedor["api_key"],
                    "headers": {
                        "User-Agent": "opencode/1.19.4",
                        "X-Msh-Platform": "kimi_code_cli",
                        "X-Msh-Version": "2.3.3",
                        "X-Msh-Device-Id": device,
                    },
                },
                "models": {api_modelo: modelo_cliente},
            }
        },
    )
    env.update(
        OPENCODE_DISABLE_AUTOUPDATE="1",
        OPENCODE_DISABLE_SHARE="1",
        OPENCODE_DISABLE_MODELS_FETCH="1",
        OPENCODE_DISABLE_LSP_DOWNLOAD="1",
        OPENCODE_DISABLE_MCP="1",
    )
