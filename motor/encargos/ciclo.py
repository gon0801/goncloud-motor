"""Ciclo de vida del encargo. No decide el trabajo de la aplicación."""

from __future__ import annotations

import hashlib
import json
import os
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
    registro.append(
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
        f"comprobacion:{efecto_id}:instrumento:{juicio['firma']}",
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
        f"comprobacion:{efecto_id}:toml",
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
        registro.anotar(encargo_id, evento, f"{prefijo}:{evento['kind']}:{i}:{evento['data'].get('hora','')}")


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
                f"plazo:{enc.reanuda_en}",
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
                f"intento:{proc['pid']}:{proc['inicio']}",
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
                f"actividad:{firma[:16]}",
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
            _aplicar(registro, encargo_id, decision["eventos"], f"paso:{hora}:{arbol}")
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


def _es_uuid(valor: str) -> bool:
    try:
        uuid.UUID(valor)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _lanzar_uno(registro, enc: Encargo, directorio: Path, espacio: Path, hora: str, lanzador) -> None:
    if observar(directorio, enc.proc.como_dict() if enc.proc else _leer_proc(directorio)) == "vivo":
        return
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
    registro.anotar(
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
                "herramientas_observables": cmd[0] not in {"grok", "claude", "codex"},
                "firma": proc["firma"],
                "hora": hora,
            },
        },
        f"intento:{proc['pid']}:{proc.get('inicio')}",
    )


def marcar_espera(registro: Registro, encargo_id: str, motivo: str, reanuda_en: str, hora: str) -> dict:
    registro.anotar(
        encargo_id,
        {
            "kind": "espera",
            "data": {"motivo": motivo, "reanuda_en": reanuda_en, "hora": hora},
        },
        f"espera:{reanuda_en}:{motivo}",
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
        f"pregunta:{hashlib.sha256(texto.encode()).hexdigest()[:12]}",
    )
    return vista(registro.fold(encargo_id))


def afirmar(registro: Registro, encargo_id: str, texto: str, hora: str) -> dict:
    registro.anotar(
        encargo_id,
        {"kind": "afirmacion", "data": {"texto": texto, "hora": hora}},
        f"afirmacion:{hashlib.sha256(texto.encode()).hexdigest()[:12]}",
    )
    return vista(registro.fold(encargo_id))


def parada_local(base: Path, clave: str, hora: str) -> dict:
    return activar_parada_local(base, clave, hora)


def crecer_log_intento(base: Path, encargo_id: str, texto: str) -> None:
    crecer_log(_directorio(base, encargo_id), texto)
