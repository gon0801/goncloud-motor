"""El motor pide y comprueba pstack. Todo lo de "usar pstack" que no toca disco ni procesos.

- `huella(fuente, formato)`: qué hizo el agente, sacado de SU registro (nunca de su resultado).
- `juzga(huella, exigencia)`: `Cumple` o `Falta` con lo que faltó en lenguaje llano. Lo único que
  se juzga es mecánico y visible en cualquier registro: cuántos subagentes lanzó.
- `instrucciones(exigencia, cli)`: la sección "Cómo trabajas" del encargo; sale de la misma
  exigencia que luego juzga, así lo que se pide y lo que se comprueba no se separan.
- `FORMATOS`: una fila por CLI con el aspecto, en su registro, de lanzar un subagente y correr un
  comando. La fuente del registro de cada CLI: docs/arena/pstack/hechos.md §4.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

SKILL_BASE = "poteto-mode"
CLAVES_CELDA = ("skills", "skill", "playbook", "todos", "min_subagentes", "puerta")
CLAVES_PSTACK = ("commit", "exigir")


class ConfError(ValueError):
    """Una conf que no se puede usar. Vive aquí para que `pstack` y `conf` lo compartan sin ciclo."""


LANES = ("fast", "gate", "release")
PAPELES = ("implementar", "revisar")


# --------------------------------------------------------------------------- tipos


@dataclass(frozen=True)
class Exigencia:
    """El piso mecánico de un (carril, papel). Viene de `pstack.toml`."""

    skills: tuple[str, ...] = (SKILL_BASE,)
    playbook: bool = False
    min_subagentes: int = 0

    def to_dict(self) -> dict:
        return {
            "skills": list(self.skills),
            "playbook": self.playbook,
            "min_subagentes": self.min_subagentes,
        }

    @staticmethod
    def from_dict(d: dict) -> Exigencia:
        """Acepta un `pstack.json` viejo (con `puerta` o `todos`) sin tronar: se ignoran."""
        return Exigencia(
            skills=tuple(d.get("skills", (SKILL_BASE,))),
            playbook=bool(d.get("playbook", False)),
            min_subagentes=int(d.get("min_subagentes", 0)),
        )


@dataclass(frozen=True)
class Accion:
    """Una llamada de herramienta del agente principal, ya sin el formato de su CLI."""

    tipo: str  # subagente | comando | otra
    nombre: str = ""  # tipo de subagente o comando


@dataclass(frozen=True)
class Huella:
    subagentes: tuple[str, ...] = ()
    comandos: tuple[str, ...] = ()
    acciones: int = 0
    legible: bool = True  # False: el registro no se pudo leer ("no verificable")

    def to_dict(self) -> dict:
        return {
            "subagentes": list(self.subagentes),
            "comandos": list(self.comandos)[:200],
            "acciones": self.acciones,
            "legible": self.legible,
        }


@dataclass(frozen=True)
class Cumple:
    verificable: bool = (
        True  # False: no se pudo leer el registro; la entrega pasa con aviso
    )


@dataclass(frozen=True)
class Falta:
    faltas: tuple[str, ...]  # frases llanas, van tal cual al siguiente encargo

    def texto(self) -> str:
        return "; ".join(self.faltas)


# --------------------------------------------------------------------------- formatos


def _args(x) -> dict:
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
        except ValueError:
            return {}
        return v if isinstance(v, dict) else {}
    return {}


Llamadas = tuple[
    list[tuple[str, dict]], bool
]  # (herramienta, args) en orden; ¿era un evento del CLI?


def _bloques(o: dict) -> Llamadas:
    """Claude y Qwen: {"type":"assistant","message":{"content":[{"type":"tool_use","name":..,"input":..}]}}.
    Las llamadas de un subagente traen `parent_tool_use_id`: no son del agente principal."""
    visto = o.get("type") in ("assistant", "user", "system") or "message" in o
    if o.get("parent_tool_use_id") or o.get("type") != "assistant":
        return [], visto
    contenido = (o.get("message") or {}).get("content") or []
    if not isinstance(contenido, list):
        return [], visto
    return [
        _desenvuelve(b.get("name", ""), _args(b.get("input")))
        for b in contenido
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ], visto


def _desenvuelve(nombre: str, args: dict) -> tuple[str, dict]:
    """Qwen llama a sus herramientas diferidas con `tool_call {name, arguments}`: la acción es la de adentro."""
    if nombre == "tool_call" and args.get("name"):
        return str(args["name"]), _args(args.get("arguments"))
    return nombre, args


def _tool_calls(o: dict) -> Llamadas:
    """Kimi: {"role":"assistant","tool_calls":[{"function":{"name":..,"arguments":"<json>"}}]}."""
    visto = "role" in o
    if o.get("role") != "assistant":
        return [], visto
    out = []
    for c in o.get("tool_calls") or []:
        f = c.get("function", c) if isinstance(c, dict) else {}
        out.append((f.get("name", ""), _args(f.get("arguments"))))
    return out, visto


def _estado(o: dict) -> Llamadas:
    """opencode (`--format json`): la parte de herramienta lleva `tool` y `state.input`."""
    p = o.get("part") if isinstance(o.get("part"), dict) else o
    visto = "type" in o or "part" in o
    estado = p.get("state")
    nombre = p.get("tool") or p.get("name")
    if not (nombre and isinstance(estado, dict)):
        return [], visto
    return [(str(nombre), _args(estado.get("input")))], visto


def _tool_name(o: dict) -> Llamadas:
    """Grok (`updates.jsonl` / `streaming-json`): una línea por herramienta con `tool_name`."""
    u = (
        (o.get("params") or {}).get("update")
        if isinstance(o.get("params"), dict)
        else None
    )
    visto = u is not None or any(
        k in o
        for k in ("tool_name", "tool_call", "update", "sessionUpdate", "toolCall")
    )  # el objeto final de `--output-format json` (hechos.md §4) no trae nada de esto
    if isinstance(
        u, dict
    ):  # updates.jsonl: solo `tool_call` abre una acción; sus `tool_call_update` la repiten
        if u.get("sessionUpdate") != "tool_call" or not u.get("title"):
            return [], visto
        return [(str(u["title"]), _args(u.get("rawInput")))], visto
    nombre = o.get("tool_name") or (o.get("tool_call") or {}).get("name")
    if not nombre:
        return [], visto
    args = o.get("args") or o.get("input") or o.get("arguments") or o.get("raw_input")
    return [(str(nombre), _args(args))], visto


def _muse(o: dict) -> Llamadas:
    """Muse (`session.jsonl`): `assistant_tool_calls_committed` con la lista de llamadas {name, args}."""
    if (
        "children" in o
    ):  # session.jsonl envuelve algunas transacciones en un marco con `record_json`
        out, visto = [], "retained_frame" in o
        for c in o["children"]:
            try:
                hijo = json.loads(c.get("record_json", ""))
            except (AttributeError, ValueError):
                continue
            if isinstance(hijo, dict):
                llamadas_hijo, v = _muse(hijo)
                out += llamadas_hijo
                visto = visto or v
        return out, visto
    visto = "type" in o or "event" in o or "payload_type" in o
    if o.get("payload_type") == "tool.result" and isinstance(o.get("payload"), dict):
        # `muse exec --json`: solo el resultado de cada herramienta, con su nombre
        p = o["payload"]
        nombre = str((p.get("correlation_facts") or {}).get("tool_name", ""))
        return ([(nombre, {})] if nombre else []), visto
    evento = (
        (o.get("payload") or {}).get("event")
        if isinstance(o.get("payload"), dict)
        else None
    )
    if isinstance(evento, dict):
        o = evento
    llamadas = o.get("calls") or o.get("tool_calls") or o.get("toolCalls")
    if llamadas is None and o.get("name"):
        llamadas = [o]
    out = []
    for c in llamadas or []:
        if isinstance(c, dict):
            f = c.get("function", c)
            out.append(
                (str(f.get("name", "")), _args(f.get("args", f.get("arguments"))))
            )
    return out, visto


@dataclass(frozen=True)
class Formato:
    """Cómo se ve cada acción en el registro de un CLI. Una tabla, no ifs: agregar un CLI es una
    fila aquí, un mapa en pstack-motor/<cli>.md y un registro de ejemplo en tests/test_pstack.py."""

    cli: str
    fuente: str  # dónde está su registro (hechos.md §4)
    extrae: Callable[[dict], Llamadas]
    subagente: tuple[str, ...]
    comando: tuple[str, ...]
    arg_id: tuple[
        str, ...
    ] = ()  # el CLI repite la misma llamada con otro estado: se cuenta una vez


FORMATOS: dict[str, Formato] = {
    "claude": Formato(
        "claude",
        "transcript ~/.claude/projects/<cwd>/<sesion>.jsonl (con --session-id) o log.txt en stream-json",
        _bloques,
        subagente=("agent", "task"),
        comando=("bash",),
    ),
    "kimi": Formato(
        "kimi",
        "log.txt con --output-format stream-json (copia en ~/.kimi-code/sessions/<wd>/<sesion>/agents/main/wire.jsonl)",
        _tool_calls,
        subagente=("agent", "agentswarm"),
        comando=("bash", "shell"),
    ),
    "opencode": Formato(
        "opencode",
        "log.txt con `opencode run --format json`",
        _estado,
        subagente=("task", "subagent"),
        comando=("bash",),
    ),
    "grok": Formato(
        "grok",
        "~/.grok/sessions/<cwd>/<sessionId>/updates.jsonl",
        _tool_name,
        subagente=("spawn_subagent",),
        comando=("run_terminal_command",),
    ),
    "muse": Formato(
        "muse",
        "~/.local/share/muse/sessions/AAAA/MM/DD/<id>/session.jsonl (o `muse exec --json` en log.txt)",
        _muse,
        subagente=("subagent_spawn", "workflow"),
        comando=("bash", "run_command", "shell"),
    ),
    "qwen": Formato(
        "qwen",
        "log.txt con --output-format stream-json, con la salida a un archivo (por una tubería corta a 64 KB); subagentes por `tool_call{name:agent}`",
        _bloques,
        subagente=("task", "agent"),
        comando=("run_shell_command", "bash"),
    ),
}


@dataclass(frozen=True)
class NoMedido:
    """El CLI se lanza, pero su registro no tiene lector.

    No invalida la conf. `huella` lo deja no verificable, y eso no es falla del modelo.
    """

    cli: str


def lector(cli: str) -> Formato | NoMedido:
    """El lector del registro de `cli`, o `NoMedido` si no hay uno."""
    encontrado = FORMATOS.get(cli)
    if encontrado is None:
        return NoMedido(cli)
    return encontrado


def _clasifica(f: Formato, herramienta: str, args: dict) -> list[Accion]:
    h = herramienta.lower().rsplit(".", 1)[-1]
    if h in f.subagente:
        tipo = next(
            (
                str(args[k])
                for k in ("subagent_type", "agent", "agent_type", "role", "description")
                if args.get(k)
            ),
            "subagente",
        )
        # AgentSwarm: un subagente por elemento de `items`; sin lista no lanza a nadie
        if h == "agentswarm":
            items = args.get("items")
            cuantos = len(items) if isinstance(items, list) else 0
        else:
            cuantos = 1
        return [Accion("subagente", tipo) for _ in range(cuantos)]
    if h in f.comando:
        cmd = next((str(args[k]) for k in ("command", "cmd") if args.get(k)), "")
        return [Accion("comando", cmd)]
    return [Accion("otra")]


def _objetos(fuente: str | Iterable[str]) -> Iterable[dict]:
    """Objetos JSON del registro: uno por línea, con sangría en varias líneas (grok `--output-format json`),
    o un arreglo completo (qwen `--output-format json`)."""
    texto = fuente if isinstance(fuente, str) else "\n".join(fuente)
    inicio = re.search(r"(?m)^\[", texto)
    if inicio:  # el arreglo puede traer avisos del CLI (stderr) antes o después
        try:
            arreglo, _ = json.JSONDecoder().raw_decode(texto[inicio.start() :])
            for o in arreglo:
                if isinstance(o, dict):
                    yield o
            return
        except (ValueError, TypeError):
            pass
    lineas = texto.splitlines(keepends=True)
    i, dec = 0, json.JSONDecoder()
    while i < len(lineas):
        linea = lineas[i].strip()
        i += 1
        if linea[:1] != "{":
            continue  # avisos del CLI, salida de runwrap
        try:
            o = json.loads(linea)
        except ValueError:
            # un objeto con sangría ocupa varias líneas (`grok --output-format json`)
            resto = "".join(lineas[i - 1 :]).lstrip()
            try:
                o, fin = dec.raw_decode(resto)
            except ValueError:
                continue
            i += resto[:fin].count("\n")
        if isinstance(o, dict):
            yield o


def _llamadas(
    fuente: str | Iterable[str], formato: str
) -> tuple[list[tuple[str, dict]], bool]:
    """Las (herramienta, args) de cada llamada del agente principal, en orden, y si el registro se
    pudo leer. Una llamada que el CLI repite con otro estado (mismo id) va una vez. La usan
    `eventos` y el recorte del registro, para contar igual."""
    f = FORMATOS[formato]
    pares: list[tuple[str, dict]] = []
    legible = False
    vistos: set[str] = set()
    for o in _objetos(fuente):
        llamadas, visto = f.extrae(o)
        legible = legible or visto
        ident = str(o.get("tool_call_id") or o.get("callID") or "")
        if ident and llamadas:
            if ident in vistos:
                continue
            vistos.add(ident)
        pares += llamadas
    return pares, legible


def eventos(fuente: str | Iterable[str], formato: str) -> tuple[list[Accion], bool]:
    """(acciones del agente principal en orden, ¿el registro se pudo leer?). Lo usa `huella`."""
    pares, legible = _llamadas(fuente, formato)
    f = FORMATOS[formato]
    acciones: list[Accion] = []
    for herramienta, args in pares:
        acciones.extend(_clasifica(f, herramienta, args))
    return acciones, legible


def huella(fuente: str | Iterable[str] | None, formato: str) -> Huella:
    """Lo que el agente hizo según su registro. Sin registro, con uno que no se entiende, o con un
    formato `NoMedido` (sin lector): `legible=False`. Eso no es falla del modelo."""
    if not fuente or isinstance(lector(formato), NoMedido):
        return Huella(legible=False)
    acciones, legible = eventos(fuente, formato)
    if not legible:
        return Huella(legible=False)
    return Huella(
        subagentes=tuple(a.nombre for a in acciones if a.tipo == "subagente"),
        comandos=tuple(a.nombre for a in acciones if a.tipo == "comando"),
        acciones=len(acciones),
    )


# --------------------------------------------------------------------------- juicio


def juzga(h: Huella, e: Exigencia) -> Cumple | Falta:
    if not h.legible:
        return Cumple(verificable=False)
    if len(h.subagentes) < e.min_subagentes:
        return Falta(
            (
                f"lanzó {len(h.subagentes)} subagente(s) y se pedían al menos {e.min_subagentes}",
            )
        )
    return Cumple()


def instrucciones(e: Exigencia, cli: str) -> str:
    """La sección "Cómo trabajas" del encargo; sale de la misma exigencia que luego juzga.
    No pide cargar ni abrir nada: las guías van completas en el anexo del encargo."""
    pasos = [
        "Las guías que pide este intento van completas en «## Anexo: tus guías», al final del "
        "encargo. Las rutas que una guía menciona son relativas a la carpeta de esa guía.",
    ]
    if e.min_subagentes:
        linea = (
            f"Lanza al menos {e.min_subagentes} subagente(s) con la herramienta de subagentes de "
            "tu CLI."
        )
        if cli == "kimi":
            linea += (
                " Cada elemento de `items` de `AgentSwarm` cuenta como un subagente."
            )
        pasos.append(linea)
    pasos.append(
        "El motor juzga lo que haces, no lo que lees: lo que solo digas no cuenta."
    )
    cuerpo = "\n".join(f"{i}. {p}" for i, p in enumerate(pasos, 1))
    return f"## Cómo trabajas\nEste encargo se hace con pstack.\n{cuerpo}\n"


# --------------------------------------------------------------------------- conf (pstack.toml)


@dataclass(frozen=True)
class PstackConf:
    commit: str = ""
    exigir: bool = False
    exigencias: dict[tuple[str, str], Exigencia] = field(
        default_factory=dict
    )  # (lane, papel)

    def exigencia(self, lane: str, papel: str) -> Exigencia:
        return self.exigencias.get((lane, papel), Exigencia())


def _celda(raw: dict, nombre: str = "") -> Exigencia:
    for k in raw:
        if k not in CLAVES_CELDA:
            raise ConfError(
                f"pstack.toml {nombre or 'celda'}: clave desconocida {k!r}; "
                f"las válidas son {', '.join(CLAVES_CELDA)}"
            )
    skills = raw.get("skills", raw.get("skill", [SKILL_BASE]))
    if isinstance(skills, str):
        skills = [skills]
    if not skills or skills[0] != SKILL_BASE:
        skills = [SKILL_BASE, *[s for s in skills if s != SKILL_BASE]]
    return Exigencia(
        skills=tuple(skills),
        playbook=bool(raw.get("playbook", False)),
        min_subagentes=int(raw.get("min_subagentes", 0)),
    )


def parse_conf(raw: dict) -> PstackConf:
    """`pstack.toml` ya leído -> PstackConf. [exigencia.<carril>.<papel>] (o <papel>.<carril>)."""
    p = raw.get("pstack", {})
    for k in p:
        if k not in CLAVES_PSTACK:
            raise ConfError(
                f"pstack.toml [pstack]: clave desconocida {k!r}; "
                f"las válidas son {', '.join(CLAVES_PSTACK)}"
            )
    if not isinstance(p.get("exigir", False), bool):
        raise ConfError("pstack.toml [pstack] exigir debe ser true o false")
    celdas: dict[tuple[str, str], Exigencia] = {}
    for k1, sub in (raw.get("exigencia") or {}).items():
        if not isinstance(sub, dict):
            raise ConfError(f"pstack.toml [exigencia.{k1}] debe ser una tabla")
        for k2, celda in sub.items():
            lane, papel = (k1, k2) if k1 in LANES else (k2, k1)
            if lane not in LANES or papel not in PAPELES or not isinstance(celda, dict):
                raise ConfError(
                    f"pstack.toml [exigencia.{k1}.{k2}]: carril {LANES} y papel {PAPELES}"
                )
            celdas[(lane, papel)] = _celda(celda, f"[exigencia.{k1}.{k2}]")
    return PstackConf(
        commit=str(p.get("commit", "")),
        exigir=bool(p.get("exigir", False)),
        exigencias=celdas,
    )
