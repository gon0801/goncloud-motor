"""Lo que significa cada respuesta de GitHub a un merge. El texto de GitHub se lee
solo aquí, en la frontera: fuera solo viajan tipos."""

from __future__ import annotations


class MergeRechazado(RuntimeError):
    pass


class PuntaMovida(MergeRechazado):
    """La punta de la rama del PR ya no es el SHA revisado."""


class Conflicto(MergeRechazado):
    """La rama choca con la base y no se puede crear el merge."""


class PoliticaBloquea(MergeRechazado):
    """La política de la rama (protección, revisiones, checks) no deja mergear."""


class Borrador(MergeRechazado):
    """El PR sigue en borrador."""


class BaseMovida(MergeRechazado):
    """La base cambió mientras GitHub mergeaba: el mismo merge se puede repetir."""


class ErrorDeGitHub(MergeRechazado):
    """GitHub contestó con un error suyo (5xx): el mismo merge se puede repetir."""


class MergeSinConfirmar(MergeRechazado):
    """`gh pr merge` salió bien, pero GitHub todavía no muestra el PR mergeado."""


class PrSinAsentar(MergeRechazado):
    """GitHub dijo que la rama del PR cambió, pero su punta sigue en el SHA pedido:
    todavía asienta el PR."""


class MergeSinRespuesta(RuntimeError):
    """`gh pr merge` no terminó a tiempo. No es un rechazo: no se sabe si GitHub
    mergeó, así que quien reintenta vuelve a mirar el PR antes."""


class PrSinLeer(RuntimeError):
    """`gh pr merge` salió bien, pero el PR no se pudo leer para confirmarlo. Como
    `MergeSinRespuesta`, no es un rechazo: no se sabe si GitHub mergeó."""


# Las que repetir el mismo merge puede arreglar, cada una con la etiqueta que su
# espera lleva después del primer ": ".
ETIQUETAS: dict[type[BaseException], str] = {
    BaseMovida: "base movida",
    ErrorDeGitHub: "error de GitHub",
    MergeSinConfirmar: "merge sin confirmar",
    PrSinAsentar: "PR sin asentar",
    MergeSinRespuesta: "merge sin respuesta",
    PrSinLeer: "PR sin leer",
}
PASAJERAS = tuple(ETIQUETAS)
# La política no dejó mergear y no se supo si por los checks.
CHECKS_SIN_LEER = "checks sin leer"
# Los tropiezos de un merge (las PASAJERAS y los checks sin leer) comparten una
# serie: el motor cuenta las esperas seguidas por lo que va antes del primer
# ": ", y todas empiezan igual. Con una serie por tipo, dos tipos que se alternan
# no llegan nunca al tope. Con cinco, dos lecturas fallidas todavía dejan tres
# intentos a una base movida.
TOPE_TROPIEZOS_MERGE = 5

_RECHAZOS_DE_MERGE = (
    (Borrador, ("draft", "borrador")),
    (PuntaMovida, ("head branch was modified", "does not match", "match-head",
                   "expected head", "head commit")),
    (BaseMovida, ("base branch was modified",)),
    (ErrorDeGitHub, ("http 500", "http 502", "http 503", "http 504")),
    (Conflicto, ("cannot be cleanly created", "conflict", "conflicto")),
)


def clasificar_merge(error: BaseException) -> MergeRechazado:
    if isinstance(error, MergeRechazado):
        return error
    texto = str(error)
    for tipo, marcas in _RECHAZOS_DE_MERGE:
        if any(marca in texto.lower() for marca in marcas):
            return tipo(texto)
    return PoliticaBloquea(texto)


def tras_el_rechazo(
    error: BaseException, lectura: BaseException | None, causa: str, seguidos: int
) -> tuple[str, str]:
    """La espera con la que se repite el mismo merge ("" si no se repite) y la
    causa con la que queda el rechazo. Se repite un tropiezo (una respuesta
    pasajera, o `lectura`, el error al leer los checks requeridos tras un rechazo
    de la política) mientras la serie no llega al tope. `seguidos` son los
    tropiezos seguidos antes de este; el que llega al tope dice cuántos hubo."""
    if lectura is None and not isinstance(error, PASAJERAS):
        return "", causa
    if seguidos + 1 < TOPE_TROPIEZOS_MERGE:
        return causa, causa
    operacion, detalle = causa.split(": ", 1)
    return "", f"{operacion} tras {seguidos + 1} tropiezos seguidos: {detalle}"


def motivo_del_merge(
    numero: int, error: BaseException, lectura: BaseException | None = None
) -> str:
    """Por qué no se integró el PR. `lectura` es el error al leer los checks
    requeridos tras un rechazo de la política."""
    etiqueta = CHECKS_SIN_LEER if lectura is not None else ETIQUETAS.get(type(error))
    tipo = f"{etiqueta}: " if etiqueta else ""
    motivo = f"no se pudo integrar el PR #{numero}: {tipo}{error}"
    if lectura is not None:
        motivo += f"; no se pudieron leer los checks requeridos: {lectura}"
    return motivo
