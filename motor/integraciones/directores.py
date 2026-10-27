from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from motor.encargos.ciclo import ahora_iso, consultar, ordenar, recibir
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


def encargar(
    registro: Registro,
    texto: str,
    clave: str,
    hora: str,
    director: str,
    canal: str,
    via: str,
    adjuntos: tuple[str, ...] = (),
) -> dict:
    canal = nombre_canal(canal)
    salida = recibir(
        registro,
        texto,
        clave,
        hora,
        director=director,
        canal=canal,
        via=via,
        adjuntos=adjuntos,
    )
    if via == "directa" and not salida.get("conflicto"):
        path = registro.base / "directo" / f"{salida['encargo']}.json"
        if not path.exists():
            _atomico(
                path,
                {
                    "estado": "activo",
                    "sesion": clave,
                    "director": director,
                    "encargo": salida["encargo"],
                },
            )
    return salida


def cerrar_sesion(registro: Registro, encargo: str) -> dict:
    path = registro.base / "directo" / f"{encargo}.json"
    actual = _leer(path)
    if actual is None:
        return {"encargo": encargo, "estado": "sin-sesion"}
    actual["estado"] = "cerrado"
    _atomico(path, actual)
    return actual


def promover(registro: Registro, encargo: str, clave: str, hora: str) -> dict:
    enc = registro.fold(encargo)
    if enc is None:
        return {"rechazada": True, "motivo": "no hay encargo", "encargo": encargo}
    sesion = _leer(registro.base / "directo" / f"{encargo}.json")
    if sesion and sesion.get("estado") != "cerrado":
        salida = consultar(registro, encargo)
        salida["rechazada"] = True
        salida["motivo"] = "el ejecutor directo sigue abierto"
        return salida
    if enc.proc is not None or enc.reserva:
        salida = consultar(registro, encargo)
        salida["rechazada"] = True
        salida["motivo"] = "el ejecutor anterior sigue abierto"
        return salida
    resultado = registro.anotar(
        encargo,
        {"kind": "via", "data": {"via": "durable", "hora": hora, "clave": clave}},
        f"via:{encargo}:durable",
    )
    salida = consultar(registro, encargo)
    if resultado == "ajena":
        salida["conflicto"] = True
    elif resultado == "ilegal":
        salida["rechazada"] = True
        salida["motivo"] = "no pude pasar a durable"
    elif resultado == "igual":
        salida["retransmision"] = True
    return salida


def resultado_directo(
    registro: Registro, encargo: str, texto: str, hora: str
) -> dict:
    enc = registro.fold(encargo)
    if enc is None:
        return {"rechazada": True, "motivo": "no hay encargo"}
    espacio = registro.base / "espacio" / encargo
    espacio.mkdir(parents=True, exist_ok=True)
    destino = espacio / "resultado.txt"
    if not destino.exists():
        temporal = destino.with_suffix(".txt.tmp")
        temporal.write_text(texto, encoding="utf-8")
        temporal.replace(destino)
    cuerpo = destino.read_text(encoding="utf-8")
    if cuerpo != texto:
        salida = consultar(registro, encargo)
        salida["conflicto"] = True
        salida["motivo"] = "el resultado ya publicado es otro"
        return salida
    evidencia = hashlib.sha256(cuerpo.encode()).hexdigest()
    resultado = registro.anotar(
        encargo,
        {
            "kind": "resultado_directo",
            "data": {"evidencia": evidencia, "hora": hora},
        },
        f"resultado-directo:{encargo}:{evidencia}",
    )
    salida = consultar(registro, encargo)
    salida["evidencia"] = evidencia
    if resultado == "igual":
        salida["retransmision"] = True
    elif resultado == "ilegal":
        salida["rechazada"] = True
        salida["motivo"] = "no pude cerrar la via directa"
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
    }


def _cuerpo_del_aviso(registro: Registro, encargo: str, evidencia: str) -> str | None:
    destino = registro.base / "espacio" / encargo / "resultado.txt"
    if destino.is_file():
        cuerpo = destino.read_text(encoding="utf-8")
        if hashlib.sha256(cuerpo.encode()).hexdigest() == evidencia:
            return cuerpo
        return None
    if len(evidencia) == 64 and all(c in "0123456789abcdef" for c in evidencia):
        return None
    return evidencia


def drenar(registro: Registro, cortar: str = "") -> dict:
    publicados = []
    for encargo in registro.ids():
        enc = registro.fold(encargo)
        if enc is None or enc.estado != "terminado" or not enc.canal or not enc.evidencia:
            continue
        aviso = _aviso_de(enc)
        path = registro.base / "avisos" / f"{aviso['id']}.json"
        guardado = _leer(path) or aviso
        if guardado.get("estado") == "publicado" and guardado.get("recibo"):
            publicados.append(guardado)
            continue
        try:
            nombre = nombre_canal(str(guardado["canal"]))
        except ValueError:
            guardado["estado"] = "incierto"
            guardado["motivo"] = "el canal no es un nombre"
            _atomico(path, guardado)
            publicados.append(guardado)
            continue
        canal_dir = registro.base / "canal" / nombre
        entrega = canal_dir / guardado["id"]
        if cortar == "antes" and not entrega.exists():
            guardado["estado"] = "pendiente"
            _atomico(path, guardado)
            publicados.append(guardado)
            continue
        cuerpo = _cuerpo_del_aviso(registro, encargo, str(aviso["evidencia"]))
        if cuerpo is None:
            guardado["estado"] = "incierto"
            guardado["motivo"] = "no hay un resultado legible que concuerde con la evidencia"
            _atomico(path, guardado)
            publicados.append(guardado)
            continue
        if not entrega.exists():
            canal_dir.mkdir(parents=True, exist_ok=True)
            temporal = entrega.with_suffix(".tmp")
            temporal.write_text(cuerpo, encoding="utf-8")
            temporal.replace(entrega)
        if cortar == "despues" and not guardado.get("recibo"):
            guardado["estado"] = "incierto"
            _atomico(path, guardado)
            publicados.append(guardado)
            continue
        if entrega.read_text(encoding="utf-8") != cuerpo:
            guardado["estado"] = "incierto"
            guardado["motivo"] = "el canal tiene otro contenido y no se puede conciliar"
            _atomico(path, guardado)
            publicados.append(guardado)
            continue
        guardado["estado"] = "publicado"
        guardado["recibo"] = "pub_" + aviso["id"]
        _atomico(path, guardado)
        publicados.append(guardado)
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


def hora() -> str:
    return ahora_iso()
