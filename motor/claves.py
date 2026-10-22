"""Las claves que evitan anotar dos veces el mismo hecho (`ukey` de `Store.append`).

Este es el único módulo que arma una: una función por tipo de evento, y `POR_TIPO` las lista.
Dos reglas para que no vuelvan las claves demasiado gruesas (un hecho nuevo cae como repetido) ni
demasiado finas (el mismo hecho entra dos veces):
- la clave nombra todo lo que hace distinto al hecho (plan, bloque, SHA, y la huella del insumo si el
  resultado depende de él), y nada más;
- nunca lleva la hora del reloj: lo que puede repetirse de verdad (una falla otra vez, un merge
  rechazado otra vez) se numera con la cuenta de lo que ya hay en el log.
"""

from __future__ import annotations


def plan_cargado(plan: str, version: int) -> str:
    """Cada entrega con contenido distinto del vigente es una versión: volver a entregar un texto
    anterior es una versión nueva, no la misma."""
    return f"plan:{plan}:v{version}"


def comando(cmd_id: str) -> str:
    return f"cmd:{cmd_id}"


def nota(cmd_id: str) -> str:
    return f"nota:{cmd_id}"


def conf_rechazada(sha: str) -> str:
    return f"confmala:{sha}"


def version_faltante(maquina: str, pedida: str) -> str:
    return f"versionfalta:{maquina}:{pedida}"


def repo_fijado(plan: str, repo: str) -> str:
    return f"pin:{plan}:{repo}"


def intento_iniciado(plan: str, block: str, role: str, n: int) -> str:
    return f"start:{plan}:{block}:{role}:{n}"


def intento_terminado(plan: str, block: str, role: str, n: int) -> str:
    return f"ended:{plan}:{block}:{role}:{n}"


def cooldown(carpeta: str) -> str:
    return f"cooldown:{carpeta}"


def revisado(carpeta: str) -> str:
    return f"review:{carpeta}"


def _con_huella(base: str, huella: str, maquina: str) -> str:
    return f"{base}:{huella}:{maquina}" if huella or maquina else base


def verificado(
    plan: str, block: str, sha: str, huella: str = "", maquina: str = ""
) -> str:
    return _con_huella(f"verified:{plan}:{block}:{sha}", huella, maquina)


def pr_abierto(plan: str, block: str, numero: int) -> str:
    return f"pr:{plan}:{block}:{numero}"  # tras un merge, el arreglo abre otro PR


def marcado_listo(plan: str, block: str, sha: str) -> str:
    return f"ready:{plan}:{block}:{sha}"


def pr_cerrado(plan: str, block: str, numero: int) -> str:
    return f"prcerrado:{plan}:{block}:{numero}"


def checks_esperando(
    plan: str, block: str, sha: str, faltan: tuple[str, ...], github_termino: bool
) -> str:
    return f"espera:{plan}:{block}:{sha}:{','.join(faltan)}:{github_termino}"


def checks_resultado(
    plan: str, block: str, sha: str, rechazo: int | None = None
) -> str:
    """El resultado final de las pruebas de un SHA. `rechazo` numera los merges que GitHub rechazó
    sobre ese mismo SHA: cada uno es un resultado nuevo, no el mismo."""
    base = f"checks:{plan}:{block}:{sha}"
    return base if rechazo is None else f"rechazo:{plan}:{block}:{sha}:{rechazo}"


def integrado(plan: str, block: str, sha: str) -> str:
    return f"merged:{plan}:{block}:{sha}"


def verificacion_iniciada(plan: str, block: str, sha: str, n: int = 0) -> str:
    return f"verify-inicio:{plan}:{block}:{sha}:{n}"


def despliegue_iniciado(plan: str, block: str, sha: str, n: int = 0) -> str:
    base = f"deploy-inicio:{plan}:{block}:{sha}"
    return base if not n else f"{base}:{n}"


def prueba_iniciada(plan: str, block: str, sha: str, n: int = 0) -> str:
    return f"prove-inicio:{plan}:{block}:{sha}:{n}"


def desplegado(
    plan: str, block: str, sha: str, huella: str = "", maquina: str = "", vez: int = 0
) -> str:
    base = _con_huella(f"deployed:{plan}:{block}:{sha}", huella, maquina)
    return base if not vez else f"{base}:v{vez}"


def probado(
    plan: str, block: str, sha: str, huella: str = "", maquina: str = "", vez: int = 0
) -> str:
    base = _con_huella(f"proved:{plan}:{block}:{sha}", huella, maquina)
    return base if not vez else f"{base}:v{vez}"


def humano_hecho(plan: str, block: str) -> str:
    return f"humano:{plan}:{block}"


def limpiado(plan: str) -> str:
    return f"limpiado:{plan}"


def aviso_encolado(plan: str, key: str) -> str:
    return f"notice:{plan}:{key}"


def falla_de_efecto(plan: str, block: str, accion: str, n: int) -> str:
    """La falla número `n` de esa acción en ese bloque (la cuenta sale del log, no del reloj)."""
    return f"falla:{plan}:{block}:{accion}:{n}"


def efecto_ok(plan: str, block: str, accion: str, n: int) -> str:
    """El éxito que cierra la racha cuya última falla fue la número `n`."""
    return f"ok:{plan}:{block}:{accion}:{n}"


POR_TIPO = {
    "PlanLoaded": plan_cargado,
    "CommandReceived": comando,
    "Note": nota,
    "ConfRechazada": conf_rechazada,
    "VersionFaltante": version_faltante,
    "RepoPinned": repo_fijado,
    "AttemptStarted": intento_iniciado,
    "AttemptEnded": intento_terminado,
    "Cooldown": cooldown,
    "Reviewed": revisado,
    "Verified": verificado,
    "PrOpened": pr_abierto,
    "MarkedReady": marcado_listo,
    "PrCerrado": pr_cerrado,
    "ChecksEsperando": checks_esperando,
    "ChecksResult": checks_resultado,
    "Merged": integrado,
    "VerifyStarted": verificacion_iniciada,
    "DeployStarted": despliegue_iniciado,
    "ProveStarted": prueba_iniciada,
    "Deployed": desplegado,
    "Proved": probado,
    "HumanDone": humano_hecho,
    "Limpiado": limpiado,
    "NoticeQueued": aviso_encolado,
    "EffectFailed": falla_de_efecto,
    "EffectOk": efecto_ok,
}
