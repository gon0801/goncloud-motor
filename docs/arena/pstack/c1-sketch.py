"""Candidato 1: el motor exige pstack leyendo la huella del agente, no su palabra.

Bosquejo de tipos y firmas. Los cuerpos levantan NotImplementedError; los `# TODO` son la lógica
difícil. Python 3.12, dataclasses congeladas, lógica pura aparte de los efectos.

Mapa de módulos (lo nuevo y lo que cambia):

  motor/pstack.py     NUEVO. Todo el conocimiento de "usar pstack": qué se exige (Exigencia), cómo se
                      ve en el registro de cada CLI (FORMATOS), el juicio (juzga, puerta) y el texto que
                      va en el encargo (instrucciones). Puro: sin procesos, sin disco salvo `valida`.
  motor/conf.py       Ruta gana `huella` (formato de su registro). Conf gana `pstack: PstackConf`.
  motor/effects.py    asegura_pstack (copia fija por árbol), start_attempt (placeholders {pstack}
                      {sesion}, sección del encargo, pstack.json), collect (juzga antes de "entregado").
  motor/runwrap.py    lee log.txt mientras corre y corta en la puerta; resuelve argv[0] sin los shims.
  motor/machine.py    classify reconoce la puerta. "sin_pstack" NO entra en NO_ES_FALLA.
  motor/relevo.py     DE_KIND["sin_pstack"] = Falla.TASK_FAILURE.
  motor/shims/        agente_ajeno.py + un shim por CLI de agente: dentro de un encargo no se lanza
                      otro agente por la terminal (cuota honesta, registro limpio).
  __main__.conf_valida  llama a pstack.valida.

En goncloud-motor-conf (lo que llega solo a las tres máquinas cada 15 min):

  pstack/             copia EXACTA de cursor/plugins@<sha> (harness/pstack/claude-code + 7 skills de
                      cursor-team-kit), regenerable con scripts/trae_pstack.py <sha>. No se edita a mano.
  pstack-motor/       lo propio del motor: motor.md (el contrato del motor pisa a poteto-mode) y un mapa
                      por CLI: claude.md kimi.md opencode.md grok.md muse.md qwen.md
  pstack.toml         exigencias por papel y carril, puerta, estado de cada formato.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

# =========================================================================== motor/pstack.py

PAPELES = ("implementar", "revisar")
LANES = ("fast", "gate", "release")  # los de plan.LANES


class Senal(enum.StrEnum):
    """Lo único que el registro de un CLI puede probar sobre pstack. Lo demás es palabra del agente."""

    SKILL = "skill"  # cargó una skill: herramienta de skills o lectura de su SKILL.md en la copia fija
    PLAYBOOK = "playbook"  # leyó poteto-mode/playbooks/<x>.md de la copia fija
    TODOS = "todos"  # usó su herramienta de lista de tareas
    SUBAGENTE = "subagente"  # lanzó un subagente propio (Agent, task, spawn_subagent, subagent_spawn…)
    COMANDO = "comando"  # corrió un comando real


@dataclass(frozen=True)
class Llamada:
    """Una llamada de herramienta del agente principal, ya sin el formato del CLI."""

    herramienta: str
    args: Mapping[str, str]
    orden: int  # 1, 2, 3… entre las llamadas del agente principal (no las de sus subagentes)


@dataclass(frozen=True)
class Hecho:
    senal: Senal
    nombre: str  # skill sin prefijo ("poteto-mode"), playbook ("feature"), tipo de subagente, comando
    orden: int


@dataclass(frozen=True)
class Huella:
    """Lo que el agente hizo, según su propio registro. Se deriva de log.txt; nunca se le pregunta."""

    formato: str
    sesion: str
    llamadas: int  # total de llamadas del agente principal
    hechos: tuple[Hecho, ...]
    ilegible: bool  # el log trae salida pero ninguna línea se pudo leer: falla del motor, no del agente

    def skills(self, antes_de: int | None = None) -> frozenset[str]:
        raise NotImplementedError

    def cuenta(self, senal: Senal) -> int:
        raise NotImplementedError


@dataclass(frozen=True)
class Exigencia:
    """El piso mecánico para un (papel, carril). Viene de pstack.toml; escribe el encargo Y juzga la
    huella, así lo que se pide y lo que se comprueba no pueden separarse."""

    skills: tuple[str, ...]  # siempre empieza con "poteto-mode" (lo valida parse_conf)
    playbook: bool  # leyó al menos un playbook de poteto-mode
    todos: bool
    subagentes_min: int
    comandos_min: int
    puerta: int  # poteto-mode cargado antes de la llamada N del agente principal; 0 = sin puerta


@dataclass(frozen=True)
class Falta:
    """Una cosa que faltó, en palabras llanas: va tal cual al siguiente encargo ("Qué pasó antes")."""

    texto: str


class Modo(enum.StrEnum):
    OBSERVA = "observa"  # se calcula y guarda la huella, no se rechaza (solo mientras se mide un formato)
    EXIGE = "exige"


@dataclass(frozen=True)
class PstackConf:
    raiz_conf: Path  # <conf>/pstack y <conf>/pstack-motor
    exigencias: Mapping[tuple[str, str], Exigencia]  # (papel, lane) -> exigencia; completa (valida)
    modos: Mapping[str, Modo]  # formato -> modo; un formato sin modo no puede estar en una ruta

    def exigencia(self, papel: str, lane: str) -> Exigencia:
        return self.exigencias[(papel, lane)]


# ---------------------------------------------------------------- formatos (uno por registro de CLI)


@dataclass(frozen=True)
class Formato:
    """Cómo se ve una llamada de herramienta en la salida en streaming de un CLI. Una tabla, no ifs:
    agregar un CLI = una fila aquí + un mapa en pstack-motor/<cli>.md + fixtures medidos."""

    nombre: str
    llamadas: Callable[[dict, str], tuple[Llamada, ...]]  # (línea JSON, sesión) -> llamadas principales
    skill: frozenset[str]  # herramientas que cargan una skill por nombre
    arg_skill: str
    leer: frozenset[str]  # herramientas que leen un archivo
    arg_ruta: str
    subagente: frozenset[str]
    arg_tipo: str
    todos: frozenset[str]
    comando: frozenset[str]


def _claude(linea: dict, sesion: str) -> tuple[Llamada, ...]:
    # TODO: {"type":"assistant","session_id":…,"parent_tool_use_id":null,"message":{"content":[
    #   {"type":"tool_use","name":…,"input":{…}}]}}. Solo session_id == sesion y parent nulo:
    # descarta un `claude -p` anidado (13 sesiones en A-implementar-1) y las llamadas de subagentes.
    raise NotImplementedError


def _kimi(linea: dict, sesion: str) -> tuple[Llamada, ...]:
    # TODO: {"role":"assistant","tool_calls":[{"function":{"name":…,"arguments":"<json>"}}]}.
    # Sin sesión por línea: lo anidado lo impiden los shims de agente ajeno.
    raise NotImplementedError


def _opencode(linea: dict, sesion: str) -> tuple[Llamada, ...]:
    raise NotImplementedError  # TODO medir `opencode run --format json` (no medido en hechos §4)


def _grok(linea: dict, sesion: str) -> tuple[Llamada, ...]:
    raise NotImplementedError  # TODO medir `--output-format streaming-json` (updates ACP tool_call)


def _muse(linea: dict, sesion: str) -> tuple[Llamada, ...]:
    raise NotImplementedError  # TODO medir `muse exec --json`; si no trae llamadas, session.jsonl


FORMATOS: dict[str, Formato] = {
    "claude-stream": Formato(
        "claude-stream", _claude,
        skill=frozenset({"Skill"}), arg_skill="skill",
        leer=frozenset({"Read"}), arg_ruta="file_path",
        subagente=frozenset({"Agent", "Task"}), arg_tipo="subagent_type",
        todos=frozenset({"TodoWrite"}), comando=frozenset({"Bash"}),
    ),
    "kimi-stream": Formato(
        "kimi-stream", _kimi,
        skill=frozenset({"Skill"}), arg_skill="skill",
        leer=frozenset({"Read"}), arg_ruta="path",  # TODO medir el nombre del argumento
        subagente=frozenset({"Agent", "AgentSwarm"}), arg_tipo="subagent_type",
        todos=frozenset({"SetTodoList"}), comando=frozenset({"Bash"}),  # TODO medir
    ),
    "opencode-json": Formato(
        "opencode-json", _opencode,
        skill=frozenset({"skill"}), arg_skill="name",
        leer=frozenset({"read"}), arg_ruta="filePath",
        subagente=frozenset({"task"}), arg_tipo="subagent_type",
        todos=frozenset({"todowrite"}), comando=frozenset({"bash"}),
    ),
    "grok-acp": Formato(
        "grok-acp", _grok,
        skill=frozenset(), arg_skill="",  # grok no tiene herramienta de skills: carga = read_file
        leer=frozenset({"read_file"}), arg_ruta="path",
        subagente=frozenset({"spawn_subagent"}), arg_tipo="agent",
        todos=frozenset({"todo_write"}), comando=frozenset({"run_terminal_command"}),
    ),
    "muse-jsonl": Formato(
        "muse-jsonl", _muse,
        skill=frozenset({"muse.read_skill", "read_skill"}), arg_skill="name",
        leer=frozenset({"muse.read_file", "read_file"}), arg_ruta="path",
        subagente=frozenset({"muse.subagent_spawn", "muse.workflow"}), arg_tipo="subagent_type",
        todos=frozenset({"muse.write_todos"}), comando=frozenset({"muse.bash", "bash"}),
    ),
    # qwen: sin fila hasta medir su herramienta de skills. Sin fila, su ruta no pasa `conf valida`.
}

# La copia fija se reconoce por su carpeta con el árbol, con / o \ y desde WSL (/mnt/c/…).
_RE_SKILL = r"[/\\]pstack[/\\]{arbol}[/\\]pstack[/\\]skills[/\\]([^/\\]+)[/\\]SKILL\.md$"
_RE_PLAYBOOK = r"[/\\]pstack[/\\]{arbol}[/\\]pstack[/\\]skills[/\\]poteto-mode[/\\]playbooks[/\\]([^/\\]+)\.md$"


def hecho(f: Formato, ll: Llamada, arbol: str) -> Hecho | None:
    """Una llamada -> lo que prueba. `pstack:poteto-mode` y `poteto-mode` cuentan igual (Claude con
    --plugin-dir pone el prefijo). Una lectura cuenta solo si es de la copia fija de este intento."""
    raise NotImplementedError


def huella(formato: str, lineas: Iterable[str], sesion: str, arbol: str) -> Huella:
    """log.txt -> Huella. Ignora líneas que no son JSON (avisos del CLI, salida de runwrap)."""
    # TODO: json.loads por línea; FORMATOS[formato].llamadas; hecho(); acumular sin estado global.
    raise NotImplementedError


def puerta(h: Huella, e: Exigencia) -> Falta | None:
    """¿Ya pasó la llamada N sin cargar poteto-mode? Lo usa runwrap en vivo para cortar temprano."""
    if not e.puerta or h.llamadas < e.puerta:
        return None
    if "poteto-mode" in h.skills(antes_de=e.puerta):
        return None
    return Falta(
        f"no cargó poteto-mode en sus primeras {e.puerta} acciones; se detuvo para no gastar cuota"
    )


def juzga(h: Huella, e: Exigencia) -> tuple[Falta, ...]:
    """El piso completo al terminar. Vacío = cumple. Pura y total: cada campo de Exigencia se mira."""
    # TODO: puerta(h, e); cada skill de e.skills en h.skills(); e.playbook -> cuenta(PLAYBOOK) >= 1;
    # e.todos -> cuenta(TODOS) >= 1; subagentes y comandos contra sus mínimos. Textos llanos, sin rutas.
    raise NotImplementedError


def instrucciones(e: Exigencia, raiz: Path, cli: str) -> str:
    """La sección "Cómo trabajas" del encargo, derivada de la misma Exigencia que luego juzga.

    1. Lee `<raiz>/pstack-motor/motor.md` y `<raiz>/pstack-motor/<cli>.md` (contrato y herramientas).
    2. Carga poteto-mode leyendo `<raiz>/pstack/skills/poteto-mode/SKILL.md` (o con tu herramienta de
       skills) antes de tu acción número {e.puerta}.
    3..n. Una línea por cada campo de la exigencia que aplique.
    Última: "El motor revisa tu registro de herramientas, no tu resumen."
    """
    raise NotImplementedError


def parse_conf(raw: dict, raiz_conf: Path) -> PstackConf:
    """pstack.toml -> PstackConf. Exige las 6 combinaciones papel×lane y que toda exigencia empiece
    por poteto-mode. ConfError si no (así una conf sin pstack no llega a ninguna máquina)."""
    raise NotImplementedError


def valida(raiz_conf: Path, p: PstackConf, rutas: Mapping[str, object]) -> list[str]:
    """Para `motor conf valida`:
    - pstack/ trae poteto-mode, agents/{poteto-agent,comment-sicko}.md, .claude-plugin/ y >= 40 skills;
    - cada skill nombrada en una exigencia existe en pstack/skills/;
    - cada ruta tiene `huella` en FORMATOS y en p.modos, y existe pstack-motor/<ruta.cli>.md;
    - ningún modo queda en OBSERVA para un formato de un modelo de los carriles (solo en canario).
    """
    raise NotImplementedError


# =========================================================================== motor/conf.py (cambios)


@dataclass(frozen=True)
class Ruta:
    modelo: str
    cuenta: str
    cli: str
    argv: tuple[str, ...]  # plantilla: {brief} {prompt} {result} + NUEVOS {pstack} {sesion}
    huella: str  # NUEVO: nombre en pstack.FORMATOS; obligatorio
    max_parallel: int = 1
    env: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Conf:
    sha: str
    papeles: dict[str, str]
    carriles: dict[str, tuple[str, ...]]
    rutas: dict[str, Ruta]
    reglas: str
    reglas_revisor: str
    tiempos: dict[str, float]
    avisos: dict[str, str]
    pstack: PstackConf  # NUEVO
    motor: dict[str, str] = field(default_factory=dict)


# =========================================================================== motor/effects.py (cambios)


@dataclass(frozen=True)
class PstackIntento:
    """Lo que el arranque fija y la recogida usa: `<intento>/pstack.json`. Se juzga contra la exigencia
    que escribió el encargo, aunque la conf haya cambiado mientras el agente trabajaba."""

    arbol: str  # id de la copia fija (hash de los árboles git de pstack/ y pstack-motor/)
    raiz: Path  # <home>/pstack/<arbol>
    sesion: str  # uuid5(dir del intento): estable si el arranque se repite tras una caída
    formato: str
    modo: Modo
    exigencia: Exigencia


class EffectsPstack:
    """Solo para ubicar las firmas: en el código real son métodos de `Effects`."""

    def asegura_pstack(self) -> tuple[str, Path]:
        """Materializa `<home>/pstack/<arbol>/` desde la conf ya validada (`git archive` de pstack/ y
        pstack-motor/ en una carpeta temporal y rename atómico). Si ya existe, no hace nada. Las copias
        de árboles sin intentos vivos se borran con los registros de 14 días."""
        raise NotImplementedError

    def prepara_pstack(self, d: Path, role: str, lane: str, ruta: Ruta) -> PstackIntento:
        """Escribe `<d>/pstack.json`. Lo llama start_attempt antes de armar el argv.
        argv: x.format(brief=…, prompt=…, result=…, pstack=str(raiz), sesion=sesion).
        runwrap: + ["--huella", formato, "--sesion", sesion, "--arbol", arbol, "--puerta", str(n)]
        encargo: _brief_* agrega pstack.instrucciones(exigencia, raiz, ruta.cli)."""
        raise NotImplementedError

    def juzga_pstack(self, d: Path) -> dict | None:
        """Lo llama collect justo antes de devolver "entregado" (y antes de anotar Reviewed).
        Lee pstack.json y log.txt, escribe `<d>/huella.json` (auditoría) y devuelve
        {"kind": "sin_pstack", "detalle": "; ".join(faltas)} si falta algo y el modo es EXIGE.
        Huella ilegible (el CLI cambió su formato): no se culpa al agente; la entrega sigue a Acepta y
        al revisor, huella.json lo anota y sale un aviso "no pude leer el registro de <cli>" una vez."""
        raise NotImplementedError


# =========================================================================== motor/runwrap.py (cambios)


@dataclass
class PuertaEnVivo:
    """Lee log.txt por incrementos (solo líneas completas) en el mismo bucle de 0.2 s que ya mira el
    avance. Si pstack.puerta() da Falta: mata el grupo y escribe outcome {"puerta": texto}.
    Cuesta lo que cuesta leer unos KB por segundo; no hay modelo."""

    formato: str
    sesion: str
    arbol: str
    n: int
    leido: int = 0  # bytes ya procesados
    llamadas: list[Llamada] = field(default_factory=list)

    def avanza(self, nuevo: bytes) -> Falta | None:
        raise NotImplementedError


def resuelve_sin_shims(argv0: str, path_sin_shims: str) -> str:
    """El agente se lanza con la ruta real de su CLI: con los shims de agente ajeno primero en el PATH,
    `claude` encontraría al shim y el encargo no arrancaría."""
    raise NotImplementedError


# =========================================================================== motor/machine.py (cambio)


def classify(
    exit_code: int,
    timed_out: bool,
    no_progress: bool,
    tail: str,
    proveedor: str = "",
    puerta: str = "",  # NUEVO: texto de la Falta de la puerta, si runwrap cortó por ella
) -> dict:
    """Orden: tiempo, sin avance, PUERTA -> {"kind": "sin_pstack", "detalle": puerta}, salida != 0,
    terminado. La puerta va antes que la salida: el corte de runwrap deja exit != 0."""
    raise NotImplementedError


# NO_ES_FALLA = ("entregado", "veredicto", "corte")  sin cambios: sin_pstack sí es falla del modelo.
# relevo.DE_KIND["sin_pstack"] = Falla.TASK_FAILURE  -> dos seguidas del mismo modelo = relevo.

# =========================================================================== motor/shims/agente_ajeno.py

AGENTES_AJENOS = ("claude", "kimi", "opencode", "grok", "muse", "qwen")


def agente_ajeno(nombre: str) -> int:
    """Shim de `claude`, `kimi`, … dentro de un encargo: sale 3 con "dentro de un encargo del motor no
    se lanzan otros agentes; usa tus subagentes". Los paneles de pstack se hacen con subagentes del
    propio CLI; la diversidad de modelos la pone el revisor del motor."""
    raise NotImplementedError

