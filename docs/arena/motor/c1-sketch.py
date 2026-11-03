"""motor: boceto de tipos y firmas de las piezas NUEVAS (candidato 1, "taller crecido").

Es un boceto de interfaz (estilo .pyi): los cuerpos son `...` y la lógica delicada va como
"Pseudocódigo" en el docstring. No es código para correr.

Mapa de módulos (paquete `motor`, repo gon0801/goncloud-motor = goncloud-taller renombrado):

    motor/
      store.py            REUSO   log SQLite append-only, único escritor = daemon (+ lector RO)
      plan.py             REUSO+  formato de taller + `repo:` por plan y por bloque
      machine.py          REUSO+  fold/decide/estado/merge_gate puros; Route/AccountId; estados PR..CHECKS
      notify.py           REUSO   plantillas + lint_plain (+ prefijo de máquina)
      conf.py             NUEVO   repo de conf compartido -> Conf (roles, cadenas, rutas, reglas, versión)
      repo_settings.py    NUEVO   `.motor.toml` leído de la rama BASE -> RepoSettings
      workspace.py        NUEVO   espejo por repo, worktrees, fotos wip (sin tocar el índice del agente)
      github.py           NUEVO   GitHubPort + GhGitHub (gh CLI) + compuertas puras portadas de merge.py
      fake_github.py      PROTO   FakeGitHub sobre bare repo + prs.json + CI falso
      deploy.py           NUEVO   receta genérica con DeployStarted y checklist-primero
      notifier.py         NUEVO   transporte Telegram del bot "Motor" (solo sendMessage)
      plataforma/
        __init__.py       NUEVO   Plataforma (protocolo), ProcHandle, boot_id, detectar()
        posix.py          PORTE   killpg/start_new_session de taller
        windows.py        PORTE   Job objects, resolve_shim, breakaway, de Hermes process.py
        wsl.py            PORTE   guardián die-with-parent de Hermes process.py
        servicio.py       NUEVO   render de plist launchd / XML de Task Scheduler
      runwrap.py          PORTE   usa Plataforma; fotos wip periódicas
      daemon.py           REUSO+  tick = conf.refresh -> ingerir -> fold -> decide -> ejecutar
      servicio_loop.py    NUEVO   supervisor `motor servicio` + autoactualización (exit 75)
      client.py           NUEVO   lo que llama el CLI: entregar/estado/hecho/nota/recibo
      cli.py              REUSO+  argparse; todo con --json

Cadena de llamada más larga: cli -> client -> inbox (archivo). daemon -> machine (puro) -> efecto.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NewType, Protocol

# =========================================================================== identidades
# Se parsean en la frontera (CLI, conf, GitHub); adentro se confía en el tipo.

MachineName = Literal["mac", "mini", "pc"]
PlanId = NewType("PlanId", str)
BlockId = NewType("BlockId", str)
CmdId = NewType("CmdId", str)  # ULID que genera el cliente; ukey del recibo
ModelId = NewType(
    "ModelId", str
)  # "anthropic/claude-opus-5-5": identidad de AUTOR y de revisor
AccountId = NewType(
    "AccountId", str
)  # "anthropic": la cuota y el cooldown son de la cuenta
BootId = NewType("BootId", str)  # cambia en cada arranque del SO


@dataclass(frozen=True)
class Sha:
    """40 hex. Solo `Sha.parse` lo construye: un SHA corto o inventado no entra al sistema."""

    hex: str

    @staticmethod
    def parse(s: str) -> Sha:
        """Pseudocódigo: re.fullmatch(r"[0-9a-f]{40}", s) o ValueError."""
        ...


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @staticmethod
    def parse(s: str) -> RepoRef:
        """Acepta 'owner/name', https o ssh (incluye alias tipo git@github-claw:).
        Base: la regex `_GITHUB` de entrega_pr ampliada a alias de ssh."""
        ...

    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


# =========================================================================== conf compartida (conf.py)

Rol = Literal["implementar", "revisar"]


@dataclass(frozen=True)
class Tiempos:
    timeout_s: float = 4 * 3600
    sin_avance_s: float = 45 * 60
    wip_cada_s: float = 300
    cooldown_cuota_s: float = 5 * 3600  # si el error no trae hora
    cooldown_rate_limit_s: float = 120  # de Hermes router: un throttle no es una cuota
    aviso_lento_s: float = 6 * 3600
    checks_sondeo_s: float = 60


@dataclass(frozen=True)
class Route:
    """Una posición de una cadena: qué modelo, por qué CLI, contra qué cuenta.

    Reemplaza a taller.Provider. `argv` es la plantilla headless ({brief} {prompt} {result}
    {model}); `lanzador` = "wsl" envuelve el argv con wsl.exe en la PC.
    """

    model: ModelId
    account: AccountId
    cli: str  # nombre lógico: claude | kimi | opencode | grok | muse | codex | cursor
    argv: tuple[str, ...]
    effort: str | None
    lanzador: Literal["directo", "wsl"] = "directo"
    max_parallel: int = 1  # por cuenta y por máquina


@dataclass(frozen=True)
class Conf:
    """Todo lo que debe ser idéntico en las tres máquinas. Fuente: repo goncloud-motor-conf."""

    sha: Sha  # commit de conf; se anota en cada AttemptStarted
    motor_version: str  # versión fijada; el daemon se actualiza a ella
    cadenas: dict[str, tuple[Route, ...]]  # carril v6 -> 4 posiciones
    rol_a_carril: dict[Rol, str]  # implementar -> "codigo", revisar -> "planear"
    reglas_encargo: str  # preámbulo de todo encargo (no --no-verify, etc.)
    reglas_revisor: str  # reglas y trampas del lead, destiladas
    aviso_chat: str  # chat de David (no es secreto)
    tiempos: Tiempos


@dataclass(frozen=True)
class Maquina:
    """Solo hechos de esta máquina. NUNCA roles, modelos ni reglas."""

    nombre: MachineName
    home: Path  # ~/.motor
    path_extra: tuple[str, ...]  # p. ej. ~/.openclaw/tools/node/bin en la Mini
    env_extra: dict[str, str]  # p. ej. NODE_EXTRA_CA_CERTS de Kaspersky en la PC
    wsl_distro: str = ""
    secretos: Path = Path()  # secretos.env (600 / ACL de dueño)


class ConfRepo:
    """Clon local de goncloud-motor-conf. Solo lectura para el motor."""

    def __init__(self, home: Path, url: str): ...

    def refresh(self, ahora: float) -> Conf:
        """fetch como máximo cada 15 min; devuelve la Conf de origin/main ya validada.

        Regla: si la conf nueva no valida, sigue con la anterior y encola UN aviso
        (clave 'conf-invalida:<sha>'). Nunca deja al daemon sin conf.
        """
        ...

    @staticmethod
    def valida(raiz: Path) -> list[str]:
        """`motor conf valida`: el Acepta de todo plan que cambie la conf.

        Reglas: cada carril con ≥2 cuentas distintas; cada cli con plantilla headless;
        rol_a_carril total; versión con tag existente.
        """
        ...


# =========================================================================== .motor.toml (repo_settings.py)

MergeMethod = Literal["squash", "merge", "rebase"]


@dataclass(frozen=True)
class BotGate:
    """Compuerta opcional de un bot revisor (portada de merge.py: autor + marcador por SHA)."""

    autores: tuple[
        str, ...
    ]  # logins reservados, p. ej. github-actions / github-actions[bot]
    marcador: str  # texto que cita el SHA revisado
    bloquea_si: str  # regex de veredicto bloqueante


@dataclass(frozen=True)
class DeployRecipe:
    desplegar: str
    checklist: str
    rollback: str
    ya_desplegado: str = ""  # sonda opcional; si falta, se usa checklist
    requiere: tuple[
        str, ...
    ] = ()  # "ssh:goncloud", "cli:psql"... `motor valida` lo comprueba aquí


@dataclass(frozen=True)
class RepoSettings:
    repo: RepoRef
    base: str
    pinned_at: Sha  # SHA de la base del que se leyó: la rama del agente no lo cambia
    merge_method: MergeMethod
    checks_extra: tuple[
        str, ...
    ]  # se SUMAN a los requeridos por la protección de la base
    bot: BotGate | None
    deploy: DeployRecipe | None
    wip_publico: bool  # repo público: ¿fotos wip cada N min o solo al final?


# =========================================================================== GitHub (github.py)


class PrState(enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    MERGED = "merged"
    CLOSED = "closed"


@dataclass(frozen=True)
class PrView:
    number: int
    head_ref: str
    head: Sha
    state: PrState
    mergeable: Literal["ok", "conflicto", "desconocido"]
    merge_commit: Sha | None


@dataclass(frozen=True)
class ChecksVerdict:
    sha: Sha
    pendientes: tuple[str, ...]
    fallas: tuple[tuple[str, str], ...]  # (nombre, extracto del log fallido)
    verdes: tuple[str, ...]

    @property
    def terminal(self) -> bool:
        return not self.pendientes

    @property
    def green(self) -> bool:
        return self.terminal and not self.fallas


@dataclass(frozen=True)
class MergeTicket:
    """Igual que en taller, más PR y checks. Solo machine.merge_gate lo construye."""

    plan: PlanId
    block: BlockId
    repo: RepoRef
    pr: int
    sha: Sha


class GitHubPort(Protocol):
    """Todo lo que el motor necesita de GitHub. Sin JSON de GitHub en la superficie.

    Toda operación es idempotente: se puede repetir tras un corte sin duplicar nada.
    """

    def file_at(self, repo: RepoRef, ref: str, path: str) -> str | None: ...

    def ensure_draft_pr(
        self, repo: RepoRef, base: str, head_ref: str, titulo: str, cuerpo: str
    ) -> PrView:
        """Reusa el PR abierto de esa rama si existe (de entrega_pr). Repo público: cuerpo neutro."""
        ...

    def view(self, repo: RepoRef, number: int) -> PrView: ...

    def mark_ready(self, repo: RepoRef, number: int, sha: Sha) -> None:
        """`gh pr ready` solo si la cabeza sigue siendo `sha`; si no, no hace nada."""
        ...

    def required_checks(self, repo: RepoRef, base: str) -> tuple[str, ...]:
        """Reglas (rulesets) ∪ protección clásica de la base."""
        ...

    def checks(
        self, repo: RepoRef, sha: Sha, requeridos: tuple[str, ...], bot: BotGate | None
    ) -> ChecksVerdict:
        """check-runs + statuses del SHA EXACTO, deduplicados por nombre (último intento gana)."""
        ...

    def merge(self, repo: RepoRef, ticket: MergeTicket, method: MergeMethod) -> PrView:
        """`gh pr merge N --<method> --match-head-commit <sha>` y relee. Si ya estaba MERGED con
        ese head, devuelve la vista sin volver a mergear."""
        ...


class GhGitHub:
    """Implementación con el CLI `gh` y GH_TOKEN del entorno del servicio (nunca a disco ni log)."""

    def __init__(self, gh_bin: str, env: dict[str, str]): ...


def lee_repo_settings(gh: GitHubPort, repo: RepoRef) -> RepoSettings:
    """Lee `.motor.toml` de la punta de la base (vía GitHubPort.file_at). Sin archivo -> defaults."""
    ...


def compuerta_bot(
    comentarios: list[tuple[str, str]], sha: Sha, gate: BotGate
) -> str | None:
    """Pura (de merge.py.parsear_bot): None si el bot aprobó ESE sha; si no, por qué no.
    Exige autor reservado: el texto del marcador lo puede escribir cualquiera."""
    ...


# =========================================================================== workspace (workspace.py)


@dataclass(frozen=True)
class WipRef:
    """Rama de fotos: motor-wip/<maquina>/<plan>/<bloque>. Nunca la rama del agente."""

    nombre: str


class Workspace:
    """Git local por repo: ~/.motor/repos/<owner>__<name> (espejo) y ~/.motor/wt/<plan>/<bloque>."""

    def __init__(self, home: Path, maquina: MachineName): ...

    def rama(self, plan: PlanId, block: BlockId) -> str:
        """motor/<maquina>/<plan>/<bloque>: dos máquinas con el mismo plan nunca chocan."""
        ...

    def wip(self, plan: PlanId, block: BlockId) -> WipRef: ...

    def prepara(self, repo: RepoRef, base: str, plan: PlanId, block: BlockId) -> Path:
        """Worktree en la punta de la rama remota (o de la base). Si la foto wip es hija de esa
        punta, deja sus archivos SIN commitear; el encargo lo dice.

        Pseudocódigo:
          fetch rama y wip
          reset --hard origin/<rama> (o crea desde origin/<base>)
          si wip existe y merge-base(wip, punta) == punta y tree(wip) != tree(punta):
              git checkout <wip> -- .   ; borrar archivos que wip no tiene
        Windows: core.longpaths=true en el espejo.
        """
        ...

    def sha_en_origin(self, repo: RepoRef, sha: str, rama: str) -> Sha | None:
        """De taller: SHA completo si existe y ES la punta de la rama; si no, None (mintió)."""
        ...

    def copia_limpia(self, repo: RepoRef, sha: Sha, donde: Path) -> Path: ...


FotoResultado = Literal["empujada", "igual", "secreto", "error"]


def foto_wip(worktree: Path, wip: WipRef, hay_secreto: object) -> FotoResultado:
    """La llama runwrap cada `wip_cada_s` y al final. No toca el índice ni la rama del agente.

    Pseudocódigo:
      GIT_INDEX_FILE=<tmp> git read-tree HEAD; git add -A   (respeta .gitignore)
      tree = git write-tree ; si tree == último empujado -> "igual"
      diff = git diff-tree -p HEAD <tree> ; si hay_secreto(diff) -> "secreto" (no se empuja)
      c = git commit-tree <tree> -p HEAD -m "wip"
      git push -f origin <c>:refs/heads/<wip>
    """
    ...


# =========================================================================== plataforma


@dataclass(frozen=True)
class ProcHandle:
    """Vivo/muerto sin creerle al pid: tras reiniciar, los pids se reciclan."""

    pid: int
    boot_id: BootId
    inicio: float  # hora de creación del proceso según el SO


class Contenido(Protocol):
    def poll(self) -> int | None: ...

    def mata_arbol(self) -> None:
        """POSIX: TERM y luego KILL al grupo (EPERM de macOS tolerado). Windows: cerrar el Job."""
        ...


class Plataforma(Protocol):
    def boot_id(self) -> BootId:
        """macOS: sysctl kern.bootsessionuuid; Linux: /proc/sys/kernel/random/boot_id;
        Windows: hora de arranque (ahora - GetTickCount64, redondeada al minuto)."""
        ...

    def lanza_desacoplado(
        self, argv: list[str], cwd: Path, env: dict[str, str], log: Path
    ) -> ProcHandle:
        """Lanza runwrap fuera del daemon. POSIX: start_new_session. Windows: DETACHED_PROCESS |
        CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB (si se niega el
        breakaway, reintenta sin él y lo anota: el agente morirá con el daemon, y se acepta)."""
        ...

    def vivo(self, h: ProcHandle) -> bool:
        """False si boot_id cambió o si el pid existe pero con otra hora de inicio."""
        ...

    def contiene_y_corre(
        self, argv: list[str], cwd: Path, env: dict[str, str], log: Path
    ) -> Contenido:
        """Dentro de runwrap: el AGENTE en su grupo (POSIX) o en su Job kill-on-close (Windows),
        stdin nulo, sin ventana. resolve_shim para .cmd de npm. Lanzador wsl -> guardián."""
        ...


def detectar() -> Plataforma:
    """os.name == "nt" -> WindowsPlataforma; si no, PosixPlataforma."""
    ...


def resolve_shim(argv: list[str], which: object, systemroot: str) -> list[str]:
    """Pura (de process._resolve_windows_shim): .cmd/.bat -> [cmd.exe, /d, /c, ruta, ...]."""
    ...


def envuelve_wsl(
    argv: list[str], distro: str, cwd_windows: Path, pasa_env: tuple[str, ...]
) -> tuple[list[str], dict[str, str]]:
    """wsl.exe -d <distro> --cd <wslpath> -- python3 -c <GUARDIAN> --parent <argv>;
    devuelve también WSLENV con las variables a cruzar (p. ej. META_API_KEY/u)."""
    ...


# =========================================================================== resultados de intento

ResultadoKind = Literal[
    "entregado",
    "veredicto",  # éxitos
    "cuota",
    "rate_limit",
    "auth",
    "transitorio",  # de la cuenta: no cuentan contra el modelo
    "corte",  # la máquina se reinició: no cuenta contra nadie
    "tiempo",
    "sin_avance",
    "falla",
    "sin_resultado",  # cuentan contra el modelo en este bloque
    "mintio",
    "sin_cambios",
    "veredicto_invalido",
]


def classify(
    exit_code: int, timed_out: bool, no_progress: bool, tail: str, cli: str
) -> dict:
    """De taller + adaptadores de Hermes: el parseo de la salida depende del CLI (stream-json de
    claude, json de grok...). Separa rate_limit (2 min) de cuota (hora del error o 5 h)."""
    ...


# =========================================================================== núcleo: lo que crece en machine.py


class Estado(enum.Enum):
    ESPERANDO = "esperando dependencias"
    HUMANO = "espera un paso de David"
    IMPLEMENTANDO = "implementando"
    REVISANDO = "revisando"
    ARREGLAR = "por arreglar"
    VERIFICAR = "por verificar"
    PR = "por abrir PR draft"  # NUEVO
    REVISAR = "por revisar"
    LISTO_PR = "por marcar listo"  # NUEVO: tras APROBADO, gh pr ready
    CHECKS = "esperando checks"  # NUEVO: sondeo barato; no es una pausa del plan
    MERGE = "por mergear"
    DESPLEGAR = "por desplegar"
    PROBAR = "por probar con datos reales"
    SIN_AGENTE = "sin agente disponible"
    HECHO = "hecho"


@dataclass(frozen=True)
class OpenPr:
    block: BlockId
    sha: Sha


@dataclass(frozen=True)
class MarkReady:
    block: BlockId
    pr: int
    sha: Sha


@dataclass(frozen=True)
class PollChecks:
    """El efecto decide si toca consultar (≤1 vez cada checks_sondeo_s); solo anota lo terminal."""

    block: BlockId
    sha: Sha


def revisores_para(
    autores: frozenset[ModelId],
    conf: Conf,
    cooldowns: dict[AccountId, float],
    ahora: float,
) -> tuple[Route, ...]:
    """Orden de David: Claude (primera posición del carril de revisar); si no está disponible,
    cualquier modelo que NO trabajó en el bloque.

    Pseudocódigo:
      orden = cadena(rol_a_carril["revisar"]) + resto de rutas de todas las cadenas (sin repetir modelo)
      return [r for r in orden if r.model not in autores and cooldowns.get(r.account, 0) <= ahora]
    """
    ...


# =========================================================================== cliente (client.py)

EstadoOrden = Literal["tomado", "encolado", "rechazado"]


@dataclass(frozen=True)
class Recibo:
    cmd_id: CmdId
    estado: EstadoOrden
    plan: PlanId | None
    maquina: MachineName
    errores: tuple[str, ...] = ()  # solo si rechazado: lo que `valida` encontró


@dataclass(frozen=True)
class LineaBloque:
    plan: PlanId
    bloque: BlockId
    estado: Estado
    frase: str  # llana, para que la IA se la repita a David
    desde: float


class Cliente:
    """Lo único que usan el lead, claw e Hermi. Nunca escribe el log: deja órdenes en inbox/
    (rename atómico) y lee el log en modo solo lectura."""

    def __init__(self, home: Path, maquina: Maquina): ...

    def entregar(
        self, plan_md: Path, *, en: MachineName | None = None, espera_s: float = 10
    ) -> Recibo:
        """Valida aquí (plan + .motor.toml de la base + `requiere` de la receta en ESTA máquina);
        si `en` es otra máquina, pasa el plan por ssh a `motor entrega -` de allá.

        Pseudocódigo:
          errores = valida(plan_md) ; si hay -> Recibo(rechazado)
          cmd = nuevo_ulid(); escribe inbox/.tmp -> rename inbox/<cmd>.json
          espera hasta espera_s a ver CommandReceived{cmd} en el log -> tomado ; si no, encolado
        """
        ...

    def estado(
        self, plan: PlanId | None = None, *, todas: bool = False
    ) -> list[LineaBloque]: ...

    def hecho(self, plan: PlanId, bloque: BlockId) -> Recibo: ...

    def nota(self, plan: PlanId, bloque: BlockId, texto: str) -> Recibo: ...

    def recibo(self, cmd_id: CmdId) -> Recibo: ...


# =========================================================================== avisos (notifier.py)


class Transporte(Protocol):
    def envia(self, chat: str, texto: str) -> bool: ...


class TelegramBot:
    """Bot "Motor", solo sendMessage. El mismo token en las tres máquinas; nadie hace getUpdates."""

    def __init__(self, token: str, base_url: str = "https://api.telegram.org"): ...


def prefijo(maquina: MachineName, titulo_plan: str) -> str:
    """'En la Mini · Ingesta 02:' — sin rutas ni SHAs; pasa por lint_plain con el resto."""
    ...


# =========================================================================== servicio (servicio_loop.py, plataforma/servicio.py)


def servicio(home: Path) -> int:
    """`motor servicio`: lo que arranca launchd / Task Scheduler. Nunca muere por un error del daemon.

    Pseudocódigo:
      bucle:
        p = lanza `motor daemon --home home`
        code = p.wait()
        si code == 75: continuar          # se autoactualizó: relanzar YA con el binario nuevo
        dormir(min(60, backoff))          # caída: backoff corto, sin límite de reintentos
    """
    ...


def autoactualiza(
    conf: Conf, version_actual: str, uv_bin: str
) -> Literal["igual", "instalada", "fallo"]:
    """Si conf.motor_version != actual: `uv tool install --force git+ssh://…@v<ver>`; 'instalada'
    hace que el daemon salga con 75 al final del tick. Los runwrap en curso siguen con el código
    viejo y escriben outcome.json con `schema` versionado."""
    ...


def render_launchd(maquina: Maquina, usuario: str, motor_bin: Path) -> str:
    """/Library/LaunchDaemons/com.goncloud.motor.plist: UserName, HOME, PATH, RunAtLoad, KeepAlive."""
    ...


def render_tarea_windows(maquina: Maquina, usuario: str, motor_bin: Path) -> str:
    """XML de Task Scheduler: BootTrigger, LogonType=Password, ExecutionTimeLimit=PT0S,
    RestartOnFailure (1 min, 999), MultipleInstancesPolicy=IgnoreNew, sin 'solo con AC'."""
    ...
