"""Ciclo de vida del encargo. No decide el trabajo de la aplicación."""

from __future__ import annotations
from motor.io import escribir_texto, leer_json, leer_texto

import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import threading
import uuid
from concurrent.futures import TimeoutError as MiradaTardia
from dataclasses import replace
from pathlib import Path

from .copiar import borrar_arbol, copiar_arbol, vaciar_arbol
from .comprobacion import (
    FalloInstrumento,
    JuicioValidado,
    VERSION_LECTOR_DEEPSEEK,
    digest,
    firma_producto,
    hallazgo_fuera_de_alcance_por_restriccion,
    hallazgos_deepseek,
    iniciar_comprobacion,
    instrucciones_responsable,
    instrucciones_revision,
    interno_de,
    leer_declaradas,
    leer_disputa,
    leer_juicio,
    version_producto,
)
from . import capacidades, necesidades, trabajos_ci
from .dominio import (
    Encargo,
    Fase,
    FASE_IMPLICITA,
    HUELLA_DEEPSEEK_SIN_REVISION,
    TOPE_NO_CONCLUYENTES,
    _EVENTOS_DE_MARCADOR,
    _NOTA_COPIA_SESION,
    _clase_de_fallo,
    _fallo_transitorio,
    fallo_permanente_de_entrega,
    impedimento_bloquea_trabajo,
    TransicionIlegal,
    alcance_de,
    ajustes_cubiertos,
    bloqueantes_abiertos,
    bloqueos_nuevos,
    correcciones_sin_ver,
    puede_recibir_intento,
    revision_independiente_cubre_producto,
    _texto_normalizado,
    decision_fallo,
    decision_proceso,
    defecto_al_adoptar_fases,
    fuentes_autorizadas,
    huella_bloqueo,
    huella_entrega,
    hubo_avance,
    id_encargo,
    id_hallazgo,
    marca_inspeccion_entregada,
    normaliza_comando,
    inspeccion_bloquea,
    respuesta_consumida,
    revision_externa_pendiente,
    nivel_cumplido,
    nivel_objetivo,
    limita_mas_que,
    sin_nada_en_curso,
    prompt_responsable,
    prompt_revisor,
    recibo_de,
    reducir,
    siguiente_escalon,
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
from .lectura import Error as ErrorLectura
from .lectura import Leido, Pendiente as PendienteLectura
from .politica import (
    CERRADOS_GRACIA_S,
    CERRADOS_POR_PASADA,
    CERRADOS_RELECTURA_S,
    CERRADOS_SONDEO_RAPIDO_S,
    CERRADOS_VENTANA_S,
    TOPE_DEEPSEEK_MIN,
    hallazgo_contra_politica,
)
from .resultado import Avanzo, Devolver, Espera, NecesitaAgente, Resultado, permite_lanzar
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
_RELECTURA_DEEPSEEK_S = 60
_AVISO_LECTURA_DEEPSEEK_S = 600
_REINTENTO_FORZADO_S = 15
_TOPE_LOG_JOB = 80
_LIMITE_CUERPO_PR = 65_536
_NOTA_CUERPO_RECORTADO = (
    "\n\n[Nota: el cuerpo supera el límite de GitHub y se recortó. "
    "El texto completo está en `.interno/cuerpo-pr.md`, en el espacio del "
    "encargo y fuera del PR.]"
)


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


def _capacidad_desplegar(
    base: Path, espacio: Path | None, repo: str
) -> tuple[bool, str]:
    """(imposible, motivo) según las capacidades declaradas de la máquina.

    True solo con un hecho declarado: `tiene` sin `ssh-goncloud`, o el
    `[deploy] requiere` del `.motor.toml` con algo que la máquina no declara.
    Sin configuración legible, o con una política ilegible o con una receta que
    una fase aún no creó, la capacidad queda desconocida y eso no cambia nivel.
    """
    from motor.conf import (
        RepoConf,
        load_maquina,
        motivo_requiere,
        requisitos_faltantes,
    )

    home = base if (base / "maquina.toml").is_file() else Path(
        os.environ.get("MOTOR_HOME") or Path.home() / ".motor"
    )
    try:
        maquina = load_maquina(home)
    except (OSError, ValueError):
        return False, ""
    requiere = ["ssh-goncloud"]
    tom = espacio / ".motor.toml" if espacio is not None else None
    if tom is not None and tom.is_file():
        try:
            requiere += list(RepoConf.parse(leer_texto(tom)).requiere)
        except (ValueError, TypeError, AttributeError):
            pass
    faltan = requisitos_faltantes(tuple(requiere), maquina.tiene)
    if faltan:
        return True, motivo_requiere(maquina.nombre, repo, faltan)
    return False, ""


TOPE_MIRADA_S = 10.0


def _mirada_acotada(consultar):
    """La mirada de solo lectura al remoto lleva un tope corto: la recepción
    no se queda bloqueada minutos esperando al remoto (F1 de DeepSeek). Si
    el puerto no contesta a tiempo, la capacidad queda desconocida y el
    nivel no cambia; el remoto se vuelve a mirar al adoptar fases y las
    validaciones al integrar siguen igual. El hilo es daemon: la salida del
    proceso no lo espera (h_dc2a2b5a — el pool de concurrent.futures se
    junta al salir aunque shutdown sea wait=False) y el gh rezagado muere
    con su propio timeout; su resultado tardío jamás se registra."""
    caja: list = []
    hecho = threading.Event()

    def _correr():
        try:
            caja.append(consultar())
        except BaseException as exc:  # noqa: BLE001  se reclasifica al llamador
            caja.append(exc)
        finally:
            hecho.set()

    threading.Thread(target=_correr, daemon=True, name="mirada-remoto").start()
    if not hecho.wait(TOPE_MIRADA_S):
        raise MiradaTardia(f"la mirada no contestó en {TOPE_MIRADA_S:g} s")
    resultado = caja[0]
    if isinstance(resultado, BaseException):
        raise resultado
    return resultado


def _problemas_de_entrega(
    enc: Encargo, gh, causa_gh: str
) -> list[tuple[str, str, str]]:
    """Causas de la entrega que la máquina ya deja ver al recibir."""
    problemas: list[tuple[str, str, str]] = []
    if gh is None:
        return problemas
    if causa_gh:
        problemas.append(
            (
                "entrega:github",
                f"gh no pudo comprobar {enc.entrega.repo}: {causa_gh}",
                "instalar gh, autenticarlo para el repo y volver a recibir",
            )
        )
    remoto = _remoto_del_repo(str(enc.entrega.repo or ""))
    if remoto:
        causa = _comprobar_remoto(remoto)
        if causa:
            problemas.append(
                (
                    "entrega:ssh",
                    f"el remoto {remoto} no responde con el transporte configurado: {causa}",
                    "dejar el remoto accesible con la llave ssh o la credencial configurada",
                )
            )
        else:
            causa = _comprobar_push(remoto, _rama_fase(enc))
            if causa:
                problemas.append(
                    (
                        "entrega:ssh",
                        f"git push --dry-run a la rama propia falló: {causa}",
                        "dejar el remoto accesible para escribir en el repo",
                    )
                )
    return problemas


def _sondeo_entrega(
    registro: Registro,
    enc: Encargo,
    gh,
    causa_gh: str,
    hora: str,
) -> None:
    """Comprueba al recibir, no al entregar, que la entrega puede publicarse.

    Reutiliza la mirada de solo lectura del puerto (`gh.capacidad`) y añade las
    sondas del remoto configurado y del push. Solo una causa permanente queda
    como impedimento visible desde el inicio; un fallo transitorio o desconocido
    sigue el camino normal. No deja un pendiente de entrega: el producto todavía no existe y el
    turno del agente debe correr. La entrega con pendientes se decide al
    publicar, cuando ya hay trabajo que entregar. Sin puerto no se inventa una
    causa.
    """
    if enc.entrega.nivel not in ("pr", "integrado", "desplegado"):
        return
    permanentes = [
        (clase, causa, condicion)
        for clase, causa, condicion in _problemas_de_entrega(enc, gh, causa_gh)
        if fallo_permanente_de_entrega(causa)
    ]
    for clase, causa, condicion in permanentes:
        _anotar_impedimento(registro, enc, clase, causa, condicion, hora)


def _sondeo_nivel(
    registro: Registro, enc: Encargo, gh, espacio: Path | None, hora: str
) -> None:
    """Registra el nivel efectivo y el estado de la entrega ya comprobados.

    Solo un impedimento fijo y legible limita el nivel: el remoto que prohibe
    mergear deja el encargo en `pr`; la máquina sin capacidad declarada de
    desplegar, en `integrado`. Un remoto sin leer, sin configuración de máquina
    o con política desconocida no cambia nada: el flujo normal espera o
    reintenta por sus propios caminos. La misma mirada de solo lectura alimenta
    la comprobación de la entrega, así que un remoto lento no se consulta dos
    veces. La mirada lleva un tope (TOPE_MIRADA_S): la recepción no espera
    minutos al remoto.
    """
    if enc.entrega.nivel not in ("pr", "integrado", "desplegado"):
        return
    causas: list[str] = []
    integrar_bloqueado = False
    capacidad = None
    causa_gh = ""
    if gh is not None and enc.entrega.base:
        try:
            capacidad = _mirada_acotada(
                lambda: gh.capacidad(enc.entrega.repo, enc.entrega.base)
            )
        except MiradaTardia:
            causa_gh = (
                f"la mirada al remoto no llegó a tiempo ({TOPE_MIRADA_S:g} s)"
            )
            causas.append(causa_gh + "; sin cambios de nivel")
        except Exception as exc:  # noqa: BLE001  la mirada no tumba la recepción
            causa_gh = f"la mirada al remoto falló ({type(exc).__name__}: {exc})"
            causas.append(causa_gh + "; sin cambios de nivel")
        else:
            if not capacidad.repo_ok:
                causa_gh = (
                    f"remoto sin acceso comprobable: {capacidad.detalle}"
                )
                causas.append(causa_gh)
            elif capacidad.integrar_bloqueado:
                integrar_bloqueado = True
                causas.append(capacidad.detalle)
            elif capacidad.integrar_bloqueado is None:
                causas.append(f"reglas del remoto sin leer: {capacidad.detalle}")
            elif capacidad.base_ok is False:
                causas.append(f"{capacidad.detalle}; sin cambios de nivel")
    _sondeo_entrega(registro, enc, gh, causa_gh, hora)
    if enc.entrega.nivel not in ("integrado", "desplegado"):
        return
    desplegar_imposible, motivo_maquina = (False, "")
    if enc.entrega.nivel == "desplegado":
        desplegar_imposible, motivo_maquina = _capacidad_desplegar(
            registro.base, espacio, enc.entrega.repo
        )
    nivel = (
        "pr"
        if integrar_bloqueado
        else ("integrado" if desplegar_imposible else "")
    )
    if not nivel:
        return
    vigente = enc.nivel_efectivo
    if vigente and not limita_mas_que(nivel, vigente):
        # La limitación vigente ya es igual o más estricta que esta
        # determinación: no se relaja ni se duplica (h_6a425441).
        return
    if desplegar_imposible:
        causas.append(motivo_maquina)
    motivo = (
        f"El motor no puede integrar: {'; '.join(causas)}. "
        f"El encargo queda limitado a pr."
        if nivel == "pr"
        else f"La máquina no puede desplegar: {'; '.join(causas)}. "
        f"El encargo queda limitado a integrado."
    )
    registro.anotar(
        enc.id,
        {
            "kind": "nivel_efectivo",
            "data": {"nivel": nivel, "motivo": motivo, "hora": hora},
        },
        "nivel-efectivo:%s:%s:%s"
        % (
            enc.id,
            nivel,
            hashlib.sha256("|".join(causas).encode()).hexdigest()[:12],
        ),
    )


TOPE_ENTREGA_S = 10.0


def _remoto_del_repo(repo: str) -> str:
    """El remoto configurado del repo, o "" si es local.

    Se respeta el transporte que ya tiene el repo (ssh o https) y lo que git
    tenga configurado para él; no se reescribe a `git@github.com`.
    """
    from motor.repositorios import resolver_repositorio

    try:
        resuelto = resolver_repositorio(repo)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    remoto = str(getattr(resuelto, "remoto", "") or "")
    if remoto.startswith(("git@", "ssh://", "http://", "https://")):
        return remoto
    if re.match(r"[^/@]+@[^/:]+:", remoto):
        return remoto
    return ""


def _comprobar_remoto(url: str) -> str:
    """Lectura del remoto sin escribir y sin pedir credenciales. "" si llega."""
    try:
        r = subprocess.run(
            ["git", "ls-remote", "--", url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=TOPE_ENTREGA_S,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "true",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"no se pudo ejecutar git: {type(exc).__name__}: {exc}"
    if r.returncode == 0:
        return ""
    return (r.stderr or r.stdout).strip()[-300:]


def _comprobar_push(url: str, rama: str) -> str:
    """`git push --dry-run` a una rama propia desde un repo temporal."""
    import tempfile

    destino = f"HEAD:refs/heads/{(rama or 'motor')}-preflight"
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}
    with tempfile.TemporaryDirectory(prefix="motor-preflight-") as temporal:
        for argv in (
            ["git", "init", "--quiet", "-b", "preflight", temporal],
            [
                "git", "-C", temporal,
                "-c", "user.name=motor", "-c", "user.email=motor@localhost",
                "commit", "--quiet", "--allow-empty", "-m", "preflight",
            ],
            ["git", "-C", temporal, "remote", "add", "origin", url],
        ):
            try:
                r = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdin=subprocess.DEVNULL,
                    timeout=TOPE_ENTREGA_S,
                    env=env,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return f"no se pudo preparar la prueba: {type(exc).__name__}: {exc}"
            if r.returncode != 0:
                return (r.stderr or r.stdout).strip()[-300:]
        try:
            r = subprocess.run(
                [
                    "git", "-C", temporal, "push", "--dry-run", "--quiet",
                    "origin", destino,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=TOPE_ENTREGA_S,
                env=env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return f"no se pudo ejecutar git push: {type(exc).__name__}: {exc}"
        if r.returncode == 0:
            return ""
        return (r.stderr or r.stdout).strip()[-300:]


def recibir(
    registro: Registro,
    texto: str,
    clave: str,
    hora: str,
    director: str = "",
    adjuntos: tuple[str, ...] = (),
    restricciones: tuple[str, ...] = (),
    permisos: tuple[str, ...] = (),
    prohibiciones: tuple[str, ...] = (),
    canal: str = "",
    via: str = "durable",
    detenido: bool = False,
    entrega_nivel: str = "terminado",
    repo: str = "",
    rama: str = "",
    base_rama: str = "",
    modelos_solicitados: tuple[tuple[str, str, str], ...] = (),
    gh=None,
) -> dict:
    if via == "directa" and entrega_nivel != "terminado":
        raise TransicionIlegal(
            "la via directa no entrega niveles externos; usa la via durable"
        )
    huella = huella_entrega(
        texto, adjuntos, restricciones, permisos, prohibiciones
    )
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
        "permisos": list(permisos),
        "prohibiciones": list(prohibiciones),
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
        permisos,
        prohibiciones,
        adjuntos,
        canal,
        via,
        entrega_nivel,
        repo,
        rama,
        base_rama,
        modelos_solicitados,
    )
    if entrega_nivel != "terminado":
        # Sondeo de solo lectura del remoto antes del primer intento: registra
        # solo las restricciones ya comprobadas. Sin puerto no hay mirada y el
        # nivel queda sin cambios. La ruta del espacio va sin crearla: en la
        # recepción todavía no hay clon ni `.motor.toml` que leer.
        enc_fresco = registro.fold(encargo)
        if enc_fresco is not None:
            _sondeo_nivel(
                registro, enc_fresco, gh, registro.base / "espacio" / encargo, hora
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
    registro: Registro,
    encargo_id: str,
    accion: str,
    clave: str,
    hora: str,
    autor: str = "",
    *, fase: str = "", cierre: str = "",
) -> dict:
    with turno(registro.base):
        salida = _ordenar(registro, encargo_id, accion, clave, hora, autor, fase=fase, cierre=cierre)
        if not salida.get("rechazada") and not salida.get("conflicto") and (
            not salida.get("retransmision") or accion == "reabrir"
        ):
            sincronizada = _enviar_orden(registro, encargo_id, accion, clave)
            if accion == "reabrir" and not sincronizada:
                salida["autoridad_pendiente"] = True
        return salida


def _enviar_orden(
    registro: Registro, encargo_id: str, accion: str, clave: str,
    *, datos: dict | None = None,
) -> bool:
    from .transporte import ConfigAutoridadInvalida, cargar, operar

    try:
        cfg = cargar(registro.base)
    except ConfigAutoridadInvalida:
        return False
    if cfg is None:
        return True
    mensaje = {"op": accion, "encargo": encargo_id, "clave": clave}
    if accion == "reabrir":
        if datos is None:
            orden = registro.por_ukey(f"orden:{clave}")
            if not orden or orden["kind"] != "orden":
                return False
            datos = orden["data"]
        if datos.get("accion") != "reabrir":
            return False
        mensaje.update({k: datos[k] for k in ("fase", "cierre")})
    resp = operar(registro.base, mensaje)
    salida = resp.get("salida") or {}
    return bool(
        resp.get("alcanzada")
        and not salida.get("rechazada")
        and not salida.get("conflicto")
        and not salida.get("error")
    )


def _ordenar(
    registro: Registro,
    encargo_id: str,
    accion: str,
    clave: str,
    hora: str,
    autor: str = "",
    *, fase: str = "", cierre: str = "",
) -> dict:
    ukey = f"orden:{clave}"
    previo = _recibo_previo(registro, encargo_id, ukey, accion)
    if previo:
        return previo
    enc = registro.fold(encargo_id)
    if enc is None:
        raise TransicionIlegal("no hay encargo")
    recibo = recibo_de(ukey)
    datos = {"accion": accion, "hora": hora}
    if accion == "reabrir":
        datos.update(
            fase=fase or (enc.cierre_forzado_fase.id if enc.cierre_forzado_fase else ""),
            cierre=cierre or enc.cierre_forzado_huella,
            clave=clave,
        )
    try:
        reducir(enc, {"kind": "orden", "data": datos})
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
    datos_orden = {
        **datos,
        "accion": accion,
        "recibo": recibo,
        "resultado": "aplicada",
        "hora": hora,
        "encargo": encargo_id,
    }
    if str(autor or ""):
        datos_orden["autor"] = str(autor)
    resultado = registro.append(encargo_id, "orden", datos_orden, ukey, hora)
    if resultado == "ilegal":
        motivo = "transicion ilegal"
        try:
            reducir(
                registro.fold(encargo_id),
                {"kind": "orden", "data": datos},
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


_PUERTO_DEL_REGISTRO = object()


def corregir(
    registro: Registro,
    encargo_id: str,
    texto: str,
    clave: str,
    hora: str,
    cierra=(),
    evidencia: str = "",
    autor: str = "",
    gh=_PUERTO_DEL_REGISTRO,
) -> dict:
    """Registra una corrección sin interrumpir al agente. La toma el siguiente
    intento. Un encargo cancelado la rechaza; uno terminado solo la acepta si su
    PR sigue abierto en GitHub (`gh`, por omisión el puerto del registro), y
    entonces se reabre en la misma rama y el mismo PR. Un rechazo deja constancia
    pero no gasta la clave: se vuelve a evaluar al repetirla, porque la causa
    puede haber cambiado (por ejemplo, un encargo que pasó a durable).

    `cierra` enumera hallazgos que el propietario declara cerrados; `evidencia`
    es lo que sostiene ese cierre. Un cierre del propietario no se reabre sin
    una reproducción nueva."""
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
    ids = tuple(str(x) for x in (cierra or ()) if str(x).strip())
    datos_base = {"texto": texto, "cierra": list(ids), "evidencia": str(evidencia or "")}

    def _mismo(datos: dict) -> bool:
        return (
            datos.get("texto") == datos_base["texto"]
            and list(datos.get("cierra") or ()) == datos_base["cierra"]
            and str(datos.get("evidencia") or "") == datos_base["evidencia"]
        )

    if prev:
        salida = vista(enc, prev["data"]["recibo"])
        if prev["encargo"] != encargo_id or not _mismo(prev["data"]):
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
    if (
        enc.estado == "terminado"
        and enc.pr_numero
        and enc.nivel_alcanzado not in ("integrado", "desplegado")
    ):
        if gh is _PUERTO_DEL_REGISTRO:
            from .cli import _puerto_github

            gh = _puerto_github(registro.base)
        estado_pr = ""
        if gh is not None:
            try:
                estado_pr = str(gh.ver(enc.entrega.repo, enc.pr_numero).estado)
            except Exception:  # noqa: BLE001  sin lectura no se reabre
                estado_pr = ""
        if estado_pr != "abierto":
            situacion = f"está {estado_pr}" if estado_pr else "no se pudo leer en GitHub"
            return rechazar(
                f"el encargo ya terminó y su PR #{enc.pr_numero} {situacion}; "
                "abre un encargo nuevo"
            )
    datos = {"texto": texto, "recibo": recibo, "hora": hora}
    if ids:
        datos["cierra"] = list(ids)
    if str(evidencia or ""):
        datos["evidencia"] = str(evidencia)
    if str(autor or ""):
        datos["autor"] = str(autor)
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
        nombre = leer_texto(actual).strip()
        if nombre.startswith("intento-") and "/" not in nombre and "\\" not in nombre:
            return raiz / nombre
    return raiz


def _espacio(base: Path, encargo_id: str) -> Path:
    path = base / "espacio" / encargo_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ya_incorporado(enc: Encargo | None) -> bool:
    return enc is not None and enc.generacion in enc.incorporado


def _ultima_firma(enc: Encargo | None) -> str:
    return enc.ultima_firma if enc is not None else ""


def _marcar_incorporado(registro: Registro, enc: Encargo, hora: str) -> None:
    """El resultado de este intento ya se incorporó. La generación identifica
    el intento y la marca vive en el registro, no en su directorio."""
    registro.anotar(
        enc.id,
        {
            "kind": "incorporado",
            "data": {"generacion": enc.generacion, "hora": hora},
        },
        f"incorporado:{enc.id}:{enc.generacion}",
    )


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
        for linea in leer_texto(indice_kimi).splitlines():
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
        escribir_texto(indice_destino, "\n".join(filas) + ("\n" if filas else ""))

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
        return leer_json(path)
    except (OSError, ValueError):
        return None


def _cerrar_procesos_del_encargo(registro: Registro, enc: Encargo) -> int:
    """Cierra lo que siga vivo de un encargo cerrado: sus herramientas del
    buzón y los procesos de cada intento, aunque el padre ya no esté."""
    raiz = Path(registro.base) / "ejecucion" / enc.id
    cerrados = 0
    try:
        cerrados += necesidades.cerrar_herramientas_del_encargo(raiz)
    except OSError:
        pass
    candidatos: list[tuple[Path, dict]] = []
    for carpeta in (raiz, *sorted(raiz.glob("intento-*"))):
        proc = _leer_proc(carpeta)
        if proc:
            candidatos.append((carpeta, proc))
    if enc.proc is not None:
        candidatos.append((raiz, enc.proc.como_dict()))
    vistos: set[tuple] = set()
    for carpeta, proc in candidatos:
        ident = (proc.get("pid"), str(proc.get("inicio") or ""))
        if ident in vistos:
            continue
        vistos.add(ident)
        try:
            cerrar(carpeta, proc)
            cerrados += 1
        except (OSError, ValueError):
            pass
    return cerrados


# Encargos cuya limpieza ya falló en este proceso, con los intentos fallidos.
# Un fallo se anota una sola vez y se permite un único reintento para el caso
# transitorio (un handle que tarda en soltarse); después se deja de intentar.
# Un arranque nuevo del supervisor vuelve a permitir ese reintento.
_LIMPIEZA_FALLIDA: dict[tuple[str, str], int] = {}
_MAX_INTENTOS_LIMPIEZA = 2


def _anotar_limpieza_fallida(
    registro: Registro,
    enc: Encargo,
    ruta: Path,
    exc: OSError,
    hora: str,
    intento: int = 1,
) -> None:
    nota = (
        f"limpieza({ruta.name}): {type(exc).__name__}: {exc}; "
        "no se reintenta en cada barrido"
    )[:2000]
    # La ukey lleva el intento: con la fija, el segundo fallo (otra ruta u otro
    # mensaje) se descartaría en silencio por el dedup del registro.
    registro.anotar(
        enc.id,
        {"kind": "procedimiento", "data": {"nota": nota, "hora": hora}},
        f"limpieza-fallida:{enc.id}:{intento}",
    )


def _limpiar_encargo_cerrado(
    registro: Registro, enc: Encargo, hora: str = ""
) -> bool:
    """Un encargo terminado o cancelado ya no necesita su runtime ni los
    intentos de ejecución. El historial vive en `registro.sqlite`; el espacio
    del producto se conserva. Lo que no se pueda borrar queda anotado una sola
    vez: se reintenta una vez más y después no se vuelve a intentar en cada
    barrido."""
    if enc.estado not in ("terminado", "cancelado"):
        return False
    clave = (str(Path(registro.base)), enc.id)
    fallos_previos = _LIMPIEZA_FALLIDA.get(clave, 0)
    if fallos_previos >= _MAX_INTENTOS_LIMPIEZA:
        return False
    hora = hora or ahora_iso()
    _cerrar_procesos_del_encargo(registro, enc)
    base = Path(registro.base)
    borrado = False
    fallos: list[tuple[Path, OSError]] = []

    def borrar(ruta: Path) -> None:
        """Borra una ruta y guarda el error para anotarlo sin cortar el resto."""
        nonlocal borrado
        if not (ruta.exists() or ruta.is_symlink()):
            return
        try:
            borrar_arbol(ruta)
            borrado = True
        except OSError as exc:
            fallos.append((ruta, exc))

    borrar(base / "runtime" / enc.id)
    ejecucion = base / "ejecucion" / enc.id
    try:
        intentos = sorted(
            (
                ruta
                for ruta in ejecucion.iterdir()
                if ruta.name.startswith("intento-")
            ),
            key=lambda ruta: ruta.name,
        )
    except FileNotFoundError:
        intentos = []
    except OSError as exc:
        fallos.append((ejecucion, exc))
        intentos = []
    for intento in intentos:
        borrar(intento)
    borrar(ejecucion / "actual")
    if fallos:
        _LIMPIEZA_FALLIDA[clave] = fallos_previos + 1
        ruta, exc = fallos[0]
        _anotar_limpieza_fallida(
            registro, enc, ruta, exc, hora, intento=fallos_previos + 1
        )
    else:
        _LIMPIEZA_FALLIDA.pop(clave, None)
    return borrado


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


# Un encargo cuya historia no pliega es un estado posible. Su fallo se dice una
# vez por encargo y clase de error, no en cada vuelta del supervisor.
_PLEGADOS_ILEGIBLES: set[tuple[str, str]] = set()


def plegar_aislado(registro: Registro, encargo_id: str) -> Encargo | None:
    """Pliega un encargo sin que su historia rota detenga a los demás.

    `None` avisa que ese encargo no se pudo leer en esta vuelta. No se intenta
    anotar nada en él: `append` también pliega su historia y volvería a fallar.
    """
    import sys

    try:
        return registro.fold(encargo_id)
    except Exception as exc:
        clase = type(exc).__name__
        clave = (encargo_id, clase)
        if clave not in _PLEGADOS_ILEGIBLES:
            _PLEGADOS_ILEGIBLES.add(clave)
            print(
                f"no se pudo plegar {encargo_id} ({clase}): {_causa_de_excepcion(exc)}",
                file=sys.stderr,
                flush=True,
            )
        return None


def _contener_fallo_de_barrido(
    registro: Registro, encargo_id: str, exc: Exception, hora: str
) -> dict | None:
    """El error de un encargo se queda en ese encargo.

    Se anota `instrumento_fallo` (fuente `barrido`, clase `barrido:<Excepción>`). La
    TOPE_FALLOS_BARRIDO.ª vuelta seguida del supervisor con la misma clase marca esa
    herramienta como no disponible y deja un pendiente de entrega: el encargo termina
    con lo que haya, sin esperar a que una persona mande un ajuste. Devuelve la vista
    del encargo, o None si no se pudo leer."""
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
        total = enc.fallos_barrido
        _anotar_instrumento(
            registro, enc, _FUENTE_BARRIDO, causa,
            "reintentar en la vuelta siguiente"
            if seguidos + 1 < TOPE_FALLOS_BARRIDO
            else "se sigue sin esa herramienta y se entrega con pendientes",
            hora, str(total + 1), clase=clase,
        )
        if seguidos + 1 >= TOPE_FALLOS_BARRIDO:
            registro.anotar(
                enc.id,
                {
                    "kind": "instrumento_no_disponible",
                    "data": {"clase": clase, "causa": causa, "hora": hora},
                },
                f"instrumento-no-disponible:{enc.id}:{clase}",
            )
            registro.anotar(
                enc.id,
                {
                    "kind": "nota_pr",
                    "data": {
                        "texto": (
                            "herramienta no disponible para este encargo: "
                            f"{clase} ({str(causa)[:200]})"
                        ),
                        "hora": hora,
                    },
                },
                f"nota-herramienta:{enc.id}:{clase}",
            )
            registro.anotar(
                enc.id,
                {
                    "kind": "pendiente_entrega",
                    "data": {
                        "texto": (
                            "el supervisor no pudo atender ese paso tras "
                            f"{seguidos + 1} fallos iguales: {str(causa)[:400]}"
                        ),
                        "hora": hora,
                    },
                },
                f"pendiente-barrido:{enc.id}:{clase}",
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


_MARCADORES_MIGRADOS: set[tuple[str, int, int]] = set()


def _identidad_de_base(base: Path) -> tuple[str, int, int]:
    try:
        datos = (base / "registro.sqlite").stat()
    except OSError:
        return (str(base), 0, 0)
    return (str(base), int(datos.st_dev), int(datos.st_ino))


def _generacion_de_directorio(nombre: str) -> int | None:
    if not nombre.startswith("intento-"):
        return None
    resto = nombre[len("intento-"):]
    try:
        return int(resto.split("-", 1)[0])
    except ValueError:
        return None


def _directorios_del_encargo(base: Path, encargo_id: str) -> list[Path]:
    raiz = base / "ejecucion" / encargo_id
    if not raiz.is_dir():
        return []
    directorios = [raiz]
    for hija in raiz.iterdir():
        if hija.is_dir() and _generacion_de_directorio(hija.name) is not None:
            directorios.append(hija)
    return directorios


def _migrar_un_marcador(
    registro: Registro, enc: Encargo, directorio: Path, nombre: str, hora: str
) -> None:
    ruta = directorio / nombre
    if not ruta.is_file():
        return
    generacion = _generacion_de_directorio(directorio.name)
    if generacion is None:
        generacion = enc.generacion
    if nombre == "incorporado":
        registro.anotar(
            enc.id,
            {
                "kind": "incorporado",
                "data": {"generacion": generacion, "hora": hora},
            },
            f"incorporado:{enc.id}:{generacion}",
        )
    elif nombre == "quietud.json":
        try:
            doc = leer_json(ruta)
        except (OSError, ValueError):
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        evento = int(doc.get("evento") or 0)
        vueltas = int(doc.get("vueltas") or 0)
        datos = {
            "evento": evento,
            "vueltas": vueltas,
            "desde": float(doc.get("desde") or 0),
            "visto": float(doc.get("visto") or 0),
            "hora": hora,
        }
        registro.anotar(
            enc.id,
            {"kind": "quietud_vigilada", "data": datos},
            f"quietud-vigilada:{enc.id}:{evento}:{vueltas}",
        )
    elif nombre == "deepseek_lectura.json":
        try:
            doc = leer_json(ruta)
        except (OSError, ValueError):
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        datos = {**doc, "hora": hora}
        epoch = float(doc.get("epoch") or 0)
        registro.anotar(
            enc.id,
            {"kind": "deepseek_lectura", "data": datos},
            f"deepseek-lectura:{enc.id}:{str(doc.get('sha') or '')}:{int(epoch * 1000)}",
        )
    elif nombre == "deepseek_cerrado.json":
        try:
            doc = leer_json(ruta)
        except (OSError, ValueError):
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        datos = {**doc, "hora": hora}
        registro.anotar(
            enc.id,
            {"kind": "deepseek_cerrado", "data": datos},
            _ukey_de_hecho("deepseek-cerrado", enc.id, datos),
        )
    elif nombre == "ci-sondeo.json":
        try:
            doc = leer_json(ruta)
        except (OSError, ValueError):
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        datos = {**doc, "hora": hora}
        registro.anotar(
            enc.id,
            {"kind": "ci_sondeo", "data": datos},
            _ukey_de_hecho("ci-sondeo", enc.id, datos),
        )
    # `cierre.json` no era una decisión: duplicaba el evento `comprobacion`
    # que ya se escribe justo después. El evento es el dueño.
    ruta.unlink(missing_ok=True)


_MIGRACION_FALLIDA: set[tuple[str, str]] = set()


def migrar_marcadores(registro: Registro, hora: str) -> None:
    """Convierte los marcadores de una instalación anterior en eventos una sola
    vez. El evento se persiste antes de borrar el archivo: si el motor cae en
    medio, la siguiente vuelta ve la `ukey` y solo termina el borrado.

    La historia de cada encargo se pliega aislada: una historia rota no impide
    migrar los demás ni detiene el barrido. `append` también pliega esa historia
    y puede fallar; ese encargo se reintenta en la vuelta siguiente."""
    import sys

    identidad = _identidad_de_base(registro.base)
    if identidad in _MARCADORES_MIGRADOS:
        return
    nombres = (
        "incorporado",
        "quietud.json",
        "deepseek_lectura.json",
        "deepseek_cerrado.json",
        "ci-sondeo.json",
        "cierre.json",
    )
    fallo = False
    for encargo_id in registro.ids():
        try:
            directorios = [
                directorio
                for directorio in _directorios_del_encargo(registro.base, encargo_id)
                if any((directorio / nombre).is_file() for nombre in nombres)
            ]
            if not directorios:
                continue
            enc = plegar_aislado(registro, encargo_id)
            if enc is None:
                continue
            for directorio in directorios:
                for nombre in nombres:
                    _migrar_un_marcador(registro, enc, directorio, nombre, hora)
        except Exception as exc:  # noqa: BLE001  un encargo no tumba la migración
            fallo = True
            clave = (encargo_id, type(exc).__name__)
            if clave not in _MIGRACION_FALLIDA:
                _MIGRACION_FALLIDA.add(clave)
                print(
                    f"no se pudo migrar {encargo_id} ({type(exc).__name__}): "
                    f"{_causa_de_excepcion(exc)}",
                    file=sys.stderr,
                    flush=True,
                )
    if not fallo:
        _MARCADORES_MIGRADOS.add(identidad)


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
    migrar_marcadores(registro, hora)
    informes = []
    presupuesto = [CERRADOS_POR_PASADA]
    for encargo_id in registro.ids():
        try:
            previo = plegar_aislado(registro, encargo_id)
            if previo is None:
                continue
            if _aparcado_por_barrido(previo):
                informes.append(_vista_o_minima(previo))
                continue
            informes += _barrer_encargos(
                registro, [encargo_id], hora, epoch, lanzador, umbral_s, gh,
                presupuesto,
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
    presupuesto: list[int] | None = None,
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
        if enc.estado in ("terminado", "cancelado"):
            # Un encargo cerrado no vuelve al buzón, pero sí pasa por la
            # autoridad mientras le quede algo que reintentar: el resultado o
            # la orden canónica. Cuando ya está resuelto se deja de consultar.
            if not _autoridad_cerrada_resuelta(registro, enc):
                try:
                    enc = _mirar_autoridad(registro, enc, hora)
                except Exception as exc:  # noqa: BLE001  el cierre no tumba el barrido
                    registro.anotar(
                        enc.id,
                        {
                            "kind": "procedimiento",
                            "data": {
                                "nota": (
                                    "no se pudo mirar la autoridad del encargo "
                                    f"cerrado: {type(exc).__name__}: {exc}"
                                )[:2000],
                                "hora": hora,
                            },
                        },
                        f"autoridad-cerrado:{enc.id}:{type(exc).__name__}",
                    )
                    enc = registro.fold(enc.id) or enc
            # La fase activa puede no tener PR (por ejemplo, cerró sin
            # cambios) y una fase anterior sí. El sondeo de DeepSeek recorre
            # las fases, así que no depende de `enc.pr_numero`.
            if enc.estado == "terminado" and not parada:
                enc = _revisar_deepseek_del_cerrado(
                    registro, enc, gh, hora, epoch, presupuesto
                )
            if enc.estado == "terminado" and enc.pr_numero and not parada:
                enc = _revisar_ci_del_pr(registro, enc, gh, hora)
            _limpiar_encargo_cerrado(registro, enc, hora)
            informes.append(vista(enc))
            continue
        if (
            not parada
            and enc.pendientes_entrega
            and enc.proc is None
            and not enc.reserva
            and enc.estado in ("recibido", "ejecutando")
        ):
            # La entrega con pendientes va delante de cualquier paso que pueda
            # volver a fallar: es el camino que corta la espera y el ciclo.
            enc = _entregar_con_pendientes(registro, enc, gh, hora)
            informes.append(vista(enc))
            continue
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
        if (
            not parada
            and enc.pendientes_entrega
            and enc.proc is None
            and not enc.reserva
            and enc.estado in ("recibido", "ejecutando")
        ):
            enc = _entregar_con_pendientes(registro, enc, gh, hora)
        if enc.host and enc.host != socket.gethostname() and (enc.reserva or enc.proc):
            arbol = "incierto"
        observacion_path = directorio / "herramienta.json"
        if enc.proc and observacion_path.is_file():
            try:
                obs = leer_json(observacion_path)
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
                enc = registro.fold(encargo_id) or enc
        firma = firma_espacio(espacio)
        firma_previa = _ultima_firma(enc)
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
            enc = registro.fold(encargo_id) or enc
        por_incorporar = (
            arbol == "ausente"
            and (
                enc.proc is not None
                or (
                    (directorio / "outcome.json").exists()
                    and not _ya_incorporado(enc)
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
            )
        )
        if not parada and enc.via != "directa":
            enc = _releer_deepseek(
                registro, enc, gh, _directorio(base, encargo_id), hora, epoch,
                devolver=not por_incorporar,
            )
        if por_incorporar:
            enc = _incorporar(registro, enc, espacio, hora, directorio)
            arbol = observar(directorio, proc)
        if not parada:
            enc = _refrescar_cuerpo_excepciones(
                registro, enc, gh, espacio, hora
            )
        if arbol == "ausente" and enc.proc is not None:
            _soltar_conductor(registro, enc, hora)
        if enc.proc is not None and arbol == "ausente":
            registro.anotar(
                encargo_id,
                {"kind": "intento_cerrado", "data": {"hora": hora}},
                f"intento-cerrado:{encargo_id}:{enc.proc.pid}:{enc.proc.inicio}",
            )
            enc = registro.fold(encargo_id) or enc
        resultado_paso: Resultado = NecesitaAgente(
            str(getattr(enc, "rol", "") or "responsable")
        )
        if (
            not parada
            and enc.proc is None
            and not enc.reserva
            and not impedimento_bloquea_trabajo(enc)
            and enc.intentos_nuevos
            and enc.estado in ("recibido", "ejecutando")
            and enc.via != "directa"
        ):
            if (
                _aplica_entrega(enc) or _revision_aprobada_vigente(enc)
            ) and _devolver_correcciones(registro, enc, hora):
                enc = registro.fold(encargo_id)
                resultado_paso = NecesitaAgente("responsable")
            elif _aplica_entrega(enc):
                _RESULTADOS_DE_PASO.pop(encargo_id, None)
                enc = _paso_entrega(registro, enc, gh, hora)
                resultado_paso = _RESULTADOS_DE_PASO.pop(
                    encargo_id, NecesitaAgente(str(enc.rol or "responsable"))
                )
                if (
                    isinstance(resultado_paso, NecesitaAgente)
                    and enc.rol == "revisor"
                    and _revision_aprobada_sin_bloqueos_ajenos(enc)
                ):
                    enc = _esperar_entrega_sin_avance(registro, enc, hora)
                    resultado_paso = Espera(enc.reanuda_en, enc.motivo)
            elif _revision_aprobada_vigente(enc):
                _RESULTADOS_DE_PASO.pop(encargo_id, None)
                enc = _intentar_cierre(registro, enc, espacio, directorio, hora)
                resultado_paso = _RESULTADOS_DE_PASO.pop(
                    encargo_id, NecesitaAgente(str(enc.rol or "responsable"))
                )
            arbol = observar(directorio, proc)
        if enc.estado == "terminado" and enc.pr_numero and not parada:
            enc = _revisar_ci_del_pr(registro, enc, gh, hora)
        sin_hecho = _edad(enc, epoch) >= umbral_s
        if arbol == "ausente" and (enc.estado == "deteniendo" or parada):
            from .despliegue import observacion as observar_despliegue

            if observar_despliegue(registro, enc) != "ausente":
                arbol = "incierto"
        decision = _decision(enc, arbol, directorio, sin_hecho, hora)
        if isinstance(resultado_paso, Devolver):
            # Devolver entrega el trabajo a un rol: el turno siguiente es suyo.
            # No es una espera ni un avance: el rol nombrado tiene que correr.
            resultado_paso = NecesitaAgente(
                str(resultado_paso.rol or getattr(enc, "rol", "") or "responsable")
            )
        if not permite_lanzar(resultado_paso):
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
                if not permite_lanzar(resultado_paso):
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
            # Matar el turno (colgado, vencido) cierra solo las herramientas del turno.
            necesidades.cerrar_herramientas(directorio, solo_del_turno=True)
        if decision["lanzar"] and lanzador is not None and not parada:
            _lanzar_uno(registro, enc, directorio, espacio, hora, lanzador)
            enc = registro.fold(encargo_id)
        enc = _vigilar_quietud(registro, enc, decision, arbol, parada, hora, epoch)
        if _limpiar_encargo_cerrado(registro, enc, hora):
            enc = registro.fold(encargo_id) or enc
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
    causa y la deja como pendiente visible para entregar con lo que hay.
    Cualquier evento nuevo reinicia la cuenta. La observacion vive en el
    registro (`quietud_vigilada`), no en un archivo aparte."""
    quieto = (
        not parada
        and arbol == "ausente"
        and not decision["lanzar"]
        and not decision["matar"]
        and sin_nada_en_curso(enc)
    )
    if not quieto:
        # Al dejar de estar quieto la observacion se olvida, como el archivo que
        # se borraba. Se anota una sola vez para no llenar el registro.
        if enc.quietud_vigilada[1] != 0:
            registro.anotar(
                enc.id,
                {
                    "kind": "quietud_vigilada",
                    "data": {
                        "evento": 0,
                        "vueltas": 0,
                        "desde": 0.0,
                        "visto": epoch,
                        "hora": hora,
                    },
                },
                f"quietud-vigilada:{enc.id}:reset:{int(epoch * 1000)}",
            )
            return registro.fold(enc.id) or enc
        return enc
    # La propia observacion no cuenta como movimiento: el objetivo es el ultimo
    # evento que no sea una vuelta de quietud.
    ultimo = registro.ultimo_evento(enc.id, excluir=_EVENTOS_DE_MARCADOR)
    evento, vueltas, desde, visto = (
        enc.quietud_vigilada if enc.quietud_vigilada else (0, 0, 0.0, 0.0)
    )
    if evento == ultimo and epoch - visto <= _QUIETUD_S:
        vueltas += 1
        desde = desde or epoch
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
        return registro.fold(enc.id) or enc
    registro.anotar(
        enc.id,
        {
            "kind": "quietud_vigilada",
            "data": {
                "evento": ultimo,
                "vueltas": vueltas,
                "desde": desde,
                "visto": epoch,
                "hora": hora,
            },
        },
        f"quietud-vigilada:{enc.id}:{ultimo}:{int(epoch * 1000)}",
    )
    return registro.fold(enc.id) or enc


def _pedidos_efecto(espacio: Path) -> list[dict]:
    path = interno_de(espacio) / "pedido-efecto.json"
    if not path.exists():
        path = espacio / "pedido-efecto.json"
    if not path.exists():
        return []
    try:
        datos = leer_json(path)
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


def _corte_de_instrumento(
    registro: Registro,
    enc: Encargo,
    causa: str,
    hora: str,
    serie: str,
    rol: str,
) -> bool:
    """La misma causa de instrumento sobre el mismo producto en dos intentos
    seguidos no vuelve al responsable.

    Deja un hecho visible y pasa el rol a otro modelo autorizado; si el relevo
    repite la misma causa, el relevo se agota y el encargo entrega con
    pendientes. No es un tope de rondas: exige clase y causa idénticas y el
    mismo producto."""
    enc = registro.fold(enc.id) or enc
    version = enc.producto.firma if enc.producto else ""
    fallos = [f for f in enc.fallos_instrumento if f[0] == version]
    if len(fallos) < 3:
        return False
    ultimo, previo = fallos[-1], fallos[-2]
    if ultimo[1] != previo[1]:
        return False
    if _texto_normalizado(ultimo[2]) != _texto_normalizado(previo[2]):
        return False
    clase = ultimo[1]
    clave = hashlib.sha256(
        f"{clase}|{_texto_normalizado(causa)}|{version}".encode()
    ).hexdigest()[:16]
    diagnostico = (
        f"el instrumento {clase or 'de comprobación'} falló dos veces seguidas "
        f"con la misma causa sobre el mismo producto: {str(causa)[:400]}"
    )
    registro.anotar(
        enc.id,
        {
            "kind": "sin_progreso",
            "data": {
                "huella": f"instrumento:{clave}",
                "diagnostico": diagnostico,
                "escalar": True,
                "rol": rol or "responsable",
                "hora": hora,
            },
        },
        f"instrumento-corte:{enc.id}:{clave}:{enc.generacion}:{serie}",
    )
    return True


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
    if estrategia == "reparar_instrumento" and _corte_de_instrumento(
        registro, enc, motivo, hora, serie, rol
    ):
        return
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


def _necesita_agente(enc: Encargo) -> tuple[Encargo, Resultado]:
    """El paso no avanzó y necesita un turno del rol que lleva el encargo."""
    return enc, NecesitaAgente(str(getattr(enc, "rol", "") or "responsable"))


def _con_espera(enc: Encargo) -> tuple[Encargo, Resultado]:
    """La espera visible ya quedó registrada en el encargo."""
    return enc, Espera(enc.reanuda_en, enc.motivo)


def _sin_avance(
    registro: Registro,
    enc: Encargo,
    abierto: str,
    intentado: str,
    hora: str,
    serie: str,
    rol: str = "responsable",
) -> None:
    """Escalera de una vuelta que no cerro ningun bloqueante: la primera vuelve
    a diagnosticar, la segunda seguida releva el mismo rol a otro modelo
    autorizado. Si el relevo tampoco avanza en el mismo punto, el encargo
    entrega con los pendientes abiertos. Si ya está aprobado pero la entrega
    sigue bloqueada, pide intervención y conserva el encargo abierto.
    `enc` ya incorpora el hallazgo de la vuelta.

    Una vuelta cuenta una sola vez: si otra fuente del mismo barrido ya la
    resolvio, esta no suma ni escala. Un bloqueante que el responsable no habia
    visto es informacion nueva: la vuelta vuelve a el para corregir y la escalera
    empieza de cero. No hay tope para esa serie: mientras cada vuelta traiga
    informacion nueva, el encargo sigue hasta cerrar los bloqueantes."""
    huella = huella_bloqueo(enc)
    base = f"sin-progreso:{enc.id}:{huella}:{enc.generacion}:{serie}"
    nuevos = sorted(bloqueos_nuevos(enc))
    if not nuevos and enc.generacion and enc.vuelta_contada == enc.generacion:
        return
    if nuevos:
        registro.anotar(
            enc.id,
            {
                "kind": "sin_progreso",
                "data": {
                    "huella": huella,
                    "nueva": True,
                    "nuevos": nuevos,
                    "escalar": False,
                    "rol": rol,
                    "hora": hora,
                },
            },
            base + ":nueva",
        )
        _devolver(
            registro, enc, "investigar", abierto,
            "corregir los bloqueantes nuevos de esta vuelta (" + ", ".join(nuevos)
            + ") junto con los que siguen abiertos; lo aprobado que no cambio se conserva",
            hora, f"nuevos-{serie}",
        )
        return
    siguiente = siguiente_escalon(enc, rol)
    if siguiente != "diagnostico":
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
                    "rol": rol,
                    "hora": hora,
                },
            },
            base + ":escalar",
        )
        if siguiente == "relevo":
            _devolver(
                registro, enc, enc.estrategia or "investigar", abierto,
                "el mismo bloqueo sigue: sigue con otro modelo autorizado del "
                "mismo rol; si tampoco avanza, se entrega con los pendientes abiertos",
                hora, f"relevo-{serie}", rol=rol,
            )
        return
    registro.anotar(
        enc.id,
        {
            "kind": "sin_progreso",
            "data": {"huella": huella, "escalar": False, "rol": rol, "hora": hora},
        },
        base + ":diagnostico",
    )
    _devolver(
        registro, enc, "diagnosticar_bloqueo", abierto,
        "diagnosticar por que la correccion no resolvio el bloqueo; "
        "no repetir la misma accion",
        hora, f"diagnostico-{serie}",
    )


def _disputas_pendientes(
    registro: Registro, enc: Encargo, interno: Path
) -> list[tuple]:
    """Disputas con evidencia que el revisor independiente todavía no decidió.

    Una disputa deja de estar pendiente cuando hay una revisión independiente
    posterior. Si el revisor la acepta, el hallazgo queda resuelto o fuera de
    alcance; si la rechaza, sigue abierto y vuelve al responsable.
    """
    try:
        disputas = leer_disputa(interno)
    except ValueError:
        return []
    if not disputas:
        return []
    enc = registro.fold(enc.id) or enc
    abiertos = {h.id: h for h in bloqueantes_abiertos(enc, "deepseek")}
    if not abiertos:
        return []
    ultima_disputa = 0
    ultima_revision = 0
    for evento in registro.eventos(enc.id):
        if evento["kind"] == "disputa":
            ultima_disputa = max(ultima_disputa, int(evento["id"]))
        elif (
            evento["kind"] == "revision"
            and (evento["data"].get("origen") or "independiente") == "independiente"
            and (evento["data"].get("fase") or enc.fase_activa) == enc.fase_activa
        ):
            ultima_revision = max(ultima_revision, int(evento["id"]))
    if not ultima_disputa or ultima_revision > ultima_disputa:
        return []
    return [
        (abiertos[d["hallazgo"]], d["evidencia"])
        for d in disputas
        if d["hallazgo"] in abiertos
    ]


def _disputa_de_deepseek(
    registro: Registro, enc: Encargo, interno: Path, hora: str
) -> bool:
    """Registra la disputa con evidencia para que la decida el revisor.

    El responsable no descarta un hallazgo de DeepSeek: si sostiene que no
    aplica, deja en `.interno/disputa.json` el id y la evidencia. Ya no se pide
    autorización al propietario: el revisor independiente decide con esa
    evidencia, la resuelve con `resueltos` y la razón queda en el PR. Mientras
    el revisor no la resuelva, el hallazgo sigue abierto y el trabajo continúa.
    """
    if not bloqueantes_abiertos(enc, "deepseek"):
        return False
    try:
        disputas = leer_disputa(interno)
    except ValueError as exc:
        _anotar_instrumento(
            registro, enc, "disputa", str(exc),
            "corregir .interno/disputa.json con hallazgo y evidencia",
            hora, f"disputa-{enc.generacion}", clase="disputa.json",
        )
        if _instrumento_omitido(enc, "disputa.json"):
            return False
        if not _impedimento_de_instrumento(
            registro, enc, "disputa.json", str(exc),
            "que .interno/disputa.json vuelva a validar", hora,
        ):
            _devolver(
                registro, enc, "reparar_instrumento", str(exc),
                "corregir la disputa sin descartar el hallazgo por cuenta propia",
                hora, "disputa",
            )
        return True
    if not disputas:
        return False
    abiertos = {h.id: h for h in bloqueantes_abiertos(enc, "deepseek")}
    partes = [
        f"{abiertos[d['hallazgo']].id} «{abiertos[d['hallazgo']].titulo}»: "
        f"{d['evidencia']}"
        for d in disputas
        if d["hallazgo"] in abiertos
    ]
    if not partes:
        return False
    texto = (
        "El responsable sostiene, con evidencia, que estos hallazgos de DeepSeek "
        "no aplican: " + "; ".join(partes)
        + ". El revisor independiente decide con esta evidencia; su decisión y "
        "la razón quedan escritas en el PR."
    )
    ukey = (
        f"disputa:{enc.id}:{enc.fase_activa}:"
        + hashlib.sha256(texto.encode()).hexdigest()[:12]
    )
    if registro.por_ukey(ukey) is not None:
        return False
    registro.anotar(
        enc.id,
        {"kind": "nota_pr", "data": {"texto": texto, "hora": hora}},
        ukey,
    )
    registro.anotar(
        enc.id,
        {
            "kind": "procedimiento",
            "data": {"nota": texto, "hora": hora},
        },
        f"disputa-procedimiento:{enc.id}:{ukey[-12:]}",
    )
    registro.anotar(
        enc.id,
        {
            "kind": "disputa",
            "data": {
                "hallazgos": [d["hallazgo"] for d in disputas],
                "evidencia": texto,
                "hora": hora,
            },
        },
        f"disputa-dato:{enc.id}:{ukey[-12:]}",
    )
    return False


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


def _sonda_entorno(enc: Encargo, espacio: Path, base: Path):
    """El entorno se prueba con el mismo lanzamiento directo que el agente."""
    from .aislamiento import sondear

    return sondear(
        espacio,
        base / "runtime" / enc.id / "sonda",
        fuentes_autorizadas(enc),
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


_VERIFICABLES = (
    "entorno",
    "comprobaciones.json",
    "fases.json",
    "pedido-efecto.json",
    "disputa.json",
)

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
    "disputa.json": ".interno/disputa.json vuelve a validar",
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


def _apariciones(enc: Encargo, clase: str) -> int:
    """Cuántas veces apareció esta causa. Sube al registrarla y al resolverla,
    así una retransmisión no crea un evento y una recaída sí."""
    return dict(enc.apariciones).get(clase, 0)


def _anotar_impedimento(
    registro, enc: Encargo, clase: str, causa: str, condicion: str, hora: str,
    agente: str = "",
) -> None:
    """`agente` es el que se iba a lanzar si la causa depende de él (su shell)."""
    enc = registro.fold(enc.id) or enc
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
        f"impedimento:{enc.id}:{clase}:{_apariciones(enc, clase) + 1}",
    )


def _resolver_impedimento(registro, enc: Encargo, clase: str, hora: str) -> None:
    enc = registro.fold(enc.id) or enc
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
        f"impedimento-resuelto:{enc.id}:{clase}:{_apariciones(enc, clase)}",
    )


def _resolver_impedimentos_de_entrega(
    registro, enc: Encargo, hora: str, clases: tuple[str, ...]
) -> None:
    """Levanta el aviso de entrega cuando la operación que lo causó ya pasó.

    Un impedimento de entrega no se resuelve por sondeo (no hay puerto en la
    verificación de la recepción); se resuelve con la prueba real: el push o la
    publicación que antes fallaba. Así no queda para siempre como causa.
    """
    enc = registro.fold(enc.id) or enc
    for impedimento in list(enc.impedimentos):
        if impedimento.clase in clases:
            _resolver_impedimento(registro, enc, impedimento.clase, hora)
            enc = registro.fold(enc.id) or enc


def _bloqueo_entorno(registro, enc: Encargo, espacio: Path, hora: str) -> bool:
    """El entorno manda sobre el trabajador: si no deja trabajar, el fallo de
    instrumento no es una corrección que el trabajador pueda hacer."""
    sonda = _sonda_entorno(enc, espacio, registro.base)
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
    enc = registro.fold(enc.id) or enc
    artefacto = dict(enc.artefactos_instrumento).get(clase, "")
    if not artefacto:
        return None
    ruta = Path(artefacto)
    if clase == "juicio:referencia" or ruta.name == "revision.json":
        try:
            referencia = leer_json(ruta)
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
    resultado = leer_juicio(
        ruta, alcance_de(enc), bloqueantes_abiertos(enc, "deepseek")
    )
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
        return _sonda_entorno(enc, espacio, base).ok
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
        if clase == "disputa.json":
            leer_disputa(interno)
            return True
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return False
    return None


def _instrumento_se_puede_omitir(clase: str) -> bool:
    """Toda herramienta que falla igual tres veces se deja de usar.

    Incluye el juicio, la inspección, el entorno y los artefactos internos: el
    revisor decide con la evidencia que haya y lo que no se pudo comprobar queda
    escrito en el PR. Ninguna caída deja al encargo esperando a una persona."""
    return True


def _instrumento_omitido(enc: Encargo, clase: str) -> bool:
    return clase in enc.herramientas_no_disponibles


def _impedimento_de_instrumento(
    registro, enc: Encargo, clase: str, causa: str, condicion: str, hora: str
) -> bool:
    """El mismo instrumento con el mismo defecto no es información nueva.

    Se mide por la clase del defecto contra el registro de fallos, no por el
    texto: un identificador de intento dentro del mensaje no reinicia la cuenta.
    Una clase distinta sí es un intento de reparación y se deja pasar.

    A la tercera falla igual, la herramienta se marca como no disponible para
    este encargo: deja de bloquear y el trabajo sigue con la evidencia que hay,
    también cuando la que falla es el juicio o la inspección. Devuelve True
    cuando el caso queda manejado (la tercera falla ya no gasta un turno de
    reparación leyendo el fold anterior) y False cuando el llamador debe
    devolver el trabajo para reparar el instrumento."""
    enc = registro.fold(enc.id) or enc
    _corte_de_instrumento(
        registro, enc, causa, hora, f"impedimento-{clase}", str(enc.rol or "responsable")
    )
    previas = sum(1 for f in enc.fallos_instrumento if f[1] == clase)
    if previas < 2:
        return False
    if _instrumento_se_puede_omitir(clase):
        registro.anotar(
            enc.id,
            {
                "kind": "instrumento_no_disponible",
                "data": {"clase": clase, "causa": causa, "hora": hora},
            },
            f"instrumento-no-disponible:{enc.id}:{clase}",
        )
        registro.anotar(
            enc.id,
            {
                "kind": "nota_pr",
                "data": {
                    "texto": (
                        "herramienta no disponible para este encargo: "
                        f"{clase} ({str(causa)[:200]})"
                    ),
                    "hora": hora,
                },
            },
            f"nota-herramienta:{enc.id}:{clase}",
        )
        if clase.startswith(("juicio:", "inspeccion:")):
            registro.anotar(
                enc.id,
                {
                    "kind": "pendiente_entrega",
                    "data": {
                        "texto": (
                            f"no se pudo obtener {clase} tras tres fallos iguales: "
                            f"{str(causa)[:400]}"
                        ),
                        "hora": hora,
                    },
                },
                f"pendiente-instrumento:{enc.id}:{clase}",
            )
        # La herramienta ya no bloquea: no se gasta un turno de reparación.
        return True
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


def _revision_aprobada_sin_bloqueos_ajenos(enc: Encargo) -> bool:
    """La revisión vigente cubre el producto y no queda un bloqueo de otro rol.

    Los bloqueos del supervisor no cuentan aquí: el paso de entrega los lee de
    la observación y los cierra o los devuelve antes de publicar o cerrar. Los
    del revisor independiente y los de DeepSeek sí siguen exigiendo su turno.
    """
    return revision_independiente_cubre_producto(enc) and not (
        bloqueantes_abiertos(enc, "independiente")
        or bloqueantes_abiertos(enc, "deepseek")
    )


def _correccion_deepseek_conserva_aprobadas(enc: Encargo) -> bool:
    fase = enc.fase_actual
    return bool(
        fase is not None
        and fase.deepseek_vigente is not None
        and enc.revision_vigente is not None
        and enc.revision_vigente.veredicto == "aprobado"
        and (
            bloqueantes_abiertos(enc, "deepseek")
            or revision_independiente_cubre_producto(enc)
        )
        and enc.rol != "revisor"
        and not bloqueantes_abiertos(enc, "independiente")
    )


def _bloqueo_externo_abierto(enc: Encargo) -> str:
    """Bloqueantes de DeepSeek que siguen abiertos, con el texto con que
    `_paso_entrega` los reporta. Vacio si no hay ninguno."""
    abiertos = bloqueantes_abiertos(enc, "deepseek")
    if not abiertos:
        return ""
    return "; ".join(f"{h.id} {h.titulo}" for h in abiertos) + " (DeepSeek)"


def _deepseek_por_releer(enc: Encargo) -> bool:
    fase = enc.fase_actual
    return bool(
        fase is not None
        and fase.deepseek_vigente is not None
        and fase.deepseek_vigente.lector_version < VERSION_LECTOR_DEEPSEEK
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
    if not bloqueantes_abiertos(enc, "independiente") and not bloqueantes_abiertos(
        enc, "deepseek"
    ):
        # Los bloqueos del supervisor no frenan el paso de entrega: el propio
        # paso los lee de la observacion y devuelve el trabajo con su detalle.
        return revision_independiente_cubre_producto(enc)
    if bloqueantes_abiertos(enc, "independiente"):
        return False
    return (
        fase is not None
        and fase.deepseek_vigente is not None
        and enc.producto is not None
        and (enc.producto.sha_git != enc.deepseek_sha or _deepseek_por_releer(enc))
        and (
            enc.revision_vigente.version == enc.producto.firma
            or _correccion_deepseek_conserva_aprobadas(enc)
        )
    )


def _evidencia_revision(registro: Registro, enc: Encargo) -> str:
    enc = registro.fold(enc.id) or enc
    return dict(enc.evidencias_revision).get(enc.fase_activa, "")


def _producto_no_aprobado(enc: Encargo, espacio: Path):
    """La version vigente en disco si ya no es la aprobada. Ninguna si
    coincide con el producto registrado y con la referencia aprobada.

    Un producto que perdio su `sha_git` (firma vieja que contaba archivos
    ignorados) y cuyo arbol coincide con la version aprobada no abre otra
    revision: la diferencia era de firma, no de producto.
    """
    if enc.producto is None:
        return None
    actual = version_producto(espacio)
    if (
        not enc.producto.sha_git
        and enc.revision_vigente is not None
        and actual.firma == enc.revision_vigente.version
    ):
        return None
    referencias = {enc.producto.firma}
    fase = enc.fase_actual
    if not _correccion_deepseek_conserva_aprobadas(enc):
        if fase is not None and fase.revisado is not None:
            referencias.add(fase.revisado.firma)
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


def _intentar_cierre_paso(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    directorio: Path,
    hora: str,
    veredicto_comprobado: bool = False,
) -> tuple[Encargo, Resultado]:
    """Despacha efectos con recibo y compone el cierre si el nivel se cumplio.

    Lo usan _cerrar_con_juicio y el reintento sin LLM de barrer: un efecto
    sin asentar reintenta este paso, nunca rehace el producto.

    Con entrega pr y el nivel pr alcanzado, el cierre pasa por
    `_cerrar_con_veredicto`, que relee a DeepSeek con el puerto de GitHub. Quien
    llega aquí sin ese paso (`veredicto_comprobado` falso) no cierra: el
    siguiente paso de entrega lo hace con el veredicto vigente.
    """

    if (
        not veredicto_comprobado
        and enc.entrega.nivel in ("pr", "integrado", "desplegado")
        and enc.nivel_alcanzado == "pr"
        and enc.pr_numero
        and enc.estado != "terminado"
    ):
        return _necesita_agente(enc)

    if _devolver_correcciones(registro, enc, hora):
        return (
            registro.fold(enc.id) or enc,
            NecesitaAgente("responsable"),
        )
    if enc.producto is not None and not enc.producto.sha_git:
        stale = _producto_no_aprobado(enc, espacio)
        if stale is not None:
            return _reabrir_revision(registro, enc, stale, hora), Devolver(
                "revisor", "el producto cambió desde la revisión aprobada"
            )
    try:
        pedidos = _pedidos_efecto(espacio)
    except (ValueError, TypeError) as exc:
        _anotar_instrumento(
            registro, enc, "adaptador", str(exc),
            "corregir id, operacion, clave y cuerpo en .interno/pedido-efecto.json",
            hora, f"pedidos-{enc.generacion}", clase="pedido-efecto.json",
        )
        if _instrumento_omitido(enc, "pedido-efecto.json"):
            pedidos = []
        else:
            manejado = _impedimento_de_instrumento(
                registro, enc, "pedido-efecto.json", str(exc),
                "que .interno/pedido-efecto.json vuelva a validar", hora,
            )
            if not manejado:
                _devolver(
                    registro, enc, "reparar_instrumento", str(exc),
                    "corregir la solicitud interna sin tocar el producto",
                    hora, "pedidos",
                )
            return registro.fold(enc.id) or enc, (
                Avanzo() if manejado else Devolver("responsable", "reparar el instrumento")
            )
    if any(p["operacion"] == "desplegar" for p in pedidos):
        if nivel_objetivo(enc) != "desplegado":
            causa = (
                "el encargo no autoriza desplegar"
                if enc.entrega.nivel != "desplegado"
                else "el nivel efectivo queda limitado a "
                + nivel_objetivo(enc)
                + ": "
                + (enc.nivel_efectivo_motivo or "el sondeo de capacidad ya lo comprobó")
            )
            _anotar_impedimento(
                registro, enc, "autorizacion", causa,
                "que el encargo autorice desplegar o no lo pida", hora,
            )
            registro.anotar(
                enc.id,
                {"kind": "pendiente_entrega", "data": {"texto": causa, "hora": hora}},
                f"pendiente-despliegue-no-autorizado:{enc.id}",
            )
            return registro.fold(enc.id) or enc, Avanzo()
        if not nivel_cumplido(enc):
            return _necesita_agente(enc)
        pedidos = [p for p in pedidos if p["operacion"] != "desplegar"]
    if pedidos and not enc.autoridad:
        marcar_espera(
            registro,
            enc.id,
            "resultado comprobado; falta autoridad para conciliar efectos",
            _reanuda_en(30),
            hora,
        )
        return _con_espera(registro.fold(enc.id) or enc)
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
            return _con_espera(registro.fold(enc.id) or enc)
    enc = registro.fold(enc.id)
    if enc.entrega.nivel != "terminado" and not nivel_cumplido(enc):
        return _necesita_agente(enc)
    from .cierre import componer

    try:
        cierre = componer(enc, pedidos, _evidencia_revision(registro, enc))
    except ValueError as exc:
        _anotar_instrumento(
            registro, enc, "adaptador", str(exc),
            "completar lo que falta para el cierre sin tocar el producto aprobado",
            hora, f"cierre-{enc.generacion}",
        )
        return registro.fold(enc.id) or enc, Avanzo()
    sin_recibir = [
        n
        for n in enc.procedimiento
        if n.startswith((_NOTA_SIN_INTENTO, _NOTA_SIN_INTENTO_ANTERIOR))
    ]
    if sin_recibir:
        # El aviso al propietario es esta evidencia: ahí tiene que ir lo que
        # nadie recibió.
        cierre["evidencia"] += " Atención: " + " ".join(sin_recibir)
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
        return registro.fold(enc.id) or enc, NecesitaAgente("responsable")
    if enc.estado == "terminado":
        _publicar_resultado(registro, enc, hora)
    return enc, Avanzo()



_RESULTADOS_DE_PASO: dict[str, Resultado] = {}


def _intentar_cierre(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    directorio: Path,
    hora: str,
    veredicto_comprobado: bool = False,
) -> Encargo:
    """Ejecuta el cierre y conserva el resultado cerrado que devolvió el paso."""
    enc, resultado = _intentar_cierre_paso(
        registro, enc, espacio, directorio, hora, veredicto_comprobado
    )
    _RESULTADOS_DE_PASO[enc.id] = resultado
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
        # La pregunta ya está registrada como solicitud visible y no bloquea: el
        # barrido sigue y el freno de no concluyentes acota la repetición.
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
                # La solicitud queda visible y admite respuesta, pero no detiene
                # el trabajo: el revisor sigue con lo que tenga y la repetición
                # la corta el freno de no concluyentes.
                pass
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
    registro.anotar(
        enc.id,
        {
            "kind": "procedimiento",
            "data": {
                "nota": (
                    "consulta sin capacidad concedida, visible como solicitud "
                    f"{ident}; el trabajo sigue sin esperar respuesta"
                ),
                "hora": hora,
            },
        },
        f"consulta-sin-capacidad:{enc.id}:{ident}",
    )


def _cerrar_con_juicio(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    directorio: Path,
    juicio: JuicioValidado,
    hora: str,
) -> Encargo:
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
        if _instrumento_omitido(enc, "pedido-efecto.json"):
            error_pedidos = ""
        else:
            if not _impedimento_de_instrumento(
                registro, enc, "pedido-efecto.json", error_pedidos,
                "que .interno/pedido-efecto.json vuelva a validar", hora,
            ):
                _devolver(
                    registro, enc, "reparar_instrumento", error_pedidos,
                    "corregir la solicitud interna sin tocar el producto",
                    hora, "pedidos",
                )
            _marcar_incorporado(registro, enc, hora)
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
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
    version_previa = enc.revision_vigente.version if enc.revision_vigente else ""
    enc_previo = enc
    disputa_previa = bool(_disputas_pendientes(registro, enc, interno_de(espacio)))
    revisor = ""
    for rol, _agente, modelo in reversed(enc.modelos):
        if rol == "revisor":
            revisor = modelo
            break
    revision_id = "rev_" + hashlib.sha256(
        f"{enc.id}:{enc.generacion}:{juicio.version}".encode()
    ).hexdigest()[:12]
    abiertos_ds = {h.id: h for h in bloqueantes_abiertos(enc, "deepseek")}
    hallazgos_juicio = juicio.hallazgos
    ya_resueltos = {h.id for h in hallazgos_juicio if h.estado == "resuelto"}
    ids_ds = [
        hid
        for hid in juicio.resueltos
        if hid in abiertos_ds and hid not in ya_resueltos
    ]
    resueltos_ds = [
        replace(abiertos_ds[hid], estado="resuelto")
        for hid in ids_ds
    ]
    if resueltos_ds:
        marca = juicio.evidencia or "descartado por el revisor independiente"
        hallazgos_juicio = tuple(hallazgos_juicio) + tuple(
            replace(h, cierre_evidencia=marca) for h in resueltos_ds
        )
        for h in resueltos_ds:
            registro.anotar(
                enc.id,
                {
                    "kind": "nota_pr",
                    "data": {
                        "texto": (
                            f"hallazgo de DeepSeek descartado por el revisor "
                            f"independiente: {h.id} «{h.titulo}» ({marca[:400]})"
                        ),
                        "hora": hora,
                    },
                },
                f"disputa-resuelta:{enc.id}:{h.id}",
            )
            _anotar_excepcion(
                registro, enc, "descarte", enc.pr_sha, h.id, marca, hora,
                f"excepcion:{enc.id}:descarte:{enc.pr_sha}:{h.id}",
            )
        enc = registro.fold(enc.id) or enc
    fuera_ds = [
        (hid, razon)
        for hid, razon in juicio.fuera_de_alcance
        if hid in abiertos_ds and hid not in ya_resueltos and hid not in ids_ds
    ]
    if fuera_ds:
        hallazgos_juicio = tuple(hallazgos_juicio) + tuple(
            replace(
                abiertos_ds[hid],
                gravedad="observacion",
                estado="resuelto",
                cierre_evidencia=razon,
            )
            for hid, razon in fuera_ds
        )
        for hid, razon in fuera_ds:
            h = abiertos_ds[hid]
            registro.anotar(
                enc.id,
                {
                    "kind": "hallazgo_fuera_de_alcance",
                    "data": {
                        "id": hid,
                        "titulo": h.titulo,
                        "razon": razon,
                        "hora": hora,
                    },
                },
                f"fuera-alcance:{enc.id}:{enc.fase_activa}:{hid}",
            )
            _anotar_excepcion(
                registro, enc, "fuera_alcance", enc.pr_sha, hid, razon, hora,
                f"excepcion:{enc.id}:fuera:{enc.pr_sha}:{hid}",
            )
        enc = registro.fold(enc.id) or enc
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
                "hallazgos": [h.como_dict() for h in hallazgos_juicio],
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
    if (
        juicio.veredicto == "aprobado"
        and disputa_previa
        and bloqueantes_abiertos(enc, "deepseek")
    ):
        abiertos = bloqueantes_abiertos(enc, "deepseek")
        registro.anotar(
            enc.id,
            {
                "kind": "nota_pr",
                "data": {
                    "texto": (
                        "el revisor independiente no descartó la disputa; "
                        "el hallazgo vuelve al responsable: "
                        + "; ".join(f"{h.id} {h.titulo}" for h in abiertos)
                    ),
                    "hora": hora,
                },
            },
            f"disputa-rechazada:{enc.id}:{enc.generacion}",
        )
        _devolver(
            registro, enc, "investigar", abiertos[0].titulo,
            "corregir " + ", ".join(h.id for h in abiertos)
            + "; la disputa con evidencia no fue aceptada por el revisor",
            hora, f"disputa-rechazada-{enc.generacion}",
        )
        _marcar_incorporado(registro, enc, hora)
        return registro.fold(enc.id)
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
            _marcar_incorporado(registro, enc, hora)
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
        _marcar_incorporado(registro, enc, hora)
        return registro.fold(enc.id)
    if juicio.veredicto == "no_concluyente":
        if enc.no_concluyentes >= TOPE_NO_CONCLUYENTES:
            # El mismo pedido sin avance N veces seguidas: la escalera ya relevó
            # o dejó la entrega con pendientes. No se vuelve a ligar la misma
            # inspección ni se gasta otro turno con el mismo pedido.
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
        else:
            # Salida propia de las no_concluyente: el pedido del revisor vuelve
            # al responsable y la repetición la cuenta y la corta el freno
            # (`TOPE_NO_CONCLUYENTES`), no la escalera de los bloqueos.
            _devolver(
                registro, enc, "investigar",
                juicio.nota or "evidencia insuficiente para aprobar",
                juicio.nota or "aportar la evidencia que falta sin rebajar el pedido",
                hora, "no-concluyente",
            )
        _marcar_incorporado(registro, enc, hora)
        return registro.fold(enc.id)
    enc = _intentar_cierre(registro, enc, espacio, directorio, hora)
    _marcar_incorporado(registro, enc, hora)
    return registro.fold(enc.id)


def _incorporar(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    hora: str,
    directorio: Path | None = None,
) -> Encargo:
    directorio = directorio or _directorio(registro.base, enc.id)
    outcome_path = directorio / "outcome.json"
    if enc.intento_interrumpido and (not enc.intento or not outcome_path.is_file()):
        _marcar_incorporado(registro, enc, hora)
        return enc
    if outcome_path.is_file():
        outcome = leer_json(outcome_path)
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
            leer_json((directorio / "arranque.json"))
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
        if enc.intento_interrumpido:
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
        if outcome.get("autenticacion_expirada"):
            marcar_espera(
                registro,
                enc.id,
                "renovando el acceso de Kimi para continuar la misma sesion",
                hora,
                hora,
            )
            _marcar_incorporado(registro, enc, hora)
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
                _marcar_incorporado(registro, enc, hora)
                return registro.fold(enc.id)
        if outcome.get("fallo_runtime"):
            from .rutas import fallo

            seguidas = dict(enc.fallos_proveedor).get(agente, 0) + 1
            causa = fallo(
                agente,
                outcome["fallo_runtime"],
                hora,
                seguidas,
                modelo=str(spec.get("modelo") or ""),
            )
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
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
        if any(p.agente == agente for p in enc.pausas):
            registro.anotar(
                enc.id,
                {
                    "kind": "proveedor_disponible",
                    "data": {
                        "agente": agente,
                        "modelo": str(spec.get("modelo") or ""),
                        "hora": hora,
                    },
                },
                f"proveedor-disponible:{enc.id}:{enc.generacion}",
            )
            enc = registro.fold(enc.id)
    if enc.rol != "revisor":
        return _incorporar_responsable(registro, enc, espacio, hora, directorio)
    return _incorporar_revisor(registro, enc, espacio, hora, directorio)


def _digest_interno(espacio: Path) -> str:
    """Huella de lo entregado fuera del producto: el árbol `.interno` sin los
    canales del host.

    La firma del producto no lo ve (`.interno` está excluido), y una
    corrección del responsable puede vivir ahí dentro: el cuerpo del PR, las
    comprobaciones declaradas, una disputa. Los canales del host se excluyen
    porque no son entrega del responsable: `buzon` (el puente que
    `necesidades.preparar` reescribe en cada lanzamiento), `entregas`
    (`necesidades` guarda ahí los datos de las herramientas) y `logs`
    (`_copia_log_job` copia ahí los logs del CI). Solo cuentan archivos
    regulares: un enlace roto o una cola no se leen. `""` si no hay árbol;
    `"vacio"` si queda vacío."""
    raiz = interno_de(espacio)
    if not raiz.is_dir():
        return ""
    canales = {necesidades.BUZON.name, necesidades.ENTREGAS.name, "logs"}
    partes = []
    for path in sorted(raiz.rglob("*")):
        if not path.is_file() or path.relative_to(raiz).parts[0] in canales:
            continue
        partes.append(
            path.relative_to(espacio).as_posix()
            + ":"
            + hashlib.sha256(path.read_bytes()).hexdigest()
        )
    return (
        hashlib.sha256("|".join(partes).encode()).hexdigest() if partes else "vacio"
    )


def _incorporar_responsable(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    hora: str,
    directorio: Path,
) -> Encargo:
    if _bloqueo_entorno(registro, enc, espacio, hora):
        _marcar_incorporado(registro, enc, hora)
        return registro.fold(enc.id)
    interno = interno_de(espacio, crear=True)
    enc, error_fases = adoptar_fases(registro, enc, interno, hora)
    if error_fases:
        _anotar_instrumento(
            registro, enc, "fases", error_fases,
            "corregir .interno/fases.json sin devolver el plan al usuario",
            hora, f"fases-{enc.generacion}", clase="fases.json",
        )
        if _instrumento_omitido(enc, "fases.json"):
            error_fases = ""
        else:
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
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
    enc_fases = registro.fold(enc.id)
    if enc_fases is not None:
        enc = enc_fases
        if enc.entrega.nivel in ("integrado", "desplegado"):
            # Las fases quedaron adoptadas: antes del siguiente intento o
            # efecto de entrega se comprueba la capacidad declarada de la
            # máquina. Sin puerto GitHub aquí, la mirada del remoto vive en
            # `recibir`.
            _sondeo_nivel(registro, enc, None, espacio, hora)
            tras_sondeo = registro.fold(enc.id)
            if tras_sondeo is not None:
                enc = tras_sondeo
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
                "interno": _digest_interno(espacio),
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "hora": hora,
            },
        },
        f"producto:{enc.id}:{enc.fase_activa}:{enc.generacion}:{version.firma}",
    )
    enc = registro.fold(enc.id)
    if _disputa_de_deepseek(registro, enc, interno, hora):
        _marcar_incorporado(registro, enc, hora)
        return registro.fold(enc.id)
    pendientes_disputa = _disputas_pendientes(registro, enc, interno)
    if pendientes_disputa:
        abiertos_disputa = [h for h, _ in pendientes_disputa]
        _devolver(
            registro, enc, "decidir_disputa",
            "el responsable sostiene con evidencia que no aplican: "
            + "; ".join(f"{h.id} {h.titulo}" for h in abiertos_disputa),
            "decidir con la evidencia si el hallazgo aplica; si no aplica, "
            "descártalo y deja la razón en el PR; si aplica, el responsable "
            "lo corrige",
            hora, f"disputa-revisor-{enc.generacion}",
            rol="revisor",
        )
        _marcar_incorporado(registro, enc, hora)
        return registro.fold(enc.id)
    if error_declaradas:
        _anotar_instrumento(
            registro, enc, "comprobacion", error_declaradas,
            "corregir .interno/comprobaciones.json sin tocar el producto",
            hora, f"declaradas-{enc.generacion}", clase="comprobaciones.json",
        )
        if _instrumento_omitido(enc, "comprobaciones.json"):
            declaradas = None
            error_declaradas = ""
        else:
            if not _impedimento_de_instrumento(
                registro, enc, "comprobaciones.json", error_declaradas,
                "que .interno/comprobaciones.json vuelva a validar", hora,
            ):
                _devolver(
                    registro, enc, "reparar_instrumento", error_declaradas,
                    "corregir las comprobaciones declaradas y volver a observar",
                    hora, "declaradas",
                )
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
    try:
        _pedidos_efecto(espacio)
    except (ValueError, TypeError) as exc:
        _anotar_instrumento(
            registro, enc, "adaptador", str(exc),
            "corregir id, operacion, clave y cuerpo en .interno/pedido-efecto.json",
            hora, f"pedidos-{enc.generacion}", clase="pedido-efecto.json",
        )
        if not _instrumento_omitido(enc, "pedido-efecto.json"):
            if not _impedimento_de_instrumento(
                registro, enc, "pedido-efecto.json", str(exc),
                "que .interno/pedido-efecto.json vuelva a validar", hora,
            ):
                _devolver(
                    registro, enc, "reparar_instrumento", str(exc),
                    "corregir la solicitud interna sin tocar el producto",
                    hora, "pedidos",
                )
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
    if declaradas is not None:
        path = directorio / "observaciones.json"
        observacion = None
        esperadas = declaradas
        if path.is_file():
            try:
                candidata = leer_json(path)
            except ValueError:
                candidata = None
            if candidata and _observacion_corresponde(
                candidata, version.firma, esperadas
            ):
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
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
        ident = digest({"comprobaciones": esperadas})[:16]
        registro.anotar(
            enc.id,
            {
                "kind": "observacion_supervisor",
                "data": {
                    "version": version.firma,
                    "comprobaciones": observacion.get("comprobaciones") or [],
                    "ejecuciones": observacion.get("ejecuciones") or [],
                    "hora": hora,
                },
            },
            f"observacion:{enc.id}:{version.firma}:{ident}",
        )
        enc = registro.fold(enc.id)
    if commit_entregado and not _deepseek_por_releer(enc):
        abierto = _bloqueo_externo_abierto(enc)
        if abierto and revision_externa_pendiente(enc):
            # Falta el veredicto de DeepSeek sobre este commit: una
            # vuelta sin novedad del supervisor no es una vuelta sin avance, y el
            # bloqueante abierto puede ser uno que su fuente ya cerró. No cuenta;
            # se espera, sin lanzar otro turno que devolvería lo mismo.
            marcar_espera(
                registro, enc.id,
                f"esperando la revisión externa del commit {commit_entregado[:12]} "
                "(DeepSeek) antes de contar otra vuelta",
                _reanuda_en(60), hora,
            )
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
        if abierto:
            _sin_avance(
                registro, enc, abierto,
                "se devolvio el trabajo y el implementador entrego la misma "
                f"version, sin cambios en el arbol del commit {commit_entregado[:12]}",
                hora, f"misma-version-{commit_entregado[:12]}",
            )
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
    entregas = enc.internos_producto
    interno_previo = entregas[-2] if len(entregas) >= 2 else None
    interno_actual = entregas[-1] if entregas else None
    abiertos_propios = bloqueantes_abiertos(enc, "independiente")
    if (
        not _deepseek_por_releer(enc)
        and enc.revision_vigente is not None
        and enc.revision_vigente.version == version.firma
        and abiertos_propios
        and not bloqueos_nuevos(enc)
        and interno_previo is not None
        and interno_actual == interno_previo
    ):
        # La corrección devuelve la versión que el revisor ya juzgó, con los
        # mismos ids devueltos y sin atenderlos: es una vuelta sin avance y no
        # gasta otra revisión sobre lo mismo. La entrega solo cuenta como
        # vacía si tampoco cambió .interno: la firma del producto no lo ve, y
        # ahí vive una corrección real (el cuerpo del PR, las comprobaciones
        # declaradas, una disputa).
        _sin_avance(
            registro, enc,
            "; ".join(f"{h.id} {h.titulo}" for h in abiertos_propios),
            "se devolvio el trabajo y el implementador entrego la misma "
            f"version ({version.firma[:12]}), sin atender los bloqueantes devueltos",
            hora, f"misma-version-{version.firma[:12]}",
        )
        _marcar_incorporado(registro, enc, hora)
        return registro.fold(enc.id) or enc
    if _correccion_deepseek_conserva_aprobadas(enc):
        _marcar_incorporado(registro, enc, hora)
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
    _marcar_incorporado(registro, enc, hora)
    return registro.fold(enc.id)


def _juicio_de_referencia(enc: Encargo, directorio: Path):
    """El juicio que la referencia señala, o None si no se puede leer."""
    try:
        referencia = leer_json((directorio / "revision.json"))
        return leer_juicio(
            Path(referencia["resultado"]), alcance_de(enc),
            bloqueantes_abiertos(enc, "deepseek"),
        )
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
    if sol.estado == "pregunta":
        # La pregunta por autorización no bloquea: el juicio se consume como una
        # revisión más y la repetición la cuenta el freno de no concluyentes.
        # Un pedido repetido sin respuesta no queda callado: deja el pendiente
        # visible para entregar con lo que hay, sin esperar a una persona.
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
        return False
    if sol.estado != "pendiente":
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
    if _salta_inspeccion(registro, enc, directorio, hora):
        _marcar_incorporado(registro, enc, hora)
        return registro.fold(enc.id)
    ref_path = directorio / "revision.json"
    if not ref_path.is_file():
        if _bloqueo_entorno(registro, enc, espacio, hora):
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
        _fallo_juicio(
            registro, enc, "juicio:referencia",
            "falta la referencia de la revision",
            "volver a lanzar la revision sobre la misma version",
            "que la referencia de la revisión señale un juicio válido "
            "para la versión revisada",
            str(ref_path), hora, f"sinref-{enc.generacion}",
        )
        _marcar_incorporado(registro, enc, hora)
        return registro.fold(enc.id)
    try:
        referencia = leer_json(ref_path)
        if not isinstance(referencia, dict) or not str(
            referencia.get("resultado") or ""
        ):
            raise ValueError("la referencia no señala el resultado del juicio")
    except (OSError, UnicodeError, ValueError) as exc:
        if _bloqueo_entorno(registro, enc, espacio, hora):
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id) or enc
        causa = f"la referencia de la revisión no se puede utilizar: {exc}"
        _fallo_juicio(
            registro, enc, "juicio:referencia", causa,
            "volver a lanzar la revision sobre la misma version",
            "que la referencia de la revisión señale un juicio válido "
            "para la versión revisada",
            str(ref_path), hora, f"refilegible-{enc.generacion}",
        )
        _marcar_incorporado(registro, enc, hora)
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
        _marcar_incorporado(registro, enc, hora)
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
    resultado = leer_juicio(
        Path(referencia["resultado"]), alcance,
        bloqueantes_abiertos(enc, "deepseek"),
    )
    if isinstance(resultado, FalloInstrumento):
        if _bloqueo_entorno(registro, enc, espacio, hora):
            _marcar_incorporado(registro, enc, hora)
            return registro.fold(enc.id)
        # Un juicio ilegible se vuelve a emitir sobre la misma versión: no es
        # trabajo del responsable. El mismo defecto dos veces sí lo detiene.
        _fallo_juicio(
            registro, enc, f"juicio:{resultado.defecto}", resultado.causa,
            "volver a emitir el juicio con el formato pedido sobre la misma version",
            "que el juicio vuelva a validar para la versión revisada",
            str(Path(referencia["resultado"])), hora, f"juicio-{enc.generacion}",
        )
        _marcar_incorporado(registro, enc, hora)
        return registro.fold(enc.id)
    enc = _contestar_con_juicio_del_revisor(
        registro, enc, espacio, directorio, resultado, hora
    )
    if resultado.instrumento:
        if _bloqueo_entorno(registro, enc, espacio, hora):
            _marcar_incorporado(registro, enc, hora)
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
        _marcar_incorporado(registro, enc, hora)
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
                candidata = leer_json(fuente)
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
    if _git(espacio, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode == 0:
        return ""
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
# El commit o push de la misma version no se reintenta en bucle: a la segunda
# falla seguida vuelve al responsable con la causa exacta.
TOPE_FALLOS_ENTREGA = 2
# Lo que el aviso del resumen dice en palabras simples cuando se corta el bucle.
_AVISO_ENTREGA_DEVUELTA = (
    "el motor dejo de reintentar; el trabajo vuelve al responsable."
)
# `resumen._alerta_instrumento` recorta la causa a 140 caracteres. El corte
# reserva ese espacio para el aviso y, si hace falta, recorta solo la causa.
_TOPE_CAUSA_AVISO = 140


def _causa_con_aviso(
    causa: str, aviso: str, tope: int = _TOPE_CAUSA_AVISO
) -> str:
    """La causa primero y el aviso completo dentro del limite del resumen."""
    texto = " ".join(str(causa or "").split())
    espacio = max(tope - len(aviso) - 2, 1)
    if len(texto) > espacio:
        texto = texto[: espacio - 1].rstrip() + "…"
    return f"{texto}. {aviso}"
def _fallos_de_repo_seguidos(enc: Encargo, causa: str) -> int:
    """Esperas seguidas por el mismo fallo (la misma operación), sin nada
    distinto entre ellas. Un ajuste, una respuesta, una orden o un intento que sí
    arrancó las cortan. El reducer mantiene la serie."""
    clase = _clase_de_fallo(causa)
    guardada, seguidos = enc.fallos_repo
    return seguidos if guardada == clase else 0


def _fallos_de_entrega_seguidos(enc: Encargo, causa: str) -> int:
    """Esperas seguidas por el mismo fallo de entrega, sin contar las
    transitorias. Un ajuste, una respuesta, una orden o un intento que empezó
    las cortan. El reducer mantiene la serie."""
    clase = _clase_de_fallo(causa)
    guardada, seguidos = enc.fallos_entrega
    return seguidos if guardada == clase else 0


def _entrega_devuelta_pendiente(registro: Registro, enc: Encargo) -> bool:
    """La ultima devolucion de entrega es de esta version y este intento: no se
    reintenta el commit hasta que el responsable haya dado su vuelta."""
    producto = getattr(enc, "producto", None)
    version = producto.firma if producto else ""
    eventos = getattr(registro, "eventos", None)
    if not callable(eventos):
        return False
    for ev in reversed(eventos(enc.id)):
        if ev["kind"] != "entrega_devuelta":
            continue
        datos = ev["data"]
        return (
            datos.get("version") == version
            and int(datos.get("generacion") or 0)
            == int(getattr(enc, "generacion", 0) or 0)
        )
    return False


def _entrega_devuelta_de_la_version(registro: Registro, enc: Encargo) -> bool:
    """Ya se devolvio la entrega de esta version: una falla posterior vuelve al
    responsable sin abrir otra espera."""
    producto = getattr(enc, "producto", None)
    version = producto.firma if producto else ""
    eventos = getattr(registro, "eventos", None)
    if not callable(eventos):
        return False
    for ev in reversed(eventos(enc.id)):
        if ev["kind"] == "entrega_devuelta":
            return ev["data"].get("version") == version
    return False


def _esperar_entrega_sin_avance(
    registro: Registro, enc: Encargo, hora: str
) -> Encargo:
    """La aprobación ya cubre el producto y el paso de entrega no tiene nada
    nuevo que publicar: espera visible, sin gastar otro turno del revisor y sin
    tope de rondas ni pregunta al propietario. Al vencer el plazo, el barrido
    vuelve a mirar la entrega por su cuenta."""
    marcar_espera(
        registro,
        enc.id,
        "la revisión aprobada ya cubre el producto y la entrega no tiene nada "
        "nuevo que publicar; no se relanza al revisor",
        _reanuda_en(300),
        hora,
    )
    return registro.fold(enc.id) or enc


def _fallo_de_repo(
    registro: Registro, enc: Encargo, causa: str, hora: str, serie: str
) -> None:
    """El repo no quedó listo: espera y reintenta. El mismo fallo
    TOPE_FALLOS_REPO veces seguidas deja un impedimento visible y se entrega lo
    que haya con los pendientes, sin esperar a una persona."""
    _anotar_instrumento(
        registro, enc, "github", causa,
        "dejar accesible el repo declarado y reintentar",
        hora, serie,
    )
    marcar_espera(registro, enc.id, causa, _reanuda_en(300), hora)
    enc = registro.fold(enc.id) or enc
    veces = _fallos_de_repo_seguidos(enc, causa)
    if veces < TOPE_FALLOS_REPO:
        return
    texto = (
        f"Git falló {veces} veces seguidas en la misma operación y el motor dejó "
        f"de reintentar. Último error: {causa}. Corrige el repositorio o el "
        "espacio y manda un ajuste con director corregir para que reintente; "
        "también sirve director parar y después director continuar."
    )
    if enc.estado == "esperando_reintento":
        registro.anotar(
            enc.id,
            {"kind": "plazo", "data": {"hora": hora}},
            f"plazo-repo:{enc.id}:{veces}",
        )
    _anotar_impedimento(
        registro, enc, "repo", texto,
        "que el repositorio y el espacio vuelvan a responder", hora,
    )
    registro.anotar(
        enc.id,
        {"kind": "pendiente_entrega", "data": {"texto": texto, "hora": hora}},
        f"pendiente-repo:{enc.id}:{veces}",
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


def _duena_de_rama(enc: Encargo, rama: str) -> Fase | None:
    """La fase cuya rama es `rama`, o None si es la rama sin fase."""
    for fase in enc.fases:
        if _rama_de_fase(enc, fase) == rama:
            return fase
    return None


def _descartar_cambios_de_bloqueada(espacio: Path) -> None:
    """Descarta el trabajo sin confirmar de una fase bloqueada.

    La fase que sigue parte de su base: ni cambios pendientes ni archivos
    exclusivos de la bloqueada, aunque su commit o su publicación fallaran.
    `.interno` y `.runtime` viven fuera del producto y no se tocan."""
    _git(espacio, "reset", "--hard", "--quiet", "HEAD")
    _git(
        espacio, "clean", "-fdq",
        "--exclude=.interno", "--exclude=.interno/**",
        "--exclude=.runtime", "--exclude=.runtime/**",
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
            saliente = _duena_de_rama(enc, actual)
            if saliente is not None and saliente.estado == "bloqueada":
                # La bloqueada dejó el árbol sucio: la fase que sigue parte de
                # su base sin sus cambios pendientes ni sus archivos exclusivos.
                _descartar_cambios_de_bloqueada(espacio)
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


_RUTAS_INTERNAS = (
    "**/.interno", "**/.interno/**", "**/__pycache__/**", "**/.runtime/**",
)
_CACHES_INTERNAS = ("**/__pycache__/**", "**/.runtime/**")
_SIEMPRE_FUERA = ("**/.interno", "**/.interno/**")
_FUERA_DEL_PRODUCTO = tuple(f":(exclude,glob){ruta}" for ruta in _RUTAS_INTERNAS)


def _preparar_indice(espacio: Path) -> str:
    """Deja el producto en el indice y saca lo interno. Devuelve la causa del fallo.

    `git add` no puede nombrar rutas ignoradas en su pathspec: con `.interno` en
    el `.gitignore` devuelve 1 aunque agregue lo demas. Por eso se agrega todo y
    luego se saca lo interno con `rm --cached --ignore-unmatch`. Las caches
    vuelven al estado de `HEAD` igual que antes: ni sus altas ni sus cambios ni
    sus borrados entran al commit.
    """
    r = _git(espacio, "add", "-A", "--", ".")
    if r.returncode != 0:
        return f"git add fallo: {r.stderr.strip()[-300:]}"
    r = _git(
        espacio, "reset", "--quiet", "HEAD", "--",
        *(f":(glob){ruta}" for ruta in _CACHES_INTERNAS),
    )
    if r.returncode != 0:
        return f"git reset del indice fallo: {r.stderr.strip()[-300:]}"
    r = _git(
        espacio, "rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--",
        *(f":(glob){ruta}" for ruta in _SIEMPRE_FUERA),
    )
    if r.returncode != 0:
        return f"git rm del indice fallo: {r.stderr.strip()[-300:]}"
    return ""


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
    causa = _preparar_indice(espacio)
    if causa:
        return "", causa
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
        # El commit local existe y es el que se intentó publicar: el fallo del
        # push no borra el sha. Sin él, un rechazo del remoto (o una red caída)
        # parecería un head desviado con sha vacío (F10).
        return sha, f"git push fallo: {r.stderr.strip()[-300:]}"
    return sha, ""


def _minutos_desde(hora_evento: str, hora: str) -> float:
    from datetime import datetime

    try:
        a = datetime.fromisoformat(hora_evento.replace("Z", "+00:00"))
        b = datetime.fromisoformat(hora.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (b - a).total_seconds() / 60.0


def _resolver_bloqueantes_deepseek_omitido(
    registro: Registro, enc: Encargo, sha: str, razon: str, hora: str
) -> Encargo:
    """Los bloqueantes de una lectura anterior no frenan el SHA que se omite.

    Si DeepSeek no publicó nada para este SHA no hay hallazgos nuevos que
    leer; los que quedaron abiertos son de una lectura de otro SHA y ya no
    sostienen la fase. Se cierran con la razón de la omisión para que el nivel
    avance, sin inventar una lectura aprobada.
    """
    if enc.deepseek_sha == sha:
        # La lectura abierta es la del mismo SHA: esos bloqueantes son de
        # DeepSeek y siguen bloqueando. Sólo se cierran los de un SHA anterior
        # (o los que quedaron sin lectura asociada).
        return enc
    abiertos = bloqueantes_abiertos(enc, "deepseek")
    if not abiertos:
        return enc
    ids = sorted(h.id for h in abiertos)
    registro.anotar(
        enc.id,
        {
            "kind": "revision",
            "data": {
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "revision_id": "rev_ds_omitida_" + sha[:12],
                "version": enc.producto.firma if enc.producto else "",
                "base_version": enc.deepseek_sha,
                "alcance": "completa",
                "veredicto": "aprobado",
                "revisor": "deepseek",
                "origen": "deepseek",
                "lector_version": VERSION_LECTOR_DEEPSEEK,
                "cubre": ids,
                "hallazgos": [
                    replace(
                        h,
                        estado="resuelto",
                        cierre_evidencia=razon,
                    ).como_dict()
                    for h in abiertos
                ],
                "hora": hora,
            },
        },
        f"revision-ds-omitida:{enc.id}:{enc.fase_activa}:{sha}:"
        + ",".join(ids),
    )
    return registro.fold(enc.id) or enc


def _leer_deepseek(
    gh, repo: str, numero: int, sha: str, desde: str
) -> Leido | PendienteLectura | ErrorLectura:
    """La revisión externa como lectura tipada, sin convertirla a bool.

    `Pendiente` es «todavía no hay comentario»; `Error` conserva la causa de la
    lectura; `Leido` trae los comentarios, aunque solo sean avisos de timeout.
    """
    try:
        leidos = gh.comentarios_deepseek(repo, numero, sha)
    except Exception as exc:  # noqa: BLE001  la causa se conserva, no se adivina
        return ErrorLectura(str(exc) or type(exc).__name__)
    if not leidos:
        return PendienteLectura(desde)
    comentarios = _deepseek_con_contenido(leidos, sha)
    return Leido((leidos, comentarios))


def _omitir_deepseek(
    registro: Registro, enc: Encargo, numero: int, sha: str, texto: str, hora: str
) -> Encargo:
    """Tras el tope, la fase sigue sin la revisión externa.

    La nota queda en el PR y la huella `sin-revision` libera el SHA para
    integrar o desplegar. Una relectura posterior con comentarios reales
    sustituye la huella y trata el hallazgo como tardío. Los bloqueantes de
    una lectura anterior no impiden avanzar la fase; los del mismo SHA sí:
    sin revisión nueva, siguen bloqueando y la fase no avanza.
    """
    if enc.deepseek_sha == sha and bloqueantes_abiertos(enc, "deepseek"):
        return enc
    registro.anotar(
        enc.id,
        {"kind": "nota_pr", "data": {"texto": texto, "hora": hora}},
        f"nota-deepseek:{enc.id}:{sha[:12]}",
    )
    enc = registro.fold(enc.id) or enc
    enc = _resolver_bloqueantes_deepseek_omitido(
        registro, enc, sha, texto, hora
    )
    registro.anotar(
        enc.id,
        {
            "kind": "entrega_externa",
            "data": {
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "paso": "deepseek_sin_revision",
                "repo": enc.entrega.repo,
                "numero": numero,
                "sha": sha,
                "huella": HUELLA_DEEPSEEK_SIN_REVISION,
                "recibo": sha,
                "evidencia": texto,
                "hora": hora,
            },
        },
        f"entrega:{enc.id}:{enc.fase_activa}:deepseek_sin_revision:{sha}",
    )
    _anotar_excepcion(
        registro, enc, "omision", sha, "", texto, hora,
        f"excepcion:{enc.id}:omision:{enc.fase_activa}:{sha}",
    )
    return registro.fold(enc.id) or enc


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


def _leer_cuerpo_del_espacio(espacio: Path) -> str | None:
    """Lee `.interno/cuerpo-pr.md` sin seguir enlaces ni aceptar un archivo
    compartido con otro lugar del host. Devuelve None si no es un archivo
    regular propio del espacio."""
    ruta = espacio / ".interno" / "cuerpo-pr.md"
    try:
        if capacidades._es_enlace(ruta):
            return None
        info = os.lstat(ruta)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return None
        if not ruta.resolve().is_relative_to(espacio.resolve()):
            return None
        fd = os.open(
            ruta,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except (OSError, ValueError, RuntimeError):
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return None
        entrada = os.fdopen(fd, "r", encoding="utf-8", errors="replace")
        fd = -1
        with entrada:
            try:
                return entrada.read(_LIMITE_CUERPO_PR + 1)
            except OSError:
                return None
    finally:
        if fd >= 0:
            os.close(fd)


def _cabe_en_el_limite(cuerpo: str) -> bool:
    """GitHub acepta 65 536 caracteres, pero el API también se queja por bytes
    (64 KiB en varios casos): se respetan las dos medidas."""
    return (
        len(cuerpo) <= _LIMITE_CUERPO_PR
        and len(cuerpo.encode("utf-8")) <= _LIMITE_CUERPO_PR
    )


def _cuerpo_recortado(texto: str, resto: str) -> str:
    max_caracteres = _LIMITE_CUERPO_PR - len(resto)
    max_bytes = _LIMITE_CUERPO_PR - len(resto.encode("utf-8"))
    if max_caracteres <= 0 or max_bytes <= 0:
        return resto[:_LIMITE_CUERPO_PR]
    candidato = texto[:max_caracteres]
    if len(candidato.encode("utf-8")) > max_bytes:
        candidato = candidato.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return f"{candidato}{resto}"


def _anotar_excepcion(
    registro: Registro, enc: Encargo, tipo: str, sha: str, hallazgo: str,
    motivo: str, hora: str, clave: str,
) -> None:
    """La excepción del check de merge, estructurada y con su SHA.

    El check solo acepta esta fuente; el texto del PR no autoriza nada.
    """
    registro.anotar(
        enc.id,
        {
            "kind": "excepcion",
            "data": {
                "tipo": tipo,
                "sha": str(sha or ""),
                "id": str(hallazgo or ""),
                "motivo": " ".join(str(motivo or "").split()),
                "hora": hora,
            },
        },
        clave,
    )


def _excepciones_del_registro(
    registro: Registro, enc: Encargo, sha: str
) -> list[dict]:
    """Las excepciones publicadas en el marcador del cuerpo del PR.

    Salen del pliegue: `Encargo.excepciones` deriva los eventos `excepcion`. Un
    encargo anterior a esa marca conserva los `hallazgo_fuera_de_alcance` como
    fuente mientras no haya ninguno estructurado, con su hora. Las entradas que
    `_fuera_de_alcance_por_restricciones` agrega sin evento no exceptúan: nunca
    se juzgaron en un SHA.
    """
    enc = registro.fold(enc.id) or enc
    if enc.excepciones:
        return [
            {
                "tipo": tipo,
                "sha": sha_evento,
                "id": identificador,
                "motivo": motivo,
                "hora": hora_evento,
            }
            for tipo, sha_evento, identificador, motivo, hora_evento in enc.excepciones
        ]
    return [
        {
            "tipo": "fuera_alcance",
            "sha": str(sha or ""),
            "id": str(hid or "").strip(),
            "motivo": " ".join(str(razon or "").split()),
            "hora": str(hora_evento or ""),
        }
        for hid, razon, hora_evento in enc.excepciones_legado
        if str(hid or "").strip() and str(razon or "").strip()
    ]


def _cuerpo_pr(
    enc: Encargo, espacio: Path, fase_id: str = "", excepciones=(),
) -> str:
    """El cuerpo del PR: lo que el responsable dejó en `.interno/cuerpo-pr.md`,
    con una línea final que identifica el encargo y la fase. Sin archivo o vacío
    se conserva el cuerpo de siempre. Los pendientes y notas del motor se añaden
    siempre después del texto del responsable. `fase_id` permite publicar el
    cuerpo de una fase anterior, como la de un hallazgo tardío."""
    defecto = f"Encargo {enc.id}. Revision independiente aprobada."
    texto = _leer_cuerpo_del_espacio(espacio)
    vacio = texto is None or not texto.replace("\x00", "").strip()
    if vacio:
        texto = defecto
    texto = texto.replace("\x00", "").rstrip()
    adicionales: list[str] = []
    # Notas y pendientes por fase: el cuerpo de una fase lleva lo suyo, no el
    # de otras. Los pendientes del encargo valen mientras la entrega está en
    # curso; cuando la fase ya se bloqueó, viven en la fase.
    fases = getattr(enc, "fases", ())
    fase = None
    if fase_id:
        fase = next((f for f in fases if f.id == fase_id), None)
    elif fases:
        fase = enc.fase_actual
    pendientes = getattr(enc, "pendientes_entrega", ())
    if not pendientes and fase is not None:
        pendientes = fase.pendientes
    notas = fase.notas if fase is not None else ()
    if not notas:
        notas = getattr(enc, "notas_pr", ()) if fase is None else ()
    if pendientes:
        adicionales.append(
            "## Pendientes abiertos al entregar\n"
            + "\n".join(f"- {p}" for p in pendientes)
        )
    fuera = getattr(enc, "fuera_de_alcance", ())
    if fuera:
        adicionales.append(
            "## Hallazgos fuera de alcance\n"
            + "\n".join(
                f"- {hid} «{titulo}»: {razon}"
                for hid, titulo, razon in fuera
            )
        )
    if notas:
        adicionales.append(
            "## Notas del motor\n" + "\n".join(f"- {n}" for n in notas)
        )
    if vacio and not adicionales and not excepciones:
        return defecto
    if adicionales:
        texto = texto + "\n\n" + "\n\n".join(adicionales)
    linea = (
        f"Encargo {enc.id}. Fase: "
        f"{fase_id or enc.fase_activa or FASE_IMPLICITA}."
    )
    # El marcador va junto a la línea del encargo: el recorte conserva ese
    # final, así que el check nunca pierde la fuente de excepciones.
    marca = ""
    if excepciones:
        marca = (
            "\n<!-- motor:excepciones="
            + json.dumps(list(excepciones), ensure_ascii=False, sort_keys=True)
            + " -->"
        )
    cuerpo = f"{texto}\n\n{linea}{marca}"
    if _cabe_en_el_limite(cuerpo):
        return cuerpo
    resto = f"{_NOTA_CUERPO_RECORTADO}\n\n{linea}{marca}"
    return _cuerpo_recortado(texto, resto)


def _huella_cuerpo(cuerpo: str) -> str:
    return hashlib.sha256(cuerpo.encode("utf-8")).hexdigest()[:16]


def _cuerpo_ya_publicado(
    registro: Registro, enc: Encargo, sha: str, huella: str = "",
    fase_id: str = "",
) -> bool:
    enc = registro.fold(enc.id) or enc
    fase = fase_id or enc.fase_activa
    return any(
        f == fase and s == sha and (not huella or h == huella)
        for f, s, h in enc.cuerpos_publicados
    )


def _anotar_cuerpo_publicado(
    registro: Registro, enc: Encargo, numero: int, sha: str, hora: str,
    huella: str = "", fase_id: str = "",
) -> None:
    fase = fase_id or enc.fase_activa
    registro.anotar(
        enc.id,
        {
            "kind": "entrega_externa",
            "data": {
                "fase": fase,
                "intento": enc.intento,
                "paso": "pr_cuerpo",
                "repo": enc.entrega.repo,
                "numero": numero,
                "sha": sha,
                "huella": huella,
                "hora": hora,
            },
        },
        f"entrega:{enc.id}:{fase}:pr_cuerpo:{sha}:{huella}",
    )


def _actualizar_cuerpo_pr(
    registro: Registro,
    enc: Encargo,
    gh,
    numero: int,
    sha: str,
    espacio: Path,
    hora: str,
    fase_id: str = "",
) -> Encargo:
    cuerpo = _cuerpo_pr(
        enc,
        espacio,
        fase_id,
        _excepciones_del_registro(registro, enc, enc.pr_sha or sha),
    )
    huella = _huella_cuerpo(cuerpo)
    if _cuerpo_ya_publicado(registro, enc, sha, huella, fase_id):
        return enc
    try:
        gh.actualizar_cuerpo(enc.entrega.repo, numero, cuerpo)
    except Exception as exc:
        _anotar_instrumento(
            registro,
            enc,
            "github",
            f"no se pudo actualizar el cuerpo del PR #{numero}: {exc}",
            "reintentar el cuerpo en la próxima publicación",
            hora,
            f"cuerpo-{sha[:12]}",
        )
        return registro.fold(enc.id) or enc
    _anotar_cuerpo_publicado(
        registro, enc, numero, sha, hora, huella, fase_id
    )
    return registro.fold(enc.id) or enc


def _refrescar_cuerpo_excepciones(
    registro: Registro, enc: Encargo, gh, espacio: Path, hora: str
) -> Encargo:
    """El PR ya abierto recibe el marcador de excepciones del check de merge.

    Ese marcador cubre descartes del revisor, hallazgos fuera de alcance y la
    omisión de DeepSeek. El cuerpo se publica por SHA; la huella decide si hay
    algo nuevo que publicar, así que repetir la pasada no lo reescribe.
    """
    if gh is None or not getattr(enc, "pr_numero", 0):
        return enc
    if not _excepciones_del_registro(registro, enc, enc.pr_sha):
        return enc
    return _actualizar_cuerpo_pr(
        registro, enc, gh, enc.pr_numero, enc.pr_sha, espacio, hora
    )


def _huella_comentarios(comentarios: list[dict]) -> str:
    """Identidad del comentario que DeepSeek tiene hoy sobre un SHA. Cambia con
    el estado de un hallazgo, una generación nueva o una re-revisión."""
    h = hashlib.sha256()
    for c in comentarios:
        h.update(str(c.get("texto") or "").encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


_ESTADOS_REVISION_AGOTADA = ("timeout", "timed_out", "timed-out", "time_out")
_MARCA_COMPLETION_DEEPSEEK = "ai-review:completion="


def _completion_deepseek(texto: str) -> dict:
    """El marcador estructurado del sticky de ai-review, si lo trae."""
    inicio = texto.find(_MARCA_COMPLETION_DEEPSEEK)
    if inicio < 0:
        return {}
    resto = texto[inicio + len(_MARCA_COMPLETION_DEEPSEEK):].strip()
    try:
        valor, _ = json.JSONDecoder().raw_decode(resto)
    except ValueError:
        return {}
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        return {"status": valor}
    return {}


def _sin_reintento(valor) -> bool:
    if valor is False:
        return True
    return isinstance(valor, str) and valor.strip().lower() in (
        "false", "no", "0"
    )


def _comentario_sin_revision(comentario: dict, sha: str) -> bool:
    """El sticky declara que la revisión no llegó y no se reintenta.

    Manda el marcador de completitud; la frase de 900 s es sólo el respaldo
    para comentarios viejos sin marcador. Un comentario con un Critical, High
    o Medium dentro del alcance no es una ausencia: es una revisión real.
    """
    texto = str(comentario.get("texto") or "")
    if not texto:
        return False
    completion = _completion_deepseek(texto)
    estado = str(
        completion.get("status") or completion.get("state") or ""
    ).strip().lower()
    declara = estado in _ESTADOS_REVISION_AGOTADA and _sin_reintento(
        completion.get("retry", completion.get("reintentar"))
    )
    if not declara:
        bajo = texto.lower()
        declara = (
            ("excedió el tiempo límite" in bajo or "excedio el tiempo limite" in bajo)
            and ("no se reintenta" in bajo or "not retried" in bajo
                 or "will not retry" in bajo)
        )
    if not declara:
        return False
    return not any(
        h.gravedad == "bloqueante"
        for h in hallazgos_deepseek([{"texto": texto, "sha": sha}], sha)
    )


def _deepseek_con_contenido(comentarios: list[dict], sha: str) -> list[dict]:
    """Los comentarios que realmente revisan el SHA, sin los avisos de timeout."""
    return [
        c for c in comentarios if not _comentario_sin_revision(c, sha)
    ]


def _anotar_lectura_deepseek(
    registro: Registro, enc: Encargo, numero: int, sha: str,
    comentarios: list[dict], hora: str,
) -> Encargo:
    """Anota lo que DeepSeek dice hoy del SHA de la fase activa. Un hallazgo
    abierto que el comentario ya no trae queda resuelto. Cada lectura distinta
    tiene su clave (huella del comentario y número de lectura del SHA): el
    mismo comentario no anota nada dos veces y un comentario que vuelve a un
    texto anterior sí se lee otra vez."""
    hallazgos = list(hallazgos_deepseek(comentarios, sha))
    fuera: list[tuple[str, str, str]] = []
    for indice, h in enumerate(hallazgos):
        if h.gravedad != "bloqueante":
            continue
        razon = hallazgo_fuera_de_alcance_por_restriccion(
            enc.restricciones,
            h,
            permisos=enc.permisos,
            prohibiciones=enc.prohibiciones,
        ) or hallazgo_contra_politica(h)
        if razon:
            fuera.append((h.id, h.titulo, razon))
            hallazgos[indice] = replace(
                h, gravedad="observacion", estado="resuelto", cierre_evidencia=razon
            )
    nuevos_bloq = {h.id for h in hallazgos if h.gravedad == "bloqueante"}
    fusion = list(hallazgos)
    for h in bloqueantes_abiertos(enc, "deepseek"):
        if h.id not in nuevos_bloq:
            fusion.append(replace(h, estado="resuelto"))
    huella = _huella_comentarios(comentarios)
    for hid, titulo, razon in fuera:
        registro.anotar(
            enc.id,
            {
                "kind": "hallazgo_fuera_de_alcance",
                "data": {"id": hid, "titulo": titulo, "razon": razon, "hora": hora},
            },
            f"fuera-alcance:{enc.id}:{enc.fase_activa}:{hid}",
        )
        _anotar_excepcion(
            registro, enc, "fuera_alcance", sha, hid, razon, hora,
            f"excepcion:{enc.id}:fuera:{sha}:{hid}",
        )
    if fuera:
        enc = registro.fold(enc.id) or enc
    enc = registro.fold(enc.id) or enc
    lecturas = dict(
        ((f, s), n) for f, s, n in enc.lecturas_deepseek
    ).get((enc.fase_activa, sha), 0)
    serie = f"{sha}:lector{VERSION_LECTOR_DEEPSEEK}:{huella}:{lecturas}"
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
                "cubre": [h.id for h in bloqueantes_abiertos(enc, "deepseek")],
                "hallazgos": [h.como_dict() for h in fusion],
                "hora": hora,
            },
        },
        f"revision-ds:{enc.id}:{enc.fase_activa}:{serie}",
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
                "huella": huella,
                "recibo": sha,
                "hora": hora,
            },
        },
        f"entrega:{enc.id}:{enc.fase_activa}:deepseek_leido:{serie}",
    )
    return registro.fold(enc.id)


def _marca_deepseek(enc: Encargo) -> dict:
    return enc.lectura_deepseek.como_dict()


def _marcar_lectura_deepseek(
    registro: Registro,
    enc: Encargo,
    sha: str,
    epoch: float,
    error: str = "",
    aviso: float = 0.0,
) -> None:
    registro.anotar(
        enc.id,
        {
            "kind": "deepseek_lectura",
            "data": {
                "sha": sha,
                "epoch": epoch,
                "error": error,
                "aviso": aviso,
                "hora": ahora_iso(epoch),
            },
        },
        f"deepseek-lectura:{enc.id}:{sha}:{int(epoch * 1000)}",
    )


def _releer_deepseek(
    registro: Registro, enc: Encargo, gh, directorio: Path, hora: str, epoch: float,
    devolver: bool = True,
) -> Encargo:
    """El comentario de DeepSeek sobre el SHA vigente puede cambiar sin que
    cambie el SHA: un hallazgo descartado o resuelto, una generación nueva, una
    re-revisión. Cada barrido, con el espaciado de `_RELECTURA_DEEPSEEK_S`, se
    mira el comentario; si es el mismo que se leyó no se anota nada. Con un
    agente en curso solo se anota: devolver el trabajo es cosa de quien
    incorpora su vuelta."""
    enc = registro.fold(enc.id) or enc
    fase = enc.fase_actual
    if (
        gh is None
        or fase is None
        or enc.via == "directa"
        or enc.entrega.nivel not in ("pr", "integrado", "desplegado")
        or fase.nivel_alcanzado not in ("pr_abierto", "pr")
        or fase.estado == "preparada"
        or not fase.pr_numero
        or not fase.pr_sha
        or fase.deepseek_sha != fase.pr_sha
        or enc.estado in ("terminado", "cancelado", "deteniendo", "detenido")
    ):
        return enc
    sha = fase.pr_sha
    marca = _marca_deepseek(enc)
    try:
        desde = epoch - float(marca.get("epoch") or 0)
    except (TypeError, ValueError):
        desde = _RELECTURA_DEEPSEEK_S
    espaciado = _REINTENTO_FORZADO_S if marca.get("error") else _RELECTURA_DEEPSEEK_S
    if marca.get("sha") == sha and 0 <= desde < espaciado:
        return enc
    try:
        comentarios = _deepseek_con_contenido(
            gh.comentarios_deepseek(enc.entrega.repo, fase.pr_numero, sha), sha
        )
    except Exception as exc:
        # El espaciado no se consume con una lectura que falló: se reintenta
        # pronto. La causa queda en el registro (`lectura_fallida`) y en la
        # marca, sin tocar motivo, siguiente ni causa: el próximo turno del
        # agente no se redirige.
        error = f"{type(exc).__name__}: {exc}"
        # Un aviso al empezar el tramo de fallos y otro cuando pasan
        # `_AVISO_LECTURA_DEEPSEEK_S` desde el último, no según el reloj.
        try:
            ultimo_aviso = float(marca.get("aviso") or 0)
        except (TypeError, ValueError):
            ultimo_aviso = 0.0
        nuevo_tramo = (
            not marca.get("error") or epoch - ultimo_aviso >= _AVISO_LECTURA_DEEPSEEK_S
        )
        _marcar_lectura_deepseek(
            registro, enc, sha, epoch, error, epoch if nuevo_tramo else ultimo_aviso
        )
        if not nuevo_tramo:
            return enc
        registro.anotar(
            enc.id,
            {
                "kind": "lectura_fallida",
                "data": {
                    "fase": enc.fase_activa,
                    "fuente": "deepseek",
                    "sha": sha,
                    "causa": f"no se pudo releer el comentario de DeepSeek: {error}",
                    "hora": hora,
                },
            },
            f"lectura-fallida:{enc.id}:{enc.fase_activa}:{sha}:{int(epoch)}",
        )
        return registro.fold(enc.id) or enc
    _marcar_lectura_deepseek(registro, enc, sha, epoch)
    if marca.get("error"):
        registro.anotar(
            enc.id,
            {
                "kind": "lectura_fallida",
                "data": {
                    "fase": enc.fase_activa,
                    "fuente": "deepseek",
                    "sha": sha,
                    "causa": "",
                    "hora": hora,
                },
            },
            f"lectura-recuperada:{enc.id}:{enc.fase_activa}:{sha}:{int(epoch)}",
        )
        enc = registro.fold(enc.id) or enc
    if not comentarios:
        return enc
    huella = _huella_comentarios(comentarios)
    if huella == fase.deepseek_huella:
        return enc
    enc = _anotar_lectura_deepseek(
        registro, enc, fase.pr_numero, sha, comentarios, hora
    )
    abiertos = bloqueantes_abiertos(enc, "deepseek")
    if (
        devolver
        and abiertos
        and enc.estado in ("recibido", "ejecutando")
        and enc.proc is None
        and not enc.reserva
    ):
        pendientes_disputa = _disputas_pendientes(
            registro, enc, interno_de(_espacio(registro.base, enc.id))
        )
        if pendientes_disputa:
            _devolver(
                registro, enc, "decidir_disputa",
                "el responsable sostiene con evidencia que no aplican: "
                + "; ".join(f"{h.id} {h.titulo}" for h, _ in pendientes_disputa),
                "decidir con la evidencia si el hallazgo aplica; si no aplica, "
                "descártalo y deja la razón en el PR",
                hora, f"disputa-revisor-{huella}", rol="revisor",
            )
        else:
            _devolver(
                registro, enc, "investigar", abiertos[0].titulo,
                "corregir " + ", ".join(h.id for h in abiertos)
                + " (DeepSeek); lo aprobado que no cambio se conserva",
                hora, f"deepseek-{huella}",
            )
    elif (
        devolver
        and not abiertos
        and enc.estado not in (
            "terminado", "cancelado", "deteniendo", "detenido"
        )
        and enc.proc is None
        and not enc.reserva
        and enc.revision_vigente is not None
        and enc.revision_vigente.veredicto == "aprobado"
        and not bloqueantes_abiertos(enc, "independiente")
        and not bloqueantes_abiertos(enc, "supervisor")
        and not _revision_aprobada_vigente(enc)
    ):
        # DeepSeek quedó limpio. Si la corrección cambió el producto desde la
        # última aprobación de Opus, el tramo acumulado necesita su única
        # revisión final antes de integrar o cerrar. Con un bloqueante de Opus o
        # del supervisor abierto, o un veredicto que no es «aprobado», sigue el
        # ciclo normal: el turno es del responsable, no otra revisión sobre lo
        # ya rechazado.
        _devolver(
            registro, enc, "revisar_evidencia",
            "DeepSeek quedó sin bloqueantes; falta la revisión final del tramo corregido",
            "revisar solo lo que cambió desde la última aprobación de Opus "
            "antes de integrar o cerrar",
            hora,
            f"revision-final-{enc.producto.firma if enc.producto else ''}",
            rol="revisor",
        )
    return registro.fold(enc.id)


def _epoch_de_hora(hora: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(
            str(hora).replace("Z", "+00:00")
        ).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _cierre_epoch(registro: Registro, enc: Encargo) -> float:
    """La época del cierre del encargo, no la primera vez que el motor lo ve.

    El cierre puede ser una comprobación confirmada, un cierre forzado histórico
    o el bloqueo de la última fase: un encargo cerrado como entrega parcial
    tiene su época en el `bloqueo_fase`, no en el cierre de una fase anterior.
    """
    enc = registro.fold(enc.id) or enc
    if enc.hora_cierre:
        return _epoch_de_hora(enc.hora_cierre)
    return _epoch_de_hora(enc.hora)


def _marca_deepseek_cerrado(enc: Encargo) -> dict:
    return enc.deepseek_cerrado.como_dict()


def _ukey_de_hecho(prefijo: str, encargo_id: str, datos: dict) -> str:
    """La clave sale del contenido del hecho: repetirlo no duplica el evento."""
    estable = {k: v for k, v in datos.items() if k != "hora"}
    huella = hashlib.sha256(
        json.dumps(estable, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()[:16]
    return f"{prefijo}:{encargo_id}:{huella}"


def _marcar_deepseek_cerrado(
    registro: Registro, enc: Encargo, marca: dict, hora: str
) -> None:
    datos = {
        "shas": marca.get("shas") or {},
        "inicios": marca.get("inicios") or {},
        "desde": float(marca.get("desde") or 0),
        "hora": hora,
    }
    registro.anotar(
        enc.id,
        {"kind": "deepseek_cerrado", "data": datos},
        _ukey_de_hecho("deepseek-cerrado", enc.id, datos),
    )


def _anotar_nota_en_pr_tardia(
    registro: Registro, enc: Encargo, gh, fase, sha: str, huella: str,
    texto: str, hora: str,
) -> Encargo:
    """Añade la nota al cuerpo actual del PR sin reescribir el de la fase
    vigente. Un PR de una fase anterior conserva su descripción original."""
    serie = f"{enc.id}:{fase.id}:{sha}:{huella}"
    ukey = f"cuerpo-tardio:{serie}"
    if registro.por_ukey(ukey) is not None:
        return enc
    try:
        vista = gh.ver(enc.entrega.repo, fase.pr_numero)
    except Exception as exc:
        _anotar_instrumento(
            registro, enc, "github",
            f"no se pudo leer el cuerpo del PR #{fase.pr_numero}: {exc}",
            "reintentar la nota del hallazgo tardío en la próxima pasada",
            hora, f"cuerpo-tardio-{sha[:12]}",
        )
        return registro.fold(enc.id) or enc
    cuerpo = str(getattr(vista, "cuerpo", "") or "").rstrip()
    if texto not in cuerpo:
        encabezado = (
            "" if "## Notas del motor" in cuerpo else "## Notas del motor\n"
        )
        nuevo = f"{cuerpo}\n\n{encabezado}- {texto}".strip()
        if not _cabe_en_el_limite(nuevo):
            # No se recorta el cuerpo existente: se perderían en silencio las
            # notas previas. La nota queda en el estado del encargo.
            registro.anotar(
                enc.id,
                {
                    "kind": "nota_pr",
                    "data": {
                        "texto": (
                            texto
                            + " (el cuerpo del PR llegó al límite; la nota "
                            "queda en el estado del encargo)"
                        ),
                        "hora": hora,
                    },
                },
                ukey,
            )
            return registro.fold(enc.id) or enc
        try:
            gh.actualizar_cuerpo(enc.entrega.repo, fase.pr_numero, nuevo)
        except Exception as exc:
            _anotar_instrumento(
                registro, enc, "github",
                f"no se pudo anotar el hallazgo tardío en el PR "
                f"#{fase.pr_numero}: {exc}",
                "reintentar la nota del hallazgo tardío en la próxima pasada",
                hora, f"cuerpo-tardio-{sha[:12]}",
            )
            return registro.fold(enc.id) or enc
    registro.anotar(
        enc.id,
        {"kind": "nota_pr", "data": {"texto": texto, "hora": hora}},
        ukey,
    )
    return registro.fold(enc.id) or enc


def _avisar_hallazgo_tardio(
    registro: Registro, enc: Encargo, fase, sha: str, huella: str,
    abiertos: list, gh, espacio: Path, hora: str,
) -> Encargo:
    """Un hallazgo tardío se avisa en el PR de su fase y al director.

    Nunca cambia el estado del encargo: una pregunta por un PR ya cerrado lo
    sacaba de `terminado` y, tras el ajuste, lo dejaba en `recibido` para
    siempre. La nota y el aviso son idempotentes por SHA y huella del
    comentario, así que releer el mismo comentario no los repite.
    """
    detalle = "; ".join(f"{h.id} {h.titulo}" for h in abiertos)
    texto = (
        f"DeepSeek encontró {detalle} en el PR #{fase.pr_numero} de la fase "
        f"{fase.id} después de cerrar; si quieres corregirlo, abre otro encargo."
    )
    serie = f"{enc.id}:{fase.id}:{sha}:{huella}"
    registro.anotar(
        enc.id,
        {"kind": "nota_pr", "data": {"texto": texto, "hora": hora}},
        f"nota-tardia:{serie}",
    )
    registro.anotar(
        enc.id,
        {"kind": "procedimiento", "data": {"nota": texto, "hora": hora}},
        f"aviso-director:{serie}",
    )
    enc = registro.fold(enc.id) or enc
    if gh is not None and espacio is not None:
        enc = _anotar_nota_en_pr_tardia(
            registro, enc, gh, fase, sha, huella, texto, hora
        )
    return enc


def _revisar_deepseek_del_cerrado(
    registro: Registro, enc: Encargo, gh, hora: str, epoch: float,
    presupuesto: list[int] | None = None,
) -> Encargo:
    """Un hallazgo tardío de DeepSeek en un encargo ya cerrado se avisa.

    El encargo terminado no vuelve al ciclo normal, así que la relectura de
    DeepSeek —que excluye los cerrados— nunca lo vería. Se miran los PR de
    todas las fases con entrega externa, no solo el de la fase activa: una
    revisión tardía sobre una fase anterior integrada también se avisa. Cada
    Critical, High o Medium dentro del alcance queda como nota en el PR de su
    fase y como aviso informativo al director, en una sola pasada. El estado
    no cambia. Una ausencia declarada por el sticky no cuenta como hallazgo.

    El sondeo se limita a 7 días desde el cierre del encargo. Los primeros
    60 minutos relee cada minuto y después cada 30 minutos; los errores también
    se espacian. Los encargos cerrados antes de que este cambio empezara a
    observar no se sondean. El presupuesto compartido limita las consultas por
    pasada de barrido; lo que falte se retoma en la siguiente.
    """
    enc = registro.fold(enc.id) or enc
    if (
        gh is None
        or enc.estado != "terminado"
        or enc.entrega.nivel not in ("pr", "integrado", "desplegado")
    ):
        return enc
    fases = [
        f for f in enc.fases
        if f.pr_numero
        and f.pr_sha
        and f.nivel_alcanzado in ("pr", "integrado", "desplegado")
    ]
    if not fases:
        return enc
    espacio = _espacio(registro.base, enc.id)
    marca = _marca_deepseek_cerrado(enc)
    if not marca["desde"]:
        marca["desde"] = epoch
        _marcar_deepseek_cerrado(registro, enc, marca, hora)
    cierre = _cierre_epoch(registro, enc)
    if cierre and cierre < marca["desde"] - CERRADOS_GRACIA_S:
        return enc
    edad = max(0.0, epoch - cierre) if cierre else 0.0
    if edad >= CERRADOS_VENTANA_S:
        return enc
    if presupuesto is None:
        presupuesto = [CERRADOS_POR_PASADA]
    leidas = marca["shas"]
    cambio = False
    espaciado = (
        _RELECTURA_DEEPSEEK_S
        if edad < CERRADOS_SONDEO_RAPIDO_S
        else CERRADOS_RELECTURA_S
    )
    for fase in fases:
        sha = fase.pr_sha
        desde = epoch - leidas.get(sha, 0.0)
        if 0 <= desde < espaciado:
            continue
        if presupuesto[0] <= 0:
            break
        presupuesto[0] -= 1
        try:
            crudos = gh.comentarios_deepseek(
                enc.entrega.repo, fase.pr_numero, sha
            )
        except Exception:
            # El error también se espacia: la marca de la última tentativa
            # vale para la siguiente lectura.
            leidas[sha] = epoch
            cambio = True
            continue
        leidas[sha] = epoch
        cambio = True
        comentarios = _deepseek_con_contenido(crudos, sha)
        if not comentarios:
            continue
        huella = _huella_comentarios(comentarios)
        if huella == fase.deepseek_huella:
            continue
        abiertos = [
            h for h in hallazgos_deepseek(comentarios, sha)
            if h.gravedad == "bloqueante"
            and not hallazgo_fuera_de_alcance_por_restriccion(
                enc.restricciones,
                h,
                permisos=enc.permisos,
                prohibiciones=enc.prohibiciones,
            )
        ]
        if not abiertos:
            continue
        enc = _avisar_hallazgo_tardio(
            registro, enc, fase, sha, huella, abiertos, gh, espacio, hora
        )
    if cambio:
        _marcar_deepseek_cerrado(registro, enc, marca, hora)
    return enc


def _cierre_sin_aplicar(
    registro: Registro, enc: Encargo, que: str, sha: str, hora: str
) -> Encargo:
    """Un evento que el registro no aplicó no deja cerrar: DeepSeek tiene un
    veredicto que el estado del encargo no recoge. Se anota y se reintenta."""
    causa = f"no se pudo anotar {que} antes de cerrar"
    _anotar_instrumento(
        registro, enc, "github", causa,
        "reintentar la comprobación del veredicto de DeepSeek antes de cerrar",
        hora, f"deepseek-sin-aplicar-{sha[:12]}",
    )
    marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
    return registro.fold(enc.id)


def _veredicto_antes_de_cerrar(
    registro: Registro, enc: Encargo, gh, hora: str
) -> Encargo | None:
    """Antes de terminar una fase con entrega pr (con integrado o desplegado,
    antes del merge), el veredicto vigente de DeepSeek sobre el SHA final de su
    PR; y, si es la última fase, sobre el de cada PR de las anteriores:
    DeepSeek puede publicar un hallazgo después de que el motor leyó, y la fase
    ya cerrada no volvería a mirarse. Se reabre la fase cuyo PR sigue abierto
    (nivel alcanzado pr); con integrado o desplegado las anteriores ya se
    fusionaron y su corrección no cabe en ese PR: se avisa en su PR y al
    director, sin cambiar el estado ni frenar el cierre.

    Devuelve None si se puede cerrar. Si hay un High o un Medium abierto, la
    fase de ese PR vuelve al responsable y se devuelve el encargo así."""
    if (
        gh is None
        or enc.entrega.nivel not in ("pr", "integrado", "desplegado")
        or enc.nivel_alcanzado != "pr"
        or not enc.pr_numero
        or not enc.pr_sha
        or enc.deepseek_sha != enc.pr_sha
    ):
        return None
    fase = enc.fase_actual
    sha = enc.pr_sha
    try:
        comentarios = _deepseek_con_contenido(
            gh.comentarios_deepseek(enc.entrega.repo, enc.pr_numero, sha), sha
        )
        previas = []
        if all(f.estado == "preparada" for f in enc.fases if f.id != enc.fase_activa):
            for f in enc.fases:
                if (
                    f.id == enc.fase_activa
                    or f.nivel_alcanzado not in ("pr", "integrado", "desplegado")
                    or not f.pr_numero
                    or not f.pr_sha
                ):
                    continue
                previas.append(
                    (
                        f,
                        _deepseek_con_contenido(
                            gh.comentarios_deepseek(
                                enc.entrega.repo, f.pr_numero, f.pr_sha
                            ),
                            f.pr_sha,
                        ),
                    )
                )
    except Exception as exc:
        if fase.deepseek_huella != HUELLA_DEEPSEEK_SIN_REVISION:
            causa = f"no se pudieron leer los comentarios de DeepSeek: {exc}"
            _anotar_instrumento(
                registro, enc, "github", causa,
                "reintentar la lectura de DeepSeek antes de cerrar",
                hora, f"deepseek-cerrar-{sha[:12]}",
            )
            marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
            return registro.fold(enc.id)
        # La fase siguió sin revisión tras el tope: una relectura fallida no
        # vuelve a frenar el cierre. Si llega un comentario, el siguiente
        # barrido lo lee y lo trata como tardío.
        return None
    if comentarios and _huella_comentarios(comentarios) != fase.deepseek_huella:
        enc = _anotar_lectura_deepseek(
            registro, enc, enc.pr_numero, sha, comentarios, hora
        )
        if enc.fase_actual.deepseek_huella != _huella_comentarios(comentarios):
            return _cierre_sin_aplicar(
                registro, enc, "la lectura de DeepSeek", sha, hora
            )
        abiertos = bloqueantes_abiertos(enc, "deepseek")
        if abiertos:
            _devolver(
                registro, enc, "investigar", abiertos[0].titulo,
                "corregir " + ", ".join(h.id for h in abiertos)
                + " (DeepSeek); lo aprobado que no cambio se conserva",
                hora, f"deepseek-{_huella_comentarios(comentarios)}",
            )
            return registro.fold(enc.id)
    for previa, comentarios in previas:
        if not comentarios or _huella_comentarios(comentarios) == previa.deepseek_huella:
            continue
        abiertos = [
            h for h in hallazgos_deepseek(comentarios, previa.pr_sha)
            if h.gravedad == "bloqueante"
        ]
        if not abiertos:
            continue
        huella = _huella_comentarios(comentarios)
        if previa.nivel_alcanzado != "pr":
            # El PR ya se fusionó: la corrección no cabe en él. No se termina
            # en silencio con el hallazgo abierto: se deja la nota en el PR de
            # esa fase y el aviso al director, sin cambiar el estado ni
            # frenar el cierre.
            enc = _avisar_hallazgo_tardio(
                registro, enc, previa, previa.pr_sha, huella, abiertos, gh,
                _espacio(registro.base, enc.id), hora,
            )
            continue
        registro.anotar(
            enc.id,
            {
                "kind": "fase_reabierta",
                "data": {
                    "fase": previa.id,
                    "sha": previa.pr_sha,
                    "numero": previa.pr_numero,
                    "causa": (
                        f"DeepSeek dejó abierto en el PR #{previa.pr_numero} de la "
                        f"fase {previa.id}: " + "; ".join(h.titulo for h in abiertos)
                    ),
                    "hora": hora,
                },
            },
            f"reabierta:{enc.id}:{previa.id}:{previa.pr_sha}:{huella}",
        )
        enc = registro.fold(enc.id)
        if enc.fase_activa != previa.id:
            return _cierre_sin_aplicar(
                registro, enc, f"reabrir la fase {previa.id}", previa.pr_sha, hora
            )
        enc = _anotar_lectura_deepseek(
            registro, enc, previa.pr_numero, previa.pr_sha, comentarios, hora
        )
        abiertos_ds = bloqueantes_abiertos(enc, "deepseek")
        _devolver(
            registro, enc, "investigar",
            abiertos_ds[0].titulo if abiertos_ds else abiertos[0].titulo,
            "corregir " + ", ".join(h.id for h in abiertos_ds)
            + f" (DeepSeek) en el PR #{previa.pr_numero}; lo aprobado que no "
            "cambio se conserva",
            hora, f"deepseek-{huella}",
        )
        return registro.fold(enc.id)
    return None


def _cerrar_con_veredicto(
    registro: Registro, enc: Encargo, gh, espacio: Path, directorio: Path, hora: str
) -> Encargo:
    if gh is None and _requiere_veredicto(enc):
        # Sin puerto de GitHub no hay veredicto que comprobar: no se cierra.
        return enc
    devuelto = _veredicto_antes_de_cerrar(registro, enc, gh, hora)
    if devuelto is not None:
        return devuelto
    return _intentar_cierre(
        registro, enc, espacio, directorio, hora, veredicto_comprobado=True
    )


def _requiere_veredicto(enc: Encargo) -> bool:
    """Cerrar esta fase exige releer a DeepSeek: entrega con PR y nivel pr alcanzado."""
    return bool(
        enc.entrega.nivel in ("pr", "integrado", "desplegado")
        and enc.nivel_alcanzado == "pr"
        and enc.pr_numero
    )


def _sha_final_sin_leer(enc: Encargo, gh) -> bool:
    """El nivel pr ya se alcanzó pero el commit final no está leído por DeepSeek: o hay un
    commit nuevo sin leer, o el producto cambió después del commit del PR y aún no se publicó.
    Cerrar por el atajo daría por bueno un PR que no es el producto aprobado; se sigue por
    el camino normal (commit, push, lectura)."""
    return bool(
        gh is not None
        and enc.entrega.nivel == "pr"
        and enc.nivel_alcanzado == "pr"
        and enc.pr_numero
        and (
            enc.deepseek_sha != enc.pr_sha
            or (enc.producto is not None and not enc.producto.sha_git)
        )
    )


def _copia_log_job(
    espacio: Path, nombre: str, run_id: str, job_id: str, texto: str
) -> str:
    """El literal completo del log del job queda en el espacio del responsable
    (`.interno/logs/`, que no viaja en el producto: _commit_push lo excluye).
    Un fallo que vive en medio del log no puede perderse en un recorte: la
    devolución referencia la copia y el extracto la acompaña sin sustituirla
    (h_a8968fb0). Devuelve la ruta relativa al espacio, o "" si no se pudo.

    La identidad de la copia lleva el ID del job: dos jobs de una misma
    corrida pueden sanear al mismo nombre y no se sobrescriben (h_c4902b32).
    La escritura no sigue enlaces: rechaza un `.interno` o `logs` que sean
    enlaces, y crea el temporal con O_EXCL|O_NOFOLLOW (un enlace preexistente
    se quita a sí mismo, nunca se escribe a través de él), así el contenido
    del espacio no puede truncar nada fuera del espacio (h_ff7d6d39)."""
    if not texto:
        return ""
    if len(texto.encode("utf-8", errors="replace")) > capacidades.MAX_LOG:
        return ""
    slug = re.sub(r"[^A-Za-z0-9_.-]", "-", nombre).strip("-") or "job"
    destino = espacio / ".interno" / "logs"
    try:
        for padre in (espacio / ".interno", destino):
            if padre.is_symlink():
                return ""
        destino.mkdir(parents=True, exist_ok=True)
        sufijo = f"-job{job_id}" if job_id else ""
        ruta = destino / f"{slug}{sufijo}-run{run_id or 'sin-run'}.log"
        temporal = ruta.with_name(ruta.name + ".tmp")
        try:
            temporal.unlink()
        except OSError:
            pass
        fd = os.open(
            temporal,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texto)
        os.replace(temporal, ruta)
    except OSError:
        return ""
    return f".interno/logs/{ruta.name}"


def _recorte_log_job(texto: str, nombre: str, copia: str = "") -> str:
    """El literal completo del job queda copiado en el espacio; en el motivo se
    acota para no inundarlo: se conservan el principio y el final, y lo omitido
    queda anotado con dónde está el literal. El extracto acompaña a la copia,
    no la sustituye."""
    lineas = texto.splitlines()
    if len(lineas) <= _TOPE_LOG_JOB:
        return "\n".join(lineas)
    cabeza, cola = lineas[:55], lineas[-24:]
    omitidas = len(lineas) - len(cabeza) - len(cola)
    literal = copia or "el log del job"
    nota = (
        f"[log de {nombre}: se omiten {omitidas} líneas intermedias; "
        f"el literal completo queda en {literal}]"
    )
    return "\n".join([*cabeza, nota, *cola])


def _logs_de_fallidos(
    gh, repo: str, veredicto_ci, espacio: Path, corridas=None, detalles=None
) -> str:
    """El log literal de cada job en rojo: el job exacto por su ID (el
    check-run de Actions comparte el ID del job), no su nombre (con el nombre
    `gh` intenta /actions/jobs/<nombre> y falla, h_a8968fb0) y sin la cola
    agregada de la corrida, que mezcla los jobs. El literal completo queda
    copiado en el espacio del responsable y el motivo lo referencia; un fallo
    en medio del log no depende de dónde caiga en el extracto. Un log que no
    se pudo leer no frena la devolución: queda el nombre y la url."""
    partes = []
    for corrida in (
        veredicto_ci.corridas_fallidas if corridas is None else corridas
    ):
        nombre = str(corrida.get("nombre") or "")
        url = str(corrida.get("url") or "")
        run_id = str(corrida.get("run_id") or "")
        job_id = str(corrida.get("job_id") or "")
        texto = ""
        if run_id and nombre:
            try:
                texto = gh.log_fallido_job(repo, run_id, nombre, job_id)
            except Exception:
                texto = ""
        if texto:
            if detalles is not None:
                detalles[nombre] = texto
            copia = _copia_log_job(espacio, nombre, run_id, job_id, texto)
            cabeza = f"log de {nombre} ({url}):"
            if copia:
                cabeza += f"\ncopia literal: {copia}"
            partes.append(
                f"{cabeza}\n{_recorte_log_job(texto, nombre, copia)}".rstrip()
            )
        else:
            partes.append(f"corrida en rojo: {nombre} ({url})".rstrip())
    return "\n\n".join(partes)


_CONCLUSIONES_VERDES = ("success", "skipped", "neutral")
# Gitleaks imprime «leaks found: N» solo con hallazgos; el paso verde dice «no
# leaks found» y cada job de GitHub abre con «Secret source: Actions», así que
# buscar «secret» o «leak» sueltos clasificaba cualquier rojo como tubería.
_HALLAZGOS_SECRETOS = re.compile(r"leaks found: [1-9]|\bruleid:\s")
# Caídas que una prueba del producto no produce: mandan aunque el paso traiga
# pruebas en rojo.
_INFRA_CI_SEGURA = (
    "the job was not started",
    # «runner» suelto aparece en toda ruta /home/runner/work de un traceback.
    "runner has received a shutdown signal",
    "lost communication with the server",
    "no space left",
    "returned error: 5",
    "rpc failed",
    "early eof",
    "hung up unexpectedly",
    "failed to download action",
    "service is unhealthy",
    "could not resolve host",
    "connection reset",
    "unable to access",
    "computed checksum did not match",
)
# El resumen de un corredor de pruebas: con él, las marcas débiles de abajo son
# texto de las pruebas (una aserción sobre «billing» o «rate limit»).
# Sin anclar al inicio: cada línea del log de gh lleva la hora delante.
_RESUMEN_PRUEBAS = re.compile(
    r"\b[1-9]\d* (?:failed|errors?)\b|failed \((?:[a-z]+=\d+, )*(?:failures|errors)="
    r"|assertionerror|short test summary info"
)
_INFRA_CI = (
    "billing",
    "payment",
    "minutes",
    "rate limit",
    "could not resolve host",
    "connection refused",
    "timed out",
    "max retries exceeded",
)

# Marcas de que el rojo viene de una prueba del producto y no de la tubería.
# Sin una de estas, un job conocido sigue siendo «incierto»: no se devuelve al
# responsable ni se reintenta, se dice que no se puede clasificar con certeza.
_PRUEBA_CI = (
    "failed",
    "failure",
    "assertion",
    "traceback",
    "syntaxerror",
    "importerror",
    "modulenotfound",
    "no such file",
    "error:",
    "not ok",
)


def _texto_de_pasos_fallidos(texto: str) -> str:
    """Las líneas de los pasos que terminaron en error.

    `gh run view --log` trae el job entero como «job<TAB>paso<TAB>línea»: la
    cabecera, los pasos verdes y la limpieza nombran runners, secretos o
    «failed» sin haber fallado. Sin pasos identificables se usa el texto
    completo."""
    pasos: dict[str, list[str]] = {}
    for linea in str(texto or "").splitlines():
        partes = linea.split("\t", 2)
        if len(partes) == 3:
            pasos.setdefault(partes[1], []).append(partes[2])
    fallidos = [
        lineas for lineas in pasos.values() if any("##[error]" in x for x in lineas)
    ]
    if not fallidos:
        return str(texto or "")
    return "\n".join(x for lineas in fallidos for x in lineas)


def _clasificar_fallo_ci(nombre: str, conclusion: str, texto: str) -> str:
    """Clasifica un job en rojo para el aviso y la corrección.

    «codigo» es una prueba del producto que el responsable corrige en el mismo
    PR; «tuberia» es infraestructura (secretos o Gitleaks, cancelación, tiempo
    límite, GitHub caído o falta de minutos); «incierto» es un fallo sin
    diagnóstico suficiente para distinguirlos."""
    if str(conclusion or "") in (
        "timed_out",
        "startup_failure",
        "cancelled",
        "action_required",
        "stale",
    ):
        return "tuberia"
    bajo = _texto_de_pasos_fallidos(texto).lower()
    if _HALLAZGOS_SECRETOS.search(bajo) or any(m in bajo for m in _INFRA_CI_SEGURA):
        return "tuberia"
    if _RESUMEN_PRUEBAS.search(bajo):
        return "codigo"
    if any(marca in bajo for marca in _INFRA_CI):
        return "tuberia"
    if any(marca in bajo for marca in _PRUEBA_CI):
        return "codigo"
    return "incierto"


def _job_en_llano(nombre: str) -> str:
    base = str(nombre or "").split(" (")[0].strip()
    job = trabajos_ci.trabajo(base)
    nombres = {
        "bateria": "la comprobación de pruebas del producto",
        "ops-tests": "la comprobación de pruebas de las herramientas",
        "macos": "la comprobación en macOS",
        "windows": "la comprobación en Windows",
        "gate": "la revisión del repositorio",
    }
    return nombres.get(
        job,
        f"la comprobación automática «{base}»" if base else "una comprobación automática",
    )


def _causa_tuberia_en_llano(caidas, texto: str) -> str:
    junto = (
        _texto_de_pasos_fallidos(texto)
        + " "
        + " ".join(str(c.get("conclusion") or "") for c in caidas)
    ).lower()
    if _HALLAZGOS_SECRETOS.search(junto):
        return "el control de secretos encontró claves viejas en el repositorio"
    # Antes que los minutos: «has timed out after 15 minutes» es un límite de tiempo.
    if any(marca in junto for marca in ("timed out", "timed_out", "timeout")):
        return "se agotó el tiempo límite de la corrida"
    if any(marca in junto for marca in ("minutes", "billing", "payment")):
        return "faltan minutos de CI"
    if any(marca in junto for marca in ("cancelled", "canceled")):
        return "la corrida se canceló"
    if any(
        marca in junto
        for marca in (
            "could not resolve host",
            "connection",
            "runner has received a shutdown signal",
            "lost communication with the server",
            "returned error: 5",
            "rpc failed",
            "early eof",
            "hung up unexpectedly",
            "failed to download action",
            "service is unhealthy",
            "max retries exceeded",
            "rate limit",
            "unable to access",
            "startup_failure",
        )
    ):
        return "el servicio de GitHub no respondió"
    return "la tubería del CI falló"


def _aviso_ci_en_llano(
    clase: str, caidas, texto: str, reabrir: bool = True,
    sin_relanzamiento: bool = False,
) -> str:
    """El aviso para alguien que no es técnico: qué falló, de quién es y qué
    conviene hacer. Sin nombres de archivo, hashes ni el log literal."""
    que = " y ".join(
        dict.fromkeys(_job_en_llano(str(c.get("nombre") or "")) for c in caidas)
    ) or "una comprobación automática"
    if clase == "codigo" and not reabrir:
        return (
            f"Falló {que}: es del código del cambio. El cambio ya está "
            "integrado; recomiendo abrir un encargo para corregirlo."
        )
    if clase == "codigo":
        return (
            f"Falló {que}: es del código del cambio, no de la tubería. Ya se "
            "mandó a corregir en el mismo PR, no tienes que hacer nada."
        )
    if clase == "tuberia":
        causa = _causa_tuberia_en_llano(caidas, texto)
        if sin_relanzamiento:
            # No hubo segundo intento (no se pudo identificar el run): no se
            # sabe si era pasajero, así que no se anuncia problema fijo.
            return (
                f"La tubería falló: {causa}. No se pudo relanzar sola para ver "
                "si era pasajero. El cambio ya está revisado; recomiendo "
                "mergear y abrir un encargo para arreglar la tubería."
            )
        return (
            f"La tubería de este proyecto tiene un problema fijo: {causa}. "
            "El cambio ya está revisado; recomiendo mergear y abrir un encargo "
            "para arreglar la tubería."
        )
    return (
        f"Falló {que} y no puedo saber con certeza si es del código del cambio "
        "o de la tubería. Recomiendo pedirle a Claude que lo revise."
    )


def _anotar_ci_del_pr(
    registro: Registro,
    enc: Encargo,
    *,
    ukey: str,
    fase: str,
    sha: str,
    estado: str,
    fallas=(),
    causa: str = "",
    reabrir: bool = False,
    clase: str = "",
    reintento: str = "",
    aviso: str = "",
    hora: str,
) -> Encargo:
    registro.anotar(
        enc.id,
        {
            "kind": "ci_pr",
            "data": {
                "fase": fase,
                "sha": sha,
                "estado": estado,
                "fallas": list(fallas),
                "causa": causa,
                "reabrir": reabrir,
                "clase": clase,
                "reintento": reintento,
                "aviso": aviso,
                "hora": hora,
            },
        },
        ukey,
    )
    return registro.fold(enc.id) or enc


_CI_SONDEO_S = 60
_CI_SONDEO_MAX_S = 90 * 60


def _segundos_entre(desde: str, hasta: str) -> float | None:
    from datetime import datetime

    try:
        a = datetime.fromisoformat(str(desde).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(hasta).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (b - a).total_seconds()


def _sondeo_ci(enc: Encargo) -> dict:
    return enc.sondeo_ci.como_dict()


def _guardar_sondeo_ci(
    registro: Registro, enc: Encargo, doc: dict, hora: str
) -> None:
    datos = {**doc, "hora": hora}
    registro.anotar(
        enc.id,
        {"kind": "ci_sondeo", "data": datos},
        _ukey_de_hecho("ci-sondeo", enc.id, datos),
    )


def _leer_veredicto_ci(gh, enc: Encargo, sha: str) -> Leido | ErrorLectura:
    """El CI del PR como lectura tipada; un error nunca cuenta como verde."""
    try:
        return Leido(gh.checks(enc.entrega.repo, enc.pr_numero, sha, ()))
    except Exception as exc:  # noqa: BLE001  la causa se conserva
        return ErrorLectura(str(exc) or type(exc).__name__)


def _revisar_ci_del_pr(
    registro: Registro, enc: Encargo, gh, hora: str
) -> Encargo:
    """Lee el CI real del PR cuando terminó.

    Un pendiente o un verde no cambia nada. Un rojo avisa al propietario una
    vez; si el fallo es de una prueba del producto, además reabre la fase para
    corregir en la misma rama y el mismo PR. Un rojo de tubería se relanza una
    sola vez (`gh run rerun --failed`) y se lee el resultado de esa segunda
    corrida: si pasa, la falla era pasajera y no hay aviso; si sigue rojo, es
    permanente y el aviso lo dice en palabras llanas. Nunca hay más de un
    reintento. La lectura se espacia para no agotar la cuota de GitHub y deja
    su marca en un archivo, no en un evento por sondeo; si el CI no termina en
    90 minutos, queda el estado terminal «sin_resultado» y se deja de sondear.
    Un error de lectura no consume el resultado: se reintenta en el siguiente
    sondeo."""
    enc = registro.fold(enc.id) or enc
    fase = enc.fase_actual
    if (
        gh is None
        or enc.estado != "terminado"
        or enc.entrega.nivel not in ("pr", "integrado", "desplegado")
        or not enc.pr_numero
        or not enc.pr_sha
        or fase is None
    ):
        return enc
    sha = enc.pr_sha
    ukey = f"ci-pr:{enc.id}:{sha}"
    if registro.por_ukey(ukey) is not None:
        return enc
    clave_reintento = f"ci-pr-reintento:{enc.id}:{sha}"
    evento_reintento = registro.por_ukey(clave_reintento)
    reintentado = evento_reintento is not None
    sondeo = _sondeo_ci(enc)
    if str(sondeo.get("sha") or "") != sha:
        sondeo = {"sha": sha, "primero": hora, "ultimo": "", "sondeos": 0}
    ultimo = str(sondeo.get("ultimo") or "")
    transcurrido = _segundos_entre(ultimo, hora) if ultimo else None
    if transcurrido is not None and transcurrido < _CI_SONDEO_S:
        return enc
    primero = str(sondeo.get("primero") or hora)
    edad = _segundos_entre(primero, hora)
    al_limite = edad is not None and edad >= _CI_SONDEO_MAX_S
    if not al_limite:
        _guardar_sondeo_ci(
            registro,
            enc,
            {
                "sha": sha,
                "primero": primero,
                "ultimo": hora,
                "sondeos": int(sondeo.get("sondeos") or 0) + 1,
            },
            hora,
        )
    lectura_ci = _leer_veredicto_ci(gh, enc, sha)
    if isinstance(lectura_ci, ErrorLectura):
        exc = lectura_ci.causa
        if al_limite:
            hechos = int(sondeo.get("errores_limite") or 0)
            if hechos < 1:
                # Una lectura fallida justo al vencer puede esconder un
                # resultado ya listo: se concede una sola lectura más en el
                # siguiente barrido y ahí sí termina el sondeo (sin sondear
                # errores para siempre).
                _guardar_sondeo_ci(
                    registro,
                    enc,
                    {
                        "sha": sha,
                        "primero": primero,
                        "ultimo": hora,
                        "sondeos": int(sondeo.get("sondeos") or 0),
                        "errores_limite": hechos + 1,
                    },
                    hora,
                )
                registro.anotar(
                    enc.id,
                    {
                        "kind": "procedimiento",
                        "data": {
                            "nota": f"no se pudo leer el CI del PR todavía: {exc}",
                            "hora": hora,
                        },
                    },
                    f"ci-pr-error:{enc.id}:{sha}",
                )
                return registro.fold(enc.id) or enc
            # Al vencer el plazo con una segunda lectura fallida sí es
            # terminal: se declara sin resultado y se deja de sondear.
            return _anotar_ci_del_pr(
                registro,
                enc,
                ukey=ukey,
                fase=fase.id,
                sha=sha,
                estado="sin_resultado",
                causa=(
                    "el CI del PR no terminó en "
                    f"{_CI_SONDEO_MAX_S // 60} minutos y al vencer no se pudo "
                    f"leer: {exc}"
                ),
                hora=hora,
            )
        # Sin consumir `ukey`: el resultado todavía no se conoce y un fallo de
        # lectura no puede darlo por perdido.
        registro.anotar(
            enc.id,
            {
                "kind": "procedimiento",
                "data": {
                    "nota": f"no se pudo leer el CI del PR todavía: {exc}",
                    "hora": hora,
                },
            },
            f"ci-pr-error:{enc.id}:{sha}",
        )
        return registro.fold(enc.id) or enc
    veredicto_ci = lectura_ci.datos
    corridas = tuple(
        c
        for c in (getattr(veredicto_ci, "corridas", ()) or ())
        if isinstance(c, dict)
    )
    pendiente = any(
        str(c.get("status") or "") != "completed" for c in corridas
    ) or (not corridas and not getattr(veredicto_ci, "fallas", ()))
    if pendiente:
        if al_limite:
            return _anotar_ci_del_pr(
                registro,
                enc,
                ukey=ukey,
                fase=fase.id,
                sha=sha,
                estado="sin_resultado",
                causa=(
                    "el CI del PR no terminó en "
                    f"{_CI_SONDEO_MAX_S // 60} minutos; se deja de sondear"
                ),
                hora=hora,
            )
        return enc
    caidas = [
        c
        for c in corridas
        if str(c.get("conclusion") or "") not in _CONCLUSIONES_VERDES
    ]
    if not caidas and getattr(veredicto_ci, "fallas", ()):
        caidas = [
            {
                "nombre": str(nombre),
                "conclusion": "failure",
                "status": "completed",
            }
            for nombre in getattr(veredicto_ci, "fallas", ())
        ]
    if not caidas:
        return _anotar_ci_del_pr(
            registro,
            enc,
            ukey=ukey,
            fase=fase.id,
            sha=sha,
            estado="verde",
            hora=hora,
        )
    if reintentado:
        datos = (evento_reintento or {}).get("data") or {}
        previos = {str(x) for x in (datos.get("job_ids") or ()) if str(x)}
        hora_reintento = str(datos.get("hora") or "")

        def _lectura_anterior(c: dict) -> bool:
            job_id = str(c.get("job_id") or "")
            completed = str(c.get("completed_at") or "")
            if previos:
                mismo = bool(job_id) and job_id in previos
            else:
                mismo = True
            return mismo and (
                not completed or not hora_reintento or completed <= hora_reintento
            )

        if all(_lectura_anterior(c) for c in caidas):
            # El rerun aún no empezó: esto sigue siendo la corrida anterior.
            if al_limite:
                # Al vencer el plazo no se vuelve a sondear cada barrido: si el
                # reintento no arrancó a tiempo, el sondeo termina.
                return _anotar_ci_del_pr(
                    registro,
                    enc,
                    ukey=ukey,
                    fase=fase.id,
                    sha=sha,
                    estado="sin_resultado",
                    causa=(
                        "el reintento del CI no empezó en "
                        f"{_CI_SONDEO_MAX_S // 60} minutos; se deja de sondear"
                    ),
                    hora=hora,
                )
            return enc
    nombres = [
        f"{str(c.get('nombre') or 'job')} ({str(c.get('conclusion') or '')})"
        for c in caidas
    ]
    espacio = _espacio(registro.base, enc.id)
    detalles: dict[str, str] = {}
    texto = _logs_de_fallidos(
        gh, enc.entrega.repo, veredicto_ci, espacio, corridas=caidas,
        detalles=detalles,
    )
    clases = {
        str(c.get("nombre") or ""): _clasificar_fallo_ci(
            str(c.get("nombre") or ""),
            str(c.get("conclusion") or ""),
            detalles.get(str(c.get("nombre") or ""), ""),
        )
        for c in caidas
    }
    valores = set(clases.values())
    # El aviso lee la causa del log entero de cada job: `texto` va recortado y
    # el paso que falló (el «leaks found» de Gitleaks) puede quedar fuera.
    texto_aviso = (
        "\n".join(_texto_de_pasos_fallidos(t) for t in detalles.values() if t)
        or texto
    )
    sin_relanzamiento = False
    if valores == {"tuberia"} and not reintentado:
        run_ids = tuple(
            dict.fromkeys(
                str(c.get("run_id_corrida") or c.get("run_id") or "")
                for c in caidas
                if str(c.get("run_id_corrida") or c.get("run_id") or "")
            )
        )
        if run_ids:
            registro.anotar(
                enc.id,
                {
                    "kind": "ci_pr",
                    "data": {
                        "fase": fase.id,
                        "sha": sha,
                        "estado": "reintentando",
                        "fallas": nombres,
                        "causa": texto,
                        "reabrir": False,
                        "clase": "tuberia",
                        "reintento": "",
                        "aviso": "",
                        "job_ids": [
                            str(c.get("job_id") or "")
                            for c in caidas
                            if str(c.get("job_id") or "")
                        ],
                        "hora": hora,
                    },
                },
                clave_reintento,
            )
            enc = registro.fold(enc.id) or enc
            _guardar_sondeo_ci(
                registro,
                enc,
                {"sha": sha, "primero": hora, "ultimo": hora, "sondeos": 1},
                hora,
            )
            try:
                for run_id in run_ids:
                    gh.relanzar_fallidas(enc.entrega.repo, run_id)
            except Exception as exc:
                return _anotar_ci_del_pr(
                    registro,
                    enc,
                    ukey=ukey,
                    fase=fase.id,
                    sha=sha,
                    estado="rojo",
                    fallas=nombres,
                    causa=f"{texto}\nno se pudo relanzar el CI: {exc}",
                    clase="tuberia",
                    reintento="permanente",
                    aviso=_aviso_ci_en_llano("tuberia", caidas, texto_aviso),
                    hora=hora,
                )
            return enc
        # Sin run_ids no hay rerun posible: el rojo se anuncia sin un segundo
        # intento, y el aviso no puede decir problema fijo.
        sin_relanzamiento = True
    if "codigo" in valores:
        clase = "codigo"
        reabrir = enc.nivel_alcanzado not in ("integrado", "desplegado")
    elif valores == {"tuberia"}:
        clase, reabrir = "tuberia", False
    else:
        clase, reabrir = "incierto", False
    reintento = "permanente" if clase == "tuberia" else ""
    return _anotar_ci_del_pr(
        registro,
        enc,
        ukey=ukey,
        fase=fase.id,
        sha=sha,
        estado="rojo",
        fallas=nombres,
        causa=texto,
        reabrir=reabrir,
        clase=clase,
        reintento=reintento,
        aviso=_aviso_ci_en_llano(
            clase, caidas, texto_aviso, reabrir=reabrir,
            sin_relanzamiento=sin_relanzamiento,
        ),
        hora=hora,
    )


def _head_desviado(
    registro: Registro, enc: Encargo, hora: str,
    numero: int, sha_entregado: str, head_vigente: str,
) -> Encargo:
    """F6: el head vigente del PR ya no es el sha aprobado y entregado. Cerrar
    con el sha guardado daría por terminado un PR cuyo head es otro contenido
    (el head es lo que se mergea), y las rondas de un sha que ya no es head
    están muertas. Se anota la desviación una vez por head: la primera vez el
    trabajo vuelve al responsable nombrando ambos shas; la repetición entra en
    la escalera del freno hasta que alguien concilia el head."""
    causa_head = (
        f"el head vigente del PR #{numero} ({head_vigente[:12]}) ya no es el "
        f"sha aprobado y entregado ({sha_entregado[:12]}); concilia el head "
        "del PR"
    )
    clave_head = (
        f"entrega:{enc.id}:{enc.fase_activa}:head_vigente:{head_vigente}"
    )
    ya_avisado = registro.por_ukey(clave_head) is not None
    registro.anotar(
        enc.id,
        {
            "kind": "entrega_externa",
            "data": {
                "fase": enc.fase_activa,
                "intento": enc.intento,
                "paso": "head_vigente",
                "repo": enc.entrega.repo,
                "numero": numero,
                "sha": sha_entregado,
                "head": head_vigente,
                "hora": hora,
            },
        },
        clave_head,
    )
    enc = registro.fold(enc.id)
    if ya_avisado:
        # Salida propia de la conciliación del head: el primer aviso ya volvió
        # al responsable y el head sigue distinto. Queda en espera visible sin
        # gastar turnos ni revisores; el paso se reanuda cuando el head
        # concilie (otro head distinto vuelve a abrir el aviso).
        marcar_espera(
            registro, enc.id,
            causa_head + "; el paso se reanuda cuando el head del PR concilie",
            _reanuda_desde(hora, 60), hora,
        )
    else:
        _devolver(
            registro, enc, "investigar", causa_head,
            "conciliar el head vigente del PR con el sha aprobado y "
            "entregado; lo aprobado que no cambio se conserva",
            hora, f"head-{head_vigente[:12]}",
        )
    return registro.fold(enc.id)


def _observacion_corresponde(
    observacion, version: str, declaradas: list[dict]
) -> bool:
    """La observacion es de esta version y de estas comprobaciones declaradas."""
    if not isinstance(observacion, dict):
        return False
    if (observacion.get("version") or observacion.get("producto")) != version:
        return False
    return observacion.get("comprobaciones") == declaradas


def _observacion_de_la_version(
    registro: Registro,
    enc: Encargo,
    version: str,
    declaradas: list[dict],
    directorio: Path,
) -> dict | None:
    for ev in reversed(registro.eventos(enc.id)):
        if (
            ev["kind"] == "observacion_supervisor"
            and _observacion_corresponde(ev["data"], version, declaradas)
        ):
            return ev["data"]
    path = directorio / "observaciones.json"
    if not path.is_file():
        return None
    try:
        candidata = leer_json(path)
    except (OSError, ValueError):
        return None
    if _observacion_corresponde(candidata, version, declaradas):
        return candidata
    return None


_TOPE_COLA_DE_FALLO = 1200


def _cola_de_salida(texto: object) -> str:
    """El final de una salida, que es donde el fallo deja su causa."""
    texto = str(texto or "").strip()
    if len(texto) <= _TOPE_COLA_DE_FALLO:
        return texto
    return "[...]\n" + texto[-_TOPE_COLA_DE_FALLO:]


def _detalle_en_rojo(ejecucion: dict) -> str:
    """Objetivo, argv, rc y el final de stdout/stderr de una comprobacion en
    rojo, para que el responsable corrija exactamente lo que fallo."""
    objetivo = str(ejecucion.get("objetivo") or "comprobacion")
    argv = ejecucion.get("argv")
    comando = (
        " ".join(str(x) for x in argv)
        if isinstance(argv, list) and argv
        else "(sin argv)"
    )
    lineas = [f"{objetivo}: rc {ejecucion.get('rc')} (argv: {comando})"]
    for nombre in ("stdout", "stderr"):
        cola = _cola_de_salida(ejecucion.get(nombre))
        if cola:
            lineas.append(f"{nombre} (final):\n{cola}")
    return "\n".join(lineas)


def _huella_de_fallas(observacion: dict) -> str:
    """Identidad estable de las comprobaciones en rojo.

    Usa objetivo, argv y rc, no la salida: un `Ran 26 tests in 5.370s` distinto
    no es otra falla ni abre otra vuelta. Un cambio de comando o de rc si lo es.
    """
    firmas = [
        [str(e.get("objetivo") or "comprobacion"), e.get("argv"), e.get("rc")]
        for e in observacion.get("ejecuciones") or []
        if isinstance(e, dict)
        and not e.get("error_instrumento")
        and (e.get("rc") is None or str(e.get("rc")) != "0")
    ]
    return digest({"fallas": firmas})[:12]


def _fallas_de_observacion(
    observacion: dict,
) -> tuple[list[str], list[str]]:
    """Separa una comprobacion en rojo de un instrumento que no pudo observar."""
    rojas = []
    instrumentos = []
    error = str(observacion.get("error_instrumento") or "")
    if error:
        instrumentos.append(error)
    for ejecucion in observacion.get("ejecuciones") or []:
        if not isinstance(ejecucion, dict):
            continue
        objetivo = str(ejecucion.get("objetivo") or "comprobacion")
        error = str(ejecucion.get("error_instrumento") or "")
        if error:
            instrumentos.append(f"{objetivo}: {error}")
        elif ejecucion.get("rc") is None or str(ejecucion.get("rc")) != "0":
            rojas.append(_detalle_en_rojo(ejecucion))
    return rojas, instrumentos


def _id_de_comprobacion(ejecucion: dict) -> str:
    """La identidad estable de una comprobacion: su comando, no su prosa.

    El objetivo puede cambiar de redaccion sin que cambie la comprobacion. Si
    cambia el argv, es otro comando y la observacion no puede cerrar el bloqueo
    viejo: ese bloqueo vuelve al responsable.
    """
    argv = ejecucion.get("argv")
    comando = (
        " ".join(str(x) for x in argv)
        if isinstance(argv, list) and argv
        else ""
    )
    return id_hallazgo("supervisor", comando, "")


def _ids_observados_verdes(observacion: dict) -> set[str]:
    """Las comprobaciones que esta observacion ejecuto y paso en verde."""
    verdes: set[str] = set()
    for ejecucion in observacion.get("ejecuciones") or []:
        if not isinstance(ejecucion, dict) or ejecucion.get("error_instrumento"):
            continue
        rc = ejecucion.get("rc")
        if rc is None or str(rc) != "0":
            continue
        verdes.add(_id_de_comprobacion(ejecucion))
    return verdes


def _comandos_observados_verdes(observacion: dict) -> set[str]:
    """Los comandos que esta observacion ejecuto y paso en verde.

    Un bloqueo guardado con una identidad anterior (objetivo mas argv) no casa
    por id con la identidad actual; el mismo comando en verde lo cierra.
    """
    comandos: set[str] = set()
    for ejecucion in observacion.get("ejecuciones") or []:
        if not isinstance(ejecucion, dict) or ejecucion.get("error_instrumento"):
            continue
        rc = ejecucion.get("rc")
        if rc is None or str(rc) != "0":
            continue
        argv = ejecucion.get("argv")
        comando = (
            " ".join(str(x) for x in argv)
            if isinstance(argv, list) and argv
            else ""
        )
        comandos.add(normaliza_comando(comando))
    return comandos


def _hallazgos_en_rojo(observacion: dict) -> list[dict]:
    """Las comprobaciones en rojo como bloqueantes con identidad estable.

    El id usa el comando (argv), no la salida ni el rc: la misma comprobacion que
    vuelve a fallar es el mismo bloqueo, y una nueva entrega con el mismo id
    abierto pasa por la escalera de `_sin_avance` en vez de abrir otra vuelta
    identica.
    """
    salida = []
    for ejecucion in observacion.get("ejecuciones") or []:
        if not isinstance(ejecucion, dict) or ejecucion.get("error_instrumento"):
            continue
        rc = ejecucion.get("rc")
        if rc is None or str(rc) == "0":
            continue
        objetivo = str(ejecucion.get("objetivo") or "comprobacion")
        argv = ejecucion.get("argv")
        comando = (
            " ".join(str(x) for x in argv)
            if isinstance(argv, list) and argv
            else ""
        )
        salida.append(
            {
                "id": _id_de_comprobacion(ejecucion),
                "gravedad": "bloqueante",
                "criterio": "comportamiento",
                "titulo": f"comprobacion del supervisor en rojo: {objetivo}",
                "reproduccion": _detalle_en_rojo(ejecucion),
                "ancla": comando,
                "origen": "supervisor",
                "estado": "abierto",
            }
        )
    return salida


def _registrar_comprobaciones(
    registro: Registro,
    enc: Encargo,
    observacion: dict,
    version: str,
    hora: str,
) -> Encargo:
    """Abre y cierra los bloqueos de las comprobaciones de esta observacion."""
    fase = enc.fase_actual
    if fase is None:
        return enc
    hallazgos = _hallazgos_en_rojo(observacion)
    if not hallazgos and not any(
        h.origen == "supervisor" and h.estado == "abierto" for h in fase.hallazgos
    ):
        return enc
    registro.anotar(
        enc.id,
        {
            "kind": "comprobaciones_supervisor",
            "data": {
                "version": version,
                "hallazgos": hallazgos,
                # Solo lo observado y verde cierra un bloqueo: una comprobacion
                # retirada de la declaracion no prueba que se resolvio.
                "verdes": sorted(_ids_observados_verdes(observacion)),
                "verdes_comandos": sorted(
                    _comandos_observados_verdes(observacion)
                ),
                "hora": hora,
            },
        },
        f"comprobaciones-supervisor:{enc.id}:{enc.generacion}:"
        f"{_huella_de_fallas(observacion)}",
    )
    return registro.fold(enc.id) or enc


def _bloqueos_sin_observacion(enc: Encargo, observacion: dict) -> list:
    """Bloqueos del supervisor que esta observacion no vio ni en rojo ni en verde.

    Son los que quedan abiertos porque su comprobacion salio de la declaracion
    o cambio de comando: retirarla no prueba que se resolvio.
    """
    fase = enc.fase_actual
    if fase is None:
        return []
    rojos = {h["id"] for h in _hallazgos_en_rojo(observacion)}
    verdes = _ids_observados_verdes(observacion)
    comandos = _comandos_observados_verdes(observacion)
    return [
        h
        for h in fase.hallazgos
        if h.origen == "supervisor"
        and h.estado == "abierto"
        and h.id not in rojos
        and h.id not in verdes
        and normaliza_comando(h.ancla) not in comandos
    ]


def _espera_por_observacion(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    directorio: Path,
    hora: str,
) -> tuple[Encargo, Resultado] | None:
    """None si la version vigente ya tiene una observacion verde del comprobador.

    Con comprobaciones declaradas, commit y push esperan a que el comprobador
    registre su observacion sobre esta version: eso es una `Espera`. Si la
    observacion trae una comprobacion en rojo, la version vuelve al responsable
    y no se publica: eso es un `Devolver`, que abre su intento. Un `Devolver`
    no puede viajar como si fuera una espera, o el trabajo queda dormido hasta
    la quietud.
    """
    try:
        declaradas = leer_declaradas(interno_de(espacio))
    except ValueError as exc:
        _anotar_instrumento(
            registro, enc, "comprobacion", str(exc),
            "corregir .interno/comprobaciones.json sin tocar el producto",
            hora, f"obs-publicacion-{enc.generacion}", clase="comprobaciones.json",
        )
        if _instrumento_omitido(enc, "comprobaciones.json"):
            declaradas = None
        else:
            if not _impedimento_de_instrumento(
                registro, enc, "comprobaciones.json", str(exc),
                "que .interno/comprobaciones.json vuelva a validar", hora,
            ):
                _devolver(
                    registro, enc, "reparar_instrumento", str(exc),
                    "corregir .interno/comprobaciones.json sin tocar el producto",
                    hora, f"comprobaciones-{enc.generacion}",
                )
                return registro.fold(enc.id) or enc, Devolver("responsable", str(exc))
            observado = registro.fold(enc.id) or enc
            return observado, Espera(observado.reanuda_en, observado.motivo)
    if declaradas is None or enc.producto is None:
        if declaradas is None and enc.producto is not None:
            enc = registro.fold(enc.id) or enc
            abierto_sup = any(
                h.origen == "supervisor" and h.estado == "abierto"
                for h in (enc.fase_actual.hallazgos if enc.fase_actual else ())
            )
            if abierto_sup:
                # Sin una observacion en verde el bloqueo no se resuelve. Se
                # deja abierto y visible, y la fase se entrega con un pendiente
                # en vez de publicarse como si la comprobacion hubiera pasado.
                omitido = _instrumento_omitido(enc, "comprobaciones.json")
                texto = (
                    "las comprobaciones declaradas no se pudieron volver a "
                    "observar: el comprobador quedo omitido tras fallos iguales; "
                    "el bloqueo del supervisor sigue abierto y se entrega con "
                    "este pendiente"
                    if omitido
                    else "las comprobaciones declaradas desaparecieron sin una "
                    "observacion en verde sobre el producto vigente; el bloqueo "
                    "del supervisor sigue abierto y se entrega con este pendiente"
                )
                registro.anotar(
                    enc.id,
                    {
                        "kind": "pendiente_entrega",
                        "data": {"texto": texto, "hora": hora},
                    },
                    f"pendiente-comprobaciones-"
                    f"{'omitidas' if omitido else 'retiradas'}:"
                    f"{enc.id}:{enc.generacion}",
                )
                return registro.fold(enc.id) or enc, Avanzo()
        return None
    version = version_producto(espacio).firma
    esperadas = declaradas
    observacion = _observacion_de_la_version(
        registro, enc, version, esperadas, directorio
    )
    if observacion is None:
        iniciar_comprobacion(espacio, directorio)
        marcar_espera(
            registro,
            enc.id,
            "esperando la observacion del comprobador sobre la version "
            f"{version[:12]} antes de publicar",
            _reanuda_en(30),
            hora,
        )
        observado = registro.fold(enc.id) or enc
        return observado, Espera(observado.reanuda_en, observado.motivo)
    fallas, instrumentos = _fallas_de_observacion(observacion)
    if instrumentos:
        causa = "la observacion del comprobador no se pudo usar: " + "; ".join(
            instrumentos
        )
        _devolver(
            registro,
            enc,
            "reparar_instrumento",
            causa,
            "reparar el comando de comprobacion sin tocar el producto",
            hora,
            f"observacion-instrumento-{version[:12]}",
        )
        return registro.fold(enc.id) or enc, Devolver("responsable", causa)
    if not fallas:
        enc = _registrar_comprobaciones(registro, enc, observacion, version, hora)
        sin_observar = _bloqueos_sin_observacion(enc, observacion)
        if sin_observar:
            # Un bloqueo que esta observacion no vio (comprobacion retirada o con
            # otro comando) no se resuelve. Pasa por la escalera: diagnostico,
            # relevo y, si nada avanza, entrega con el pendiente escrito; nunca
            # se cierra en silencio ni se queda devolviendo el turno para siempre.
            causa = (
                "el bloqueo del supervisor no se observo en la comprobacion "
                "declarada: " + "; ".join(h.titulo for h in sin_observar)
                + ". Restituye esa comprobacion o justifica el cambio de "
                "comando; el bloqueo sigue abierto"
            )
            _sin_avance(
                registro, enc, causa,
                "redeclarar la comprobacion retirada o cambiada y observarla en "
                "verde",
                hora, f"observacion-sin-{_huella_de_fallas(observacion)}",
            )
            return registro.fold(enc.id) or enc, Devolver("responsable", causa)
        return None
    enc = _registrar_comprobaciones(registro, enc, observacion, version, hora)
    causa = "la comprobacion del supervisor esta en rojo: " + "\n".join(fallas)
    _sin_avance(
        registro,
        enc,
        causa,
        "se devolvio la comprobacion en rojo y la version no cambio",
        hora,
        f"observacion-roja-{version[:12]}-{_huella_de_fallas(observacion)}",
    )
    nuevo = registro.fold(enc.id) or enc
    if nuevo.pendientes_entrega and nuevo.pendientes_entrega != enc.pendientes_entrega:
        return nuevo, Avanzo()
    return nuevo, Devolver("responsable", causa)


def _head_antes_de_cerrar(
    registro: Registro, enc: Encargo, gh, hora: str
) -> Encargo | None:
    """El cierre por atajo no publica nada: concilia el head vigente del PR con
    el sha aprobado y entregado antes de cerrar. Un head abierto distinto no se
    da por terminado con el sha guardado."""
    if gh is None or not enc.pr_numero or not enc.pr_sha:
        return None
    try:
        vista = gh.ver(enc.entrega.repo, enc.pr_numero)
    except Exception as exc:
        causa = f"no se pudo leer el head del PR #{enc.pr_numero}: {exc}"
        _anotar_instrumento(
            registro, enc, "github", causa,
            "reintentar la lectura del head del PR antes de cerrar",
            hora, f"head-ver-{enc.generacion}",
        )
        marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
        return registro.fold(enc.id)
    head = str(getattr(vista, "head", "") or "").strip()
    if head and head != enc.pr_sha:
        return _head_desviado(registro, enc, hora, enc.pr_numero, enc.pr_sha, head)
    return None


def _entregar_con_pendientes(
    registro: Registro, enc: Encargo, gh, hora: str
) -> Encargo:
    """Entrega lo que hay sin esperar a una persona.

    Publica el PR (o actualiza el ya abierto) con los pendientes y las notas del
    motor escritos en el cuerpo, y bloquea la fase con causa y evidencia; el
    encargo sigue con la siguiente fase que tenga dependencias válidas, o
    termina como entrega parcial si no queda ninguna alcanzable. Si el nivel
    autorizado no tiene puerto externo o el PR no se puede publicar, igual
    bloquea: deja dicho en la evidencia qué faltó.
    """
    if enc.estado in ("terminado", "cancelado", "deteniendo", "detenido"):
        return enc
    if _devolver_correcciones(registro, enc, hora):
        return registro.fold(enc.id)
    espacio = _espacio(registro.base, enc.id)
    pendientes = enc.pendientes_entrega or (
        "el encargo se entrega sin que el revisor haya concluido",
    )
    causa = ""
    if (
        not (enc.producto and enc.producto.manifiesto)
        and any(p.startswith(_RELEVO_AGOTADO) for p in pendientes)
    ):
        _bloquear_fase(registro, enc, "no hay producto que entregar",
                      "no hay producto que entregar; la fase termina "
                      "bloqueada con el motivo: " + "; ".join(pendientes),
                      hora,
                      f"{enc.reaperturas}:sin-producto:{len(pendientes)}")
        enc = registro.fold(enc.id) or enc
        _marcar_incorporado(registro, enc, hora)
        if enc.estado == "terminado":
            _publicar_resultado(registro, enc, hora)
            _limpiar_encargo_cerrado(registro, enc, hora)
        return enc
    if not enc.pendientes_entrega:
        registro.anotar(
            enc.id,
            {
                "kind": "pendiente_entrega",
                "data": {"texto": pendientes[0], "hora": hora},
            },
            f"pendiente-entrega:{enc.id}:{enc.fase_activa}",
        )
        enc = registro.fold(enc.id) or enc
    publicable = enc.entrega.nivel != "terminado" and gh is not None
    if enc.entrega.nivel != "terminado" and gh is None:
        causa = (
            "nivel de entrega "
            + enc.entrega.nivel
            + " sin puerto GitHub configurado en este supervisor; el PR no se publicó"
        )
    numero = enc.pr_numero
    sha = enc.producto.sha_git if enc.producto is not None else ""
    if publicable:
        causa = _asegurar_repo(enc, espacio, registro, hora)
        if not causa:
            sha, causa = _commit_push(enc, espacio)
        if not causa:
            rama = _rama_fase(enc)
            base = (
                enc.entrega.base
                if nivel_objetivo(enc) in ("integrado", "desplegado")
                else _base_fase(enc)
            )
            if not numero:
                ref_base = _ref_existente(espacio, f"origin/{base}", base)
                conteo = _git(espacio, "rev-list", "--count", f"{ref_base}..{sha}") if ref_base else None
                if conteo is None or conteo.returncode != 0:
                    marcar_espera(registro, enc.id, "no se pudieron comprobar los commits para publicar el PR",
                                  _reanuda_en(120), hora)
                    return registro.fold(enc.id)
                if conteo.stdout.strip() == "0":
                    if any(p.startswith(_RELEVO_AGOTADO) for p in pendientes):
                        _bloquear_fase(
                            registro, enc,
                            "no hay commits que entregar en el PR",
                            "no hay commits que entregar en el PR; la fase "
                            "termina bloqueada con el motivo: "
                            + "; ".join(pendientes),
                            hora,
                            f"{enc.reaperturas}:sin-commits:{len(pendientes)}",
                        )
                        enc = registro.fold(enc.id) or enc
                        _marcar_incorporado(registro, enc, hora)
                        if enc.estado == "terminado":
                            _publicar_resultado(registro, enc, hora)
                            _limpiar_encargo_cerrado(registro, enc, hora)
                        return enc
                    _bloquear_fase(
                        registro, enc,
                        "no hay commits que entregar en el PR",
                        "no hay commits que entregar en el PR; la fase termina "
                        "bloqueada con el motivo: " + "; ".join(pendientes),
                        hora,
                        f"{enc.reaperturas}:sin-commits-sin-relevo:{len(pendientes)}",
                    )
                    enc = registro.fold(enc.id) or enc
                    _marcar_incorporado(registro, enc, hora)
                    if enc.estado == "terminado":
                        _publicar_resultado(registro, enc, hora)
                        _limpiar_encargo_cerrado(registro, enc, hora)
                    return enc
            cuerpo = _cuerpo_pr(
                replace(enc, pendientes_entrega=tuple(pendientes)),
                espacio,
                excepciones=_excepciones_del_registro(
                    registro, enc, enc.pr_sha or sha
                ),
            )
            try:
                if numero:
                    gh.actualizar_cuerpo(enc.entrega.repo, numero, cuerpo)
                    vista = gh.ver(enc.entrega.repo, numero)
                    sha = str(getattr(vista, "head", "") or sha)
                else:
                    pr = gh.abrir_pr(
                        enc.entrega.repo,
                        rama,
                        base,
                        f"{enc.clave}: {' '.join(enc.texto.split())[:72]}",
                        cuerpo,
                    )
                    numero = int(pr.numero)
                    sha = str(pr.head or sha)
                    if getattr(pr, "borrador", False):
                        gh.marcar_listo(enc.entrega.repo, numero)
            except Exception as exc:
                causa = f"no se pudo publicar el PR con pendientes: {exc}"
    if causa:
        registro.anotar(
            enc.id,
            {
                "kind": "nota_pr",
                "data": {
                    "texto": (
                        "no se pudo publicar el PR con pendientes: " + str(causa)[:400]
                    ),
                    "hora": hora,
                },
            },
            f"nota-pr-fallo:{enc.id}:{enc.fase_activa}",
        )
        enc = registro.fold(enc.id) or enc
    elif publicable and numero:
        registro.anotar(
            enc.id,
            {
                "kind": "entrega_externa",
                "data": {
                    "fase": enc.fase_activa,
                    "intento": enc.intento,
                    "paso": "pr_con_pendientes",
                    "repo": enc.entrega.repo,
                    "numero": numero,
                    "sha": sha,
                    "recibo": str(numero),
                    "hora": hora,
                },
            },
            f"entrega:{enc.id}:{enc.fase_activa}:pr_con_pendientes:{numero}:{sha}",
        )
        enc = registro.fold(enc.id) or enc
    evidencia = "la fase se entrega con pendientes abiertos: " + "; ".join(pendientes)
    if numero:
        evidencia += f"; el PR #{numero} queda publicado con ellos"
    if causa:
        evidencia += f". No se pudo publicar el PR: {str(causa)[:400]}"
    _bloquear_fase(registro, enc, pendientes[0], evidencia, hora,
                   f"{enc.reaperturas}:{len(pendientes)}:{sha}")
    enc = registro.fold(enc.id) or enc
    _marcar_incorporado(registro, enc, hora)
    if enc.estado == "terminado":
        _publicar_resultado(registro, enc, hora)
        _limpiar_encargo_cerrado(registro, enc, hora)
    return enc


def _bloquear_fase(
    registro: Registro, enc: Encargo, causa: str, evidencia: str, hora: str,
    serie: str,
) -> None:
    """Bloquea la fase activa con causa y evidencia.

    El evento `bloqueo_fase` no termina el encargo: el reductor avanza a la
    siguiente fase con dependencias válidas o cierra como entrega parcial. La
    serie hace idempotente el evento por encargo, fase, reaperturas y SHA.
    """
    registro.anotar(
        enc.id,
        {
            "kind": "bloqueo_fase",
            "data": {
                "causa": " ".join(str(causa).split())[:400],
                "evidencia": " ".join(str(evidencia).split())[:2000],
                "hora": hora,
            },
        },
        f"bloqueo-fase:{enc.id}:{enc.fase_activa}:{serie}",
    )


def _preguntas_pedidas_al_revisor(
    registro: Registro, enc: Encargo
) -> list:
    enc = registro.fold(enc.id) or enc
    pedidas = set(enc.preguntas_pedidas)
    return [s for s in enc.solicitudes if s.id in pedidas]


def _texto_del_revisor(juicio: JuicioValidado) -> str:
    evidencia = " ".join(
        str(juicio.evidencia or juicio.nota or "").split()
    ) or "el revisor no aportó evidencia adicional"
    if juicio.veredicto == "aprobado":
        return (
            "Autorizado por el revisor independiente con esta evidencia: "
            + evidencia
        )[:2000]
    return (
        "El revisor independiente no aprobó la operación: "
        + evidencia
        + ". El responsable sigue con la opción más razonable y no espera respuesta."
    )[:2000]


def _contestar_con_juicio_del_revisor(
    registro: Registro,
    enc: Encargo,
    espacio: Path,
    directorio: Path,
    juicio: JuicioValidado,
    hora: str,
) -> Encargo:
    """La pregunta vencida la decide el revisor con la evidencia de su juicio,
    no una respuesta genérica atribuida a él."""
    pendientes = _preguntas_pedidas_al_revisor(registro, enc)
    if not pendientes:
        return enc
    texto = _texto_del_revisor(juicio)
    for sol in pendientes:
        if sol.estado != "pregunta":
            continue
        try:
            salida = necesidades.responder(
                registro, enc.id, sol.id, texto, "revisor",
                f"revisor-pregunta:{sol.id}:{enc.generacion}", hora,
            )
        except TransicionIlegal:
            continue
        if not salida.get("aplicada"):
            continue
        registro.anotar(
            enc.id,
            {
                "kind": "nota_pr",
                "data": {
                    "texto": (
                        f"pregunta {sol.id} contestada por el revisor "
                        f"independiente con su evidencia: "
                        f"{sol.pregunta or sol.motivo}"
                    ),
                    "hora": hora,
                },
            },
            f"nota-pregunta:{enc.id}:{sol.id}",
        )
        enc = registro.fold(enc.id) or enc
        try:
            necesidades.continuar_respuestas(
                registro, enc, espacio, directorio, hora
            )
        except (OSError, ValueError, TransicionIlegal):
            pass
        enc = registro.fold(enc.id) or enc
    return enc


def _entregar_con_falla_permanente(
    registro: Registro, enc: Encargo, causa: str, hora: str, serie: str
) -> Encargo:
    """Una causa que no cambia esperando deja el aviso visible y el pendiente.

    No se reintenta en bucle: el barrido siguiente entrega el trabajo con el
    pendiente escrito (o bloquea la fase con la causa) sin esperar a una persona.
    """
    _anotar_instrumento(
        registro, enc, "github", causa,
        "dejar la llave ssh y gh disponibles en la máquina, o relevar",
        hora, f"{serie}-{enc.generacion}-permanente",
    )
    _anotar_impedimento(
        registro, enc, "entrega:publicar", causa,
        "que la máquina tenga llave ssh y gh autenticado para el repo",
        hora,
    )
    registro.anotar(
        enc.id,
        {
            "kind": "pendiente_entrega",
            "data": {
                "texto": (
                    "la entrega no se pudo publicar por una causa "
                    f"permanente de esta máquina: {causa}"
                )[:2000],
                "hora": hora,
            },
        },
        f"pendiente-entrega-publicar:{enc.id}:{enc.generacion}",
    )
    return registro.fold(enc.id) or enc


def _paso_entrega_paso(
    registro: Registro, enc: Encargo, gh, hora: str
) -> tuple[Encargo, Resultado]:
    """Entrega externa sin LLM: commit/push del supervisor, PR listo y
    espera de la revision de DeepSeek por SHA. Idempotente por paso+SHA."""

    from .politica_despliegue import adoptar as adoptar_politica_despliegue

    enc = adoptar_politica_despliegue(
        registro, enc, _espacio(registro.base, enc.id), hora
    )
    enc = _refrescar_cuerpo_excepciones(
        registro, enc, gh, _espacio(registro.base, enc.id), hora
    )
    if enc.nivel_alcanzado == "desplegado":
        from .despliegue import comprobar_recibo

        try:
            comprobar_recibo(enc)
        except ValueError as exc:
            _preguntar_despliegue(registro, enc, str(exc), hora)
            return registro.fold(enc.id) or enc, Avanzo()
    if _devolver_correcciones(registro, enc, hora):
        return registro.fold(enc.id) or enc, NecesitaAgente("responsable")
    if _entrega_devuelta_pendiente(registro, enc):
        return enc, Avanzo()
    if (
        nivel_cumplido(enc)
        and enc.estado != "terminado"
        and _revision_aprobada_vigente(enc)
        and not _deepseek_por_releer(enc)
        and not _sha_final_sin_leer(enc, gh)
        and not (gh is None and _requiere_veredicto(enc))
    ):
        desviado = _head_antes_de_cerrar(registro, enc, gh, hora)
        if desviado is not None:
            return desviado, Avanzo()
        return (
            _cerrar_con_veredicto(
                registro, enc, gh, _espacio(registro.base, enc.id),
                _directorio(registro.base, enc.id), hora,
            ),
            Avanzo(),
        )
    if enc.entrega.nivel == "desplegado" and enc.nivel_alcanzado == "integrado":
        enc = _paso_despliegue(registro, enc, _espacio(registro.base, enc.id), hora)
        if nivel_cumplido(enc):
            return (
                _cerrar_con_veredicto(
                    registro, enc, gh, _espacio(registro.base, enc.id),
                    _directorio(registro.base, enc.id), hora,
                ),
                Avanzo(),
            )
        return _necesita_agente(enc)
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
        return _con_espera(registro.fold(enc.id) or enc)
    espacio = _espacio(registro.base, enc.id)
    directorio = _directorio(registro.base, enc.id)
    if _git(espacio, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode == 0:
        causa = "hay una mezcla en curso con la base; resuélvela antes de entregar"
        _devolver(
            registro, enc, "integrar_main", causa,
            "resolver el conflicto, 'git add' y 'git commit'; después continuar la entrega",
            hora, f"mezcla-{enc.generacion}",
        )
        return registro.fold(enc.id) or enc, Devolver("responsable", causa)
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
            return registro.fold(enc.id) or enc, Devolver("responsable", causa)
        _fallo_de_repo(registro, enc, causa, hora, "repo-entrega")
        return registro.fold(enc.id) or enc, Avanzo()
    enc = registro.fold(enc.id) or enc
    stale = _producto_no_aprobado(enc, espacio)
    if stale is not None:
        return _reabrir_revision(registro, enc, stale, hora), Devolver(
            "revisor", "el producto cambió desde la revisión aprobada"
        )
    observado = _espera_por_observacion(registro, enc, espacio, directorio, hora)
    if observado is not None:
        return observado
    sha, causa = _commit_push(enc, espacio)
    if causa:
        punta = ""
        if enc.pr_numero:
            try:
                vista_push = gh.ver(enc.entrega.repo, enc.pr_numero)
                punta = str(getattr(vista_push, "head", "") or "").strip()
            except Exception:
                punta = ""
        if punta and sha and punta != sha:
            entregado = str(enc.pr_sha or "").strip()
            if punta and entregado and punta != entregado:
                # F6: la punta del remoto ya no es el sha entregado: cabeza
                # desviada de verdad, se concilia contra la punta vigente.
                # F10: un fallo de push con la punta en el sha entregado no es
                # desviación — el push propio no aterrizó todavía (el sha local
                # puede estar más adelantado): reintento genérico, no desvío.
                return (
                    _head_desviado(
                        registro, enc, hora, enc.pr_numero, entregado, punta
                    ),
                    Avanzo(),
                )
        if not _fallo_transitorio(causa) and fallo_permanente_de_entrega(causa):
            # La llave rechazada, el gh ausente y la credencial inválida no
            # mejoran esperando: quedan visibles y el encargo entrega lo que
            # haya con el pendiente escrito, sin bucle de horas. Una red caída
            # que menciona el remoto sigue siendo transitoria y va a Espera.
            return _entregar_con_falla_permanente(
                registro, enc, causa, hora, "push"
            ), Avanzo()
        corte = not _fallo_transitorio(causa) and (
            _entrega_devuelta_de_la_version(registro, enc)
            or _fallos_de_entrega_seguidos(enc, causa) + 1
            >= TOPE_FALLOS_ENTREGA
        )
        # El aviso del resumen lee la causa del ultimo instrumento_fallo. En el
        # corte reserva el espacio del aviso dentro del limite del resumen y
        # recorta solo la causa: asi el aviso siempre dice que el motor dejo de
        # reintentar. La serie distinta evita que el registro deduplique la
        # causa sin explicacion que ya quedo del primer fallo.
        _anotar_instrumento(
            registro, enc, "github",
            _causa_con_aviso(causa, _AVISO_ENTREGA_DEVUELTA) if corte else causa,
            "reintentar commit y push de la misma version aprobada",
            hora,
            f"push-{enc.generacion}-{_clase_de_fallo(causa)}"
            + ("-corte" if corte else ""),
        )
        if corte:
            registro.anotar(
                enc.id,
                {
                    "kind": "nota_pr",
                    "data": {
                        "texto": (
                            "el motor dejo de reintentar el commit o push de "
                            "esta version porque la misma falla de git se "
                            "repitio; el trabajo vuelve al responsable con la "
                            f"causa. {causa}"
                        ),
                        "hora": hora,
                    },
                },
                f"nota-reintento-entrega:{enc.id}:{enc.generacion}",
            )
            registro.anotar(
                enc.id,
                {
                    "kind": "entrega_devuelta",
                    "data": {
                        "version": enc.producto.firma if enc.producto else "",
                        "generacion": enc.generacion,
                        "clase": _clase_de_fallo(causa),
                        "causa": causa,
                        "hora": hora,
                    },
                },
                f"entrega-devuelta:{enc.id}:{enc.generacion}:{_clase_de_fallo(causa)}",
            )
            _devolver(
                registro, enc, "investigar", causa,
                "investigar la causa exacta de git del commit o push y corregir "
                "el espacio o el repositorio; el producto que no cambio conserva "
                "su aprobación",
                hora, f"push-{enc.generacion}",
            )
            return registro.fold(enc.id) or enc, Devolver("responsable", causa)
        marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
        return _con_espera(registro.fold(enc.id) or enc)
    # El push de la rama funcionó: la causa de transporte de la recepción ya
    # no está y su aviso se levanta. Si la publicación falla después, se anota
    # la causa de la publicación.
    _resolver_impedimentos_de_entrega(registro, enc, hora, ("entrega:ssh",))
    enc = registro.fold(enc.id) or enc
    rama = _rama_fase(enc)
    base = (
        enc.entrega.base if nivel_objetivo(enc) in ("integrado", "desplegado")
        else _base_fase(enc)
    )
    firma = enc.producto.firma if enc.producto is not None else ""
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
            f"entrega:{enc.id}:{enc.fase_activa}:{paso}:{sha}:{firma}",
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
            return _con_espera(registro.fold(enc.id) or enc)
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
                return _intentar_cierre_paso(
                    registro, enc, espacio, directorio, hora
                )
            return _necesita_agente(enc)
        if vista_pr.estado == "abierto":
            enc = _actualizar_cuerpo_pr(
                registro, enc, gh, numero, sha, espacio, hora
            )
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
                    return _intentar_cierre_paso(
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
                        return _intentar_cierre_paso(
                            registro, enc, espacio, directorio, hora
                        )
                    # desplegado sin diff: no se abre un PR vacío.
                    enc = _paso_despliegue(registro, enc, espacio, hora)
                    if nivel_cumplido(enc) and enc.estado != "terminado":
                        return _intentar_cierre_paso(
                            registro, enc, espacio, directorio, hora
                        )
                    return _necesita_agente(enc)
        try:
            pr = gh.abrir_pr(
                enc.entrega.repo,
                rama,
                base,
                f"{enc.clave}: {' '.join(enc.texto.split())[:72]}",
                _cuerpo_pr(
                    enc,
                    espacio,
                    excepciones=_excepciones_del_registro(
                        registro, enc, enc.pr_sha or sha
                    ),
                ),
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
            enc = _actualizar_cuerpo_pr(
                registro, enc, gh, numero, sha, espacio, hora
            )
        except Exception as exc:
            if "no commits between" in str(exc).lower():
                # La rama no tiene nada publicable sobre su base: no es un
                # fallo del agente ni un PR imposible. Se entrega como
                # `sin_cambios` (o se concilia integrado si ya está contenido).
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
                                    f"fase {enc.fase_activa} sin commits "
                                    f"publicables sobre {base}: {exc}"
                                ),
                                "hora": hora,
                            },
                        },
                        f"entrega:{enc.id}:{enc.fase_activa}:sin_cambios:{sha}",
                    )
                    return _intentar_cierre_paso(
                        registro, registro.fold(enc.id) or enc,
                        espacio, directorio, hora,
                    )
                ref_base = _ref_existente(espacio, f"origin/{base}", base)
                punta = (
                    _git(
                        espacio, "rev-parse", "--verify", "--quiet",
                        f"{ref_base}^{{commit}}",
                    ).stdout.strip()
                    if ref_base
                    else sha
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
                            "sha": punta or sha,
                            "recibo": punta or sha,
                            "evidencia": (
                                f"fase {enc.fase_activa} ya contenida en "
                                f"{base}: {exc}"
                            ),
                            "hora": hora,
                        },
                    },
                    f"entrega:{enc.id}:{enc.fase_activa}:integrado:{sha}",
                )
                enc = registro.fold(enc.id) or enc
                if nivel_cumplido(enc):
                    return _intentar_cierre_paso(
                        registro, enc, espacio, directorio, hora
                    )
                enc = _paso_despliegue(registro, enc, espacio, hora)
                if nivel_cumplido(enc) and enc.estado != "terminado":
                    return _intentar_cierre_paso(
                        registro, enc, espacio, directorio, hora
                    )
                return registro.fold(enc.id) or enc, Avanzo()
            causa = f"no se pudo abrir o preparar el PR: {exc}"
            if not _fallo_transitorio(causa) and fallo_permanente_de_entrega(causa):
                return _entregar_con_falla_permanente(
                    registro, enc, causa, hora, "pr"
                ), Avanzo()
            _anotar_instrumento(
                registro, enc, "github", causa,
                "reintentar la apertura del PR de la misma rama",
                hora, f"pr-{enc.generacion}",
            )
            marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
            return _con_espera(registro.fold(enc.id) or enc)
    # El PR se consultó o se abrió: la máquina sí alcanza el remoto y publica.
    _resolver_impedimentos_de_entrega(
        registro, enc, hora, ("entrega:github", "entrega:publicar")
    )
    enc = registro.fold(enc.id)
    inicio_vuelta = enc
    por_releer = _deepseek_por_releer(enc) and (
        enc.fase_actual is None
        or enc.fase_actual.deepseek_huella != HUELLA_DEEPSEEK_SIN_REVISION
    )
    if enc.deepseek_sha != sha or por_releer:
        eventos = registro.eventos(enc.id)
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
        lectura_ds = _leer_deepseek(
            gh, enc.entrega.repo, numero, sha, primera
        )
        if isinstance(lectura_ds, ErrorLectura):
            exc = lectura_ds.causa
            causa = f"no se pudieron leer los comentarios de DeepSeek: {exc}"
            _anotar_instrumento(
                registro, enc, "github", causa,
                "reintentar la lectura de DeepSeek para el mismo SHA",
                hora, f"deepseek-leer-{sha[:12]}",
            )
            if minutos >= TOPE_DEEPSEEK_MIN:
                texto = (
                    "sin revisión de DeepSeek para el SHA "
                    f"{sha[:12]} tras {int(minutos)} min (no se pudo leer: "
                    f"{str(exc)[:200]}); la fase sigue como si no hubiera "
                    "hallazgos"
                )
                enc = _omitir_deepseek(registro, enc, numero, sha, texto, hora)
                if enc.deepseek_huella != HUELLA_DEEPSEEK_SIN_REVISION:
                    return enc, Avanzo()
                return _paso_entrega_paso(registro, enc, gh, hora)
            marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
            return _con_espera(registro.fold(enc.id) or enc)
        if isinstance(lectura_ds, PendienteLectura):
            leidos: list[dict] = []
            comentarios: list[dict] = []
        else:
            datos_ds = lectura_ds.datos
            leidos, comentarios = datos_ds if isinstance(datos_ds, tuple) else ([], [])
        if leidos and not comentarios:
            # El sticky de ai-review declara que la revisión excedió su límite
            # y no se reintenta. No es una revisión aprobada ni una espera de
            # 30 min: se trata como ausencia en esta misma pasada.
            texto = (
                "sin revisión de DeepSeek para el SHA "
                f"{sha[:12]} tras {int(minutos)} min: el sticky de ai-review "
                "declara que la revisión excedió su tiempo límite y no se "
                "reintenta; la fase sigue como si no hubiera hallazgos"
            )
            enc = _omitir_deepseek(registro, enc, numero, sha, texto, hora)
            if enc.deepseek_huella != HUELLA_DEEPSEEK_SIN_REVISION:
                return enc, Avanzo()
            return _paso_entrega_paso(registro, enc, gh, hora)
        if not comentarios:
            disparado = any(
                ev["kind"] == "entrega_externa"
                and ev["data"].get("paso") == "deepseek_disparado"
                and ev["data"].get("sha") == sha
                for ev in eventos
            )
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
            if minutos >= TOPE_DEEPSEEK_MIN:
                texto = (
                    "sin revisión de DeepSeek para el SHA "
                    f"{sha[:12]} tras {int(minutos)} min; la fase sigue como "
                    "si no hubiera hallazgos"
                )
                enc = _omitir_deepseek(registro, enc, numero, sha, texto, hora)
                if enc.deepseek_huella != HUELLA_DEEPSEEK_SIN_REVISION:
                    return enc, Avanzo()
                return _paso_entrega_paso(registro, enc, gh, hora)
            marcar_espera(
                registro,
                enc.id,
                f"esperando revisión de DeepSeek para el SHA {sha}",
                _reanuda_desde(hora, 60),
                hora,
            )
            return _con_espera(registro.fold(enc.id) or enc)
        relectura_ds = enc.deepseek_sha == sha
        enc = _anotar_lectura_deepseek(registro, enc, numero, sha, comentarios, hora)
        enc = _refrescar_cuerpo_excepciones(
            registro, enc, gh, espacio, hora
        )
        abiertos_ds = bloqueantes_abiertos(enc, "deepseek")
        if abiertos_ds:
            pendientes_disputa = _disputas_pendientes(
                registro, enc, interno_de(espacio)
            )
            if pendientes_disputa:
                _devolver(
                    registro, enc, "decidir_disputa",
                    "el responsable sostiene con evidencia que no aplican: "
                    + "; ".join(
                        f"{h.id} {h.titulo}" for h, _ in pendientes_disputa
                    ),
                    "decidir con la evidencia si el hallazgo aplica; si no "
                    "aplica, descártalo y deja la razón en el PR",
                    hora, f"disputa-revisor-{sha[:12]}", rol="revisor",
                )
            elif relectura_ds or hubo_avance(inicio_vuelta, enc):
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
            return registro.fold(enc.id) or enc, Devolver(
                str(enc.rol or "responsable"), "DeepSeek mantiene el bloqueo"
            )
    enc = registro.fold(enc.id)
    if not _revision_aprobada_sin_bloqueos_ajenos(enc):
        if bloqueantes_abiertos(enc, "deepseek"):
            # Sigue siendo el turno de DeepSeek: no se adelanta Opus ni se
            # integra nada con un High o Medium abierto.
            return _necesita_agente(registro.fold(enc.id) or enc)
        # DeepSeek ya no tiene bloqueantes, pero la corrección cambió el
        # producto desde la última aprobación de Opus. Ninguna corrección se
        # integra ni se cierra sin que Opus vea el tramo final; si no cambió
        # nada, la aprobación vigente ya cubre el producto y este paso no aplica.
        _devolver(
            registro, enc, "revisar_evidencia",
            "DeepSeek quedó sin bloqueantes; falta la revisión final del tramo corregido",
            "revisar solo lo que cambió desde la última aprobación de Opus "
            "antes de integrar o cerrar",
            hora,
            f"revision-final-{enc.producto.firma if enc.producto else ''}",
            rol="revisor",
        )
        return registro.fold(enc.id) or enc, Devolver(
            "revisor", "revisión final del tramo corregido"
        )
    if (
        nivel_objetivo(enc) in ("integrado", "desplegado")
        and enc.nivel_alcanzado == "pr"
    ):
        # Aquí `deepseek_sha == pr_sha` por construcción: el bloque de lectura de
        # arriba espera o lee cuando DeepSeek no ha revisado el SHA, así que no
        # se integra uno sin leer.
        devuelto = _veredicto_antes_de_cerrar(registro, enc, gh, hora)
        if devuelto is not None:
            return devuelto, Avanzo()
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
            from motor.github import MergeRechazado, PoliticaBloquea

            causa = f"no se pudo integrar el PR #{enc.pr_numero}: {exc}"
            checks = None
            lectura_fallida = False
            if isinstance(exc, PoliticaBloquea):
                try:
                    checks = gh.checks_requeridos(
                        enc.entrega.repo, enc.pr_numero, enc.pr_sha
                    )
                except MergeRechazado as rechazo:
                    causa += f"; {rechazo}"
                except Exception as lectura:
                    lectura_fallida = True
                    causa += f"; no se pudieron leer los checks requeridos: {lectura}"
            if lectura_fallida:
                # No saber el estado de los checks no es un rechazo de la
                # política: se reintenta, sin preguntar a nadie.
                marcar_espera(registro, enc.id, causa, _reanuda_en(120), hora)
                return _con_espera(registro.fold(enc.id) or enc)
            if checks is not None and (
                checks.en_marcha
                and not checks.fallas
                and set(checks.faltan) <= set(checks.en_marcha)
            ):
                marcar_espera(
                    registro, enc.id,
                    f"esperando checks requeridos del PR #{enc.pr_numero}: "
                    + ", ".join(checks.en_marcha),
                    _reanuda_desde(hora, 120), hora,
                )
                return _con_espera(registro.fold(enc.id) or enc)
            if checks is not None and checks.fallas:
                # El CI rojo explica por qué no hubo merge: vuelve al responsable
                # con el log del check, no a una pregunta. La fase conserva la
                # aprobación; deja de entregarse mientras el SHA siga en rojo.
                rojos = ", ".join(checks.fallas)
                sha = str(enc.pr_sha or "")
                espacio = _espacio(registro.base, enc.id)
                log = _logs_de_fallidos(gh, enc.entrega.repo, checks, espacio)
                registro.anotar(
                    enc.id,
                    {
                        "kind": "entrega_bloqueada",
                        "data": {
                            "fase": enc.fase_activa,
                            "sha": sha,
                            "causa": "checks requeridos en rojo: " + rojos,
                            "hora": hora,
                        },
                    },
                    f"entrega-bloqueada:{enc.id}:{sha}:{rojos}",
                )
                _devolver(
                    registro,
                    enc,
                    "pon_en_verde",
                    f"pon en verde {rojos}",
                    (
                        "El merge del PR #"
                        f"{enc.pr_numero} no ocurrió porque los checks requeridos "
                        f"están en rojo: {rojos}. Corrige el producto y vuelve a "
                        "entregar; el CI no es una compuerta, solo explica por qué "
                        "no se integró."
                        + (f"\n{log}" if log else "")
                    )[:4000],
                    hora,
                    f"merge-ci-{sha[:12]}",
                    rol="responsable",
                )
                return registro.fold(enc.id) or enc, Devolver(
                    "responsable", f"pon en verde {rojos}"
                )
            # Un rechazo que no viene de un check (permisos, conflicto, punta
            # movida) es un impedimento visible con entrega con pendientes.
            _anotar_instrumento(
                registro, enc, "github", causa,
                "resolver la causa del merge rechazado sobre el SHA revisado",
                hora, f"merge-{enc.pr_sha[:12]}",
            )
            _anotar_impedimento(
                registro, enc, "merge", causa,
                "que el repositorio permita integrar el SHA revisado",
                hora,
            )
            registro.anotar(
                enc.id,
                {
                    "kind": "pendiente_entrega",
                    "data": {
                        "texto": "merge rechazado: " + causa[:400],
                        "hora": hora,
                    },
                },
                f"pendiente-merge:{enc.id}:{enc.pr_sha}",
            )
            return registro.fold(enc.id) or enc, Avanzo()
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
        return (
            _cerrar_con_veredicto(registro, enc, gh, espacio, directorio, hora),
            Avanzo(),
        )
    return _necesita_agente(enc)



def _paso_entrega(registro: Registro, enc: Encargo, gh, hora: str) -> Encargo:
    """Ejecuta el paso de entrega y conserva el resultado cerrado que devolvió."""
    enc, resultado = _paso_entrega_paso(registro, enc, gh, hora)
    _RESULTADOS_DE_PASO[enc.id] = resultado
    return enc


def _preguntar_despliegue(registro: Registro, enc: Encargo, causa: str, hora: str) -> None:
    """El despliegue que no puede completarse deja un impedimento visible y un
    pendiente de entrega; no pregunta ni espera a una persona."""
    huella = hashlib.sha256(causa.encode()).hexdigest()[:12]
    if enc.estado == "esperando_reintento":
        # El fallo reemplaza una espera de la receta que ya no corre: sin
        # reabrirla, el pendiente quedaría dormido hasta el plazo.
        registro.anotar(
            enc.id,
            {"kind": "plazo", "data": {"hora": hora}},
            f"plazo-despliegue:{enc.id}:{huella}",
        )
    _anotar_impedimento(
        registro, enc, "despliegue:" + huella, causa,
        "que el despliegue pueda completarse o se retire del pedido", hora,
    )
    registro.anotar(
        enc.id,
        {
            "kind": "pendiente_entrega",
            "data": {"texto": "despliegue: " + causa[:400], "hora": hora},
        },
        f"pendiente-despliegue:{enc.id}:{enc.fase_activa}:{huella}",
    )


def _espera_despliegue_sin_autoridad(
    registro: Registro, enc: Encargo, fresco: Encargo, hora: str
) -> None:
    """El despliegue que pierde la autoridad queda en espera explícita.

    Sin la espera el planificador volvería a lanzar un turno sobre el mismo
    paso (sin escrituras), y el recibo de una receta ya aplicada se perdería
    de vista. La espera se reanuda sola: cuando la autoridad vuelve,
    `_mirar_autoridad` rearma `escrituras` y el plazo vencido reabre el paso,
    que concilia el recibo que haya quedado. Una parada ya ordenada no se
    toca: la maneja su propio camino."""
    if (
        not fresco.intentos_nuevos
        or fresco.estado in ("deteniendo", "detenido", "cancelado", "terminado")
        or fresco.estado == "esperando_reintento"
    ):
        return
    marcar_espera(
        registro, enc.id,
        "la autoridad se perdió durante el paso de despliegue; el paso se "
        "reanuda cuando la autoridad vuelva y el recibo que haya quedado se "
        "concilia",
        _reanuda_desde(hora, 30), hora,
    )


def _paso_despliegue(
    registro: Registro, enc: Encargo, espacio: Path, hora: str
) -> Encargo:
    """El sello se obtiene de la receta ejecutada sobre el commit integrado."""
    from . import despliegue, politica_despliegue

    if nivel_objetivo(enc) != "desplegado" or enc.nivel_alcanzado != "integrado":
        return enc
    enc = politica_despliegue.adoptar(registro, enc, espacio, hora)
    if nivel_cumplido(enc):
        return enc
    # Conserva el diagnóstico de instrumentos inválidos, pero ningún pedido
    # del agente ni recibo de ensayo acredita una ejecución de producción.
    try:
        _pedidos_efecto(espacio)
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
    politica = politica_despliegue.resolver(espacio, enc)
    if politica.requiere_despliegue is None:
        resultado = {"estado": "esperando", "motivo":
                     "falta declarar si la fase despliega y su receta real"}
    else:
        resultado = despliegue.iniciar(registro, enc, espacio, politica, hora)
    fresco = registro.fold(enc.id) or enc
    if not fresco.escrituras or not fresco.intentos_nuevos:
        _espera_despliegue_sin_autoridad(registro, enc, fresco, hora)
        return registro.fold(enc.id) or fresco
    if resultado["estado"] != "aplicado":
        causa = resultado["motivo"]
        if resultado.get("recibo"):
            causa += "; recibo: " + resultado["recibo"]
        if resultado["estado"] in ("fallido", "incierto"):
            _preguntar_despliegue(registro, enc, causa, hora)
        else:
            marcar_espera(registro, enc.id, causa, _reanuda_desde(hora, 30), hora)
        return registro.fold(enc.id) or enc
    # Una parada durante la receta no se convierte en un cierre. El recibo
    # queda disponible para conciliar al continuar el encargo.
    fresco = registro.fold(enc.id) or enc
    if not fresco.escrituras or not fresco.intentos_nuevos:
        _espera_despliegue_sin_autoridad(registro, enc, fresco, hora)
        return registro.fold(enc.id) or fresco
    registro.anotar(
        enc.id,
        {"kind": "entrega_externa", "data": {
            "fase": enc.fase_activa, "intento": enc.intento, "paso": "desplegado",
            "repo": enc.entrega.repo, "numero": enc.pr_numero,
            "sha": enc.fase_actual.integrado_sha,
            "recibo": resultado["recibo"], "hora": hora,
        }},
        f"entrega:{enc.id}:{enc.fase_activa}:desplegado:{enc.fase_actual.integrado_sha}",
    )
    return registro.fold(enc.id) or enc


def _lanzar_uno(
    registro, enc: Encargo, directorio: Path, espacio: Path, hora: str, lanzador
) -> None:
    """Todos los agentes se lanzan directo: no hay distro ni jaula que consultar
    antes del turno. La sonda del entorno corre en el barrido, antes de consumirlo."""
    return _lanzar_uno_en_turno(
        registro, enc, directorio, espacio, hora, lanzador
    )


def _lanzar_uno_en_turno(
    registro, enc: Encargo, directorio: Path, espacio: Path, hora: str, lanzador
) -> None:
    from motor.runwrap import _publica

    with turno(registro.base):
        enc = registro.fold(enc.id) or enc
        if (
            enc.via == "directa"
            or enc.proc is not None
            or enc.reserva
            or impedimento_bloquea_trabajo(enc)
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
            _publica(copia, leer_texto(fuente))
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
                flota_doc = leer_json(flota_home_path)
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
            if cmd and gen_path is not None:
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
            from .rutas import fallo

            agente = agente_lanzado or enc.agente or rol
            causa = fallo(agente, str(exc), hora, 1, modelo=modelo_lanzado)
            registro.anotar(
                enc.id,
                {"kind": "proveedor_fallo", "data": {**causa, "hora": hora}},
                f"proveedor-fallo:{enc.id}:{hora}",
            )
            if not enc.agentes:
                marcar_espera(
                    registro,
                    enc.id,
                    "cuenta del agente: " + str(exc),
                    causa["hasta"],
                    hora,
                )
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


_RELEVO_AGOTADO = "El relevo agotó los modelos autorizados"


def marcar_relevo_agotado(
    registro: Registro, enc: Encargo, rol: str, hora: str
) -> Encargo:
    """Un relevo sin otro modelo autorizado no espera a una persona.

    Deja el pendiente en palabras simples y deja que la entrega normal publique
    el PR con lo que hay, o termine con el motivo si todavía no hay producto.
    """
    abiertos = bloqueantes_abiertos(enc)
    fresco = registro.fold(enc.id)
    if fresco is not None and len(fresco.ajustes) > len(enc.ajustes):
        # Una corrección llegó mientras se elegía el modelo. No se convierte
        # en entrega con pendientes: el próximo barrido se la da al responsable.
        return fresco
    texto = (
        f"{_RELEVO_AGOTADO} del rol {rol}. El motor entrega lo que hay con los "
        "pendientes abiertos, sin esperar a una persona."
    )
    if abiertos:
        texto += (
            " Quedan abiertos: "
            + "; ".join(f"{h.id} {h.titulo}" for h in abiertos)
            + "."
        )
    registro.anotar(
        enc.id,
        {"kind": "pendiente_entrega", "data": {"texto": texto, "hora": hora}},
        f"relevo-agotado:{enc.id}:{enc.fase_activa}:{rol}:{enc.generacion}",
    )
    return registro.fold(enc.id) or enc


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
    permisos: tuple[str, ...] = (),
    prohibiciones: tuple[str, ...] = (),
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
            "permisos": list(permisos),
            "prohibiciones": list(prohibiciones),
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


def _cierre_token(enc: Encargo) -> str:
    """Identidad del cierre vigente: un cierre nuevo tras reabrir el PR tiene
    otra evidencia y no puede heredar la marca del anterior."""
    ultima = enc.evidencia[-1] if enc.evidencia else ""
    return f"{len(enc.evidencia)}:{hashlib.sha256(ultima.encode()).hexdigest()[:12]}"


def _autoridad_cerrada_resuelta(registro: Registro, enc: Encargo) -> bool:
    """Un cerrado que ya entregó el resultado o la orden de su cierre vigente
    no se vuelve a consultar. La marca es por estado y cierre: una vieja sin
    estado, la de `detenido` o la de un cierre anterior no impide sellar."""
    token = _cierre_token(enc)
    evento = registro.por_ukey(
        f"autoridad-cerrada:{enc.id}:{enc.estado}:{token}"
    )
    return bool(
        evento
        and str(evento["data"].get("estado") or "") == enc.estado
        and str(evento["data"].get("cierre") or "") == token
    )


def _marcar_autoridad_cerrada(registro: Registro, enc: Encargo, hora: str) -> None:
    token = _cierre_token(enc)
    registro.anotar(
        enc.id,
        {
            "kind": "autoridad_cerrada",
            "data": {"hora": hora, "estado": enc.estado, "cierre": token},
        },
        f"autoridad-cerrada:{enc.id}:{enc.estado}:{token}",
    )


def _marcador_autoridad_final(registro: Registro, enc: Encargo):
    """La última marca de cierre de este encargo, con o sin token. Las marcas
    de orden de un detenido no cuentan: no sellan el cierre."""
    for evento in reversed(registro.eventos(enc.id)):
        if evento["kind"] != "autoridad_cerrada":
            continue
        datos = evento["data"]
        if datos.get("orden"):
            continue
        estado = str(datos.get("estado") or "")
        if estado and estado != enc.estado:
            continue
        return evento
    return None


def _nuevo_cierre_tras_marcador(
    registro: Registro, enc: Encargo, marcador: dict
) -> bool:
    """Hubo una reapertura o un cierre nuevo después de la última marca."""
    for evento in registro.eventos(enc.id):
        if evento["id"] <= marcador["id"]:
            continue
        if evento["kind"] in ("cierre_forzado", "resultado_directo"):
            return True
        if evento["kind"] == "ci_pr" and evento["data"].get("reabrir"):
            return True
        if evento["kind"] == "orden" and evento["data"].get("accion") == "reabrir":
            return True
    return False


def _marcar_autoridad_orden(
    registro: Registro, enc: Encargo, recibo: str, hora: str
) -> None:
    """La orden canónica de un detenido se manda una vez, pero su marca no
    sella el cierre: el encargo todavía puede continuar y terminar."""
    registro.anotar(
        enc.id,
        {"kind": "autoridad_cerrada", "data": {"hora": hora, "orden": recibo}},
        f"autoridad-orden:{enc.id}:{recibo}",
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
        orden = None
        for evento in reversed(registro.eventos(enc.id)):
            if evento["kind"] == "orden" and evento["data"]["accion"] in (
                "parar",
                "cancelar",
                "reabrir",
            ):
                orden = evento["data"]
                break
        if orden is not None:
            clave = orden["clave"] if orden["accion"] == "reabrir" else orden["recibo"] + "-canonico"
            if enc.estado == "cancelado":
                if _enviar_orden(registro, enc.id, orden["accion"], clave, datos=orden):
                    _marcar_autoridad_cerrada(registro, enc, hora)
            elif registro.por_ukey(
                f"autoridad-orden:{enc.id}:{orden['recibo']}"
            ) is None:
                if _enviar_orden(registro, enc.id, orden["accion"], clave, datos=orden):
                    _marcar_autoridad_orden(
                        registro, enc, str(orden["recibo"]), hora
                    )
        elif enc.estado == "cancelado":
            _marcar_autoridad_cerrada(registro, enc, hora)
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
    if enc.estado == "terminado":
        marcador = _marcador_autoridad_final(registro, enc)
        nuevo = marcador is not None and _nuevo_cierre_tras_marcador(
            registro, enc, marcador
        )
        if estado == "terminado" and not nuevo:
            _marcar_autoridad_cerrada(registro, enc, hora)
        elif _publicar_resultado(registro, enc, hora):
            _marcar_autoridad_cerrada(registro, enc, hora)
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
        and enc.generacion not in enc.incorporado
    ):
        try:
            ref = leer_json((directorio / "revision.json"))
            juicio = leer_json(Path(ref["resultado"]))
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
        referencia = leer_json((directorio / "revision.json"))
        observacion = leer_json(Path(referencia["observaciones"]))
        juicio = leer_json(Path(referencia["resultado"]))
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
        validado = validar_juicio(
            crudo, alcance, externos=bloqueantes_abiertos(enc, "deepseek")
        )
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
    hallazgos_validados = list(validado.hallazgos)
    abiertos_ds = {h.id: h for h in bloqueantes_abiertos(enc, "deepseek")}
    aplicados = False
    ya_resueltos = {h.id for h in hallazgos_validados}
    marca = validado.evidencia or "descartado por el revisor independiente"
    for hid in validado.resueltos:
        h = abiertos_ds.get(hid)
        if h is None or hid in ya_resueltos:
            continue
        hallazgos_validados.append(
            replace(h, estado="resuelto", cierre_evidencia=marca)
        )
        registro.anotar(
            enc.id,
            {
                "kind": "nota_pr",
                "data": {
                    "texto": (
                        f"hallazgo de DeepSeek descartado por el revisor "
                        f"independiente: {hid} «{h.titulo}» ({marca[:400]})"
                    ),
                    "hora": hora,
                },
            },
            f"disputa-resuelta-autoridad:{enc.id}:{hid}",
        )
        _anotar_excepcion(
            registro, enc, "descarte", enc.pr_sha, hid, marca, hora,
            f"excepcion:{enc.id}:descarte:{enc.pr_sha}:{hid}",
        )
        ya_resueltos.add(hid)
        aplicados = True
    for hid, razon in validado.fuera_de_alcance:
        h = abiertos_ds.get(hid)
        if h is None or hid in ya_resueltos:
            continue
        hallazgos_validados.append(
            replace(
                h,
                gravedad="observacion",
                estado="resuelto",
                cierre_evidencia=razon,
            )
        )
        registro.anotar(
            enc.id,
            {
                "kind": "hallazgo_fuera_de_alcance",
                "data": {"id": hid, "titulo": h.titulo, "razon": razon, "hora": hora},
            },
            f"fuera-alcance:{enc.id}:{fase}:{hid}",
        )
        _anotar_excepcion(
            registro, enc, "fuera_alcance", enc.pr_sha, hid, razon, hora,
            f"excepcion:{enc.id}:fuera:{enc.pr_sha}:{hid}",
        )
        ya_resueltos.add(hid)
        aplicados = True
    if aplicados:
        enc = registro.fold(enc.id) or enc
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
                "hallazgos": [h.como_dict() for h in hallazgos_validados],
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
