from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from motor.encargos.ciclo import (
    ahora_iso,
    consultar,
    corregir,
    ordenar,
    recibir,
    resolver_entrega,
)
from motor.encargos.dominio import TransicionIlegal, espera_inspeccion_consumida, reducir
from motor.encargos.registro import Registro
from motor.integraciones import canales


def _atomico(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(path.suffix + ".tmp")
    temporal.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporal.replace(path)


def _leer(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


ESPERA_BASE_S = 60
ESPERA_TOPE_S = 3600
TRANSPORTE_TIMEOUT_S = 30.0
TOPE_DRENAR_S = 20.0


def espera_s(fallos: int) -> int:
    """1, 2, 4... minutos entre intentos de un mismo aviso, con tope de una hora."""
    return min(ESPERA_BASE_S * 2 ** min(max(fallos - 1, 0), 20), ESPERA_TOPE_S)


def _epoch(hora: str) -> float | None:
    try:
        return datetime.fromisoformat(str(hora).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    recibo, error, _ = _correr_transporte(registro, comando, aviso, cuerpo, con_id)
    return recibo, error


def _correr_transporte(
    registro: Registro,
    comando: list,
    aviso: dict,
    cuerpo: str,
    con_id: bool = False,
) -> tuple[str, str, bool]:
    """(recibo, error, canal_caido). Solo un proceso que no corre, que no responde o que sale con error
    marca el canal caído; una salida ilegible o sin recibo es un fallo de ese mensaje."""
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
            timeout=TRANSPORTE_TIMEOUT_S,
            env=env,
        )
        texto = hecho.stdout.decode("utf-8") if hecho.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc), True
    except UnicodeDecodeError as exc:
        return "", str(exc), False
    if hecho.returncode != 0:
        detalle = (hecho.stderr or hecho.stdout).decode("utf-8", errors="replace")[-200:]
        return "", f"el transporte salio {hecho.returncode}: {detalle}", True
    recibo = _recibo_transporte(texto)
    if not recibo:
        return "", "el transporte no devolvio recibo", False
    return recibo, "", False


def _conciliar_transporte(
    registro: Registro, comando: list, aviso: dict, cuerpo: str
) -> tuple[str, str]:
    estado, dato, _ = _correr_conciliacion(registro, comando, aviso, cuerpo)
    return estado, dato


def _correr_conciliacion(
    registro: Registro,
    comando: list,
    aviso: dict,
    cuerpo: str,
) -> tuple[str, str, bool]:
    """(estado, dato, canal_caido), con el mismo criterio de canal caído que `_correr_transporte`."""
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
            argv, input=cuerpo.encode("utf-8"), capture_output=True, timeout=TRANSPORTE_TIMEOUT_S, env=env,
        )
        texto = hecho.stdout.decode("utf-8") if hecho.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "incierto", str(exc), True
    except UnicodeDecodeError as exc:
        return "incierto", str(exc), False
    if hecho.returncode != 0:
        return "incierto", f"la conciliacion salio {hecho.returncode}", True
    texto = texto.strip()
    if not texto:
        return "incierto", "la conciliacion no devolvio recibo", False
    try:
        doc = json.loads(texto)
    except ValueError:
        return "incierto", "la conciliacion no devolvio un JSON valido", False
    if not isinstance(doc, dict):
        return "incierto", "la conciliacion no devolvio un objeto JSON", False
    for campo in ("id", "recibo", "receipt"):
        if doc.get(campo):
            return "hallado", str(doc[campo]), False
    if doc.get("ausente") is True or str(doc.get("estado") or "") in (
        "ausente",
        "no-encontrado",
        "not_found",
    ):
        return "ausente", "", False
    return "incierto", "la conciliacion no confirmo el aviso", False


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
        previos = int(guardado.get("intentos") or 0)
    except (TypeError, ValueError):
        previos = 0
    guardado.setdefault("fallos", previos)
    guardado["intentos"] = previos + 1
    _atomico(path, guardado)


def _diferir(guardado: dict, ahora: float) -> None:
    """El próximo intento de un aviso que no llegó toca tras una espera que crece con cada fallo."""
    try:
        previos = int(guardado.get("fallos", guardado.get("intentos")) or 0)
    except (TypeError, ValueError):
        previos = 0
    guardado["fallos"] = previos + 1
    guardado["proximo_intento"] = _iso(ahora + espera_s(guardado["fallos"]))


def _por_esperar(guardado: dict, ahora: float) -> bool:
    toca = _epoch(guardado.get("proximo_intento") or "")
    return toca is not None and ahora < toca


def _enviar_aviso(
    registro: Registro,
    comando: list,
    guardado: dict,
    cuerpo: str,
    path: Path,
    vuelta: "Vuelta",
) -> None:
    """Un envío. Si el transporte no da recibo, difiere el aviso y corta el canal en esta vuelta."""
    _registrar_intento(path, guardado)
    recibo, error, caido = _correr_transporte(registro, comando, guardado, cuerpo)
    if recibo:
        guardado["estado"] = "publicado"
        guardado["alcance"] = "externo"
        guardado["recibo"] = recibo
        vuelta.respondieron.add(guardado["canal"])
        for campo in ("motivo", "proximo_intento", "fallos"):
            guardado.pop(campo, None)
    else:
        guardado["estado"] = "incierto"
        guardado["alcance"] = "externo"
        guardado["recibo"] = ""
        guardado["motivo"] = error
        _diferir(guardado, vuelta.ahora)
        if caido:
            vuelta.fallo_el_transporte(guardado["canal"], error)
    _atomico(path, guardado)


def _entregado(aviso: dict) -> bool:
    return bool(
        aviso.get("estado") == "publicado"
        and aviso.get("recibo")
        and aviso.get("alcance") == "externo"
    )


REMISION = "El detalle técnico está en el estado del encargo."

_REPETIDO = re.compile(r"\b(un archivo|un comando|un identificador interno|una versión)(?:(?:,| y) \1)+")
_SUSTITUCIONES = (
    (re.compile(r"`[^`]*`"), lambda m: "un archivo" if re.search(r"[/\\]|\.\w{1,4}\b", m.group()) else "un comando"),
    (re.compile(r"\b(?:enc|av|p|h|f|s)_[0-9a-f]{6,}\b"), lambda m: "un identificador interno"),
    (re.compile(r"[\w.~-]*(?:[/\\][\w.~-]+)+"), lambda m: "un archivo"),
    (re.compile(r"\b[\w-]+\.(?:py|json|md|txt|yml|yaml|toml|sh|ts|js|log)\b"), lambda m: "un archivo"),
    (re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}\b"), lambda m: "una versión"),
)


def _llano(texto: str, tope: int = 240) -> tuple[str, bool]:
    """Una frase de un agente para una persona no técnica y si traía detalle técnico.

    Cada archivo, comando, id o hash se cambia por su nombre común, así la frase conserva su sentido
    ("¿Uso la cuenta de un archivo?") en vez de quedar mutilada. Quien la escribe añade `REMISION`
    cuando hubo cambios, para que el detalle se lea donde está.
    """
    t, tecnico = str(texto or ""), False
    for patron, cambio in _SUSTITUCIONES:
        t, n = patron.subn(cambio, t)
        tecnico = tecnico or n > 0
    t = _REPETIDO.sub(r"\1", re.sub(r"\s+", " ", t)).strip(" .,;:-")
    return (t if len(t) <= tope else t[: tope - 1].rstrip() + "…"), tecnico


def _con_remision(frase: str, tecnico: bool) -> str:
    return f"{frase} {REMISION}" if tecnico else frase


def _apariciones_de_impedimentos(registro: Registro, encargo: str) -> dict[str, int]:
    """Cuántas veces se trabó el encargo por cada clase: cada paso de "no pendiente" a "pendiente" cuenta una."""
    estado, previas, cuenta = None, set(), {}
    for evento in registro.eventos(encargo):
        estado = reducir(estado, evento)
        ahora = {i.clase for i in estado.impedimentos}
        for clase in ahora - previas:
            cuenta[clase] = cuenta.get(clase, 0) + 1
        previas = ahora
    return cuenta


def hechos_de_avance(enc, apariciones: dict[str, int] | None = None) -> list[dict]:
    """Hechos ya registrados que merecen un aviso a una persona no técnica. La identidad es el hecho.

    Van: el encargo arrancó, necesita una decisión, el PR está listo para decidir el merge y se trabó.
    El cierre es otro aviso (`_aviso_de`). Decisiones del agente, esperas de reintento, revisiones,
    comprobaciones y fases intermedias quedan en el registro y no salen al canal.

    Un trabado se identifica por su clase y por la vez que el encargo se trabó con ella (`apariciones`):
    el texto que cambia dentro de un mismo bloqueo no repite el aviso, y un bloqueo nuevo de la misma
    clase, tras resolverse el anterior, sí lo avisa.
    """
    if enc.estado in ("cancelado", "detenido", "deteniendo", "terminado"):
        return []
    hechos = []
    fase = enc.fase_actual
    if fase is not None or enc.estado in ("ejecutando", "esperando_reintento", "conciliando"):
        hechos.append(
            {"tipo": "inicio", "identidad": "inicio", "texto": "Empecé a trabajar en tu encargo."}
        )
    if fase is not None and fase.pr_numero and fase.nivel_alcanzado == "pr":
        hechos.append(
            {
                "tipo": "pr",
                "identidad": str(fase.pr_numero),
                "texto": f"El PR #{fase.pr_numero} está listo para que decidas si lo integras.",
            }
        )
    preguntadas = set()
    for sol in enc.solicitudes:
        if sol.estado != "pregunta":
            continue
        pregunta, tecnico = _llano(sol.pregunta or sol.motivo)
        preguntadas.update(t for t in (sol.pregunta, sol.motivo, pregunta) if t)
        hechos.append(
            {
                "tipo": "pregunta",
                "identidad": sol.id,
                "solicitud": sol.id,
                "texto": _con_remision(
                    "Necesito tu decisión para seguir." + (f" {pregunta}" if pregunta else ""),
                    tecnico,
                ),
            }
        )
    if enc.estado == "necesita_usuario" and enc.motivo:
        pregunta, tecnico = _llano(enc.motivo)
        if not {enc.motivo, pregunta} & preguntadas:
            hechos.append(
                {
                    "tipo": "pregunta",
                    "identidad": "usuario:" + hashlib.sha256(enc.motivo.encode()).hexdigest()[:12],
                    "texto": _con_remision(
                        f"Necesito tu decisión para seguir. {pregunta}".rstrip(), tecnico
                    ),
                }
            )
    clase, condicion = "", ""
    if enc.impedimento and (enc.impedimento_causa or enc.impedimento_condicion):
        clase, condicion = enc.impedimento, enc.impedimento_condicion
    elif (espera := espera_inspeccion_consumida(enc)) is not None:
        clase, condicion = espera["clase"], ""
    if clase:
        hace_falta, tecnico = _llano(condicion)
        hechos.append(
            {
                "tipo": "trabado",
                "identidad": f"{clase}:{(apariciones or {}).get(clase, 1)}",
                "texto": _con_remision(
                    "Tu encargo se trabó y no puede avanzar solo."
                    + (f" Hace falta: {hace_falta}." if hace_falta else ""),
                    tecnico or not hace_falta,
                ),
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


@dataclass
class Vuelta:
    """Lo que una pasada de `drenar` comparte entre avisos: la hora y los canales cuyo transporte falló."""

    ahora: float
    caidos: set[str] = field(default_factory=set)
    limite: float | None = None
    errores: dict[str, str] = field(default_factory=dict)
    respondieron: set[str] = field(default_factory=set)

    def agotada(self) -> bool:
        return self.limite is not None and time.monotonic() >= self.limite

    def fallo_el_transporte(self, canal: str, error: str) -> None:
        self.caidos.add(canal)
        self.errores[canal] = error


def _publicar_aviso(
    registro: Registro,
    aviso: dict,
    cuerpo: str | None,
    cortar: str,
    vuelta: Vuelta | None = None,
) -> dict:
    """Misma recuperación de recibo para el cierre y para el avance. Sin modelo."""
    vuelta = vuelta or Vuelta(time.time())
    path = registro.base / "avisos" / f"{aviso['id']}.json"
    guardado = _leer(path) or dict(aviso)
    if guardado.get("estado") == "publicado" and guardado.get("recibo"):
        return _cerrar_aviso(guardado, False)
    if guardado.get("estado") == "descartado":
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
        if nombre in vuelta.caidos or _por_esperar(guardado, vuelta.ahora):
            return _cerrar_aviso(guardado, False)
        if not conciliacion:
            if _declara_idempotencia(cfg):
                _enviar_aviso(registro, comando, guardado, cuerpo, path, vuelta)
                return _cerrar_aviso(guardado, True)
            guardado["motivo"] = "aviso incierto sin conciliacion; no se reenvia"
            _atomico(path, guardado)
            return _cerrar_aviso(guardado, True)
        resultado_con, dato, caido = _correr_conciliacion(
            registro, conciliacion, guardado, cuerpo
        )
        if resultado_con == "hallado":
            # Solo una entrega probada recupera el canal: un envío con recibo o una conciliación que lo halla.
            # Una conciliación sin veredicto no marca ni fallo ni recuperación; con "ausente" decide el envío que sigue.
            vuelta.respondieron.add(nombre)
            guardado["estado"] = "publicado"
            guardado["alcance"] = "externo"
            guardado["recibo"] = dato
            for campo in ("motivo", "proximo_intento", "fallos"):
                guardado.pop(campo, None)
            _atomico(path, guardado)
            return _cerrar_aviso(guardado, True)
        if resultado_con == "incierto":
            guardado["motivo"] = dato
            _diferir(guardado, vuelta.ahora)
            if caido:
                vuelta.fallo_el_transporte(nombre, dato)
            _atomico(path, guardado)
            return _cerrar_aviso(guardado, True)
        _enviar_aviso(registro, comando, guardado, cuerpo, path, vuelta)
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
            recibo, error, caido = _correr_transporte(registro, comando, guardado, cuerpo)
            if caido:
                vuelta.fallo_el_transporte(nombre, error)
            elif recibo:
                vuelta.respondieron.add(nombre)
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
    if nombre in vuelta.caidos:
        return _cerrar_aviso(guardado, False)
    _enviar_aviso(registro, comando, guardado, cuerpo, path, vuelta)
    return _cerrar_aviso(guardado, True)


def _descartar_avances_de_terminados(registro: Registro, terminados: set[str]) -> None:
    """De un encargo terminado solo vale el aviso de cierre: los avances sin entregar se descartan."""
    for path in sorted((registro.base / "avisos").glob("*.json")):
        try:
            doc = _leer(path)
        except (OSError, ValueError):
            continue
        if (
            not doc
            or doc.get("encargo") not in terminados
            or not doc.get("tipo")
            or doc.get("estado") in ("publicado", "descartado")
        ):
            continue
        doc["estado"] = "descartado"
        doc["motivo"] = "encargo terminado; solo se envia el aviso de cierre"
        for campo in ("proximo_intento", "fallos"):
            doc.pop(campo, None)
        _atomico(path, doc)


def drenar(
    registro: Registro,
    cortar: str = "",
    ahora: float | None = None,
    tope_s: float | None = None,
) -> dict:
    """Una pasada de avisos. Con `tope_s`, no empieza avisos nuevos pasado ese tiempo; lo que queda sin
    intentar se retoma en la pasada siguiente. Un envío ya empezado conserva su timeout propio: matarlo
    por el tope podría cortar un mensaje que ya salió y duplicarlo, y marcaría caído un canal sano."""
    vuelta = Vuelta(
        time.time() if ahora is None else ahora,
        limite=None if tope_s is None else time.monotonic() + tope_s,
    )
    cortada = False
    publicados = []
    corte = registro.corte_avisos()
    encargos = {ident: registro.fold(ident) for ident in registro.ids()}
    terminados = {i for i, e in encargos.items() if e is not None and e.estado == "terminado"}
    if terminados:
        _descartar_avances_de_terminados(registro, terminados)
    for encargo, enc in encargos.items():
        if enc is None or not enc.canal:
            continue
        anterior = registro.fold(encargo, hasta=corte)
        historicos = {
            _aviso_de_hecho(anterior, hecho)["id"]
            for hecho in hechos_de_avance(anterior) if hecho["tipo"] in (
                "inicio", "pr"
            )
        } if anterior is not None else set()
        trabajos: list[tuple[dict, str | None]] = []
        if enc.estado == "terminado" and enc.evidencia:
            aviso = _aviso_de(enc)
            trabajos.append(
                (aviso, _cuerpo_del_aviso(registro, encargo, str(aviso["evidencia"])))
            )
        apariciones = _apariciones_de_impedimentos(registro, encargo) if enc.impedimento else None
        for hecho in hechos_de_avance(enc, apariciones):
            aviso = _aviso_de_hecho(enc, hecho)
            if aviso["id"] in historicos and not (
                registro.base / "avisos" / f"{aviso['id']}.json"
            ).exists():
                continue
            trabajos.append((aviso, hecho["texto"]))
        for aviso, cuerpo in trabajos:
            if vuelta.agotada():
                cortada = True
                break
            publicados.append(_publicar_aviso(registro, aviso, cuerpo, cortar, vuelta))
        if cortada:
            break
    try:
        canales.registrar_pasada(registro.base, vuelta.ahora, vuelta.errores, vuelta.respondieron)
    except OSError:
        pass
    return {"avisos": publicados, "cortada_por_tope": cortada}


class DrenadoAparte:
    """Corre `drenar` en un hilo propio para que la vuelta del supervisor no espere a los avisos.

    El hilo abre su propio `Registro` (una conexión sqlite no cruza hilos) y hay una sola pasada viva a la
    vez: si la anterior sigue, `lanzar` no hace nada. Cada pasada lleva el tope de `drenar`.
    """

    def __init__(self, base: Path, tope_s: float = TOPE_DRENAR_S):
        self._base = base
        self._tope_s = tope_s
        self._hilo: threading.Thread | None = None
        self.ultimo_error = ""

    def lanzar(self) -> bool:
        if self._hilo is not None and self._hilo.is_alive():
            return False
        self._hilo = threading.Thread(target=self._pasada, name="drenado-avisos", daemon=True)
        self._hilo.start()
        return True

    def esperar(self, timeout: float | None = None) -> None:
        if self._hilo is not None:
            self._hilo.join(timeout)

    def _pasada(self) -> None:
        try:
            registro = Registro(self._base)
            try:
                drenar(registro, tope_s=self._tope_s)
            finally:
                registro.close()
            self.ultimo_error = ""
        except Exception as exc:  # noqa: BLE001  los avisos nunca detienen al supervisor
            self.ultimo_error = f"{type(exc).__name__}: {exc}"[:200]


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
