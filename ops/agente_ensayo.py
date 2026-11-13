"""Agente de ensayo: un programa, no un modelo.

El motor lo corre con `--agente ensayo`, que lanza `bash <espacio>/trabajo.sh`.
Ese `trabajo.sh` llama a este archivo con la ruta de un escenario JSON:

    exec "$PYTHON" "$ARBOL/ops/agente_ensayo.py" "$(dirname "$0")/escenario.json"

Lee el turno por el protocolo normal (el prompt del turno o la revision.json
que el lanzador deja en el intento) y escribe producto y juicio según el
escenario. Habla el protocolo del motor, así que un cambio de protocolo lo
actualiza en el mismo PR.

Solo usa la biblioteca estándar de Python 3.11 o posterior, así que corre con
el intérprete del servicio sin instalar nada.
"""

import json
import re
import sys
import types
from pathlib import Path
from typing import (
    Literal,
    NotRequired,
    TypedDict,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

Archivos = dict[str, str]
"""Ruta relativa al espacio y su contenido completo."""


class Dependencia(TypedDict):
    fase: str
    version: NotRequired[str]


class Receta(TypedDict, total=False):
    desplegar: str
    checklist: str
    rollback: str


class Fase(TypedDict):
    """Las claves que el motor acepta en una fase de `.interno/fases.json`
    (`_adoptar_fases` y `datos_despliegue` en `motor/encargos/dominio.py`)."""

    id: str
    resultado: str
    requiere: NotRequired[list[str | Dependencia]]
    requiere_despliegue: NotRequired[bool | None]
    lane: NotRequired[Literal["fast", "gate", "release"]]
    despliegue: NotRequired[Receta]


class Comprobacion(TypedDict):
    objetivo: str
    argv: list[str]
    tope: int


class Escenario(TypedDict):
    """Lo que el agente hace en cada turno. Las claves de fase son `Fase.id`."""

    fases: list[Fase]
    producto: dict[str, Archivos]
    exigido: NotRequired[dict[str, Archivos]]
    """Lo que el revisor compara por fase; sin él, compara contra `producto`."""
    comprobaciones: NotRequired[dict[str, list[Comprobacion]]]
    arreglos: NotRequired[dict[str, Archivos]]
    """Por texto contenido en el título de un bloqueante abierto, lo que el
    responsable reescribe al corregir."""
    resuelve: NotRequired[bool]
    """Con False, el revisor que aprueba una corrección no nombra `resueltos`."""
    titulo_bloqueo: NotRequired[str]


class FormaInvalida(ValueError):
    pass


def _validar(valor: object, tipo: object, donde: str) -> None:
    """Comprueba `valor` contra `tipo`. Los TypedDict de arriba son la única
    descripción de la forma: una clave nueva se declara ahí y queda validada."""
    origen = get_origin(tipo)
    if is_typeddict(tipo):
        if not isinstance(valor, dict):
            raise FormaInvalida(f"{donde}: se esperaba un objeto")
        claves = vars(tipo)
        requeridas, opcionales = claves["__required_keys__"], claves["__optional_keys__"]
        faltan = requeridas - valor.keys()
        sobran = valor.keys() - requeridas - opcionales
        if faltan or sobran:
            raise FormaInvalida(f"{donde}: faltan={sorted(faltan)} sobran={sorted(sobran)}")
        campos = get_type_hints(tipo)
        for clave, sub in valor.items():
            _validar(sub, campos[clave], f"{donde}.{clave}")
    elif origen is list:
        if not isinstance(valor, list):
            raise FormaInvalida(f"{donde}: se esperaba una lista")
        for i, sub in enumerate(valor):
            _validar(sub, get_args(tipo)[0], f"{donde}[{i}]")
    elif origen is dict:
        if not isinstance(valor, dict):
            raise FormaInvalida(f"{donde}: se esperaba un objeto")
        for clave, sub in valor.items():
            _validar(sub, get_args(tipo)[1], f"{donde}.{clave}")
    elif origen is Literal:
        if valor not in get_args(tipo):
            raise FormaInvalida(f"{donde}: {valor!r} no es uno de {list(get_args(tipo))}")
    elif origen in (Union, types.UnionType):
        for opcion in get_args(tipo):
            try:
                _validar(valor, opcion, donde)
                return
            except FormaInvalida:
                continue
        raise FormaInvalida(f"{donde}: {valor!r} no tiene ninguna forma de {tipo}")
    elif tipo is type(None):
        if valor is not None:
            raise FormaInvalida(f"{donde}: se esperaba null")
    elif tipo in (str, bool, int):
        # `type is`: en JSON true no es un número, aunque en Python bool herede de int.
        if type(valor) is not tipo:
            raise FormaInvalida(f"{donde}: se esperaba {tipo.__name__}, llegó {valor!r}")
    else:
        raise TypeError(f"{donde}: forma sin validar {tipo}")


def leer_escenario(ruta: Path) -> Escenario:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    try:
        _validar(datos, Escenario, "escenario")
        if not datos["fases"]:
            raise FormaInvalida("escenario.fases: el agente toma la primera fase y no hay")
        for i, fase in enumerate(datos["fases"]):
            # La regla con la que `_adoptar_fases` del dominio rechaza un id, sin el
            # strip: el producto se indexa por el id tal como está escrito.
            fid = fase["id"]
            if not fid or not all(c.isalnum() or c in "-_" for c in fid):
                raise FormaInvalida(
                    f"escenario.fases[{i}].id: {fid!r} no es un id de fase que el motor acepte"
                )
    except FormaInvalida as e:
        raise ValueError(f"{ruta}: {e}") from None
    return cast(Escenario, datos)


def _intento(espacio: Path) -> Path:
    raiz = espacio.parent.parent / "ejecucion" / espacio.name
    return raiz / (raiz / "actual").read_text(encoding="utf-8").strip()


def _prompt(espacio: Path, dir_: Path) -> str:
    arranque = json.loads((dir_ / "arranque.json").read_text(encoding="utf-8"))
    runtime = espacio.parent.parent / "runtime" / espacio.name / arranque["token"]
    return (runtime / "prompt.md").read_text(encoding="utf-8")


def _fase_activa(prompt: str) -> str:
    m = re.search(r"Trabaja solo la fase activa \(([\w-]+)\)", prompt)
    return m.group(1) if m else ""


def _fase_activa_del_revisor(prompt: str) -> str:
    m = re.search(r"(?:cubre solo la|fase final) fase ([\w-]+):", prompt)
    return m.group(1) if m else ""


def _bloqueantes_del_prompt(prompt: str) -> dict[str, str]:
    abiertos: dict[str, str] = {}
    for linea in prompt.splitlines():
        m = re.match(r"^(h_[0-9a-f]+) \[[a-z_]+\] (.+)$", linea)
        if m:
            abiertos[m.group(1)] = m.group(2).strip()
    return abiertos


def _escribir(escenario: Escenario, interno: Path, espacio: Path, fase: str) -> None:
    interno.mkdir(parents=True, exist_ok=True)
    for ruta, contenido in escenario["producto"].get(fase, {}).items():
        destino = espacio / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido, encoding="utf-8", newline="\n")
    (interno / "contexto.md").write_text(
        "producto de la fase " + fase + " escrito por el agente de ensayo",
        encoding="utf-8", newline="\n",
    )
    (interno / "evidencia.txt").write_text(
        "los archivos de la fase " + fase + " quedaron con el contenido del escenario",
        encoding="utf-8", newline="\n",
    )
    (interno / "cuerpo-pr.md").write_text(
        "Fase " + fase + " del banco compuerta. Comprobado con las comprobaciones declaradas.",
        encoding="utf-8", newline="\n",
    )
    declaradas = escenario.get("comprobaciones", {}).get(fase)
    if declaradas:
        (interno / "comprobaciones.json").write_text(
            json.dumps({"comprobaciones": declaradas}, indent=1),
            encoding="utf-8", newline="\n",
        )


def trabajar(escenario: Escenario, espacio: Path, dir_: Path) -> None:
    interno = espacio / ".interno"
    prompt = _prompt(espacio, dir_)
    fase = _fase_activa(prompt)
    if not fase:
        # Primera vuelta: organiza el trabajo en fases y toma la primera.
        (interno / "fases.json").write_text(
            json.dumps({"fases": escenario["fases"]}, indent=1),
            encoding="utf-8", newline="\n",
        )
        fase = escenario["fases"][0]["id"]
    _escribir(escenario, interno, espacio, fase)
    for titulo in _bloqueantes_del_prompt(prompt).values():
        for patron, fichas in escenario.get("arreglos", {}).items():
            if patron in titulo:
                for ruta, contenido in fichas.items():
                    destino = espacio / ruta
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    destino.write_text(contenido, encoding="utf-8", newline="\n")


def revisar(escenario: Escenario, espacio: Path, dir_: Path) -> None:
    revision = json.loads((dir_ / "revision.json").read_text(encoding="utf-8"))
    prompt = _prompt(espacio, dir_)
    fase = _fase_activa_del_revisor(prompt)
    alcance = str(revision.get("alcance") or "completa")
    exigido = escenario.get("exigido", escenario["producto"]).get(fase, {})
    cumple = all(
        (espacio / ruta).is_file()
        and (espacio / ruta).read_text(encoding="utf-8") == contenido
        for ruta, contenido in exigido.items()
    )
    cubre = _bloqueantes_del_prompt(prompt)
    juicio: dict[str, object] = {
        "version": revision["version"],
        "evidencia": (
            "comparé el producto de la fase " + fase + " con el pedido: "
            + ("coincide" if cumple else "no coincide con lo pedido")
        ),
    }
    if not cumple:
        juicio["veredicto"] = "cambios"
        juicio["bloqueantes"] = [{
            "criterio": "comportamiento",
            "titulo": escenario.get(
                "titulo_bloqueo",
                "el producto de la fase " + fase + " no cumple el pedido",
            ),
            "reproduccion": "comparar el producto de la fase con el pedido",
            "ancla": (sorted(exigido) or ["producto"])[0] + ":1",
        }]
    else:
        juicio["veredicto"] = "aprobado"
        if alcance == "correccion" and cubre and escenario.get("resuelve", True):
            juicio["resueltos"] = sorted(cubre)
        juicio["aprobadas"] = [sorted(exigido)] if exigido else []
    Path(revision["resultado"]).write_text(
        json.dumps(juicio, ensure_ascii=False), encoding="utf-8", newline="\n"
    )


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("uso: agente_ensayo.py <escenario.json>")
    escenario = leer_escenario(Path(sys.argv[1]))
    espacio = Path.cwd()
    dir_ = _intento(espacio)
    if (dir_ / "revision.json").is_file():
        revisar(escenario, espacio, dir_)
    else:
        trabajar(escenario, espacio, dir_)


if __name__ == "__main__":
    main()
