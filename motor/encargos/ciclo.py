"""Ciclo de vida del encargo. No decide el trabajo de la aplicación."""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path

from .comprobacion import (
    FalloInstrumento,
    JuicioValidado,
    digest,
    firma_producto,
    hallazgos_deepseek,
    iniciar_comprobacion,
    instrucciones_responsable,
    instrucciones_revision,
    interno_de,
    leer_declaradas,
    leer_juicio,
    version_producto,
)
from .dominio import (
    Encargo,
    TransicionIlegal,
    alcance_de,
    bloqueantes_abiertos,
    revision_independiente_cubre_producto,
    decision_fallo,
    decision_proceso,
    huella_bloqueo,
    huella_entrega,
    id_encargo,
    nivel_cumplido,
    prompt_responsable,
    prompt_revisor,
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
    detenido: bool = False,
    entrega_nivel: str = "terminado",
    repo: str = "",
    rama: str = "",
    base_rama: str = "",
) -> dict:
    if via == "directa" and entrega_nivel != "terminado":
        raise TransicionIlegal(
            "la via directa no entrega niveles externos; usa la via durable"
        )
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
            or bool(datos.get("detenido")) != detenido
            or (datos.get("entrega_nivel") or "terminado") != entrega_nivel
            or (datos.get("repo") or "") != repo
            or (datos.get("rama") or "") != rama
            or (datos.get("base") or "") != base_rama
        ):
            salida["conflicto"] = True
        return salida
    encargo = id_encargo(clave, huella)
    recibo = recibo_de(ukey)
    datos_entrega = {
        "id": encargo,
        "clave": clave,
        "huella": huella,
        "texto": texto,
        "director": director,
        "adjuntos": list(adjuntos),
        "restricciones": list(restricciones),
        "canal": canal,
        "via": via,
        "detenido": detenido,
        "entrega_nivel": entrega_nivel,
        "repo": repo,
        "rama": rama,
        "base": base_rama,
        "recibo": recibo,
        "hora": hora,
    }
    reducir(None, {"kind": "entrega", "data": datos_entrega})
    registro.append(encargo, "entrega", datos_entrega, ukey, hora)
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
        entrega_nivel,
        repo,
        rama,
        base_rama,
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


def _almacen_sesion(base: Path, encargo_id: str, rol: str) -> Path:
    return base / "runtime" / encargo_id / f"sesion-{rol or 'responsable'}"


def _copiar_sesion(origen: Path, destino: Path) -> None:
    import shutil

    for sub in (".codex", ".grok"):
        fuente = origen / sub
        if fuente.is_dir():
            shutil.copytree(
                fuente,
                destino / sub,
                dirs_exist_ok=True,
                ignore=lambda _d, nombres: [
                    n for n in nombres if n == "auth.json"
                ],
            )

    codex = destino / ".codex"
    if codex.is_dir():
        import sqlite3
        from contextlib import closing

        rollouts = {p.name: p.resolve() for p in (codex / "sessions").rglob("*.jsonl")}
        for indice in codex.glob("state_*.sqlite"):
            with closing(sqlite3.connect(indice)) as db, db:
                columnas = {fila[1] for fila in db.execute("PRAGMA table_info(threads)")}
                if not {"id", "rollout_path"} <= columnas:
                    continue
                for ident, antigua in db.execute("SELECT id, rollout_path FROM threads"):
                    nombre = Path(str(antigua)).name
                    if nombre in rollouts:
                        db.execute(
                            "UPDATE threads SET rollout_path=? WHERE id=?",
                            (str(rollouts[nombre]), ident),
                        )


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
    gh=None,
) -> list[dict]:
    """Un paso. `lanzador(enc, prompt, espacio, sesion) -> argv` o None si no hay agente.

    `gh` es un GitHubPort para encargos con nivel de entrega pr o mas;
    sin el, esos encargos esperan visibles en el paso de entrega.
    """
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
        sin_llm = False
        if (
            not parada
            and enc.proc is None
            and not enc.reserva
            and enc.intentos_nuevos
            and enc.estado in ("recibido", "ejecutando")
            and enc.via != "directa"
        ):
            if _aplica_entrega(enc):
                enc = _paso_entrega(registro, enc, gh, hora)
                sin_llm = True
            elif _revision_aprobada_vigente(enc):
                enc = _intentar_cierre(registro, enc, espacio, directorio, hora)
                sin_llm = enc.estado == "terminado" or (
                    enc.rol == "revisor" and _revision_aprobada_vigente(enc)
                )
            if sin_llm:
                arbol = observar(directorio, proc)
        sin_hecho = _edad(enc, epoch) >= umbral_s
        decision = decision_proceso(
            enc,
            arbol,
            herramienta_en_curso(directorio),
            sin_hecho,
            enc.herramientas_observables,
            hora,
        )
        if sin_llm:
            decision["lanzar"] = False
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
    path = interno_de(espacio) / "pedido-efecto.json"
    if not path.exists():
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


def _anotar_instrumento(
    registro: Registro,
    enc: Encargo,
    fuente: str,
    causa: str,
    siguiente: str,
    hora: str,
    serie: str,
) -> None:
    registro.anotar(
        enc.id,
        {
            "kind": "instrumento_fallo",
            "data": {
                "fuente": fuente,
                "causa": causa,
                "version": enc.producto.firma if enc.producto else "",
                "siguiente": siguiente,
                "hora": hora,
            },
        },
        f"instrumento:{enc.id}:{fuente}:{serie}",
    )


def _devolver(
    registro: Registro,
    enc: Encargo,
    estrategia: str,
    motivo: str,
    siguiente: str,
    hora: str,
    serie: str,
) -> None:
    registro.anotar(
        enc.id,
        {
            "kind": "estrategia",
            "data": {
                "estrategia": estrategia,
                "rol": "responsable",
                "motivo": motivo,
                "siguiente": siguiente,
                "hora": hora,
            },
        },
        f"devolucion:{enc.id}:{enc.generacion}:{serie}",
    )


def _reanuda_en(segundos: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(seconds=segundos)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _reanuda_desde(hora: str, segundos: int) -> str:
    from datetime import datetime, timedelta

    try:
        base = datetime.fromisoformat(hora.replace("Z", "+00:00"))
    except ValueError:
        return _reanuda_en(segundos)
    return (base + timedelta(seconds=segundos)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _revision_aprobada_vigente(enc: Encargo) -> bool:
    return revision_independiente_cubre_producto(enc) and not bloqueantes_abiertos(enc)


def _correccion_deepseek_conserva_aprobadas(enc: Encargo) -> bool:
    return bool(
        enc.deepseek_vigente is not None
        and enc.revision_vigente is not None
        and enc.revision_vigente.veredicto == "aprobado"
        and (
            bloqueantes_abiertos(enc, "deepseek")
            or revision_independiente_cubre_producto(enc)
        )
        and enc.rol != "revisor"
        and not bloqueantes_abiertos(enc, "independiente")
        and not (set(alcance_de(enc).tocadas) & {r for r, _h in enc.aprobadas})
    )


def _aplica_entrega(enc: Encargo) -> bool:
    if enc.entrega.nivel == "terminado" or enc.revision_vigente is None:
        return False
    if enc.revision_vigente.veredicto != "aprobado":
        return False
    if not bloqueantes_abiertos(enc):
        return _revision_aprobada_vigente(enc)
    if bloqueantes_abiertos(enc, "independiente"):
        return False
    return (
        enc.deepseek_vigente is not None
        and enc.producto is not None
        and enc.producto.sha_git != enc.deepseek_sha
        and (
            enc.revision_vigente.version == enc.producto.firma
            or _correccion_deepseek_conserva_aprobadas(enc)
        )
    )


def _evidencia_revision(registro: Registro, enc: Encargo) -> str:
    for ev in reversed(registro.eventos(enc.id)):
        if ev["kind"] == "revision" and (ev["data"].get("origen") or "independiente") == "independiente":
            return str(ev["data"].get("evidencia") or "")
    return ""


def _producto_no_aprobado(enc: Encargo, espacio: Path):
    """La version vigente en disco si ya no es la aprobada. Ninguna si
    coincide con el producto registrado y con la referencia aprobada."""
    if enc.producto is None:
        return None
    actual = version_producto(espacio)
    referencias = {enc.producto.firma}
    if not _correccion_deepseek_conserva_aprobadas(enc):
        if enc.revisado is not None:
            referencias.add(enc.revisado.firma)
        if enc.revision_vigente is not None:
            referencias.add(enc.revision_vigente.version)
    referencias.discard("")
    if any(actual.firma != referencia for referencia in referencias):
        return actual
    return None


def _reabrir_revision(
    registro: Registro, enc: Encargo, version, hora: str
) -> Encargo:
    registro.anotar(
        enc.id,
        {
            "kind": "producto_listo",
            "data": {
                "version": version.firma,
                "manifiesto": [list(x) for x in version.manifiesto],
                "hora": hora,
            },
        },
        f"producto:{enc.id}:{enc.generacion}:{version.firma}",
    )
    registro.anotar(
        enc.id,
        {
            "kind": "estrategia",
            "data": {
                "estrategia": "revisar_evidencia",
                "rol": "revisor",
                "motivo": "el producto cambio tras la aprobacion",
                "siguiente": "re-revisar la version vigente antes de publicar o cerrar",
                "hora": hora,
            },
        },
        f"revision-stale:{enc.id}:{version.firma}",
    )
    return registro.fold(enc.id)


def _intentar_cierre(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    directorio: Path,
    hora: str,
) -> Encargo:
    """Despacha efectos con recibo y compone el cierre si el nivel se cumplio.

    Lo usan _cerrar_con_juicio y el reintento sin LLM de barrer: un efecto
    sin asentar reintenta este paso, nunca rehace el producto.
    """
    from motor.runwrap import _publica

    if enc.producto is not None and not enc.producto.sha_git:
        stale = _producto_no_aprobado(enc, espacio)
        if stale is not None:
            return _reabrir_revision(registro, enc, stale, hora)
    try:
        pedidos = _pedidos_efecto(espacio)
    except (ValueError, TypeError) as exc:
        _anotar_instrumento(
            registro, enc, "adaptador", str(exc),
            "corregir id, operacion, clave y cuerpo en .interno/pedido-efecto.json",
            hora, f"pedidos-{enc.generacion}",
        )
        _devolver(
            registro, enc, "reparar_instrumento", str(exc),
            "corregir la solicitud interna sin tocar el producto",
            hora, "pedidos",
        )
        return registro.fold(enc.id)
    if pedidos and not enc.autoridad:
        marcar_espera(
            registro,
            enc.id,
            "resultado comprobado; falta autoridad para conciliar efectos",
            _reanuda_en(30),
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
            causa = resultado.get("motivo") or "efecto sin recibo confirmado"
            _anotar_instrumento(
                registro, enc, "adaptador", causa,
                "conciliar el efecto antes de completar la entrega",
                hora, f"efecto-{pedido['id']}-{enc.generacion}",
            )
            marcar_espera(
                registro,
                enc.id,
                "resultado comprobado; conciliar efecto sin recibo antes de publicar",
                _reanuda_en(30),
                hora,
            )
            return registro.fold(enc.id)
    enc = registro.fold(enc.id)
    if enc.entrega.nivel != "terminado" and not nivel_cumplido(enc):
        return enc
    from .cierre import componer

    try:
        cierre = componer(enc, pedidos, _evidencia_revision(registro, enc))
    except ValueError as exc:
        _anotar_instrumento(
            registro, enc, "adaptador", str(exc),
            "completar lo que falta para el cierre sin tocar el producto aprobado",
            hora, f"cierre-{enc.generacion}",
        )
        return registro.fold(enc.id)
    _publica(directorio / "cierre.json", json.dumps(cierre, ensure_ascii=False))
    _anotar_juicio(registro, enc, cierre, hora, enc.generacion)
    enc = registro.fold(enc.id)
    if enc.estado == "terminado":
        _publicar_resultado(registro, enc, hora)
    return enc


def _cerrar_con_juicio(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    directorio: Path,
    juicio: JuicioValidado,
    hora: str,
) -> Encargo:
    from motor.runwrap import _publica

    try:
        pedidos = _pedidos_efecto(espacio)
        error_pedidos = ""
    except (ValueError, TypeError) as exc:
        pedidos = []
        error_pedidos = str(exc)
    if juicio.veredicto == "aprobado" and error_pedidos:
        _anotar_instrumento(
            registro, enc, "adaptador", error_pedidos,
            "corregir id, operacion, clave y cuerpo en .interno/pedido-efecto.json",
            hora, f"pedidos-{enc.generacion}",
        )
        _devolver(
            registro, enc, "reparar_instrumento", error_pedidos,
            "corregir la solicitud interna sin tocar el producto",
            hora, "pedidos",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    if juicio.veredicto == "aprobado" and pedidos:
        if set(juicio.efectos_pendientes) != {str(p["id"]) for p in pedidos}:
            _anotar_instrumento(
                registro, enc, "juicio",
                "la aprobacion no enumera en efectos_pendientes los IDs exactos solicitados",
                "volver a emitir el juicio enumerando los efectos aprobados",
                hora, f"efectos-{enc.generacion}",
            )
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id)
    version_previa = enc.revision_vigente.version if enc.revision_vigente else ""
    ids_previos = {h.id for h in bloqueantes_abiertos(enc, "independiente")}
    revisor = ""
    for rol, _agente, modelo in reversed(enc.modelos):
        if rol == "revisor":
            revisor = modelo
            break
    revision_id = "rev_" + hashlib.sha256(
        f"{enc.id}:{enc.generacion}:{juicio.version}".encode()
    ).hexdigest()[:12]
    registro.anotar(
        enc.id,
        {
            "kind": "revision",
            "data": {
                "revision_id": revision_id,
                "version": juicio.version,
                "base_version": juicio.base_version,
                "alcance": juicio.alcance,
                "veredicto": juicio.veredicto,
                "revisor": revisor or enc.agente,
                "origen": "independiente",
                "cubre": list(juicio.cubre),
                "hallazgos": [h.como_dict() for h in juicio.hallazgos],
                "aprobadas": [list(x) for x in juicio.aprobadas],
                "evidencia": juicio.evidencia,
                "efectos_pendientes": list(juicio.efectos_pendientes),
                "hora": hora,
            },
        },
        f"revision-dato:{enc.id}:{enc.generacion}:{juicio.version}",
    )
    enc = registro.fold(enc.id)
    if juicio.veredicto == "cambios":
        ids_ahora = {h.id for h in bloqueantes_abiertos(enc, "independiente")}
        progreso = juicio.version != version_previa or bool(ids_previos - ids_ahora)
        abiertos = bloqueantes_abiertos(enc, "independiente")
        primero = abiertos[0] if abiertos else None
        motivo = primero.titulo if primero else (juicio.nota or "hay bloqueantes")
        if not progreso:
            huella = huella_bloqueo(enc)
            if enc.sin_progreso >= 1:
                detalle = "; ".join(f"{h.id} {h.titulo}" for h in abiertos)
                registro.anotar(
                    enc.id,
                    {
                        "kind": "sin_progreso",
                        "data": {
                            "huella": huella,
                            "diagnostico": (
                                "el mismo bloqueo sigue tras el diagnostico, "
                                f"sin informacion nueva: {detalle}"
                            ),
                            "escalar": True,
                            "hora": hora,
                        },
                    },
                    f"sin-progreso:{enc.id}:{huella}:escalar",
                )
            else:
                registro.anotar(
                    enc.id,
                    {
                        "kind": "sin_progreso",
                        "data": {"huella": huella, "escalar": False, "hora": hora},
                    },
                    f"sin-progreso:{enc.id}:{huella}:diagnostico",
                )
                _devolver(
                    registro, enc, "diagnosticar_bloqueo", motivo,
                    "diagnosticar por que la correccion no resolvio el bloqueo; "
                    "no repetir la misma accion",
                    hora, "diagnostico",
                )
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id)
        registro.anotar(
            enc.id,
            {
                "kind": "procedimiento",
                "data": {
                    "nota": juicio.nota or motivo,
                    "hora": hora,
                },
            },
            f"reparacion:{enc.id}:{enc.generacion}",
        )
        _devolver(
            registro, enc, "investigar", motivo,
            "corregir " + ", ".join(h.id for h in abiertos)
            + "; lo aprobado que no cambio se conserva",
            hora, "cambios",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    if juicio.veredicto == "no_concluyente":
        _devolver(
            registro, enc, "investigar",
            juicio.nota or "evidencia insuficiente para aprobar",
            juicio.nota or "aportar la evidencia que falta sin rebajar el pedido",
            hora, "no-concluyente",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    enc = _intentar_cierre(registro, enc, espacio, directorio, hora)
    _publica(directorio / "incorporado", hora)
    return registro.fold(enc.id)


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
        sesion_runtime = str(outcome.get("sesion_runtime") or "")
        if sesion_runtime:
            registro.anotar(
                enc.id,
                {
                    "kind": "intento_cerrado",
                    "data": {"hora": hora, "sesion_runtime": sesion_runtime},
                },
                f"sesion-runtime:{enc.id}:{enc.generacion}",
            )
            enc = registro.fold(enc.id)
        spec = (
            json.loads((directorio / "arranque.json").read_text(encoding="utf-8"))
            if (directorio / "arranque.json").is_file()
            else {}
        )
        agente = spec.get("agente") or enc.agente or "externo"
        token_intento = str(spec.get("token") or "")
        if token_intento:
            _copiar_sesion(
                registro.base / "runtime" / enc.id / token_intento,
                _almacen_sesion(registro.base, enc.id, enc.rol),
            )
        if outcome.get("fallo_runtime") and spec.get("sesion_modo") == "reanudada":
            from motor.fallas import clasifica

            mensaje_fallo = str(outcome["fallo_runtime"])
            clase_proveedor = clasifica(agente, "", mensaje_fallo)["kind"]
            es_proveedor = clase_proveedor in (
                "cuota",
                "rate_limit",
                "auth",
                "modelo_no_disponible",
                "transitorio",
            )
            bajo = mensaje_fallo.lower()
            afirma_sesion_perdida = any(
                frase in bajo
                for frase in (
                    "no rollout found",
                    "thread/resume failed",
                    "session not found",
                    "no session found",
                    "conversation not found",
                )
            )
            if afirma_sesion_perdida and not es_proveedor:
                import shutil

                shutil.rmtree(
                    _almacen_sesion(registro.base, enc.id, enc.rol),
                    ignore_errors=True,
                )
                registro.anotar(
                    enc.id,
                    {
                        "kind": "sesion_perdida",
                        "data": {"rol": enc.rol or "responsable", "hora": hora},
                    },
                    f"sesion-perdida:{enc.id}:{enc.generacion}",
                )
                _anotar_instrumento(
                    registro, registro.fold(enc.id), "sesion",
                    "la sesion anterior no se pudo reanudar: "
                    + mensaje_fallo[:300],
                    "continuar con sesion reconstruida desde la libreta",
                    hora, f"sesion-{enc.generacion}",
                )
                _publica(directorio / "incorporado", hora)
                return registro.fold(enc.id)
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
        return _incorporar_responsable(registro, enc, espacio, hora, directorio)
    return _incorporar_revisor(registro, enc, espacio, hora, directorio)


def _incorporar_responsable(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    hora: str,
    directorio: Path,
) -> Encargo:
    from motor.runwrap import _publica

    interno = interno_de(espacio, crear=True)
    try:
        declaradas = leer_declaradas(interno)
        error_declaradas = ""
    except ValueError as exc:
        declaradas = None
        error_declaradas = str(exc)
    version = version_producto(espacio)
    registro.anotar(
        enc.id,
        {
            "kind": "producto_listo",
            "data": {
                "version": version.firma,
                "manifiesto": [list(x) for x in version.manifiesto],
                "declara_comprobaciones": declaradas is not None,
                "hora": hora,
            },
        },
        f"producto:{enc.id}:{enc.generacion}:{version.firma}",
    )
    enc = registro.fold(enc.id)
    if error_declaradas:
        _anotar_instrumento(
            registro, enc, "comprobacion", error_declaradas,
            "corregir .interno/comprobaciones.json sin tocar el producto",
            hora, f"declaradas-{enc.generacion}",
        )
        _devolver(
            registro, enc, "reparar_instrumento", error_declaradas,
            "corregir las comprobaciones declaradas y volver a observar",
            hora, "declaradas",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    try:
        _pedidos_efecto(espacio)
    except (ValueError, TypeError) as exc:
        _anotar_instrumento(
            registro, enc, "adaptador", str(exc),
            "corregir id, operacion, clave y cuerpo en .interno/pedido-efecto.json",
            hora, f"pedidos-{enc.generacion}",
        )
        _devolver(
            registro, enc, "reparar_instrumento", str(exc),
            "corregir la solicitud interna sin tocar el producto",
            hora, "pedidos",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    if declaradas is not None:
        path = directorio / "observaciones.json"
        observacion = None
        if path.is_file():
            try:
                candidata = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                candidata = None
            if candidata and (
                candidata.get("version") or candidata.get("producto")
            ) == version.firma:
                observacion = candidata
        if observacion is None:
            iniciar_comprobacion(espacio, directorio)
            return enc
        if observacion.get("error_instrumento"):
            _anotar_instrumento(
                registro, enc, "comprobacion",
                str(observacion["error_instrumento"]),
                "corregir las comprobaciones declaradas y volver a observar",
                hora, f"obs-{enc.generacion}",
            )
            _devolver(
                registro, enc, "reparar_instrumento",
                str(observacion["error_instrumento"]),
                "reparar el comando de comprobacion sin tocar el producto",
                hora, "obs",
            )
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id)
        registro.anotar(
            enc.id,
            {
                "kind": "observacion_supervisor",
                "data": {
                    "version": version.firma,
                    "ejecuciones": observacion.get("ejecuciones") or [],
                    "hora": hora,
                },
            },
            f"observacion:{enc.id}:{version.firma}",
        )
        enc = registro.fold(enc.id)
    if _correccion_deepseek_conserva_aprobadas(enc):
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    alcance = alcance_de(enc)
    registro.anotar(
        enc.id,
        {
            "kind": "estrategia",
            "data": {
                "estrategia": "revisar_evidencia",
                "rol": "revisor",
                "motivo": (
                    "revisar la correccion y sus efectos"
                    if alcance.clase == "correccion"
                    else "revisar el producto completo contra el pedido"
                ),
                "siguiente": "inspeccionar producto y observaciones del supervisor",
                "hora": hora,
            },
        },
        f"revision:{enc.id}:{enc.generacion}",
    )
    _publica(directorio / "incorporado", hora)
    return registro.fold(enc.id)


def _incorporar_revisor(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    hora: str,
    directorio: Path,
) -> Encargo:
    from motor.runwrap import _publica

    ref_path = directorio / "revision.json"
    if not ref_path.is_file():
        _anotar_instrumento(
            registro, enc, "juicio", "falta la referencia de la revision",
            "volver a lanzar la revision sobre la misma version",
            hora, f"sinref-{enc.generacion}",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    referencia = json.loads(ref_path.read_text(encoding="utf-8"))
    version_actual = version_producto(espacio)
    version_esperada = str(
        referencia.get("version")
        or (enc.producto.firma if enc.producto else "")
    )
    if version_actual.firma != version_esperada:
        _anotar_instrumento(
            registro, enc, "juicio",
            "el producto cambio durante la revision",
            "volver a revisar la version vigente",
            hora, f"stale-{enc.generacion}",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    if enc.producto is None or enc.producto.firma != version_actual.firma:
        registro.anotar(
            enc.id,
            {
                "kind": "producto_listo",
                "data": {
                    "version": version_actual.firma,
                    "manifiesto": [list(x) for x in version_actual.manifiesto],
                    "hora": hora,
                },
            },
            f"producto:{enc.id}:{enc.generacion}:{version_actual.firma}",
        )
        enc = registro.fold(enc.id)
    alcance = alcance_de(enc)
    resultado = leer_juicio(Path(referencia["resultado"]), alcance)
    if isinstance(resultado, FalloInstrumento):
        _anotar_instrumento(
            registro, enc, resultado.fuente, resultado.causa,
            "volver a emitir el juicio con el formato pedido sobre la misma version",
            hora, f"juicio-{enc.generacion}",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    if resultado.instrumento:
        _anotar_instrumento(
            registro, enc, "comprobacion", resultado.instrumento,
            "reparar el instrumento de comprobacion sin tocar el producto",
            hora, f"instrumento-revisor-{enc.generacion}",
        )
        _devolver(
            registro, enc, "reparar_instrumento", resultado.instrumento,
            "corregir el comando de comprobacion y volver a observar",
            hora, "instrumento",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    return _cerrar_con_juicio(registro, enc, espacio, directorio, resultado, hora)


def _modelo_en_argv(cmd: list[str]) -> str:
    for i, arg in enumerate(cmd[:-1]):
        if arg in ("-m", "--model"):
            return str(cmd[i + 1])
    return ""


def _alcance_para_revision(
    registro: Registro, enc: Encargo, espacio: Path, raiz: Path
):
    """Alcance desde el fold, con la observacion del supervisor de la version
    vigente leida del evento, no del archivo mas reciente por mtime."""
    alcance = alcance_de(enc)
    observacion = None
    for ev in reversed(registro.eventos(enc.id)):
        if (
            ev["kind"] == "observacion_supervisor"
            and str(ev["data"].get("version") or "") == alcance.version
        ):
            observacion = {
                "version": alcance.version,
                "producto": alcance.version,
                "ejecuciones": ev["data"].get("ejecuciones") or [],
            }
            break
    if observacion is None:
        for fuente in sorted(raiz.glob("intento-*/observaciones.json")):
            try:
                candidata = json.loads(fuente.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                candidata.get("version") or candidata.get("producto")
            ) == alcance.version:
                observacion = candidata
    if observacion is not None:
        try:
            observacion["solicitudes_efecto"] = _pedidos_efecto(espacio)
        except (ValueError, TypeError) as exc:
            observacion["solicitudes_efecto"] = None
            observacion["error_solicitudes_efecto"] = str(exc)
        observacion.pop("error_instrumento", None)
    return replace(alcance, observacion=observacion)


def _git(espacio: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(espacio), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=180,
    )


def _repo_url(repo: str) -> str:
    if "://" in repo or repo.startswith(("/", ".", "~")) or Path(repo).exists():
        return repo
    return f"https://github.com/{repo}.git"


def _asegurar_repo(enc: Encargo, espacio: Path) -> str:
    """El espacio de un encargo con entrega es un clon en la rama del encargo.

    Devuelve la causa si no se pudo; el supervisor clona fuera de la jaula,
    sin sesion LLM.
    """
    if (espacio / ".git").exists():
        return ""
    if version_producto(espacio).manifiesto:
        return (
            "el espacio ya tiene producto pero no es un clon del repo "
            f"{enc.entrega.repo}; no se mezcla sobre un trabajo existente"
        )
    url = _repo_url(enc.entrega.repo)
    r = subprocess.run(
        ["git", "clone", "--quiet", url, str(espacio / ".clon-tmp")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=300,
    )
    if r.returncode != 0:
        return f"no se pudo clonar {enc.entrega.repo}: {r.stderr.strip()[-300:]}"
    clon = espacio / ".clon-tmp"
    for entrada in clon.iterdir():
        entrada.replace(espacio / entrada.name)
    clon.rmdir()
    r = _git(espacio, "checkout", "-B", enc.entrega.rama, f"origin/{enc.entrega.base}")
    if r.returncode != 0:
        return (
            f"no se pudo crear la rama {enc.entrega.rama} desde "
            f"{enc.entrega.base}: {r.stderr.strip()[-300:]}"
        )
    return ""


def _commit_push(enc: Encargo, espacio: Path) -> tuple[str, str]:
    """Commit del producto (sin .interno) y push. Devuelve (sha, causa)."""
    if _git(espacio, "ls-files", "--", ".interno").stdout.strip():
        _git(espacio, "rm", "-r", "--cached", "--quiet", ".interno")
    r = _git(
        espacio, "add", "-A", "--", ".",
        ":(exclude).interno", ":(exclude)**/.interno",
        ":(exclude)**/__pycache__", ":(exclude)**/.runtime",
    )
    if r.returncode != 0:
        return "", f"git add fallo: {r.stderr.strip()[-300:]}"
    pendientes = _git(espacio, "diff", "--cached", "--quiet")
    if pendientes.returncode not in (0, 1):
        return "", f"git diff del indice fallo: {pendientes.stderr.strip()[-300:]}"
    if pendientes.returncode == 1:
        titulo = " ".join(enc.texto.split())[:72] or enc.id
        r = _git(
            espacio,
            "-c", "user.name=motor de encargos",
            "-c", "user.email=motor@localhost",
            "commit", "--quiet", "-m", f"{enc.clave}: {titulo}",
        )
        if r.returncode != 0:
            return "", f"git commit fallo: {r.stderr.strip()[-300:]}"
    sha = _git(espacio, "rev-parse", "HEAD").stdout.strip()
    r = _git(espacio, "push", "--quiet", "origin", enc.entrega.rama)
    if r.returncode != 0:
        return "", f"git push fallo: {r.stderr.strip()[-300:]}"
    return sha, ""


def _minutos_desde(hora_evento: str, hora: str) -> float:
    from datetime import datetime

    try:
        a = datetime.fromisoformat(hora_evento.replace("Z", "+00:00"))
        b = datetime.fromisoformat(hora.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (b - a).total_seconds() / 60.0


def _disparar_deepseek(gh, enc: Encargo, espacio: Path, sha: str) -> None:
    """Recuperacion por dispatch, como en el motor por planes. Best effort:
    si el workflow no existe en el SHA o el disparo falla, la espera sigue
    su curso; nunca se finge una revision leida."""
    try:
        from motor.effects import inputs_de_workflow

        worker = _git(espacio, "show", f"{sha}:.github/workflows/ai-review-worker.yml")
        if worker.returncode != 0:
            return
        publish = _git(espacio, "show", f"{sha}:.github/workflows/ai-review-publish.yml")
        if publish.returncode == 0:
            archivo, texto = "ai-review-publish.yml", publish.stdout
        else:
            archivo, texto = "ai-review-worker.yml", worker.stdout
        base = _git(espacio, "rev-parse", f"origin/{enc.entrega.base}").stdout.strip()
        valores = {
            "pr_number": str(enc.pr_numero),
            "head_sha": sha,
            "base_sha": base,
            "request_id": "1001",
            "coordinator_run_id": str(enc.pr_numero),
            "finding_identity": "current",
        }
        inputs = {}
        for nombre, obligatorio in inputs_de_workflow(texto):
            if nombre in valores and (obligatorio or nombre == "pr_number"):
                inputs[nombre] = valores[nombre]
            elif obligatorio:
                inputs[nombre] = valores.get(nombre, "motor")
        gh.disparar_workflow(enc.entrega.repo, archivo, inputs)
    except Exception:
        pass


def _paso_entrega(registro: Registro, enc: Encargo, gh, hora: str) -> Encargo:
    """Entrega externa sin LLM: commit/push del supervisor, PR listo y
    espera de la revision de DeepSeek por SHA. Idempotente por paso+SHA."""
    if nivel_cumplido(enc) and enc.estado != "terminado":
        return _intentar_cierre(
            registro, enc, _espacio(registro.base, enc.id),
            _directorio(registro.base, enc.id), hora,
        )
    if gh is None:
        causa = (
            "nivel de entrega "
            + enc.entrega.nivel
            + " sin puerto GitHub configurado en este supervisor"
        )
        _anotar_instrumento(
            registro, enc, "github", causa,
            "configurar el puerto GitHub del supervisor y reintentar",
            hora, "sin-puerto",
        )
        marcar_espera(registro, enc.id, causa, _reanuda_en(300), hora)
        return registro.fold(enc.id)
    espacio = _espacio(registro.base, enc.id)
    directorio = _directorio(registro.base, enc.id)
    causa = _asegurar_repo(enc, espacio)
    if causa:
        _anotar_instrumento(
            registro, enc, "github", causa,
            "dejar accesible el repo declarado y reintentar",
            hora, "repo-entrega",
        )
        marcar_espera(registro, enc.id, causa, _reanuda_en(300), hora)
        return registro.fold(enc.id)
    stale = _producto_no_aprobado(enc, espacio)
    if stale is not None:
        return _reabrir_revision(registro, enc, stale, hora)
    sha, causa = _commit_push(enc, espacio)
    if causa:
        _anotar_instrumento(
            registro, enc, "github", causa,
            "reintentar commit y push de la misma version aprobada",
            hora, f"push-{enc.generacion}",
        )
        marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
        return registro.fold(enc.id)
    for paso in ("commit", "push"):
        registro.anotar(
            enc.id,
            {
                "kind": "entrega_externa",
                "data": {
                    "paso": paso,
                    "repo": enc.entrega.repo,
                    "sha": sha,
                    "recibo": sha,
                    "hora": hora,
                },
            },
            f"entrega:{enc.id}:{paso}:{sha}",
        )
    try:
        pr = gh.abrir_pr(
            enc.entrega.repo,
            enc.entrega.rama,
            enc.entrega.base,
            f"{enc.clave}: {' '.join(enc.texto.split())[:72]}",
            f"Encargo {enc.id}. Revision independiente aprobada.",
        )
        numero = int(pr.numero)
        registro.anotar(
            enc.id,
            {
                "kind": "entrega_externa",
                "data": {
                    "paso": "pr_abierto",
                    "repo": enc.entrega.repo,
                    "numero": numero,
                    "sha": pr.head or sha,
                    "recibo": str(numero),
                    "hora": hora,
                },
            },
            f"entrega:{enc.id}:pr_abierto:{numero}",
        )
        enc = registro.fold(enc.id)
        if pr.borrador:
            gh.marcar_listo(enc.entrega.repo, numero)
        registro.anotar(
            enc.id,
            {
                "kind": "entrega_externa",
                "data": {
                    "paso": "pr_listo",
                    "repo": enc.entrega.repo,
                    "numero": numero,
                    "sha": sha,
                    "recibo": str(numero),
                    "hora": hora,
                },
            },
            f"entrega:{enc.id}:pr_listo:{numero}:{sha}",
        )
    except Exception as exc:
        causa = f"no se pudo abrir o preparar el PR: {exc}"
        _anotar_instrumento(
            registro, enc, "github", causa,
            "reintentar la apertura del PR de la misma rama",
            hora, f"pr-{enc.generacion}",
        )
        marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
        return registro.fold(enc.id)
    enc = registro.fold(enc.id)
    if enc.deepseek_sha != sha:
        try:
            comentarios = gh.comentarios_deepseek(enc.entrega.repo, numero, sha)
        except Exception as exc:
            causa = f"no se pudieron leer los comentarios de DeepSeek: {exc}"
            _anotar_instrumento(
                registro, enc, "github", causa,
                "reintentar la lectura de DeepSeek para el mismo SHA",
                hora, f"deepseek-leer-{sha[:12]}",
            )
            marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
            return registro.fold(enc.id)
        if not comentarios:
            eventos = registro.eventos(enc.id)
            disparado = any(
                ev["kind"] == "entrega_externa"
                and ev["data"].get("paso") == "deepseek_disparado"
                and ev["data"].get("sha") == sha
                for ev in eventos
            )
            primera = next(
                (
                    str(ev["data"].get("hora") or ev["ts"])
                    for ev in eventos
                    if ev["kind"] == "entrega_externa"
                    and ev["data"].get("paso") in ("pr_listo", "commit")
                    and ev["data"].get("sha") == sha
                ),
                hora,
            )
            minutos = _minutos_desde(primera, hora)
            if not disparado and minutos >= 2:
                _disparar_deepseek(gh, enc, espacio, sha)
                registro.anotar(
                    enc.id,
                    {
                        "kind": "entrega_externa",
                        "data": {
                            "paso": "deepseek_disparado",
                            "repo": enc.entrega.repo,
                            "numero": numero,
                            "sha": sha,
                            "hora": hora,
                        },
                    },
                    f"entrega:{enc.id}:deepseek_disparado:{sha}",
                )
            marcar_espera(
                registro,
                enc.id,
                f"esperando revisión de DeepSeek para el SHA {sha}",
                _reanuda_desde(hora, 60),
                hora,
            )
            return registro.fold(enc.id)
        hallazgos = hallazgos_deepseek(comentarios, sha)
        nuevos_bloq = {h.id for h in hallazgos if h.gravedad == "bloqueante"}
        fusion = list(hallazgos)
        for h in bloqueantes_abiertos(enc, "deepseek"):
            if h.id not in nuevos_bloq:
                fusion.append(replace(h, estado="resuelto"))
        registro.anotar(
            enc.id,
            {
                "kind": "revision",
                "data": {
                    "revision_id": "rev_ds_" + sha[:12],
                    "version": enc.producto.firma if enc.producto else "",
                    "base_version": enc.deepseek_sha,
                    "alcance": "correccion" if enc.deepseek_sha else "completa",
                    "veredicto": "cambios" if nuevos_bloq else "aprobado",
                    "revisor": "deepseek",
                    "origen": "deepseek",
                    "cubre": [
                        h.id for h in bloqueantes_abiertos(enc, "deepseek")
                    ],
                    "hallazgos": [h.como_dict() for h in fusion],
                    "hora": hora,
                },
            },
            f"revision-ds:{enc.id}:{sha}",
        )
        registro.anotar(
            enc.id,
            {
                "kind": "entrega_externa",
                "data": {
                    "paso": "deepseek_leido",
                    "repo": enc.entrega.repo,
                    "numero": numero,
                    "sha": sha,
                    "recibo": sha,
                    "hora": hora,
                },
            },
            f"entrega:{enc.id}:deepseek_leido:{sha}",
        )
        enc = registro.fold(enc.id)
        abiertos_ds = bloqueantes_abiertos(enc, "deepseek")
        if abiertos_ds:
            _devolver(
                registro, enc, "investigar", abiertos_ds[0].titulo,
                "corregir " + ", ".join(h.id for h in abiertos_ds)
                + " (DeepSeek); lo aprobado que no cambio se conserva",
                hora, "deepseek",
            )
            return registro.fold(enc.id)
    enc = registro.fold(enc.id)
    if enc.entrega.nivel in ("integrado", "desplegado") and enc.nivel_alcanzado == "pr":
        try:
            vista_pr = gh.ver(enc.entrega.repo, enc.pr_numero)
            if vista_pr.estado != "mergeado":
                vista_pr = gh.merge(
                    enc.entrega.repo,
                    enc.pr_numero,
                    enc.pr_sha,
                    "squash",
                    f"{enc.clave}: {' '.join(enc.texto.split())[:72]}",
                    )
        except Exception as exc:
            from motor.github import MergeRechazado

            causa = f"no se pudo integrar el PR #{enc.pr_numero}: {exc}"
            _anotar_instrumento(
                registro, enc, "github", causa,
                "resolver la causa del merge rechazado sobre el SHA revisado",
                hora, f"merge-{enc.pr_sha[:12]}",
            )
            if isinstance(exc, MergeRechazado):
                marcar_pregunta(registro, enc.id, causa, True, hora)
            else:
                marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
            return registro.fold(enc.id)
        registro.anotar(
            enc.id,
            {
                "kind": "entrega_externa",
                "data": {
                    "paso": "integrado",
                    "repo": enc.entrega.repo,
                    "numero": enc.pr_numero,
                    "sha": vista_pr.merge_commit or vista_pr.head,
                    "recibo": vista_pr.merge_commit or vista_pr.head,
                    "hora": hora,
                },
            },
            f"entrega:{enc.id}:integrado:{enc.pr_sha}",
        )
        enc = registro.fold(enc.id)
    if enc.entrega.nivel == "desplegado" and enc.nivel_alcanzado == "integrado":
        efecto = next(
            (
                e
                for e in enc.efectos
                if e.operacion == "desplegar" and e.estado == "aplicado" and e.recibo
            ),
            None,
        )
        if efecto is None:
            registro.anotar(
                enc.id,
                {
                    "kind": "sin_progreso",
                    "data": {
                        "huella": enc.bloqueo_huella,
                        "diagnostico": (
                            "integrado; falta un efecto de despliegue "
                            "declarado con recibo para cerrar el nivel "
                            "desplegado"
                        ),
                        "escalar": True,
                        "hora": hora,
                    },
                },
                f"entrega:{enc.id}:desplegado:sin-efecto",
            )
            return registro.fold(enc.id)
        registro.anotar(
            enc.id,
            {
                "kind": "entrega_externa",
                "data": {
                    "paso": "desplegado",
                    "repo": enc.entrega.repo,
                    "numero": enc.pr_numero,
                    "sha": enc.pr_sha,
                    "recibo": efecto.recibo,
                    "hora": hora,
                },
            },
            f"entrega:{enc.id}:desplegado:{efecto.id}",
        )
        enc = registro.fold(enc.id)
    if nivel_cumplido(enc):
        return _intentar_cierre(registro, enc, espacio, directorio, hora)
    return enc


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
        _copiar_sesion(_almacen_sesion(registro.base, enc.id, rol), runtime)
        if enc.entrega.nivel != "terminado" and rol == "responsable":
            causa_repo = _asegurar_repo(enc, espacio)
            if causa_repo:
                _anotar_instrumento(
                    registro, enc, "github", causa_repo,
                    "dejar accesible el repo declarado y reintentar",
                    hora, f"repo-{generacion}",
                )
                marcar_espera(registro, enc.id, causa_repo, _reanuda_en(300), hora)
                return
        epoca = enc.epoca_revisor if rol == "revisor" else enc.epoca_responsable
        sesion = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{enc.id}:{rol}:{epoca}")
        )
        if rol == "revisor":
            alcance = _alcance_para_revision(registro, enc, espacio, raiz)
            observacion = alcance.observacion or {}
            origen = directorio / "observaciones-revision.json"
            _publica(origen, json.dumps(observacion, ensure_ascii=False))
            resultado = runtime / "resultado.json"
            _publica(
                directorio / "revision.json",
                json.dumps(
                    {
                        "observaciones": str(origen),
                        "resultado": str(resultado),
                        "version": alcance.version,
                        "alcance": alcance.clase,
                    }
                ),
            )
            contenido = prompt_revisor(enc, alcance)
            if enc.motivo.startswith("instrumento:") and enc.causa:
                contenido += (
                    "\n\nEl intento anterior fallo de forma, no de producto: "
                    + enc.causa
                    + ". Emite el juicio con el formato pedido."
                )
            contenido += "\n\n" + instrucciones_revision(alcance, resultado)
        else:
            interno_de(espacio, crear=True)
            contenido = prompt_responsable(enc)
            contenido += "\n\n" + instrucciones_responsable(interno_de(espacio))
        # Sólo observaciones de este encargo; la autoridad sigue fuera de la jaula.
        copias_observadas = []
        for fuente in sorted(raiz.glob("intento-*/observaciones*.json")):
            copia = runtime / "observaciones" / fuente.parent.name / fuente.name
            copia.parent.mkdir(parents=True, exist_ok=True)
            _publica(copia, fuente.read_text(encoding="utf-8"))
            contenido = contenido.replace(str(fuente), str(copia))
            for nota in enc.procedimiento:
                _, separador, referencia = nota.partition("observaciones del supervisor ")
                if separador and Path(referencia).resolve() == fuente.resolve():
                    contenido = contenido.replace(referencia, str(copia))
            copias_observadas.append(str(copia))
        if copias_observadas:
            contenido += (
                "\n\nLas observaciones disponibles estan copiadas en tu runtime privado:\n"
                + "\n".join(copias_observadas)
                + "\nUsa estas copias. No necesitas acceso al registro ni al directorio de autoridad."
            )
        prompt = runtime / "prompt.md"
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
        poteto_digest = ""
        meta: dict = {}
        try:
            from motor.flota.runtime import PotetoAusente, exigir_poteto, vincular

            gen_path = Path(flota_ruta) if flota_ruta else None
            if rol == "responsable":
                try:
                    skill, poteto_digest = exigir_poteto(
                        gen_path, registro.base, runtime
                    )
                except PotetoAusente as exc:
                    _liberar_flota()
                    _anotar_instrumento(
                        registro, enc, "poteto", str(exc),
                        "instalar poteto-mode en la generacion o en la politica y reintentar",
                        hora, f"poteto-{generacion}",
                    )
                    marcar_espera(registro, enc.id, str(exc), _reanuda_en(300), hora)
                    return
                contenido += (
                    "\n\nLee poteto-mode en " + str(skill)
                    + ". Sus referencias estan en el paquete local. Adapta a herramientas nativas. "
                    "Las instrucciones del usuario prevalecen. Este encargo usa un unico agente "
                    "sin delegacion ni Cursor. No apliques cantidades fijas, nuevos gates ni "
                    "pruebas fuera de los casos autorizados."
                )
            _publica(prompt, contenido)
            lanzado = lanzador(enc, prompt, espacio, sesion)
            if isinstance(lanzado, tuple):
                cmd, meta = lanzado
            else:
                cmd = lanzado
            agente_lanzado = str(meta.get("agente") or "") or (
                Path(cmd[0]).stem.lower() if cmd else ""
            )
            if cmd and gen_path is not None:
                cmd = vincular(gen_path, cmd, funcion="planear" if rol == "revisor" else "codigo")
        except Exception:
            _liberar_flota()
            raise
        if not cmd:
            _liberar_flota()
            return
        modelo_lanzado = str(meta.get("modelo") or "") or _modelo_en_argv(cmd)
        sesion_modo = str(meta.get("sesion_modo") or "nueva")
        entorno_gen = (
            {"MOTOR_GENERACION": flota_generacion, "MOTOR_GENERACION_RUTA": flota_ruta}
            if flota_generacion
            else None
        )
        try:
            preparar_administrado(
                cmd, espacio, directorio, enc, sesion, hora, runtime, entorno_gen,
                agente=agente_lanzado,
                modelo=modelo_lanzado,
                sesion_modo=sesion_modo,
                poteto_digest=poteto_digest,
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
    entrega_nivel: str = "terminado",
    repo: str = "",
    rama: str = "",
    base_rama: str = "",
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
            "entrega_nivel": entrega_nivel,
            "repo": repo,
            "rama": rama,
            "base_rama": base_rama,
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
    pendiente = enc.estado == "terminado" or _revision_aprobada_vigente(enc)
    if (
        not pendiente
        and not enc.candidato
        and enc.estado not in ("deteniendo", "detenido", "cancelado")
        and not (directorio / "incorporado").is_file()
    ):
        try:
            ref = json.loads((directorio / "revision.json").read_text(encoding="utf-8"))
            juicio = json.loads(Path(ref["resultado"]).read_text(encoding="utf-8"))
            pendiente = pendiente or juicio.get("clase") in (
                "confirmado",
                "producto_aprobado",
            ) or juicio.get("veredicto") == "aprobado"
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


def _resultado_desde_fold(
    registro: Registro, enc: Encargo, directorio: Path
) -> tuple[dict, dict | None]:
    """Observacion y juicio para la autoridad, desde el log de eventos.

    Ya no depende de que el directorio actual conserve revision.json: el
    fold guarda la revision y la observacion vigentes. El archivo solo se
    lee como respaldo para encargos cerrados por la via anterior.
    """
    eventos = registro.eventos(enc.id)
    revision = next(
        (
            ev["data"]
            for ev in reversed(eventos)
            if ev["kind"] == "revision"
            and (ev["data"].get("origen") or "independiente") == "independiente"
        ),
        None,
    )
    if revision is not None and enc.producto is not None:
        obs_evento = next(
            (
                ev["data"]
                for ev in reversed(eventos)
                if ev["kind"] == "observacion_supervisor"
                and str(ev["data"].get("version") or "") == enc.producto.firma
            ),
            None,
        )
        espacio = _espacio(registro.base, enc.id)
        try:
            solicitudes = _pedidos_efecto(espacio)
        except (ValueError, TypeError):
            solicitudes = []
        observacion = {
            "version": enc.producto.firma,
            "producto": enc.producto.firma,
            "manifiesto": [list(x) for x in enc.producto.manifiesto],
            "ejecuciones": (obs_evento or {}).get("ejecuciones") or [],
            "solicitudes_efecto": solicitudes,
        }
        juicio = {
            "veredicto": revision.get("veredicto"),
            "version": revision.get("version"),
            "evidencia": revision.get("evidencia") or "",
            "hallazgos": revision.get("hallazgos") or [],
            "aprobadas": revision.get("aprobadas") or [],
            "efectos_pendientes": revision.get("efectos_pendientes") or [],
        }
        return observacion, juicio
    try:
        referencia = json.loads(
            (directorio / "revision.json").read_text(encoding="utf-8")
        )
        observacion = json.loads(
            Path(referencia["observaciones"]).read_text(encoding="utf-8")
        )
        juicio = json.loads(Path(referencia["resultado"]).read_text(encoding="utf-8"))
        return observacion, juicio
    except (OSError, ValueError, KeyError):
        return {}, None


def _publicar_resultado(registro: Registro, enc: Encargo, hora: str) -> bool:
    from .transporte import ConfigAutoridadInvalida, cargar, operar

    try:
        cfg = cargar(registro.base)
    except ConfigAutoridadInvalida:
        return False
    if cfg is None:
        return True
    d = _directorio(registro.base, enc.id)
    observacion, juicio = _resultado_desde_fold(registro, enc, d)
    if juicio is None:
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


def _recibir_resultado_nuevo(
    registro: Registro,
    enc: Encargo,
    token: str,
    host: str,
    observaciones: dict,
    juicio: dict,
    hora: str,
) -> dict:
    from .comprobacion import validar_juicio
    from .dominio import VersionProducto

    version = str(observaciones.get("version") or observaciones.get("producto") or "")
    manifiesto = tuple(
        (str(x[0]), str(x[1]))
        for x in (observaciones.get("manifiesto") or ())
        if isinstance(x, (list, tuple)) and len(x) >= 2
    )
    if not version or not manifiesto:
        return {"rechazado": True, "motivo": "el resultado no trae versión del producto"}
    if enc.producto is None or enc.producto.firma != version:
        registro.anotar(
            enc.id,
            {
                "kind": "producto_listo",
                "data": {
                    "version": version,
                    "manifiesto": [list(x) for x in manifiesto],
                    "hora": hora,
                },
            },
            f"producto-autoridad:{enc.id}:{version}",
        )
        enc = registro.fold(enc.id)
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
    alcance = alcance_de(enc)
    try:
        validado = validar_juicio(crudo, alcance)
    except (ValueError, KeyError, TypeError) as exc:
        return {"rechazado": True, "motivo": str(exc)}
    if validado.veredicto != "aprobado":
        return {"rechazado": True, "motivo": "el resultado no fue comprobado"}
    if any(e.estado in ("intencion", "incierto") for e in enc.efectos):
        return {"rechazado": True, "motivo": "la autoridad conserva efectos sin recibo"}
    pedidos = list(observaciones.get("solicitudes_efecto") or [])
    if pedidos and set(validado.efectos_pendientes) != {str(p["id"]) for p in pedidos}:
        return {
            "rechazado": True,
            "motivo": "la aprobacion no enumera los efectos solicitados",
        }
    registro.anotar(
        enc.id,
        {
            "kind": "revision",
            "data": {
                "revision_id": "rev_autoridad_" + version[:12],
                "version": version,
                "base_version": validado.base_version,
                "alcance": validado.alcance,
                "veredicto": validado.veredicto,
                "revisor": "autoridad",
                "origen": "independiente",
                "cubre": list(validado.cubre),
                "hallazgos": [h.como_dict() for h in validado.hallazgos],
                "aprobadas": [list(x) for x in validado.aprobadas],
                "evidencia": validado.evidencia,
                "efectos_pendientes": list(validado.efectos_pendientes),
                "hora": hora,
            },
        },
        f"revision-autoridad:{enc.id}:{version}",
    )
    enc = registro.fold(enc.id)
    from .cierre import comprobar_destino, componer

    try:
        cierre = componer(enc, pedidos, validado.evidencia)
        if pedidos:
            comprobar_destino(cierre, DestinoEnsayo(registro.base / "destino"))
    except (ValueError, KeyError, TypeError) as exc:
        return {"rechazado": True, "motivo": str(exc)}
    resultado = registro.anotar(
        enc.id,
        {
            "kind": "comprobacion",
            "data": {**cierre, "observaciones": observaciones, "host": host, "hora": hora},
        },
        f"resultado:{enc.id}:{token}",
    )
    return {
        "registrado": resultado in ("nuevo", "igual"),
        "encargo": enc.id,
        "estado": registro.fold(enc.id).estado,
    }


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
    from .comprobacion import validar_juicio_legado

    with turno(registro.base):
        enc = registro.fold(encargo_id)
        if (
            enc is None
            or enc.conductor != token
            or enc.host != host
            or enc.huella != pedido
        ):
            return {"conflicto": True, "motivo": "resultado de otro conductor o pedido"}
        if "veredicto" in juicio:
            return _recibir_resultado_nuevo(
                registro, enc, token, host, observaciones, juicio, hora
            )
        try:
            validar_juicio_legado(juicio, observaciones, digest(observaciones))
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
            from .cierre import comprobar_destino, componer_legado

            try:
                cierre = componer_legado(
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
