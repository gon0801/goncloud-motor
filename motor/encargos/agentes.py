"""Adaptadores de los CLI de suscripción, separados de su supervisión."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def _disponible(agente: str, generacion: Path | None = None) -> bool:
    if agente in ("sleep", "ensayo"):
        return os.name != "nt" and Path("/usr/bin/sandbox-exec").is_file()
    if agente == "grok" and os.name == "nt":
        return False
    if os.name == "nt":
        if agente in ("muse", "kimi"):
            from motor.plataforma.psec_windows import disponible
        else:
            from motor.plataforma.jaula_windows import disponible

        if not disponible():
            return False
    if generacion is not None and (generacion / "runtime.json").is_file():
        from motor.flota.runtime import resolver_ejecutable

        try:
            resolver_ejecutable(generacion, [agente], requerido=agente != "muse")
        except (OSError, ValueError, TypeError):
            return False
        if agente == "muse" and not shutil.which(agente):
            return False
    elif not shutil.which(agente):
        return False
    if os.name != "nt" and not Path("/usr/bin/sandbox-exec").is_file():
        return False
    if agente == "grok":
        return (Path.home() / ".grok" / "auth.json").is_file()
    if agente == "codex":
        path = (
            Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            / "auth.json"
        )
        try:
            datos = json.loads(path.read_text(encoding="utf-8"))
            return datos.get("auth_mode") == "chatgpt" and not datos.get(
                "OPENAI_API_KEY"
            )
        except (OSError, ValueError):
            return False
    if agente == "muse":
        try:
            datos = json.loads(
                (Path.home() / ".config/muse/auth.json").read_text(encoding="utf-8")
            )
            cuenta = datos["providers"]["meta"]
            return (
                isinstance(cuenta, dict)
                and cuenta.get("subs_active") is True
                and bool(cuenta.get("access_token"))
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False
    if agente == "kimi":
        return (Path.home() / ".kimi-code/credentials/kimi-code.json").is_file()
    return False


_MODELO_POR_DEFECTO = {
    "grok": "xai/grok-4.7",
    "codex": "openai/gpt-6.1-sol",
    "muse": "meta/muse-wonder-1.0",
    "kimi": "kimi-code/k3.1-pro",
}
_PREFIJO_PROVEEDOR = {
    "grok": "xai/",
    "codex": "openai/",
    "muse": "meta/",
    "kimi": "kimi-code/",
}


def _modelos_para(agente: str, rol: str, generacion: Path | None) -> tuple[str, ...]:
    """Identidades proveedor/modelo que el agente podría correr para este
    rol, en orden de la política."""
    if generacion is not None and agente in _PREFIJO_PROVEEDOR:
        from motor.flota.runtime import modelos_de_chain

        try:
            refs = modelos_de_chain(
                generacion,
                "planear" if rol == "revisor" else "codigo",
                _PREFIJO_PROVEEDOR[agente],
            )
        except (OSError, ValueError, TypeError):
            refs = ()
        if refs:
            return refs
    return (_MODELO_POR_DEFECTO.get(agente, ""),)


def _sesion_modo(enc, agente: str, rol: str) -> str:
    anteriores = [a for r, a, _m in enc.modelos if r == rol]
    if agente not in _PREFIJO_PROVEEDOR:
        return "nueva"
    if not anteriores:
        return "nueva"
    if anteriores[-1] != agente:
        return "reconstruida"
    viva = enc.viva_revisor if rol == "revisor" else enc.viva_responsable
    if not viva:
        return "reconstruida"
    if agente in ("codex", "muse", "kimi"):
        previa = enc.sesion_revisor if rol == "revisor" else enc.sesion_responsable
        return "reanudada" if previa else "reconstruida"
    return "reanudada"


def _lanzador(agente: str, modelo: str = "", sesion_modo: str = "nueva"):
    pelado = modelo.split("/", 1)[-1] if modelo else ""

    def dormir(enc, prompt: Path, espacio: Path, sesion: str):
        del enc, prompt, sesion
        return ["/bin/bash", "-c", "sleep 3600"]

    def grok(enc, prompt: Path, espacio: Path, sesion: str):
        del enc
        return [
            "grok",
            "--model",
            pelado or "grok-4.7",
            "--reasoning-effort",
            "high",
            "--cwd",
            str(espacio),
            "--session-id",
            sesion,
            "--permission-mode",
            "bypassPermissions",
            "--always-approve",
            "--no-plan",
            "--max-turns",
            "24",
            "--disable-web-search",
            "--output-format",
            "streaming-json",
            "--prompt-file",
            str(prompt),
        ]

    def codex(enc, prompt: Path, espacio: Path, sesion: str):
        del sesion
        texto = (
            "Lee el pedido y las instrucciones en "
            + str(prompt)
            + ". Ejecuta el trabajo autorizado."
        )
        comunes = [
            "--ignore-user-config",
            "--ignore-rules",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-m",
            pelado or "gpt-6.1-sol",
            "-c",
            'model_reasoning_effort="xhigh"',
        ]
        previa = (
            enc.sesion_revisor
            if (enc.rol or "responsable") == "revisor"
            else enc.sesion_responsable
        )
        if sesion_modo == "reanudada" and previa:
            return ["codex", "exec", "resume", *comunes, previa, texto]
        return [
            "codex",
            "exec",
            *comunes,
            "--cd",
            str(espacio),
            texto,
        ]

    def ensayo(enc, prompt: Path, espacio: Path, sesion: str):
        del enc, prompt, sesion
        return ["/bin/bash", str(espacio / "trabajo.sh")]

    def muse(enc, prompt: Path, espacio: Path, sesion: str):
        previa = (
            enc.sesion_revisor
            if (enc.rol or "responsable") == "revisor"
            else enc.sesion_responsable
        )
        identidad = previa if sesion_modo == "reanudada" and previa else sesion
        return [
            "muse",
            "exec",
            "--provider",
            "meta",
            "--model",
            pelado or "muse-wonder-1.0",
            "--json",
            "--session-id",
            identidad,
            "--workspace",
            str(espacio),
            "--prompt-file",
            str(prompt),
            "--yolo",
            "--no-foreign-personal-context",
            "--disable-web-tools",
            "--disable-reminders",
            "--disable-request-user-input",
        ]

    def kimi(enc, prompt: Path, espacio: Path, sesion: str):
        del espacio, sesion
        previa = (
            enc.sesion_revisor
            if (enc.rol or "responsable") == "revisor"
            else enc.sesion_responsable
        )
        texto = (
            "Lee el pedido y las instrucciones en "
            + str(prompt)
            + ". Ejecuta el trabajo autorizado."
        )
        argv = [
            "kimi",
            "--model",
            modelo or "kimi-code/k3.1-pro",
            "--output-format",
            "stream-json",
            "--prompt",
            texto,
        ]
        if sesion_modo == "reanudada" and previa:
            argv.extend(["--session", previa])
        return argv

    if agente == "sleep":
        return dormir
    if agente == "grok":
        return grok
    if agente == "codex":
        return codex
    if agente == "muse":
        return muse
    if agente == "kimi":
        return kimi
    if agente == "ensayo":
        return ensayo
    return None
