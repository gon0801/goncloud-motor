"""La escritura externa pasa por aquí. El trabajador no abre el destino."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from .dominio import Encargo, efecto_de
from .registro import Registro


class RespuestaPerdida(RuntimeError):
    pass


class SinAutorizacion(RuntimeError):
    pass


class ParadaAntesDeEnviar(RuntimeError):
    pass


class AutoridadInalcanzable(RuntimeError):
    pass


_turno_hilo = threading.RLock()
_turno_nivel = threading.local()


class _Turno:
    """Orden común entre parar y escribir. El mismo hilo puede volver a entrar."""

    def __init__(self, base: Path):
        self.path = Path(base) / "turno.lock"
        self.fh = None

    def __enter__(self):
        _turno_hilo.acquire()
        niveles = getattr(_turno_nivel, "bases", {})
        self.clave = str(self.path.resolve())
        nivel = niveles.get(self.clave, 0)
        niveles[self.clave] = nivel + 1
        _turno_nivel.bases = niveles
        if nivel == 0:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(self.path, "a+", encoding="utf-8")
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        if self.fh is not None:
            if os.name == "nt":
                import msvcrt

                try:
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None
        _turno_nivel.bases[self.clave] -= 1
        _turno_hilo.release()


def turno(base: Path) -> _Turno:
    return _Turno(base)


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
        self.vigente = None

    def consultar(self, clave: str) -> dict:
        path = self.raiz / f"{_nombre(clave)}.json"
        if not path.exists():
            return {"estado": "ausente"}
        return json.loads(path.read_text(encoding="utf-8"))

    def aplicar(self, clave: str, cuerpo: str, operacion: str = "") -> dict:
        with turno(self.raiz.parent):
            actual = self.consultar(clave)
            if actual.get("estado") == "aplicado":
                if (
                    actual.get("cuerpo") != cuerpo
                    or actual.get("operacion", "") != operacion
                ):
                    return {
                        "conflicto": True,
                        "motivo": "la operacion existente tiene otro cuerpo",
                    }
                if self.perder:
                    self.perder = False
                    raise RespuestaPerdida(actual["recibo"])
                return actual
            if self.vigente is not None and not self.vigente():
                raise ParadaAntesDeEnviar()
            recibo = (
                "dst_"
                + hashlib.sha256(
                    f"{self.raiz.resolve()}:{operacion}:{clave}:{cuerpo}".encode()
                ).hexdigest()[:16]
            )
            payload = {
                "estado": "aplicado",
                "recibo": recibo,
                "cuerpo": cuerpo,
                "n": 1,
                "destino": str(self.raiz.resolve()),
                "operacion": operacion,
                "clave": clave,
            }
            _atomico(
                self.raiz / f"{_nombre(clave)}.json",
                json.dumps(payload, sort_keys=True),
            )
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
        and d.get("destino") == destino
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
        if f"{item.destino}:{item.clave}" == recurso:
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
        if (
            prev
            and prev["encargo"] == encargo_id
            and prev["data"].get("recurso") == recurso
        ):
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


def _autorizado_ahora(registro: Registro, encargo_id: str, autoridad: bool) -> bool:
    enc = registro.fold(encargo_id)
    if enc is None:
        return False
    return _autorizado(registro, enc, autoridad)


def _mismo_efecto(
    previo, operacion: str, clave: str, cuerpo: str, destino: str
) -> bool:
    return (
        previo.destino == destino
        and previo.operacion == operacion
        and previo.clave == clave
        and (previo.cuerpo or "") == (cuerpo or "")
    )


def _efecto_en_autoridad(
    registro: Registro,
    encargo_id: str,
    efecto_id: str,
    operacion: str,
    clave: str,
    cuerpo: str,
) -> dict | None:
    """None: no hay autoridad configurada y el destino local sigue siendo el de ensayo.

    Si hay autoridad, la escritura ocurre allá. Este host no toca su destino.
    """
    from .transporte import cargar, operar

    if not _autorizado_ahora(
        registro, encargo_id, not autoridad_ausente(registro.base)
    ):
        raise ParadaAntesDeEnviar()
    if cargar(registro.base) is None:
        return None
    resp = operar(
        registro.base,
        {
            "op": "efecto",
            "encargo": encargo_id,
            "id": efecto_id,
            "operacion": operacion,
            "clave_destino": clave,
            "cuerpo": cuerpo,
        },
    )
    if not resp.get("alcanzada"):
        raise AutoridadInalcanzable(resp.get("motivo") or "autoridad inalcanzable")
    salida = resp.get("salida") or {}
    if (
        salida.get("conflicto")
        or salida.get("rechazado")
        or salida.get("incierto")
        or salida.get("enviado") is False
    ):
        return salida
    recibo = salida.get("recibo")
    if not recibo:
        raise AutoridadInalcanzable("la autoridad no devolvio recibo")
    return {"recibo": recibo, "n": salida.get("n") or 1, "cuerpo": cuerpo}


def _rechazo(
    registro: Registro,
    encargo_id: str,
    efecto_id: str,
    clave: str,
    hora: str,
    motivo: str,
) -> dict:
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
    with turno(registro.base):
        return _despachar(
            registro,
            encargo_id,
            destino,
            efecto_id,
            operacion,
            clave,
            cuerpo,
            hora,
            autoridad,
        )


def _despachar(
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
    from .transporte import ConfigAutoridadInvalida, cargar

    try:
        cfg = cargar(registro.base)
    except ConfigAutoridadInvalida as exc:
        return _rechazo(registro, encargo_id, efecto_id, clave, hora, str(exc))
    destino_id = str(destino.raiz.resolve())
    if cfg is not None:
        destino_id = "autoridad:" + json.dumps(
            {k: cfg.get(k) for k in ("modo", "host", "base", "argv")}, sort_keys=True
        )
    recurso = f"{destino_id}:{clave}"
    enc = registro.fold(encargo_id)
    if enc is None:
        raise SinAutorizacion("no hay encargo")
    if autoridad is None:
        autoridad = not autoridad_ausente(registro.base)
    # La consulta puede aceptar una parada. La autorización se vuelve a leer después.
    consulta = destino.consultar(clave) if cfg is None else {"estado": "desconocido"}
    enc = registro.fold(encargo_id)
    if enc is None:
        raise SinAutorizacion("no hay encargo")
    previo = efecto_de(enc, efecto_id)
    if previo and not _mismo_efecto(previo, operacion, clave, cuerpo, destino_id):
        return {"conflicto": True, "motivo": "la identidad del efecto no coincide"}
    if previo and previo.estado == "aplicado" and previo.recibo:
        if _mismo_efecto(previo, operacion, clave, cuerpo, destino_id):
            return {
                "ya_aplicado": True,
                "recibo": previo.recibo,
                "n": consulta.get("n"),
            }
        return {
            "conflicto": True,
            "motivo": "el efecto ya aplicado no coincide con esta operacion",
        }
    if consulta.get("estado") == "aplicado":
        if (
            str(consulta.get("cuerpo") or "") != (cuerpo or "")
            or consulta.get("operacion", "") != operacion
        ):
            return {
                "conflicto": True,
                "motivo": "el destino no tiene el contenido pedido",
            }
        if _conceder(registro, encargo_id, recurso, hora) != "ok":
            return _rechazo(
                registro,
                encargo_id,
                efecto_id,
                clave,
                hora,
                "el recurso sigue concedido",
            )
        propio = _registrar_efecto(
            registro,
            encargo_id,
            efecto_id,
            "aplicado",
            operacion,
            clave,
            cuerpo,
            hora,
            destino_id,
            recibo=str(consulta.get("recibo") or ""),
            observacion="conciliado contra el destino",
        )
        if propio != "ok":
            return _rechazo(
                registro,
                encargo_id,
                efecto_id,
                clave,
                hora,
                "el recibo no quedo registrado",
            )
        return {
            "conciliado": True,
            "recibo": consulta["recibo"],
            "n": consulta.get("n"),
        }
    if not _autorizado(registro, enc, autoridad):
        return _rechazo(
            registro,
            encargo_id,
            efecto_id,
            clave,
            hora,
            "sin autorizacion de escritura",
        )
    if (
        cfg is None
        and previo
        and previo.estado == "incierto"
        and consulta.get("estado") != "ausente"
    ):
        return {"incierto": True, "motivo": "el destino no confirma ni niega"}
    if _conceder(registro, encargo_id, recurso, hora) != "ok":
        return _rechazo(
            registro, encargo_id, efecto_id, clave, hora, "el recurso sigue concedido"
        )
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
            destino_id,
            observacion="intención persistida",
        )
        if propio != "ok":
            return _rechazo(
                registro,
                encargo_id,
                efecto_id,
                clave,
                hora,
                "la intencion no quedo registrada",
            )
    enc = registro.fold(encargo_id)
    if enc is None or not _autorizado(registro, enc, autoridad):
        return {
            "intencion": True,
            "enviado": False,
            "motivo": "parada aceptada antes del envio",
        }
    destino.vigente = lambda: _autorizado_ahora(registro, encargo_id, autoridad)
    try:
        remoto = _efecto_en_autoridad(
            registro, encargo_id, efecto_id, operacion, clave, cuerpo
        )
        if remoto is None:
            aplicado = destino.aplicar(clave, cuerpo, operacion)
        else:
            aplicado = remoto
    except ParadaAntesDeEnviar:
        return {
            "intencion": True,
            "enviado": False,
            "motivo": "parada aceptada antes del envio",
        }
    except AutoridadInalcanzable as exc:
        _registrar_efecto(
            registro,
            encargo_id,
            efecto_id,
            "incierto",
            operacion,
            clave,
            cuerpo,
            hora,
            destino_id,
            observacion="sin respuesta de la autoridad; conciliar por la misma identidad",
        )
        return {"incierto": True, "enviado": None, "motivo": str(exc)}
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
            destino_id,
            observacion="la respuesta se perdió",
        )
        return {"incierto": True}
    if (
        aplicado.get("conflicto")
        or aplicado.get("rechazado")
        or aplicado.get("incierto")
        or aplicado.get("enviado") is False
        or not aplicado.get("recibo")
    ):
        return aplicado
    propio = _registrar_efecto(
        registro,
        encargo_id,
        efecto_id,
        "aplicado",
        operacion,
        clave,
        cuerpo,
        hora,
        destino_id,
        recibo=aplicado["recibo"],
        observacion="recibo del destino",
    )
    if propio != "ok":
        return {
            "intencion": True,
            "enviado": True,
            "recibo": aplicado["recibo"],
            "motivo": "el recibo no quedo registrado",
        }
    return {"recibo": aplicado["recibo"], "n": aplicado["n"]}


def activar_parada_local(base: Path, clave: str, hora: str) -> dict:
    with turno(base):
        return _activar_parada_local(base, clave, hora)


def _activar_parada_local(base: Path, clave: str, hora: str) -> dict:
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
