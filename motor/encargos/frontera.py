"""La escritura externa pasa por aquí. El trabajador no abre el destino."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .dominio import Encargo, efecto_de
from .registro import Registro


class RespuestaPerdida(RuntimeError):
    pass


class SinAutorizacion(RuntimeError):
    pass


def _atomico(path: Path, texto: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class DestinoEnsayo:
    """Directorio de ensayo. `aplicar` no incrementa si la clave ya tiene recibo."""

    def __init__(self, raiz: Path):
        self.raiz = Path(raiz)
        self.raiz.mkdir(parents=True, exist_ok=True)
        self.perder = False

    def consultar(self, clave: str) -> dict:
        path = self.raiz / f"{_nombre(clave)}.json"
        if not path.exists():
            return {"estado": "ausente"}
        return json.loads(path.read_text(encoding="utf-8"))

    def aplicar(self, clave: str, cuerpo: str) -> dict:
        actual = self.consultar(clave)
        if actual.get("estado") == "aplicado":
            if self.perder:
                self.perder = False
                raise RespuestaPerdida(actual["recibo"])
            return actual
        recibo = "dst_" + hashlib.sha256(clave.encode()).hexdigest()[:16]
        payload = {"estado": "aplicado", "recibo": recibo, "cuerpo": cuerpo, "n": 1}
        _atomico(self.raiz / f"{_nombre(clave)}.json", json.dumps(payload, sort_keys=True))
        if self.perder:
            self.perder = False
            raise RespuestaPerdida(recibo)
        return payload


def _nombre(clave: str) -> str:
    return hashlib.sha256(clave.encode()).hexdigest()[:24]


def _evento_efecto(enc: Encargo, efecto_id: str, estado: str, **extra: str) -> dict:
    previo = efecto_de(enc, efecto_id)
    data = {
        "id": efecto_id,
        "destino": extra.get("destino") or (previo.destino if previo else ""),
        "operacion": extra.get("operacion") or (previo.operacion if previo else ""),
        "clave": extra.get("clave") or (previo.clave if previo else ""),
        "cuerpo": extra.get("cuerpo") or "",
        "estado": estado,
        "recibo": extra.get("recibo") or "",
        "observacion": extra.get("observacion") or "",
        "hora": extra.get("hora") or "",
    }
    return {"kind": "efecto", "data": data}


def _registrar_efecto(
    registro: Registro,
    encargo_id: str,
    efecto_id: str,
    estado: str,
    operacion: str,
    clave: str,
    cuerpo: str,
    hora: str,
    destino: str,
    recibo: str = "",
    observacion: str = "",
) -> str:
    enc = registro.fold(encargo_id)
    if enc is None:
        return "ilegal"
    evento = _evento_efecto(
        enc,
        efecto_id,
        estado,
        destino=destino,
        operacion=operacion,
        clave=clave,
        cuerpo=cuerpo,
        recibo=recibo,
        observacion=observacion,
        hora=hora,
    )
    ukey = f"efecto:{encargo_id}:{efecto_id}:{estado}"
    resultado = registro.anotar(encargo_id, evento, ukey)
    if resultado == "nuevo":
        return "ok"
    if resultado != "igual":
        return resultado
    prev = registro.por_ukey(ukey)
    if not prev or prev["encargo"] != encargo_id:
        return "ajena"
    d = prev["data"]
    mismo = (
        d.get("id") == efecto_id
        and d.get("operacion") == operacion
        and d.get("clave") == clave
        and d.get("estado") == estado
        and d.get("cuerpo", cuerpo) == cuerpo
    )
    return "ok" if mismo else "ajena"


def _concesiones(registro: Registro, recurso: str) -> list[tuple[str, dict]]:
    rows = registro.db.execute(
        "SELECT encargo, data FROM events WHERE kind = 'concesion' ORDER BY id"
    ).fetchall()
    salida = []
    for encargo, data in rows:
        try:
            parsed = json.loads(data)
        except ValueError:
            continue
        if parsed.get("recurso") == recurso:
            salida.append((encargo, parsed))
    return salida


def _titular_suelta(registro: Registro, titular: Encargo, recurso: str) -> bool:
    """Solo se transfiere un recurso cuyo ejecutor anterior ya no está y cuyo efecto quedó aplicado."""
    from .sesion import observar

    efecto = None
    for item in titular.efectos:
        if item.clave == recurso:
            efecto = item
    if efecto is not None and efecto.estado in ("intencion", "incierto"):
        return False
    if titular.reserva and titular.proc is None:
        return False
    if titular.proc is None:
        return efecto is None or efecto.estado == "aplicado"
    visto = observar(registro.base / "ejecucion" / titular.id, titular.proc.como_dict())
    if visto != "ausente":
        return False
    return efecto is None or efecto.estado == "aplicado"


def _conceder(registro: Registro, encargo_id: str, recurso: str, hora: str) -> str:
    previas = _concesiones(registro, recurso)
    generacion = 1
    if previas:
        titular_id, ultima = previas[-1]
        if titular_id == encargo_id:
            return "ok"
        titular = registro.fold(titular_id)
        if titular is None or not _titular_suelta(registro, titular, recurso):
            return "ocupada"
        generacion = int(ultima.get("generacion") or 1) + 1
    ukey = f"concesion:{hashlib.sha256(recurso.encode()).hexdigest()[:16]}:{generacion}"
    resultado = registro.anotar(
        encargo_id,
        {
            "kind": "concesion",
            "data": {"recurso": recurso, "generacion": generacion, "hora": hora},
        },
        ukey,
    )
    if resultado == "nuevo":
        return "ok"
    if resultado == "igual":
        prev = registro.por_ukey(ukey)
        if prev and prev["encargo"] == encargo_id and prev["data"].get("recurso") == recurso:
            return "ok"
    return "ocupada"


def anotar_local(base: Path, linea: dict) -> None:
    buzon = Path(base) / "buzon-local.jsonl"
    buzon.parent.mkdir(parents=True, exist_ok=True)
    with open(buzon, "a", encoding="utf-8") as f:
        f.write(json.dumps(linea, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def autoridad_ausente(base: Path) -> bool:
    return (Path(base) / "autoridad.no").exists()


def _autorizado(registro: Registro, enc: Encargo, autoridad: bool) -> bool:
    if leer_parada_local(registro.base) or not autoridad:
        return False
    if not enc.escrituras or not enc.intentos_nuevos:
        return False
    return enc.estado not in ("deteniendo", "detenido", "cancelado", "terminado")


def _rechazo(registro: Registro, encargo_id: str, efecto_id: str, clave: str, hora: str, motivo: str) -> dict:
    anotar_local(
        registro.base,
        {
            "encargo": encargo_id,
            "efecto": efecto_id,
            "clave": clave,
            "motivo": motivo,
            "hora": hora,
        },
    )
    return {"rechazado": True, "motivo": motivo}


def despachar(
    registro: Registro,
    encargo_id: str,
    destino: DestinoEnsayo,
    efecto_id: str,
    operacion: str,
    clave: str,
    cuerpo: str,
    hora: str,
    autoridad: bool | None = None,
) -> dict:
    enc = registro.fold(encargo_id)
    if enc is None:
        raise SinAutorizacion("no hay encargo")
    if autoridad is None:
        autoridad = not autoridad_ausente(registro.base)
    # La consulta puede aceptar una parada. La autorización se vuelve a leer después.
    consulta = destino.consultar(clave)
    enc = registro.fold(encargo_id)
    if enc is None:
        raise SinAutorizacion("no hay encargo")
    previo = efecto_de(enc, efecto_id)
    if previo and previo.estado == "aplicado" and previo.recibo:
        return {"ya_aplicado": True, "recibo": previo.recibo, "n": consulta.get("n")}
    if consulta.get("estado") == "aplicado":
        if _conceder(registro, encargo_id, clave, hora) != "ok":
            return _rechazo(registro, encargo_id, efecto_id, clave, hora, "el recurso sigue concedido")
        propio = _registrar_efecto(
            registro,
            encargo_id,
            efecto_id,
            "aplicado",
            operacion,
            clave,
            cuerpo,
            hora,
            str(destino.raiz),
            recibo=str(consulta.get("recibo") or ""),
            observacion="conciliado contra el destino",
        )
        if propio != "ok":
            return _rechazo(registro, encargo_id, efecto_id, clave, hora, "el recibo no quedo registrado")
        return {"conciliado": True, "recibo": consulta["recibo"], "n": consulta.get("n")}
    if not _autorizado(registro, enc, autoridad):
        return _rechazo(registro, encargo_id, efecto_id, clave, hora, "sin autorizacion de escritura")
    if previo and previo.estado == "incierto" and consulta.get("estado") != "ausente":
        return {"incierto": True, "motivo": "el destino no confirma ni niega"}
    if _conceder(registro, encargo_id, clave, hora) != "ok":
        return _rechazo(registro, encargo_id, efecto_id, clave, hora, "el recurso sigue concedido")
    if previo is None or previo.estado != "intencion":
        propio = _registrar_efecto(
            registro,
            encargo_id,
            efecto_id,
            "intencion",
            operacion,
            clave,
            cuerpo,
            hora,
            str(destino.raiz),
            observacion="intención persistida",
        )
        if propio != "ok":
            return _rechazo(registro, encargo_id, efecto_id, clave, hora, "la intencion no quedo registrada")
    enc = registro.fold(encargo_id)
    if enc is None or not _autorizado(registro, enc, autoridad):
        return {"intencion": True, "enviado": False, "motivo": "parada aceptada antes del envio"}
    try:
        aplicado = destino.aplicar(clave, cuerpo)
    except RespuestaPerdida:
        _registrar_efecto(
            registro,
            encargo_id,
            efecto_id,
            "incierto",
            operacion,
            clave,
            cuerpo,
            hora,
            str(destino.raiz),
            observacion="la respuesta se perdió",
        )
        return {"incierto": True}
    propio = _registrar_efecto(
        registro,
        encargo_id,
        efecto_id,
        "aplicado",
        operacion,
        clave,
        cuerpo,
        hora,
        str(destino.raiz),
        recibo=aplicado["recibo"],
        observacion="recibo del destino",
    )
    if propio != "ok":
        return {"intencion": True, "enviado": True, "recibo": aplicado["recibo"], "motivo": "el recibo no quedo registrado"}
    return {"recibo": aplicado["recibo"], "n": aplicado["n"]}


def activar_parada_local(base: Path, clave: str, hora: str) -> dict:
    """Impide trabajo en este host. No escribe el registro canónico ni autoriza escrituras."""
    path = Path(base) / "parada-local.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    recibo = "loc_" + hashlib.sha256(clave.encode()).hexdigest()[:12]
    datos = {"clave": clave, "recibo": recibo, "hora": hora, "escrituras": False}
    _atomico(path, json.dumps(datos, sort_keys=True))
    return datos


def leer_parada_local(base: Path) -> dict | None:
    path = Path(base) / "parada-local.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
