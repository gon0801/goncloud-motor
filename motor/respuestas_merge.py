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


# Las que repetir el mismo merge puede arreglar, cada una con la etiqueta que su
# espera lleva antes del primer ": ". El motor cuenta los reintentos seguidos por
# ese prefijo: con su etiqueta, una respuesta no gasta los intentos de otra.
ETIQUETAS: dict[type[BaseException], str] = {
    BaseMovida: "base movida",
    ErrorDeGitHub: "error de GitHub",
    MergeSinConfirmar: "merge sin confirmar",
    PrSinAsentar: "PR sin asentar",
    MergeSinRespuesta: "merge sin respuesta",
}
PASAJERAS = tuple(ETIQUETAS)
# La política no dejó mergear y no se supo si por los checks.
CHECKS_SIN_LEER = "checks sin leer"

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


def motivo_del_merge(
    numero: int, error: BaseException, lectura: BaseException | None = None
) -> str:
    """Por qué no se integró el PR. `lectura` es el error al leer los checks
    requeridos tras un rechazo de la política."""
    etiqueta = CHECKS_SIN_LEER if lectura is not None else ETIQUETAS.get(type(error))
    serie = f" ({etiqueta})" if etiqueta else ""
    motivo = f"no se pudo integrar el PR #{numero}{serie}: {error}"
    if lectura is not None:
        motivo += f"; no se pudieron leer los checks requeridos: {lectura}"
    return motivo
