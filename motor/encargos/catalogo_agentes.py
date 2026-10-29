"""Identidades y capacidades de los clientes de suscripción del motor."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Adaptador:
    prefijos: tuple[str, ...]
    modelo: str
    dominios: tuple[str, ...]
    psec: bool = True
    cliente_windows: str = ""


ADAPTADORES = {
    "codex": Adaptador(
        ("openai/",), "openai/gpt-6.1-sol", ("chatgpt.com", "auth.openai.com"), False
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
}


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
    return (
        adaptador.cliente_windows
        if os.name == "nt" and adaptador and adaptador.cliente_windows
        else agente
    )
