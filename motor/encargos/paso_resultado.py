"""El resultado nuevo que recibe la autoridad: se decide el lote entero antes de escribir.

`ciclo._recibir_resultado_nuevo` observa el plegado, el registro y git, y escribe
lo que devuelve `decidir` con un solo `Registro.append_many`. `decidir` no
escribe: lee el registro solo con `proyectar`, git solo con `region` y el destino
solo con `destino`. Como `proyectar` es la cuenta que hace la escritura, lo que
decide es lo que queda guardado, y un apagón deja el resultado entero o nada.

Las ukeys y los datos son los de los registros ya guardados: un registro viejo
a medias da `igual` donde ya tiene un evento y pliega igual. Cambiar uno rompe
esa igualdad.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from .cierre import componer, comprobar_destino
from .comprobacion import JuicioValidado, validar_juicio
from .dominio import FASE_IMPLICITA, Encargo, Hallazgo, alcance_de, bloqueantes_abiertos
from .frontera import DestinoEnsayo
from .registro import Evento, Rechazo

Manifiesto = tuple[tuple[str, str], ...]
# (huella, línea anclada, lo que la sigue) de la región que un hallazgo ancla en un SHA.
Region = tuple[str, str, list[str] | None]


@dataclass(frozen=True)
class Foto:
    """Lo que el ciclo observó antes de decidir."""

    enc: Encargo
    token: str
    host: str
    observaciones: dict
    juicio: dict
    hora: str
    version: str
    manifiesto: Manifiesto
    fase: str
    huella: str
    # La fase con que se abrió el intento vigente; vacía sin intento o sin evento.
    fase_del_intento: str
    # La respuesta de un intento que ya dejó su comprobación.
    previo: dict | None


@dataclass(frozen=True)
class Paso:
    """Lo que el paso guarda en una transacción, lo que deja fuera y lo que responde.

    `omitidos` son los eventos que el registro rechazaría. El paso sigue sin
    ellos, como sigue un llamador de `anotar`, que devuelve el rechazo sin
    lanzar: meterlos al lote rechazaría el resultado entero."""

    eventos: tuple[Evento, ...]
    omitidos: tuple[tuple[Evento, Rechazo], ...]
    respuesta: dict


@dataclass(frozen=True)
class _Lote:
    proyectar: Callable[[Sequence[Evento]], Encargo]
    estado: Encargo
    eventos: tuple[Evento, ...] = ()
    omitidos: tuple[tuple[Evento, Rechazo], ...] = ()

    def con(self, evento: Evento) -> _Lote:
        try:
            estado = self.proyectar([*self.eventos, evento])
        except Rechazo as rechazo:
            return replace(self, omitidos=(*self.omitidos, (evento, rechazo)))
        return replace(self, estado=estado, eventos=(*self.eventos, evento))

    def paso(self, respuesta: dict) -> Paso:
        return Paso(self.eventos, self.omitidos, respuesta)


def producto_de(observaciones: dict) -> tuple[str, Manifiesto]:
    version = str(observaciones.get("version") or observaciones.get("producto") or "")
    manifiesto = tuple(
        (str(x[0]), str(x[1]))
        for x in (observaciones.get("manifiesto") or ())
        if isinstance(x, (list, tuple)) and len(x) >= 2
    )
    return version, manifiesto


def _excepcion(
    tipo: str, sha: str, hallazgo: str, motivo: str, hora: str, clave: str,
    ancla: str = "", region: str = "", region_ancla: str = "",
    region_contexto: list[str] | None = None,
) -> Evento:
    """Los datos que arma `ciclo._anotar_excepcion`: los dos escriben la misma
    ukey, y `ciclo._descarte_sigue_vigente` lee la región de cualquiera."""
    datos: dict[str, object] = {
        "tipo": tipo,
        "sha": str(sha or ""),
        "id": str(hallazgo or ""),
        "motivo": " ".join(str(motivo or "").split()),
        "hora": hora,
    }
    if ancla:
        datos["ancla"] = str(ancla)
    if region:
        datos["region"] = str(region)
    if region_ancla:
        datos["region_ancla"] = str(region_ancla)
    if region_contexto is not None:
        datos["region_contexto"] = [str(x) for x in region_contexto]
    return Evento("excepcion", datos, clave)


def _crudo(juicio: dict) -> dict:
    crudo = dict(juicio)
    if "hallazgos" in crudo:
        hallazgos = crudo.pop("hallazgos") or []
        crudo["bloqueantes"] = [
            h for h in hallazgos if isinstance(h, dict) and h.get("gravedad") == "bloqueante"
        ]
        crudo["observaciones"] = [
            str(h.get("titulo") or "")
            for h in hallazgos
            if isinstance(h, dict) and h.get("gravedad") != "bloqueante"
        ]
        crudo["resueltos"] = [
            str(h.get("id"))
            for h in hallazgos
            if isinstance(h, dict) and h.get("estado") == "resuelto"
        ]
    return crudo


def _descartar(
    lote: _Lote,
    enc: Encargo,
    foto: Foto,
    validado: JuicioValidado,
    region: Callable[[str, Hallazgo], Region],
) -> tuple[_Lote, list[Hallazgo]]:
    """Los hallazgos de DeepSeek que el revisor descarta o deja fuera del alcance,
    con sus notas y excepciones, y la lista que la revisión guarda."""
    hallazgos = list(validado.hallazgos)
    abiertos_ds = {h.id: h for h in bloqueantes_abiertos(enc, "deepseek")}
    ya_resueltos = {h.id for h in hallazgos}
    marca = validado.evidencia or "descartado por el revisor independiente"
    for hid in validado.resueltos:
        h = abiertos_ds.get(hid)
        if h is None or hid in ya_resueltos:
            continue
        hallazgos.append(replace(h, estado="resuelto", cierre_evidencia=marca))
        lote = lote.con(Evento(
            "nota_pr",
            {
                "texto": (
                    f"hallazgo de DeepSeek descartado por el revisor "
                    f"independiente: {hid} «{h.titulo}» ({marca[:400]})"
                ),
                "hora": foto.hora,
            },
            f"disputa-resuelta-autoridad:{enc.id}:{hid}",
        ))
        huella_region, texto_region, siguiente_region = region(enc.pr_sha, h)
        lote = lote.con(_excepcion(
            "descarte", enc.pr_sha, hid, marca, foto.hora,
            f"excepcion:{enc.id}:descarte:{enc.pr_sha}:{hid}",
            ancla=h.ancla,
            region=huella_region,
            region_ancla=texto_region,
            region_contexto=siguiente_region,
        ))
        ya_resueltos.add(hid)
    for hid, razon in validado.fuera_de_alcance:
        h = abiertos_ds.get(hid)
        if h is None or hid in ya_resueltos:
            continue
        hallazgos.append(
            replace(h, gravedad="observacion", estado="resuelto", cierre_evidencia=razon)
        )
        lote = lote.con(Evento(
            "hallazgo_fuera_de_alcance",
            {"id": hid, "titulo": h.titulo, "razon": razon, "hora": foto.hora},
            f"fuera-alcance:{enc.id}:{foto.fase}:{hid}",
        ))
        lote = lote.con(_excepcion(
            "fuera_alcance", enc.pr_sha, hid, razon, foto.hora,
            f"excepcion:{enc.id}:fuera:{enc.pr_sha}:{hid}",
        ))
        ya_resueltos.add(hid)
    return lote, hallazgos


def _revision(
    foto: Foto, enc: Encargo, validado: JuicioValidado, hallazgos: list[Hallazgo]
) -> Evento:
    return Evento(
        "revision",
        {
            "revision_id": "rev_autoridad_" + foto.version[:12],
            "version": foto.version,
            "base_version": validado.base_version,
            "alcance": validado.alcance,
            "veredicto": validado.veredicto,
            "revisor": "autoridad",
            "origen": "independiente",
            "cubre": list(validado.cubre),
            "hallazgos": [h.como_dict() for h in hallazgos],
            "aprobadas": [list(x) for x in validado.aprobadas],
            "evidencia": validado.evidencia,
            "efectos_pendientes": list(validado.efectos_pendientes),
            "fase": foto.fase,
            "intento": enc.intento,
            "hora": foto.hora,
        },
        f"revision-autoridad:{enc.id}:{foto.fase}:{foto.version}",
    )


def decidir(
    foto: Foto,
    proyectar: Callable[[Sequence[Evento]], Encargo],
    region: Callable[[str, Hallazgo], Region],
    destino: Callable[[], DestinoEnsayo],
) -> Paso:
    """El lote que guarda el resultado y la respuesta que da.

    Un rechazo guarda solo la asociación del conductor con la fase."""
    enc, fase, hora = foto.enc, foto.fase, foto.hora
    asociado = _Lote(proyectar, enc)
    if foto.token:
        asociado = asociado.con(Evento(
            "conductor_fase",
            {"token": foto.token, "fase": fase, "hora": hora},
            f"conductor-fase:{enc.id}:{foto.token}",
        ))

    def rechazo(motivo: str) -> Paso:
        return asociado.paso({"rechazado": True, "motivo": motivo})

    if foto.previo is not None:
        return asociado.paso(foto.previo)
    activa = enc.fase_activa or (FASE_IMPLICITA if not enc.fases else "")
    if fase != activa:
        return rechazo("el resultado pertenece a otra fase")
    if foto.fase_del_intento and foto.fase_del_intento != fase:
        return rechazo("el intento pertenece a otra fase")
    actual = next((f for f in enc.fases if f.id == fase), None)
    if actual is not None and actual.estado == "preparada":
        return rechazo("la fase ya está preparada")
    lote = asociado
    if enc.producto is None or enc.producto.firma != foto.version:
        lote = lote.con(Evento(
            "producto_listo",
            {
                "version": foto.version,
                "manifiesto": [list(x) for x in foto.manifiesto],
                "fase": fase,
                "intento": enc.intento,
                "hora": hora,
            },
            f"producto-autoridad:{enc.id}:{fase}:{foto.version}",
        ))
        enc = lote.estado
    try:
        validado = validar_juicio(
            _crudo(foto.juicio), alcance_de(enc), externos=bloqueantes_abiertos(enc, "deepseek")
        )
    except (ValueError, KeyError, TypeError) as exc:
        return rechazo(str(exc))
    if validado.veredicto != "aprobado":
        return rechazo("el resultado no fue comprobado")
    if any(e.estado in ("intencion", "incierto") for e in enc.efectos):
        return rechazo("la autoridad conserva efectos sin recibo")
    pedidos = list(foto.observaciones.get("solicitudes_efecto") or [])
    if pedidos and set(validado.efectos_pendientes) != {str(p["id"]) for p in pedidos}:
        return rechazo("la aprobacion no enumera los efectos solicitados")
    lote, hallazgos = _descartar(lote, enc, foto, validado, region)
    lote = lote.con(_revision(foto, enc, validado, hallazgos))
    enc = lote.estado
    try:
        cierre = componer(enc, pedidos, validado.evidencia)
        if pedidos:
            comprobar_destino(cierre, destino())
    except (ValueError, KeyError, TypeError) as exc:
        return rechazo(str(exc))
    recibo = f"resultado:{enc.id}:{fase}:{foto.token}"
    lote = lote.con(Evento(
        "comprobacion",
        {
            **cierre,
            "fase": fase,
            "huella_resultado": foto.huella,
            "observaciones": foto.observaciones,
            "host": foto.host,
            "hora": hora,
        },
        recibo,
    ))
    return lote.paso({
        "registrado": any(evento.ukey == recibo for evento in lote.eventos),
        "encargo": enc.id,
        "estado": lote.estado.estado,
        "fase": fase,
        "recibo": recibo,
    })
