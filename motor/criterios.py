"""Expediente compartido de un bloque: mandato, medicion y sugerencia.

Privado. No decide el avance (eso sigue en machine) ni ejecuta efectos.
Una sugerencia no fabrica un Incumple ni autoriza un shell.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from .plan import Block, Plan

Id = str
Huella = str
Fase = Literal["acepta", "integracion", "instalacion", "prueba_real"]
CasoPrueba = Literal["dinero", "datos", "instala", "falla"]
_CASOS = ("dinero", "datos", "instala", "falla")
_SECRETOS = ("secreto", "credencial", "token", "contrasena", "contraseña")


@dataclass(frozen=True)
class Criterio:
    id: Id
    version: Huella
    fuente: str
    proteccion: Literal["seguridad", "datos_dinero", "comportamiento", "prueba_exigible"]
    conducta: str
    excepciones: tuple[str, ...]
    receta: str
    fase: Fase
    caso_prueba: CasoPrueba | None
    bloque: str = ""


@dataclass(frozen=True)
class Alcance:
    bloque: str
    criterio: Id
    escenario: Id
    destino: str


@dataclass(frozen=True)
class ClaveEvidencia:
    alcance: Alcance
    arbol: Huella
    base_relevante: Huella | None
    criterio_version: Huella
    receta: Huella
    entorno: Huella
    fase: Fase
    generacion_destino: Huella | None
    operacion: Id | None


@dataclass(frozen=True)
class Cumple:
    esperado: str
    observado: str
    recibo: Id


@dataclass(frozen=True)
class Incumple:
    esperado: str
    observado: str
    recibo: Id


@dataclass(frozen=True)
class NoMedido:
    causa: Literal["herramienta", "codigo", "proveedor", "consulta", "ambiguo"]
    recurso: str
    evidencia: str


@dataclass(frozen=True)
class Medicion:
    id: Id
    clave: ClaveEvidencia
    resultado: Cumple | Incumple | NoMedido


@dataclass(frozen=True)
class Sugerencia:
    id: Id
    intento_o_comentario: Id
    criterio: Id | None
    escenario_propuesto: str
    conducta_rota: str
    repro_propuesto: str


@dataclass(frozen=True)
class Corregido:
    alcance: Alcance
    medicion: Id


@dataclass(frozen=True)
class Aclarado:
    alcance: Alcance
    mandato: str
    intento: Id
    criterio_vigente: Criterio
    fundamento: str
    evento_mandato: int
    resultado: Literal["aplicar_excepcion_vigente", "medir_criterio_aclarado"]


@dataclass(frozen=True)
class Expediente:
    criterios: tuple[Criterio, ...]
    mediciones: tuple[Medicion, ...]
    resoluciones: tuple[Corregido | Aclarado, ...]
    sugerencias: tuple[Sugerencia, ...]


@dataclass(frozen=True)
class Nota:
    motivo: str


@dataclass(frozen=True)
class Medir:
    criterio: Criterio
    alcance: Alcance


@dataclass(frozen=True)
class Aclarar:
    criterios: tuple[Id, ...]
    mandato: str
    alcance: Alcance


def _version(block: Block, contenido: str, acepta: str) -> str:
    raw = "\n".join((contenido, acepta, block.pruebas, block.prueba_real))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _proteccion(block: Block, texto: str) -> str:
    if block.pruebas in ("dinero", "datos"):
        return "datos_dinero"
    bajo = texto.lower()
    if any(p in bajo for p in _SECRETOS):
        return "seguridad"
    if block.pruebas in _CASOS:
        return "prueba_exigible"
    return "comportamiento"


def _fase(block: Block) -> str:
    if block.pruebas == "instala":
        return "instalacion"
    if block.prueba_real and block.pruebas in _CASOS:
        return "prueba_real"
    return "acepta"


def criterios_de(plan: Plan, block: Block) -> tuple[Criterio, ...]:
    """Un criterio por fila que trae Acepta. El id no se parte despues."""
    out = []
    for row in block.rows:
        if not row.acepta.strip():
            continue
        texto = f"{row.contenido} {row.acepta}"
        caso = block.pruebas if block.pruebas in _CASOS else None
        out.append(
            Criterio(
                id=f"{plan.id}/{block.id}/{row.id}",
                version=_version(block, row.contenido, row.acepta),
                fuente=row.id,
                proteccion=_proteccion(block, texto),
                conducta=row.contenido,
                excepciones=(),
                receta=row.acepta,
                fase=_fase(block),
                caso_prueba=caso,
                bloque=block.id,
            )
        )
    return tuple(out)


def _alcance(criterio: Criterio, escenario: str = "") -> Alcance:
    return Alcance(
        bloque=criterio.bloque,
        criterio=criterio.id,
        escenario=escenario or criterio.fuente,
        destino="",
    )


def valorar(sugerencia: Sugerencia, expediente: Expediente) -> Nota | Medir | Aclarar:
    """Sin hecho nuevo no reabre. No ejecuta el repro ni fabrica un Incumple."""
    if sugerencia.criterio is None or not sugerencia.conducta_rota.strip():
        return Nota("sin criterio o sin conducta rota: queda como nota")
    crit = next((c for c in expediente.criterios if c.id == sugerencia.criterio), None)
    if crit is None:
        return Nota("criterio desconocido: queda como nota")
    for med in expediente.mediciones:
        mismo = med.clave.alcance.criterio == crit.id
        if mismo and isinstance(med.resultado, Cumple):
            return Nota("hecho nuevo: ya hay un Cumple vigente de ese criterio")
    for res in expediente.resoluciones:
        if isinstance(res, Corregido) and res.alcance.criterio == crit.id:
            return Nota("hecho nuevo: ese criterio ya quedo corregido")
        if isinstance(res, Aclarado) and res.alcance.criterio == crit.id:
            return Nota("hecho nuevo: ese criterio ya quedo aclarado")
    pedido = sugerencia.repro_propuesto.strip()
    if pedido and pedido != crit.receta.strip():
        return Nota("la receta propuesta no es la del criterio: no se ejecuta")
    chocan = [
        c
        for c in expediente.criterios
        if c.receta == crit.receta and c.conducta != crit.conducta and c.id != crit.id
    ]
    if chocan:
        ids = tuple(c.id for c in (crit, *chocan))
        return Aclarar(
            criterios=ids,
            mandato="dos criterios con la misma receta piden conductas distintas",
            alcance=_alcance(crit, sugerencia.escenario_propuesto),
        )
    return Medir(criterio=crit, alcance=_alcance(crit, sugerencia.escenario_propuesto))


def _medicion(c: Criterio, head: str, visto: dict) -> Medicion:
    if visto.get("ok"):
        resultado = Cumple(
            esperado=c.conducta,
            observado="la verificacion del criterio salio bien",
            recibo=f"verified:{head}",
        )
    else:
        faltan = list(visto.get("falta_comando") or [])
        resultado = NoMedido(
            causa="herramienta" if faltan else "ambiguo",
            recurso=", ".join(faltan),
            evidencia=str(visto.get("detalle", ""))[:300],
        )
    return Medicion(
        id=f"verified:{head}:{c.id}",
        clave=ClaveEvidencia(
            alcance=_alcance(c),
            arbol=head,
            base_relevante=None,
            criterio_version=c.version,
            receta=c.version,
            entorno=str(visto.get("huella_maquina") or ""),
            fase=c.fase,
            generacion_destino=None,
            operacion=None,
        ),
        resultado=resultado,
    )


def expediente_del_bloque(bloque: str, snapshot) -> Expediente:
    """El mismo expediente para implementador, aclarador y asesor."""
    bv = snapshot.blocks[bloque]
    criterios = criterios_de(snapshot.plan, bv.block)
    mediciones = []
    head = bv.head()
    visto = bv.verified.get(head) if head else None
    if visto is not None:
        mediciones = [_medicion(c, head, visto) for c in criterios]
    sugerencias = _sugerencias(bv)
    return Expediente(
        criterios=criterios,
        mediciones=tuple(mediciones),
        resoluciones=tuple(getattr(bv, "resoluciones", ()) or ()),
        sugerencias=tuple(sugerencias),
    )


def _sugerencias(bv) -> list[Sugerencia]:
    out = []
    for rev in bv.reviews:
        for i, hallazgo in enumerate(rev.get("bloqueantes") or []):
            if not isinstance(hallazgo, dict):
                continue
            out.append(
                Sugerencia(
                    id=f"{rev.get('sha', '')}:{i}",
                    intento_o_comentario=str(rev.get("modelo") or ""),
                    criterio=None,
                    escenario_propuesto=str(hallazgo.get("texto") or ""),
                    conducta_rota=str(hallazgo.get("texto") or ""),
                    repro_propuesto=str(hallazgo.get("repro") or ""),
                )
            )
    return out


def texto(exp: Expediente) -> str:
    """El mismo texto al frente de los dos encargos."""
    if not (exp.criterios or exp.mediciones or exp.sugerencias or exp.resoluciones):
        return ""
    lineas = ["## Expediente"]
    for c in exp.criterios:
        lineas.append(
            f"- {c.id} ({c.proteccion}, {c.fase}): {c.conducta} | Acepta: {c.receta}"
        )
    for med in exp.mediciones:
        lineas.append(_linea_medicion(med))
    for sug in exp.sugerencias:
        lineas.append(f"- sugerencia, no es una orden: {sug.conducta_rota}")
    for res in exp.resoluciones:
        lineas.append(_linea_resolucion(res))
    return "\n".join(lineas) + "\n\n"


def _linea_medicion(med: Medicion) -> str:
    r = med.resultado
    cid = med.clave.alcance.criterio
    if isinstance(r, Cumple):
        return f"- {cid}: Cumple ({r.recibo})"
    if isinstance(r, Incumple):
        return f"- {cid}: Incumple ({r.recibo})"
    return f"- {cid}: NoMedido ({r.causa})"


def _linea_resolucion(res) -> str:
    if isinstance(res, Corregido):
        return f"- corregido: {res.alcance.criterio}"
    return f"- aclarado: {res.alcance.criterio}"
