from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from motor.encargos.ciclo import (
    ahora_iso,
    consultar,
    corregir,
    ordenar,
    recibir,
    resolver_entrega,
)
from motor.encargos.dominio import TransicionIlegal, espera_inspeccion_consumida
from motor.encargos.registro import Registro


def _atomico(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(path.suffix + ".tmp")
    temporal.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporal.replace(path)


def _leer(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def id_aviso(encargo: str, evidencia: str) -> str:
    return "av_" + hashlib.sha256(f"{encargo}:{evidencia}".encode()).hexdigest()[:16]


def nombre_canal(canal: str) -> str:
    if not canal or canal in {".", ".."} or "/" in canal or "\\" in canal or "\0" in canal:
        raise ValueError("el canal es un nombre, no una ruta")
    return canal


def _identidad_normalizada(identidad: dict | None) -> dict:
    if not identidad:
        return {}
    salida: dict = {}
    if identidad.get("pid") is not None and identidad.get("pid") != "":
        try:
            salida["pid"] = int(identidad["pid"])
        except (TypeError, ValueError):
            salida["pid"] = identidad["pid"]
    for campo in ("inicio", "boot_id", "host", "sesion", "job"):
        if identidad.get(campo):
            salida[campo] = str(identidad[campo])
    return salida


def _cierre_observado(sesion: dict | None) -> bool:
    if not sesion:
        return False
    if sesion.get("estado") != "cerrado":
        return False
    if not (sesion.get("observacion") == "ausente" or sesion.get("cierre_observado")):
        return False
    if sesion.get("cierre_pid") is None or sesion.get("pid") is None:
        return False
    try:
        if int(sesion["cierre_pid"]) != int(sesion["pid"]):
            return False
    except (TypeError, ValueError):
        if str(sesion["cierre_pid"]) != str(sesion["pid"]):
            return False
    return str(sesion.get("cierre_inicio") or "") == str(sesion.get("inicio") or "")


def registrar_identidad(
    registro: Registro, encargo: str, identidad: dict | None
) -> dict:
    datos = _identidad_normalizada(identidad)
    path = registro.base / "directo" / f"{encargo}.json"
    actual = _leer(path) or {
        "estado": "sin-identidad",
        "director": "",
        "encargo": encargo,
    }
    if datos:
        cambia = any(
            campo in datos and actual.get(campo) != datos[campo]
            for campo in ("pid", "inicio", "boot_id", "host", "sesion")
        )
        actual.update(datos)
        if cambia:
            actual["estado"] = "activo" if actual.get("pid") else "sin-identidad"
            actual["cierre_observado"] = False
            for marca in ("observacion", "cierre_pid", "cierre_inicio", "motivo"):
                actual.pop(marca, None)
        elif actual.get("estado") in (None, "sin-identidad"):
            actual["estado"] = "activo" if datos.get("pid") else "sin-identidad"
    actual["encargo"] = encargo
    _atomico(path, actual)
    return actual


def _ruta_identidad_canal(registro: Registro, encargo: str) -> Path:
    return registro.base / "canal-identidad" / f"{encargo}.json"


def registrar_identidad_canal(
    registro: Registro,
    encargo: str,
    canal: str,
    director: str,
    sesion: str = "",
) -> dict:
    """La identidad del canal queda fija al nacer el encargo. No se reescribe."""
    path = _ruta_identidad_canal(registro, encargo)
    actual = _leer(path)
    if actual is not None:
        return actual
    doc = {
        "canal": nombre_canal(canal),
        "director": director,
        "sesion": str(sesion or ""),
    }
    _atomico(path, doc)
    return doc


def encargar(
    registro: Registro,
    texto: str,
    clave: str,
    hora: str,
    director: str,
    canal: str,
    via: str,
    adjuntos: tuple[str, ...] = (),
    identidad: dict | None = None,
    entrega_nivel: str = "",
    repo: str = "",
    rama: str = "",
    base_rama: str = "",
    modelos_solicitados: tuple[tuple[str, str, str], ...] = (),
    restricciones: tuple[str, ...] = (),
) -> dict:
    canal = nombre_canal(canal)
    entrega_nivel = resolver_entrega(entrega_nivel, repo, via)
    salida = recibir(
        registro,
        texto,
        clave,
        hora,
        director=director,
        canal=canal,
        via=via,
        adjuntos=adjuntos,
        restricciones=restricciones,
        entrega_nivel=entrega_nivel,
        repo=repo,
        rama=rama,
        base_rama=base_rama,
        modelos_solicitados=modelos_solicitados,
    )
    if via == "directa" and not salida.get("conflicto"):
        path = registro.base / "directo" / f"{salida['encargo']}.json"
        datos = _identidad_normalizada(identidad)
        if not path.exists():
            doc = {
                "estado": "activo" if datos.get("pid") else "sin-identidad",
                "sesion": datos.get("sesion") or clave,
                "director": director,
                "encargo": salida["encargo"],
            }
            doc.update(datos)
            _atomico(path, doc)
        elif datos:
            registrar_identidad(registro, salida["encargo"], datos)
    if not salida.get("conflicto") and salida.get("encargo"):
        sesion = ""
        if identidad:
            sesion = str(identidad.get("sesion") or "")
        registrar_identidad_canal(
            registro, salida["encargo"], canal, director, sesion
        )
    return salida


def cerrar_sesion(registro: Registro, encargo: str) -> dict:
    path = registro.base / "directo" / f"{encargo}.json"
    actual = _leer(path)
    if actual is None:
        return {"encargo": encargo, "estado": "sin-sesion"}
    if not actual.get("pid"):
        actual["estado"] = "sin-identidad"
        actual["cierre_observado"] = False
        actual.pop("cierre_pid", None)
        actual.pop("cierre_inicio", None)
        actual["motivo"] = "sin identidad del ejecutor; no se acredita el cierre"
        _atomico(path, actual)
        return actual
    proc = {
        "pid": int(actual["pid"]),
        "inicio": str(actual.get("inicio") or ""),
        "boot_id": str(actual.get("boot_id") or ""),
        "host": str(actual.get("host") or ""),
        "job": str(actual.get("job") or ""),
    }
    directorio = registro.base / "directo-sesion" / encargo
    directorio.mkdir(parents=True, exist_ok=True)
    _atomico(directorio / "proc.json", proc)
    from motor.encargos.sesion import cerrar as _cerrar_real

    try:
        observacion = _cerrar_real(directorio, proc)
    except Exception as exc:  # noqa: BLE001 el borde de cierre informa, no acredita
        observacion = "incierto"
        actual["motivo"] = str(exc)
    actual["observacion"] = observacion
    if observacion == "ausente":
        actual["estado"] = "cerrado"
        actual["cierre_observado"] = True
        actual["cierre_pid"] = proc["pid"]
        actual["cierre_inicio"] = proc["inicio"]
        actual.pop("motivo", None)
    else:
        actual["estado"] = "activo"
        actual["cierre_observado"] = False
        actual.pop("cierre_pid", None)
        actual.pop("cierre_inicio", None)
        actual["motivo"] = actual.get("motivo") or (
            "el ejecutor sigue vivo" if observacion == "vivo" else "cierre incierto"
        )
    _atomico(path, actual)
    return actual


def _hay_autoridad(registro: Registro) -> bool:
    from motor.encargos.transporte import cargar

    try:
        return cargar(registro.base) is not None
    except Exception:  # noqa: BLE001 hay config ilegible; no se aplica local
        return True


def _pendiente_path(registro: Registro, mensaje: dict) -> Path:
    clave = str(mensaje.get("clave") or mensaje.get("evidencia") or "")
    nombre = hashlib.sha256(
        f"{mensaje.get('op')}:{mensaje.get('encargo')}:{clave}".encode()
    ).hexdigest()[:24]
    return registro.base / "pendientes-autoridad" / f"{nombre}.json"


def _guardar_pendiente(registro: Registro, mensaje: dict) -> Path:
    path = _pendiente_path(registro, mensaje)
    _atomico(path, mensaje)
    return path


def _aplicar_promover_local(
    registro: Registro, encargo: str, clave: str, hora: str
) -> dict:
    from motor.encargos.dominio import recibo_de

    ukey = f"via:{encargo}:durable"
    resultado = registro.anotar(
        encargo,
        {"kind": "via", "data": {"via": "durable", "hora": hora, "clave": clave}},
        ukey,
    )
    salida = consultar(registro, encargo)
    salida["recibo"] = recibo_de(ukey)
    if resultado == "ajena":
        salida["conflicto"] = True
    elif resultado == "ilegal":
        salida["rechazada"] = True
        salida["motivo"] = "no pude pasar a durable"
    elif resultado == "igual":
        salida["retransmision"] = True
    return salida


def aplicar_promover_canonico(
    registro: Registro, encargo: str, clave: str, hora: str
) -> dict:
    enc = registro.fold(encargo)
    if enc is None:
        return {"rechazada": True, "motivo": "no hay encargo", "encargo": encargo}
    return _aplicar_promover_local(registro, encargo, clave, hora)


def _asegurar_producto(
    registro: Registro, encargo: str, texto: str
) -> tuple[str | None, dict | None]:
    espacio = registro.base / "espacio" / encargo
    espacio.mkdir(parents=True, exist_ok=True)
    destino = espacio / "resultado.txt"
    if destino.exists():
        cuerpo = destino.read_text(encoding="utf-8")
        if cuerpo != texto:
            salida = consultar(registro, encargo)
            salida["conflicto"] = True
            salida["motivo"] = "el resultado ya publicado es otro"
            return None, salida
        return cuerpo, None
    temporal = destino.with_suffix(".txt.tmp")
    temporal.write_text(texto, encoding="utf-8")
    temporal.replace(destino)
    return texto, None


def _anotar_resultado_local(
    registro: Registro, encargo: str, evidencia: str, hora: str
) -> dict:
    from motor.encargos.dominio import recibo_de

    ukey = f"resultado-directo:{encargo}:{evidencia}"
    resultado = registro.anotar(
        encargo,
        {
            "kind": "resultado_directo",
            "data": {"evidencia": evidencia, "hora": hora},
        },
        ukey,
    )
    salida = consultar(registro, encargo)
    salida["evidencia"] = evidencia
    salida["recibo"] = recibo_de(ukey)
    if resultado == "igual":
        salida["retransmision"] = True
    elif resultado == "ilegal":
        salida["rechazada"] = True
        salida["motivo"] = "no pude cerrar la via directa"
    elif resultado == "ajena":
        salida["conflicto"] = True
    return salida


def aplicar_resultado_canonico(
    registro: Registro,
    encargo: str,
    texto: str,
    hora: str,
    evidencia_dada: str = "",
) -> dict:
    enc = registro.fold(encargo)
    if enc is None:
        return {"rechazada": True, "motivo": "no hay encargo", "encargo": encargo}
    cuerpo, conflicto = _asegurar_producto(registro, encargo, texto)
    if conflicto is not None:
        return conflicto
    assert cuerpo is not None
    evidencia = hashlib.sha256(cuerpo.encode()).hexdigest()
    if evidencia_dada and evidencia_dada != evidencia:
        salida = consultar(registro, encargo)
        salida["conflicto"] = True
        salida["motivo"] = "la evidencia no coincide con el producto"
        return salida
    return _anotar_resultado_local(registro, encargo, evidencia, hora)


def _enviar_canonico(
    registro: Registro, mensaje: dict
) -> tuple[dict, Path]:
    from motor.encargos.transporte import operar

    path = _guardar_pendiente(registro, mensaje)
    resp = operar(registro.base, mensaje)
    return resp, path


def conciliar_autoridad(registro: Registro) -> dict:
    carpeta = registro.base / "pendientes-autoridad"
    conciliados: list[dict] = []
    pendientes: list[str] = []
    if not carpeta.is_dir():
        return {"conciliados": conciliados, "pendientes": pendientes}
    if not _hay_autoridad(registro):
        return {
            "conciliados": conciliados,
            "pendientes": [str(p) for p in sorted(carpeta.glob("*.json"))],
        }
    from motor.encargos.transporte import operar

    for path in sorted(carpeta.glob("*.json")):
        try:
            mensaje = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pendientes.append(str(path))
            continue
        resp = operar(registro.base, mensaje)
        if not resp.get("alcanzada"):
            pendientes.append(str(path))
            continue
        salida_aut = resp.get("salida") or {}
        encargo = str(mensaje.get("encargo") or "")
        if salida_aut.get("rechazada") or salida_aut.get("conflicto") or salida_aut.get("error"):
            path.unlink(missing_ok=True)
            conciliados.append(
                {"encargo": encargo, "op": mensaje.get("op"), "rechazada": True}
            )
            continue
        if mensaje.get("op") == "promover":
            salida = _aplicar_promover_local(
                registro,
                encargo,
                str(mensaje.get("clave") or ""),
                str(mensaje.get("hora") or ""),
            )
        elif mensaje.get("op") == "resultado_directo":
            salida = aplicar_resultado_canonico(
                registro,
                encargo,
                str(mensaje.get("texto") or ""),
                str(mensaje.get("hora") or ""),
                str(mensaje.get("evidencia") or ""),
            )
        else:
            pendientes.append(str(path))
            continue
        if salida.get("conflicto"):
            pendientes.append(str(path))
            continue
        path.unlink(missing_ok=True)
        conciliados.append(
            {
                "encargo": encargo,
                "op": mensaje.get("op"),
                "recibo": salida.get("recibo") or salida_aut.get("recibo") or "",
                "via": salida.get("via"),
                "estado": salida.get("estado"),
            }
        )
    return {"conciliados": conciliados, "pendientes": pendientes}


def _tiene_pendiente(registro: Registro, encargo: str) -> bool:
    carpeta = registro.base / "pendientes-autoridad"
    if not carpeta.is_dir():
        return False
    for path in sorted(carpeta.glob("*.json")):
        try:
            mensaje = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(mensaje.get("encargo") or "") == encargo:
            return True
    return False


def consultar_canonico(registro: Registro, encargo: str) -> dict:
    if not _hay_autoridad(registro):
        return consultar(registro, encargo)
    if _tiene_pendiente(registro, encargo):
        try:
            conciliar_autoridad(registro)
        except Exception:  # noqa: BLE001 consultar no cae por conciliar
            pass
    local = consultar(registro, encargo) if registro.fold(encargo) is not None else None
    from motor.encargos.transporte import operar

    try:
        resp = operar(registro.base, {"op": "ver", "encargo": encargo})
    except Exception:  # noqa: BLE001 sin respuesta canonica se muestra lo local
        resp = {}
    canon = resp.get("salida") if isinstance(resp, dict) else None
    if not resp.get("alcanzada") or not isinstance(canon, dict) or canon.get("error"):
        if local is not None:
            return local
        return consultar(registro, encargo)
    if local is not None and canon == local:
        return local
    salida = dict(canon)
    salida["fuente"] = "autoridad"
    salida["autoridad"] = True
    return salida


def promover(registro: Registro, encargo: str, clave: str, hora: str) -> dict:
    enc = registro.fold(encargo)
    if enc is None:
        return {"rechazada": True, "motivo": "no hay encargo", "encargo": encargo}
    sesion = _leer(registro.base / "directo" / f"{encargo}.json")
    if enc.via == "directa" and not _cierre_observado(sesion):
        salida = consultar(registro, encargo)
        salida["rechazada"] = True
        if sesion and sesion.get("estado") == "sin-identidad":
            salida["motivo"] = "sin identidad del ejecutor; no se acredita el cierre"
        elif sesion is None:
            salida["motivo"] = "falta el cierre observado de la sesion directa"
        else:
            salida["motivo"] = "el ejecutor directo sigue abierto"
        return salida
    if enc.proc is not None or enc.reserva:
        salida = consultar(registro, encargo)
        salida["rechazada"] = True
        salida["motivo"] = "el ejecutor anterior sigue abierto"
        return salida
    if not _hay_autoridad(registro):
        return _aplicar_promover_local(registro, encargo, clave, hora)
    mensaje = {"op": "promover", "encargo": encargo, "clave": clave, "hora": hora}
    resp, path = _enviar_canonico(registro, mensaje)
    if not resp.get("alcanzada"):
        salida = consultar(registro, encargo)
        salida["pendiente_autoridad"] = True
        salida["pendiente"] = str(path)
        salida["motivo"] = resp.get("motivo") or "autoridad inalcanzable"
        return salida
    salida_aut = resp.get("salida") or {}
    if salida_aut.get("rechazada") or salida_aut.get("conflicto") or salida_aut.get("error"):
        path.unlink(missing_ok=True)
        salida = consultar(registro, encargo)
        for campo in ("rechazada", "conflicto", "error", "motivo"):
            if salida_aut.get(campo):
                salida[campo] = salida_aut[campo]
        return salida
    salida = _aplicar_promover_local(registro, encargo, clave, hora)
    path.unlink(missing_ok=True)
    salida["recibo_autoridad"] = salida_aut.get("recibo") or salida.get("recibo") or ""
    if salida_aut.get("retransmision"):
        salida["retransmision"] = True
    return salida


def resultado_directo(
    registro: Registro, encargo: str, texto: str, hora: str
) -> dict:
    enc = registro.fold(encargo)
    if enc is None:
        return {"rechazada": True, "motivo": "no hay encargo"}
    if enc.via != "directa":
        salida = consultar(registro, encargo)
        salida["rechazada"] = True
        salida["motivo"] = "el resultado directo solo cierra la via directa"
        return salida
    sesion = _leer(registro.base / "directo" / f"{encargo}.json")
    if not _cierre_observado(sesion):
        salida = consultar(registro, encargo)
        salida["rechazada"] = True
        salida["motivo"] = (
            "sin identidad del ejecutor; no se acredita el cierre"
            if sesion and sesion.get("estado") == "sin-identidad"
            else "falta el cierre observado de la sesion directa"
        )
        return salida
    cuerpo, conflicto = _asegurar_producto(registro, encargo, texto)
    if conflicto is not None:
        return conflicto
    assert cuerpo is not None
    evidencia = hashlib.sha256(cuerpo.encode()).hexdigest()
    if not _hay_autoridad(registro):
        return _anotar_resultado_local(registro, encargo, evidencia, hora)
    mensaje = {
        "op": "resultado_directo",
        "encargo": encargo,
        "texto": texto,
        "evidencia": evidencia,
        "clave": f"resultado-directo:{encargo}:{evidencia}",
        "hora": hora,
    }
    resp, path = _enviar_canonico(registro, mensaje)
    if not resp.get("alcanzada"):
        salida = consultar(registro, encargo)
        salida["evidencia"] = evidencia
        salida["pendiente_autoridad"] = True
        salida["pendiente"] = str(path)
        salida["motivo"] = resp.get("motivo") or "autoridad inalcanzable"
        return salida
    salida_aut = resp.get("salida") or {}
    if salida_aut.get("rechazada") or salida_aut.get("conflicto") or salida_aut.get("error"):
        path.unlink(missing_ok=True)
        salida = consultar(registro, encargo)
        salida["evidencia"] = evidencia
        for campo in ("rechazada", "conflicto", "error", "motivo"):
            if salida_aut.get(campo):
                salida[campo] = salida_aut[campo]
        return salida
    salida = _anotar_resultado_local(registro, encargo, evidencia, hora)
    path.unlink(missing_ok=True)
    salida["recibo_autoridad"] = salida_aut.get("recibo") or salida.get("recibo") or ""
    if salida_aut.get("retransmision"):
        salida["retransmision"] = True
    return salida


def _aviso_de(enc) -> dict:
    evidencia = enc.evidencia[-1]
    return {
        "id": id_aviso(enc.id, evidencia),
        "encargo": enc.id,
        "canal": enc.canal,
        "evidencia": evidencia,
        "estado": "pendiente",
        "recibo": "",
        "entrega": {
            "nivel_autorizado": enc.entrega.nivel,
            "nivel_alcanzado": enc.nivel_alcanzado,
            "repo": enc.entrega.repo,
            "pr_numero": enc.pr_numero,
            "pr_sha": enc.pr_sha,
        },
    }


def _cuerpo_del_aviso(registro: Registro, encargo: str, evidencia: str) -> str | None:
    destino = registro.base / "espacio" / encargo / "resultado.txt"
    es_hash = len(evidencia) == 64 and all(
        c in "0123456789abcdef" for c in evidencia
    )
    if destino.is_file():
        cuerpo = destino.read_text(encoding="utf-8")
        if es_hash:
            return cuerpo if hashlib.sha256(cuerpo.encode()).hexdigest() == evidencia else None
        return cuerpo
    if es_hash:
        return None
    return evidencia


def _config_canal(registro: Registro, canal: str) -> dict | None:
    path = registro.base / "canal.json"
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"error": "canal.json ilegible"}
    if not isinstance(doc, dict):
        return None
    cfg = doc.get(canal)
    if not isinstance(cfg, dict) and isinstance(doc.get("canal"), dict):
        cfg = doc["canal"]
    if not isinstance(cfg, dict) and ("comando" in doc or "conciliacion" in doc):
        cfg = doc
    if not isinstance(cfg, dict) or not cfg:
        return None
    return cfg


def _recibo_transporte(texto: str) -> str:
    limpio = (texto or "").strip()
    if not limpio:
        return ""
    try:
        doc = json.loads(limpio)
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        for campo in ("id", "recibo", "receipt"):
            if doc.get(campo):
                return str(doc[campo])
        return ""
    for linea in limpio.splitlines():
        if linea.strip():
            return linea.strip()
    return ""


def _ejecutar_transporte(
    registro: Registro,
    comando: list,
    aviso: dict,
    cuerpo: str,
    con_id: bool = False,
) -> tuple[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "MOTOR_AVISO_ID": str(aviso.get("id") or ""),
            "MOTOR_AVISO_ENCARGO": str(aviso.get("encargo") or ""),
            "MOTOR_AVISO_CANAL": str(aviso.get("canal") or ""),
            "MOTOR_AVISO_EVIDENCIA": str(aviso.get("evidencia") or ""),
            "MOTOR_AVISO_BASE": str(registro.base),
        }
    )
    argv = [str(x) for x in comando]
    if con_id:
        argv.append(str(aviso.get("id") or ""))
    try:
        hecho = subprocess.run(
            argv,
            input=cuerpo.encode("utf-8"),
            capture_output=True,
            timeout=30,
            env=env,
        )
        texto = hecho.stdout.decode("utf-8") if hecho.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        return "", str(exc)
    if hecho.returncode != 0:
        detalle = (hecho.stderr or hecho.stdout).decode("utf-8", errors="replace")[-200:]
        return "", f"el transporte salio {hecho.returncode}: {detalle}"
    recibo = _recibo_transporte(texto)
    if not recibo:
        return "", "el transporte no devolvio recibo"
    return recibo, ""


def _conciliar_transporte(
    registro: Registro, comando: list, aviso: dict, cuerpo: str
) -> tuple[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "MOTOR_AVISO_ID": str(aviso.get("id") or ""),
            "MOTOR_AVISO_ENCARGO": str(aviso.get("encargo") or ""),
            "MOTOR_AVISO_CANAL": str(aviso.get("canal") or ""),
            "MOTOR_AVISO_EVIDENCIA": str(aviso.get("evidencia") or ""),
            "MOTOR_AVISO_BASE": str(registro.base),
        }
    )
    argv = [str(x) for x in comando] + [str(aviso.get("id") or "")]
    try:
        hecho = subprocess.run(
            argv, input=cuerpo.encode("utf-8"), capture_output=True, timeout=30, env=env,
        )
        texto = hecho.stdout.decode("utf-8") if hecho.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        return "incierto", str(exc)
    if hecho.returncode != 0:
        return "incierto", f"la conciliacion salio {hecho.returncode}"
    texto = texto.strip()
    if not texto:
        return "incierto", "la conciliacion no devolvio recibo"
    try:
        doc = json.loads(texto)
    except ValueError:
        return "incierto", "la conciliacion no devolvio un JSON valido"
    if not isinstance(doc, dict):
        return "incierto", "la conciliacion no devolvio un objeto JSON"
    for campo in ("id", "recibo", "receipt"):
        if doc.get(campo):
            return "hallado", str(doc[campo])
    if doc.get("ausente") is True or str(doc.get("estado") or "") in (
        "ausente",
        "no-encontrado",
        "not_found",
    ):
        return "ausente", ""
    return "incierto", "la conciliacion no confirmo el aviso"


def _declara_idempotencia(cfg: dict | None) -> bool:
    if not cfg:
        return False
    if cfg.get("idempotente") is True:
        return True
    marca = cfg.get("idempotencia")
    return marca is True or str(marca or "") == "MOTOR_AVISO_ID"


def _registrar_intento(path: Path, guardado: dict) -> None:
    guardado["estado"] = "incierto"
    try:
        guardado["intentos"] = int(guardado.get("intentos") or 0) + 1
    except (TypeError, ValueError):
        guardado["intentos"] = 1
    _atomico(path, guardado)


def _enviar_aviso(
    registro: Registro, comando: list, guardado: dict, cuerpo: str, path: Path
) -> None:
    _registrar_intento(path, guardado)
    recibo, error = _ejecutar_transporte(registro, comando, guardado, cuerpo)
    if recibo:
        guardado["estado"] = "publicado"
        guardado["alcance"] = "externo"
        guardado["recibo"] = recibo
        guardado.pop("motivo", None)
    else:
        guardado["estado"] = "incierto"
        guardado["alcance"] = "externo"
        guardado["recibo"] = ""
        guardado["motivo"] = error
    _atomico(path, guardado)


def _entregado(aviso: dict) -> bool:
    return bool(
        aviso.get("estado") == "publicado"
        and aviso.get("recibo")
        and aviso.get("alcance") == "externo"
    )


def hechos_de_avance(enc) -> list[dict]:
    """Hechos ya registrados que merecen un aviso. La identidad es el hecho, no el reloj."""
    if enc.estado in ("cancelado", "detenido", "deteniendo"):
        return []
    hechos = []
    fase = enc.fase_actual
    if fase is not None:
        hechos.append(
            {
                "tipo": "fase",
                "identidad": f"{fase.id}:{fase.estado}:{fase.resultado}:{fase.evidencia}",
                "texto": (
                    f"Fase {fase.id} ({fase.estado}). "
                    f"Resultado: {fase.resultado or 'sin resultado todavía'}."
                ),
            }
        )
        if fase.revision_vigente is not None:
            ref = fase.revision_vigente
            hechos.append(
                {
                    "tipo": "revision",
                    "identidad": f"{ref.revision_id}:{ref.veredicto}:{ref.version}",
                    "texto": f"Revisión de {fase.id}: {ref.veredicto} sobre {ref.version}.",
                }
            )
        if fase.pr_numero:
            hechos.append(
                {
                    "tipo": "pr",
                    "identidad": f"{fase.pr_numero}:{fase.pr_sha}",
                    "texto": f"PR #{fase.pr_numero} de {fase.id} en {fase.pr_sha}.",
                }
            )
    for sol in enc.solicitudes:
        if sol.estado != "pregunta":
            continue
        hechos.append(
            {
                "tipo": "pregunta",
                "identidad": sol.id,
                "solicitud": sol.id,
                "texto": (
                    f"Pregunta {sol.id} del encargo {enc.id}, fase {sol.fase}: "
                    f"{sol.pregunta or sol.motivo}"
                ),
            }
        )
    for dec in enc.decisiones:
        hechos.append(
            {
                "tipo": "decision",
                "identidad": dec.id,
                "texto": (
                    f"Decisión: {dec.eleccion}. Motivo: {dec.motivo}. "
                    f"Evidencia: {dec.evidencia or 'sin evidencia'}."
                ),
            }
        )
    if enc.estado == "esperando_reintento" and (enc.motivo or enc.reanuda_en):
        hechos.append(
            {
                "tipo": "espera",
                "identidad": f"reintento:{enc.reanuda_en}:{enc.motivo}",
                "texto": (
                    f"Espera: {enc.motivo or 'servicio'}. "
                    f"Reanuda: {enc.reanuda_en or 'sin hora'}."
                ),
            }
        )
    elif enc.impedimento and (enc.impedimento_causa or enc.impedimento_condicion):
        hechos.append(
            {
                "tipo": "espera",
                "identidad": f"impedimento:{enc.impedimento}:{enc.impedimento_causa}",
                "texto": (
                    f"Espera de {enc.impedimento}: {enc.impedimento_causa}. "
                    f"Condición: {enc.impedimento_condicion}."
                ),
            }
        )
    elif (espera := espera_inspeccion_consumida(enc)) is not None:
        hechos.append(
            {
                "tipo": "espera",
                "identidad": f"impedimento:{espera['clase']}:{espera['causa']}",
                "texto": f"Espera de {espera['clase']}: {espera['causa']}. Condición: {espera['condicion']}.",
            }
        )
    return hechos


def _aviso_de_hecho(enc, hecho: dict) -> dict:
    evidencia = f"avance:{hecho['tipo']}:{hecho['identidad']}"
    aviso = {
        "id": id_aviso(enc.id, evidencia),
        "encargo": enc.id,
        "canal": enc.canal,
        "evidencia": evidencia,
        "estado": "pendiente",
        "recibo": "",
        "tipo": hecho["tipo"],
        "entrega": {
            "nivel_autorizado": enc.entrega.nivel,
            "nivel_alcanzado": enc.nivel_alcanzado,
            "repo": enc.entrega.repo,
            "pr_numero": enc.pr_numero,
            "pr_sha": enc.pr_sha,
        },
    }
    if hecho.get("solicitud"):
        aviso["solicitud"] = hecho["solicitud"]
    return aviso


def _canal_de_origen(
    registro: Registro, encargo: str, canal: str, sesion: str
) -> tuple[str, dict | None]:
    """Sólo el canal y la sesión que dieron origen al encargo hablan por él."""
    enc = registro.fold(encargo)
    if enc is None:
        return "", {"rechazada": True, "motivo": "no hay encargo", "aplicada": False, "encargo": encargo}
    try:
        canal_resp = nombre_canal(canal)
    except ValueError as exc:
        return "", {"rechazada": True, "motivo": str(exc), "aplicada": False, "encargo": encargo}
    identidad = _leer(_ruta_identidad_canal(registro, encargo)) or {}
    origen = str(identidad.get("canal") or enc.canal or "")
    if not origen or canal_resp != origen:
        return canal_resp, {
            "rechazada": True,
            "motivo": "el canal no es el de origen del encargo",
            "aplicada": False,
            "encargo": encargo,
        }
    sesion_origen = str(identidad.get("sesion") or "")
    if sesion_origen and str(sesion or "") != sesion_origen:
        return canal_resp, {
            "rechazada": True,
            "motivo": "la identidad del canal no coincide",
            "aplicada": False,
            "encargo": encargo,
        }
    return canal_resp, None


def corregir_canal(
    registro: Registro,
    encargo: str,
    texto: str,
    clave: str,
    canal: str,
    hora: str,
    sesion: str = "",
) -> dict:
    """Registra la corrección si el canal es el de origen. No interrumpe al agente."""
    canal_corr, rechazo = _canal_de_origen(registro, encargo, canal, sesion)
    if rechazo is not None:
        return rechazo
    salida = corregir(registro, encargo, texto, clave, hora)
    salida["encargo"] = encargo
    salida["canal"] = canal_corr
    return salida


def responder_canal(
    registro: Registro,
    encargo: str,
    solicitud: str,
    texto: str,
    clave: str,
    canal: str,
    hora: str,
    sesion: str = "",
) -> dict:
    """Aplica la respuesta a esa pregunta si el canal es el de origen. No abre otro encargo."""
    canal_resp, rechazo = _canal_de_origen(registro, encargo, canal, sesion)
    if rechazo is not None:
        return rechazo
    from motor.encargos.necesidades import responder

    try:
        salida = responder(
            registro,
            encargo,
            solicitud,
            texto,
            f"canal:{canal_resp}",
            clave,
            hora,
        )
    except TransicionIlegal as exc:
        return {
            "rechazada": True,
            "motivo": str(exc),
            "aplicada": False,
            "encargo": encargo,
        }
    salida["encargo"] = encargo
    salida["canal"] = canal_resp
    if not salida.get("aplicada"):
        salida["tardia"] = True
        salida["motivo"] = (
            salida.get("motivo") or "la respuesta no aplica a una pregunta vigente"
        )
        return salida
    from motor.encargos.ciclo import _directorio, _espacio
    from motor.encargos.necesidades import continuar_respuestas

    actual = registro.fold(encargo)
    if actual is not None:
        salida["continuacion"] = continuar_respuestas(
            registro,
            actual,
            _espacio(registro.base, encargo),
            _directorio(registro.base, encargo),
            hora,
        )
    return salida


def _cerrar_aviso(guardado: dict, novedad: bool) -> dict:
    guardado["entregado"] = _entregado(guardado)
    guardado["novedad"] = novedad
    if not guardado["entregado"]:
        guardado["nota"] = (
            "un archivo local no es un mensaje entregado al canal"
            if guardado.get("alcance") == "local" or guardado.get("estado") == "local-pendiente"
            else guardado.get("nota") or ""
        )
        if not guardado["nota"]:
            guardado.pop("nota", None)
    return guardado


def _publicar_aviso(
    registro: Registro, aviso: dict, cuerpo: str | None, cortar: str
) -> dict:
    """Misma recuperación de recibo para el cierre y para el avance. Sin modelo."""
    path = registro.base / "avisos" / f"{aviso['id']}.json"
    guardado = _leer(path) or dict(aviso)
    if guardado.get("estado") == "publicado" and guardado.get("recibo"):
        return _cerrar_aviso(guardado, False)
    if guardado.get("estado") == "local-pendiente" and guardado.get("alcance") == "local":
        try:
            nombre_previo = nombre_canal(str(guardado.get("canal") or ""))
        except ValueError:
            nombre_previo = ""
        cfg_previo = _config_canal(registro, nombre_previo) if nombre_previo else None
        comando_previo = (
            list(cfg_previo.get("comando") or [])
            if cfg_previo and not cfg_previo.get("error")
            else []
        )
        if not comando_previo:
            return _cerrar_aviso(guardado, False)
    try:
        nombre = nombre_canal(str(guardado["canal"]))
    except ValueError:
        guardado["estado"] = "incierto"
        guardado["motivo"] = "el canal no es un nombre"
        _atomico(path, guardado)
        return _cerrar_aviso(guardado, True)
    canal_dir = registro.base / "canal" / nombre
    entrega = canal_dir / guardado["id"]
    if cuerpo is None:
        guardado["estado"] = "incierto"
        guardado["motivo"] = "no hay un resultado legible que concuerde con la evidencia"
        _atomico(path, guardado)
        return _cerrar_aviso(guardado, True)
    try:
        intentos = int(guardado.get("intentos") or 0)
    except (TypeError, ValueError):
        intentos = 0
    ya_enviado = guardado.get("estado") == "incierto" or intentos > 0
    if ya_enviado:
        guardado["estado"] = "incierto"
    cfg = _config_canal(registro, nombre)
    if cfg and cfg.get("error"):
        if ya_enviado:
            guardado["estado"] = "incierto"
            guardado["motivo"] = str(cfg["error"])
        else:
            guardado["estado"] = "local-pendiente"
            guardado["alcance"] = "local"
            guardado["recibo"] = ""
            guardado["motivo"] = str(cfg["error"])
        _atomico(path, guardado)
        return _cerrar_aviso(guardado, True)
    comando = list(cfg.get("comando") or []) if cfg else []
    conciliacion = list(cfg.get("conciliacion") or []) if cfg else []
    if guardado.get("estado") == "incierto":
        if not comando:
            guardado["motivo"] = "sin transporte configurado; no se reenvia"
            _atomico(path, guardado)
            return _cerrar_aviso(guardado, True)
        if _declara_idempotencia(cfg):
            _enviar_aviso(registro, comando, guardado, cuerpo, path)
            return _cerrar_aviso(guardado, True)
        if not conciliacion:
            guardado["motivo"] = "aviso incierto sin conciliacion; no se reenvia"
            _atomico(path, guardado)
            return _cerrar_aviso(guardado, True)
        resultado_con, dato = _conciliar_transporte(
            registro, conciliacion, guardado, cuerpo
        )
        if resultado_con == "hallado":
            guardado["estado"] = "publicado"
            guardado["alcance"] = "externo"
            guardado["recibo"] = dato
            guardado.pop("motivo", None)
            _atomico(path, guardado)
            return _cerrar_aviso(guardado, True)
        if resultado_con == "incierto":
            guardado["motivo"] = dato
            _atomico(path, guardado)
            return _cerrar_aviso(guardado, True)
        _enviar_aviso(registro, comando, guardado, cuerpo, path)
        return _cerrar_aviso(guardado, True)
    if cortar == "antes" and not entrega.exists():
        guardado["estado"] = "pendiente"
        _atomico(path, guardado)
        return _cerrar_aviso(guardado, True)
    if not entrega.exists():
        canal_dir.mkdir(parents=True, exist_ok=True)
        temporal = entrega.with_suffix(".tmp")
        temporal.write_text(cuerpo, encoding="utf-8")
        temporal.replace(entrega)
    if entrega.read_text(encoding="utf-8") != cuerpo:
        guardado["estado"] = "incierto"
        guardado["motivo"] = "el canal tiene otro contenido y no se puede conciliar"
        _atomico(path, guardado)
        return _cerrar_aviso(guardado, True)
    if cortar == "despues" and not guardado.get("recibo"):
        if comando:
            _registrar_intento(path, guardado)
            _ejecutar_transporte(registro, comando, guardado, cuerpo)
            guardado["estado"] = "incierto"
            guardado["alcance"] = "externo"
            guardado["motivo"] = "envio hecho sin recibo; falta conciliar"
        else:
            guardado["estado"] = "local-pendiente"
            guardado["alcance"] = "local"
            guardado["recibo"] = ""
        _atomico(path, guardado)
        return _cerrar_aviso(guardado, True)
    if not comando:
        guardado["estado"] = "local-pendiente"
        guardado["alcance"] = "local"
        guardado["recibo"] = ""
        _atomico(path, guardado)
        return _cerrar_aviso(guardado, True)
    _enviar_aviso(registro, comando, guardado, cuerpo, path)
    return _cerrar_aviso(guardado, True)


def drenar(registro: Registro, cortar: str = "") -> dict:
    publicados = []
    corte = registro.corte_avisos()
    for encargo in registro.ids():
        enc = registro.fold(encargo)
        if enc is None or not enc.canal:
            continue
        anterior = registro.fold(encargo, hasta=corte)
        historicos = {
            _aviso_de_hecho(anterior, hecho)["id"]
            for hecho in hechos_de_avance(anterior) if hecho["tipo"] in (
                "fase", "revision", "pr", "decision"
            )
        } if anterior is not None else set()
        trabajos: list[tuple[dict, str | None]] = []
        if enc.estado == "terminado" and enc.evidencia:
            aviso = _aviso_de(enc)
            trabajos.append(
                (aviso, _cuerpo_del_aviso(registro, encargo, str(aviso["evidencia"])))
            )
        for hecho in hechos_de_avance(enc):
            aviso = _aviso_de_hecho(enc, hecho)
            if aviso["id"] in historicos and not (
                registro.base / "avisos" / f"{aviso['id']}.json"
            ).exists():
                continue
            trabajos.append((aviso, hecho["texto"]))
        for aviso, cuerpo in trabajos:
            publicados.append(_publicar_aviso(registro, aviso, cuerpo, cortar))
    return {"avisos": publicados}


def capacidades_openbot(argv: list[str]) -> dict:
    ayuda = subprocess.run(argv + ["--help"], capture_output=True, text=True)
    cancelar = subprocess.run(argv + ["cancel"], capture_output=True, text=True)
    texto = "\n".join(
        parte for parte in (ayuda.stdout, ayuda.stderr, cancelar.stdout, cancelar.stderr) if parte
    )
    parada = cancelar.returncode == 0 and "Usage" not in texto
    return {
        "entrega": "openbot-cli run" in texto and "--id" in texto,
        "consulta": "openbot-cli status" in texto,
        "parada": parada,
        "timeout_cancela": "the work continues" not in texto,
        "skills": False,
        "publicacion_de_sesion": False,
        "recepcion": any(
            marca in texto for marca in ("reply", "responder", "inbound")
        ),
        "rc_ayuda": ayuda.returncode,
        "rc_cancelar": cancelar.returncode,
    }


def guardar_capacidades(base: Path, doc: dict) -> None:
    _atomico(base / "politica" / "openbot.json", doc)


def relay(
    registro: Registro, encargo: str, accion: str, clave: str, hora: str
) -> dict:
    enc = registro.fold(encargo)
    if enc is None:
        return {"rechazada": True, "motivo": "no hay encargo", "encargo": encargo}
    if accion == "parar" and enc.director == "openbot":
        caps = _leer(registro.base / "politica" / "openbot.json") or {}
        if not caps.get("parada"):
            salida = consultar(registro, encargo)
            salida["parada_ofrecida"] = False
            salida["motivo"] = "OpenBot no tiene parada demostrada; el trabajo sigue"
            return salida
    return ordenar(registro, encargo, accion, clave, hora)


def parar(registro: Registro, encargo: str, clave: str, hora: str) -> dict:
    return ordenar(registro, encargo, "parar", clave, hora)


def hora() -> str:
    return ahora_iso()
