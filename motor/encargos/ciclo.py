"""Ciclo de vida del encargo. No decide el trabajo de la aplicación."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
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
    turno,
)
from .registro import Registro
from .sesion import _enjaular, cerrar, crecer_log, herramienta_en_curso, lanzar, observar

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
    _espejo_entrega(registro, encargo, texto, clave, hora, director, restricciones)
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
    with turno(registro.base):
        return _ordenar(registro, encargo_id, accion, clave, hora)


def _ordenar(registro: Registro, encargo_id: str, accion: str, clave: str, hora: str) -> dict:
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
        enc = _mirar_autoridad(registro, enc, hora)
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
        if enc.reserva and enc.proc is None and proc and arbol != "vivo" and _edad(enc, epoch) >= 30:
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


def _comandos_de(texto: str) -> list[str]:
    comandos = []
    for linea in texto.splitlines():
        if linea.startswith("comprobar:"):
            comando = linea.split(":", 1)[1].strip()
            if comando:
                comandos.append(comando)
    return comandos


def _lineas_comprobar(enc: Encargo, espacio: Path | None = None) -> list[str]:
    """Un procedimiento que el trabajador dejó en su espacio sustituye al comando del pedido."""
    if espacio is not None:
        for nombre in ("procedimiento.txt", "comprobacion.txt"):
            path = espacio / nombre
            if not path.is_file():
                continue
            comandos = _comandos_de(path.read_text(encoding="utf-8", errors="replace"))
            if comandos:
                return comandos
    return _comandos_de("\n".join((enc.texto, *enc.restricciones, *enc.ajustes)))


def _fallo_instrumento(firma: str, causa: str, reparacion: str) -> dict:
    return {
        "clase": "instrumento",
        "causa": causa,
        "reparacion": reparacion,
        "firma": firma,
        "rc": None,
        "stdout": "",
        "stderr": "",
    }


def _correr(espacio: Path, argv: list[str], firma: str) -> dict:
    log = espacio.parent.parent / "ejecucion" / espacio.name / "comprobacion.log"
    envuelto, aislamiento = _enjaular(argv, espacio, log)
    if aislamiento != "jaula":
        return _fallo_instrumento(
            firma,
            "la comprobacion no tiene jaula",
            "correr la comprobacion con sandbox-exec en el espacio del encargo",
        )
    try:
        corrida = subprocess.run(
            envuelto,
            cwd=espacio,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            return _fallo_instrumento(firma, f"no existe {argv[0]}", "usar un comando que exista")
        return _fallo_instrumento(firma, f"no se pudo ejecutar {argv[0]}", "usar un comando ejecutable")
    except subprocess.TimeoutExpired:
        return _fallo_instrumento(firma, "la comprobacion no termino", "acotar la comprobacion")
    return {
        "rc": corrida.returncode,
        "stdout": corrida.stdout or "",
        "stderr": corrida.stderr or "",
        "firma": firma,
    }


def _juicio_de_rc(corrida: dict, firma: str, evidencia: str) -> dict:
    """0 confirma. 1 es el producto. Cualquier otro código es la herramienta, no el producto."""
    rc = corrida.get("rc")
    if rc is None or corrida.get("clase") == "instrumento":
        return {
            "clase": "instrumento",
            "causa": corrida.get("causa") or "la comprobacion no corrio",
            "reparacion": corrida.get("reparacion") or "sustituir la comprobacion y volver a mirar el producto",
            "firma": firma,
        }
    detalle = (corrida.get("stderr") or corrida.get("stdout") or "la comprobacion fallo").strip()
    if rc == 0:
        return {"clase": "confirmado", "evidencia": evidencia, "firma": firma}
    if rc == 1:
        return {"clase": "defecto", "causa": detalle[-400:], "firma": firma}
    return {
        "clase": "instrumento",
        "causa": detalle[-400:],
        "reparacion": "sustituir la comprobacion: el comando no examino el producto",
        "firma": firma,
    }


def _es_suma(enc: Encargo) -> bool:
    texto = "\n".join((enc.texto, *enc.restricciones, *enc.ajustes))
    return "sumar" in texto and "suma" in texto.lower()


def _juicio_contiene(espacio: Path, nombre: str, esperado: str) -> dict:
    """Lee el archivo. Sirve cuando el pedido pide ese contenido, no como prueba de otra cosa."""
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
        return _fallo_instrumento(firma, str(exc), "revisar el comando del pedido")
    if not argv:
        return _fallo_instrumento(firma, "comando vacio", "escribir el comando")
    exe = argv[0]
    if "/" in exe:
        candidato = Path(exe) if os.path.isabs(exe) else espacio / exe
        if not candidato.is_file():
            return _fallo_instrumento(firma, f"no existe {exe}", "usar un comando que exista")
        if not os.access(candidato, os.X_OK):
            return _fallo_instrumento(firma, f"no se pudo ejecutar {exe}", "usar un comando ejecutable")
    elif shutil.which(exe) is None:
        return _fallo_instrumento(firma, f"no existe {exe}", "usar un comando que exista")
    corrida = _correr(espacio, argv, firma)
    return _juicio_de_rc(corrida, firma, f"{comando} rc=0")


def _juicio_objetivo(enc: Encargo, espacio: Path) -> dict | None:
    """Si el pedido pide la suma de los dos números, el supervisor llama sumar(2, 3)."""
    if not _es_suma(enc):
        return None
    ruta = espacio / "suma.py"
    if not ruta.is_file() or "def sumar" not in ruta.read_text(encoding="utf-8", errors="replace"):
        return None
    firma = "objetivo:sumar(2,3)"
    codigo = (
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('suma', 'suma.py')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "print(mod.sumar(2, 3))\n"
    )
    corrida = _correr(espacio, [sys.executable, "-B", "-c", codigo], firma)
    juicio = _juicio_de_rc(corrida, firma, "sumar(2, 3) devolvió 5")
    if juicio["clase"] != "confirmado":
        return juicio
    valor = (corrida.get("stdout") or "").strip()
    if valor == "5":
        return juicio
    return {"clase": "defecto", "causa": f"sumar(2, 3) devolvió {valor or 'nada'}", "firma": firma}


def _hojas_toml(datos, prefijo: str = ""):
    if isinstance(datos, dict):
        for clave, valor in datos.items():
            ruta = f"{prefijo}.{clave}" if prefijo else str(clave)
            yield from _hojas_toml(valor, ruta)
    elif isinstance(datos, list):
        for indice, valor in enumerate(datos):
            yield from _hojas_toml(valor, f"{prefijo}[{indice}]")
    elif isinstance(datos, str) and prefijo:
        yield prefijo, datos


def _sustituto_toml(enc: Encargo, espacio: Path) -> dict | None:
    """El grep que no examina el archivo no decide. El parser lee el toml y no lo modifica."""
    texto = "\n".join((enc.texto, *enc.restricciones, *enc.ajustes)).lower()
    if "toml" not in texto and "definicion" not in texto:
        return None
    candidatos = sorted(espacio.glob("*.toml"))
    firma = "toml:" + ",".join(path.name for path in candidatos)
    if not candidatos:
        return {
            "clase": "instrumento",
            "causa": "no hay un toml en el espacio",
            "reparacion": "dejar el archivo toml y leerlo con el parser",
            "firma": "toml:ausente",
        }
    import tomllib

    partes = []
    for path in candidatos:
        crudo = path.read_bytes()
        try:
            datos = tomllib.loads(crudo.decode("utf-8"))
        except (UnicodeError, tomllib.TOMLDecodeError):
            return {"clase": "defecto", "causa": f"{path.name} no es toml valido", "firma": f"toml:{path.name}"}
        hojas = list(_hojas_toml(datos))
        if not hojas:
            return {"clase": "defecto", "causa": f"{path.name} no tiene definiciones", "firma": f"toml:{path.name}"}
        partes.extend(f"{clave}={valor}" for clave, valor in hojas)
    return {
        "clase": "confirmado",
        "evidencia": ("toml confirma " + "; ".join(partes))[:2000],
        "firma": firma or "toml",
    }


def _pedidos_efecto(espacio: Path) -> list[dict] | None:
    path = espacio / "pedido-efecto.json"
    if not path.exists():
        return []
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(datos, dict):
        datos = [datos]
    if not isinstance(datos, list):
        return None
    for item in datos:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            return None
    return datos


def _anotar_juicio(registro: Registro, enc: Encargo, juicio: dict, hora: str, serie: int) -> None:
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


def _anotar_varios(registro: Registro, enc: Encargo, juicios: list[dict], hora: str, serie: int) -> int:
    for juicio in juicios:
        serie += 1
        _anotar_juicio(registro, enc, juicio, hora, serie)
    return serie


def _devolver_revision(registro: Registro, enc: Encargo, hora: str) -> None:
    registro.anotar(
        enc.id,
        {
            "kind": "estrategia",
            "data": {
                "estrategia": enc.estrategia or "revision_tecnica",
                "rol": "responsable",
                "motivo": "la revision devolvio la comprobacion al responsable",
                "siguiente": "sustituir la comprobacion que fallo como instrumento y volver a mirar el producto",
                "hora": hora,
            },
        },
        f"estrategia:{enc.id}:devolucion:{enc.firma_fallo}:{enc.repeticiones}",
    )


def _incorporar(registro: Registro, enc: Encargo, espacio: Path, hora: str) -> Encargo:
    """El supervisor ejecuta el objetivo. Una frase del agente no cierra el encargo."""
    ev_path = espacio / "evidencia-comportamiento.txt"
    texto = ev_path.read_text(encoding="utf-8", errors="replace") if ev_path.is_file() else ""
    if texto.strip():
        registro.anotar(
            enc.id,
            {"kind": "afirmacion", "data": {"texto": texto.strip()[:2000], "hora": hora}},
            f"afirmacion:{enc.id}:{hashlib.sha256(texto.encode()).hexdigest()[:12]}",
        )
    juicios = [_juicio_comando(espacio, comando) for comando in _lineas_comprobar(enc, espacio)]
    if not _es_suma(enc):
        for linea in texto.splitlines():
            limpia = linea.strip()
            if " contiene " not in limpia:
                continue
            nombre, _, esperado = limpia.partition(" contiene ")
            if nombre.strip() and esperado.strip():
                juicios.append(_juicio_contiene(espacio, nombre.strip(), esperado.strip()))
    objetivo = _juicio_objetivo(enc, espacio)
    if objetivo:
        juicios.append(objetivo)
    instrumentos = [juicio for juicio in juicios if juicio["clase"] == "instrumento"]
    defectos = [juicio for juicio in juicios if juicio["clase"] == "defecto"]
    confirmados = [juicio for juicio in juicios if juicio["clase"] == "confirmado"]
    if instrumentos and not defectos and not confirmados:
        sustituto = _sustituto_toml(enc, espacio)
        if sustituto and sustituto["clase"] == "confirmado":
            confirmados.append(sustituto)
        elif sustituto and sustituto["clase"] == "defecto":
            defectos.append(sustituto)
        elif sustituto and sustituto["clase"] == "instrumento":
            instrumentos.append(sustituto)
    if not juicios and not confirmados:
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
    if defectos:
        _anotar_varios(registro, enc, defectos, hora, enc.repeticiones)
        return registro.fold(enc.id)
    if not confirmados:
        _anotar_varios(registro, enc, instrumentos, hora, enc.repeticiones)
        enc = registro.fold(enc.id)
        if enc.rol == "revisor":
            _devolver_revision(registro, enc, hora)
            enc = registro.fold(enc.id)
        return enc
    serie = _anotar_varios(registro, enc, instrumentos, hora, enc.repeticiones)
    enc = registro.fold(enc.id)
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
            serie + 1,
        )
        return registro.fold(enc.id)
    destino = DestinoEnsayo(registro.base / "destino")
    for pedido in pedidos:
        efecto_id = str(pedido.get("id") or "").strip()
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
        if _efecto_asentado(resultado):
            continue
        if resultado.get("rechazado") or resultado.get("enviado") is False or resultado.get("conflicto"):
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
                serie + 1,
            )
        return registro.fold(enc.id)
    for juicio in confirmados:
        serie += 1
        _anotar_juicio(registro, enc, juicio, hora, serie)
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
        registro.anotar(
            enc.id,
            {"kind": "reserva_cerrada", "data": {"hora": hora}},
            f"reserva-cerrada:{enc.id}:{enc.generacion}",
        )
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
    else:
        _avisar_intento(registro, enc.id, proc, hora)


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


def _marcar_autoridad(registro: Registro, encargo_id: str, alcanzable: bool, hora: str, marca: str) -> None:
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
        },
    )
    _marcar_autoridad(registro, encargo_id, bool(resp.get("alcanzada")), hora, "entrega")


def _mirar_autoridad(registro: Registro, enc: Encargo, hora: str) -> Encargo:
    from .transporte import cargar, operar

    if cargar(registro.base) is None:
        return enc
    resp = operar(registro.base, {"op": "ver", "encargo": enc.id})
    if not resp.get("alcanzada"):
        if enc.autoridad:
            _marcar_autoridad(registro, enc.id, False, hora, "caida")
            return registro.fold(enc.id) or enc
        return enc
    if not enc.autoridad:
        _marcar_autoridad(registro, enc.id, True, hora, "vuelve")
        enc = registro.fold(enc.id) or enc
    estado = str((resp.get("salida") or {}).get("estado") or "")
    if estado in ("deteniendo", "detenido", "cancelado") and enc.estado not in (
        "deteniendo",
        "detenido",
        "cancelado",
        "terminado",
    ):
        ordenar(registro, enc.id, "parar", f"autoridad-{enc.id}", hora)
        enc = registro.fold(enc.id) or enc
    return enc


def _avisar_intento(registro: Registro, encargo_id: str, proc: dict, hora: str) -> None:
    from .transporte import cargar, operar

    if cargar(registro.base) is None:
        return
    resp = operar(
        registro.base,
        {
            "op": "intento",
            "encargo": encargo_id,
            "pid": proc.get("pid"),
            "inicio": proc.get("inicio") or "",
            "boot_id": proc.get("boot_id") or "",
            "job": proc.get("job") or "",
        },
    )
    if not resp.get("alcanzada"):
        _marcar_autoridad(registro, encargo_id, False, hora, "intento")


def observar_intento(
    registro: Registro,
    encargo_id: str,
    pid: int,
    inicio: str,
    hora: str,
    boot_id: str = "",
    job: str = "",
    sesion: str = "",
) -> dict:
    enc = registro.fold(encargo_id)
    if enc is None:
        raise TransicionIlegal("no hay encargo")
    if enc.proc is not None:
        return {"ya": True, "encargo": encargo_id}
    resultado = registro.anotar(
        encargo_id,
        {
            "kind": "intento_abierto",
            "data": {
                "id": f"int-{pid}",
                "pid": int(pid),
                "inicio": str(inicio),
                "boot_id": boot_id,
                "job": job,
                "sesion": sesion,
                "herramientas_observables": True,
                "hora": hora,
            },
        },
        f"intento:{encargo_id}:{pid}:{inicio}",
    )
    if resultado == "ilegal":
        raise TransicionIlegal("no hay autorización para otro intento")
    return vista(registro.fold(encargo_id))
