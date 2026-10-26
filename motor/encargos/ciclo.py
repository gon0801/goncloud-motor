"""Ciclo de vida del encargo. No decide el trabajo de la aplicación."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import uuid
from pathlib import Path

from .dominio import (
    Encargo,
    TransicionIlegal,
    clasificar_motor06,
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
    autoridad_ausente,
    despachar,
    leer_parada_local,
)
from .registro import Registro
from .sesion import cerrar, crecer_log, herramienta_en_curso, lanzar, observar

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
) -> dict:
    huella = huella_entrega(texto, adjuntos, restricciones)
    ukey = f"entrega:{clave}"
    prev = registro.por_ukey(ukey)
    if prev:
        enc = registro.fold(prev["encargo"])
        salida = vista(enc, prev["data"]["recibo"])
        if prev["data"]["huella"] != huella:
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
            "recibo": recibo,
            "hora": hora,
        },
        ukey,
        hora,
    )
    return vista(registro.fold(encargo), recibo)


def _recibo_previo(registro: Registro, encargo_id: str, ukey: str, accion: str) -> dict | None:
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


def ordenar(registro: Registro, encargo_id: str, accion: str, clave: str, hora: str) -> dict:
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


def corregir(registro: Registro, encargo_id: str, texto: str, clave: str, hora: str) -> dict:
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


def reparar_motor06(
    registro: Registro,
    encargo_id: str,
    producto: Path,
    salida_grep: str,
    efecto_id: str,
    destino: DestinoEnsayo,
    hora: str,
) -> dict:
    """Regresión de MOTOR 06: el grep malo no toca el producto ni repite el efecto."""
    antes = Path(producto).read_bytes()
    toml_text = antes.decode()
    juicio = clasificar_motor06(salida_grep, toml_text)
    if juicio["clase"] != "instrumento":
        raise TransicionIlegal("esa salida no es el instrumento de MOTOR 06")
    registro.anotar(
        encargo_id,
        {
            "kind": "comprobacion",
            "data": {**juicio, "hora": hora},
        },
        f"comprobacion:{encargo_id}:{efecto_id}:instrumento:{juicio['firma']}",
    )
    if Path(producto).read_bytes() != antes:
        raise TransicionIlegal("el producto cambió al reparar el instrumento")
    efecto = despachar(
        registro,
        encargo_id,
        destino,
        efecto_id,
        "merge",
        f"destino:{efecto_id}",
        "ya integrado",
        hora,
    )
    if not efecto.get("ya_aplicado") and not efecto.get("conciliado"):
        raise TransicionIlegal("el efecto ya aplicado volvió a despacharse")
    reparado = clasificar_motor06("", toml_text)
    if reparado["clase"] != "confirmado":
        raise TransicionIlegal("el parser no confirmó el producto")
    registro.anotar(
        encargo_id,
        {"kind": "comprobacion", "data": {**reparado, "hora": hora}},
        f"comprobacion:{encargo_id}:{efecto_id}:toml",
    )
    if Path(producto).read_bytes() != antes:
        raise TransicionIlegal("el producto cambió al confirmar")
    return vista(registro.fold(encargo_id))


def firma_espacio(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    for candidato in sorted(path.rglob("*")):
        if not candidato.is_file():
            continue
        if ".git" in candidato.parts or candidato.name in ("log.txt", "stderr.txt"):
            continue
        h.update(candidato.relative_to(path).as_posix().encode())
        h.update(candidato.read_bytes())
    return h.hexdigest()


def _directorio(base: Path, encargo_id: str) -> Path:
    path = base / "ejecucion" / encargo_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _espacio(base: Path, encargo_id: str) -> Path:
    path = base / "espacio" / encargo_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _guardar_proc(directorio: Path, proc: dict) -> None:
    tmp = directorio / "proc.json.tmp"
    tmp.write_text(json.dumps(proc), encoding="utf-8")
    os.replace(tmp, directorio / "proc.json")


def _leer_proc(directorio: Path) -> dict | None:
    path = directorio / "proc.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _aplicar(registro: Registro, encargo_id: str, eventos: list[dict], prefijo: str) -> None:
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
        if cambio and registro.por_ukey(f"estrategia:{encargo_id}:{enc.firma_fallo}") is None:
            registro.anotar(encargo_id, cambio, f"estrategia:{encargo_id}:{enc.firma_fallo}")
            enc = registro.fold(encargo_id)
        directorio = _directorio(base, encargo_id)
        espacio = _espacio(base, encargo_id)
        proc = enc.proc.como_dict() if enc.proc else _leer_proc(directorio)
        if enc.proc is None and proc and observar(directorio, proc) == "vivo":
            registro.anotar(
                encargo_id,
                {
                    "kind": "intento_abierto",
                    "data": {
                        "id": enc.intento or "adoptado",
                        "pid": proc["pid"],
                        "inicio": proc["inicio"],
                        "boot_id": proc.get("boot_id") or "",
                        "job": proc.get("job") or "",
                        "sesion": enc.sesion,
                        "herramientas_observables": enc.herramientas_observables,
                        "hora": hora,
                    },
                },
                f"intento:{encargo_id}:{proc['pid']}:{proc['inicio']}",
            )
            enc = registro.fold(encargo_id)
            proc = enc.proc.como_dict() if enc.proc else proc
        arbol = observar(directorio, proc if enc.proc or proc else None)
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
        if enc.reserva and enc.proc is None and arbol != "vivo" and _edad(enc, epoch) >= 30:
            registro.anotar(
                encargo_id,
                {"kind": "reserva_cerrada", "data": {"hora": hora}},
                f"reserva-cerrada:{encargo_id}:{enc.generacion}",
            )
            enc = registro.fold(encargo_id)
        if (
            arbol == "ausente"
            and enc.proc is not None
            and enc.escrituras
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
            enc = _incorporar(registro, enc, espacio, hora)
        if enc.estado == "terminado" and enc.proc is not None and arbol == "ausente":
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
            _aplicar(registro, encargo_id, decision["eventos"], f"paso:{encargo_id}:{hora}:{arbol}")
            enc = registro.fold(encargo_id)
        if decision["lanzar"] and registro.por_ukey(
            f"comprobacion:{encargo_id}:no:{(firma or '')[:16]}"
        ):
            decision["lanzar"] = False
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


def _lineas_comprobar(enc: Encargo) -> list[str]:
    comandos = []
    for bloque in (enc.texto, *enc.restricciones, *enc.ajustes):
        for linea in bloque.splitlines():
            if linea.startswith("comprobar:"):
                comando = linea.split(":", 1)[1].strip()
                if comando:
                    comandos.append(comando)
    return comandos


def _juicio_contiene(espacio: Path, nombre: str, esperado: str) -> dict:
    firma = "contiene:" + hashlib.sha256(f"{nombre}:{esperado}".encode()).hexdigest()[:12]
    ruta = (espacio / nombre).resolve()
    try:
        ruta.relative_to(espacio.resolve())
    except ValueError:
        return {"clase": "defecto", "causa": "la ruta sale del espacio", "firma": firma}
    if not ruta.is_file():
        return {"clase": "defecto", "causa": f"no está {nombre}", "firma": firma}
    contenido = ruta.read_text(encoding="utf-8", errors="replace")
    if esperado in contenido:
        return {"clase": "confirmado", "evidencia": f"{nombre}={contenido}", "firma": firma}
    return {"clase": "defecto", "causa": f"{nombre} no contiene {esperado}", "firma": firma}


def _juicio_comando(espacio: Path, comando: str) -> dict:
    firma = "cmd:" + hashlib.sha256(comando.encode()).hexdigest()[:12]
    try:
        argv = shlex.split(comando)
    except ValueError as exc:
        return {
            "clase": "instrumento",
            "causa": str(exc),
            "reparacion": "revisar el comando del pedido",
            "firma": firma,
        }
    if not argv:
        return {
            "clase": "instrumento",
            "causa": "comando vacio",
            "reparacion": "escribir el comando",
            "firma": firma,
        }
    try:
        corrida = subprocess.run(
            argv,
            cwd=espacio,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return {
            "clase": "instrumento",
            "causa": f"no existe {argv[0]}",
            "reparacion": "usar un comando que exista",
            "firma": firma,
        }
    except subprocess.TimeoutExpired:
        return {
            "clase": "instrumento",
            "causa": "la comprobacion no termino",
            "reparacion": "acotar la comprobacion",
            "firma": firma,
        }
    if corrida.returncode != 0:
        detalle = (corrida.stderr or corrida.stdout or "la comprobacion fallo").strip()
        return {"clase": "defecto", "causa": detalle[-400:], "firma": firma}
    return {"clase": "confirmado", "evidencia": f"{comando} rc=0", "firma": firma}


def _pedidos_efecto(espacio: Path) -> list[dict] | None:
    path = espacio / "pedido-efecto.json"
    if not path.exists():
        return []
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(datos, dict):
        return [datos]
    if isinstance(datos, list):
        return [item for item in datos if isinstance(item, dict)]
    return None


def _anotar_juicio(registro: Registro, enc: Encargo, juicio: dict, hora: str, serie: int) -> None:
    registro.anotar(
        enc.id,
        {"kind": "comprobacion", "data": {**juicio, "hora": hora}},
        f"comprobacion:{enc.id}:{juicio['clase']}:{juicio.get('firma') or juicio.get('motivo')}:{serie}",
    )


def _incorporar(registro: Registro, enc: Encargo, espacio: Path, hora: str) -> Encargo:
    """El supervisor lee el espacio. Una frase del agente no cierra el encargo."""
    ev_path = espacio / "evidencia-comportamiento.txt"
    texto = ev_path.read_text(encoding="utf-8", errors="replace") if ev_path.is_file() else ""
    if texto.strip():
        registro.anotar(
            enc.id,
            {"kind": "afirmacion", "data": {"texto": texto.strip()[:2000], "hora": hora}},
            f"afirmacion:{enc.id}:{hashlib.sha256(texto.encode()).hexdigest()[:12]}",
        )
    juicios = []
    for linea in texto.splitlines():
        limpia = linea.strip()
        if " contiene " not in limpia:
            continue
        nombre, _, esperado = limpia.partition(" contiene ")
        if nombre.strip() and esperado.strip():
            juicios.append(_juicio_contiene(espacio, nombre.strip(), esperado.strip()))
    for comando in _lineas_comprobar(enc):
        juicios.append(_juicio_comando(espacio, comando))
    if not juicios:
        firma = firma_espacio(espacio)
        registro.anotar(
            enc.id,
            {
                "kind": "comprobacion",
                "data": {
                    "clase": "no_concluyente",
                    "motivo": "el trabajador termino sin un reclamo verificable",
                    "firma": firma,
                    "hora": hora,
                },
            },
            f"comprobacion:{enc.id}:no:{(firma or '')[:16]}",
        )
        return registro.fold(enc.id)
    malos = [juicio for juicio in juicios if juicio["clase"] != "confirmado"]
    if malos:
        serie = enc.repeticiones
        for juicio in malos:
            serie += 1
            _anotar_juicio(registro, enc, juicio, hora, serie)
        return registro.fold(enc.id)
    pedidos = _pedidos_efecto(espacio)
    if pedidos is None:
        _anotar_juicio(
            registro,
            enc,
            {
                "clase": "instrumento",
                "causa": "pedido-efecto.json no se pudo leer",
                "reparacion": "escribir un objeto con id",
                "firma": "pedido-efecto",
            },
            hora,
            enc.repeticiones + 1,
        )
        return registro.fold(enc.id)
    destino = DestinoEnsayo(registro.base / "destino")
    for pedido in pedidos:
        efecto_id = str(pedido.get("id") or "")
        if not efecto_id:
            continue
        resultado = despachar(
            registro,
            enc.id,
            destino,
            efecto_id,
            str(pedido.get("operacion") or "publicar"),
            str(pedido.get("clave") or efecto_id),
            str(pedido.get("cuerpo") or ""),
            hora,
        )
        if resultado.get("rechazado") or resultado.get("enviado") is False:
            _anotar_juicio(
                registro,
                enc,
                {
                    "clase": "instrumento",
                    "causa": resultado.get("motivo") or "el efecto no se envio",
                    "reparacion": "repetir el efecto cuando haya autorizacion",
                    "firma": f"efecto:{efecto_id}",
                },
                hora,
                enc.repeticiones + 1,
            )
            return registro.fold(enc.id)
    for indice, juicio in enumerate(juicios, start=1):
        _anotar_juicio(registro, enc, juicio, hora, indice)
    return registro.fold(enc.id)


def _es_uuid(valor: str) -> bool:
    try:
        uuid.UUID(valor)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _lanzar_uno(registro, enc: Encargo, directorio: Path, espacio: Path, hora: str, lanzador) -> None:
    if observar(directorio, enc.proc.como_dict() if enc.proc else _leer_proc(directorio)) == "vivo":
        return
    enc = registro.fold(enc.id) or enc
    if enc.proc is not None or enc.reserva:
        return
    rol = (
        "revisor"
        if enc.estrategia == "revision_tecnica" and enc.rol != "revisor"
        else (enc.rol or "responsable")
    )
    generacion = enc.generacion + 1
    reservado = registro.anotar(
        enc.id,
        {
            "kind": "reserva",
            "data": {"rol": rol, "generacion": generacion, "hora": hora},
        },
        f"reserva:{enc.id}:{generacion}",
    )
    if reservado != "nuevo":
        return
    enc = registro.fold(enc.id) or enc
    sesion = enc.sesion if _es_uuid(enc.sesion) else str(uuid.uuid5(uuid.NAMESPACE_URL, enc.id))
    prompt = directorio / "prompt.md"
    prompt.write_text(prompt_de(enc), encoding="utf-8")
    cmd = lanzador(enc, prompt, espacio, sesion)
    if not cmd:
        return
    log = directorio / "log.txt"
    proc = lanzar(cmd, espacio, log)
    proc["firma"] = firma_espacio(espacio)
    _guardar_proc(directorio, proc)
    observable = Path(str(cmd[0])).name not in {"grok", "claude", "codex"}
    anotado = registro.anotar(
        enc.id,
        {
            "kind": "intento_abierto",
            "data": {
                "id": f"int-{proc['pid']}",
                "pid": proc["pid"],
                "inicio": proc.get("inicio") or "",
                "boot_id": proc.get("boot_id") or "",
                "job": proc.get("job") or "",
                "sesion": sesion,
                "rol": enc.rol,
                "herramientas_observables": observable,
                "firma": proc["firma"],
                "hora": hora,
            },
        },
        f"intento:{enc.id}:{proc['pid']}:{proc.get('inicio')}",
    )
    if anotado == "ilegal":
        from motor import plataforma

        plataforma.matar_proc(int(proc["pid"]), str(proc.get("inicio") or ""))
        plataforma.cerrar_huerfanos(
            int(proc["pid"]), str(proc.get("inicio") or ""), str(proc.get("job") or "")
        )


def marcar_espera(registro: Registro, encargo_id: str, motivo: str, reanuda_en: str, hora: str) -> dict:
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
