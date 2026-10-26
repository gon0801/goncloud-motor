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
        "estado": estado,
        "recibo": extra.get("recibo") or "",
        "observacion": extra.get("observacion") or "",
        "hora": extra.get("hora") or "",
    }
    return {"kind": "efecto", "data": data}


def anotar_local(base: Path, linea: dict) -> None:
    buzon = Path(base) / "buzon-local.jsonl"
    buzon.parent.mkdir(parents=True, exist_ok=True)
    with open(buzon, "a", encoding="utf-8") as f:
        f.write(json.dumps(linea, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def autoridad_ausente(base: Path) -> bool:
    return (Path(base) / "autoridad.no").exists()


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
    if leer_parada_local(registro.base) or not autoridad or not enc.escrituras or not enc.intentos_nuevos:
        anotar_local(
            registro.base,
            {
                "encargo": encargo_id,
                "efecto": efecto_id,
                "clave": clave,
                "motivo": "sin autorizacion de escritura",
                "hora": hora,
            },
        )
        return {"rechazado": True, "motivo": "sin autorizacion de escritura"}
    previo = efecto_de(enc, efecto_id)
    if previo and previo.estado == "aplicado" and previo.recibo:
        return {"ya_aplicado": True, "recibo": previo.recibo, "n": destino.consultar(clave).get("n")}
    consulta = destino.consultar(clave)
    if consulta.get("estado") == "aplicado":
        registro.anotar(
            encargo_id,
            _evento_efecto(
                enc,
                efecto_id,
                "aplicado",
                destino=str(destino.raiz),
                operacion=operacion,
                clave=clave,
                recibo=consulta["recibo"],
                observacion="conciliado contra el destino",
                hora=hora,
            ),
            f"efecto:{efecto_id}:aplicado",
        )
        return {"conciliado": True, "recibo": consulta["recibo"], "n": consulta.get("n")}
    if previo and previo.estado == "incierto" and consulta.get("estado") != "ausente":
        return {"incierto": True, "motivo": "el destino no confirma ni niega"}
    if previo is None:
        registro.anotar(
            encargo_id,
            _evento_efecto(
                enc,
                efecto_id,
                "intencion",
                destino=str(destino.raiz),
                operacion=operacion,
                clave=clave,
                observacion="intención persistida",
                hora=hora,
            ),
            f"efecto:{efecto_id}:intencion",
        )
    try:
        aplicado = destino.aplicar(clave, cuerpo)
    except RespuestaPerdida:
        registro.anotar(
            encargo_id,
            _evento_efecto(
                enc,
                efecto_id,
                "incierto",
                destino=str(destino.raiz),
                operacion=operacion,
                clave=clave,
                observacion="la respuesta se perdió",
                hora=hora,
            ),
            f"efecto:{efecto_id}:incierto",
        )
        return {"incierto": True}
    registro.anotar(
        encargo_id,
        _evento_efecto(
            enc,
            efecto_id,
            "aplicado",
            destino=str(destino.raiz),
            operacion=operacion,
            clave=clave,
            recibo=aplicado["recibo"],
            observacion="recibo del destino",
            hora=hora,
        ),
        f"efecto:{efecto_id}:aplicado",
    )
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
