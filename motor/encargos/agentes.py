"""Adaptadores de los CLI de suscripción, separados de su supervisión."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .catalogo_agentes import ADAPTADORES, cliente_para, modelo_canonico


def _disponible(agente: str, generacion: Path | None = None) -> bool:
    if agente in ("sleep", "ensayo"):
        return os.name != "nt" and Path("/usr/bin/sandbox-exec").is_file()
    if agente not in ADAPTADORES:
        return False
    from .aislamiento import usa_wsl

    if usa_wsl(agente):
        # El agente corre en la distro: cuentan su jaula, su ejecutable y su suscripcion
        # de alli, no los de Windows.
        from motor.plataforma import jaula_wsl

        return jaula_wsl.causa_no_disponible(agente) == ""
    if os.name == "nt":
        if ADAPTADORES[agente].psec:
            from motor.plataforma.psec_windows import disponible
        else:
            from motor.plataforma.jaula_windows import disponible

        if not disponible():
            return False
    if generacion is not None and (generacion / "runtime.json").is_file():
        from motor.flota.runtime import resolver_ejecutable

        try:
            resolver_ejecutable(
                generacion, [cliente_para(agente)], requerido=agente != "muse"
            )
        except (OSError, ValueError, TypeError):
            return False
        if agente == "muse" and not shutil.which(agente):
            return False
    elif not shutil.which(cliente_para(agente)):
        return False
    if os.name != "nt" and not Path("/usr/bin/sandbox-exec").is_file():
        return False
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
        from .autenticacion_muse import cuenta_muse

        try:
            return bool(cuenta_muse())
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            return False
    if agente == "kimi":
        import tomllib

        try:
            origen = Path(
                os.environ.get("KIMI_CODE_HOME", str(Path.home() / ".kimi-code"))
            )
            datos = tomllib.loads((origen / "config.toml").read_text(encoding="utf-8"))
            clave = datos["providers"]["managed:kimi-code"]["oauth"]["key"]
            return (
                isinstance(clave, str)
                and clave.startswith("oauth/kimi-code")
                and "/" not in clave.removeprefix("oauth/")
                and (
                    origen / "credentials" / (clave.removeprefix("oauth/") + ".json")
                ).is_file()
            )
        except (OSError, ValueError, TypeError, KeyError):
            return False
    if agente in ("claude", "opencode", "qwen", "grok"):
        from .suscripciones import disponible

        return disponible(agente)
    return False


def _modelos_para(agente: str, rol: str, generacion: Path | None) -> tuple[str, ...]:
    """Identidades proveedor/modelo que el agente podría correr para este
    rol, en orden de la política."""
    if generacion is not None and agente in ADAPTADORES:
        from motor.flota.runtime import modelos_de_chain

        try:
            refs = tuple(
                dict.fromkeys(
                    modelo_canonico(ref)
                    for prefijo in ADAPTADORES[agente].prefijos
                    for ref in modelos_de_chain(
                        generacion, "planear" if rol == "revisor" else "codigo", prefijo
                    )
                )
            )
        except (OSError, ValueError, TypeError):
            refs = ()
        if refs:
            return refs
    return (ADAPTADORES[agente].modelo if agente in ADAPTADORES else "",)


def _sesion_modo(enc, agente: str, rol: str) -> str:
    anteriores = [a for r, a, _m in enc.modelos if r == rol]
    if agente not in ADAPTADORES:
        return "nueva"
    if not anteriores:
        return "nueva"
    if anteriores[-1] != agente:
        return "reconstruida"
    viva = enc.viva_revisor if rol == "revisor" else enc.viva_responsable
    if not viva:
        return "reconstruida"
    if agente in ("codex", "muse", "kimi", "claude", "opencode", "qwen"):
        previa = enc.sesion_revisor if rol == "revisor" else enc.sesion_responsable
        if (
            agente == "kimi"
            and cliente_para(agente) == "opencode"
            and previa
            and not previa.startswith("ses_")
        ):
            return "reconstruida"
        return "reanudada" if previa else "reconstruida"
    return "reanudada"


def _lanzador(agente: str, modelo: str = "", sesion_modo: str = "nueva"):
    modelo = modelo_canonico(modelo)
    pelado = modelo.split("/", 1)[-1] if modelo else ""

    def dormir(enc, prompt: Path, espacio: Path, sesion: str):
        del enc, prompt, sesion
        return ["/bin/bash", "-c", "sleep 3600"]

    def grok(enc, prompt: Path, espacio: Path, sesion: str):
        previa = (
            enc.sesion_revisor
            if (enc.rol or "responsable") == "revisor"
            else enc.sesion_responsable
        )
        return [
            "grok",
            "--model",
            pelado or "grok-4.7",
            "--reasoning-effort",
            "high",
            "--cwd",
            str(espacio),
            *(
                ["--resume", previa]
                if sesion_modo == "reanudada" and previa
                else ["--session-id", sesion]
            ),
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
        if cliente_para("kimi") == "opencode":
            return opencode(enc, prompt, "motor-kimi/" + (pelado or "k3.1-pro"))
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

    def opencode(enc, prompt: Path, referencia: str):
        previa = (
            enc.sesion_revisor
            if (enc.rol or "responsable") == "revisor"
            else enc.sesion_responsable
        )
        return [
            "opencode",
            "run",
            "--pure",
            "--auto",
            "--format",
            "json",
            "--model",
            referencia,
            "--variant",
            "high",
            *(["--session", previa] if sesion_modo == "reanudada" and previa else []),
            "Lee el pedido y las instrucciones en "
            + str(prompt)
            + ". Ejecuta el trabajo autorizado.",
        ]

    def suscripcion(enc, prompt: Path, espacio: Path, sesion: str):
        del espacio
        previa = (
            enc.sesion_revisor
            if (enc.rol or "responsable") == "revisor"
            else enc.sesion_responsable
        )
        reanudar = sesion_modo == "reanudada" and previa
        texto = (
            "Lee el pedido y las instrucciones en "
            + str(prompt)
            + ". Ejecuta el trabajo autorizado."
        )
        if agente == "claude":
            return [
                "claude",
                "--safe-mode",
                "--print",
                "--verbose",
                "--output-format",
                "stream-json",
                "--model",
                pelado or "claude-sonnet-5-5",
                "--effort",
                "high",
                "--dangerously-skip-permissions",
                *(["--resume", previa] if reanudar else ["--session-id", sesion]),
                texto,
            ]
        if agente == "qwen":
            return [
                "qwen",
                "--auth-type",
                "openai",
                "--model",
                pelado or "qwen3.8-max",
                "--output-format",
                "stream-json",
                "--approval-mode",
                "yolo",
                *(["--resume", previa] if reanudar else ["--session-id", sesion]),
                "--prompt",
                texto,
            ]
        return opencode(enc, prompt, modelo or ADAPTADORES[agente].modelo)

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
    if agente in ("claude", "opencode", "qwen"):
        return suscripcion
    if agente == "ensayo":
        return ensayo
    return None
