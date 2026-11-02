"""Adaptadores de los CLI de suscripción, separados de su supervisión."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .catalogo_agentes import ADAPTADORES, cliente_para, modelo_canonico

# El lanzamiento corre con --print: el proceso sale cuando el agente responde y
# nadie lo despierta, asi que una tarea en segundo plano se queda sin dueno.
# `Bash(run_in_background:true)` es una regla de parametro que niega esas
# llamadas; a secas, cada nombre quita del registro de la sesion la herramienta
# que espera o deja trabajo para despues del turno: Monitor (esperar), CronCreate
# (agendar), ScheduleWakeup (despertar despues) y Workflow (equipos de agentes
# en segundo plano, con run para reanudar despues). Las reglas de negacion
# siguen vigentes bajo --dangerously-skip-permissions. El valor va junto y
# antes de otro flag: --disallowedTools es variadica y se tragaria el prompt.
PROHIBIDO_SEGUNDO_PLANO = (
    "Bash(run_in_background:true),Monitor,CronCreate,ScheduleWakeup,Workflow"
)

# El subagente (Agent/Task) arranca en segundo plano por defecto y su resultado
# llega en un turno posterior, asi que ninguna regla de --disallowedTools lo
# cubre: un parametro omitido nunca coincide con una regla de parametro, y la
# regla del nombre pelado apagaria el subagente entero. Medido en el 2.1.310 de
# este host con una API local y documentado en env-vars: con esta variable el
# esquema de Bash y del subagente deja de tener run_in_background, la llamada
# explicita falla y el subagente corre en primer plano, dentro del turno.
# suscripciones.preparar lo pone en el entorno del lanzamiento de claude.
SIN_SEGUNDO_PLANO = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}

# Herramientas que el init de agy 1.4.12 anunció y que esperan o agendan
# después del turno. El esquema de permisos de settings.json no puede
# nombrarlas (solo command, read_file, write_file, read_url, execute_url,
# mcp y unsandboxed) y --mode no es un filtro de herramientas. El agente
# propio las omite de `tools` y pone excludeDefaultComponents, que el
# changelog 1.2.1 documenta como salida de las herramientas de fábrica.
HERRAMIENTAS_TRAS_EL_TURNO = frozenset(
    {
        "schedule",
        "wait",
        "wait_5_seconds",
        "manage_task",
        "send_command_input",
        "command_status",
        "run_workflow",
        "manage_inbox",
    }
)
HERRAMIENTAS_MEDIDAS = (
    "ask_custom_permission",
    "ask_permission",
    "ask_question",
    "browser_click_element",
    "browser_drag_pixel_to_pixel",
    "browser_get_dom",
    "browser_get_network_request",
    "browser_input",
    "browser_list_network_requests",
    "browser_mouse_down",
    "browser_mouse_up",
    "browser_move_mouse",
    "browser_press_key",
    "browser_refresh_page",
    "browser_resize_window",
    "browser_scroll",
    "browser_scroll_dom",
    "browser_select_option",
    "browser_subagent",
    "call_mcp_tool",
    "capture_browser_console_logs",
    "capture_browser_screenshot",
    "click_browser_pixel",
    "command_status",
    "define_subagent",
    "delete_knowledge",
    "execute_browser_javascript",
    "find_by_name",
    "finish",
    "generate_image",
    "grep_search",
    "invoke_subagent",
    "list_browser_pages",
    "list_dir",
    "list_permissions",
    "list_plugin_accounts",
    "list_resources",
    "manage_inbox",
    "manage_subagents",
    "manage_task",
    "multi_replace_file_content",
    "notebook_edit",
    "notebook_execution",
    "open_browser_url",
    "read_browser_page",
    "read_resource",
    "read_url_content",
    "replace_file_content",
    "run_command",
    "run_workflow",
    "schedule",
    "search_marketplace",
    "search_web",
    "sed_file",
    "send_command_input",
    "send_message",
    "view_file",
    "wait",
    "wait_5_seconds",
    "write_to_file",
)
HERRAMIENTAS_DEL_TURNO = tuple(
    nombre for nombre in HERRAMIENTAS_MEDIDAS if nombre not in HERRAMIENTAS_TRAS_EL_TURNO
)
AGENTE_TURNO = "motor-turno"


def markdown_agente_turno() -> str:
    herramientas = "\n".join(f"  - {nombre}" for nombre in HERRAMIENTAS_DEL_TURNO)
    return (
        "---\n"
        f"name: {AGENTE_TURNO}\n"
        "description: Turno del motor. Solo las herramientas de esta sesion.\n"
        "mainAgent: true\n"
        "subagent: false\n"
        "excludeDefaultComponents: true\n"
        "tools:\n"
        f"{herramientas}\n"
        "---\n"
        "\n"
        "Ejecuta el pedido de este turno.\n"
    )


# El llavero es del usuario del sistema. Un home de prueba no lo tiene, y
# `secret-tool lookup` / `security -w` imprimirían el secreto.
_TOPE_LLAVERO_S = 3


def _home_del_proceso(base: Path) -> bool:
    candidatos = [
        valor
        for valor in (os.environ.get("HOME"), os.environ.get("USERPROFILE"))
        if valor
    ]
    if not candidatos:
        return True
    try:
        actual = base.resolve()
    except OSError:
        actual = base
    for valor in candidatos:
        try:
            if actual == Path(valor).resolve():
                return True
        except OSError:
            if actual == Path(valor):
                return True
    return False


def _sesion_por_clave(cli: Path) -> bool:
    clave = os.environ.get("GEMINI_API_KEY") or ""
    if not clave.strip():
        return False
    try:
        datos = json.loads((cli / "settings.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    return isinstance(datos, dict) and datos.get("modelProvider") == "gemini"


def _llavero_antigravity() -> bool:
    """El ítem service=gemini account=antigravity existe. No lee el secreto."""
    if sys.platform == "darwin":
        argv = [
            "security",
            "find-generic-password",
            "-s",
            "gemini",
            "-a",
            "antigravity",
        ]
        try:
            corrida = subprocess.run(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=_TOPE_LLAVERO_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return corrida.returncode == 0
    if sys.platform == "win32":
        try:
            corrida = subprocess.run(
                ["cmdkey", "/list"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=_TOPE_LLAVERO_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        datos = corrida.stdout or b""
        aguja = "gemini:antigravity"
        return aguja.encode("utf-8") in datos or aguja.encode("utf-16-le") in datos
    try:
        corrida = subprocess.run(
            [
                "secret-tool",
                "search",
                "service",
                "gemini",
                "username",
                "antigravity",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=_TOPE_LLAVERO_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    datos = corrida.stdout or b""
    # Sin --all no va el secreto. El código 0 tampoco basta: sale 0 sin ítems.
    return b"gemini" in datos and b"antigravity" in datos


def sesion_antigravity_presente(home: Path | None = None) -> bool:
    """Hay sesión de agy. No lee ni registra secretos.

    La carpeta y su caché no autentican: `agy changelog` las crea sin
    login. Cuenta el archivo de token sin abrirlo, el par
    modelProvider=gemini con GEMINI_API_KEY no vacía, o el ítem del
    llavero cuando el home es el del proceso.
    """
    base = Path.home() if home is None else home
    cli = base / ".gemini" / "antigravity-cli"
    token = cli / "antigravity-oauth-token"
    try:
        if token.is_file() and token.stat().st_size > 0:
            return True
    except OSError:
        pass
    if _sesion_por_clave(cli):
        return True
    if _home_del_proceso(base):
        return _llavero_antigravity()
    return False


def ruta_agente_turno(home: Path | None = None) -> Path:
    base = Path.home() if home is None else home
    return base / ".gemini" / "config" / "agents" / f"{AGENTE_TURNO}.md"


def asegurar_agente_turno(home: Path | None = None) -> Path:
    """Deja el agente del turno donde agy descubre los agentes globales.

    No es la carpeta de sesión (`antigravity-cli`). El destino es uno
    solo; cada escritor usa un temporal propio porque dos reemplazos
    del mismo nombre se pisan.
    """
    destino = ruta_agente_turno(home)
    destino.parent.mkdir(parents=True, exist_ok=True)
    texto = markdown_agente_turno()
    if destino.is_file():
        try:
            if destino.read_text(encoding="utf-8") == texto:
                return destino
        except OSError:
            pass
    temporal = destino.with_name(
        f".{destino.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporal.write_text(texto, encoding="utf-8")
        os.replace(temporal, destino)
    finally:
        temporal.unlink(missing_ok=True)
    return destino


def resolver_lanzador(
    agente: str,
    generacion: Path | None = None,
    *,
    which=None,
) -> list[str] | None:
    """El argv con el que el motor lanzaría al agente.

    Sin generación, el PATH. Con generación, el lanzador de runtime.json.
    Antigravity y Muse, si esa generación no trae el cliente, siguen en
    el PATH: el paquete de flota no publica `agy`. Un lanzador declarado
    pero inválido no cae al PATH. Muse además exige su binario en el PATH
    aunque la generación lo traiga. No mira autenticación.
    """
    which = which or shutil.which
    if agente not in ADAPTADORES:
        return None
    cliente = cliente_para(agente)
    if generacion is not None and (generacion / "runtime.json").is_file():
        from motor.flota.runtime import resolver_ejecutable

        try:
            argv = resolver_ejecutable(
                generacion,
                [cliente],
                requerido=agente not in ("muse", "antigravity"),
            )
        except (OSError, ValueError, TypeError):
            return None
        if agente == "muse":
            ruta = which(agente)
            if not ruta:
                return None
            if argv[0] == cliente:
                return [ruta]
        if agente == "antigravity" and argv[0] == cliente:
            ruta = which(cliente)
            return [ruta] if ruta else None
        return argv
    ruta = which(cliente)
    return [ruta] if ruta else None


def resolver_binario(
    agente: str,
    generacion: Path | None = None,
    *,
    which=shutil.which,
) -> str | None:
    """La ruta del ejecutable que el motor lanzaría para el agente."""
    argv = resolver_lanzador(agente, generacion, which=which)
    return argv[0] if argv else None


def _disponible(agente: str, generacion: Path | None = None) -> bool:
    if agente in ("sleep", "ensayo"):
        return os.name != "nt"
    if agente not in ADAPTADORES:
        return False
    if resolver_binario(agente, generacion) is None:
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
    if agente == "antigravity":
        return sesion_antigravity_presente()
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
    if agente in ("codex", "muse", "kimi", "claude", "opencode", "qwen", "antigravity"):
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
        # Cada intento que no reanuda estrena id: repetir el de un intento
        # fallido hace que Grok lo rechace con "Session ID already in use".
        identidad = (
            previa
            if sesion_modo == "reanudada" and previa
            else str(uuid.uuid4())
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
                else ["--session-id", identidad]
            ),
            "--permission-mode",
            "bypassPermissions",
            "--always-approve",
            "--no-plan",
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
                "--disallowedTools",
                PROHIBIDO_SEGUNDO_PLANO,
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

    def antigravity(enc, prompt: Path, espacio: Path, sesion: str):
        del espacio, sesion
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
        return [
            "agy",
            "--print",
            texto,
            "--model",
            pelado or "gemini-4-argon-high",
            "--output-format",
            "stream-json",
            "--agent",
            AGENTE_TURNO,
            "--dangerously-skip-permissions",
            *(["--conversation", previa] if reanudar else []),
        ]

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
    if agente == "antigravity":
        return antigravity
    if agente == "ensayo":
        return ensayo
    return None
