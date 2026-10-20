"""motor — mapa de módulos y firmas (candidato C2). Solo tipos; cuerpos NotImplementedError.

Paquete `motor` (stdlib, Python 3.11+), instalado con `uv tool install git+…goncloud-motor@estable`.

    motor/
      __main__.py        CLI: instala | doctor | servicio | valida | plantilla | entrega | estado |
                              historia | hecho | nota | actualiza | daemon
      config/            UNA fuente de verdad, versionada con el código:
        modelos.json       tabla v6 (carriles × 4 posiciones)           ← Hermes model-routing.source.json
        papeles.toml       papel → carril, herramientas, brief            ← Hermes roles/*.md + taller briefs
        reglas.md          texto que entra a todo brief (nunca --no-verify, nunca push a principal, …)
        skills/motor/SKILL.md   la misma skill para claw y Hermi
      nucleo/            máquina de estados pura + log                   ← taller
        plan.py  store.py  machine.py  avisos_texto.py  daemon.py
      relevo/            tabla, salud, política de relevo, adaptadores    ← Hermes subscriptions/
        tipos.py  catalogo.py  politica.py  redaccion.py  archivos_privados.py  adaptadores/{claude,codex,grok,kimi,muse,opencode,…}.py
      proceso/           runwrap multiplataforma                          ← taller runwrap + Hermes process
        runwrap.py  windows.py  wsl.py
      github/            push, PR, checks, compuertas, merge              ← Hermes entrega_pr + openclaw merge.py
        publicar.py  compuertas.py
      efectos.py         shell delgado: una acción → un efecto idempotente ← taller effects
      despliegue.py      receta de .motor.toml + prueba-real
      avisos.py          Telegram directo + buzón + espejo opcional
      host.py            ~/.motor/host.toml, secretos.env, .motor.toml del repo
      servicio.py        launchd / schtasks / autoactualización

Un solo escritor del log (el daemon). CLI → inbox/. Agentes → git + result.json. Nada más escribe.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

# =============================================================================
# host.py — frontera de configuración (parse aquí; dentro, tipos)
# =============================================================================

Marca = Literal["mac", "mini", "pc"]


@dataclass(frozen=True)
class Host:
    """~/.motor/host.toml: lo ÚNICO distinto por máquina. Nunca contiene reglas ni modelos."""
    nombre: Marca                               # encabezado de los avisos ("Hermes:" / "OpenClaw:" / "Mac:")
    home: Path                                  # ~/.motor
    rutas_cli: dict[str, Path]                  # {"claude": "/Users/dn/.local/bin/claude", ...} (doctor las llena)
    env_extra: tuple[tuple[str, str], ...]      # p. ej. NODE_EXTRA_CA_CERTS/SSL_CERT_FILE (Kaspersky en la PC)
    por_wsl: frozenset[str]                     # adaptadores que corren con `wsl.exe --` (muse en la PC)
    espejo: tuple[str, ...]                     # comando opcional que despierta a la sesión de la marca
    telegram_chat: str


@dataclass(frozen=True)
class RepoConfig:
    """`.motor.toml` en la raíz del repo: lo específico de cada proyecto. Nada de esto vive en el motor."""
    principal: str
    checks: tuple[str, ...]                     # nombres de checks que deben estar verdes sobre el SHA exacto
    merge: Literal["squash", "merge", "rebase"]
    draft_primero: bool
    bots_del_sha: tuple[str, ...]               # logins de bots cuyo comentario sobre el SHA final se exige
    despliegue: "Receta | None"
    entorno_pruebas: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Receta:
    desplegar: str
    checklist: str
    rollback: str


def leer_host(home: Path) -> Host:
    raise NotImplementedError


def leer_secretos(home: Path) -> dict[str, str]:
    """secretos.env (modo 600): CLAUDE_CODE_OAUTH_TOKEN, GH_TOKEN, TELEGRAM_BOT_TOKEN, META_API_KEY…
    Se inyectan al env de cada agente y de gh; jamás al log, al brief ni a un aviso (redaccion)."""
    raise NotImplementedError


def leer_repo_config(worktree: Path) -> RepoConfig:
    """Lee `.motor.toml` del SHA entregado. Falta → el plan se rechaza en `valida` (no a media corrida)."""
    raise NotImplementedError


# =============================================================================
# nucleo/plan.py — el plan en Markdown es el contrato (reemplaza SUA)
# =============================================================================

LANES = ("fast", "gate", "release")
HUMAN_KINDS = ("login", "2fa", "captcha", "pago")


@dataclass(frozen=True)
class Fila:
    id: str
    contenido: str
    acepta: str                                 # comando que debe salir 0 en copia limpia del SHA


@dataclass(frozen=True)
class Humano:
    kind: str                                   # uno de HUMAN_KINDS
    que_hacer: str
    sonda: str = ""


@dataclass(frozen=True)
class Bloque:
    id: str
    titulo: str
    lane: str
    depende: tuple[str, ...]
    filas: tuple[Fila, ...]
    aviso: str
    prueba_real: str
    release: bool                               # la receta viene de .motor.toml, no del plan
    humano: Humano | None


@dataclass(frozen=True)
class Plan:
    id: str
    titulo: str
    repo: str                                   # remoto git (nuevo respecto a taller)
    bloques: tuple[Bloque, ...]


class PlanError(ValueError):
    errores: list[str]


def parse_plan(texto: str, *, implementadores_disponibles: int) -> Plan:
    """Valida TODO en la frontera: autopiloto, repo, lanes, Acepta ≥1 por bloque de código,
    prueba-real, humano ∈ HUMAN_KINDS, sin ciclos, ≥2 implementadores en el carril. Lanza PlanError con todos."""
    raise NotImplementedError


def plantilla() -> str:
    """Lo que claw/Hermi rellenan. Incluye comentarios con la regla pequeño-vs-plan."""
    raise NotImplementedError


# =============================================================================
# nucleo/store.py — log append-only, único escritor
# =============================================================================

@dataclass(frozen=True)
class Event:
    id: int
    ts: float
    plan: str
    bloque: str
    kind: str                                   # AttemptStarted|AttemptEnded|Verified|Reviewed|PrAbierto|Checks|Merged|
                                                # Deployed|Proved|HumanDone|Note|Cooldown|Salud|NoticeQueued|PlanLoaded
    data: dict


class Store:
    def __init__(self, path: Path) -> None:
        """SQLite WAL + triggers append-only + lock de archivo: un segundo daemon sale con error claro."""
        raise NotImplementedError

    def append(self, plan: str, bloque: str, kind: str, data: dict, *, ukey: str | None = None) -> bool:
        """Idempotente por ukey: el mismo hecho dos veces queda una sola vez."""
        raise NotImplementedError

    def events(self, plan: str | None = None) -> list[Event]:
        raise NotImplementedError


# =============================================================================
# relevo/tipos.py — del router de Hermes, sin cambios de forma
# =============================================================================

class Falla(enum.StrEnum):
    AUTH = "auth"
    CUOTA = "quota"                 # ventana gastada: bench 15 min o hasta la hora declarada
    THROTTLE = "rate_limit"         # segundos; reintento en la misma ruta con backoff (20, 60), tope 120
    MODELO_NO_DISPONIBLE = "model_unavailable"
    TRANSPORTE = "transport"
    PETICION_INVALIDA = "invalid_request"
    TERMINAL_INVALIDO = "invalid_terminal"   # el proveedor contestó pero no en el contrato (result.json/veredicto)
    FALLA_TAREA = "task_failure"
    SIN_AVANCE = "no_progress"      # de taller: ni log ni árbol cambian en N min
    TIEMPO = "timeout"
    PERDIDO = "lost"                # pid desapareció sin outcome.json (apagón)
    MINTIO = "lied"                 # dijo SHA que no es la punta de su rama
    SIN_CAMBIOS = "no_changes"      # volvió a entregar lo mismo que ya falló


AVANZAN: frozenset[Falla]                       # Hermes _ADVANCING_FAILURES + SIN_AVANCE, TIEMPO, PERDIDO, MINTIO, SIN_CAMBIOS
BLOQUEAN_TRAS_MUTACION: frozenset[Falla]        # Hermes _BLOCK_AFTER_MUTATION


@dataclass(frozen=True)
class Ruta:
    """Una posición compilada de la tabla v6 (Hermes RouteSpec)."""
    posicion: int                               # 0..3
    etiqueta: str                               # "anthropic/claude-sonnet-5-5 (high)"
    adaptador: str                              # "claude" | "codex" | "kimi" | "opencode_zai" | "grok" | "muse" | …
    modelo: str
    esfuerzo: Literal["high", "xhigh", "max", "medium", "low"] | None


@dataclass(frozen=True)
class Encargo:
    """Lo que un intento debe hacer. Sustituye InvocationRequest; sin pre_invoke (autorización poteto retirada)."""
    plan: str
    bloque: str
    papel: Literal["implementar", "revisar"]
    n: int
    worktree: Path
    rama: str                                   # motor/<plan>/<bloque>; "" para revisar (copia detached)
    sha_base: str                               # SHA del que parte (revisar: el SHA revisado)
    brief: str                                  # texto completo; viaja por archivo privado, nunca por argv
    feedback: tuple[str, ...]


@dataclass(frozen=True)
class Lanzamiento:
    """Lo que `runwrap` ejecuta. Es lo que antes hacía `adapter.invoke` de forma sincrónica."""
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    prompt_por: Literal["archivo", "stdin"]
    herramientas: tuple[str, ...]               # p. ej. --allowedTools de Claude; solo lectura para revisar


@dataclass(frozen=True)
class Evidencia:
    """Salida redactada y acotada de un proceso (Hermes ProcessEvidence + taller outcome.json)."""
    exit: int
    stdout: str
    stderr: str
    stream: str                                 # stdout entero redactado, solo para que el adaptador lo parsee
    timed_out: bool
    sin_avance: bool
    segundos: float
    wip_error: str


@dataclass(frozen=True)
class Resultado:
    """Hermes InvocationResult, normalizado y redactado en __post_init__."""
    ok: bool
    texto: str
    adaptador: str
    modelo: str
    falla: Falla | None = None
    retry_after_s: float | None = None
    reset_at: str | None = None                 # ISO si el proveedor declaró cuándo vuelve la cuota
    session_id: str = ""
    detalle: str = field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class Salud:
    """Hermes AdapterHealth: lista/no lista por cuenta, sin inferencia."""
    listo: bool
    adaptador: str
    falla: Falla | None = None
    detalle: str = ""


# =============================================================================
# relevo/catalogo.py — compila config/modelos.json
# =============================================================================

def carril_de(papel: str) -> str:
    """papeles.toml: implementar→codigo, revisar→planear(=revisar), …"""
    raise NotImplementedError


def compilar_carril(carril: str, fuente: dict) -> tuple[Ruta, Ruta, Ruta, Ruta]:
    """Exactamente 4 posiciones; familia del modelo → adaptador (Hermes _FAMILY_ADAPTERS)."""
    raise NotImplementedError


def cargar_tabla() -> dict:
    """config/modelos.json del paquete instalado. Sin override por env: una fuente."""
    raise NotImplementedError


# =============================================================================
# relevo/politica.py — EL corte: el bucle de router._run_owned hecho función pura
# =============================================================================

@dataclass(frozen=True)
class IntentoVisto:
    """Lo que la política necesita saber de cada intento anterior del bloque (viene de fold)."""
    posicion: int
    adaptador: str
    papel: str
    terminado: bool
    ok: bool
    falla: Falla | None
    muto: bool                                  # tree_fingerprint al inicio != al final (Hermes)
    retries_throttle: int


@dataclass(frozen=True)
class EstadoCuentas:
    """Vista de salud por adaptador, plegada del log (reemplaza subscription_health + cooldowns)."""
    cooldown_hasta: dict[str, float]            # adaptador → epoch
    cuarentena_desde: dict[str, float]          # adaptador → epoch (horizonte 1 h, Hermes _QUARANTINE_RETRY_SECONDS)
    ocupados: dict[str, int]                    # adaptador → intentos vivos (max_parallel por adaptador)


@dataclass(frozen=True)
class Elegida:
    ruta: Ruta
    modo: Literal["start", "continue"]          # continue si un intento anterior mutó el árbol (Hermes)


@dataclass(frozen=True)
class Espera:
    hasta: float                                # todos en cooldown/ocupados; el reloj lo resuelve, no una pausa


@dataclass(frozen=True)
class Agotado:
    razon: str                                  # 4 posiciones agotadas o todas autoras → aviso "atorado" único


def siguiente(carril: tuple[Ruta, ...], intentos: tuple[IntentoVisto, ...], cuentas: EstadoCuentas,
              excluir: frozenset[str], ahora: float) -> Elegida | Espera | Agotado:
    """Puro. TODO (pseudocódigo, portado de router._run_owned):
      ultimo = intentos[-1] si hay
      si ultimo.terminado y (ultimo.ok o falla ∉ AVANZAN o (falla ∈ BLOQUEAN_TRAS_MUTACION y ultimo.muto)):
          -> Agotado("el último intento no permite avanzar") ; el nivel de arriba decide ARREGLAR con feedback
      candidatas = rutas con posicion > ultimo.posicion (o >= si ultimo interrumpido/PERDIDO)
      candidatas = [r for r in candidatas if r.adaptador ∉ excluir]            # revisores_para
      para r en candidatas:
          si cooldown_hasta[r.adaptador] > ahora y cuarentena no activa: seguir
          si cuarentena activa (ahora - desde <= 3600): seguir
          si ocupados[r.adaptador] >= max_parallel: seguir
          si ultimo.falla == THROTTLE y ultimo.posicion == r.posicion y retries < 2: -> Elegida(r, modo)  # misma ruta
          -> Elegida(r, "continue" si any(i.muto for i in intentos) else "start")
      si todas en cooldown: -> Espera(min(cooldown_hasta))
      -> Agotado(...)
    """
    raise NotImplementedError


def bench_para(resultado: Resultado, ahora: float) -> float | None:
    """Hermes _bench_for: CUOTA → 15 min o reset_at; THROTTLE → 2 min o retry_after acotado; otras → None."""
    raise NotImplementedError


def revisores_para(carril_revisar: tuple[Ruta, ...], autores: frozenset[str]) -> tuple[Ruta, ...]:
    """Orden de David: Claude primero; si no puede, cualquier posición cuyo adaptador NO implementó el bloque."""
    raise NotImplementedError


# =============================================================================
# relevo/adaptadores — contrato por proveedor (Hermes), ahora en dos mitades
# =============================================================================

class Adaptador(Protocol):
    nombre: str

    def sondear(self, ruta: Ruta, host: Host) -> Salud:
        """Sin inferencia: `claude auth status`, archivo de auth, etc. Lo usa `doctor` y la política."""
        ...

    def comando(self, encargo: Encargo, ruta: Ruta, host: Host) -> Lanzamiento:
        """Banderas headless verificadas (claude --print --permission-mode dontAsk --allowedTools …;
        muse exec --yolo; opencode run --auto -m …; codex exec --dangerously-bypass…; grok --always-approve…).
        Para `revisar`, herramientas de solo lectura. En la PC, si nombre ∈ host.por_wsl → wsl.py envuelve."""
        ...

    def leer(self, evidencia: Evidencia) -> Resultado:
        """Parser del stream del proveedor (claude stream-json subtype; opencode 2.x salida a medias;
        retry-after; "resets at"). Clasifica Falla. Nunca toma el texto del modelo como verdad del trabajo."""
        ...


def registro(host: Host) -> dict[str, Adaptador]:
    raise NotImplementedError


# =============================================================================
# proceso/runwrap.py — un agente, un proceso aparte del daemon, en 3 SO
# =============================================================================

@dataclass(frozen=True)
class OpcionesRunwrap:
    dir_intento: Path                           # brief.md, log.txt, result.json, outcome.json, agent.pid
    cwd: Path
    timeout_s: float
    sin_avance_s: float
    wip_rama: str                               # "" = sin wip-push (revisar)
    wip_cada_s: float                           # 300: commit `wip:` + push a la rama del bloque; nunca a principal
    lanzamiento: Lanzamiento


def correr(op: OpcionesRunwrap) -> int:
    """python -m motor.proceso.runwrap … ; vive aunque el daemon muera.
    TODO:
      prompt → archivo privado 600 (Hermes archivos_privados) ; stdin=/dev/null
      POSIX: start_new_session + killpg TERM→KILL (taller). Windows: Job object kill-on-close +
             _resolve_windows_shim para .cmd de npm (Hermes windows.py). WSL: guardián PDEATHSIG (Hermes wsl.py)
      bucle: cada 0.2 s poll; mtime(log, árbol) para sin_avance; cada wip_cada_s → _wip_push
      al salir por cualquier razón: _wip_push final; outcome.json por rename atómico (redactado y acotado)
    """
    raise NotImplementedError


def vivo(pid: int) -> bool:
    raise NotImplementedError


# =============================================================================
# nucleo/machine.py — fold / estado / decide / MergeTicket (taller), con salud en el mismo log
# =============================================================================

class Estado(enum.Enum):
    ESPERANDO = "esperando dependencias"
    HUMANO = "espera un paso de David"
    IMPLEMENTANDO = "implementando"
    REVISANDO = "revisando"
    VERIFICAR = "por verificar"
    REVISAR = "por revisar"
    ARREGLAR = "por arreglar"
    PR = "por abrir o actualizar el PR"
    CHECKS = "esperando checks del SHA"
    MERGE = "por mergear"
    DESPLEGAR = "por desplegar"
    PROBAR = "por probar con datos reales"
    SIN_AGENTE = "sin agente disponible"
    HECHO = "hecho"
    # No existe FALLADO ni PAUSADO. Un fallo produce otra acción.


@dataclass
class VistaBloque:
    bloque: Bloque
    intentos: list[IntentoVisto]
    verificados: dict[str, dict]                # sha → {ok, detalle}
    revisiones: list[dict]                      # {sha, veredicto, bloqueantes[{texto, repro, arreglo}], notas, adaptador}
    pr: dict | None                             # {numero, url, draft}
    checks: dict[str, dict]                     # sha → {verdes: bool, detalle}
    mergeados: list[str]
    desplegados: dict[str, dict]
    probados: dict[str, dict]
    humano_hecho: bool
    notas: list[str]
    avisos: set[str]

    def head(self) -> str: ...
    def autores(self) -> frozenset[str]: ...
    def corriendo(self) -> IntentoVisto | None: ...


@dataclass
class Snapshot:
    plan: Plan
    bloques: dict[str, VistaBloque]
    cuentas: EstadoCuentas
    avisos_plan: set[str]


def fold(plan: Plan, eventos: list[Event]) -> Snapshot:
    raise NotImplementedError


def estado(snap: Snapshot, bid: str) -> Estado:
    """Deriva; no se guarda. Regla 4: un hallazgo sin `repro` no bloquea."""
    raise NotImplementedError


@dataclass(frozen=True)
class MergeTicket:
    """Solo `github.compuertas` lo construye. Sin ticket, `efectos.merge` no compila la llamada."""
    plan: str
    bloque: str
    sha: str
    pr: int


@dataclass(frozen=True)
class StartAttempt:
    bloque: str; papel: str; elegida: Elegida; n: int; sha: str; feedback: tuple[str, ...]

@dataclass(frozen=True)
class Verify:      bloque: str; sha: str
@dataclass(frozen=True)
class AbrirPR:     bloque: str; sha: str
@dataclass(frozen=True)
class MarcarReady: bloque: str; sha: str; pr: int
@dataclass(frozen=True)
class LeerChecks:  bloque: str; sha: str; pr: int
@dataclass(frozen=True)
class Merge:       ticket: MergeTicket
@dataclass(frozen=True)
class Deploy:      bloque: str; sha: str
@dataclass(frozen=True)
class Prove:       bloque: str; sha: str
@dataclass(frozen=True)
class ProbeHuman:  bloque: str
@dataclass(frozen=True)
class Notify:      key: str; bloque: str; plantilla: str; campos: tuple[tuple[str, str], ...]

Accion = StartAttempt | Verify | AbrirPR | MarcarReady | LeerChecks | Merge | Deploy | Prove | ProbeHuman | Notify


def decide(snap: Snapshot, tabla: dict, ahora: float) -> list[Accion]:
    """Puro. Por bloque según `estado`: ARREGLAR → siguiente(carril codigo, …) ; REVISAR →
    siguiente(revisores_para(carril revisar, autores), …) ; Espera → nada (o Notify "lento" si > 6 h) ;
    Agotado → Notify "atorado" una vez, el resto del plan sigue. MERGE solo si hay MergeTicket."""
    raise NotImplementedError


def clasificar(ev: Evidencia, res: Resultado | None, sha_en_origin: str, sha_previo: str) -> Falla | None:
    """Combina la clasificación del adaptador con las de taller: MINTIO, SIN_CAMBIOS, PERDIDO, SIN_AVANCE, TIEMPO."""
    raise NotImplementedError


# =============================================================================
# github/ — genérico para cualquier repo; lee RepoConfig, nunca constantes de Orbit
# =============================================================================

@dataclass(frozen=True)
class VistaPR:
    """Congelada en una llamada (gh pr view + check-runs + hilos): la entrada de las compuertas."""
    numero: int
    draft: bool
    head_sha: str
    estado: str
    checks: dict[str, Literal["verde", "rojo", "pendiente", "ausente"]]   # por nombre, del SHA exacto
    hilos_sin_resolver: int
    hallazgos_bloqueantes_sin_adjudicar: int                               # bot ai-review: High/Critical o seguridad/datos
    bots_que_comentaron_el_sha: frozenset[str]
    coderabbit_rate_limited: bool                                          # pasa con aviso (David 2026-09-25)


def publicar_rama(worktree: Path, rama: str, secretos: dict[str, str]) -> tuple[bool, str]:
    """Hermes entrega_pr._subir: push con GIT_TERMINAL_PROMPT=0; motivo corto y redactado si falla."""
    raise NotImplementedError


def abrir_o_reusar_pr(repo: str, base: str, rama: str, titulo: str, cuerpo: str, draft: bool,
                      secretos: dict[str, str]) -> dict:
    """Hermes entrega_pr: idempotente (si ya hay PR abierto de la rama, su URL). owner/repo desde la URL del remoto."""
    raise NotImplementedError


def marcar_ready(repo: str, pr: int, secretos: dict[str, str]) -> None:
    """`gh pr ready`; en repos como Orbit esto dispara `completa`."""
    raise NotImplementedError


def leer_vista(repo: str, pr: int, secretos: dict[str, str]) -> VistaPR:
    raise NotImplementedError


def compuertas(vista: VistaPR, cfg: RepoConfig, sha_esperado: str, aprobado: bool) -> MergeTicket | list[str]:
    """Puro (openclaw merge.py portado). Todas se evalúan; la lista de razones es el diagnóstico completo:
    head == sha_esperado ; not draft ; APROBADO sobre ese sha ; cfg.checks todos verdes para ese sha ;
    hilos resueltos ; sin bloqueante del bot sin adjudicar ; bots_del_sha ⊆ comentaron (salvo rate limited → aviso)."""
    raise NotImplementedError


def mergear(repo: str, t: MergeTicket, metodo: str, secretos: dict[str, str]) -> str:
    """`gh pr merge --<metodo> --match-head-commit <t.sha>`; relee MERGED; devuelve SHA de la principal."""
    raise NotImplementedError


# =============================================================================
# despliegue.py / avisos.py / efectos.py / servicio.py / daemon
# =============================================================================

def desplegar(worktree_principal: Path, receta: Receta, env: dict[str, str]) -> tuple[bool, str]:
    """desplegar → checklist ; si algo sale ≠0 → rollback. Idempotente por marca `Deployed(sha)` en el log.
    El motor no sabe de ssh: la receta del repo sí."""
    raise NotImplementedError


def probar_real(cmd: str, env: dict[str, str]) -> tuple[bool, str]:
    raise NotImplementedError


PLANTILLAS_AVISO: dict[str, str]                # entregado | humano | lento | terminado | atorado | intrusion


def lint_plain(texto: str) -> str:
    """≤8 líneas; sin hex ≥7; sin rutas; sin jerga. Lanza AvisoInvalido. Prueba sobre TODAS las plantillas."""
    raise NotImplementedError


def encolar_aviso(home: Path, marca: Marca, plantilla: str, campos: dict[str, str]) -> None:
    """Renderiza "<Marca>: …", pasa lint_plain, escribe avisos/pendientes/<ts>-<key>.txt."""
    raise NotImplementedError


def enviar_pendientes(home: Path, chat: str, token: str, espejo: tuple[str, ...]) -> None:
    """Telegram Bot API directo; reclamo por rename (patrón de avisos_sesion); si falla, reintenta al siguiente tick.
    Después, `espejo` (opcional) despierta a la sesión de la marca; su fallo no deshace el aviso."""
    raise NotImplementedError


class Efectos:
    """Shell delgado (taller). Cada método: ¿ya existe el efecto? → anota y sale. Ningún modelo aquí."""

    def __init__(self, host: Host, store: Store, adaptadores: dict[str, Adaptador]) -> None:
        raise NotImplementedError

    def start_attempt(self, plan: Plan, a: StartAttempt) -> None:
        """worktree/rama (reset --hard origin/<rama> si existe: sigue del wip-push); brief con reglas.md +
        papeles.toml + feedback; adaptador.comando → runwrap detached; AttemptStarted(ukey)."""
        raise NotImplementedError

    def collect(self, plan: Plan, bloque: str, att: IntentoVisto) -> None:
        """outcome.json → adaptador.leer → clasificar; CUOTA/THROTTLE → Cooldown(bench_para); revisar → Reviewed;
        implementar → sha_en_origin → AttemptEnded(entregado|falla)."""
        raise NotImplementedError

    def verify(self, plan: Plan, a: Verify) -> None: raise NotImplementedError
    def abrir_pr(self, plan: Plan, a: AbrirPR) -> None: raise NotImplementedError
    def marcar_ready(self, plan: Plan, a: MarcarReady) -> None: raise NotImplementedError
    def leer_checks(self, plan: Plan, a: LeerChecks) -> None: raise NotImplementedError
    def merge(self, plan: Plan, a: Merge) -> None: raise NotImplementedError      # exige MergeTicket por tipo
    def deploy(self, plan: Plan, a: Deploy) -> None: raise NotImplementedError
    def prove(self, plan: Plan, a: Prove) -> None: raise NotImplementedError
    def probe_human(self, plan: Plan, a: ProbeHuman) -> None: raise NotImplementedError
    def notify(self, plan: Plan, a: Notify) -> None: raise NotImplementedError


class Daemon:
    """tick = ingerir(inbox + intentos terminados) → fold → decide → ejecutar → enviar avisos → ¿actualizar?"""

    def __init__(self, home: Path) -> None: raise NotImplementedError
    def tick(self, ahora: float | None = None) -> bool: raise NotImplementedError

    def quizas_actualizar(self) -> bool:
        """Cada 30 min: `git ls-remote` de la etiqueta `estable`; si cambió y ningún efecto síncrono corre,
        `uv tool upgrade goncloud-motor` y `sys.exit(0)` → KeepAlive relanza. Los runwrap siguen vivos."""
        raise NotImplementedError


def instalar_servicio(host: Host) -> str:
    """mac/mini: ~/Library/LaunchAgents/com.goncloud.motor.plist (gui, KeepAlive, RunAtLoad) + caffeinate mientras
    haya intentos; mini además `pmset autorestart 1`. pc: schtasks ONSTART /RU usuario /RP (pide la contraseña una
    vez) "ejecutar aunque el usuario no haya iniciado sesión"; env_extra de host.toml en el comando."""
    raise NotImplementedError


def doctor(host: Host, adaptadores: dict[str, Adaptador]) -> dict:
    """Por CLI: ruta, banderas headless presentes, sondear() → listo/falla; servicio instalado/corriendo;
    secretos presentes; `.motor.toml` legible si se pasa un repo. Salida --json para claw/Hermi."""
    raise NotImplementedError
