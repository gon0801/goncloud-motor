"""Ciclo de vida del encargo. No decide el trabajo de la aplicación."""

from __future__ import annotations

import hashlib
import json
import socket
import uuid
from dataclasses import replace
from pathlib import Path

from .comprobacion import (
    digest,
    firma_producto,
    iniciar_comprobacion,
    instrucciones_responsable,
    instrucciones_revision,
    leer_juicio,
)
from .dominio import (
    Encargo,
    TransicionIlegal,
    decision_fallo,
    decision_proceso,
    huella_entrega,
    id_encargo,
    prompt_de,
    recibo_de,
    reducir,
    vista,
)
from .frontera import (
    DestinoEnsayo,
    activar_parada_local,
    despachar,
    leer_parada_local,
    turno,
)
from .registro import Registro
from .sesion import (
    cerrar,
    crecer_log,
    herramienta_en_curso,
    lanzar_administrado,
    observar,
    preparar_administrado,
    recuperar_arranque,
)

_UMBRAL_S = 30


def ahora_iso(epoch: float | None = None) -> str:
    from datetime import datetime, timezone

    if epoch is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def recibir(
    registro: Registro,
    texto: str,
    clave: str,
    hora: str,
    director: str = "",
    adjuntos: tuple[str, ...] = (),
    restricciones: tuple[str, ...] = (),
    canal: str = "",
    via: str = "durable",
) -> dict:
    huella = huella_entrega(texto, adjuntos, restricciones)
    ukey = f"entrega:{clave}"
    prev = registro.por_ukey(ukey)
    if prev:
        enc = registro.fold(prev["encargo"])
        datos = prev["data"]
        salida = vista(enc, datos["recibo"])
        if (
            datos["huella"] != huella
            or datos.get("via", "durable") != via
            or datos.get("canal", "") != canal
            or datos.get("director", "") != director
        ):
            salida["conflicto"] = True
        return salida
    encargo = id_encargo(clave, huella)
    recibo = recibo_de(ukey)
    registro.append(
        encargo,
        "entrega",
        {
            "id": encargo,
            "clave": clave,
            "huella": huella,
            "texto": texto,
            "director": director,
            "adjuntos": list(adjuntos),
            "restricciones": list(restricciones),
            "canal": canal,
            "via": via,
            "recibo": recibo,
            "hora": hora,
        },
        ukey,
        hora,
    )
    _espejo_entrega(
        registro,
        encargo,
        texto,
        clave,
        hora,
        director,
        restricciones,
        adjuntos,
        canal,
        via,
    )
    return vista(registro.fold(encargo), recibo)


def _recibo_previo(
    registro: Registro, encargo_id: str, ukey: str, accion: str
) -> dict | None:
    prev = registro.por_ukey(ukey)
    if not prev:
        return None
    enc = registro.fold(encargo_id)
    salida = vista(enc, prev["data"]["recibo"])
    if prev["data"].get("accion") != accion or prev["encargo"] != encargo_id:
        salida["conflicto"] = True
    else:
        salida["retransmision"] = True
        if prev["data"].get("resultado") == "rechazada":
            salida["rechazada"] = True
            salida["motivo"] = prev["data"].get("motivo") or ""
    return salida


def ordenar(
    registro: Registro, encargo_id: str, accion: str, clave: str, hora: str
) -> dict:
    with turno(registro.base):
        salida = _ordenar(registro, encargo_id, accion, clave, hora)
        if not salida.get("rechazada") and not salida.get("retransmision"):
            _enviar_orden(registro, encargo_id, accion, clave)
        return salida


def _enviar_orden(registro: Registro, encargo_id: str, accion: str, clave: str) -> bool:
    from .transporte import ConfigAutoridadInvalida, cargar, operar

    try:
        cfg = cargar(registro.base)
    except ConfigAutoridadInvalida:
        return False
    if cfg is None:
        return True
    resp = operar(registro.base, {"op": accion, "encargo": encargo_id, "clave": clave})
    salida = resp.get("salida") or {}
    return bool(
        resp.get("alcanzada")
        and not salida.get("rechazada")
        and not salida.get("error")
    )


def _ordenar(
    registro: Registro, encargo_id: str, accion: str, clave: str, hora: str
) -> dict:
    ukey = f"orden:{clave}"
    previo = _recibo_previo(registro, encargo_id, ukey, accion)
    if previo:
        return previo
    enc = registro.fold(encargo_id)
    if enc is None:
        raise TransicionIlegal("no hay encargo")
    recibo = recibo_de(ukey)
    try:
        reducir(enc, {"kind": "orden", "data": {"accion": accion, "hora": hora}})
    except TransicionIlegal as exc:
        registro.append(
            encargo_id,
            "recibo",
            {
                "accion": accion,
                "recibo": recibo,
                "resultado": "rechazada",
                "motivo": str(exc),
                "hora": hora,
                "encargo": encargo_id,
            },
            ukey,
            hora,
        )
        salida = vista(enc, recibo)
        salida["rechazada"] = True
        salida["motivo"] = str(exc)
        return salida
    resultado = registro.append(
        encargo_id,
        "orden",
        {
            "accion": accion,
            "recibo": recibo,
            "resultado": "aplicada",
            "hora": hora,
            "encargo": encargo_id,
        },
        ukey,
        hora,
    )
    if resultado == "ilegal":
        motivo = "transicion ilegal"
        try:
            reducir(
                registro.fold(encargo_id),
                {"kind": "orden", "data": {"accion": accion, "hora": hora}},
            )
        except TransicionIlegal as exc:
            motivo = str(exc)
        registro.append(
            encargo_id,
            "recibo",
            {
                "accion": accion,
                "recibo": recibo,
                "resultado": "rechazada",
                "motivo": motivo,
                "hora": hora,
                "encargo": encargo_id,
            },
            ukey,
            hora,
        )
        salida = vista(registro.fold(encargo_id), recibo)
        salida["rechazada"] = True
        salida["motivo"] = motivo
        return salida
    if resultado == "ajena":
        salida = vista(registro.fold(encargo_id), recibo)
        salida["conflicto"] = True
        return salida
    return vista(registro.fold(encargo_id), recibo)


def corregir(
    registro: Registro, encargo_id: str, texto: str, clave: str, hora: str
) -> dict:
    ukey = f"ajuste:{clave}"
    prev = registro.por_ukey(ukey)
    enc = registro.fold(encargo_id)
    if enc is None:
        raise TransicionIlegal("no hay encargo")
    if prev:
        salida = vista(registro.fold(encargo_id), prev["data"]["recibo"])
        if prev["data"].get("texto") != texto:
            salida["conflicto"] = True
        else:
            salida["retransmision"] = True
        return salida
    recibo = recibo_de(ukey)
    if enc.estado != "detenido":
        registro.append(
            encargo_id,
            "recibo",
            {
                "accion": "corregir",
                "texto": texto,
                "recibo": recibo,
                "resultado": "rechazada",
                "motivo": "el ajuste espera el estado detenido",
                "hora": hora,
            },
            ukey,
            hora,
        )
        salida = vista(enc, recibo)
        salida["rechazada"] = True
        salida["motivo"] = "el ajuste espera el estado detenido"
        return salida
    registro.append(
        encargo_id,
        "ajuste",
        {"texto": texto, "recibo": recibo, "hora": hora},
        ukey,
        hora,
    )
    return vista(registro.fold(encargo_id), recibo)


def consultar(registro: Registro, encargo_id: str) -> dict:
    enc = registro.fold(encargo_id)
    if enc is None:
        raise TransicionIlegal("no hay encargo")
    return vista(enc)


def firma_espacio(path: Path) -> str:
    return firma_producto(path)


def _directorio(base: Path, encargo_id: str) -> Path:
    raiz = base / "ejecucion" / encargo_id
    raiz.mkdir(parents=True, exist_ok=True)
    actual = raiz / "actual"
    if actual.is_file():
        nombre = actual.read_text(encoding="utf-8").strip()
        if nombre.startswith("intento-") and "/" not in nombre and "\\" not in nombre:
            return raiz / nombre
    return raiz


def _espacio(base: Path, encargo_id: str) -> Path:
    path = base / "espacio" / encargo_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _leer_proc(directorio: Path) -> dict | None:
    path = directorio / "proc.json"
    if not path.exists():
        path = directorio / "wrapper.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _aplicar(
    registro: Registro, encargo_id: str, eventos: list[dict], prefijo: str
) -> None:
    for i, evento in enumerate(eventos):
        datos = evento["data"]
        marca = datos.get("pid") or datos.get("observacion") or datos.get("firma") or i
        registro.anotar(
            encargo_id,
            evento,
            f"{prefijo}:{evento['kind']}:{marca}:{datos.get('inicio') or ''}",
        )


def barrer(
    registro: Registro,
    hora: str,
    epoch: float,
    lanzador,
    umbral_s: float = _UMBRAL_S,
) -> list[dict]:
    """Un paso. `lanzador(enc, prompt, espacio, sesion) -> argv` o None si no hay agente."""
    informes = []
    base = registro.base
    parada = leer_parada_local(base) is not None
    for encargo_id in registro.ids():
        enc = registro.fold(encargo_id)
        if enc is None:
            continue
        if (
            enc.estado == "esperando_reintento"
            and enc.reanuda_en
            and hora >= enc.reanuda_en
        ):
            registro.anotar(
                encargo_id,
                {"kind": "plazo", "data": {"hora": hora}},
                f"plazo:{encargo_id}:{enc.reanuda_en}",
            )
            enc = registro.fold(encargo_id)
        cambio = decision_fallo(enc)
        if (
            cambio
            and registro.por_ukey(f"estrategia:{encargo_id}:{enc.firma_fallo}") is None
        ):
            registro.anotar(
                encargo_id, cambio, f"estrategia:{encargo_id}:{enc.firma_fallo}"
            )
            enc = registro.fold(encargo_id)
        enc = _mirar_autoridad(registro, enc, hora)
        directorio = _directorio(base, encargo_id)
        if enc.reserva and enc.candidato:
            directorio = (
                base
                / "ejecucion"
                / enc.id
                / f"intento-{enc.generacion}-{enc.candidato}"
            )
        espacio = _espacio(base, encargo_id)
        proc = enc.proc.como_dict() if enc.proc else _leer_proc(directorio)
        if enc.reserva and enc.proc is None:
            arbol, recuperado = recuperar_arranque(directorio)
            proc = recuperado or proc
            if arbol == "ausente" and (
                _soltar_conductor(registro, enc, hora)
                or enc.estado in ("deteniendo", "detenido", "cancelado")
                or parada
            ):
                registro.anotar(
                    encargo_id,
                    {"kind": "reserva_cerrada", "data": {"hora": hora}},
                    f"reserva-cerrada:{encargo_id}:{enc.generacion}",
                )
                enc = registro.fold(encargo_id)
        else:
            arbol = observar(directorio, proc)
        if enc.host and enc.host != socket.gethostname() and (enc.reserva or enc.proc):
            arbol = "incierto"
        observacion_path = directorio / "herramienta.json"
        if enc.proc and observacion_path.is_file():
            try:
                obs = json.loads(observacion_path.read_text(encoding="utf-8"))
                secuencia = int(obs.get("secuencia") or 0)
            except (OSError, ValueError, TypeError):
                secuencia = 0
            if secuencia:
                registro.anotar(
                    encargo_id,
                    {
                        "kind": "actividad",
                        "data": {
                            "observada": True,
                            "hecho": obs.get("hecho") or "herramienta observada",
                            "hora": ahora_iso(obs["hora"]),
                        },
                    },
                    f"herramienta:{encargo_id}:{enc.generacion}:{secuencia}",
                )
                enc = registro.fold(encargo_id)
        firma = firma_espacio(espacio)
        firma_previa = ""
        for ev in reversed(registro.eventos(encargo_id)):
            if ev["data"].get("firma"):
                firma_previa = ev["data"]["firma"]
                break
        if (
            firma
            and firma != firma_previa
            and enc.proc
            and enc.intentos_nuevos
            and enc.estado not in ("deteniendo", "detenido", "cancelado", "terminado")
        ):
            registro.anotar(
                encargo_id,
                {
                    "kind": "actividad",
                    "data": {
                        "observada": True,
                        "hecho": "cambió el espacio de trabajo",
                        "firma": firma,
                        "hora": hora,
                    },
                },
                f"actividad:{encargo_id}:{firma[:16]}",
            )
            enc = registro.fold(encargo_id)
        if (
            arbol == "ausente"
            and (
                enc.proc is not None
                or (
                    (directorio / "outcome.json").exists()
                    and not (directorio / "incorporado").exists()
                )
            )
            and enc.intentos_nuevos
            and not parada
            and enc.estado
            not in (
                "deteniendo",
                "detenido",
                "cancelado",
                "terminado",
                "esperando_reintento",
                "necesita_usuario",
            )
        ):
            enc = _incorporar(registro, enc, espacio, hora, directorio)
            arbol = observar(directorio, proc)
        if arbol == "ausente" and enc.proc is not None:
            _soltar_conductor(registro, enc, hora)
        if enc.proc is not None and arbol == "ausente":
            registro.anotar(
                encargo_id,
                {"kind": "intento_cerrado", "data": {"hora": hora}},
                f"intento-cerrado:{encargo_id}:{enc.proc.pid}:{enc.proc.inicio}",
            )
            enc = registro.fold(encargo_id)
        sin_hecho = _edad(enc, epoch) >= umbral_s
        decision = decision_proceso(
            enc,
            arbol,
            herramienta_en_curso(directorio),
            sin_hecho,
            enc.herramientas_observables,
            hora,
        )
        if parada:
            decision["lanzar"] = False
            decision["eventos"] = [
                ev for ev in decision["eventos"] if ev["kind"] == "arbol"
            ]
            if arbol == "vivo":
                decision["matar"] = True
        if decision["eventos"]:
            _aplicar(
                registro,
                encargo_id,
                decision["eventos"],
                f"paso:{encargo_id}:{hora}:{arbol}",
            )
            enc = registro.fold(encargo_id)
        if decision["matar"] and proc:
            cerrar(directorio, proc)
        if decision["lanzar"] and lanzador is not None and not parada:
            _lanzar_uno(registro, enc, directorio, espacio, hora, lanzador)
            enc = registro.fold(encargo_id)
        informes.append(vista(enc))
    return informes


def _edad(enc: Encargo, epoch: float) -> float:
    if not enc.hora:
        return 1e9
    from datetime import datetime

    try:
        visto = datetime.fromisoformat(enc.hora.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 1e9
    return epoch - visto


def _pedidos_efecto(espacio: Path) -> list[dict]:
    path = espacio / "pedido-efecto.json"
    if not path.exists():
        return []
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"pedido-efecto.json no se puede leer: {exc}") from exc
    if isinstance(datos, dict):
        datos = [datos]
    if not isinstance(datos, list):
        raise TypeError("pedido-efecto.json debe contener un objeto o una lista")
    campos = ("id", "operacion", "clave", "cuerpo")
    ids = set()
    pedidos = []
    for indice, item in enumerate(datos):
        if not isinstance(item, dict):
            raise TypeError(f"pedido-efecto.json[{indice}] debe ser un objeto")
        invalidos = [
            k for k in campos if not isinstance(item.get(k), str) or not item[k].strip()
        ]
        if invalidos:
            raise ValueError(
                f"pedido-efecto.json[{indice}] necesita texto no vacio en: "
                + ", ".join(invalidos)
                + "; conserva la identidad autorizada y expresa el texto en cuerpo"
            )
        if item["id"] in ids:
            raise ValueError(f"pedido-efecto.json repite el id {item['id']}")
        ids.add(item["id"])
        pedidos.append({k: item[k] for k in campos})
    return pedidos


def _anotar_juicio(
    registro: Registro, enc: Encargo, juicio: dict, hora: str, serie: int
) -> None:
    registro.anotar(
        enc.id,
        {"kind": "comprobacion", "data": {**juicio, "hora": hora}},
        f"comprobacion:{enc.id}:{juicio['clase']}:{juicio.get('firma') or juicio.get('motivo')}:{serie}",
    )


def _efecto_asentado(resultado: dict) -> bool:
    if resultado.get("ya_aplicado") or resultado.get("conciliado"):
        return True
    return bool(
        resultado.get("recibo")
        and not resultado.get("incierto")
        and not resultado.get("rechazado")
        and not resultado.get("intencion")
    )


def _incorporar(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    hora: str,
    directorio: Path | None = None,
) -> Encargo:
    from motor.runwrap import _publica

    directorio = directorio or _directorio(registro.base, enc.id)
    outcome_path = directorio / "outcome.json"
    if outcome_path.is_file():
        outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        spec = (
            json.loads((directorio / "arranque.json").read_text(encoding="utf-8"))
            if (directorio / "arranque.json").is_file()
            else {}
        )
        agente = spec.get("agente") or enc.agente or "externo"
        if outcome.get("fallo_runtime"):
            from .rutas import fallo

            seguidas = 1
            for evento in reversed(registro.eventos(enc.id)):
                if evento["data"].get("agente") != agente:
                    continue
                if evento["kind"] == "proveedor_disponible":
                    break
                if evento["kind"] == "proveedor_fallo":
                    seguidas += 1
            causa = fallo(agente, outcome["fallo_runtime"], hora, seguidas)
            registro.anotar(
                enc.id,
                {"kind": "proveedor_fallo", "data": {**causa, "hora": hora}},
                f"proveedor-fallo:{enc.id}:{enc.generacion}",
            )
            if not enc.agentes:
                marcar_espera(
                    registro,
                    enc.id,
                    "runtime del agente: " + causa["causa"],
                    causa["hasta"],
                    hora,
                )
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id)
        if any(p.agente == agente for p in enc.pausas):
            registro.anotar(
                enc.id,
                {
                    "kind": "proveedor_disponible",
                    "data": {"agente": agente, "hora": hora},
                },
                f"proveedor-disponible:{enc.id}:{enc.generacion}",
            )
            enc = registro.fold(enc.id)
    if enc.rol != "revisor":
        path = directorio / "observaciones.json"
        if not path.is_file():
            iniciar_comprobacion(espacio, directorio)
            return enc
        observacion = json.loads(path.read_text(encoding="utf-8"))
        huella = digest(observacion)
        _publica(
            directorio / "observaciones.json",
            json.dumps(observacion, ensure_ascii=False),
        )
        registro.anotar(
            enc.id,
            {
                "kind": "procedimiento",
                "data": {
                    "nota": "observaciones del supervisor "
                    + str(directorio / "observaciones.json"),
                    "hora": hora,
                },
            },
            f"observaciones:{enc.id}:{enc.generacion}:{huella}",
        )
        registro.anotar(
            enc.id,
            {
                "kind": "estrategia",
                "data": {
                    "estrategia": "revisar_evidencia",
                    "rol": "revisor",
                    "motivo": "comprobaciones ejecutadas; falta relacionarlas con el pedido completo",
                    "siguiente": "inspeccionar producto y observaciones del supervisor",
                    "hora": hora,
                },
            },
            f"revision:{enc.id}:{enc.generacion}",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    referencia = json.loads((directorio / "revision.json").read_text(encoding="utf-8"))
    observacion = json.loads(
        Path(referencia["observaciones"]).read_text(encoding="utf-8")
    )
    huella = digest(observacion)
    juicio = leer_juicio(Path(referencia["resultado"]), observacion, huella, espacio)
    try:
        pedidos = _pedidos_efecto(espacio)
        error_pedidos = ""
    except (ValueError, TypeError) as exc:
        pedidos = None
        error_pedidos = str(exc)
    if juicio["clase"] == "confirmado" and pedidos:
        from .cierre import componer

        try:
            componer(juicio, observacion, pedidos, enc)
        except ValueError:
            juicio = {
                "clase": "instrumento",
                "firma": huella,
                "causa": "confirmacion antes de comprobar efectos mediados pendientes",
                "reparacion": "aprobar explicitamente el producto y enumerar los efectos pendientes sin afirmar que ya ocurrieron",
            }
    if juicio["clase"] in ("confirmado", "producto_aprobado"):
        if pedidos is None:
            juicio = {
                "clase": "instrumento",
                "firma": huella,
                "causa": error_pedidos,
                "reparacion": "corregir id, operacion, clave y cuerpo de la solicitud interna; no obtener recibos desde el trabajador",
            }
        else:
            if juicio["clase"] == "producto_aprobado":
                _anotar_juicio(registro, enc, juicio, hora, enc.generacion)
            if pedidos and not enc.autoridad:
                from datetime import datetime, timedelta, timezone

                reanuda = (datetime.now(timezone.utc) + timedelta(seconds=30)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                marcar_espera(
                    registro,
                    enc.id,
                    "resultado comprobado; falta autoridad para conciliar efectos",
                    reanuda,
                    hora,
                )
                return registro.fold(enc.id)
            destino = DestinoEnsayo(registro.base / "destino")
            for pedido in pedidos:
                resultado = despachar(
                    registro,
                    enc.id,
                    destino,
                    str(pedido["id"]),
                    str(pedido.get("operacion") or "publicar"),
                    str(pedido.get("clave") or pedido["id"]),
                    str(pedido.get("cuerpo") or ""),
                    hora,
                )
                if not _efecto_asentado(resultado):
                    if resultado.get("incierto") or resultado.get("enviado") is False:
                        from datetime import datetime, timedelta, timezone

                        reanuda = (
                            datetime.now(timezone.utc) + timedelta(seconds=30)
                        ).strftime("%Y-%m-%dT%H:%M:%SZ")
                        marcar_espera(
                            registro,
                            enc.id,
                            "resultado comprobado; conciliar efecto sin recibo antes de publicar",
                            reanuda,
                            hora,
                        )
                        return registro.fold(enc.id)
                    juicio = {
                        "clase": "instrumento",
                        "firma": huella,
                        "causa": resultado.get("motivo")
                        or "efecto sin recibo confirmado",
                        "reparacion": "conciliar efecto antes de completar la entrega",
                    }
                    break
    if juicio["clase"] in ("confirmado", "producto_aprobado") and pedidos:
        from .cierre import componer

        juicio = componer(juicio, observacion, pedidos, registro.fold(enc.id))
        _publica(directorio / "cierre.json", json.dumps(juicio, ensure_ascii=False))
    _anotar_juicio(registro, enc, juicio, hora, enc.generacion)
    enc = registro.fold(enc.id)
    if enc.estado == "terminado":
        _publicar_resultado(registro, enc, hora)
    if juicio["clase"] not in ("confirmado", "cierre_comprobado"):
        registro.anotar(
            enc.id,
            {
                "kind": "procedimiento",
                "data": {
                    "nota": juicio.get("reparacion") or juicio.get("motivo"),
                    "hora": hora,
                },
            },
            f"reparacion:{enc.id}:{enc.generacion}",
        )
        registro.anotar(
            enc.id,
            {
                "kind": "estrategia",
                "data": {
                    "estrategia": "reparar_procedimiento"
                    if juicio["clase"] == "instrumento"
                    else "investigar",
                    "rol": "responsable",
                    "motivo": juicio.get("causa") or juicio.get("motivo"),
                    "siguiente": juicio.get("reparacion") or juicio.get("motivo"),
                    "hora": hora,
                },
            },
            f"devolucion:{enc.id}:{enc.generacion}",
        )
    _publica(directorio / "incorporado", hora)
    return registro.fold(enc.id)


def _lanzar_uno(
    registro, enc: Encargo, directorio: Path, espacio: Path, hora: str, lanzador
) -> None:
    from motor.runwrap import _publica

    with turno(registro.base):
        enc = registro.fold(enc.id) or enc
        if (
            enc.via == "directa"
            or enc.proc is not None
            or enc.reserva
            or leer_parada_local(registro.base)
        ):
            return
        rol = enc.rol or "responsable"
        generacion = enc.generacion + 1
        token = uuid.uuid4().hex
        enc = replace(
            enc,
            rol=rol,
            generacion=generacion,
            conductor=token,
            host=socket.gethostname(),
        )
        raiz = registro.base / "ejecucion" / enc.id
        directorio = raiz / f"intento-{generacion}-{token}"
        directorio.mkdir(parents=True, exist_ok=True)
        runtime = registro.base / "runtime" / enc.id / token
        runtime.mkdir(parents=True, exist_ok=True)
        sesion = str(uuid.uuid5(uuid.NAMESPACE_URL, enc.id + ":" + rol))
        paquete = registro.base / "politica" / "poteto" / "SKILL.md"
        skill = (
            paquete
            if paquete.is_file()
            else Path.home() / ".agents" / "skills" / "poteto-mode" / "SKILL.md"
        )
        contenido = prompt_de(enc)
        if skill.is_file():
            local_skill = runtime / "poteto-mode.md"
            _publica(local_skill, skill.read_text(encoding="utf-8"))
            contenido += (
                "\n\nLee poteto-mode en "
                + str(local_skill)
                + ". Adapta a herramientas nativas. Las instrucciones del usuario prevalecen. "
                "Este encargo usa un unico agente sin delegacion ni Cursor. "
                "No apliques cantidades fijas, nuevos gates ni pruebas fuera de los casos autorizados."
            )
        if rol == "revisor":
            observaciones = sorted(
                raiz.glob("intento-*/observaciones.json"),
                key=lambda p: p.stat().st_mtime,
            )
            if observaciones:
                origen = observaciones[-1]
                observacion = json.loads(origen.read_text(encoding="utf-8"))
            else:
                origen = directorio / "observaciones.json"
                observacion = {
                    "producto": firma_producto(espacio),
                    "ejecuciones": [],
                    "error_instrumento": "falta observacion del supervisor",
                }
                _publica(origen, json.dumps(observacion))
            observacion.pop("error_solicitudes_efecto", None)
            try:
                observacion["solicitudes_efecto"] = _pedidos_efecto(espacio)
            except (ValueError, TypeError) as exc:
                observacion["solicitudes_efecto"] = None
                observacion["error_solicitudes_efecto"] = str(exc)
            origen = directorio / "observaciones-revision.json"
            _publica(origen, json.dumps(observacion, ensure_ascii=False))
            resultado = runtime / "resultado.json"
            _publica(
                directorio / "revision.json",
                json.dumps({"observaciones": str(origen), "resultado": str(resultado)}),
            )
            contenido += "\n\n" + instrucciones_revision(
                observacion, digest(observacion), resultado
            )
        else:
            contenido += "\n\n" + instrucciones_responsable()
        prompt = runtime / "prompt.md"
        _publica(prompt, contenido)
        flota_generacion = ""
        flota_ruta = ""
        home_path = None
        flota_reservada = False

        def _liberar_flota() -> None:
            if flota_reservada and home_path is not None:
                try:
                    from motor.flota.control import liberar_trabajo

                    liberar_trabajo(home_path, enc.id)
                except Exception:
                    pass

        try:
            flota_home_path = registro.base / "flota-home.json"
            if flota_home_path.is_file():
                flota_doc = json.loads(flota_home_path.read_text(encoding="utf-8"))
                home_flota = str(flota_doc.get("home") or "")
                if home_flota:
                    from motor.flota.control import _cerrojo_flota
                    from motor.flota.control import anotar_trabajo as _anotar_flota
                    from motor.flota.control import asignar as _asignar_flota

                    home_path = Path(home_flota)
                    if not home_path.is_absolute():
                        home_path = registro.base / home_path
                    flota_generacion = _asignar_flota(home_path)
                    while True:
                        with _cerrojo_flota(home_path):
                            flota_generacion = _asignar_flota(home_path)
                            flota_ruta = str(
                                home_path / "generaciones" / flota_generacion
                            )
                            _publica(
                                directorio / "generacion.json",
                                json.dumps(
                                    {
                                        "generacion": flota_generacion,
                                        "ruta": flota_ruta,
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                            _anotar_flota(
                                home_path,
                                enc.id,
                                flota_generacion,
                                enc.agente or rol,
                                {"reserva": True, "directorio": str(directorio)},
                            )
                            flota_reservada = True
                            if _asignar_flota(home_path) == flota_generacion:
                                break
        except Exception as exc:
            _liberar_flota()
            raise RuntimeError("no se pudo reservar la generacion de flota") from exc
        try:
            cmd = lanzador(enc, prompt, espacio, sesion)
        except Exception:
            _liberar_flota()
            raise
        if not cmd:
            _liberar_flota()
            return
        entorno_gen = (
            {"MOTOR_GENERACION": flota_generacion, "MOTOR_GENERACION_RUTA": flota_ruta}
            if flota_generacion
            else None
        )
        try:
            preparar_administrado(
                cmd, espacio, directorio, enc, sesion, hora, runtime, entorno_gen
            )
            reservado = registro.anotar(
                enc.id,
                {
                    "kind": "reserva",
                    "data": {
                        "rol": rol,
                        "generacion": generacion,
                        "candidato": token,
                        "host": socket.gethostname(),
                        "hora": hora,
                    },
                },
                f"reserva:{enc.id}:{generacion}",
            )
            if reservado != "nuevo":
                _liberar_flota()
                return
            if not _conseguir_conductor(registro, enc, token, hora):
                from datetime import datetime, timedelta, timezone

                reanuda = (datetime.now(timezone.utc) + timedelta(seconds=30)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                marcar_espera(
                    registro,
                    enc.id,
                    "la autoridad no concedio otro conductor",
                    reanuda,
                    hora,
                )
                _liberar_flota()
                return
            registro.anotar(
                enc.id,
                {
                    "kind": "conductor",
                    "data": {"token": token, "host": socket.gethostname(), "hora": hora},
                },
                f"conductor-local:{enc.id}:{token}",
            )
            _publica(raiz / "actual", directorio.name)
            from motor import plataforma

            plataforma.sincroniza_directorio(raiz)
            proc = lanzar_administrado(directorio, token)
        except Exception:
            _liberar_flota()
            raise
        if flota_generacion:
            try:
                from motor.flota.control import anotar_trabajo as _anotar_flota

                _anotar_flota(
                    home_path,
                    enc.id,
                    flota_generacion,
                    enc.agente or rol,
                    {**proc, "directorio": str(directorio)},
                )
            except Exception:
                pass


def marcar_espera(
    registro: Registro, encargo_id: str, motivo: str, reanuda_en: str, hora: str
) -> dict:
    registro.anotar(
        encargo_id,
        {
            "kind": "espera",
            "data": {"motivo": motivo, "reanuda_en": reanuda_en, "hora": hora},
        },
        f"espera:{encargo_id}:{reanuda_en}:{motivo}",
    )
    return vista(registro.fold(encargo_id))


def marcar_pregunta(
    registro: Registro, encargo_id: str, texto: str, bloquea: bool, hora: str
) -> dict:
    registro.anotar(
        encargo_id,
        {
            "kind": "pregunta",
            "data": {"texto": texto, "bloquea_todo": bloquea, "hora": hora},
        },
        f"pregunta:{encargo_id}:{hashlib.sha256(texto.encode()).hexdigest()[:12]}",
    )
    return vista(registro.fold(encargo_id))


def afirmar(registro: Registro, encargo_id: str, texto: str, hora: str) -> dict:
    registro.anotar(
        encargo_id,
        {"kind": "afirmacion", "data": {"texto": texto, "hora": hora}},
        f"afirmacion:{encargo_id}:{hashlib.sha256(texto.encode()).hexdigest()[:12]}",
    )
    return vista(registro.fold(encargo_id))


def parada_local(base: Path, clave: str, hora: str) -> dict:
    return activar_parada_local(base, clave, hora)


def crecer_log_intento(base: Path, encargo_id: str, texto: str) -> None:
    crecer_log(_directorio(base, encargo_id), texto)


def _marcar_autoridad(
    registro: Registro, encargo_id: str, alcanzable: bool, hora: str, marca: str
) -> None:
    registro.anotar(
        encargo_id,
        {"kind": "autoridad", "data": {"alcanzable": alcanzable, "hora": hora}},
        f"autoridad:{encargo_id}:{marca}:{int(alcanzable)}:{hora}",
    )


def _espejo_entrega(
    registro: Registro,
    encargo_id: str,
    texto: str,
    clave: str,
    hora: str,
    director: str,
    restricciones: tuple[str, ...],
    adjuntos: tuple[str, ...] = (),
    canal: str = "",
    via: str = "durable",
) -> None:
    from .transporte import cargar, operar

    if cargar(registro.base) is None:
        return
    resp = operar(
        registro.base,
        {
            "op": "encargar",
            "clave": clave,
            "texto": texto,
            "director": director or "ejecutor",
            "restricciones": list(restricciones),
            "adjuntos": list(adjuntos),
            "canal": canal,
            "via": via,
        },
    )
    _marcar_autoridad(
        registro, encargo_id, bool(resp.get("alcanzada")), hora, "entrega"
    )


def _mirar_autoridad(registro: Registro, enc: Encargo, hora: str) -> Encargo:
    from .transporte import ConfigAutoridadInvalida, cargar, operar

    try:
        cfg = cargar(registro.base)
    except ConfigAutoridadInvalida:
        cfg = {}
    if cfg is None:
        return enc
    if enc.estado in ("deteniendo", "detenido", "cancelado"):
        for evento in reversed(registro.eventos(enc.id)):
            if evento["kind"] == "orden" and evento["data"]["accion"] in (
                "parar",
                "cancelar",
            ):
                clave = evento["data"]["recibo"] + "-canonico"
                _enviar_orden(registro, enc.id, evento["data"]["accion"], clave)
                break
    resp = operar(registro.base, {"op": "ver", "encargo": enc.id})
    if not resp.get("alcanzada"):
        if enc.autoridad:
            _marcar_autoridad(registro, enc.id, False, hora, "caida")
            return registro.fold(enc.id) or enc
        return enc
    if enc.proc is None and not enc.reserva and enc.conductor:
        directorio = _directorio(registro.base, enc.id)
        if observar(directorio, _leer_proc(directorio)) == "ausente":
            _soltar_conductor(registro, enc, hora)
    if not enc.autoridad:
        _marcar_autoridad(registro, enc.id, True, hora, "vuelve")
        enc = registro.fold(enc.id) or enc
    estado = str((resp.get("salida") or {}).get("estado") or "")
    if enc.estado == "terminado" and estado != "terminado":
        _publicar_resultado(registro, enc, hora)
    if enc.estado == "recibido" and estado == "detenido":
        for evento in reversed(registro.eventos(enc.id)):
            if evento["kind"] == "orden":
                if evento["data"]["accion"] == "continuar":
                    _enviar_orden(
                        registro,
                        enc.id,
                        "continuar",
                        evento["data"]["recibo"] + "-canonico",
                    )
                    return enc
                break
    if estado in ("deteniendo", "detenido", "cancelado") and enc.estado not in (
        "deteniendo",
        "detenido",
        "cancelado",
        "terminado",
    ):
        ordenar(registro, enc.id, "parar", f"autoridad-{enc.id}", hora)
        enc = registro.fold(enc.id) or enc
    return enc


def _conseguir_conductor(
    registro: Registro, enc: Encargo, token: str, hora: str
) -> bool:
    from .transporte import ConfigAutoridadInvalida, cargar, operar

    try:
        cfg = cargar(registro.base)
    except ConfigAutoridadInvalida:
        return False
    if cfg is None:
        return True
    resp = operar(
        registro.base,
        {
            "op": "conceder",
            "encargo": enc.id,
            "token": token,
            "host": socket.gethostname(),
        },
    )
    return bool(resp.get("alcanzada") and (resp.get("salida") or {}).get("concedido"))


def _avisar_intento(registro: Registro, encargo_id: str, proc: dict, hora: str) -> bool:
    from .transporte import ConfigAutoridadInvalida, cargar, operar

    try:
        cfg = cargar(registro.base)
    except ConfigAutoridadInvalida:
        return False
    if cfg is None:
        return True
    resp = operar(registro.base, {"op": "intento", "encargo": encargo_id, **proc})
    salida = resp.get("salida") or {}
    if not resp.get("alcanzada") or salida.get("conflicto") or salida.get("error"):
        _marcar_autoridad(registro, encargo_id, False, hora, "intento")
        return False
    return True


def conceder_conductor(
    registro: Registro, encargo_id: str, token: str, host: str, hora: str
) -> dict:
    with turno(registro.base):
        enc = registro.fold(encargo_id)
        if enc is None or not token or not host:
            raise TransicionIlegal("falta identidad del conductor")
        if enc.reserva or enc.proc:
            return {
                "concedido": enc.conductor == token and enc.host == host,
                "conflicto": enc.conductor != token or enc.host != host,
            }
        res = registro.anotar(
            enc.id,
            {
                "kind": "reserva",
                "data": {
                    "conductor": token,
                    "host": host,
                    "hora": hora,
                    "generacion": enc.generacion + 1,
                },
            },
            f"conductor:{enc.id}:{token}",
        )
        return {"concedido": res in ("nuevo", "igual")}


def _soltar_conductor(registro: Registro, enc: Encargo, hora: str) -> bool:
    from .transporte import ConfigAutoridadInvalida, cargar, operar

    try:
        cfg = cargar(registro.base)
    except ConfigAutoridadInvalida:
        return False
    if cfg is None:
        return True
    token = enc.candidato or enc.conductor
    if not token:
        return True
    directorio = _directorio(registro.base, enc.id)
    pendiente = enc.estado == "terminado"
    if (
        not enc.candidato
        and enc.estado not in ("deteniendo", "detenido", "cancelado")
        and not (directorio / "incorporado").is_file()
    ):
        try:
            ref = json.loads((directorio / "revision.json").read_text(encoding="utf-8"))
            juicio = json.loads(Path(ref["resultado"]).read_text(encoding="utf-8"))
            pendiente = pendiente or juicio.get("clase") in (
                "confirmado",
                "producto_aprobado",
            )
        except (OSError, ValueError, KeyError):
            pass
    if pendiente and not enc.candidato:
        resp = operar(registro.base, {"op": "ver", "encargo": enc.id})
        if (
            not resp.get("alcanzada")
            or (resp.get("salida") or {}).get("estado") != "terminado"
        ):
            return False
    if enc.estado in ("deteniendo", "detenido", "cancelado"):
        for evento in reversed(registro.eventos(enc.id)):
            if evento["kind"] == "orden" and evento["data"]["accion"] in (
                "parar",
                "cancelar",
            ):
                if not _enviar_orden(
                    registro,
                    enc.id,
                    evento["data"]["accion"],
                    evento["data"]["recibo"] + "-canonico",
                ):
                    return False
                break
    resp = operar(
        registro.base,
        {
            "op": "cerrar-conductor",
            "encargo": enc.id,
            "token": token,
            "host": socket.gethostname(),
        },
    )
    salida = resp.get("salida") or {}
    return bool(
        resp.get("alcanzada") and (salida.get("cerrado") or salida.get("conflicto"))
    )


def cerrar_conductor(
    registro: Registro, encargo_id: str, token: str, host: str, hora: str
) -> dict:
    with turno(registro.base):
        enc = registro.fold(encargo_id)
        if enc is None or enc.conductor != token or enc.host != host:
            return {"conflicto": True}
        registro.anotar(
            enc.id,
            {"kind": "intento_cerrado", "data": {"hora": hora}},
            f"conductor-cerrado:{enc.id}:{token}",
        )
        if enc.estado == "deteniendo":
            registro.anotar(
                enc.id,
                {"kind": "arbol", "data": {"observacion": "ausente", "hora": hora}},
                f"conductor-ausente:{enc.id}:{token}",
            )
        return {"cerrado": True}


def observar_intento(
    registro: Registro,
    encargo_id: str,
    pid: int,
    inicio: str,
    hora: str,
    boot_id: str = "",
    job: str = "",
    sesion: str = "",
    token: str = "",
    host: str = "",
) -> dict:
    with turno(registro.base):
        enc = registro.fold(encargo_id)
        if enc is None:
            raise TransicionIlegal("no hay encargo")
        if not token or enc.conductor != token or enc.host != host:
            return {"conflicto": True, "motivo": "conductor no concedido"}
        if enc.proc:
            ident = enc.proc
            mismo = (
                ident.pid == pid
                and ident.inicio == inicio
                and ident.boot_id == boot_id
                and ident.host == host
                and ident.job == job
            )
            return {"ya": mismo, "conflicto": not mismo, "encargo": encargo_id}
        resultado = registro.anotar(
            encargo_id,
            {
                "kind": "intento_abierto",
                "data": {
                    "id": token,
                    "pid": pid,
                    "inicio": inicio,
                    "boot_id": boot_id,
                    "job": job,
                    "host": host,
                    "sesion": sesion,
                    "hora": hora,
                },
            },
            f"intento:{encargo_id}:{token}",
        )
        if resultado == "ilegal":
            raise TransicionIlegal("no hay autorización para otro intento")
        return vista(registro.fold(encargo_id))


def _publicar_resultado(registro: Registro, enc: Encargo, hora: str) -> bool:
    from .transporte import ConfigAutoridadInvalida, cargar, operar

    try:
        cfg = cargar(registro.base)
    except ConfigAutoridadInvalida:
        return False
    if cfg is None:
        return True
    d = _directorio(registro.base, enc.id)
    try:
        referencia = json.loads((d / "revision.json").read_text(encoding="utf-8"))
        observacion = json.loads(
            Path(referencia["observaciones"]).read_text(encoding="utf-8")
        )
        juicio = json.loads(Path(referencia["resultado"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return False
    resp = operar(
        registro.base,
        {
            "op": "resultado",
            "encargo": enc.id,
            "token": enc.conductor,
            "host": enc.host,
            "pedido": enc.huella,
            "observaciones": observacion,
            "juicio": juicio,
        },
    )
    registrado = bool(
        resp.get("alcanzada") and (resp.get("salida") or {}).get("registrado")
    )
    if (
        registrado
        and observar(d, enc.proc.como_dict() if enc.proc else _leer_proc(d))
        == "ausente"
    ):
        _soltar_conductor(registro, enc, hora)
    return registrado


def recibir_resultado(
    registro: Registro,
    encargo_id: str,
    token: str,
    host: str,
    pedido: str,
    observaciones: dict,
    juicio: dict,
    hora: str,
) -> dict:
    from .comprobacion import validar_juicio

    with turno(registro.base):
        enc = registro.fold(encargo_id)
        if (
            enc is None
            or enc.conductor != token
            or enc.host != host
            or enc.huella != pedido
        ):
            return {"conflicto": True, "motivo": "resultado de otro conductor o pedido"}
        try:
            validar_juicio(juicio, observaciones, digest(observaciones))
        except (ValueError, KeyError, TypeError) as exc:
            return {"rechazado": True, "motivo": str(exc)}
        if juicio["clase"] not in ("confirmado", "producto_aprobado"):
            return {"rechazado": True, "motivo": "el resultado no fue comprobado"}
        if any(e.estado in ("intencion", "incierto") for e in enc.efectos):
            return {
                "rechazado": True,
                "motivo": "la autoridad conserva efectos sin recibo",
            }
        cierre = juicio
        if juicio["clase"] == "producto_aprobado" or observaciones.get(
            "solicitudes_efecto"
        ):
            from .cierre import componer, comprobar_destino

            try:
                cierre = componer(
                    juicio,
                    observaciones,
                    observaciones.get("solicitudes_efecto") or [],
                    enc,
                )
                comprobar_destino(cierre, DestinoEnsayo(registro.base / "destino"))
            except (ValueError, KeyError, TypeError) as exc:
                return {"rechazado": True, "motivo": str(exc)}
        resultado = registro.anotar(
            enc.id,
            {
                "kind": "comprobacion",
                "data": {
                    **cierre,
                    "observaciones": observaciones,
                    "host": host,
                    "hora": hora,
                },
            },
            f"resultado:{enc.id}:{token}",
        )
        return {
            "registrado": resultado in ("nuevo", "igual"),
            "encargo": enc.id,
            "estado": registro.fold(enc.id).estado,
        }
