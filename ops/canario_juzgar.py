"""El juicio del canario de encargos: de una `Observacion` y lo esperado, una `Fila`
por cada entrada de `TABLA`, con `pasa`, `falla` o `no medido`. Es pura: no lee
nada fuera de sus argumentos, salvo `leer_esperado`, que lee el archivo de lo
esperado.

El archivo de lo esperado dice qué resultado se espera en cada fila y los topes.
Una fila que pasaba y falla es regresión; una que empieza a pasar obliga a
actualizar el archivo.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, cast, get_args

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from motor.encargos.politica import CERRADOS_VENTANA_S  # noqa: E402
from motor.io import leer_json  # noqa: E402
from ops.canario_observacion import (  # noqa: E402
    Apagon,
    Detencion,
    Momento,
    NoPliega,
    Observacion,
    Pliegue,
    Quien,
    TipoResto,
)
from tools import plegado  # noqa: E402

ESPERADO = RAIZ / "ops" / "canario_encargos.esperado.json"

Resultado = Literal["pasa", "falla", "no medido"]
NombreFila = Literal[
    "termina", "entregado", "limpio", "no pierde", "no repite",
    "cuesta poco", "se detiene", "versión", "legible",
]


@dataclass(frozen=True)
class Topes:
    minutos_termina: int
    minutos: int
    turnos_por_rol: int
    rondas: int
    minutos_se_detiene: int
    """Lo que puede tardar en pararse el encargo que falla a propósito."""


@dataclass(frozen=True)
class Esperado:
    filas: Mapping[NombreFila, Resultado]
    topes: Topes


@dataclass(frozen=True)
class Fila:
    nombre: NombreFila
    punto: str
    resultado: Resultado
    esperado: Resultado
    observado: str
    fuente: str

    @property
    def discrepancia(self) -> str:
        return DISCREPANCIAS.get((self.esperado, self.resultado), "")


DISCREPANCIAS: dict[tuple[Resultado, Resultado], str] = {
    ("pasa", "falla"): "regresión",
    ("pasa", "no medido"): "dejó de medirse",
    ("falla", "no medido"): "dejó de medirse",
    ("falla", "pasa"): "mejora: actualiza el esperado",
    ("no medido", "pasa"): "ya se mide: actualiza el esperado",
    ("no medido", "falla"): "ya se mide: actualiza el esperado",
}

Juicio = tuple[Resultado, str]

Plazo = int | Literal["actualización"]

# La tabla "Limpieza" de docs/modelo-centro-encargos.md, entrada por entrada,
# más los procesos que pide el diseño del canario. Lo que la regla conserva
# (el recibo del despliegue, lo que tiene plazo sin vencer) no es resto.
LIMPIEZA: tuple[tuple[TipoResto, str, Plazo], ...] = (
    ("rama", "rama remota encargo/*, se borra al mergear o cerrar el PR", 0),
    ("pr", "PR de un encargo cancelado, se cierra al cancelar", 0),
    ("espacio", "espacio/<id>, se borra 7 días después del estado final", 7 * 24 * 60 * 60),
    ("ejecucion", "ejecucion/<id>, se borra al cerrar la ventana de sondeo", CERRADOS_VENTANA_S),
    ("runtime", "credenciales del turno en runtime/<id>, se borran al terminar cada turno", 0),
    ("despliegue", "despliegues/<clave>, se conserva el recibo y se borra lo demás", 0),
    ("referencia", "referencias a ramas remotas en el clon del servicio,"
     " fetch --prune en cada actualización", "actualización"),
    ("proceso", "procesos de sus turnos y despliegues, no quedan al cerrar", 0),
)


def _hora(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _duracion(desde: str, hasta: str) -> timedelta:
    return _hora(hasta) - _hora(desde)


def _texto_duracion(d: timedelta) -> str:
    minutos, segundos = divmod(int(d.total_seconds()), 60)
    return f"{minutos} min {segundos} s" if segundos else f"{minutos} min"


def _sin_pliegue(obs: Observacion) -> Juicio | None:
    if obs.pliegue is None:
        return "no medido", "el encargo no está en el registro"
    if isinstance(obs.pliegue, NoPliega):
        return "no medido", "el registro no se puede plegar (ver la fila legible)"
    return None


def _bajado(p: Pliegue) -> str:
    if p.nivel_efectivo == p.nivel_autorizado:
        return ""
    motivo = f": {p.motivo_efectivo}" if p.motivo_efectivo else ""
    return f"el motor bajó el objetivo de {p.nivel_autorizado} a {p.nivel_efectivo}{motivo}"


def _termina(obs: Observacion, topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    if p.estado != "terminado":
        return "falla", f"el encargo quedó {p.estado}, no terminado"
    if not p.fases:
        return "falla", "el encargo terminó sin fases"
    if not p.completo:
        faltan = [
            f"la fase {f.id} quedó {f.estado}" + (f" ({f.causa})" if f.causa else "")
            for f in p.fases if f.estado != "preparada"
        ]
        return "falla", "entrega parcial: " + "; ".join(faltan)
    debajo = [
        f"la fase {f.id} quedó en nivel {f.nivel}, por debajo de lo que el motor exige"
        for f in p.fases if not f.cumplido
    ]
    if debajo:
        return "falla", "; ".join(debajo)
    duracion = _duracion(p.recibido, p.cierre or obs.hora)
    tarde = duracion > timedelta(minutes=topes.minutos_termina)
    niveles = ", ".join(
        f"{f.id} {f.nivel}" + ("" if f.desplegable else " (no requiere despliegue)")
        for f in p.fases
    )
    partes = [
        f"terminó solo sus {len(p.fases)} fases, cada una en el nivel que el motor exige"
        f" ({niveles})",
        f"{'pero ' if tarde else ''}tardó {_texto_duracion(duracion)}"
        f" y el tope es {topes.minutos_termina} min",
    ]
    if bajado := _bajado(p):
        partes.append(bajado)
    if any(f.nivel != p.nivel_alcanzado for f in p.fases):
        partes.append(
            f"el motor reporta nivel {p.nivel_alcanzado} porque mira solo su fase activa,"
            f" {p.fase_activa}"
        )
    return ("falla" if tarde else "pasa"), "; ".join(partes)


def _entregado(obs: Observacion, _topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    if p.nivel_autorizado not in ("integrado", "desplegado"):
        return "no medido", f"se pidió nivel {p.nivel_autorizado}: no hay merge que comprobar"
    fallas: list[str] = []
    sin_leer: list[str] = []
    if p.estado != "terminado":
        fallas.append(f"el encargo quedó {p.estado}, no entregado")
    if bajado := _bajado(p):
        fallas.append(bajado)
    if not p.base:
        sin_leer.append("la base pedida, que el registro no guarda")
    commits: dict[str, str] = {}
    for pr in obs.prs:
        if pr.numero == 0:
            fallas.append(f"la fase {pr.fase} no abrió PR")
        elif pr.estado is None:
            sin_leer.append(f"el PR #{pr.numero}, porque GitHub no respondió")
        elif pr.estado != "mergeado":
            fallas.append(f"el PR #{pr.numero} está {pr.estado}")
        elif not pr.base:
            sin_leer.append(f"la base del PR #{pr.numero}, que GitHub no dio")
        elif p.base and pr.base != p.base:
            fallas.append(f"el PR #{pr.numero} se mergeó en {pr.base} y se pidió {p.base}")
        elif not pr.merge_commit:
            sin_leer.append(f"el commit de merge del PR #{pr.numero}, que GitHub no dio")
        elif p.base:
            commits[pr.fase] = pr.merge_commit
    if not obs.prs:
        fallas.append("ninguna fase tiene PR")
    desplegables = [f.id for f in p.fases if f.desplegable]
    desplegadas = 0
    for fase in desplegables:
        if fase not in commits:
            continue
        if any(
            r.fase == fase and r.estado == "aplicado" and r.sha == commits[fase]
            for r in obs.recibos
        ):
            desplegadas += 1
            continue
        rechazos = [r.estado for r in obs.recibos if r.fase == fase and r.estado != "aplicado"]
        fallas.append(
            f"la fase {fase} no tiene recibo aplicado de su commit mergeado"
            + (f" ({'; '.join(rechazos)})" if rechazos else "")
        )
    if obs.archivo is not None:
        ruta = obs.archivo.ruta
        if obs.archivo.contenido == "no existe":
            fallas.append(f"no existe el archivo desplegado {ruta}")
        elif obs.archivo.contenido == "sin la marca":
            fallas.append(f"el archivo desplegado {ruta} no trae la marca")
        elif obs.archivo.contenido == "ilegible":
            sin_leer.append(f"el archivo desplegado {ruta}")
    if fallas:
        return "falla", "; ".join(fallas)
    if sin_leer:
        return "no medido", "no pude leer " + ", ".join(sin_leer)
    partes = [f"PR mergeados en {p.base}: {len(commits)} de {len(obs.prs)}"]
    if p.nivel_autorizado == "desplegado":
        partes.append(
            f"fases con recibo de despliegue aplicado de su commit mergeado:"
            f" {desplegadas} de {len(desplegables)}"
        )
    partes.append(
        "el archivo desplegado trae la marca" if obs.archivo is not None
        else "sin archivo desplegado que revisar"
    )
    return "pasa", "; ".join(partes)


def _vencimiento(plazo: Plazo, cierre: datetime, actualizado: str) -> tuple[datetime, str] | None:
    """Desde cuándo el resto está vencido y cómo decirlo; None si no se puede saber."""
    if plazo == "actualización":
        if not actualizado:
            return None
        hecha = _hora(actualizado)
        if hecha > cierre:
            return hecha, f"la actualización del {hecha:%Y-%m-%d %H:%M}"
        return datetime.max.replace(tzinfo=timezone.utc), "la próxima actualización del servicio"
    vence = cierre + timedelta(seconds=plazo)
    return vence, f"{vence:%Y-%m-%d %H:%M}"


def _limpio(obs: Observacion, _topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    if not p.cierre:
        return "no medido", f"el encargo sigue {p.estado}: la limpieza empieza al cerrar"
    cierre, ahora = _hora(p.cierre), _hora(obs.hora)
    quedan: list[str] = []
    en_plazo: list[str] = []
    sin_mirar: list[str] = []
    for tipo, regla, plazo in LIMPIEZA:
        restos = [r for r in obs.restos if r.tipo == tipo]
        if not restos:
            sin_mirar.append(f"la regla «{regla}», que no se revisó")
        for resto in restos:
            if resto.queda is None:
                sin_mirar.append(resto.nombre)
                continue
            if not resto.queda:
                continue
            vencimiento = _vencimiento(plazo, cierre, obs.actualizado)
            if vencimiento is None:
                sin_mirar.append(f"si {resto.nombre} ya venció: no sé cuándo se actualizó el servicio")
            elif plazo == 0 or ahora >= vencimiento[0]:
                quedan.append(resto.nombre)
            else:
                en_plazo.append(f"{resto.nombre} hasta {vencimiento[1]}")
    plazo_txt = f"; dentro de su plazo: {', '.join(en_plazo)}" if en_plazo else ""
    if quedan:
        return "falla", "quedan " + ", ".join(quedan) + plazo_txt
    if sin_mirar:
        return "no medido", "no pude mirar " + ", ".join(sin_mirar) + plazo_txt
    return "pasa", (
        f"nada vencido sin borrar: revisé las {len(LIMPIEZA)} reglas de limpieza"
        f" ({len(obs.restos)} restos){plazo_txt}"
    )


def _cuesta_poco(obs: Observacion, topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    duracion = _duracion(p.recibido, p.cierre or obs.hora)
    turnos = ", ".join(f"{rol} {n}" for rol, n in p.turnos) or "ninguno"
    texto = (
        f"turnos {turnos} (tope {topes.turnos_por_rol} por rol);"
        f" rondas de revisión {p.rondas} (tope {topes.rondas});"
        f" {_texto_duracion(duracion)} (tope {topes.minutos})"
    )
    caro = (
        any(n > topes.turnos_por_rol for _, n in p.turnos)
        or p.rondas > topes.rondas
        or duracion > timedelta(minutes=topes.minutos)
    )
    return ("falla" if caro else "pasa"), texto


def _version(obs: Observacion, _topes: Topes) -> Juicio:
    v = obs.version
    faltan = [
        nombre for nombre, valor in (
            ("el SHA desplegado (instalacion.json)", v.desplegado),
            ("el SHA del árbol del servicio", v.arbol),
            ("el SHA de la última pasada del supervisor", v.pasada),
        ) if not valor
    ]
    if faltan:
        return "no medido", "no pude leer " + ", ".join(faltan)
    if v.arbol == v.desplegado and v.pasada == v.desplegado:
        return "pasa", f"árbol y última pasada del supervisor en {v.desplegado[:12]}, el desplegado"
    return "falla", (
        f"desplegado {v.desplegado[:12]}, árbol {v.arbol[:12]},"
        f" última pasada {v.pasada[:12]}"
    )


def _legible(obs: Observacion, _topes: Topes) -> Juicio:
    p = obs.pliegue
    if p is None:
        return "no medido", "el encargo no está en el registro"
    if isinstance(p, NoPliega):
        return "falla", f"el plegado se rompe en el evento {p.evento} ({p.tipo}): {p.excepcion}"
    if p.violaciones:
        return "falla", "pliega, pero no sostiene " + ", ".join(p.violaciones)
    return "pasa", f"pliega y sostiene las {len(plegado.INVARIANTES)} invariantes de tools/plegado.py"


Condicion = tuple[bool | None, str]
"""True: se cumple; False: no; None: no se pudo mirar. El texto lo dice."""


def _corto(texto: str, largo: int = 160) -> str:
    return texto if len(texto) <= largo else texto[: largo - 1] + "…"


def _paro_con_palabra(p: Pliegue) -> Condicion:
    """docs/modelo-centro-encargos.md, "Qué significa cada palabra del reporte": una fase
    bloqueada se reporta "entrega parcial" con cada fase que falta y su causa, y `detenido`
    queda para sus cuatro causas. La causa de un `detenido` la juzgan `_se_paro_solo` y
    `_dice_por_que`: el registro todavía no la guarda como dato."""
    if p.estado == "detenido":
        return True, "quedó detenido"
    if p.estado != "terminado":
        return False, f"quedó {p.estado}: ni detenido ni entrega parcial"
    if p.completo:
        return False, "quedó terminado completo: no dijo que algo no se pudo"
    faltan = [f for f in p.fases if f.estado != "preparada"]
    if not faltan:
        return False, "quedó terminado sin fases: no nombra qué faltó"
    calladas = [f.id for f in faltan if not f.causa]
    if calladas:
        return False, f"quedó como entrega parcial sin decir por qué falta la fase {', '.join(calladas)}"
    return True, "quedó como entrega parcial: " + "; ".join(
        f"la fase {f.id}, «{_corto(f.causa)}»" for f in faltan
    )


def _se_paro_solo(p: Pliegue, hora: str, topes: Topes) -> Condicion:
    paradas = [o for o in p.ordenes if o in ("parar", "cancelar")]
    if paradas:
        return False, f"no se paró solo: recibió la orden {', '.join(paradas)}"
    if p.estado not in ("detenido", "cancelado", "terminado"):
        return False, f"no se paró: sigue {p.estado}"
    duracion = _duracion(p.recibido, p.cierre or hora)
    if duracion > timedelta(minutes=topes.minutos_se_detiene):
        return False, (
            f"tardó {_texto_duracion(duracion)} en pararse y el tope es"
            f" {topes.minutos_se_detiene} min"
        )
    return True, f"se paró solo en {_texto_duracion(duracion)} (tope {topes.minutos_se_detiene} min)"


def _dice_por_que(p: Pliegue) -> Condicion:
    causas = [*p.causas, *(f"la fase {f.id}: {f.causa}" for f in p.fases if f.causa)]
    if not causas:
        return False, "no dejó escrita la causa"
    return True, f"dijo por qué: «{_corto(causas[0])}»"


def _no_deja_roto(d: Detencion) -> Condicion:
    """El freno corrió (un checklist lanzado y en rojo), cada despliegue quedó revertido y
    el destino ya no lo trae. Lo que no se pudo leer no cuenta como revertido."""
    if d.archivo is not None and d.archivo.contenido == "con la marca":
        return False, f"{d.archivo.ruta} todavía trae lo desplegado"
    if d.despliegues is None:
        return None, "sus recibos de despliegue"
    if any(x.estado == "aplicado" for x in d.despliegues):
        return False, "su despliegue quedó aplicado: el checklist no falló"
    desplegados = [x for x in d.despliegues if any(paso == "desplegar" for paso, _ in x.pasos)]
    sin_revertir = [
        (x.fase, rollback) for x in desplegados
        if (rollback := dict(x.pasos).get("rollback", "sin correr")) != 0
    ]
    if sin_revertir:
        return False, "; ".join(
            f"el despliegue de la fase {fase} quedó sin revertir (rollback {rollback})"
            for fase, rollback in sin_revertir
        )
    rojos = [codigo for x in d.despliegues for paso, codigo in x.pasos if paso == "checklist" and codigo != 0]
    if not rojos:
        return False, "el checklist no salió en rojo en ningún recibo: el freno no corrió"
    if d.archivo is not None and d.archivo.contenido == "ilegible":
        return None, f"el destino {d.archivo.ruta}"
    destino = f" y {d.archivo.ruta} ya no lo trae" if d.archivo is not None else ""
    return True, f"el checklist salió {rojos[0]}, revirtió su despliegue (rollback salida 0){destino}"


def _acotado(p: Pliegue, topes: Topes) -> Condicion:
    turnos = ", ".join(f"{rol} {n}" for rol, n in p.turnos) or "ninguno"
    gasto = (
        f"turnos {turnos} (tope {topes.turnos_por_rol} por rol) y rondas de revisión"
        f" {p.rondas} (tope {topes.rondas})"
    )
    de_mas = any(n > topes.turnos_por_rol for _, n in p.turnos) or p.rondas > topes.rondas
    return (False, f"gastó de más: {gasto}") if de_mas else (True, f"gastó {gasto}")


def _se_detiene(obs: Observacion, topes: Topes) -> Juicio:
    """Con un checklist de despliegue que falla a propósito, el motor para el encargo
    solo y con una palabra del modelo, con la causa escrita, sin dejar el despliegue
    roto y sin gastar de más."""
    d = obs.detencion
    if d is None:
        return "no medido", "esta corrida no hizo el encargo cuyo checklist falla a propósito"
    if d.pliegue is None:
        return "no medido", f"el encargo que falla a propósito, {d.encargo}, no está en el registro"
    if isinstance(d.pliegue, NoPliega):
        return "no medido", (
            f"el registro de {d.encargo} se rompe en el evento {d.pliegue.evento}"
            f" ({d.pliegue.tipo}): {d.pliegue.excepcion}"
        )
    p = d.pliegue
    condiciones = (
        _paro_con_palabra(p), _se_paro_solo(p, obs.hora, topes), _dice_por_que(p), _no_deja_roto(d),
        _acotado(p, topes),
    )
    fallas = [texto for ok, texto in condiciones if ok is False]
    sin_mirar = [texto for ok, texto in condiciones if ok is None]
    cumple = [texto for ok, texto in condiciones if ok]
    if fallas:
        return "falla", "; ".join(fallas) + (f"; sí {'; '.join(cumple)}" if cumple else "")
    if sin_mirar:
        return "no medido", "no pude leer " + "; ".join(sin_mirar)
    return "pasa", "; ".join(cumple)


MOMENTOS: dict[Momento, str] = {
    "turno": "el turno del responsable",
    "receta": "la receta de despliegue",
}

ACEPTADAS: tuple[tuple[Quien, int], ...] = (
    ("responsable", 1), ("revisor", 1), ("desplegar", 1), ("checklist", 1), ("rollback", 0),
)
"""Lo que corre una fase sin corte: cada rol del agente una vez y su receta una vez, con
el checklist en verde. Vale para lo que se mide, una caída del supervisor con el proceso
en curso vivo: no justifica repetir nada, porque el modelo concilia el efecto en curso con
su recibo (estado `conciliando`) y no lo relanza. No mide un corte de energía, que mata
también ese proceso y con el que un turno o la receta pueden repetirse una vez."""

AGUANTA = "aguanta una caída del supervisor con el proceso en curso vivo; no mide un corte de energía"


def _y(partes: list[str]) -> str:
    return " y ".join(filter(None, (", ".join(partes[:-1]), partes[-1])))


def _sin_corte(obs: Observacion) -> Juicio | None:
    """Sin el corte que se mide no hay apagón que juzgar: ni sin cortes, ni con un corte
    que mató también al proceso que esperaba la compuerta."""
    a = obs.apagon
    if a is None:
        return "no medido", obs.sin_apagon or "esta corrida no hizo el encargo con cortes"
    if a.pliegue is None:
        return "no medido", f"el encargo con cortes, {a.encargo}, no está en el registro"
    if isinstance(a.pliegue, NoPliega):
        return "no medido", (
            f"el registro de {a.encargo} se rompe en el evento {a.pliegue.evento}"
            f" ({a.pliegue.tipo}): {a.pliegue.excepcion}"
        )
    if a.cortes is None:
        return "no medido", "no pude leer lo que el canario anotó de sus cortes"
    if not a.cortes:
        return "no medido", "el canario no llegó a cortar: ningún proceso esperó su compuerta"
    for c in a.cortes:
        if c.sobrevivio is False:
            return "no medido", (
                f"el corte durante {MOMENTOS[c.momento]} mató también al proceso que esperaba:"
                " no es el corte que se mide"
            )
        if c.sobrevivio is None:
            return "no medido", (
                f"no pude ver si el proceso que esperaba durante {MOMENTOS[c.momento]}"
                " sobrevivió al corte"
            )
    return None


def _cortes(a: Apagon) -> Condicion:
    hechos = {c.momento for c in a.cortes or ()}
    faltan = [texto for momento, texto in MOMENTOS.items() if momento not in hechos]
    if faltan:
        return None, "no hubo corte durante " + " ni durante ".join(faltan)
    return True, (
        f"tiró su supervisor durante {' y durante '.join(MOMENTOS.values())}, con el proceso que"
        f" esperaba vivo: {AGUANTA}"
    )


def _mismo_final(p: Pliegue) -> Condicion:
    """El final de un encargo sin corte: terminado, completo, cada fase en el nivel que el
    motor exige y sin bajar el objetivo."""
    if p.estado != "terminado":
        return False, f"quedó {p.estado}, no terminado"
    if not p.fases:
        return False, "terminó sin fases"
    if not p.completo:
        return False, "quedó como entrega parcial: " + "; ".join(
            f"la fase {f.id} quedó {f.estado}" + (f" ({f.causa})" if f.causa else "")
            for f in p.fases if f.estado != "preparada"
        )
    debajo = [f"la fase {f.id} quedó en {f.nivel}" for f in p.fases if not f.cumplido]
    if debajo:
        return False, "; ".join(debajo) + ", por debajo de lo que el motor exige"
    if bajado := _bajado(p):
        return False, bajado
    fases = "su fase" if len(p.fases) == 1 else f"sus {len(p.fases)} fases"
    return True, (
        f"llegó al mismo final que sin corte: terminado, {fases} en"
        f" {_y(sorted({f.nivel for f in p.fases}))}"
    )


def _producto_previo(a: Apagon) -> Condicion:
    """El responsable escribe su producto antes de esperar la compuerta del turno: lo que
    llega al destino es el trabajo de antes del corte."""
    if a.archivo is None:
        return None, "nadie pidió revisar lo que deja su despliegue"
    ruta, contenido = a.archivo.ruta, a.archivo.contenido
    if contenido == "ilegible":
        return None, f"no pude leer {ruta}"
    if contenido == "no existe":
        return False, f"no existe {ruta}, que deja su despliegue"
    trae = "trae" if contenido == "con la marca" else "no trae"
    return contenido == "con la marca", f"{ruta} {trae} lo que el responsable escribió antes del corte"


def _una_vez(a: Apagon, fases: int) -> Condicion:
    if a.invocaciones is None:
        return None, "no pude leer las invocaciones que anotan su agente y su receta"
    corridas = dict(a.invocaciones)
    if not corridas.get("responsable"):
        return None, "su responsable no anotó ningún turno: no hay qué contar"
    de_mas = [
        f"{quien}: {corridas[quien]} {'vez' if corridas[quien] == 1 else 'veces'} (acepto {n * fases})"
        for quien, n in ACEPTADAS if corridas.get(quien, 0) > n * fases
    ]
    if de_mas:
        return False, "repitió " + "; ".join(de_mas)
    return True, (
        f"sin repetir: {_y([f'{quien} {corridas.get(quien, 0)}' for quien, _ in ACEPTADAS])} veces"
        f" (acepto {_y([str(n * fases) for _, n in ACEPTADAS])})"
    )


def _un_pr_un_merge(a: Apagon, fases: int) -> Condicion:
    if a.prs is None:
        return None, "no pude leer sus PR de GitHub"
    if not a.prs:
        return None, "GitHub no trae ningún PR de sus ramas: no hay qué contar"
    prs, merges = len(a.prs), sum(1 for pr in a.prs if pr.estado == "mergeado")
    de_mas = [
        texto for texto, n in ((f"{prs} PR", prs), (f"{merges} merges", merges)) if n > fases
    ]
    if de_mas:
        return False, "repitió en GitHub: " + "; ".join(f"{texto} (acepto {fases})" for texto in de_mas)
    return True, f"{prs} PR y {merges} merge{'' if merges == 1 else 's'} (acepto {fases} y {fases})"


def _juntar(condiciones: tuple[Condicion, ...]) -> Juicio:
    fallas = [texto for ok, texto in condiciones if ok is False]
    sin_mirar = [texto for ok, texto in condiciones if ok is None]
    cumple = [texto for ok, texto in condiciones if ok]
    if fallas:
        return "falla", "; ".join(fallas) + (f"; sí {'; '.join(cumple)}" if cumple else "")
    if sin_mirar:
        return "no medido", "; ".join(sin_mirar)
    return "pasa", "; ".join(cumple)


def _no_pierde(obs: Observacion, _topes: Topes) -> Juicio:
    """Tras los cortes llega al mismo final que sin ellos y despliega lo que el
    responsable escribió antes del primero."""
    if (fuera := _sin_corte(obs)) is not None:
        return fuera
    a = cast(Apagon, obs.apagon)
    return _juntar((_cortes(a), _mismo_final(cast(Pliegue, a.pliegue)), _producto_previo(a)))


def _no_repite(obs: Observacion, _topes: Topes) -> Juicio:
    """Tras los cortes, cada rol del agente, cada paso de la receta, el PR y el merge corren
    las veces que corren sin corte (`ACEPTADAS`, por fase). Un PR o un merge de más es
    repetir aunque el corte no sea el que se mide."""
    a = obs.apagon
    if a is None or not isinstance(a.pliegue, Pliegue):
        return cast(Juicio, _sin_corte(obs))
    fases = max(len(a.pliegue.fases), 1)
    github = _un_pr_un_merge(a, fases)
    if (fuera := _sin_corte(obs)) is not None:
        return ("falla", f"{github[1]}; {fuera[1]}") if github[0] is False else fuera
    return _juntar((_cortes(a), _una_vez(a, fases), github))


Juez = Callable[[Observacion, Topes], Juicio]

TABLA: tuple[tuple[NombreFila, str, str, Juez], ...] = (
    ("termina", "1", "registro: estado, nivel y horas", _termina),
    ("entregado", "2", "GitHub (gh pr view) y el recibo de cada fase (despliegue.comprobar_recibo)",
     _entregado),
    ("limpio", "2", "la tabla Limpieza del modelo: GitHub, la base, el clon del servicio"
     " y los procesos", _limpio),
    ("no pierde", "3", "el encargo con cortes: registro, lo que el canario anotó de cada corte"
     " y su destino", _no_pierde),
    ("no repite", "3", "el encargo con cortes: las invocaciones que anotan su agente y su receta,"
     " y GitHub (gh pr list de sus ramas)", _no_repite),
    ("cuesta poco", "4", "registro: turnos, revisiones y horas", _cuesta_poco),
    ("se detiene", "5", "el encargo cuyo checklist falla a propósito: registro, sus recibos"
     " de despliegue y su destino", _se_detiene),
    ("versión", "6", "instalacion.json, supervisor-pasada.json y HEAD del árbol", _version),
    ("legible", "todos", "registro: plegado e invariantes de tools/plegado.py", _legible),
)


def juzgar(obs: Observacion, esperado: Esperado) -> tuple[Fila, ...]:
    filas = []
    for nombre, punto, fuente, juez in TABLA:
        resultado, observado = juez(obs, esperado.topes)
        filas.append(
            Fila(nombre, punto, resultado, esperado.filas[nombre], observado, fuente)
        )
    return tuple(filas)


def _sin_medir_en_windows(ruta: Path, datos: dict, nombres: set[str]) -> list[str]:
    """Las filas que en Windows no se miden, y por eso ahí se esperan "no medido"."""
    if "sin_medir_en_windows" not in datos:
        return []
    sin_medir = datos["sin_medir_en_windows"]
    filas = sin_medir.get("filas") if isinstance(sin_medir, dict) else None
    if (
        not isinstance(sin_medir, dict) or set(sin_medir) != {"filas", "razon"}
        or not isinstance(filas, list) or not filas or not all(isinstance(f, str) for f in filas)
        or len(set(filas)) != len(filas)
        or not set(filas) <= nombres
        or not isinstance(sin_medir["razon"], str) or not sin_medir["razon"].strip()
    ):
        raise ValueError(
            f"{ruta}: 'sin_medir_en_windows' lleva 'filas', una lista de filas de la tabla,"
            " y 'razon', por qué en Windows no se miden"
        )
    return filas


def leer_esperado(ruta: Path, windows: bool = os.name == "nt") -> Esperado:
    datos = leer_json(ruta)
    nombres = {nombre for nombre, *_ in TABLA}
    filas = datos.get("filas") if isinstance(datos, dict) else None
    topes = datos.get("topes") if isinstance(datos, dict) else None
    campos = {f.name for f in dataclasses.fields(Topes)}
    if (
        not isinstance(filas, dict)
        or set(filas) != nombres
        or any(v not in get_args(Resultado) for v in filas.values())
    ):
        raise ValueError(
            f"{ruta}: 'filas' debe dar uno de {list(get_args(Resultado))}"
            f" a cada fila: {sorted(nombres)}"
        )
    if (
        not isinstance(topes, dict)
        or set(topes) != campos
        or any(type(v) is not int for v in topes.values())
    ):
        raise ValueError(f"{ruta}: 'topes' debe traer enteros para {sorted(campos)}")
    # Una falla conocida sin su razón escrita se olvida: el archivo no admite comentarios.
    conocidas = sorted(nombre for nombre, valor in filas.items() if valor == "falla")
    razones = datos.get("razones")
    if (
        not isinstance(razones, dict)
        or sorted(razones) != conocidas
        or any(not isinstance(v, str) or not v.strip() for v in razones.values())
    ):
        raise ValueError(f"{ruta}: 'razones' debe explicar cada fila que se espera falla: {conocidas}")
    sin_medir = _sin_medir_en_windows(ruta, datos, nombres)
    if windows:
        filas = {**filas, **dict.fromkeys(sin_medir, "no medido")}
    return Esperado(filas=cast(Mapping[NombreFila, Resultado], filas), topes=Topes(**topes))
