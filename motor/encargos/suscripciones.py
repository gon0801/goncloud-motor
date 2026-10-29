"""Entrega al runtime únicamente las cuentas de suscripción autorizadas."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import tomllib

_TOKEN_PLAN = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
_OPENCODE_PLANES = ("opencode-go", "zai-coding-plan")
_GROK_CUENTA = "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828"


class CuentaNoDisponible(RuntimeError):
    def __init__(self, agente: str, motivo: str):
        self.agente = agente
        super().__init__(f"Cuenta de {agente}: {motivo}")


def _guardar(path: Path, datos: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)


def _claude() -> dict:
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if token:
        if not token.startswith("sk-ant-oat"):
            raise RuntimeError("Claude necesita un token OAuth de suscripcion")
        return {"accessToken": token, "scopes": ["user:inference"]}
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    archivo = home / ".credentials.json"
    if archivo.is_file():
        datos = json.loads(archivo.read_text(encoding="utf-8"))
    elif sys.platform == "darwin":
        resultado = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if resultado.returncode:
            raise RuntimeError("no se pudo leer la suscripcion Claude del host")
        datos = json.loads(resultado.stdout)
    else:
        raise RuntimeError("falta la suscripcion Claude del host")
    cuenta = datos.get("claudeAiOauth", {})
    if not cuenta.get("accessToken") or "user:inference" not in cuenta.get(
        "scopes", []
    ):
        raise RuntimeError(
            "Claude necesita OAuth de suscripcion con acceso a inferencia"
        )
    if cuenta.get("expiresAt") is not None and not math.isfinite(
        float(cuenta["expiresAt"])
    ):
        raise RuntimeError("el vencimiento OAuth de Claude es invalido")
    return cuenta


def _qwen() -> tuple[dict, str]:
    home = Path(os.environ.get("QWEN_HOME", str(Path.home() / ".qwen")))
    datos = json.loads((home / "settings.json").read_text(encoding="utf-8"))
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
    datos = json.loads(archivo.read_text(encoding="utf-8")) if archivo.is_file() else {}
    cuentas = {
        p: datos[p]
        for p in _OPENCODE_PLANES
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
                for proveedor in _OPENCODE_PLANES:
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
    cuenta = json.loads((home / "auth.json").read_text(encoding="utf-8")).get(
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


def disponible(agente: str) -> bool:
    try:
        return bool(
            {"claude": _claude, "qwen": _qwen, "opencode": _opencode, "grok": _grok}[
                agente
            ]()
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        KeyError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ):
        return False


def leer_cuenta(agente: str):
    try:
        if agente == "muse":
            from .autenticacion_muse import cuenta_muse

            return cuenta_muse(exportar=True)
        return {"claude": _claude, "qwen": _qwen, "opencode": _opencode, "grok": _grok}[
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
        cuenta = leer_cuenta(agente)
        expira = float(cuenta.get("expiresAt") or 0) / 1000
        if expira and expira <= time.time():
            inicio_flags = (
                cmd.index("--safe-mode")
                if "--safe-mode" in cmd
                else cmd.index("--model")
            )
            try:
                subprocess.run(
                    [*cmd[:inicio_flags], "--print", "--safe-mode"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=45,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                raise CuentaNoDisponible(
                    agente, "no se pudo renovar la suscripcion en el host"
                ) from None
            cuenta = leer_cuenta(agente)
            expira = float(cuenta.get("expiresAt") or 0) / 1000
            if expira and expira <= time.time():
                raise CuentaNoDisponible(
                    agente,
                    "el cliente oficial no pudo renovar el acceso de suscripcion en el host",
                )
        env.update(
            CLAUDE_CONFIG_DIR=str(runtime / ".claude"),
            CLAUDE_CODE_OAUTH_TOKEN=cuenta["accessToken"],
            DISABLE_AUTOUPDATER="1",
            DISABLE_TELEMETRY="1",
            DISABLE_ERROR_REPORTING="1",
        )
        if expira:
            env["MOTOR_CLAUDE_ACCESS_EXPIRES_AT"] = str(expira)
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
            raise CuentaNoDisponible(
                agente, "falta la cuenta de suscripcion solicitada"
            )
        _guardar(
            runtime / ".local/share/opencode/auth.json", {proveedor: cuentas[proveedor]}
        )
        env.update(
            OPENCODE_DISABLE_AUTOUPDATE="1",
            OPENCODE_DISABLE_SHARE="1",
            OPENCODE_DISABLE_LSP_DOWNLOAD="1",
            OPENCODE_DISABLE_MCP="1",
        )
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            {"enabled_providers": [proveedor], "share": "disabled", "autoupdate": False}
        )


def preparar_kimi_opencode(runtime: Path, env: dict[str, str]) -> None:
    (runtime / ".local/share/opencode").mkdir(parents=True, exist_ok=True)
    config = tomllib.loads(
        (runtime / ".kimi-code/config.toml").read_text(encoding="utf-8")
    )
    alias = config["default_model"]
    proveedor = config["providers"]["managed:kimi-code"]
    modelo = config["models"][alias]
    api_modelo = alias.partition("/")[2]
    if modelo["model"] != api_modelo:
        raise CuentaNoDisponible(
            "kimi", "el modelo solicitado no coincide con la identidad del Coding Plan"
        )
    device = (runtime / ".kimi-code/device_id").read_text(encoding="utf-8").strip()
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
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        {
            "enabled_providers": ["motor-kimi"],
            "autoupdate": False,
            "share": "disabled",
            "provider": {
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
        }
    )
    env.update(
        OPENCODE_DISABLE_AUTOUPDATE="1",
        OPENCODE_DISABLE_SHARE="1",
        OPENCODE_DISABLE_MODELS_FETCH="1",
        OPENCODE_DISABLE_LSP_DOWNLOAD="1",
        OPENCODE_DISABLE_MCP="1",
    )
