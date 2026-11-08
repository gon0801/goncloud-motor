"""Identidades y capacidades de los clientes de suscripción del motor."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Adaptador:
    prefijos: tuple[str, ...]
    modelo: str
    dominios: tuple[str, ...]
    cliente_windows: str = ""
    # Nombre del ejecutable cuando no coincide con la clave del catálogo.
    # En Windows, `agy` resuelve `agy.exe` por PATHEXT; no hace falta el sufijo.
    cliente: str = ""


ADAPTADORES = {
    "codex": Adaptador(
        ("openai/",), "openai/gpt-6.1-sol", ("chatgpt.com", "auth.openai.com"),
    ),
    "grok": Adaptador(
        ("xai/",), "xai/grok-4.7", ("cli-chat-proxy.grok.com", "api.x.ai")
    ),
    "muse": Adaptador(("meta/",), "meta/muse-wonder-1.0", ("api.meta.ai",)),
    "kimi": Adaptador(
        ("kimi-code/", "kimi/"),
        "kimi-code/k3.1-pro",
        ("api.kimi.com", "api.kimi.ai"),
        cliente_windows="opencode",
    ),
    "claude": Adaptador(
        ("anthropic/",),
        "anthropic/claude-sonnet-5-5",
        ("api.anthropic.com", "claude.ai"),
    ),
    "opencode": Adaptador(
        ("opencode-go/", "zai/", "zai-coding-plan/"),
        "opencode-go/deepseek-v4.1-flash",
        ("opencode.ai", "api.z.ai"),
    ),
    "qwen": Adaptador(
        ("qwen/",), "qwen/qwen3.8-max", ("token-plan.ap-southeast-1.maas.aliyuncs.com",)
    ),
    "antigravity": Adaptador(
        ("antigravity/",),
        "antigravity/gemini-4-argon-high",
        (),
        cliente="agy",
    ),
}


# David, 1-nov: por OpenCode Go solo corre DeepSeek. El plan no se abre a
# cualquier `opencode-go/*` aunque el cliente sepa hablarlo.
OPENCODE_GO_UNICO = "opencode-go/deepseek-v4.1-flash"


def validar_modelo(referencia: str) -> None:
    """Rechaza un modelo que el plan no autoriza, antes de encolarlo."""
    if referencia.startswith("opencode-go/") and referencia != OPENCODE_GO_UNICO:
        raise ValueError(
            "OpenCode Go solo admite "
            f"{OPENCODE_GO_UNICO}; recibido: {referencia}"
        )


def modelo_canonico(referencia: str) -> str:
    if referencia.startswith("kimi/"):
        return "kimi-code/" + referencia.partition("/")[2]
    if referencia.startswith("zai/"):
        return "zai-coding-plan/" + referencia.partition("/")[2]
    return referencia


def agente_del_modelo(referencia: str) -> str:
    return next(
        (
            nombre
            for nombre, adaptador in ADAPTADORES.items()
            if referencia.startswith(adaptador.prefijos)
        ),
        "",
    )


def identidad_modelo(referencia: str) -> str:
    """El plan de suscripción no cambia la identidad del modelo."""
    return modelo_canonico(referencia).partition("/")[2] or referencia


def cliente_para(agente: str) -> str:
    adaptador = ADAPTADORES.get(agente)
    if adaptador is None:
        return agente
    if os.name == "nt" and adaptador.cliente_windows:
        return adaptador.cliente_windows
    if adaptador.cliente:
        return adaptador.cliente
    return agente
