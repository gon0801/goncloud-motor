"""Adaptadores de los CLI de suscripción, separados de su supervisión."""

from __future__ import annotations
from motor.io import escribir_texto, leer_json, leer_texto

import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .catalogo_agentes import (
    ADAPTADORES,
    PREFIJO_DEEPSEEK_API,
    cliente_para,
    modelo_canonico,
)

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

# Herramientas que el init de agy 1.4.12 anuncia y que esperan o agendan
# después del turno. El esquema de permisos de settings.json no puede
# nombrarlas (solo command, read_file, write_file, read_url, execute_url,
# mcp y unsandboxed) y --mode no es un filtro de herramientas. Un agente
# propio con `tools:` tampoco: excludeDefaultComponents hace fallar el
# registro y sin el no filtra. Se niegan con un hook PreToolUse del runtime.
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
CLAVE_HOOK_TURNO = "motor-turno-sin-segundo-plano"
MOTIVO_HOOK_TURNO = (
    "herramienta deshabilitada por el motor: con --print nadie despierta "
    "al agente despues del turno"
)
_CODIGO_HOOK = (
    "import json;print(json.dumps({"
    "'decision':'deny',"
    f"'reason':{MOTIVO_HOOK_TURNO!r}"
    "}))"
)


def patron_hook_turno() -> str:
    return "^(?:" + "|".join(sorted(HERRAMIENTAS_TRAS_EL_TURNO)) + ")$"


def comando_hook_turno() -> str:
    argv = [sys.executable, "-c", _CODIGO_HOOK]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def hooks_turno() -> dict:
    """El hook que niega las herramientas que esperan o agendan.

    Un agente propio con `tools:` no sirve: el registro de componentes de
    agy 1.4.12 no resuelve las herramientas de navegador ni las de MCP, y
    con `excludeDefaultComponents` el arranque falla. El hook PreToolUse si
    deja el resto de herramientas y corta solo estas.

    El comando va en linea: un guion en la carpeta que se concede con
    `--add-dir` quedaria al alcance del agente, y agy lo ejecuta en cada
    llamada de herramienta.
    """
    return {
        CLAVE_HOOK_TURNO: {
            "PreToolUse": [
                {
                    "matcher": patron_hook_turno(),
                    "hooks": [
                        {
                            "type": "command",
                            "command": comando_hook_turno(),
                        }
                    ],
                }
            ]
        }
    }


def _escribir_si_cambia(destino: Path, texto: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.is_file():
        try:
            if leer_texto(destino) == texto:
                return
        except OSError:
            pass
    temporal = destino.with_name(
        f".{destino.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        escribir_texto(temporal, texto)
        os.replace(temporal, destino)
    finally:
        temporal.unlink(missing_ok=True)


def preparar_hooks_turno(runtime: Path) -> Path:
    """Deja el hook del turno en el runtime y devuelve la carpeta que agy
    suma al workspace con --add-dir.

    No toca `~/.gemini/config`: el runtime del intento es el unico dueno de
    este archivo, que muere con el.
    """
    carpeta = runtime / "agy-hooks"
    destino = carpeta / ".agents" / "hooks.json"
    texto = json.dumps(hooks_turno(), indent=2, sort_keys=True) + "\n"
    _escribir_si_cambia(destino, texto)
    return carpeta


def retirar_agente_turno(home: Path | None = None) -> None:
    """Borra el agente que escribia la version anterior del motor.

    Solo ese nombre. Los hosts que ya corrieron la version rota lo tienen
    en el directorio global, y `agy agents` lo seguiria ofreciendo.
    """
    base = Path(home) if home is not None else _home_usuario()
    if base is None:
        return
    try:
        (base / ".gemini" / "config" / "agents" / "motor-turno.md").unlink(
            missing_ok=True
        )
    except (OSError, RuntimeError):
        pass


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
    except (OSError, RuntimeError):
        actual = base
    for valor in candidatos:
        try:
            if actual == Path(valor).resolve():
                return True
        except (OSError, RuntimeError):
            if actual == Path(valor):
                return True
    return False


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
            ["secret-tool", "lookup", "service", "gemini", "username", "antigravity"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=_TOPE_LLAVERO_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    # `lookup` sale 0 solo si el ítem existe y escribe el secreto por stdout;
    # el hijo lo manda a /dev/null. Solo se mira el código de salida, sin
    # depender del reparto de streams de libsecret ni traer el valor a Python.
    return corrida.returncode == 0


def _home_usuario() -> Path | None:
    """El home del usuario del sistema, o None si el entorno no lo deja saber.

    En Windows, sin USERPROFILE ni HOMEDRIVE/HOMEPATH, Path.home() lanza
    RuntimeError. Un reporte que no puede resolver el home no debe tronar.
    """
    try:
        return Path.home()
    except (OSError, RuntimeError):
        return None


def sesion_antigravity_presente(home: Path | None = None) -> bool:
    """Hay sesión de agy. No lee ni registra secretos.

    La carpeta y su caché no autentican: `agy changelog` las crea sin
    login. Cuenta el archivo de token sin abrirlo, o el ítem del
    llavero cuando el home es el del proceso. `GEMINI_API_KEY` habla
    con la API de Gemini y no es la suscripción.

    Con `home` explícito, ese es el único home que se mira: un home de
    prueba no consulta el token ni el llavero reales. Sin `home`, se usa
    el del usuario si se puede resolver; si no, no hay sesión.
    """
    base = Path(home) if home is not None else _home_usuario()
    if base is None:
        return False
    token = base / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    try:
        if token.is_file() and token.stat().st_size > 0:
            return True
    except OSError:
        pass
    if _home_del_proceso(base):
        return _llavero_antigravity()
    return False


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


def _sesion_presente(agente: str) -> bool:
    """Hay sesión de suscripción en esta máquina, sin mirar el ejecutable.

    Las mismas comprobaciones de _disponible, sin resolver_binario: la sesión
    es de la cuenta y no depende de la generación de flota.
    """
    if agente not in ADAPTADORES:
        return False
    if agente == "codex":
        path = (
            Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            / "auth.json"
        )
        try:
            datos = leer_json(path)
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
            datos = tomllib.loads(leer_texto((origen / "config.toml")))
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


def _disponible(agente: str, generacion: Path | None = None) -> bool:
    if agente in ("sleep", "ensayo"):
        return os.name != "nt"
    if agente not in ADAPTADORES:
        return False
    if resolver_binario(agente, generacion) is None:
        return False
    return _sesion_presente(agente)


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
        texto = (
            "Lee el pedido y las instrucciones en "
            + str(prompt)
            + ". Ejecuta el trabajo autorizado."
        )
        if referencia.startswith(PREFIJO_DEEPSEEK_API):
            # El CLI nuevo ya no admite --pure ni --variant. Si la flota liga
            # un CLI anterior, vincular cambia --standalone por --pure.
            return [
                "opencode",
                "run",
                "--standalone",
                "--auto",
                "--format",
                "json",
                "--model",
                referencia,
                *(
                    ["--session", previa]
                    if sesion_modo == "reanudada" and previa
                    else []
                ),
                texto,
            ]
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
            texto,
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
