"""Rutas entre Windows y WSL, en los dos sentidos.

Solo stdlib y funciones puras: se prueban en cualquier sistema y no necesitan WSL. El espacio
y el runtime de un encargo viven en el disco de Windows (`C:\\Users\\...`) y dentro de la distro
son `/mnt/c/Users/...`. El motor guarda y compara rutas de Windows; el agente en WSL ve y escribe
rutas de Linux. Cada frontera traduce con estas funciones.

- `a_wsl` / `a_windows` traducen una ruta completa;
- `argv_a_wsl` / `argv_a_windows` traducen los elementos de un argv, incluidos `--opcion=ruta`;
- `texto_a_wsl` / `texto_a_windows` traducen las rutas sueltas dentro de un texto (un prompt,
  una nota). Una ruta con espacios dentro de un texto se corta en el primer espacio: solo se puede
  traducir entera cuando es un valor aparte (un elemento de argv, un campo).
  `texto_a_wsl` también sirve para un texto que lleva JSON escrito con `json.dumps` (el prompt de
  un revisor lleva las observaciones así): reconoce la ruta con barras dobles y se detiene en el
  escape que le sigue (`\\n`, `\\t`, `\\"`, `\\u00f1`), así que el JSON sigue siendo el mismo JSON.
  `texto_a_windows` es solo para texto plano: lo que emite lleva barras simples;
- `valores_a_wsl` / `valores_a_windows` traducen cada cadena de un documento JSON ya leído, y
  `json_a_wsl` / `json_a_windows` hacen lo mismo con el texto JSON: lo leen, traducen los valores
  y lo vuelven a escribir. Es lo que se usa con un `.interno/*.json`.

Lo que no tiene equivalente al otro lado (una ruta de Linux sin distro conocida, un UNC de otro
servidor, una ruta relativa) se devuelve igual.
"""

from __future__ import annotations

import json
import re

MONTAJE = "/mnt"
_UNC_WSL = ("wsl.localhost", "wsl$")
_OPCION = re.compile(r"^(-{0,2}[A-Za-z_][\w.-]*)=(.+)$", re.DOTALL)
_SEPARADORES = re.compile(r"[\\/]+")
# Una ruta de Windows dentro de un texto. Tiene dos formas, que se distinguen por el primer
# separador: `C:\\Users\\x` (barras dobles, como sale de un json.dumps) cuyas partes no contienen
# barras, de modo que el escape que sigue (`\n`, `\t`, `\"`, `\u00f1`) la termina; y
# `C:\Users\x` o `C:/Users/x`. Una ruta puede empezar justo después de un `\n`, `\t`... escapado.
_UNIDAD_EN_TEXTO = re.compile(
    r"(?:(?<![\w.:/\\-])|(?<=\\[nrtbf]))(?:\\\\\?\\)?[A-Za-z]:"
    r"(?:(?:\\\\[^\s\"'<>|*?`\\/]*)+|(?=[\\/])[^\s\"'<>|*?`]*)"
)
_COLA = ".,;:)]}!"


def _partes(ruta: str) -> list[str]:
    return [p for p in _SEPARADORES.split(ruta) if p]


def _unidad(ruta: str) -> tuple[str, str] | None:
    """(letra, resto) si la ruta empieza por una unidad de Windows, con o sin `\\\\?\\`."""
    if ruta.startswith("\\\\?\\"):
        ruta = ruta[4:]
    if len(ruta) >= 2 and ruta[0].isalpha() and ruta[0].isascii() and ruta[1] == ":":
        if len(ruta) == 2 or ruta[2] in "\\/":
            return ruta[0].lower(), ruta[2:]
    return None


def a_wsl(ruta: str, *, montaje: str = MONTAJE) -> str:
    """`C:\\Users\\x` -> `/mnt/c/Users/x`. `\\\\wsl.localhost\\<distro>\\home\\x` -> `/home/x`.
    Lo demás queda igual."""
    unidad = _unidad(ruta)
    if unidad:
        letra, resto = unidad
        return "/".join([montaje.rstrip("/"), letra, *_partes(resto)])
    if ruta.startswith("\\\\"):
        partes = _partes(ruta)
        if len(partes) >= 2 and partes[0].lower() in _UNC_WSL:
            return "/" + "/".join(partes[2:])
    return ruta


def a_windows(ruta: str, *, montaje: str = MONTAJE, distro: str = "") -> str:
    """`/mnt/c/Users/x` -> `C:\\Users\\x`. Una ruta de Linux se vuelve
    `\\\\wsl.localhost\\<distro>\\...` solo si se conoce la distro. Lo demás queda igual."""
    base = montaje.rstrip("/") + "/"
    if ruta.startswith(base) or ruta == base[:-1]:
        partes = _partes(ruta[len(base) :])
        if partes and len(partes[0]) == 1 and partes[0].isalpha() and partes[0].isascii():
            return partes[0].upper() + ":\\" + "\\".join(partes[1:])
    if distro and ruta.startswith("/"):
        return "\\\\wsl.localhost\\" + distro + "\\" + "\\".join(_partes(ruta))
    return ruta


def _por_elemento(elemento: str, traducir) -> str:
    traducido = traducir(elemento)
    if traducido != elemento:
        return traducido
    opcion = _OPCION.match(elemento)
    if opcion:
        valor = traducir(opcion.group(2))
        if valor != opcion.group(2):
            return f"{opcion.group(1)}={valor}"
    return elemento


def argv_a_wsl(argv: list, *, montaje: str = MONTAJE) -> list:
    return [_por_elemento(x, lambda r: a_wsl(r, montaje=montaje)) for x in argv]


def argv_a_windows(argv: list, *, montaje: str = MONTAJE, distro: str = "") -> list:
    return [
        _por_elemento(x, lambda r: a_windows(r, montaje=montaje, distro=distro))
        for x in argv
    ]


def _sin_cola(ruta: str) -> tuple[str, str]:
    cuerpo = ruta.rstrip(_COLA)
    return cuerpo, ruta[len(cuerpo) :]


def texto_a_wsl(texto: str, *, montaje: str = MONTAJE) -> str:
    def cambia(m: re.Match) -> str:
        cuerpo, cola = _sin_cola(m.group(0))
        return a_wsl(cuerpo, montaje=montaje) + cola

    return _UNIDAD_EN_TEXTO.sub(cambia, texto)


def texto_a_windows(texto: str, *, montaje: str = MONTAJE) -> str:
    base = re.escape(montaje.rstrip("/"))
    patron = re.compile(
        r"(?<![\w.:/\\~-])" + base + r"/[A-Za-z](?=/|(?![\w.-]))[^\s\"'<>|*?`:]*"
    )

    def cambia(m: re.Match) -> str:
        cuerpo, cola = _sin_cola(m.group(0))
        return a_windows(cuerpo, montaje=montaje) + cola

    return patron.sub(cambia, texto)


def _recorre(doc, traducir):
    if isinstance(doc, str):
        return traducir(doc)
    if isinstance(doc, list):
        return [_recorre(x, traducir) for x in doc]
    if isinstance(doc, dict):
        return {k: _recorre(v, traducir) for k, v in doc.items()}
    return doc


def valores_a_wsl(doc, *, montaje: str = MONTAJE):
    """Cada cadena del documento que es una ruta de Windows entera pasa a su ruta de WSL.
    Las claves no se tocan y el documento original queda intacto."""
    return _recorre(doc, lambda s: _por_elemento(s, lambda r: a_wsl(r, montaje=montaje)))


def valores_a_windows(doc, *, montaje: str = MONTAJE, distro: str = ""):
    return _recorre(
        doc,
        lambda s: _por_elemento(
            s, lambda r: a_windows(r, montaje=montaje, distro=distro)
        ),
    )


def json_a_wsl(texto: str, *, montaje: str = MONTAJE) -> str:
    """Texto JSON con rutas de Windows -> el mismo JSON con rutas de WSL. Levanta ValueError
    si no es JSON. El formato se reescribe; los valores que no son rutas llegan intactos."""
    return json.dumps(
        valores_a_wsl(json.loads(texto), montaje=montaje), ensure_ascii=False
    )


def json_a_windows(texto: str, *, montaje: str = MONTAJE, distro: str = "") -> str:
    return json.dumps(
        valores_a_windows(json.loads(texto), montaje=montaje, distro=distro),
        ensure_ascii=False,
    )
