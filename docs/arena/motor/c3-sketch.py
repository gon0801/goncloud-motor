"""motor: sketch c3 (estado de verdad en git; proceso local sin estado).

Mapa de módulos (cadena máxima 3 archivos: cli -> daemon -> {events, decide, state, effects}):

  motor/plan.py        parse_plan, validate_plan                 puro
  motor/events.py      Event, Snapshot, fold                      puro
  motor/decide.py      decide, merge_gate, reviewers_for, classify puro
  motor/config.py      SharedConfig, RepoConfig, MachineConfig    frontera: TOML -> tipos
  motor/state.py       GitState (diario + sync + arrendamiento)   única E/S de estado
  motor/effects/agents.py   AgentRunner (posix, windows, wsl), wip_push
  motor/effects/github.py   GitHub (PR, checks, merge --match-head-commit, status, issue)
  motor/effects/deploy.py   Deployer
  motor/effects/notify.py   Notifier (Telegram, plantillas, lint_plain)
  motor/daemon.py      tick
  motor/cli.py         submit/draft/status/note/hecho/doctor/adopt/update
  motor/install.py     install, render_service, doctor, update
  motor/skills.py      render_ia_skill (texto idéntico para OpenClaw y Hermes)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, NewType, Protocol
from pathlib import Path

PlanId = NewType("PlanId", str)
BlockId = NewType("BlockId", str)
Sha = NewType("Sha", str)          # 40 hex; el constructor valida en la frontera
IdemKey = NewType("IdemKey", str)  # hash(plan, bloque, tipo, n): hace cada efecto reintentable
Epoch = NewType("Epoch", int)      # arrendamiento del anfitrión; sube en adopt

# ---------------------------------------------------------------- config (3 capas)

@dataclass(frozen=True)
class Rung:
    """Una posición de escalera: qué CLI, qué modelo, qué banderas headless."""
    agent: str
    model: str
    flags: tuple[str, ...]

@dataclass(frozen=True)
class SharedConfig:
    """Viene de motor-config. Idéntica en todas las máquinas; `hash` la prueba."""
    roles: dict[str, str]
    ladders: dict[str, tuple[Rung, ...]]       # papel -> relevo ordenado (implementar, revisar, ...)
    routing_rules: "RoutingRules"
    review_default: str                         # "claude"
    motor_version: str                          # release.toml
    git_sha: Sha
    @property
    def hash(self) -> str: raise NotImplementedError

@dataclass(frozen=True)
class RepoConfig:
    """Viene de .motor/repo.toml del repo del plan."""
    slug: str
    ci_required: tuple[str, ...] | Literal["ninguno"]
    deploy: "DeploySpec | None"
    needs: tuple[str, ...]                      # p.ej. "ssh:goncloud"
    forbidden_paths: tuple[str, ...]

@dataclass(frozen=True)
class MachineConfig:
    """machine.toml. Esquema CERRADO: sin roles/modelos/reglas (se rechaza al cargar)."""
    name: str
    home: Path
    env: dict[str, str]                         # CA de Kaspersky, PATH
    service_kind: Literal["launchagent", "schtask", "systemd"]
    trusted_host: bool                          # False en la MacBook (FileVault)
    telegram_chat: str

def load_shared(clone: Path) -> SharedConfig: raise NotImplementedError
def load_repo(worktree: Path) -> RepoConfig: raise NotImplementedError
def load_machine(path: Path) -> MachineConfig:
    # TODO: tomllib; si aparece clave fuera de la lista blanca -> ConfigError
    raise NotImplementedError

# ---------------------------------------------------------------- eventos y fold (puro)

@dataclass(frozen=True)
class Event:
    seq: int
    ts: float
    kind: str                     # "plan.entregado", "intento.fin", "agente.cuota", "aviso.pendiente"...
    data: dict
    idem: IdemKey
    host: str
    epoch: Epoch
    schema: int = 1

@dataclass(frozen=True)
class Attempt:
    agent: str
    role: str
    started: float
    ended: float | None
    outcome: Literal["entregado", "cuota", "auth", "transitorio", "falla", "sin_avance",
                     "mintio", "sin_cambios", "abierto"]
    cooldown_until: float | None  # ABSOLUTO: sobrevive reinicios

@dataclass(frozen=True)
class BlockView:
    id: BlockId
    attempts: tuple[Attempt, ...]
    authors: frozenset[str]       # todos los agentes que escribieron en el bloque
    head: Sha | None
    verified: bool
    approved_sha: Sha | None
    ci_green_sha: Sha | None
    merged: bool
    deployed: bool
    proven: bool

@dataclass(frozen=True)
class Snapshot:
    plan: PlanId
    config_sha: Sha               # congelada en plan.entregado
    blocks: dict[BlockId, BlockView]
    pending_notices: tuple[str, ...]

def fold(events: list[Event]) -> Snapshot:
    # TODO: pura y total; ignora kinds desconocidos de schemas futuros (no revienta al actualizar)
    raise NotImplementedError

# ---------------------------------------------------------------- acciones y decisión (pura)

@dataclass(frozen=True)
class StartAttempt: block: BlockId; agent: str; role: str; idem: IdemKey
@dataclass(frozen=True)
class VerifyDelivery: block: BlockId; idem: IdemKey          # SHA en origin + Acepta en copia limpia
@dataclass(frozen=True)
class RunReview: block: BlockId; reviewer: str; sha: Sha; idem: IdemKey
@dataclass(frozen=True)
class MergeTicket: block: BlockId; sha: Sha; idem: IdemKey   # gh pr merge --match-head-commit sha
@dataclass(frozen=True)
class Deploy: block: BlockId; idem: IdemKey
@dataclass(frozen=True)
class Prove: block: BlockId; idem: IdemKey                   # prueba-real contra datos reales
@dataclass(frozen=True)
class Notice: template: str; fields: dict[str, str]; idem: IdemKey
@dataclass(frozen=True)
class RestoreWorktree: block: BlockId; from_ref: str
Action = (StartAttempt | VerifyDelivery | RunReview | MergeTicket | Deploy | Prove | Notice
          | RestoreWorktree)

def merge_gate(bv: BlockView) -> "MergeTicket | str":
    """Entrega verificada + APROBADO + CI verde, los tres sobre el MISMO sha; si no, razón."""
    raise NotImplementedError

def reviewers_for(bv: BlockView, cfg: SharedConfig, now: float, busy: dict[str, int]) -> tuple[str, ...]:
    # TODO: [cfg.review_default] + ladders["revisar"]; quitar bv.authors; quitar en cooldown (< now)
    raise NotImplementedError

def classify(exit_code: int, timed_out: bool, no_progress: bool, tail: str) -> dict:
    raise NotImplementedError

def decide(snap: Snapshot, cfg: SharedConfig, repo: RepoConfig, now: float) -> list[Action]:
    """Pura. Sin estado de bloqueo: 'sin agente' se reevalúa cada tick; 'atorado' avisa una vez."""
    raise NotImplementedError

# ---------------------------------------------------------------- estado en git

class LeaseLost(Exception): ...

class GitState:
    """Diario local (worktree de la rama plan/<id> en motor-state) + sincronización.

    Invariantes: un solo escritor (el anfitrión del lock.json con el epoch vigente);
    solo-agregar; un push no fast-forward => LeaseLost y el proceso se detiene.
    Si GitHub no responde: se sigue en local y se reintenta (ventana de pérdida = no empujado).
    """
    def __init__(self, home: Path, remote: str, plan: PlanId, machine: MachineConfig): ...
    def open_or_adopt(self, *, adopt: bool) -> Epoch: raise NotImplementedError
    def events(self) -> list[Event]: raise NotImplementedError
    def append(self, kind: str, data: dict, idem: IdemKey) -> Event:
        # TODO: si ya existe un evento con ese idem -> devolverlo (idempotente)
        raise NotImplementedError
    def sync(self) -> None:
        # TODO: un commit por tick si hay nuevos; push; ante rechazo -> raise LeaseLost
        raise NotImplementedError

# ---------------------------------------------------------------- efectos (frontera)

class AgentRunner(Protocol):
    """Backends: posix (killpg), windows (Job objects, shims .cmd), wsl (wsl -d motor -- cmd)."""
    def run(self, rung: Rung, brief: Path, cwd: Path, timeout: float, wip_ref: str) -> dict: ...

def wip_push(cwd: Path, remote: str, ref: str) -> Sha:
    """Fontanería: GIT_INDEX_FILE temporal, add -A, write-tree, commit-tree, push --force-with-lease
    a refs/motor/wip/<plan>/<bloque>. Sin hooks => nunca --no-verify. Cada 5 min y al terminar."""
    raise NotImplementedError

class GitHub:
    def __init__(self, slug: str): ...
    def ensure_pr(self, branch: str, title: str, body_footer: str) -> int: raise NotImplementedError  # busca antes de crear
    def checks_green(self, sha: Sha, required: tuple[str, ...] | str) -> bool: raise NotImplementedError
    def merge(self, pr: int, sha: Sha) -> None: raise NotImplementedError   # squash + --match-head-commit
    def set_status(self, sha: Sha, ctx: str, ok: bool, text: str) -> None: raise NotImplementedError
    def render_plan_issue(self, plan: PlanId, snap: Snapshot) -> None: raise NotImplementedError  # solo cuerpo, nunca comentarios
    def assert_default_branch_protected(self) -> None: raise NotImplementedError

class Deployer:
    def deploy(self, spec: "DeploySpec", sha: Sha) -> None: raise NotImplementedError
    def verify(self, spec: "DeploySpec", sha: Sha) -> bool: raise NotImplementedError  # sonda previa si hay 'iniciado' sin 'terminado'
@dataclass(frozen=True)
class DeploySpec: run: str; verify: str; rollback: str; idempotent: bool

class Notifier:
    def send(self, notice: Notice) -> None:
        # TODO: render plantilla -> lint_plain (<=8 líneas, sin SHA/ruta) -> Bot API; 200 => evento enviado
        raise NotImplementedError

# ---------------------------------------------------------------- reglas de enrutamiento

@dataclass(frozen=True)
class RoutingRules:
    small_requires: tuple[str, ...]   # solo-lectura, sin deploy/merge, cabe en una conversación

def is_small_task(writes_tracked: bool, writes_remote: bool, deploys: bool, needs_wait: bool,
                  rules: RoutingRules) -> bool:
    raise NotImplementedError

# ---------------------------------------------------------------- daemon y cliente

def tick(state: GitState, shared: SharedConfig, repo: RepoConfig, runner: AgentRunner,
         gh: GitHub, dep: Deployer, notifier: Notifier, now: float) -> None:
    """ingerir intentos terminados -> fold -> decide -> ejecutar (cada efecto idempotente) -> append -> sync.
    No guarda nada en memoria entre ticks: matar el proceso en cualquier punto es seguro."""
    raise NotImplementedError

def submit(plan_path: Path, repo_slug: str | None) -> PlanId:
    """valida plan, prueba repo.needs, congela config.git_sha, crea rama de estado, evento plan.entregado.
    Idempotente: mismo hash de plan => mismo PlanId."""
    raise NotImplementedError

def status(plan: PlanId | None) -> list[str]:
    """Una línea por bloque; si no hay disco local, lee la rama de GitHub."""
    raise NotImplementedError

def adopt(plan: PlanId) -> Epoch:
    """Tras perder disco: baja la rama, sube el epoch, restaura worktrees desde refs wip."""
    raise NotImplementedError

# ---------------------------------------------------------------- instalación

def render_service(kind: Literal["launchagent", "schtask", "systemd"], machine: MachineConfig) -> str:
    """plist KeepAlive / XML de tarea 'al iniciar, aunque no haya sesión' / unidad systemd."""
    raise NotImplementedError

def doctor(machine: MachineConfig, *, headless: bool) -> dict:
    """Por CLI: ¿corre y está autenticado EN EL CONTEXTO DEL SERVICIO? + config_hash + versión."""
    raise NotImplementedError

def update(shared: SharedConfig) -> None:
    """Solo sin intento vivo: uv tool install del tag fijado; doctor; si falla, vuelve al tag previo."""
    raise NotImplementedError

def render_ia_skill() -> str:
    """Texto único de la skill 'motor' para OpenClaw y Hermes (verbos + regla chico/plan)."""
    raise NotImplementedError
