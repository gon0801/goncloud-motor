"""El motor pide y comprueba poteto-mode. Todo lo de "usar pstack" que no toca disco ni procesos.

- `huella(fuente, formato)`: qué hizo el agente, sacado de SU registro (nunca de su resultado).
- `juzga(huella, exigencia)`: `Cumple` o `Falta` con lo que faltó en lenguaje llano.
- `puerta(eventos, n)`: ¿hay que cortar al agente porque a la acción n aún no cargó poteto-mode?
- `instrucciones(exigencia, cli)`: la sección "Cómo trabajas" del encargo; sale de la misma
  exigencia que luego juzga, así lo que se pide y lo que se comprueba no se separan.
- `FORMATOS`: una fila por CLI con el aspecto, en su registro, de cargar una skill, lanzar un
  subagente y correr un comando. La fuente del registro de cada CLI: docs/arena/pstack/hechos.md §4.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

SKILL_BASE = "poteto-mode"
PUERTA_ACCIONES = (
    6  # poteto-mode se carga antes de la acción 6 (decisión de David, 2026-10-21)
)
LANES = ("fast", "gate", "release")
PAPELES = ("implementar", "revisar")


# --------------------------------------------------------------------------- tipos


@dataclass(frozen=True)
class Exigencia:
    """El piso mecánico de un (carril, papel). Viene de `pstack.toml`."""

    skills: tuple[str, ...] = (SKILL_BASE,)
    playbook: bool = False
    todos: bool = False
    min_subagentes: int = 0
    puerta: int = PUERTA_ACCIONES

    def to_dict(self) -> dict:
        return {
            "skills": list(self.skills),
            "playbook": self.playbook,
            "todos": self.todos,
            "min_subagentes": self.min_subagentes,
            "puerta": self.puerta,
        }

    @staticmethod
    def from_dict(d: dict) -> Exigencia:
        return Exigencia(
            skills=tuple(d.get("skills", (SKILL_BASE,))),
            playbook=bool(d.get("playbook", False)),
            todos=bool(d.get("todos", False)),
            min_subagentes=int(d.get("min_subagentes", 0)),
            puerta=int(d.get("puerta", PUERTA_ACCIONES)),
        )


@dataclass(frozen=True)
class Accion:
    """Una llamada de herramienta del agente principal, ya sin el formato de su CLI."""

    tipo: str  # skill | playbook | todos | subagente | comando | otra
    nombre: str = ""  # skill sin prefijo, playbook, tipo de subagente o comando
    n: int = 0  # 1, 2, 3... entre las acciones del agente principal
    lee: tuple[
        tuple[str, str], ...
    ] = ()  # ("skill"|"playbook", nombre) leídos por terminal


@dataclass(frozen=True)
class Huella:
    skills: tuple[str, ...] = ()
    playbooks: tuple[str, ...] = ()
    todos: int = 0
    subagentes: tuple[str, ...] = ()
    comandos: tuple[str, ...] = ()
    acciones: int = 0
    poteto_en: int | None = None  # número de la acción que cargó poteto-mode
    legible: bool = True  # False: el registro no se pudo leer ("no verificable")

    def to_dict(self) -> dict:
        return {
            "skills": list(self.skills),
            "playbooks": list(self.playbooks),
            "todos": self.todos,
            "subagentes": list(self.subagentes),
            "comandos": list(self.comandos)[:200],
            "acciones": self.acciones,
            "poteto_en": self.poteto_en,
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
        # `muse exec --json`: solo el resultado de cada herramienta, con su nombre y, al leer, la ruta
        p = o["payload"]
        nombre = str((p.get("correlation_facts") or {}).get("tool_name", ""))
        ruta = re.search(r"`(/[^`]+)`", str(p.get("text", "")))
        return (
            [(nombre, {"path": ruta.group(1), "name": ruta.group(1)} if ruta else {})]
            if nombre
            else []
        ), visto
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
    skill: tuple[str, ...]  # herramientas que cargan una skill por nombre
    leer: tuple[
        str, ...
    ]  # herramientas que leen un archivo (cargar = leer su SKILL.md)
    subagente: tuple[str, ...]
    todos: tuple[str, ...]
    comando: tuple[str, ...]
    arg_id: tuple[
        str, ...
    ] = ()  # el CLI repite la misma llamada con otro estado: se cuenta una vez
    sin_skill: str = ""  # por qué este CLI no tiene herramienta de skills y carga leyendo el SKILL.md
    sin_carga_en: tuple[
        tuple[str, str], ...
    ] = ()  # (máquina, motivo): en esa máquina el CLI no entra y no puede cargar skills


FORMATOS: dict[str, Formato] = {
    "claude": Formato(
        "claude",
        "transcript ~/.claude/projects/<cwd>/<sesion>.jsonl (con --session-id) o log.txt en stream-json",
        _bloques,
        skill=("skill",),
        leer=("read",),
        subagente=("agent", "task"),
        todos=("todowrite",),
        comando=("bash",),
    ),
    "kimi": Formato(
        "kimi",
        "log.txt con --output-format stream-json (copia en ~/.kimi-code/sessions/<wd>/<sesion>/agents/main/wire.jsonl)",
        _tool_calls,
        skill=("skill",),
        leer=("read", "readfile"),
        subagente=("agent", "agentswarm"),
        todos=("settodolist",),
        comando=("bash", "shell"),
    ),
    "opencode": Formato(
        "opencode",
        "log.txt con `opencode run --format json`",
        _estado,
        skill=("skill",),
        leer=("read",),
        subagente=("task", "subagent"),
        todos=("todowrite",),
        comando=("bash",),
    ),
    "grok": Formato(
        "grok",
        "~/.grok/sessions/<cwd>/<sessionId>/updates.jsonl (grok no tiene herramienta de skills: cargar = read_file)",
        _tool_name,
        sin_skill="grok no tiene herramienta de skills: carga poteto-mode leyendo su SKILL.md con read_file",
        skill=(),
        leer=("read_file",),
        subagente=("spawn_subagent",),
        todos=("todo_write",),
        comando=("run_terminal_command",),
    ),
    "muse": Formato(
        "muse",
        "~/.local/share/muse/sessions/AAAA/MM/DD/<id>/session.jsonl (o `muse exec --json` en log.txt)",
        _muse,
        skill=("read_skill",),
        leer=("read_file", "read"),
        subagente=("subagent_spawn", "workflow"),
        todos=("write_todos",),
        comando=("bash", "run_command", "shell"),
        sin_carga_en=(
            ("mac", "missing meta credentials: falta `muse login` en la Mac"),
            ("mini", "missing meta credentials: falta `muse login` en la Mini"),
        ),
    ),
    "qwen": Formato(
        "qwen",
        "log.txt con --output-format stream-json, con la salida a un archivo (por una tubería corta a 64 KB); herramienta `skill`, subagentes por `tool_call{name:agent}`",
        _bloques,
        skill=("skill",),
        leer=("read_file", "read"),
        subagente=("task", "agent"),
        todos=("todo_write", "todowrite"),
        comando=("run_shell_command", "bash"),
    ),
}

_RUTA_SKILL = re.compile(r"([^/]+)/SKILL\.md$")
_RUTA_PLAYBOOK = re.compile(rf"{SKILL_BASE}/playbooks/([^/]+)\.md$")


def _sin_private(ruta: str) -> str:
    """En macOS `/var`, `/tmp` y `/etc` son enlaces a `/private/...`: el agente lee por cualquiera de las dos."""
    return (
        ruta[len("/private") :]
        if re.match(r"/private/(var|tmp|etc)(/|$)", ruta)
        else ruta
    )


def _bajo_skills(ruta: str, raiz: str | None) -> str | None:
    """Lo que queda de `ruta` bajo `<raiz>/pstack/skills/`, o None si cae fuera de la copia fija
    (otra copia, la skill propia del agente, un archivo que él escribió)."""
    if not raiz:
        return None
    base = _sin_private(posixpath.normpath(raiz.replace("\\", "/"))) + "/pstack/skills/"
    r = _sin_private(posixpath.normpath(ruta.replace("\\", "/")))
    if re.match(r"[A-Za-z]:", base):  # rutas de Windows: sin distinguir mayúsculas
        ok = r.lower().startswith(base.lower())
    else:
        ok = r.startswith(base)
    return r[len(base) :] if ok else None


def _nombre_skill(x: str) -> str:
    return x.strip().rsplit(":", 1)[
        -1
    ]  # `pstack:poteto-mode` y `poteto-mode` cuentan igual


_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ASIGNA = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_VARIABLE = re.compile(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})")
_SALIDA_A_OTRO_LADO = re.compile(r"(?:1|&)?>")
_ENTRADA = re.compile(r"\d*<")
_LECTORES = ("cat", "sed", "head")
_PALABRAS_DE_CONTROL = (
    "do",
    "then",
    "else",
    "elif",
    "if",
    "while",
    "until",
    "!",
    "{",
    "time",
)


def _sin_heredocs(comando: str) -> str:
    salida: list[str] = []
    cierre: str | None = None
    for linea in comando.split("\n"):
        if cierre is not None:
            if linea.strip() == cierre:
                salida.append(linea)
                cierre = None
            continue
        salida.append(linea)
        if m := _HEREDOC.search(linea):
            cierre = m.group(2)
    return "\n".join(salida)


def _simples(comando: str) -> list[tuple[str, bool]] | None:
    """Parte `comando` en comandos simples, respetando comillas: (texto, ¿su salida va por tubería?).
    None si unas comillas no cierran."""
    partes: list[tuple[str, bool]] = []
    buf: list[str] = []
    q = ""
    i, n = 0, len(comando)

    def corta(tuberia: bool = False) -> None:
        partes.append(("".join(buf), tuberia))
        buf.clear()

    while i < n:
        c = comando[i]
        sig = comando[i + 1] if i + 1 < n else ""
        if q:
            buf.append(c)
            if c == "\\" and q == '"' and sig:
                buf.append(sig)
                i += 1
            elif c == q:
                q = ""
        elif c == "\\" and sig:
            buf.append(c + sig)
            i += 1
        elif c in "'\"":
            q = c
            buf.append(c)
        elif c in "\n;":
            corta()
        elif c == "&" and sig == "&":
            corta()
            i += 1
        elif c == "&" and (sig == ">" or (buf and buf[-1][-1:] in "<>")):
            buf.append(c)
        elif c == "&":
            corta()
        elif c == "|" and sig == "|":
            corta()
            i += 1
        elif c == "|" and buf and buf[-1][-1:] == ">":
            buf.append(c)
        elif c == "|":
            corta(True)
            if sig == "&":
                i += 1
        else:
            buf.append(c)
        i += 1
    if q:
        return None
    corta()
    return partes


def _expande(palabra: str, variables: dict[str, str]) -> str:
    return _VARIABLE.sub(
        lambda m: variables.get(m.group(1) or m.group(2), m.group(0)), palabra
    )


def _argumentos(palabras: list[str]) -> list[str] | None:
    """Los argumentos de un comando sin sus redirecciones; None si desvía o descarta su salida."""
    args: list[str] = []
    i = 0
    while i < len(palabras):
        w = palabras[i]
        if _SALIDA_A_OTRO_LADO.match(w):
            return None
        if _ENTRADA.match(w):
            i += 1 if re.fullmatch(r"\d*<+-?", w) is None else 2
            continue
        if re.fullmatch(r"\d+>>?", w):
            i += 2
            continue
        args.append(w)
        i += 1
    return args


def lecturas_de_terminal(comando: str, raiz: str | None) -> tuple[tuple[str, str], ...]:
    """Las skills y playbooks de la copia fija (`<raiz>/pstack/skills/`) que `comando` lee con
    `cat`, `sed` o `head`, en orden, como ("skill", nombre) y ("playbook", nombre).
    No cuenta lo que no es un comando (cuerpos de heredoc), lo que escribe o desvía su salida,
    lo que va por tubería, `sed -i`, ni rutas relativas o de otra copia."""
    if not raiz or not comando:
        return ()
    simples = _simples(_sin_heredocs(comando))
    if simples is None:
        return ()
    variables: dict[str, str] = {}
    lee: list[tuple[str, str]] = []
    for texto, tuberia in simples:
        try:
            palabras = shlex.split(texto)
        except ValueError:
            return ()
        while palabras and palabras[0] in _PALABRAS_DE_CONTROL:
            palabras.pop(0)
        asignaciones = []
        while palabras and _ASIGNA.match(palabras[0]):
            asignaciones.append(palabras.pop(0))
        if not palabras:
            palabras_asignadas = asignaciones
        elif palabras[0] == "export" and not asignaciones:
            palabras_asignadas = [w for w in palabras[1:] if _ASIGNA.match(w)]
        else:
            palabras_asignadas = []
        for w in palabras_asignadas:
            nombre, valor = w.split("=", 1)
            variables[nombre] = _expande(valor, variables)
        if palabras_asignadas or not palabras or tuberia:
            continue
        palabras = [_expande(w, variables) for w in palabras]
        if palabras[0] not in _LECTORES:
            continue
        args = _argumentos(palabras[1:])
        if args is None:
            continue
        if palabras[0] == "sed" and any(
            a == "--in-place"
            or a.startswith("--in-place=")
            or re.match(r"-[A-Za-z]*i", a)
            for a in args
        ):
            continue
        for a in args:
            rel = _bajo_skills(a, raiz)
            if rel is None:
                continue
            if m := _RUTA_PLAYBOOK.fullmatch(rel):
                lee.append(("playbook", m.group(1)))
            elif m := _RUTA_SKILL.fullmatch(rel):
                lee.append(("skill", m.group(1)))
    return tuple(lee)


def _clasifica(
    f: Formato, herramienta: str, args: dict, n: int, raiz: str | None = None
) -> Accion:
    h = herramienta.lower().rsplit(".", 1)[-1]
    if h in f.skill:
        nombre = next(
            (
                str(args[k])
                for k in ("skill", "name", "skill_name", "id")
                if args.get(k)
            ),
            "",
        )
        if mm := _RUTA_SKILL.search(
            nombre.replace("\\", "/")
        ):  # muse: `read_skill` con la ruta del SKILL.md
            if raiz and _bajo_skills(nombre.replace("\\", "/"), raiz) is None:
                return Accion(
                    "otra", "", n
                )  # otra copia de la skill: no es la copia fija
            nombre = mm.group(1)
        return Accion("skill", _nombre_skill(nombre), n)
    if h in f.leer:
        ruta = str(
            next(
                (
                    args[k]
                    for k in (
                        "file_path",
                        "path",
                        "filePath",
                        "absolute_path",
                        "target_file",
                    )
                    if args.get(k)
                ),
                "",
            )
        ).replace("\\", "/")
        rel = _bajo_skills(ruta, raiz)
        if rel is not None:
            if mm := _RUTA_PLAYBOOK.fullmatch(rel):
                return Accion("playbook", mm.group(1), n)
            if mm := _RUTA_SKILL.fullmatch(rel):
                return Accion("skill", mm.group(1), n)
        return Accion("otra", "", n)
    if h in f.subagente:
        tipo = next(
            (
                str(args[k])
                for k in ("subagent_type", "agent", "agent_type", "role", "description")
                if args.get(k)
            ),
            "subagente",
        )
        return Accion("subagente", tipo, n)
    if h in f.todos:
        return Accion("todos", "", n)
    if h in f.comando:
        cmd = next((str(args[k]) for k in ("command", "cmd") if args.get(k)), "")
        return Accion("comando", cmd, n, lecturas_de_terminal(cmd, raiz))
    return Accion("otra", "", n)


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


def eventos(
    fuente: str | Iterable[str], formato: str, raiz: str | None = None
) -> tuple[list[Accion], bool]:
    """(acciones del agente principal en orden, ¿el registro se pudo leer?). Lo usan `huella` y la
    puerta en vivo de runwrap.
    Leer un `SKILL.md` solo cuenta como cargar la skill si está bajo `<raiz>/pstack/skills/` (la copia fija)."""
    f = FORMATOS[formato]
    acciones: list[Accion] = []
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
        for herramienta, args in llamadas:
            acciones.append(_clasifica(f, herramienta, args, len(acciones) + 1, raiz))
    return acciones, legible


def _cargas(a: Accion, tipo: str) -> tuple[str, ...]:
    """Los nombres de `tipo` (skill o playbook) que la acción cargó: por su herramienta o leyendo por terminal."""
    propios = (a.nombre,) if a.tipo == tipo else ()
    return propios + tuple(nombre for t, nombre in a.lee if t == tipo)


def huella(
    fuente: str | Iterable[str] | None, formato: str, raiz: str | None = None
) -> Huella:
    """Lo que el agente hizo según su registro. Sin registro, o con uno que no se entiende: `legible=False`."""
    if not fuente or formato not in FORMATOS:
        return Huella(legible=False)
    acciones, legible = eventos(fuente, formato, raiz)
    if not legible:
        return Huella(legible=False)
    skills = tuple(
        dict.fromkeys(nombre for a in acciones for nombre in _cargas(a, "skill"))
    )
    return Huella(
        skills=skills,
        playbooks=tuple(
            dict.fromkeys(nombre for a in acciones for nombre in _cargas(a, "playbook"))
        ),
        todos=sum(a.tipo == "todos" for a in acciones),
        subagentes=tuple(a.nombre for a in acciones if a.tipo == "subagente"),
        comandos=tuple(a.nombre for a in acciones if a.tipo == "comando"),
        acciones=len(acciones),
        poteto_en=next(
            (a.n for a in acciones if SKILL_BASE in _cargas(a, "skill")),
            None,
        ),
    )


# --------------------------------------------------------------------------- juicio


def puerta(eventos_: list[Accion], n: int = PUERTA_ACCIONES) -> bool:
    """True si hay que cortar: el agente ya hizo `n` acciones y entre ellas no cargó poteto-mode."""
    if n <= 0 or len(eventos_) < n:
        return False
    return not any(SKILL_BASE in _cargas(a, "skill") for a in eventos_[:n])


def juzga(h: Huella, e: Exigencia) -> Cumple | Falta:
    if not h.legible:
        return Cumple(verificable=False)
    faltas: list[str] = []
    for s in e.skills:
        if s not in h.skills:
            faltas.append(f"no cargó la skill {s}")
    if SKILL_BASE in h.skills and e.puerta and (h.poteto_en or 0) > e.puerta:
        faltas.append(
            f"cargó poteto-mode en su acción {h.poteto_en}; debía cargarla antes de la acción {e.puerta}"
        )
    if e.playbook and not h.playbooks:
        faltas.append("no abrió ningún playbook de poteto-mode")
    if e.todos and not h.todos:
        faltas.append("no usó su lista de tareas")
    if len(h.subagentes) < e.min_subagentes:
        faltas.append(
            f"lanzó {len(h.subagentes)} subagente(s) y se pedían al menos {e.min_subagentes}"
        )
    return Falta(tuple(faltas)) if faltas else Cumple()


def instrucciones(
    e: Exigencia, cli: str, raiz: str = "{pstack}", correccion: bool = False
) -> str:
    """La sección "Cómo trabajas" del encargo. `raiz` es la carpeta de la copia fija de pstack.
    `correccion`: el intento arregla lo que otro dejó mal; el playbook ya no se elige, es `bug-fix`."""
    f = FORMATOS.get(cli)
    cargar = (
        f"la herramienta de skills de tu CLI ({', '.join(f'`{x}`' for x in f.skill)})"
        if f and f.skill
        else "una lectura de su `SKILL.md`"
    )
    pasos = [
        f"Carga la skill `{SKILL_BASE}` antes de tu acción número {e.puerta}: con {cargar} "
        f"o leyendo `{raiz}/pstack/skills/{SKILL_BASE}/SKILL.md`. Si no la cargas, el motor te detiene."
    ]
    for s in e.skills:
        if s != SKILL_BASE:
            pasos.append(
                f"Carga y sigue completa la skill `{s}` (`{raiz}/pstack/skills/{s}/SKILL.md`)."
            )
    if cli == "claude":
        pasos.append(
            "Abre cada `SKILL.md` y el playbook con la herramienta `Read`, no con `cat` en la terminal."
        )
    if correccion:
        pasos.append(
            f"Esta es una ronda de corrección: abre el playbook `bug-fix` de `{SKILL_BASE}` "
            f"(`{raiz}/pstack/skills/{SKILL_BASE}/playbooks/bug-fix.md`) y síguelo."
        )
    elif e.playbook:
        pasos.append(
            f"Elige un playbook de `{SKILL_BASE}` y ábrelo (`{raiz}/pstack/skills/{SKILL_BASE}/playbooks/`)."
        )
    if e.todos:
        pasos.append("Lleva tu lista de tareas con la herramienta de tareas de tu CLI.")
    if e.min_subagentes:
        pasos.append(
            f"Lanza al menos {e.min_subagentes} subagente(s) reales de tu CLI cuando el playbook lo pida."
        )
    pasos.append(
        "El motor lee el registro de tus herramientas, no tu resumen: lo que solo digas no cuenta."
    )
    cuerpo = "\n".join(f"{i}. {p}" for i, p in enumerate(pasos, 1))
    return (
        f"## Cómo trabajas\nEste encargo se hace con pstack, en la copia fija `{raiz}`. "
        f"Antes lee `{raiz}/pstack-motor/motor.md` y `{raiz}/pstack-motor/{cli}.md`.\n{cuerpo}\n"
    )


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


def _celda(raw: dict) -> Exigencia:
    skills = raw.get("skills", raw.get("skill", [SKILL_BASE]))
    if isinstance(skills, str):
        skills = [skills]
    if not skills or skills[0] != SKILL_BASE:
        skills = [SKILL_BASE, *[s for s in skills if s != SKILL_BASE]]
    return Exigencia(
        skills=tuple(skills),
        playbook=bool(raw.get("playbook", False)),
        todos=bool(raw.get("todos", False)),
        min_subagentes=int(raw.get("min_subagentes", 0)),
        puerta=int(raw.get("puerta", PUERTA_ACCIONES)),
    )


def parse_conf(raw: dict) -> PstackConf:
    """`pstack.toml` ya leído -> PstackConf. [exigencia.<carril>.<papel>] (o <papel>.<carril>)."""
    p = raw.get("pstack", {})
    if not isinstance(p.get("exigir", False), bool):
        raise ValueError("pstack.toml [pstack] exigir debe ser true o false")
    celdas: dict[tuple[str, str], Exigencia] = {}
    for k1, sub in (raw.get("exigencia") or {}).items():
        if not isinstance(sub, dict):
            raise ValueError(f"pstack.toml [exigencia.{k1}] debe ser una tabla")
        for k2, celda in sub.items():
            lane, papel = (k1, k2) if k1 in LANES else (k2, k1)
            if lane not in LANES or papel not in PAPELES or not isinstance(celda, dict):
                raise ValueError(
                    f"pstack.toml [exigencia.{k1}.{k2}]: carril {LANES} y papel {PAPELES}"
                )
            celdas[(lane, papel)] = _celda(celda)
    return PstackConf(
        commit=str(p.get("commit", "")),
        exigir=bool(p.get("exigir", False)),
        exigencias=celdas,
    )
