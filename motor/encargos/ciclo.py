"""Ciclo de vida del encargo. No decide el trabajo de la aplicación."""

from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path

from .copiar import copiar_arbol, vaciar_arbol
from .comprobacion import (
    FalloInstrumento,
    JuicioValidado,
    VERSION_LECTOR_DEEPSEEK,
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
from . import capacidades, necesidades
from .dominio import (
    Encargo,
    FASE_IMPLICITA,
    TOPE_NO_CONCLUYENTES,
    TransicionIlegal,
    alcance_de,
    ajustes_cubiertos,
    bloqueantes_abiertos,
    correcciones_sin_ver,
    puede_recibir_intento,
    revision_independiente_cubre_producto,
    decision_fallo,
    decision_proceso,
    defecto_al_adoptar_fases,
    fuentes_autorizadas,
    huella_bloqueo,
    huella_entrega,
    hubo_avance,
    id_encargo,
    inspeccion_bloquea,
    marca_inspeccion_entregada,
    respuesta_consumida,
    nivel_cumplido,
    sin_nada_en_curso,
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
from .fases import adoptar as adoptar_fases
from .sesion import (
    cerrar,
    crecer_log,
    herramienta_en_curso,
    lanzar_administrado,
    observar,
    preparar_administrado,
    recuperar_arranque,
)
from .suscripciones import CuentaNoDisponible

_UMBRAL_S = 30

# Quietud: un encargo abierto que no espera con hora ni tiene agente, lanzamiento
# o paso externo en curso, y cuyo registro no se mueve. Toda espera con hora
# dura como mucho 300 s (puerto o repo de entrega) y al vencer deja un evento,
# así que 600 s sin ningún evento no puede ser una espera legítima. Las 3
# vueltas seguidas evitan escalar por una sola observación, por ejemplo la
# primera del supervisor tras una caída.
_QUIETUD_S = 600
_QUIETUD_VUELTAS = 3
_RELECTURA_CHECKS_S = 120
_REINTENTO_FORZADO_S = 15


def ahora_iso(epoch: float | None = None) -> str:
    from datetime import datetime, timezone

    if epoch is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolver_entrega(explicita: str | None, repo: str, via: str = "durable") -> str:
    """La vía directa es local; la durable con repo infiere PR salvo nivel explícito."""
    nivel = str(explicita or "").strip()
    if nivel:
        return nivel
    if via == "directa":
        return "terminado"
    if str(repo or "").strip():
        return "pr"
    return "terminado"


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
    modelos_solicitados: tuple[tuple[str, str, str], ...] = (),
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
        if not base_rama:
            base_rama = enc.entrega.base
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
            or enc.entrega.base != base_rama
            or tuple(tuple(x) for x in datos.get("modelos_solicitados", ())) != modelos_solicitados
        ):
            salida["conflicto"] = True
        return salida
    if entrega_nivel != "terminado" and repo and not base_rama:
        from motor.repositorios import rama_predeterminada

        try:
            base_rama = rama_predeterminada(repo, consultar_remoto=False)
        except (OSError, ValueError, subprocess.SubprocessError):
            base_rama = ""
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
        "modelos_solicitados": [list(x) for x in modelos_solicitados],
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
        modelos_solicitados,
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
    """Registra una corrección sin interrumpir al agente. La toma el siguiente
    intento. Sólo un encargo cerrado la rechaza. Un rechazo deja constancia
    pero no gasta la clave: se vuelve a evaluar al repetirla, porque la causa
    puede haber cambiado (por ejemplo, un encargo que pasó a durable)."""
    ukey = f"ajuste:{clave}"
    ukey_rechazo = f"ajuste-rechazo:{clave}"
    prev = registro.por_ukey(ukey)
    rechazo = registro.por_ukey(ukey_rechazo)
    if prev and prev["data"].get("resultado") == "rechazada":
        # Rechazo guardado con la clave por la versión anterior: no la ocupa.
        rechazo = rechazo or prev
        ukey = f"ajuste:{clave}:reintento"
        prev = registro.por_ukey(ukey)
    enc = registro.fold(encargo_id)
    if enc is None:
        raise TransicionIlegal("no hay encargo")
    if prev:
        salida = vista(enc, prev["data"]["recibo"])
        if prev["encargo"] != encargo_id or prev["data"].get("texto") != texto:
            salida["conflicto"] = True
        else:
            salida["retransmision"] = True
        return salida
    if rechazo and (
        rechazo["encargo"] != encargo_id or rechazo["data"].get("texto") != texto
    ):
        # La clave no se gasta con un rechazo, pero sigue siendo de ese texto.
        salida = vista(enc, rechazo["data"]["recibo"])
        salida["conflicto"] = True
        return salida
    recibo = recibo_de(ukey)

    def rechazar(motivo: str) -> dict:
        registro.append(
            encargo_id,
            "recibo",
            {
                "accion": "corregir",
                "texto": texto,
                "recibo": recibo,
                "resultado": "rechazada",
                "motivo": motivo,
                "hora": hora,
            },
            ukey_rechazo,
            hora,
        )
        salida = vista(registro.fold(encargo_id), recibo)
        salida["rechazada"] = True
        salida["motivo"] = motivo
        if rechazo:
            salida["retransmision"] = True
        return salida

    if enc.via == "directa":
        return rechazar(
            "el encargo va por la vía directa: ningún intento del motor recibiría "
            "la corrección; promuévelo a durable"
        )
    datos = {"texto": texto, "recibo": recibo, "hora": hora}
    try:
        reducir(enc, {"kind": "ajuste", "data": datos})
    except TransicionIlegal as exc:
        return rechazar(str(exc))
    resultado = registro.append(encargo_id, "ajuste", datos, ukey, hora)
    if resultado == "ilegal":
        try:
            reducir(registro.fold(encargo_id), {"kind": "ajuste", "data": datos})
        except TransicionIlegal as exc:
            return rechazar(str(exc))
    salida = vista(registro.fold(encargo_id), recibo)
    if resultado == "ajena":
        salida["conflicto"] = True
    return salida


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
    import os

    def ruta_guardada(valor) -> Path:
        texto = str(valor)
        if os.name == "nt" and texto.startswith("\\\\?\\"):
            texto = "\\\\" + texto[8:] if texto.startswith("\\\\?\\UNC\\") else texto[4:]
        return Path(texto)

    for sub in (".codex", ".grok", ".local/share/muse", ".kimi-code/sessions", ".kimi-code/file-history", ".claude/projects", ".qwen/projects", ".qwen/tmp", ".local/share/opencode"):
        fuente = origen / sub
        if fuente.is_dir():
            copiar_arbol(fuente, destino / sub, omitir=("auth.json",))

    indice_kimi = origen / ".kimi-code/session_index.jsonl"
    if indice_kimi.is_file() and not indice_kimi.is_symlink():
        indice_destino = destino / ".kimi-code/session_index.jsonl"
        indice_destino.parent.mkdir(parents=True, exist_ok=True)
        filas = []
        for linea in indice_kimi.read_text(encoding="utf-8").splitlines():
            if not linea.strip():
                continue
            try:
                fila = json.loads(linea)
            except ValueError:
                continue
            if not isinstance(fila, dict) or not all(
                isinstance(fila.get(campo), str) and fila[campo]
                for campo in ("sessionDir", "sessionId", "workDir")
            ):
                continue
            antigua = ruta_guardada(fila["sessionDir"])
            if antigua.is_relative_to(origen):
                fila["sessionDir"] = str(destino / antigua.relative_to(origen))
            filas.append(json.dumps(fila))
        indice_destino.write_text("\n".join(filas) + ("\n" if filas else ""), encoding="utf-8")

    muse = destino / ".local/share/muse/session-index.db"
    if muse.is_file() and not muse.is_symlink():
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(muse)) as db, db:
            filas = db.execute("SELECT session_id, session_dir, session_log_path FROM sessions").fetchall()
            for ident, directorio, log in filas:
                anterior = ruta_guardada(directorio)
                if anterior.is_relative_to(origen):
                    db.execute("UPDATE sessions SET session_dir=?, session_log_path=? WHERE session_id=?",
                               (str(destino / anterior.relative_to(origen)),
                                str(destino / ruta_guardada(log).relative_to(origen)), ident))

    codex = destino / ".codex"
    if codex.is_dir():
        import sqlite3
        from contextlib import closing

        rollouts = {p.name: p.resolve() for p in (codex / "sessions").rglob("*.jsonl")}
        for indice in codex.glob("state_*.sqlite"):
            if indice.is_symlink():
                continue
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


_NOTA_COPIA_SESION = "copia de sesión "


def _copiar_sesion_sin_caer(
    registro: Registro, enc: Encargo, origen: Path, destino: Path, sitio: str, hora: str
) -> None:
    """Una copia de sesión que falla queda anotada en el encargo y el turno sigue:
    la sesión es continuidad, no el resultado, y no justifica tumbar el barrido.

    La nota va como `procedimiento`, que solo suma una línea: un `instrumento_fallo`
    reescribiría la causa y el motivo del encargo por un fallo que no lo detiene.

    Una copia a medias no se usa como sesión. Guardar deja `.copia-incompleta` en el almacén
    mientras copia y la quita al terminar; si el guardado anterior no terminó, vacía el
    almacén antes, para que lo viejo no se mezcle con lo nuevo. Con la marca puesta no se
    restaura: el turno arranca sin sesión. Si falla restaurar, se vacía el runtime del turno."""
    import sqlite3

    marca = ".copia-incompleta"

    def anotar(texto: str) -> None:
        registro.anotar(
            enc.id,
            {"kind": "procedimiento", "data": {"nota": f"{_NOTA_COPIA_SESION}({sitio}): {texto}"[:2000]}},
            f"copia-sesion:{enc.id}:{sitio}:{enc.generacion}",
        )

    if sitio == "restaurar" and (origen / marca).exists():
        anotar("la sesión guardada quedó incompleta (falló una copia anterior); el turno arranca sin ella")
        return
    try:
        if sitio == "guardar":
            if (destino / marca).exists():
                vaciar_arbol(destino)
            destino.mkdir(parents=True, exist_ok=True)
            (destino / marca).touch()
        _copiar_sesion(origen, destino)
        if sitio == "guardar":
            (destino / marca).unlink(missing_ok=True)
    except (OSError, ValueError, sqlite3.Error) as exc:
        try:
            if sitio == "restaurar":
                vaciar_arbol(destino)
            else:
                # Vaciar el almacén pudo llevarse la marca antes de fallar.
                destino.mkdir(parents=True, exist_ok=True)
                (destino / marca).touch()
        except OSError:
            pass
        anotar(f"{type(exc).__name__}: {exc}; se reintenta en el siguiente turno")


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


# Veces que el mismo error al atender un encargo se anota antes de dejar de
# atenderlo: reintentar cada vuelta lo que no cambia solo llena el registro.
TOPE_FALLOS_BARRIDO = 3
_FUENTE_BARRIDO = "barrido"
_CLASE_BARRIDO = "barrido:"
_SIN_REGISTRO: set[tuple[str, str]] = set()
# Vueltas seguidas que falló cada encargo, con la clase del último error. Vive en el
# proceso del supervisor: una vuelta sana la borra, y un registro de eventos no
# distingue una vuelta sana de otra que falló antes de anotar nada.
_RACHA_BARRIDO: dict[str, tuple[str, int]] = {}


def _causa_de_excepcion(exc: BaseException) -> str:
    import traceback

    marco = traceback.extract_tb(exc.__traceback__)[-1:]
    donde = f" en {Path(marco[0].filename).name}:{marco[0].lineno} ({marco[0].name})" if marco else ""
    return f"{type(exc).__name__}: {exc}{donde}"[:2000]


def _contener_fallo_de_barrido(
    registro: Registro, encargo_id: str, exc: Exception, hora: str
) -> dict | None:
    """El error de un encargo se queda en ese encargo.

    Se anota `instrumento_fallo` (fuente `barrido`, clase `barrido:<Excepción>`). La
    TOPE_FALLOS_BARRIDO.ª vuelta seguida del supervisor con la misma clase se anota además un impedimento
    que no se puede comprobar solo: `barrer` deja de atender el encargo hasta que el
    operador mande un ajuste (director corregir), que lo levanta. Después de un ajuste
    la cuenta vuelve a empezar. Devuelve la vista del encargo, o None si no se pudo leer."""
    import sys
    import traceback

    clase = _CLASE_BARRIDO + type(exc).__name__
    causa = _causa_de_excepcion(exc)
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    previa, vueltas = _RACHA_BARRIDO.get(encargo_id, ("", 0))
    seguidos = vueltas if previa == clase else 0
    _RACHA_BARRIDO[encargo_id] = (clase, seguidos + 1)
    try:
        enc = registro.fold(encargo_id)
        if enc is None:
            return None
        if any(i.clase == clase for i in enc.impedimentos):
            return _vista_o_minima(enc)  # ya aparcado: no se llena el registro
        total = sum(
            ev["kind"] == "instrumento_fallo" and ev["data"].get("fuente") == _FUENTE_BARRIDO
            for ev in registro.eventos(encargo_id)
        )
        siguiente = "no se atiende hasta que el operador mande un ajuste (director corregir)"
        _anotar_instrumento(
            registro, enc, _FUENTE_BARRIDO, causa,
            "reintentar en la vuelta siguiente"
            if seguidos + 1 < TOPE_FALLOS_BARRIDO
            else siguiente,
            hora, str(total + 1), clase=clase,
        )
        if seguidos + 1 >= TOPE_FALLOS_BARRIDO:
            _anotar_impedimento(
                registro, enc, clase,
                f"el supervisor falló {seguidos + 1} veces seguidas al atender este encargo: {causa}",
                "que el operador arregle la causa y mande un ajuste (director corregir); "
                "mientras tanto el supervisor no atiende este encargo",
                hora,
            )
            _RACHA_BARRIDO.pop(encargo_id, None)
        return _vista_o_minima(registro.fold(encargo_id) or enc)
    except Exception as segundo:
        if (encargo_id, clase) not in _SIN_REGISTRO:
            _SIN_REGISTRO.add((encargo_id, clase))
            print(
                f"no se pudo anotar el fallo de {encargo_id} ({clase}): "
                f"{type(segundo).__name__}: {segundo}",
                file=sys.stderr,
            )
        return None


def _vista_o_minima(enc: Encargo) -> dict:
    try:
        return vista(enc)
    except TransicionIlegal:
        return {"encargo": enc.id, "estado": enc.estado}


def _aparcado_por_barrido(enc: Encargo | None) -> bool:
    """Aparcado no se atiende, salvo que se esté deteniendo: parar o cancelar un
    encargo aparcado tiene que poder cerrar su proceso."""
    return (
        enc is not None
        and enc.estado != "deteniendo"
        and any(i.clase.startswith(_CLASE_BARRIDO) for i in enc.impedimentos)
    )


def barrer(
    registro: Registro,
    hora: str,
    epoch: float,
    lanzador,
    umbral_s: float = _UMBRAL_S,
    gh=None,
) -> list[dict]:
    """Un paso sobre todos los encargos. Cada uno se atiende por separado: lo que
    lance uno no detiene a los demás ni al supervisor (ver `_contener_fallo_de_barrido`)."""
    informes = []
    for encargo_id in registro.ids():
        try:
            previo = registro.fold(encargo_id)
            if _aparcado_por_barrido(previo):
                informes.append(_vista_o_minima(previo))
                continue
            informes += _barrer_encargos(
                registro, [encargo_id], hora, epoch, lanzador, umbral_s, gh
            )
            _RACHA_BARRIDO.pop(encargo_id, None)
        except Exception as exc:
            informe = _contener_fallo_de_barrido(registro, encargo_id, exc, hora)
            if informe is not None:
                informes.append(informe)
    return informes


def _barrer_encargos(
    registro: Registro,
    ids: list[str],
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
    for encargo_id in ids:
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
                enc = registro.fold(encargo_id) or enc
        else:
            arbol = observar(directorio, proc)
        if enc.estado not in ("cancelado", "terminado"):
            try:
                necesidades.atender(registro, enc, espacio, directorio, hora)
            except (OSError, ValueError) as exc:
                registro.anotar(
                    encargo_id,
                    {
                        "kind": "procedimiento",
                        "data": {
                            "nota": f"el buzón de necesidades falló: {type(exc).__name__}: {exc}",
                            "hora": hora,
                        },
                    },
                    f"buzon-fallo:{encargo_id}:{type(exc).__name__}",
                )
            enc = registro.fold(encargo_id) or enc
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
        por_incorporar = (
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
        )
        if (
            not parada
            and enc.via != "directa"
            and enc.estado in ("recibido", "ejecutando")
        ):
            enc = _reconciliar_checks(
                registro, enc, gh, espacio, hora, epoch, forzar=por_incorporar
            )
        if por_incorporar:
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
            and not enc.impedimento
            and enc.intentos_nuevos
            and enc.estado in ("recibido", "ejecutando")
            and enc.via != "directa"
        ):
            if (
                _aplica_entrega(enc) or _revision_aprobada_vigente(enc)
            ) and _devolver_correcciones(registro, enc, hora):
                enc = registro.fold(encargo_id)
            elif _aplica_entrega(enc):
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
        decision = _decision(enc, arbol, directorio, sin_hecho, hora)
        if sin_llm:
            decision["lanzar"] = False
        if parada:
            decision["lanzar"] = False
            decision["eventos"] = [
                ev for ev in decision["eventos"] if ev["kind"] == "arbol"
            ]
            if arbol == "vivo":
                decision["matar"] = True
        # Cada causa pendiente se levanta con su propia comprobación, nunca con
        # una ajena. La que este host no puede comprobar sigue hasta un ajuste.
        if (
            enc is not None
            and enc.impedimentos
            and not parada
            and enc.via != "directa"
        ):
            resueltas = []
            for pendiente in enc.impedimentos:
                if (
                    _verificar_impedimento(
                        pendiente.clase, enc, espacio, base, registro
                    )
                    is True
                ):
                    resueltas.append(pendiente.clase)
            for clase in resueltas:
                _resolver_impedimento(registro, enc, clase, hora)
            if resueltas:
                enc = registro.fold(encargo_id) or enc
                if (
                    any(c.startswith("inspeccion:") for c in resueltas)
                    and arbol == "ausente" and enc.proc is None and not enc.reserva
                ):
                    enc = _incorporar_revisor(registro, enc, espacio, hora, directorio)
                decision = _decision(enc, arbol, directorio, sin_hecho, hora)
                if sin_llm:
                    decision["lanzar"] = False
        # Y el entorno se comprueba antes de consumir otro turno, no después de
        # que falle. Si la comprobación no puede hacerse, tampoco acredita nada.
        if decision["lanzar"] and not parada and enc.via != "directa":
            sonda = _sonda_entorno(enc, espacio, base)
            if not sonda.ok:
                _anotar_impedimento(
                    registro, enc, "entorno", sonda.causa, sonda.condicion, hora
                )
                enc = registro.fold(encargo_id) or enc
                decision["lanzar"] = False
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
        if parada or necesidades.detenida_ahora(registro, encargo_id):
            necesidades.cerrar_herramientas_del_encargo(directorio)
        elif decision["matar"]:
            # Matar el turno (colgado, vencido) no mata una corrida de CI: no depende de él.
            necesidades.cerrar_herramientas(directorio, solo_del_turno=True)
        if decision["lanzar"] and lanzador is not None and not parada:
            _lanzar_uno(registro, enc, directorio, espacio, hora, lanzador)
            enc = registro.fold(encargo_id)
        enc = _vigilar_quietud(registro, enc, decision, arbol, parada, hora, epoch)
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


def _causa_quietud(enc: Encargo, vueltas: int, segundos: int, ultimo: int) -> str:
    abiertos = ", ".join(h.id for h in bloqueantes_abiertos(enc)) or "ninguno"
    revision = enc.revision_vigente.veredicto if enc.revision_vigente else "ninguna"
    sha = enc.producto.sha_git if enc.producto else ""
    return (
        f"el encargo lleva {vueltas} vueltas del supervisor y {segundos} s sin ningun "
        f"evento nuevo (el ultimo es el {ultimo}), sin espera con hora, sin agente, "
        "sin lanzamiento pendiente ni paso externo que avance. Estado "
        f"{enc.estado}, fase {enc.fase_activa or '-'}, estrategia {enc.estrategia}; "
        f"entrega autorizada {enc.entrega.nivel}, alcanzada {enc.nivel_alcanzado}; "
        f"revision {revision}; bloqueantes abiertos: {abiertos}; sha_git "
        f"{sha[:12] or 'vacio'}, DeepSeek leido en {enc.deepseek_sha[:12] or 'ninguno'}; "
        f"la entrega se considera pendiente: {'si' if _aplica_entrega(enc) else 'no'}. "
        "Se intento: seguir con el paso de entrega o cierre de cada vuelta, sin "
        "efecto nuevo en el registro."
    )


def _vigilar_quietud(
    registro: Registro,
    enc: Encargo,
    decision: dict,
    arbol: str,
    parada: bool,
    hora: str,
    epoch: float,
) -> Encargo:
    """Cuenta vueltas seguidas en las que el encargo no tiene nada en curso y el
    registro no se movio. Al pasar el umbral deja un evento `quietud` con la
    causa y lo pasa a necesita_usuario. Cualquier evento nuevo reinicia la cuenta."""
    ruta = _directorio(registro.base, enc.id) / "quietud.json"
    quieto = (
        not parada
        and arbol == "ausente"
        and not decision["lanzar"]
        and not decision["matar"]
        and sin_nada_en_curso(enc)
    )
    if not quieto:
        ruta.unlink(missing_ok=True)
        return enc
    ultimo = registro.ultimo_evento(enc.id)
    try:
        previo = json.loads(ruta.read_text(encoding="utf-8"))
        if not isinstance(previo, dict):
            previo = {}
    except (OSError, ValueError):
        previo = {}
    if (
        previo.get("evento") == ultimo
        and epoch - float(previo.get("visto") or 0) <= _QUIETUD_S
    ):
        vueltas = int(previo.get("vueltas") or 0) + 1
        desde = float(previo.get("desde") or epoch)
    else:
        vueltas, desde = 1, epoch
    segundos = int(epoch - desde)
    if vueltas >= _QUIETUD_VUELTAS and segundos >= _QUIETUD_S:
        registro.anotar(
            enc.id,
            {
                "kind": "quietud",
                "data": {
                    "causa": _causa_quietud(enc, vueltas, segundos, ultimo),
                    "vueltas": vueltas,
                    "segundos": segundos,
                    "evento": ultimo,
                    "hora": hora,
                },
            },
            f"quietud:{enc.id}:{ultimo}",
        )
        ruta.unlink(missing_ok=True)
        return registro.fold(enc.id) or enc
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(
            {"evento": ultimo, "vueltas": vueltas, "desde": desde, "visto": epoch}
        ),
        encoding="utf-8",
    )
    return enc


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
    registro: Registro,
    enc: Encargo,
    juicio: dict,
    hora: str,
    serie: int,
    clave: str = "",
) -> str:
    sufijo = f":{clave}" if clave else ""
    return registro.anotar(
        enc.id,
        {
            "kind": "comprobacion",
            "data": {
                **juicio,
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "hora": hora,
            },
        },
        f"comprobacion:{enc.id}:{enc.fase_activa}:{juicio['clase']}:"
        f"{juicio.get('firma') or juicio.get('motivo')}:{serie}{sufijo}",
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
    clase: str = "",
    artefacto: str = "",
) -> None:
    """Registra el fallo con la operación que lo produjo. `clase` es la
    identidad estable: no cambia porque el mensaje cambie de redacción.
    `artefacto` es el archivo a revalidar para acreditar la reparación, cuando
    no basta con una ruta canónica del espacio."""
    registro.anotar(
        enc.id,
        {
            "kind": "instrumento_fallo",
            "data": {
                "fuente": fuente,
                "causa": causa,
                "clase": clase,
                "artefacto": artefacto,
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
    rol: str = "responsable",
) -> None:
    registro.anotar(
        enc.id,
        {
            "kind": "estrategia",
            "data": {
                "estrategia": estrategia,
                "rol": rol,
                "motivo": motivo,
                "siguiente": siguiente,
                "hora": hora,
            },
        },
        f"devolucion:{enc.id}:{enc.generacion}:{serie}",
    )


def _sin_avance(
    registro: Registro,
    enc: Encargo,
    abierto: str,
    intentado: str,
    hora: str,
    serie: str,
) -> None:
    """Escalera de una vuelta que no cerro ningun bloqueante: la primera vuelve
    a diagnosticar, la segunda seguida deja el encargo en necesita_usuario.
    `enc` ya incorpora el hallazgo de la vuelta."""
    huella = huella_bloqueo(enc)
    base = f"sin-progreso:{enc.id}:{huella}:{enc.generacion}:{serie}"
    if enc.sin_progreso >= 1:
        registro.anotar(
            enc.id,
            {
                "kind": "sin_progreso",
                "data": {
                    "huella": huella,
                    "diagnostico": (
                        "el mismo bloqueo sigue tras el diagnostico, sin "
                        f"informacion nueva: {abierto}. Se intento: {intentado}"
                    ),
                    "escalar": True,
                    "hora": hora,
                },
            },
            base + ":escalar",
        )
        return
    registro.anotar(
        enc.id,
        {
            "kind": "sin_progreso",
            "data": {"huella": huella, "escalar": False, "hora": hora},
        },
        base + ":diagnostico",
    )
    _devolver(
        registro, enc, "diagnosticar_bloqueo", abierto,
        "diagnosticar por que la correccion no resolvio el bloqueo; "
        "no repetir la misma accion",
        hora, f"diagnostico-{serie}",
    )


_NOTA_SIN_INTENTO = "Correcciones que ningún intento pudo recibir:"
# La primera versión de la nota escribía este prefijo, sin tildes y en minúscula.
_NOTA_SIN_INTENTO_ANTERIOR = "correcciones que ningun intento pudo recibir:"


def _devolver_correcciones(registro: Registro, enc: Encargo, hora: str) -> bool:
    """Una corrección que ningún intento recibió no se cierra ni se entrega:
    el trabajo vuelve al implementador, cuyo prompt la lleva. Si ningún intento
    puede lanzarse (todas las fases preparadas), devolverlo bloquearía el
    cierre para siempre: se cierra y la nota deja dicho qué correcciones
    nadie recibió. La nota no las da por vistas: si un intento vuelve a poder
    lanzarse, la corrección le llega."""
    if enc.estado in ("terminado", "cancelado") or not correcciones_sin_ver(enc):
        return False
    if not puede_recibir_intento(enc):
        sin_recibir = enc.ajustes[ajustes_cubiertos(enc):]
        if sin_recibir:
            registro.anotar(
                enc.id,
                {
                    "kind": "procedimiento",
                    "data": {
                        "nota": _NOTA_SIN_INTENTO + " " + " | ".join(sin_recibir),
                        "ajustes_anotados": len(enc.ajustes),
                        "hora": hora,
                    },
                },
                f"correcciones-sin-intento:{enc.id}:{len(enc.ajustes)}",
            )
        return False
    _devolver(
        registro, enc, "investigar",
        "hay correcciones del propietario que ningún intento recibió",
        "incorporar las correcciones del propietario antes de cerrar o entregar; "
        "lo aprobado que no cambio se conserva",
        hora, f"correccion-{len(enc.ajustes)}",
    )
    return True


def _sonda_entorno(enc: Encargo, espacio: Path, base: Path, agente: str | None = None):
    """`agente` es el que se lanzará en ese intento. Sin él la sonda no prueba el
    shell del CLI: antes de elegir agente no se sabe cuál usará."""
    from .aislamiento import sondear

    return sondear(
        espacio,
        base / "runtime" / enc.id / "sonda",
        fuentes_autorizadas(enc),
        agente,
    )


def _decision(
    enc: Encargo, arbol: str, directorio: Path, sin_hecho: bool, hora: str
) -> dict:
    return decision_proceso(
        enc,
        arbol,
        herramienta_en_curso(directorio),
        sin_hecho,
        enc.herramientas_observables,
        hora,
    )


_VERIFICABLES = ("entorno", "comprobaciones.json", "fases.json", "pedido-efecto.json")

_EVIDENCIA_DE = {
    "entorno": (
        "la sonda del entorno funciona: temporal del CLI, espacio y fuentes autorizadas"
    ),
    "comprobaciones.json": ".interno/comprobaciones.json vuelve a validar",
    "fases.json": (
        ".interno/fases.json valida y su propuesta se puede adoptar "
        "sobre las fases vigentes"
    ),
    "pedido-efecto.json": ".interno/pedido-efecto.json vuelve a validar",
}


def _es_verificable(clase: str) -> bool:
    # El juicio también: su archivo se puede volver a validar contra la versión
    # vigente sin gastar otro turno del revisor.
    return clase in _VERIFICABLES or clase.startswith(("juicio:", "inspeccion:"))


def _evidencia_de(clase: str) -> str:
    if clase.startswith("inspeccion:"):
        return "hay un juicio corregido o una inspección distinta sin consumir"
    if clase.startswith("juicio:"):
        return "el juicio vuelve a validar para la versión revisada"
    return _EVIDENCIA_DE.get(clase, "la comprobación de la causa")


def _apariciones(registro, enc: Encargo, clase: str) -> int:
    """Cuántas veces apareció esta causa. Sube al registrarla y al resolverla,
    así una retransmisión no crea un evento y una recaída sí."""
    return sum(
        1
        for ev in registro.eventos(enc.id)
        if ev["kind"] in ("impedimento", "impedimento_resuelto")
        and ev["data"].get("clase") == clase
    )


def _anotar_impedimento(
    registro, enc: Encargo, clase: str, causa: str, condicion: str, hora: str,
    agente: str = "",
) -> None:
    """`agente` es el que se iba a lanzar si la causa depende de él (su shell)."""
    if any(
        i.clase == clase and i.causa == causa and i.condicion == condicion
        and i.agente == agente
        for i in enc.impedimentos
    ):
        return
    registro.anotar(
        enc.id,
        {
            "kind": "impedimento",
            "data": {
                "clase": clase,
                "causa": causa,
                "condicion": condicion,
                "verificable": _es_verificable(clase),
                "agente": agente,
                "hora": hora,
            },
        },
        f"impedimento:{enc.id}:{clase}:{_apariciones(registro, enc, clase) + 1}",
    )


def _resolver_impedimento(registro, enc: Encargo, clase: str, hora: str) -> None:
    registro.anotar(
        enc.id,
        {
            "kind": "impedimento_resuelto",
            "data": {
                "clase": clase,
                "evidencia": _evidencia_de(clase),
                "hora": hora,
            },
        },
        f"impedimento-resuelto:{enc.id}:{clase}:{_apariciones(registro, enc, clase)}",
    )


def _bloqueo_entorno(registro, enc: Encargo, espacio: Path, hora: str) -> bool:
    """La jaula manda sobre el trabajador: si no deja trabajar, el fallo de
    instrumento no es una corrección que el trabajador pueda hacer."""
    sonda = _sonda_entorno(enc, espacio, registro.base, enc.agente or None)
    if sonda.ok:
        return False
    _anotar_impedimento(
        registro, enc, "entorno", sonda.causa, sonda.condicion, hora, enc.agente or ""
    )
    return True


def _juicio_reparado(
    clase: str, enc: Encargo, registro, espacio: Path
) -> bool | None:
    """Revalida el mismo archivo de juicio contra la versión vigente, sin
    aplicar su veredicto: comprobar no es cerrar con él. Repite también las
    demás condiciones con las que la incorporación lo rechaza —que la
    referencia sea de la versión vigente y que enumere los efectos pedidos—:
    levantar la espera con menos que eso solo mueve el rechazo a la siguiente
    incorporación. `None` si el registro no conserva qué archivo falló."""
    if registro is None:
        return None
    artefacto = ""
    for ev in reversed(registro.eventos(enc.id)):
        if ev["kind"] == "instrumento_fallo" and ev["data"].get("clase") == clase:
            artefacto = str(ev["data"].get("artefacto") or "")
            break
    if not artefacto:
        return None
    ruta = Path(artefacto)
    if clase == "juicio:referencia" or ruta.name == "revision.json":
        try:
            referencia = json.loads(ruta.read_text(encoding="utf-8"))
            if not isinstance(referencia, dict):
                return False
            if _version_de_referencia(referencia, enc) != (
                version_producto(espacio).firma
            ):
                return False
            ruta = Path(str(referencia["resultado"]))
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            return False
    if not ruta.is_file():
        return False
    resultado = leer_juicio(ruta, alcance_de(enc))
    if not isinstance(resultado, JuicioValidado):
        return False
    if clase == "juicio:esquema:efectos":
        try:
            pedidos = _pedidos_efecto(espacio)
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            return False
        return (
            resultado.veredicto != "aprobado"
            or not pedidos
            or set(resultado.efectos_pendientes)
            == {str(p["id"]) for p in pedidos}
        )
    return True


def _verificar_impedimento(
    clase: str, enc: Encargo, espacio: Path, base: Path, registro=None
) -> bool | None:
    """Comprueba por su cuenta si la causa dejó de estar, repitiendo la
    condición que la rechazó. `None` si este host no puede saberlo, que no es
    lo mismo que haberlo comprobado."""
    if clase == "entorno":
        # Se vuelve a probar con el agente que falló, no con uno supuesto por el texto de la
        # causa. Si el lanzador ya no lo elegiría (ni la ruta ni el modelo solicitado para el
        # rol lo incluyen), la causa no le aplica a lo que se lanzará.
        from .rutas import candidatas_de

        agente = next((i.agente for i in enc.impedimentos if i.clase == clase), "")
        candidatas = candidatas_de(enc, enc.rol or "responsable")
        if agente and candidatas and agente not in candidatas:
            return True
        return _sonda_entorno(enc, espacio, base, agente or None).ok
    if clase.startswith("inspeccion:"):
        juicio = _juicio_de_referencia(enc, _directorio(base, enc.id))
        if not isinstance(juicio, JuicioValidado):
            return False
        if juicio.version != version_producto(espacio).firma:
            return False
        if juicio.veredicto != "no_concluyente" or not juicio.inspeccion_faltante:
            return True
        ident = _id_inspeccion(enc, juicio, base)
        fase = enc.fase_actual
        if fase is None:
            return False
        previa = next((s for s in enc.solicitudes if s.id == ident), None)
        return ident != clase.removeprefix("inspeccion:") and (
            previa is None or not respuesta_consumida(fase, marca_inspeccion_entregada(previa))
        )
    if clase.startswith("juicio:"):
        return _juicio_reparado(clase, enc, registro, espacio)
    interno = interno_de(espacio)
    try:
        if clase == "comprobaciones.json":
            leer_declaradas(interno)
            return True
        if clase == "fases.json":
            from .fases import leer_propuesta

            propuesta = leer_propuesta(interno)
            if propuesta is None:
                return True
            # La forma no basta: la espera también pudo nacer de que adoptar
            # la propuesta viola las dependencias o el estado de las fases.
            return not defecto_al_adoptar_fases(enc, propuesta)
        if clase == "pedido-efecto.json":
            _pedidos_efecto(espacio)
            return True
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return False
    return None


def _impedimento_de_instrumento(
    registro, enc: Encargo, clase: str, causa: str, condicion: str, hora: str
) -> bool:
    """El mismo instrumento con el mismo defecto no es información nueva.

    Se mide por la clase del defecto contra el registro de fallos, no por el
    texto: un identificador de intento dentro del mensaje no reinicia la cuenta.
    Una clase distinta sí es un intento de reparación y se deja pasar.
    """
    previas = sum(
        1
        for ev in registro.eventos(enc.id)
        if ev["kind"] == "instrumento_fallo" and ev["data"].get("clase") == clase
    )
    if previas < 2:
        return False
    _anotar_impedimento(registro, enc, clase, causa, condicion, hora)
    return True


def _fallo_juicio(
    registro,
    enc: Encargo,
    clase: str,
    causa: str,
    siguiente: str,
    condicion: str,
    artefacto: str,
    hora: str,
    serie: str,
) -> None:
    """Único camino para registrar que el juicio no se pudo usar.

    Concentra las dos decisiones que esa condición arrastra: la identidad del
    defecto y el control de repetición. Una rama que anota y devuelve el control
    al barrido sin pasar por aquí vuelve a lanzar al revisor en cada vuelta, y
    eso ya pasó con la referencia obsoleta."""
    _anotar_instrumento(
        registro, enc, "juicio", causa, siguiente,
        hora, serie, clase=clase, artefacto=artefacto,
    )
    _impedimento_de_instrumento(registro, enc, clase, causa, condicion, hora)


def _version_de_referencia(referencia: dict, enc: Encargo) -> str:
    """La versión que la referencia declara haber revisado. Es la misma cuenta
    en la incorporación, que rechaza la referencia, y en la verificación, que
    levanta la espera: si difieren, hay una referencia que la verificación
    acepta y la incorporación sigue rechazando."""
    return str(
        referencia.get("version") or (enc.producto.firma if enc.producto else "")
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


def _bloqueo_externo_abierto(enc: Encargo) -> str:
    """Bloqueantes de DeepSeek y checks requeridos en rojo que siguen abiertos,
    con el texto con que `_paso_entrega` los reporta. Vacio si no hay ninguno."""
    partes = []
    abiertos = bloqueantes_abiertos(enc, "deepseek")
    if abiertos:
        partes.append("; ".join(f"{h.id} {h.titulo}" for h in abiertos) + " (DeepSeek)")
    fase = enc.fase_actual
    if fase is not None and fase.fallas_checks:
        partes.append(
            "checks requeridos en rojo: " + ", ".join(sorted(fase.fallas_checks))
        )
    return "; ".join(partes)


def _deepseek_por_releer(enc: Encargo) -> bool:
    return bool(
        enc.deepseek_vigente is not None
        and enc.deepseek_vigente.lector_version < VERSION_LECTOR_DEEPSEEK
        and bloqueantes_abiertos(enc, "deepseek")
        and revision_independiente_cubre_producto(enc)
    )


def _aplica_entrega(enc: Encargo) -> bool:
    if enc.entrega.nivel == "terminado" or enc.revision_vigente is None:
        return False
    if enc.revision_vigente.veredicto != "aprobado":
        return False
    fase = enc.fase_actual
    if (
        fase is not None
        and fase.entrega_bloqueada_sha
        and enc.producto is not None
        and fase.entrega_bloqueada_sha == enc.producto.sha_git
    ):
        # La aprobación se conserva; sólo deja de ser aplicable a la entrega
        # mientras el producto vigente siga en rojo.
        return False
    if not bloqueantes_abiertos(enc):
        return _revision_aprobada_vigente(enc)
    if bloqueantes_abiertos(enc, "independiente"):
        return False
    return (
        enc.deepseek_vigente is not None
        and enc.producto is not None
        and (enc.producto.sha_git != enc.deepseek_sha or _deepseek_por_releer(enc))
        and (
            enc.revision_vigente.version == enc.producto.firma
            or _correccion_deepseek_conserva_aprobadas(enc)
        )
    )


def _evidencia_revision(registro: Registro, enc: Encargo) -> str:
    for ev in reversed(registro.eventos(enc.id)):
        if (
            ev["kind"] == "revision"
            and (ev["data"].get("origen") or "independiente") == "independiente"
            and (ev["data"].get("fase") or enc.fase_activa) == enc.fase_activa
        ):
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
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "hora": hora,
            },
        },
        f"producto:{enc.id}:{enc.fase_activa}:{enc.generacion}:{version.firma}",
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

    if _devolver_correcciones(registro, enc, hora):
        return registro.fold(enc.id)
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
            hora, f"pedidos-{enc.generacion}", clase="pedido-efecto.json",
        )
        if not _impedimento_de_instrumento(
            registro, enc, "pedido-efecto.json", str(exc),
            "que .interno/pedido-efecto.json vuelva a validar", hora,
        ):
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
    sin_recibir = [
        n
        for n in enc.procedimiento
        if n.startswith((_NOTA_SIN_INTENTO, _NOTA_SIN_INTENTO_ANTERIOR))
    ]
    if sin_recibir:
        # El aviso al propietario es esta evidencia: ahí tiene que ir lo que
        # nadie recibió.
        cierre["evidencia"] += " Atención: " + " ".join(sin_recibir)
    _publica(directorio / "cierre.json", json.dumps(cierre, ensure_ascii=False))
    resultado = _anotar_juicio(
        registro,
        enc,
        {**cierre, "ajustes_vistos": ajustes_cubiertos(enc)},
        hora,
        enc.generacion,
        clave="global" if enc.objetivo_completo() else "",
    )
    enc = registro.fold(enc.id)
    if resultado == "ilegal" and correcciones_sin_ver(enc):
        _devolver_correcciones(registro, enc, hora)
        return registro.fold(enc.id)
    if enc.estado == "terminado":
        _publicar_resultado(registro, enc, hora)
    return enc


def _huella_del_pedido(base: Path, juicio: JuicioValidado) -> str:
    pedido = juicio.inspeccion
    if pedido is None:
        return ""
    try:
        recursos = capacidades.cargar(base)
    except capacidades.ConfigInvalida:
        return ""
    return capacidades.huella_recurso(
        recursos, pedido.operacion, pedido.recurso, dict(pedido.parametros)
    )


def _pedido_de_inspeccion(base: Path, juicio: JuicioValidado) -> dict:
    """Lo que el juicio pide al buzón y el estado actual de lo que leería."""
    pedido = juicio.inspeccion
    if pedido is None:
        return {}
    return {
        "operacion": pedido.operacion,
        "recurso": pedido.recurso,
        "parametros": [list(x) for x in sorted(pedido.parametros)],
        "huella": _huella_del_pedido(base, juicio),
    }


def _id_inspeccion(enc: Encargo, juicio: JuicioValidado, base: Path) -> str:
    """La identidad incluye la descripción y el pedido: corregir la consulta
    (otro SQL, otro recurso) es otra inspección, no la ya lanzada. También el
    estado del recurso: si cambió desde la última lectura, pedirlo no repite."""
    huella = _huella_del_pedido(base, juicio)
    pedido = juicio.inspeccion
    estructura = (
        ""
        if pedido is None
        else json.dumps(
            {
                "operacion": pedido.operacion,
                "recurso": pedido.recurso,
                "parametros": sorted(pedido.parametros),
            },
            sort_keys=True,
        )
    )
    clave = f"{enc.id}:{enc.fase_activa}:{juicio.version}:{juicio.inspeccion_faltante}:{estructura}"
    if huella:
        clave += f":{huella}"
    ident = "insp-" + hashlib.sha256(clave.encode()).hexdigest()[:16]
    # Una solicitud detenida antes de despachar nunca se ejecutó: pedir lo mismo
    # otra vez es una solicitud nueva, no la ya consumida.
    reintento = 0
    while any(s.id == ident and s.estado == "detenida" for s in enc.solicitudes):
        reintento += 1
        ident = "insp-" + hashlib.sha256(f"{clave}:reintento{reintento}".encode()).hexdigest()[:16]
    return ident


def _texto_inspeccion_resuelta(enc: Encargo) -> str:
    fase = enc.fase_actual
    if fase is None or not fase.inspeccion_solicitud:
        return ""
    sol = next(
        (s for s in enc.solicitudes if s.id == fase.inspeccion_solicitud), None
    )
    if sol is None or sol.estado in ("pendiente", "pregunta"):
        return ""
    respuesta = necesidades.BUZON / necesidades.RESPUESTAS / f"{sol.id}.json"
    cliente = necesidades.BUZON / necesidades.CLIENTE
    partes = [
        "solicitud: " + sol.id,
        "estado: " + sol.estado,
        f"resultado accesible en el espacio: {respuesta}",
        f"leer el resultado ya obtenido con el cliente {cliente}, subcomando ver {sol.id}",
    ]
    if sol.detalle:
        partes.append("detalle: " + sol.detalle[:400])
    return "; ".join(partes)


def _texto_respuestas_propietario(enc: Encargo) -> str:
    """Lo que el propietario contestó en esta fase, completo y sin recortar.

    No depende de que la solicitud sea la ligada a la inspección: una respuesta
    a otra solicitud de la fase también es información para quien revisa.
    """
    lineas = []
    for sol in enc.solicitudes:
        if sol.fase != enc.fase_activa:
            continue
        for texto in sol.respuestas_propietario:
            lineas.append(f"- a la solicitud {sol.id} ({sol.estado}):\n{texto}")
    if not lineas:
        return ""
    return (
        "Respuestas del propietario en esta fase. Son su palabra completa y vigente: "
        "si dan por hecho o aportan la evidencia que faltaba, úsala.\n"
        + "\n".join(lineas)
    )


def _ligar_inspeccion(
    registro: Registro, enc: Encargo, espacio: Path, juicio: JuicioValidado, hora: str
) -> None:
    """La inspección pendiente pasa por el puente: petición ejecutable si una
    capacidad concedida la cubre, o espera identificada del propietario."""
    fase = enc.fase_actual
    if fase is None:
        return
    ident = _id_inspeccion(enc, juicio, registro.base)
    previa = next((s for s in enc.solicitudes if s.id == ident), None)
    if fase.inspeccion_solicitud == ident and previa is not None:
        if previa.estado == "pregunta" and enc.estado in ("recibido", "ejecutando"):
            marcar_pregunta(
                registro,
                enc.id,
                previa.pregunta or juicio.inspeccion_faltante,
                True,
                hora,
                f"pregunta-inspeccion:{enc.id}:{ident}:{enc.generacion}",
            )
        return
    # Con la liga puesta pero sin solicitud registrada (llegó tarde, con el
    # encargo detenido) el pedido se vuelve a encolar en vez de darse por atendido.
    if previa is not None:
        if respuesta_consumida(fase, marca_inspeccion_entregada(previa)):
            # La respuesta de esa solicitud ya se entregó y consumió: el mismo
            # pedido no re-liga ni gasta otra entrega.
            return
        # El juicio vuelve a pedir una inspección cuya respuesta existe pero no
        # se entregó a esta vuelta: se re-liga para que el próximo turno la reciba.
        registro.anotar(
            enc.id,
            {
                "kind": "inspeccion_solicitud",
                "data": {
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "solicitud": ident,
                    "hora": hora,
                },
            },
            f"inspeccion:{enc.id}:{enc.fase_activa}:{ident}:{enc.generacion}",
        )
        return
    registro.anotar(
        enc.id,
        {
            "kind": "inspeccion_solicitud",
            "data": {
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "solicitud": ident,
                "hora": hora,
            },
        },
        f"inspeccion:{enc.id}:{enc.fase_activa}:{ident}:{enc.generacion}",
    )
    enc = registro.fold(enc.id) or enc
    try:
        recursos = capacidades.cargar(registro.base)
    except capacidades.ConfigInvalida:
        recursos = {}
    rev = enc.revision_vigente.revision_id if enc.revision_vigente else ""
    pregunta = (
        f"La revisión {rev or 'vigente'} de la fase {enc.fase_activa} (versión "
        f"{juicio.version[:12]}) no concluyó: falta «{juicio.inspeccion_faltante}». "
    )
    intento = enc.intento or enc.conductor or "supervisor"
    pedido = juicio.inspeccion
    if pedido is not None and pedido.operacion in capacidades.operaciones_de(
        recursos, pedido.recurso
    ):
        crudo = {
            "id": ident,
            "encargo": enc.id,
            "operacion": pedido.operacion,
            "recurso": pedido.recurso,
            "fase": enc.fase_activa,
            "intento": intento,
            "generacion": enc.generacion,
            "conductor": enc.conductor,
            "motivo": pregunta[:necesidades.MAX_MOTIVO],
            "parametros": dict(pedido.parametros),
        }
        try:
            necesidades.pedir_inspeccion(espacio, enc, crudo)
        except (OSError, ValueError):
            # Sin buzón no hay ejecución: la espera del propietario es la única
            # vía que queda y se registra abajo.
            pass
        else:
            # La liga nace con su solicitud: una liga sin solicitud no bloquea.
            necesidades.registrar_pedida(registro, enc, crudo, hora)
            if recursos[pedido.recurso].get("autorizacion") == "propietario":
                marcar_pregunta(
                    registro,
                    enc.id,
                    pregunta
                    + f"El recurso {pedido.recurso} exige tu autorización para "
                    f"{pedido.operacion}; responde a la solicitud {ident}.",
                    True,
                    hora,
                )
            return
    registro.anotar(
        enc.id,
        {
            "kind": "solicitud",
            "data": {
                "id": ident,
                "operacion": pedido.operacion if pedido is not None else "",
                "recurso": pedido.recurso if pedido is not None else "",
                "fase": enc.fase_activa,
                "intento": intento,
                "generacion": enc.generacion,
                "conductor": enc.conductor,
                "motivo": pregunta[:necesidades.MAX_MOTIVO],
                "parametros": [
                    list(x) for x in (pedido.parametros if pedido is not None else ())
                ],
                "estado": "pregunta",
                "pregunta": (
                    pregunta
                    + "Ninguna capacidad concedida del puente la cubre"
                    + (
                        f": el recurso {pedido.recurso} no está concedido"
                        if pedido is not None
                        else ""
                    )
                    + f". Concede la capacidad o responde a la solicitud {ident}."
                ),
                "hora": hora,
            },
        },
        f"solicitud:{enc.id}:{ident}",
    )
    marcar_pregunta(
        registro,
        enc.id,
        pregunta
        + "Ninguna capacidad concedida del puente la cubre. Concede la capacidad "
        f"o responde a la solicitud {ident} con el dato o la autorización.",
        True,
        hora,
    )


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
            hora, f"pedidos-{enc.generacion}", clase="pedido-efecto.json",
        )
        if not _impedimento_de_instrumento(
            registro, enc, "pedido-efecto.json", error_pedidos,
            "que .interno/pedido-efecto.json vuelva a validar", hora,
        ):
            _devolver(
                registro, enc, "reparar_instrumento", error_pedidos,
                "corregir la solicitud interna sin tocar el producto",
                hora, "pedidos",
            )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    if juicio.veredicto == "aprobado" and pedidos:
        if set(juicio.efectos_pendientes) != {str(p["id"]) for p in pedidos}:
            _fallo_juicio(
                registro, enc, "juicio:esquema:efectos",
                "la aprobacion no enumera en efectos_pendientes los IDs exactos solicitados",
                "volver a emitir el juicio enumerando los efectos aprobados",
                "que el juicio enumere en efectos_pendientes los efectos "
                "solicitados para la versión revisada",
                str(directorio / "revision.json"), hora,
                f"efectos-{enc.generacion}",
            )
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id)
    version_previa = enc.revision_vigente.version if enc.revision_vigente else ""
    enc_previo = enc
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
                "inspeccion_faltante": juicio.inspeccion_faltante,
                "pedido": _pedido_de_inspeccion(registro.base, juicio),
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "hora": hora,
            },
        },
        f"revision-dato:{enc.id}:{enc.fase_activa}:{enc.generacion}:{juicio.version}",
    )
    enc = registro.fold(enc.id)
    if juicio.veredicto == "cambios":
        progreso = hubo_avance(enc_previo, enc)
        abiertos = bloqueantes_abiertos(enc, "independiente")
        primero = abiertos[0] if abiertos else None
        motivo = primero.titulo if primero else (juicio.nota or "hay bloqueantes")
        if not progreso:
            _sin_avance(
                registro, enc,
                "; ".join(f"{h.id} {h.titulo}" for h in abiertos) or motivo,
                (
                    "cambio archivos del producto"
                    if juicio.version != version_previa
                    else "no cambio el producto"
                ) + " y el revisor independiente sigue viendo el bloqueante abierto",
                hora, "cambios",
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
        if enc.estado == "necesita_usuario" and enc.no_concluyentes >= TOPE_NO_CONCLUYENTES:
            # El mismo pedido sin avance N veces seguidas: el encargo ya pidió
            # ayuda al propietario con la causa; no se gasta otro turno.
            pass
        elif juicio.inspeccion_faltante:
            _devolver(
                registro, enc, "inspeccionar",
                juicio.inspeccion_faltante,
                "obtener por el buzon la inspeccion faltante y continuar "
                "esta misma revision; el producto aprobado no se reabre",
                hora, "inspeccion", rol="revisor",
            )
            _ligar_inspeccion(registro, enc, espacio, juicio, hora)
        elif hubo_avance(enc_previo, enc):
            _devolver(
                registro, enc, "investigar",
                juicio.nota or "evidencia insuficiente para aprobar",
                juicio.nota or "aportar la evidencia que falta sin rebajar el pedido",
                hora, "no-concluyente",
            )
        else:
            abiertos = bloqueantes_abiertos(enc)
            _sin_avance(
                registro, enc,
                "; ".join(f"{h.id} {h.titulo}" for h in abiertos)
                or juicio.nota
                or "el revisor no concluyo: evidencia insuficiente para aprobar",
                "el revisor independiente no concluyo y no cerro ningun bloqueante",
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
            _copiar_sesion_sin_caer(
                registro,
                enc,
                registro.base / "runtime" / enc.id / token_intento,
                _almacen_sesion(registro.base, enc.id, enc.rol),
                "guardar",
                hora,
            )
        if outcome.get("autenticacion_expirada"):
            marcar_espera(
                registro,
                enc.id,
                "renovando el acceso de Kimi para continuar la misma sesion",
                hora,
                hora,
            )
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id)
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

    if _bloqueo_entorno(registro, enc, espacio, hora):
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    interno = interno_de(espacio, crear=True)
    enc, error_fases = adoptar_fases(registro, enc, interno, hora)
    if error_fases:
        _anotar_instrumento(
            registro, enc, "fases", error_fases,
            "corregir .interno/fases.json sin devolver el plan al usuario",
            hora, f"fases-{enc.generacion}", clase="fases.json",
        )
        if not _impedimento_de_instrumento(
            registro, enc, "fases.json", error_fases,
            "que la propuesta de .interno/fases.json se pueda adoptar "
            "sobre las fases vigentes", hora,
        ):
            _devolver(
                registro, enc, "reparar_instrumento", error_fases,
                "corregir el formato interno de las fases y volver a intentar",
                hora, "fases",
            )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    try:
        declaradas = leer_declaradas(interno)
        error_declaradas = ""
    except ValueError as exc:
        declaradas = None
        error_declaradas = str(exc)
    version = version_producto(espacio)
    commit_entregado = _commit_entregado(enc, version, espacio)
    registro.anotar(
        enc.id,
        {
            "kind": "producto_listo",
            "data": {
                "version": version.firma,
                "manifiesto": [list(x) for x in version.manifiesto],
                "sha_git": commit_entregado,
                "declara_comprobaciones": declaradas is not None,
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "hora": hora,
            },
        },
        f"producto:{enc.id}:{enc.fase_activa}:{enc.generacion}:{version.firma}",
    )
    enc = registro.fold(enc.id)
    if error_declaradas:
        _anotar_instrumento(
            registro, enc, "comprobacion", error_declaradas,
            "corregir .interno/comprobaciones.json sin tocar el producto",
            hora, f"declaradas-{enc.generacion}", clase="comprobaciones.json",
        )
        if not _impedimento_de_instrumento(
            registro, enc, "comprobaciones.json", error_declaradas,
            "que .interno/comprobaciones.json vuelva a validar", hora,
        ):
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
            hora, f"pedidos-{enc.generacion}", clase="pedido-efecto.json",
        )
        if not _impedimento_de_instrumento(
            registro, enc, "pedido-efecto.json", str(exc),
            "que .interno/pedido-efecto.json vuelva a validar", hora,
        ):
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
            causa_obs = str(observacion["error_instrumento"])
            _anotar_instrumento(
                registro, enc, "comprobacion", causa_obs,
                "corregir las comprobaciones declaradas y volver a observar",
                hora, f"obs-{enc.generacion}", clase="comprobacion",
            )
            if not _impedimento_de_instrumento(
                registro, enc, "comprobacion", causa_obs,
                "que el comando de comprobacion vuelva a ejecutarse", hora,
            ):
                _devolver(
                    registro, enc, "reparar_instrumento", causa_obs,
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
    if commit_entregado and not _deepseek_por_releer(enc):
        abierto = _bloqueo_externo_abierto(enc)
        if abierto:
            _sin_avance(
                registro, enc, abierto,
                "se devolvio el trabajo y el implementador entrego la misma "
                f"version, sin cambios en el arbol del commit {commit_entregado[:12]}",
                hora, f"misma-version-{commit_entregado[:12]}",
            )
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id)
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


def _juicio_de_referencia(enc: Encargo, directorio: Path):
    """El juicio que la referencia señala, o None si no se puede leer."""
    try:
        referencia = json.loads(
            (directorio / "revision.json").read_text(encoding="utf-8")
        )
        return leer_juicio(Path(referencia["resultado"]), alcance_de(enc))
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return None


def _juicio_repite_inspeccion(enc: Encargo, directorio: Path, base: Path) -> bool:
    """Un pedido corregido (otra identidad) es información nueva y se consume
    aunque la solicitud anterior ya tenga su respuesta entregada. Repite cuando
    la identidad es la ligada, o coincide con otra solicitud de inspección cuya
    respuesta ya se entregó (la marca vigente de la fase).
    """
    fase = enc.fase_actual
    if fase is None or not fase.inspeccion_solicitud:
        return True
    resultado = _juicio_de_referencia(enc, directorio)
    if not isinstance(resultado, JuicioValidado):
        return True
    ident = _id_inspeccion(enc, resultado, base)
    if ident == fase.inspeccion_solicitud:
        return True
    otra = next((s for s in enc.solicitudes if s.id == ident), None)
    return (
        otra is not None
        and respuesta_consumida(fase, marca_inspeccion_entregada(otra))
    )


def _consumir_ligada(registro: Registro, enc: Encargo, hora: str) -> None:
    """Al cambiar de consulta, la respuesta ya entregada queda consumida.

    Si no se registra, volver a la consulta anterior la re-liga como si nadie
    la hubiera visto y el revisor gasta otro turno con el mismo error.
    """
    fase = enc.fase_actual
    if fase is None or not fase.inspeccion_solicitud:
        return
    sol = next((s for s in enc.solicitudes if s.id == fase.inspeccion_solicitud), None)
    if sol is None or sol.estado in ("pendiente", "pregunta"):
        return
    marca = marca_inspeccion_entregada(sol)
    if respuesta_consumida(fase, marca):
        return
    registro.anotar(
        enc.id,
        {
            "kind": "inspeccion_entregada",
            "data": {
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "solicitud": sol.id,
                "marca": marca,
                "hora": hora,
            },
        },
        f"inspeccion-entregada:{enc.id}:{enc.fase_activa}:{marca}",
    )


def _salta_inspeccion(
    registro: Registro, enc: Encargo, directorio: Path, hora: str
) -> bool:
    """True cuando el juicio presente no debe consumirse como revisión.

    Un turno muerto no produce juicio: la referencia sigue señalando el que
    una incorporación anterior ya consumió y no se gasta nada, con el gate
    abierto para relanzar con el mismo dato. Un juicio que re-declara el
    pedido ligado acredita que la respuesta entregada se consumió: se anota
    la marca y tampoco gasta revisión.
    """
    fase = enc.fase_actual
    if fase is None or not fase.inspeccion_pendiente:
        return False
    resultado = _juicio_de_referencia(enc, directorio)
    if not isinstance(resultado, JuicioValidado):
        return inspeccion_bloquea(enc) and _juicio_repite_inspeccion(
            enc, directorio, registro.base
        )
    if (
        registro.por_ukey(
            "revision-dato:"
            f"{enc.id}:{enc.fase_activa}:{enc.generacion}:{resultado.version}"
        )
        is not None
    ):
        return True
    ident = _id_inspeccion(enc, resultado, registro.base)
    if ident != fase.inspeccion_solicitud:
        _consumir_ligada(registro, enc, hora)
        enc = registro.fold(enc.id) or enc
        fase = enc.fase_actual or fase
        otra = next((s for s in enc.solicitudes if s.id == ident), None)
        return otra is not None and respuesta_consumida(
            fase, marca_inspeccion_entregada(otra)
        )
    sol = next((s for s in enc.solicitudes if s.id == ident), None)
    if sol is None:
        # La liga apunta a una solicitud que nunca se registró: no hay respuesta
        # que el juicio acredite como consumida. Se consume para que
        # `_ligar_inspeccion` vuelva a encolar el pedido en vez de descartarlo.
        return False
    if sol.estado not in ("pendiente", "pregunta"):
        marca = marca_inspeccion_entregada(sol)
        if fase.inspeccion_entregada != marca:
            registro.anotar(
                enc.id,
                {
                    "kind": "inspeccion_entregada",
                    "data": {
                        "fase": enc.fase_activa,
                        "intento": enc.intento,
                        "solicitud": sol.id,
                        "marca": marca,
                        "hora": hora,
                    },
                },
                f"inspeccion-entregada:{enc.id}:{enc.fase_activa}:{marca}",
            )
    return True


def _incorporar_revisor(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    hora: str,
    directorio: Path,
) -> Encargo:
    from motor.runwrap import _publica

    if _salta_inspeccion(registro, enc, directorio, hora):
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    ref_path = directorio / "revision.json"
    if not ref_path.is_file():
        if _bloqueo_entorno(registro, enc, espacio, hora):
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id)
        _fallo_juicio(
            registro, enc, "juicio:referencia",
            "falta la referencia de la revision",
            "volver a lanzar la revision sobre la misma version",
            "que la referencia de la revisión señale un juicio válido "
            "para la versión revisada",
            str(ref_path), hora, f"sinref-{enc.generacion}",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    try:
        referencia = json.loads(ref_path.read_text(encoding="utf-8"))
        if not isinstance(referencia, dict) or not str(
            referencia.get("resultado") or ""
        ):
            raise ValueError("la referencia no señala el resultado del juicio")
    except (OSError, UnicodeError, ValueError) as exc:
        if _bloqueo_entorno(registro, enc, espacio, hora):
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id) or enc
        causa = f"la referencia de la revisión no se puede utilizar: {exc}"
        _fallo_juicio(
            registro, enc, "juicio:referencia", causa,
            "volver a lanzar la revision sobre la misma version",
            "que la referencia de la revisión señale un juicio válido "
            "para la versión revisada",
            str(ref_path), hora, f"refilegible-{enc.generacion}",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id) or enc
    version_actual = version_producto(espacio)
    if version_actual.firma != _version_de_referencia(referencia, enc):
        _fallo_juicio(
            registro, enc, "juicio:referencia",
            "el producto cambio durante la revision",
            "volver a revisar la version vigente",
            "que la referencia de la revisión señale un juicio válido "
            "para la versión revisada",
            str(ref_path), hora, f"stale-{enc.generacion}",
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
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "hora": hora,
                },
            },
            f"producto:{enc.id}:{enc.fase_activa}:{enc.generacion}:{version_actual.firma}",
        )
        enc = registro.fold(enc.id)
    alcance = alcance_de(enc)
    resultado = leer_juicio(Path(referencia["resultado"]), alcance)
    if isinstance(resultado, FalloInstrumento):
        if _bloqueo_entorno(registro, enc, espacio, hora):
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id)
        # Un juicio ilegible se vuelve a emitir sobre la misma versión: no es
        # trabajo del responsable. El mismo defecto dos veces sí lo detiene.
        _fallo_juicio(
            registro, enc, f"juicio:{resultado.defecto}", resultado.causa,
            "volver a emitir el juicio con el formato pedido sobre la misma version",
            "que el juicio vuelva a validar para la versión revisada",
            str(Path(referencia["resultado"])), hora, f"juicio-{enc.generacion}",
        )
        _publica(directorio / "incorporado", hora)
        return registro.fold(enc.id)
    if resultado.instrumento:
        if _bloqueo_entorno(registro, enc, espacio, hora):
            _publica(directorio / "incorporado", hora)
            return registro.fold(enc.id)
        _anotar_instrumento(
            registro, enc, "comprobacion", resultado.instrumento,
            "reparar el instrumento de comprobacion sin tocar el producto",
            hora, f"instrumento-revisor-{enc.generacion}", clase="comprobacion",
        )
        if not _impedimento_de_instrumento(
            registro, enc, "comprobacion", resultado.instrumento,
            "que el comando de comprobacion vuelva a ejecutarse", hora,
        ):
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


def _rama_de_fase(enc: Encargo, fase) -> str:
    if fase is not None and fase.rama:
        return fase.rama
    if fase is None or len(enc.fases) <= 1:
        return enc.entrega.rama
    return f"{enc.entrega.rama}-{fase.id}"


def _rama_fase(enc: Encargo) -> str:
    return _rama_de_fase(enc, enc.fase_actual)


def _base_fase(enc: Encargo) -> str:
    por_id = {f.id: f for f in enc.fases}
    por_rama = {_rama_de_fase(enc, f): f for f in enc.fases}

    def entrada(fase, visitadas: frozenset[str]) -> str:
        if fase.id in visitadas:
            raise ValueError("las bases de las fases forman un ciclo")
        if fase.nivel_alcanzado not in ("sin_cambios", "integrado", "desplegado"):
            return _rama_de_fase(enc, fase)
        visitadas = visitadas | {fase.id}
        if fase.base:
            antecesora = por_rama.get(fase.base)
            return entrada(antecesora, visitadas) if antecesora else fase.base
        return base_de(fase, visitadas)

    def base_de(fase, visitadas: frozenset[str]) -> str:
        base = enc.entrega.base
        for dep_id, _version in fase.requiere:
            dep = por_id.get(dep_id)
            if dep is not None:
                candidata = entrada(dep, visitadas)
                if candidata != enc.entrega.base:
                    base = candidata
        return base

    fase = enc.fase_actual
    return base_de(fase, frozenset()) if fase is not None else enc.entrega.base


def _ref_existente(espacio: Path, *candidatos: str) -> str:
    for candidato in candidatos:
        if not candidato:
            continue
        r = _git(espacio, "rev-parse", "--verify", "--quiet", f"{candidato}^{{commit}}")
        if r.returncode == 0:
            return candidato
    return ""


def _deps_de(enc: Encargo, fase) -> list:
    salida = []
    if fase is None:
        return salida
    for dep_id, _version in fase.requiere:
        dep = next((f for f in enc.fases if f.id == dep_id), None)
        if dep is not None:
            salida.append(dep)
    return salida


def _traer_base_integrada(espacio: Path, dep_id: str, sha: str) -> str:
    """Trae los cambios de la base donde se integró el squash.

    El squash también contiene esa base. Unir sólo su historia con `-s ours`
    deja fuera archivos ajenos y el PR siguiente propone borrarlos.
    """
    padres = _git(espacio, "rev-parse", "--verify", "--quiet", f"{sha}^@").stdout.split()
    if not padres:
        return ""
    base = padres[0]
    if _git(espacio, "merge-base", "--is-ancestor", base, "HEAD").returncode == 0:
        return ""
    r = _git(
        espacio,
        "-c", "user.name=motor de encargos",
        "-c", "user.email=motor@localhost",
        "merge", "--no-ff", "--quiet",
        "-m", f"incorporar la base integrada de {dep_id} ({base[:12]})",
        base,
    )
    if r.returncode != 0:
        _git(espacio, "merge", "--abort")
        return (
            f"conflicto al incorporar la entrada {dep_id} ({base}): "
            f"{r.stderr.strip()[-300:]}"
        )
    return ""


def _merge_ancestria(espacio: Path, dep_id: str, sha: str) -> str:
    """Incorpora la ancestría de un commit cuyo contenido ya está en HEAD.

    Primero trae la base real del squash. `-s ours` sólo une la historia
    para que el sha integrado cuente como antecesor, sin reescribir el árbol.
    """
    causa = _traer_base_integrada(espacio, dep_id, sha)
    if causa:
        return causa
    r = _git(
        espacio,
        "-c", "user.name=motor de encargos",
        "-c", "user.email=motor@localhost",
        "merge", "-s", "ours", "--no-ff", "--quiet",
        "-m", f"incorporar la entrada {dep_id} ({sha[:12]})",
        sha,
    )
    if r.returncode != 0:
        _git(espacio, "merge", "--abort")
        return (
            f"conflicto al incorporar la entrada {dep_id} ({sha}): "
            f"{r.stderr.strip()[-300:]}"
        )
    return ""


def _incorporar_entradas(
    enc: Encargo, espacio: Path, registro: Registro | None = None, hora: str = ""
) -> str:
    """Incorpora la versión vigente de cada antecesor que la fase no consume.

    Nunca rebasea: `git merge --no-ff` con el commit efectivo del dep. Cuando
    la firma ya se consume, sólo falta la ancestría del commit integrado (un
    squash la reescribe) y se une con `-s ours`, árbol intacto. Devuelve la
    causa si no se pudo.
    """
    fase = enc.fase_actual
    deps = _deps_de(enc, fase)
    if any(dep.integrado_sha for dep in deps):
        # El commit integrado vive en el remoto hasta que alguien lo trae.
        _git(espacio, "fetch", "--quiet", "origin")

    def _anotar(dep, sha: str) -> None:
        if registro is None:
            return
        registro.anotar(
            enc.id,
            {
                "kind": "entrada_incorporada",
                "data": {
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "dep": dep.id,
                    "version": dep.producto.firma,
                    "sha": sha,
                    "hora": hora,
                },
            },
            f"entrada:{enc.id}:{enc.fase_activa}:{dep.id}:{dep.producto.firma}:{sha}",
        )

    for dep in deps:
        version = next((v for d, v in fase.requiere if d == dep.id), "")
        if dep.producto is None:
            continue
        integrado = ""
        if dep.integrado_sha:
            resuelto = _git(
                espacio,
                "rev-parse", "--verify", "--quiet",
                f"{dep.integrado_sha}^{{commit}}",
            )
            if resuelto.returncode != 0:
                return (
                    "no se encontro localmente el commit integrado de la "
                    f"entrada {dep.id} ({dep.integrado_sha})"
                )
            integrado = resuelto.stdout.strip() or dep.integrado_sha
        ancestro_integrado = (
            _git(
                espacio, "merge-base", "--is-ancestor", integrado, "HEAD"
            ).returncode
            == 0
            if integrado
            else False
        )
        if version == dep.producto.firma:
            # El contenido ya se consume, pero un squash de la entrada cambia
            # el commit integrado sin cambiar la firma: ese commit tiene que
            # quedar en la historia de la consumidora.
            if not integrado or ancestro_integrado:
                continue
            causa = _merge_ancestria(espacio, dep.id, integrado)
            if causa:
                return causa
            _anotar(dep, integrado)
            if registro is not None:
                fase = (registro.fold(enc.id) or enc).fase_actual
            continue
        rama_dep = _rama_de_fase(enc, dep)
        base = _ref_existente(
            espacio,
            dep.producto.sha_git,
            f"origin/{rama_dep}",
        )
        if not base:
            return (
                f"no se encontro la version de la entrada {dep.id} "
                f"({dep.producto.sha_git or rama_dep})"
            )
        sha_ref = (
            _git(espacio, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
            .stdout.strip()
            or base
        )
        r = _git(
            espacio,
            "-c", "user.name=motor de encargos",
            "-c", "user.email=motor@localhost",
            "merge", "--no-ff", "--quiet",
            "-m", f"incorporar la entrada {dep.id} ({sha_ref[:12]})",
            sha_ref,
        )
        if r.returncode != 0:
            _git(espacio, "merge", "--abort")
            return (
                f"conflicto al incorporar la entrada {dep.id} ({sha_ref}): "
                f"{r.stderr.strip()[-300:]}"
            )
        _anotar(dep, sha_ref)
        if registro is not None:
            fase = (registro.fold(enc.id) or enc).fase_actual
        if integrado and integrado != sha_ref and not ancestro_integrado:
            causa = _merge_ancestria(espacio, dep.id, integrado)
            if causa:
                return causa
            _anotar(dep, integrado)
            if registro is not None:
                fase = (registro.fold(enc.id) or enc).fase_actual
    return ""


# Veces seguidas que el mismo fallo de git deja esperar al encargo antes de
# pedir ayuda: reintentar cada 5 minutos sin fin no arregla lo que no cambia.
TOPE_FALLOS_REPO = 3
# Eventos que pueden caer entre un reintento y el siguiente sin ser un cambio.
_ENTRE_REINTENTOS = (
    "plazo", "espera", "instrumento_fallo", "reserva", "reserva_cerrada",
    "contexto_entrega",
)


def _clase_de_fallo(causa: str) -> str:
    """La operación que falló, sin el detalle que cambia entre reintentos (la
    salida de git con rutas temporales, pids o hashes): tras el primer `: ` va
    el detalle, y los hashes sueltos del encabezado tampoco cuentan."""
    operacion = str(causa).split(": ", 1)[0]
    return re.sub(r"[0-9a-f]{7,40}", "#", operacion).strip().lower()


def _fallos_de_repo_seguidos(registro: Registro, encargo_id: str, causa: str) -> int:
    """Esperas seguidas por el mismo fallo (la misma operación), sin nada
    distinto entre ellas. Un ajuste, una respuesta, una orden o un intento que sí
    arrancó las cortan."""
    clase = _clase_de_fallo(causa)
    seguidos = 0
    for ev in reversed(registro.eventos(encargo_id)):
        if ev["kind"] == "espera":
            if _clase_de_fallo(ev["data"].get("motivo") or "") != clase:
                break
            seguidos += 1
        elif ev["kind"] == "procedimiento" and str(ev["data"].get("nota") or "").startswith(
            _NOTA_COPIA_SESION
        ):
            continue  # el aviso de una copia de sesión fallida no es un cambio
        elif ev["kind"] not in _ENTRE_REINTENTOS:
            break
    return seguidos


def _fallo_de_repo(
    registro: Registro, enc: Encargo, causa: str, hora: str, serie: str
) -> None:
    """El repo no quedó listo: espera y reintenta. El mismo fallo
    TOPE_FALLOS_REPO veces seguidas pide ayuda al propietario con la causa."""
    _anotar_instrumento(
        registro, enc, "github", causa,
        "dejar accesible el repo declarado y reintentar",
        hora, serie,
    )
    marcar_espera(registro, enc.id, causa, _reanuda_en(300), hora)
    veces = _fallos_de_repo_seguidos(registro, enc.id, causa)
    if veces < TOPE_FALLOS_REPO:
        return
    marcar_pregunta(
        registro,
        enc.id,
        f"Git falló {veces} veces seguidas en la misma operación y el motor dejó "
        f"de reintentar. Último error: {causa}. Corrige el repositorio o el espacio y manda un "
        "ajuste con director corregir para que reintente; también sirve director "
        "parar y después director continuar.",
        True,
        hora,
        f"pregunta-repo:{enc.id}:{hora}",
    )


def _es_rama_sin_fase(enc: Encargo, fase, actual: str, rama: str) -> bool:
    """La rama actual es la rama sin fase de este encargo y `fase` es la
    primera: no depende de ninguna otra, así que su rama es la misma con sufijo."""
    return (
        fase is not None
        and bool(enc.entrega.rama)
        and actual == enc.entrega.rama
        and rama != actual
        and bool(enc.fases)
        and enc.fases[0].id == fase.id
        and not _deps_de(enc, fase)
    )


def _asegurar_repo(
    enc: Encargo,
    espacio: Path,
    registro: Registro | None = None,
    hora: str = "",
) -> str:
    """El espacio de un encargo con entrega es un clon en la rama del encargo.

    Devuelve la causa si no se pudo; el supervisor clona fuera de la jaula,
    sin sesion LLM. Con registro anota las entradas incorporadas.
    """
    from motor.repositorios import resolver_repositorio

    if not enc.entrega.base:
        from motor.repositorios import rama_predeterminada

        try:
            base = rama_predeterminada(enc.entrega.repo)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return f"no se pudo resolver la base de entrega: {exc}"
        if registro is not None:
            registro.anotar(
                enc.id,
                {"kind": "contexto_entrega", "data": {"base": base, "hora": hora}},
                f"contexto-entrega:{enc.id}",
            )
        enc = replace(enc, entrega=replace(enc.entrega, base=base))
    try:
        repo = resolver_repositorio(enc.entrega.repo)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return f"no se pudo resolver el repositorio de entrega: {exc}"
    if (espacio / ".git").exists():
        r = _git(espacio, "remote", "set-url", "origin", repo.remoto)
        if r.returncode != 0:
            return "no se pudo configurar el remoto de publicación"
        rama = _rama_fase(enc)
        actual = _git(espacio, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if actual != rama:
            fase = enc.fase_actual
            if _es_rama_sin_fase(enc, fase, actual, rama) and _git(
                espacio, "rev-parse", "--verify", "--quiet", f"refs/heads/{rama}"
            ).returncode != 0:
                # El trabajo empezó en la rama sin fase y las fases se
                # declararon después: la primera fase es esa misma rama con otro
                # nombre. Se conserva el trabajo, con o sin commit, en vez de
                # crear la rama desde la base y perderlo.
                r = _git(espacio, "branch", "-m", actual, rama)
                if r.returncode != 0:
                    return (
                        f"no se pudo renombrar la rama {actual} a {rama}: "
                        f"{r.stderr.strip()[-300:]}"
                    )
                return _incorporar_entradas(enc, espacio, registro, hora)
            candidatos = [f"origin/{rama}", rama]
            for dep in _deps_de(enc, fase):
                candidatos += [
                    dep.integrado_sha,
                    dep.producto.sha_git if dep.producto else "",
                    f"origin/{_rama_de_fase(enc, dep)}",
                ]
            candidatos += [f"origin/{_base_fase(enc)}", f"origin/{enc.entrega.base}"]
            inicio = _ref_existente(espacio, *candidatos)
            if not inicio:
                return f"no se encontro la base para crear la rama {rama}"
            r = _git(espacio, "checkout", "-B", rama, inicio)
            if r.returncode != 0:
                return (
                    f"no se pudo crear la rama {rama} desde {inicio}: "
                    f"{r.stderr.strip()[-300:]}"
                )
            return _incorporar_entradas(enc, espacio, registro, hora)
        return _incorporar_entradas(enc, espacio, registro, hora)
    if version_producto(espacio).manifiesto:
        return (
            "el espacio ya tiene producto pero no es un clon del repo "
            f"{enc.entrega.repo}; no se mezcla sobre un trabajo existente"
        )
    r = subprocess.run(
        ["git", "clone", "--quiet", "--", repo.fuente, str(espacio / ".clon-tmp")],
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
    rama = _rama_fase(enc)
    base = _base_fase(enc)
    candidatos = []
    for dep in _deps_de(enc, enc.fase_actual):
        candidatos += [
            dep.integrado_sha,
            dep.producto.sha_git if dep.producto else "",
            f"origin/{_rama_de_fase(enc, dep)}",
        ]
    candidatos += [f"origin/{base}", f"origin/{enc.entrega.base}"]
    inicio = _ref_existente(espacio, *candidatos)
    r = _git(espacio, "checkout", "-B", rama, inicio or f"origin/{enc.entrega.base}")
    if r.returncode != 0:
        return (
            f"no se pudo crear la rama {rama} desde "
            f"{base}: {r.stderr.strip()[-300:]}"
        )
    r = _git(espacio, "remote", "set-url", "origin", repo.remoto)
    return "" if r.returncode == 0 else "no se pudo configurar el remoto de publicación"


_FUERA_DEL_PRODUCTO = (
    ":(exclude).interno", ":(exclude)**/.interno",
    ":(exclude)**/__pycache__", ":(exclude)**/.runtime",
)


def _commit_entregado(enc: Encargo, version, espacio: Path) -> str:
    """El commit ya entregado cuando el producto vuelve con la misma firma y el
    arbol de trabajo es exactamente ese commit; vacio en cualquier otro caso."""
    previo = enc.producto
    if previo is None or not previo.sha_git or previo.firma != version.firma:
        return ""
    cabeza = _git(espacio, "rev-parse", "--verify", "--quiet", "HEAD")
    if cabeza.returncode != 0 or cabeza.stdout.strip() != previo.sha_git:
        return ""
    cambios = _git(espacio, "status", "--porcelain", "--", ".", *_FUERA_DEL_PRODUCTO)
    if cambios.returncode != 0 or cambios.stdout.strip():
        return ""
    return previo.sha_git


def _commit_push(enc: Encargo, espacio: Path) -> tuple[str, str]:
    """Commit del producto (sin .interno) y push. Devuelve (sha, causa)."""
    if _git(espacio, "ls-files", "--", ".interno").stdout.strip():
        _git(espacio, "rm", "-r", "--cached", "--quiet", ".interno")
    r = _git(espacio, "add", "-A", "--", ".", *_FUERA_DEL_PRODUCTO)
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
    r = _git(espacio, "push", "--quiet", "origin", _rama_fase(enc))
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
        base = _git(espacio, "rev-parse", f"origin/{_base_fase(enc)}").stdout.strip()
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


def _bloquear_entrega(registro: Registro, enc: Encargo, causa: str, hora: str) -> None:
    """El conflicto de la entrada frena el paso de entrega sobre el producto
    vigente: lo resuelve el responsable en el espacio y su arreglo, con
    producto nuevo, vuelve a habilitar la entrega."""
    if enc.producto is None:
        return
    registro.anotar(
        enc.id,
        {
            "kind": "entrega_bloqueada",
            "data": {
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "sha": enc.producto.sha_git,
                "causa": causa,
                "hora": hora,
            },
        },
        f"entrega-bloqueada:{enc.id}:{enc.fase_activa}:{enc.producto.sha_git}",
    )


def _checks_extra(enc: Encargo, espacio: Path) -> tuple[str, ...]:
    from motor.conf import RepoConf

    base = _ref_existente(espacio, f"origin/{enc.entrega.base}", enc.entrega.base)
    if not base:
        raise ValueError("no se encontró la base autorizada para leer .motor.toml")
    existe = _git(espacio, "ls-tree", "--name-only", base, "--", ".motor.toml")
    if existe.returncode != 0:
        raise ValueError("no se pudo consultar .motor.toml en la base autorizada")
    if not existe.stdout.strip():
        return ()
    contenido = _git(espacio, "show", f"{base}:.motor.toml")
    if contenido.returncode != 0:
        raise ValueError("no se pudo leer .motor.toml en la base autorizada")
    return RepoConf.parse(contenido.stdout).checks_extra


def _ronda_checks(registro: Registro, enc: Encargo, sha: str) -> str:
    """Sufijo de las claves de checks del SHA: un check que vuelve a rojo tras
    haber pasado a verde abre una ronda nueva, no repite el evento ya anotado."""
    verdes = sum(
        1
        for ev in registro.eventos(enc.id)
        if ev["kind"] == "entrega_externa"
        and ev["data"].get("paso") == "checks_verdes"
        and ev["data"].get("sha") == sha
        and (ev["data"].get("fase") or enc.fase_activa) == enc.fase_activa
    )
    return f":ronda{verdes}" if verdes else ""


def _anotar_checks_verdes(
    registro: Registro, enc: Encargo, numero: int, sha: str, hora: str
) -> Encargo:
    registro.anotar(
        enc.id,
        {
            "kind": "entrega_externa",
            "data": {
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "paso": "checks_verdes",
                "repo": enc.entrega.repo,
                "numero": numero,
                "sha": sha,
                "hora": hora,
            },
        },
        f"entrega:{enc.id}:{enc.fase_activa}:checks_verdes:{sha}"
        + _ronda_checks(registro, enc, sha),
    )
    return registro.fold(enc.id)


def _marca_checks(directorio: Path) -> dict:
    try:
        marca = json.loads(
            (directorio / "checks_lectura.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    return marca if isinstance(marca, dict) else {}


def _toca_releer_checks(
    marca: dict, sha: str, epoch: float, forzar: bool, generacion: int
) -> bool:
    """Una lectura por SHA cada `_RELECTURA_CHECKS_S`, como el espaciado de
    `_paso_entrega`. `forzar` se salta el espaciado una sola vez por generación
    y SHA: la incorporación pendiente puede durar muchos barridos, y la marca
    recuerda cuál ya forzó. Un forzado cuya lectura falló sigue pendiente y se
    reintenta pasados `_REINTENTO_FORZADO_S`, sin golpear a GitHub mientras
    responde con error."""
    try:
        desde = epoch - float(marca.get("epoch") or 0)
        reciente = marca.get("sha") == sha and 0 <= desde < _RELECTURA_CHECKS_S
    except (TypeError, ValueError):
        return True
    if not reciente:
        return True
    if not forzar or marca.get("forzada") == f"{generacion}:{sha}":
        return False
    return not marca.get("error") or desde >= _REINTENTO_FORZADO_S


def _marcar_lectura_checks(
    directorio: Path,
    marca: dict,
    sha: str,
    epoch: float,
    generacion: int,
    consume_forzado: bool,
    error: str = "",
) -> None:
    """Deja en disco el intento de lectura, que así sobrevive a un reinicio del
    supervisor. Solo una lectura lograda consume el forzado; un fallo deja el
    forzado pendiente y su causa a la vista."""
    try:
        directorio.mkdir(parents=True, exist_ok=True)
        (directorio / "checks_lectura.json").write_text(
            json.dumps(
                {
                    "sha": sha,
                    "epoch": epoch,
                    "forzada": (
                        f"{generacion}:{sha}"
                        if consume_forzado and not error
                        else marca.get("forzada", "")
                    ),
                    "error": error,
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _reconciliar_checks(
    registro: Registro,
    enc: Encargo,
    gh,
    espacio: Path,
    hora: str,
    epoch: float,
    forzar: bool = False,
) -> Encargo:
    """Relee los checks del SHA que dejó la entrega bloqueada, sin pasar por la
    compuerta de `_aplica_entrega`. Un check requerido que se vuelve a correr
    sobre el mismo SHA y pasa a verde se anota como `checks_verdes`; si sigue
    en rojo o no se puede leer no se anota nada y la lectura se repite pasado
    el espaciado. Con el agente corriendo no lee. Con la incorporación del
    agente pendiente `forzar` obliga una lectura, una sola por generación y
    SHA aunque la incorporación tarde muchos barridos (el comprobador del
    supervisor), para anotar un verde antes de contar la vuelta. El forzado
    se consume al leer, no al intentar: una lectura fallida lo deja pendiente
    y anota su causa en la marca."""
    fase = enc.fase_actual
    if (
        gh is None
        or fase is None
        or not fase.fallas_checks
        or not fase.entrega_bloqueada_sha
        or not enc.pr_numero
        or enc.producto is None
        or fase.entrega_bloqueada_sha != enc.producto.sha_git
        or enc.entrega.nivel == "terminado"
        or (enc.proc is not None and not forzar)
    ):
        return enc
    sha = fase.entrega_bloqueada_sha
    directorio = _directorio(registro.base, enc.id)
    marca = _marca_checks(directorio)
    if not _toca_releer_checks(marca, sha, epoch, forzar, enc.generacion):
        return enc
    try:
        veredicto = gh.checks(
            enc.entrega.repo, enc.pr_numero, sha, _checks_extra(enc, espacio),
            base_politica=enc.entrega.base,
        )
    except Exception as exc:
        _marcar_lectura_checks(
            directorio, marca, sha, epoch, enc.generacion, forzar,
            error=f"{type(exc).__name__}: {exc}",
        )
        return enc
    _marcar_lectura_checks(directorio, marca, sha, epoch, enc.generacion, forzar)
    if not veredicto.green:
        return enc
    return _anotar_checks_verdes(registro, enc, enc.pr_numero, sha, hora)


def _paso_entrega(registro: Registro, enc: Encargo, gh, hora: str) -> Encargo:
    """Entrega externa sin LLM: commit/push del supervisor, PR listo y
    espera de la revision de DeepSeek por SHA. Idempotente por paso+SHA."""
    if _devolver_correcciones(registro, enc, hora):
        return registro.fold(enc.id)
    if nivel_cumplido(enc) and enc.estado != "terminado" and not _deepseek_por_releer(enc):
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
    causa = _asegurar_repo(enc, espacio, registro, hora)
    if causa:
        if causa.startswith("conflicto al incorporar la entrada"):
            _bloquear_entrega(registro, enc, causa, hora)
            _devolver(
                registro, enc, "integrar_antecesor", causa,
                "resolver el conflicto con la entrada del antecesor conservando "
                "el trabajo propio; lo aprobado que no cambio se conserva",
                hora, f"entrada-{enc.generacion}",
            )
            return registro.fold(enc.id)
        _fallo_de_repo(registro, enc, causa, hora, "repo-entrega")
        return registro.fold(enc.id)
    enc = registro.fold(enc.id) or enc
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
    rama = _rama_fase(enc)
    base = (
        enc.entrega.base if enc.entrega.nivel in ("integrado", "desplegado")
        else _base_fase(enc)
    )
    for paso in ("commit", "push"):
        registro.anotar(
            enc.id,
            {
                "kind": "entrega_externa",
                "data": {
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "paso": paso,
                    "repo": enc.entrega.repo,
                    "rama": rama,
                    "base": base,
                    "sha": sha,
                    "recibo": sha,
                    "hora": hora,
                },
            },
            f"entrega:{enc.id}:{enc.fase_activa}:{paso}:{sha}",
        )
    if enc.pr_numero:
        # El PR ya registrado se consulta antes que cualquier operación nueva:
        # un merge aplicado con respuesta perdida se concilia, no se repite.
        try:
            vista_pr = gh.ver(enc.entrega.repo, enc.pr_numero)
            if vista_pr.base != base and vista_pr.estado == "abierto":
                gh.cambiar_base(enc.entrega.repo, enc.pr_numero, base)
                vista_pr = gh.ver(enc.entrega.repo, enc.pr_numero)
        except Exception as exc:
            causa = (
                f"no se pudo consultar el PR #{enc.pr_numero} registrado: {exc}"
            )
            _anotar_instrumento(
                registro, enc, "github", causa,
                "reintentar la consulta del PR ya registrado",
                hora, f"pr-ver-{enc.generacion}",
            )
            marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
            return registro.fold(enc.id)
        numero = enc.pr_numero
        recibo = vista_pr.merge_commit or vista_pr.head
        if (
            vista_pr.estado == "mergeado"
            and vista_pr.base == enc.entrega.base
            and enc.entrega.nivel in ("integrado", "desplegado")
            and enc.nivel_alcanzado == "pr"
        ):
            registro.anotar(
                enc.id,
                {
                    "kind": "entrega_externa",
                    "data": {
                        "fase": enc.fase_activa,
                        "intento": enc.intento,
                        "paso": "integrado",
                        "repo": enc.entrega.repo,
                        "rama": rama,
                        "base": base,
                        "numero": numero,
                        "sha": recibo,
                        "recibo": recibo,
                        "hora": hora,
                    },
                },
                f"entrega:{enc.id}:{enc.fase_activa}:integrado:{enc.pr_sha}",
            )
            enc = registro.fold(enc.id)
            if nivel_cumplido(enc):
                return _intentar_cierre(
                    registro, enc, espacio, directorio, hora
                )
            return enc
    if not enc.pr_numero or (
        vista_pr.estado == "mergeado" and vista_pr.base != base
    ):
        ref_base = _ref_existente(espacio, f"origin/{base}", base)
        if ref_base:
            conteo = _git(espacio, "rev-list", "--count", f"{ref_base}..{sha}")
            if conteo.returncode == 0 and conteo.stdout.strip() == "0":
                if enc.entrega.nivel == "pr":
                    registro.anotar(
                        enc.id,
                        {
                            "kind": "entrega_externa",
                            "data": {
                                "fase": enc.fase_activa,
                                "intento": enc.intento,
                                "paso": "sin_cambios",
                                "repo": enc.entrega.repo,
                                "rama": rama,
                                "base": base,
                                "sha": sha,
                                "recibo": sha,
                                "evidencia": (
                                    f"fase {enc.fase_activa} sin cambios publicables "
                                    f"sobre {base}; se entrega el producto revisado "
                                    "como evidencia, sin PR"
                                ),
                                "hora": hora,
                            },
                        },
                        f"entrega:{enc.id}:{enc.fase_activa}:sin_cambios:{sha}",
                    )
                    return _intentar_cierre(
                        registro, registro.fold(enc.id) or enc, espacio, directorio, hora
                    )
                if enc.entrega.nivel in ("integrado", "desplegado"):
                    # El sha ya está contenido en la base: integración ya
                    # aplicada, conciliada con la punta real de esa base.
                    punta = (
                        _git(espacio, "rev-parse", "--verify", "--quiet", f"{ref_base}^{{commit}}")
                        .stdout.strip()
                    )
                    registro.anotar(
                        enc.id,
                        {
                            "kind": "entrega_externa",
                            "data": {
                                "fase": enc.fase_activa,
                                "intento": enc.intento,
                                "paso": "integrado",
                                "repo": enc.entrega.repo,
                                "rama": rama,
                                "base": base,
                                "sha": punta,
                                "recibo": punta,
                                "evidencia": (
                                    f"fase {enc.fase_activa} ya contenida en {base} "
                                    f"en {punta}; conciliada como integrada"
                                ),
                                "hora": hora,
                            },
                        },
                        f"entrega:{enc.id}:{enc.fase_activa}:integrado:{sha}",
                    )
                    enc = registro.fold(enc.id)
                    if nivel_cumplido(enc):
                        return _intentar_cierre(
                            registro, enc, espacio, directorio, hora
                        )
                    # desplegado sin diff: no se abre un PR vacío.
                    enc = _paso_despliegue(registro, enc, espacio, hora)
                    if nivel_cumplido(enc) and enc.estado != "terminado":
                        return _intentar_cierre(
                            registro, enc, espacio, directorio, hora
                        )
                    return enc
        try:
            pr = gh.abrir_pr(
                enc.entrega.repo,
                rama,
                base,
                f"{enc.clave}: {' '.join(enc.texto.split())[:72]}",
                f"Encargo {enc.id}. Revision independiente aprobada.",
            )
            numero = int(pr.numero)
            registro.anotar(
                enc.id,
                {
                    "kind": "entrega_externa",
                    "data": {
                        "fase": enc.fase_activa,
                        "intento": enc.intento,
                        "paso": "pr_abierto",
                        "repo": enc.entrega.repo,
                        "rama": rama,
                        "base": base,
                        "numero": numero,
                        "sha": pr.head or sha,
                        "recibo": str(numero),
                        "hora": hora,
                    },
                },
                f"entrega:{enc.id}:{enc.fase_activa}:pr_abierto:{numero}",
            )
            enc = registro.fold(enc.id)
            if pr.borrador:
                gh.marcar_listo(enc.entrega.repo, numero)
            registro.anotar(
                enc.id,
                {
                    "kind": "entrega_externa",
                    "data": {
                        "fase": enc.fase_activa,
                        "intento": enc.intento,
                        "paso": "pr_listo",
                        "repo": enc.entrega.repo,
                        "rama": rama,
                        "base": base,
                        "numero": numero,
                        "sha": sha,
                        "recibo": str(numero),
                        "hora": hora,
                    },
                },
                f"entrega:{enc.id}:{enc.fase_activa}:pr_listo:{numero}:{sha}",
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
    inicio_vuelta = enc
    try:
        veredicto_ci = gh.checks(
            enc.entrega.repo, numero, sha, _checks_extra(enc, espacio),
            base_politica=enc.entrega.base,
        )
    except Exception as exc:
        causa = f"no se pudieron leer los checks del PR #{numero}: {exc}"
        _anotar_instrumento(
            registro, enc, "github", causa,
            "reintentar la lectura de checks del mismo SHA",
            hora, f"checks-leer-{sha[:12]}",
        )
        marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
        return registro.fold(enc.id)
    if not veredicto_ci.green:
        if veredicto_ci.fallas:
            fallas = sorted(str(x) for x in veredicto_ci.fallas)
            causa_ci = "checks requeridos en rojo: " + ", ".join(fallas)
            registro.anotar(
                enc.id,
                {
                    "kind": "entrega_bloqueada",
                    "data": {
                        "fase": enc.fase_activa,
                        "intento": enc.intento,
                        "sha": sha,
                        "causa": causa_ci,
                        "fallas": fallas,
                        "hora": hora,
                    },
                },
                f"entrega-bloqueada:{enc.id}:{enc.fase_activa}:{sha}"
                + _ronda_checks(registro, enc, sha),
            )
            enc = registro.fold(enc.id)
            if hubo_avance(inicio_vuelta, enc):
                _devolver(
                    registro, enc, "investigar", causa_ci,
                    "corregir los checks requeridos en rojo del PR de esta fase; "
                    "lo aprobado que no cambio se conserva",
                    hora, f"checks-{sha[:12]}",
                )
            else:
                _sin_avance(
                    registro, enc, causa_ci,
                    "se corrigio y se volvio a publicar el PR, pero siguen en "
                    "rojo los mismos checks requeridos",
                    hora, f"checks-{sha[:12]}",
                )
        else:
            marcar_espera(
                registro, enc.id,
                "esperando checks requeridos del PR "
                f"#{numero}: {', '.join(veredicto_ci.faltan)}",
                _reanuda_en(120), hora,
            )
        return registro.fold(enc.id)
    fase_ci = enc.fase_actual
    if fase_ci is not None and fase_ci.fallas_checks:
        enc = _anotar_checks_verdes(registro, enc, numero, sha, hora)
    if enc.deepseek_sha != sha or _deepseek_por_releer(enc):
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
                            "fase": enc.fase_activa,
                            "intento": enc.intento,
                            "paso": "deepseek_disparado",
                            "repo": enc.entrega.repo,
                            "numero": numero,
                            "sha": sha,
                            "hora": hora,
                        },
                    },
                    f"entrega:{enc.id}:{enc.fase_activa}:deepseek_disparado:{sha}",
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
        relectura_ds = enc.deepseek_sha == sha
        fusion = list(hallazgos)
        for h in bloqueantes_abiertos(enc, "deepseek"):
            if h.id not in nuevos_bloq:
                fusion.append(replace(h, estado="resuelto"))
        registro.anotar(
            enc.id,
            {
                "kind": "revision",
                "data": {
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "revision_id": "rev_ds_" + sha[:12],
                    "version": enc.producto.firma if enc.producto else "",
                    "base_version": enc.deepseek_sha,
                    "alcance": "correccion" if enc.deepseek_sha else "completa",
                    "veredicto": "cambios" if nuevos_bloq else "aprobado",
                    "revisor": "deepseek",
                    "origen": "deepseek",
                    "lector_version": VERSION_LECTOR_DEEPSEEK,
                    "cubre": [
                        h.id for h in bloqueantes_abiertos(enc, "deepseek")
                    ],
                    "hallazgos": [h.como_dict() for h in fusion],
                    "hora": hora,
                },
            },
            f"revision-ds:{enc.id}:{enc.fase_activa}:{sha}:lector{VERSION_LECTOR_DEEPSEEK}",
        )
        registro.anotar(
            enc.id,
            {
                "kind": "entrega_externa",
                "data": {
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "paso": "deepseek_leido",
                    "repo": enc.entrega.repo,
                    "numero": numero,
                    "sha": sha,
                    "recibo": sha,
                    "hora": hora,
                },
            },
            f"entrega:{enc.id}:{enc.fase_activa}:deepseek_leido:{sha}:lector{VERSION_LECTOR_DEEPSEEK}",
        )
        enc = registro.fold(enc.id)
        abiertos_ds = bloqueantes_abiertos(enc, "deepseek")
        if abiertos_ds:
            if relectura_ds or hubo_avance(inicio_vuelta, enc):
                _devolver(
                    registro, enc, "investigar", abiertos_ds[0].titulo,
                    "corregir " + ", ".join(h.id for h in abiertos_ds)
                    + " (DeepSeek); lo aprobado que no cambio se conserva",
                    hora, "deepseek",
                )
            else:
                _sin_avance(
                    registro, enc,
                    "; ".join(f"{h.id} {h.titulo}" for h in abiertos_ds)
                    + " (DeepSeek)",
                    "se corrigio y se volvio a publicar el PR, pero DeepSeek "
                    "mantiene el mismo bloqueante",
                    hora, f"deepseek-{sha[:12]}",
                )
            return registro.fold(enc.id)
    enc = registro.fold(enc.id)
    if enc.entrega.nivel in ("integrado", "desplegado") and enc.nivel_alcanzado == "pr":
        try:
            vista_pr = gh.ver(enc.entrega.repo, enc.pr_numero)
            if vista_pr.base != enc.entrega.base:
                raise ValueError(
                    f"el PR apunta a {vista_pr.base}; falta integrar en {enc.entrega.base}"
                )
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
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "paso": "integrado",
                    "repo": enc.entrega.repo,
                    "numero": enc.pr_numero,
                    "sha": vista_pr.merge_commit or vista_pr.head,
                    "recibo": vista_pr.merge_commit or vista_pr.head,
                    "hora": hora,
                },
            },
            f"entrega:{enc.id}:{enc.fase_activa}:integrado:{enc.pr_sha}",
        )
        enc = registro.fold(enc.id)
    enc = _paso_despliegue(registro, enc, espacio, hora)
    if nivel_cumplido(enc):
        return _intentar_cierre(registro, enc, espacio, directorio, hora)
    return enc


def _paso_despliegue(
    registro: Registro, enc: Encargo, espacio: Path, hora: str
) -> Encargo:
    """Nunca reabre un PR vacío: solo sella con el efecto que corresponde a un
    pedido vivo, con la misma identidad que exige el cierre."""
    if enc.entrega.nivel != "desplegado" or enc.nivel_alcanzado != "integrado":
        return enc
    try:
        pedidos = [
            p for p in _pedidos_efecto(espacio) if p["operacion"] == "desplegar"
        ]
    except (OSError, ValueError, TypeError) as exc:
        _anotar_instrumento(
            registro, enc, "adaptador", str(exc),
            "corregir id, operacion, clave y cuerpo en .interno/pedido-efecto.json",
            hora, f"pedidos-{enc.generacion}", clase="pedido-efecto.json",
        )
        _anotar_impedimento(
            registro, enc, "pedido-efecto.json", str(exc),
            "que .interno/pedido-efecto.json vuelva a validar", hora,
        )
        return registro.fold(enc.id) or enc
    efecto = None
    for pedido in pedidos:
        efecto = next(
            (
                e
                for e in enc.efectos
                if e.id == pedido["id"]
                and e.operacion == pedido["operacion"]
                and e.clave == pedido["clave"]
                and e.cuerpo == pedido["cuerpo"]
                and e.estado == "aplicado"
                and e.recibo
            ),
            None,
        )
        if efecto is not None:
            break
    if efecto is None and pedidos:
        from .frontera import DestinoEnsayo, despachar

        destino = DestinoEnsayo(registro.base / "destino")
        for pedido in pedidos:
            despachar(
                registro,
                enc.id,
                destino,
                str(pedido["id"]),
                str(pedido["operacion"]),
                str(pedido["clave"]),
                str(pedido["cuerpo"]),
                hora,
            )
        enc = registro.fold(enc.id)
        efecto = None
        for pedido in pedidos:
            efecto = next(
                (
                    e
                    for e in enc.efectos
                    if e.id == pedido["id"]
                    and e.operacion == pedido["operacion"]
                    and e.clave == pedido["clave"]
                    and e.cuerpo == pedido["cuerpo"]
                    and e.estado == "aplicado"
                    and e.recibo
                ),
                None,
            )
            if efecto is not None:
                break
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
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "paso": "desplegado",
                "repo": enc.entrega.repo,
                "numero": enc.pr_numero,
                "sha": enc.pr_sha,
                "recibo": efecto.recibo,
                "hora": hora,
            },
        },
        f"entrega:{enc.id}:{enc.fase_activa}:desplegado:{efecto.id}",
    )
    return registro.fold(enc.id) or enc


def _consultar_wsl(enc: Encargo) -> None:
    """Pregunta a la distro, sin turno, si puede correr a los agentes que este encargo podría
    usar. El lanzador, que sí corre con el turno, solo lee la respuesta recordada: una distro
    lenta, parada o colgada tarda aquí y no detiene a los demás encargos."""
    from motor.plataforma import jaula_wsl

    from .aislamiento import usa_wsl

    candidatos = dict.fromkeys(
        [*(a for _r, a, _m in enc.modelos_solicitados), *enc.agentes]
    )
    for agente in candidatos:
        if usa_wsl(agente):
            jaula_wsl.causa_no_disponible(agente)


class _FaltaSonda(Exception):
    """El lanzamiento necesita la sonda de `agente` y no puede correrla con el turno tomado."""

    def __init__(self, agente: str, restos: tuple[Path, ...]):
        super().__init__(agente)
        self.agente = agente
        self.restos = restos


def _lanzar_uno(
    registro, enc: Encargo, directorio: Path, espacio: Path, hora: str, lanzador
) -> None:
    """La sonda del entorno puede tardar un minuto (una distro que arranca, un `git push`) o
    colgarse: no corre dentro del turno, que es el de todos los encargos. Si el agente que se
    va a lanzar la necesita, el primer paso se deshace, la sonda corre sin turno y el segundo
    paso empieza de cero con su resultado, releyendo el estado bajo el turno."""
    import shutil

    from motor.plataforma import jaula_wsl

    sonda = None
    for _ in range(3):
        _consultar_wsl(enc)
        try:
            with jaula_wsl.sin_consultar():
                return _lanzar_uno_en_turno(
                    registro, enc, directorio, espacio, hora, lanzador, sonda
                )
        except _FaltaSonda as falta:
            for resto in falta.restos:
                shutil.rmtree(resto, ignore_errors=True)
            sonda = (
                falta.agente,
                _sonda_entorno(enc, espacio, registro.base, falta.agente),
            )


def _lanzar_uno_en_turno(
    registro, enc: Encargo, directorio: Path, espacio: Path, hora: str, lanzador,
    sonda: tuple | None = None,
) -> None:
    from motor.runwrap import _publica

    with turno(registro.base):
        enc = registro.fold(enc.id) or enc
        if (
            enc.via == "directa"
            or enc.proc is not None
            or enc.reserva
            or enc.impedimento
            or leer_parada_local(registro.base)
            or (enc.fases and all(f.estado == "preparada" for f in enc.fases))
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
        _copiar_sesion_sin_caer(
            registro, enc, _almacen_sesion(registro.base, enc.id, rol), runtime, "restaurar", hora
        )
        if enc.entrega.nivel != "terminado" and rol == "responsable":
            causa_repo = _asegurar_repo(enc, espacio, registro, hora)
            if causa_repo:
                if causa_repo.startswith("conflicto al incorporar la entrada"):
                    # El conflicto se resuelve en el espacio: este turno corre
                    # con la entrega bloqueada y su arreglo desbloquea.
                    _bloquear_entrega(registro, enc, causa_repo, hora)
                    _devolver(
                        registro, enc, "integrar_antecesor", causa_repo,
                        "resolver el conflicto con la entrada del antecesor "
                        "conservando el trabajo propio; lo aprobado que no "
                        "cambio se conserva",
                        hora, f"entrada-{generacion}",
                    )
                else:
                    _fallo_de_repo(registro, enc, causa_repo, hora, f"repo-{generacion}")
                    return
            # Las entradas incorporadas tocan las fases; el replace local de
            # generacion y conductor sigue vigente para este lanzamiento.
            fresco = registro.fold(enc.id) or enc
            enc = replace(
                enc,
                fases=fresco.fases,
                entrega=fresco.entrega,
                motivo=fresco.motivo,
                causa=fresco.causa,
                siguiente=fresco.siguiente,
                estrategia=fresco.estrategia,
            )
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
            try:
                recursos = capacidades.cargar(registro.base)
            except capacidades.ConfigInvalida:
                recursos = {}
            try:
                necesidades.preparar(espacio, enc, token, recursos)
            except (OSError, ValueError) as exc:
                causa = f"no se pudo preparar el buzón de necesidades: {exc}"
                _anotar_instrumento(
                    registro, enc, "buzon", causa,
                    "retirar el enlace o directorio que bloquea el buzón y reintentar",
                    hora, f"buzon-{generacion}",
                )
                marcar_espera(registro, enc.id, causa, _reanuda_en(300), hora)
                return
            contenido += "\n\n" + necesidades.bloque_capacidades(espacio, recursos)
            resuelta = _texto_inspeccion_resuelta(enc)
            if resuelta:
                contenido += (
                    "\n\nInspeccion pendiente de la vuelta anterior, ya obtenida "
                    "por el buzon: " + resuelta
                    + ". Continua esta revision sobre la misma version con ese dato."
                )
            contestado = _texto_respuestas_propietario(enc)
            if contestado:
                contenido += "\n\n" + contestado
            observables = capacidades.recursos_observables(recursos)
            if observables:
                contenido += (
                    "\n\nSi un recurso observable del catalogo ("
                    + ", ".join(observables)
                    + ") cambio desde la ultima lectura (otro tamano, hash o fecha), "
                    "pedirlo de nuevo no es repetir la inspeccion: declaralo otra vez "
                    "en inspeccion. Los demas recursos (por ejemplo los remotos por "
                    "http) no se pueden observar: pedir lo mismo sí cuenta como repetir."
                )
        else:
            interno_de(espacio, crear=True)
            contenido = prompt_responsable(enc)
            try:
                recursos = capacidades.cargar(registro.base)
            except capacidades.ConfigInvalida:
                recursos = {}
            try:
                necesidades.preparar(espacio, enc, token, recursos)
            except (OSError, ValueError) as exc:
                causa = f"no se pudo preparar el buzón de necesidades: {exc}"
                _anotar_instrumento(
                    registro, enc, "buzon", causa,
                    "retirar el enlace o directorio que bloquea el buzón y reintentar",
                    hora, f"buzon-{generacion}",
                )
                marcar_espera(registro, enc.id, causa, _reanuda_en(300), hora)
                return
            contenido += "\n\n" + instrucciones_responsable(
                interno_de(espacio), necesidades.bloque_capacidades(espacio, recursos)
            )
            contestado = _texto_respuestas_propietario(enc)
            if contestado:
                contenido += "\n\n" + contestado
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
                    "Las instrucciones del usuario prevalecen. Usa las skills, herramientas y apoyos "
                    "que aporten al pedido con los accesos concedidos. Elige pruebas y revisiones "
                    "segun el comportamiento, el riesgo y la evidencia que haga falta. Conserva lo "
                    "ya comprobado y repitelo cuando exista un motivo concreto."
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
            from motor.plataforma.rutas_wsl import texto_a_wsl

            from .aislamiento import usa_wsl

            en_wsl = usa_wsl(agente_lanzado)
            if en_wsl:
                # El agente lee su pedido dentro de la distro: las rutas de Windows del texto
                # (el .interno, el buzon, las observaciones) pasan a /mnt/<unidad>/. El
                # ejecutable es el de la distro; el de la generacion es de Windows.
                _publica(prompt, texto_a_wsl(contenido))
            if cmd and gen_path is not None and not en_wsl:
                cmd = vincular(
                    gen_path, cmd, funcion="planear" if rol == "revisor" else "codigo",
                    modelo_explicito=any(r == rol for r, _a, _m in enc.modelos_solicitados),
                )
        except Exception:
            _liberar_flota()
            raise
        if not cmd:
            _liberar_flota()
            return
        if en_wsl:
            if sonda is None or sonda[0] != agente_lanzado:
                # La sonda no corre con el turno tomado ni con la flota reservada: se suelta
                # todo, se prueba fuera y se vuelve a empezar con el resultado.
                _liberar_flota()
                raise _FaltaSonda(agente_lanzado, (directorio, runtime))
            if not sonda[1].ok:
                _liberar_flota()
                _anotar_impedimento(
                    registro, enc, "entorno", sonda[1].causa, sonda[1].condicion, hora,
                    agente_lanzado,
                )
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
                        "ajustes": len(enc.ajustes),
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
                    "data": {
                        "token": token,
                        "host": socket.gethostname(),
                        "fase": enc.fase_activa,
                        "intento": enc.intento,
                        "hora": hora,
                    },
                },
                f"conductor-local:{enc.id}:{token}",
            )
            _publica(raiz / "actual", directorio.name)
            from motor import plataforma

            plataforma.sincroniza_directorio(raiz)
            proc = lanzar_administrado(directorio, token)
        except CuentaNoDisponible as exc:
            _liberar_flota()
            marcar_pregunta(registro, enc.id, str(exc), True, hora)
            return
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
    registro: Registro,
    encargo_id: str,
    texto: str,
    bloquea: bool,
    hora: str,
    ukey: str = "",
) -> dict:
    registro.anotar(
        encargo_id,
        {
            "kind": "pregunta",
            "data": {"texto": texto, "bloquea_todo": bloquea, "hora": hora},
        },
        ukey
        or f"pregunta:{encargo_id}:{hashlib.sha256(texto.encode()).hexdigest()[:12]}",
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
    modelos_solicitados: tuple[tuple[str, str, str], ...] = (),
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
            **{rol: modelo for rol, _agente, modelo in modelos_solicitados},
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
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
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
                    "fase": enc.fase_activa,
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
    fase_id = enc.fase_activa
    revision = next(
        (
            ev["data"]
            for ev in reversed(eventos)
            if ev["kind"] == "revision"
            and (ev["data"].get("origen") or "independiente") == "independiente"
            and (ev["data"].get("fase") or fase_id) == fase_id
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


def _asociacion_conductor(registro: Registro, enc: Encargo, token: str) -> str:
    previo = registro.por_ukey(f"conductor-fase:{enc.id}:{token}")
    if previo is None:
        return ""
    return str(previo["data"].get("fase") or "")


def _asociar_conductor(
    registro: Registro, enc: Encargo, token: str, fase: str, hora: str
) -> None:
    if not token or not fase:
        return
    registro.anotar(
        enc.id,
        {
            "kind": "conductor_fase",
            "data": {"token": token, "fase": fase, "hora": hora},
        },
        f"conductor-fase:{enc.id}:{token}",
    )


def _fase_de_resultado(
    registro: Registro, enc: Encargo, token: str, version: str, hora: str
) -> str:
    """Fase que produjo el resultado, sin tomarla de la fase activa del momento.

    La asociación durable conductor-fase manda y se guarda en cualquier ruta que
    acepte el resultado, no solo cuando hay que recurrir a la fase activa. Después
    valen la fase del intento y la versión del producto.
    """
    ligada = _asociacion_conductor(registro, enc, token)
    if ligada:
        return ligada
    fase = ""
    for ev in reversed(registro.eventos(enc.id)):
        if ev["kind"] == "conductor" and str(ev["data"].get("token") or "") == token:
            fase = str(ev["data"].get("fase") or "")
            break
    if not fase:
        candidatas = [
            candidata.id
            for candidata in enc.fases
            if (candidata.producto is not None and candidata.producto.firma == version)
            or (candidata.revisado is not None and candidata.revisado.firma == version)
            or (
                candidata.revision_vigente is not None
                and candidata.revision_vigente.version == version
            )
        ]
        if len(candidatas) == 1:
            fase = candidatas[0]
        elif len(candidatas) > 1:
            return ""
    if not fase:
        fase = enc.fase_activa or (FASE_IMPLICITA if not enc.fases else "")
    if fase:
        _asociar_conductor(registro, enc, token, fase, hora)
    return fase


def _huella_resultado(observaciones: dict, juicio: dict) -> str:
    payload = json.dumps(
        {"observaciones": observaciones, "juicio": juicio},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _resultado_ya_aplicado(
    registro: Registro, enc: Encargo, fase: str, token: str, huella: str = ""
) -> dict | None:
    ukey = f"resultado:{enc.id}:{fase}:{token}"
    previo = registro.por_ukey(ukey)
    if previo is None:
        return None
    guardada = str(previo["data"].get("huella_resultado") or "")
    if huella and guardada and huella != guardada:
        return {
            "conflicto": True,
            "motivo": "el mismo intento trae un resultado distinto",
            "fase": fase,
        }
    return {
        "registrado": True,
        "encargo": enc.id,
        "estado": enc.estado,
        "retransmision": True,
        "recibo": ukey,
        "fase": fase,
    }


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
    fase = _fase_de_resultado(registro, enc, token, version, hora)
    if not fase:
        return {"rechazado": True, "motivo": "el resultado no identifica una única fase"}
    huella = _huella_resultado(observaciones, juicio)
    previo = _resultado_ya_aplicado(registro, enc, fase, token, huella)
    if previo is not None:
        return previo
    activa = enc.fase_activa or (FASE_IMPLICITA if not enc.fases else "")
    if fase != activa:
        return {"rechazado": True, "motivo": "el resultado pertenece a otra fase"}
    if enc.intento:
        evento_intento = next(
            (
                ev
                for ev in reversed(registro.eventos(enc.id))
                if ev["kind"] == "intento_abierto"
                and str(ev["data"].get("id") or "") == enc.intento
            ),
            None,
        )
        fase_intento = str((evento_intento or {}).get("data", {}).get("fase") or "")
        if fase_intento and fase_intento != fase:
            return {"rechazado": True, "motivo": "el intento pertenece a otra fase"}
    actual = next((f for f in enc.fases if f.id == fase), None)
    if actual is not None and actual.estado == "preparada":
        return {"rechazado": True, "motivo": "la fase ya está preparada"}
    if enc.producto is None or enc.producto.firma != version:
        registro.anotar(
            enc.id,
            {
                "kind": "producto_listo",
                "data": {
                    "version": version,
                    "manifiesto": [list(x) for x in manifiesto],
                    "fase": fase,
                    "intento": enc.intento,
                    "hora": hora,
                },
            },
            f"producto-autoridad:{enc.id}:{fase}:{version}",
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
                "fase": fase,
                "intento": enc.intento,
                "hora": hora,
            },
        },
        f"revision-autoridad:{enc.id}:{fase}:{version}",
    )
    enc = registro.fold(enc.id)
    from .cierre import comprobar_destino, componer

    try:
        cierre = componer(enc, pedidos, validado.evidencia)
        if pedidos:
            comprobar_destino(cierre, DestinoEnsayo(registro.base / "destino"))
    except (ValueError, KeyError, TypeError) as exc:
        return {"rechazado": True, "motivo": str(exc)}
    ukey = f"resultado:{enc.id}:{fase}:{token}"
    resultado = registro.anotar(
        enc.id,
        {
            "kind": "comprobacion",
            "data": {
                **cierre,
                "fase": fase,
                "huella_resultado": huella,
                "observaciones": observaciones,
                "host": host,
                "hora": hora,
            },
        },
        ukey,
    )
    return {
        "registrado": resultado in ("nuevo", "igual"),
        "encargo": enc.id,
        "estado": registro.fold(enc.id).estado,
        "fase": fase,
        "recibo": ukey,
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
                    "fase": enc.fase_activa,
                    "observaciones": observaciones,
                    "host": host,
                    "hora": hora,
                },
            },
            f"resultado:{enc.id}:{enc.fase_activa}:{token}",
        )
        return {
            "registrado": resultado in ("nuevo", "igual"),
            "encargo": enc.id,
            "estado": registro.fold(enc.id).estado,
        }
