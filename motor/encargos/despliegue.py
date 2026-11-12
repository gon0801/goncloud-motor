"""Receta real sobre el SHA integrado, con un recibo durable fuera del producto.

El trabajador sobrevive al supervisor. Una corrida interrumpida sin recibo final
queda incierta: no se repite un efecto de producción a ciegas.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from motor.io import leer_json, leer_texto

from motor import plataforma
from motor.effects import _lock_despliegue, _posix, con_python_de_comando, entorno_maquina
from motor.encargos.maquina import de_base
from motor.encargos.frontera import _atomico, _autorizado_ahora, autoridad_ausente, turno
from motor.encargos.registro import Registro

TOPE_S = 900
# El contrato de la receta (docs/encargos-despliegue.md): 75 significa que el
# destino estaba ocupado (guardas, candados o un proceso vivo al recrear), no
# que el código esté mal. Solo cuenta como ocupado con la reversa sin cambios.
SALIDA_OCUPADO = 75
# La salida real acredita la ocupación por el contrato de la receta: la línea
# de guardas con algún valor de carga mayor que cero (todas menos alias), o la
# línea llana que nombra el proceso vivo o la ocupación sin negarla. Un fallo
# propio declarado en cualquier parte de la salida (desenlace completo) la
# invalida: el 75 queda fallido.
GUARDAS_RE = re.compile(
    r"alias\|ciclos running\|harvest en cola\|harvest jobs\|app\.cli\s*[=:]\s*"
    r"([^\s|]+(?:\|[^\s|]+)*)",
    re.IGNORECASE,
)
OCUPACION = re.compile(
    r"ocupad[oa]s?\b|\b\S+\s+(?:vivo|viva|live)\b", re.IGNORECASE,
)
NEGADA = re.compile(
    r"\bno\s+(?:\S+\s+)?(?:ocupad|vivo|viva|live)|\bsin\s+ocupar", re.IGNORECASE,
)
FALLO_PROPIO = re.compile(
    r"\b(?:error|fallo|fall[óo]|falla|fallida|fallido)\b", re.IGNORECASE,
)
# Con KILL_ON_JOB_CLOSE el Job de Windows tarda un instante en desaparecer; mientras
# tanto OpenJobObject puede responder acceso denegado. Eso no es un grupo vivo: se
# reintenta la observación hasta que el sistema termine de cerrarlo.
ESPERA_GRUPO_S = 5.0


def continuaciones(registro, encargo_id: str) -> int:
    """Órdenes de continuar aplicadas al encargo: cada una autoriza una
    corrida nueva del despliegue al que quedó vinculada."""
    try:
        eventos = registro.eventos(encargo_id)
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return sum(
        1 for ev in eventos
        if isinstance(ev, dict) and ev.get("kind") == "orden"
        and isinstance(ev.get("data"), dict) and ev["data"].get("accion") == "continuar"
    )


def _indice_orden(registro, encargo_id: str, consumidas: int) -> int | None:
    """Índice de la última orden de continuar posterior a las ya consumidas.

    La última, no la primera: una orden anterior a un `impedimento_resuelto`
    queda consumida, pero una orden nueva después de esa resolución vuelve a
    autorizar el reintento.
    """
    try:
        eventos = registro.eventos(encargo_id)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    vistas = 0
    ultima = None
    for indice, ev in enumerate(eventos):
        if (
            isinstance(ev, dict) and ev.get("kind") == "orden"
            and isinstance(ev.get("data"), dict)
            and ev["data"].get("accion") == "continuar"
        ):
            vistas += 1
            if vistas > consumidas:
                ultima = indice
    return ultima


def cubre(espacio: Path, antiguo: str, nuevo: str) -> bool:
    """El SHA integrado `antiguo` ya está contenido en el desplegado `nuevo`."""
    if not (re.fullmatch(r"[0-9a-f]{40,64}", antiguo or "")
            and re.fullmatch(r"[0-9a-f]{40,64}", nuevo or "")):
        return False
    corrido = subprocess.run(
        ["git", "-C", str(espacio), "merge-base", "--is-ancestor", antiguo, nuevo],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
    )
    return corrido.returncode == 0


_PREFIJO_CAUSA_MIN = 64


def _causa_corresponde(clase: str, causa: str, fase) -> bool:
    """La causa de un impedimento de despliegue es la del bloqueo de `fase`.

    La huella del bloqueo es la identidad exacta. La causa guardada al bloquear
    puede venir truncada, así que también vale un prefijo largo; un prefijo
    corto no atribuye dueño porque podría ser el comienzo de otra causa.
    """
    huella = str(clase).removeprefix("despliegue:")
    bloqueo = " ".join(str(getattr(fase, "bloqueo_causa", "") or "").split())
    desp = bloqueo.removeprefix("despliegue:").strip()
    causa = " ".join(str(causa or "").split())
    if hashlib.sha256(bloqueo.encode()).hexdigest()[:12] == huella:
        return True
    if not causa or not desp:
        return False
    if bloqueo == causa or desp == causa:
        return True
    return len(desp) >= _PREFIJO_CAUSA_MIN and causa.startswith(desp)


def clases_de_despliegue_de_fase(registro, encargo_id: str, fase) -> set[str]:
    """Clases de los impedimentos de despliegue que pertenecen a `fase`."""
    try:
        eventos = registro.eventos(encargo_id)
    except (OSError, ValueError, KeyError, TypeError):
        return set()
    clases: set[str] = set()
    for ev in eventos:
        if not isinstance(ev, dict) or ev.get("kind") != "impedimento":
            continue
        crudo = ev.get("data")
        datos = crudo if isinstance(crudo, dict) else {}
        clase = str(datos.get("clase") or "")
        if clase.startswith("despliegue:") and _causa_corresponde(
            clase, str(datos.get("causa") or ""), fase
        ):
            clases.add(clase)
    return clases


def orden_pendiente(
    registro, enc_arg, fase, consumidas: int, espacio: Path
) -> bool:
    """Hay una orden de continuar posterior a `consumidas` sin atender.

    Una orden queda consumida cuando después de ella se resolvió el
    impedimento de este despliegue o cuando un despliegue posterior ya contiene
    el SHA de la fase. Así una orden vieja no reabre un despliegue que otra
    operación ya atendió.
    """
    indice = _indice_orden(registro, enc_arg.id, consumidas)
    if indice is None:
        return False
    try:
        eventos = registro.eventos(enc_arg.id)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    clases = clases_de_despliegue_de_fase(registro, enc_arg.id, fase)
    for ev in eventos[indice + 1:]:
        if not isinstance(ev, dict):
            continue
        crudo = ev.get("data")
        datos = crudo if isinstance(crudo, dict) else {}
        if (
            ev.get("kind") == "impedimento_resuelto"
            and str(datos.get("clase") or "") in clases
        ):
            return False
        if ev.get("kind") == "entrega_externa" and datos.get("paso") == "desplegado":
            nuevo = str(datos.get("sha") or "")
            if nuevo and cubre(espacio, fase.integrado_sha, nuevo):
                return False
    return True


def politica_de_fase(fase):
    """La receta ya persistida de una fase, sin volver a resolver el plan."""
    from .politica_despliegue import PoliticaDespliegue

    return PoliticaDespliegue(
        fase.requiere_despliegue, fase.desplegar, fase.checklist, fase.rollback,
        fase.despliegue_fuente, fase.despliegue_huella,
    )


def _guardar(path: Path, datos: dict) -> None:
    _atomico(path, json.dumps(datos, ensure_ascii=False, sort_keys=True))


def _grupo_ausente(pid: int, inicio: str, job: str) -> bool:
    """Confirma la salida del grupo tras matarlo, tolerando el cierre transitorio del Job."""
    fin = time.monotonic() + ESPERA_GRUPO_S
    while True:
        if plataforma.presencia_grupo(pid, inicio, job) == "ausente":
            return True
        if time.monotonic() >= fin:
            return False
        time.sleep(0.05)


def _guarda_ocupada(linea: str) -> bool:
    """Una línea de guardas del contrato ocupa el destino cuando algún valor
    de carga (todas menos alias) es mayor que cero."""
    m = GUARDAS_RE.search(linea)
    if not m:
        return False
    return any(
        valor.strip().isdigit() and int(valor) > 0
        for valor in m.group(1).split("|")[1:]
    )


def _reporte_de_receta(paso: dict) -> str:
    """La línea con la que la receta declaró ocupado el destino.

    Lee el desenlace completo de la salida, no palabras sueltas: un fallo
    propio declarado en cualquier línea deja el 75 como fallido; un encabezado
    de guardas con todos los valores en cero no acredita ocupación; una
    negación ("no está vivo", "no ocupado") tampoco.
    """
    salida = str(paso.get("stdout", "")) + "\n" + str(paso.get("stderr", ""))
    if FALLO_PROPIO.search(salida):
        return ""
    lineas = [linea.strip()[:200] for linea in salida.splitlines() if linea.strip()]
    diagnosticos = [
        linea for linea in lineas
        if OCUPACION.search(linea) and not NEGADA.search(linea)
    ]
    if diagnosticos:
        return diagnosticos[-1]
    for linea in lineas:
        if _guarda_ocupada(linea):
            return linea
    return ""


def _registrar_ocupado(directorio: Path, hora: str, proceso: str) -> dict:
    """El libro de intentos ocupados sobrevive al reseteo del recibo en cada reintento."""
    try:
        previo = leer_json(directorio / "ocupado.json")
        desde = str(previo.get("desde") or hora)
        intentos = int(previo.get("intentos", 0))
        continuadas = int(previo.get("continuadas", 0))
    except (OSError, ValueError, KeyError, TypeError):
        desde, intentos = hora, 0
        # El libro arranca con la fotografía del recibo: una orden anterior
        # al despliegue ya no reabre la ventana al agotarse.
        try:
            continuadas = int(leer_json(directorio / "recibo.json").get("continuadas", 0))
        except (OSError, ValueError, KeyError, TypeError):
            continuadas = 0
    libro = {
        "intentos": intentos + 1,
        "desde": desde,
        "ultimo": hora,
        "proceso": proceso,
        "continuadas": continuadas,
    }
    _guardar(directorio / "ocupado.json", libro)
    return libro


def _directorio_de_fase(registro, enc, fase):
    """El directorio del recibo de una fase por su identidad, sin puntero."""
    if fase is None or not fase.integrado_sha:
        return None
    receta = {k: getattr(fase, k) for k in ("desplegar", "checklist", "rollback")}
    try:
        _rutas(receta)
        identidad = {
            "encargo": enc.id, "fase": fase.id, "repo": enc.entrega.repo,
            "sha": fase.integrado_sha, "receta": receta,
        }
        clave = hashlib.sha256(json.dumps(identidad, sort_keys=True).encode()).hexdigest()
        return registro.base / "despliegues" / clave
    except ValueError:
        return None


def ocupado(registro, enc, fase=None) -> dict:
    """El libro de intentos ocupados del despliegue de una fase; vacío si no
    hay. Es por identidad: el libro de otra fase del encargo no manda aquí."""
    directorio = _directorio_de_fase(registro, enc, fase or enc.fase_actual)
    if directorio is None:
        return {}
    try:
        libro = leer_json(directorio / "ocupado.json")
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if not isinstance(libro.get("intentos"), int) or libro["intentos"] < 1:
        return {}
    return libro


def _ruta_del_recibo(registro, enc, fase) -> tuple[Path, dict] | None:
    """La ruta del recibo del SHA integrado vigente de la fase y su identidad."""
    if not fase.integrado_sha:
        return None
    receta = {k: getattr(fase, k) for k in ("desplegar", "checklist", "rollback")}
    try:
        _rutas(receta)
    except ValueError:
        return None
    identidad = {
        "encargo": enc.id, "fase": fase.id, "repo": enc.entrega.repo,
        "sha": fase.integrado_sha, "receta": receta,
    }
    clave = hashlib.sha256(json.dumps(identidad, sort_keys=True).encode()).hexdigest()
    return registro.base / "despliegues" / clave / "recibo.json", identidad


def recibo_de(registro, enc, fase) -> dict | None:
    """El recibo vigente de una fase, con su estado y su conteo de continuadas."""
    ruta = _ruta_del_recibo(registro, enc, fase)
    if ruta is None:
        return None
    try:
        return _leer(*ruta)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def recibo_aplicado_de(registro, enc, fase) -> str | None:
    """La ruta del recibo aplicado del SHA integrado vigente, si lo hay.

    Mira el SHA que la fase tiene ahora, no el puntero `desplegado_recibo`: una
    fase reintegrada conserva el puntero de su despliegue anterior y no por eso
    está desplegada en el SHA nuevo.
    """
    ruta = _ruta_del_recibo(registro, enc, fase)
    if ruta is None:
        return None
    try:
        datos = _leer(*ruta)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return str(ruta[0]) if datos.get("estado") == "aplicado" else None


def reabrir_ventana(registro, enc, continuadas: int) -> None:
    """Una orden de continuar reabre la ventana del caso ocupado: el libro de
    la fase activa anota la orden consumida para que la ventana no se reabra
    sola."""
    directorio = _directorio_de_fase(registro, enc, enc.fase_actual)
    if directorio is None:
        return
    path = directorio / "ocupado.json"
    try:
        libro = leer_json(path)
        if not isinstance(libro, dict) or not isinstance(libro.get("intentos"), int):
            return
    except (OSError, ValueError, KeyError, TypeError):
        return
    libro["continuadas"] = continuadas
    _guardar(path, libro)


def _correr(reg, encargo: str, paso: str, script: str, copia: Path, env: dict, bash: str):
    argv, shell, exe = _posix("bash " + shlex.quote(script), bash)
    env = con_python_de_comando(env)
    vigente = lambda: _autorizado_ahora(reg, encargo, not autoridad_ausente(reg.base))
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        with turno(reg.base):
            if paso != "rollback" and not vigente():
                return subprocess.CompletedProcess(argv, 130, "", "parada aceptada antes de " + paso), False
            marca = copia.parent / "comando.json"
            _guardar(marca, {"estado": "preparando"})
            try:
                creado = plataforma.crear_proceso(
                    argv, plataforma.Propiedad.COMANDO_CONTENIDO, cwd=copia, env=env,
                    stdout=out, stderr=err, stdin=subprocess.DEVNULL, shell=shell, executable=exe,
                )
            except OSError as exc:
                _guardar(marca, {"estado": "no_enviado"})
                return subprocess.CompletedProcess(argv, 127, "", str(exc)), False
            _guardar(marca, {
                "pid": creado.proceso.pid, "inicio": plataforma.hora_inicio(creado.proceso.pid),
                "boot_id": plataforma.boot_id(), "job": creado.job,
            })
            creado.liberar()
        proceso = creado.proceso
        fin = time.monotonic() + TOPE_S
        codigo, motivo = None, ""
        while proceso.poll() is None:
            if paso != "rollback" and not vigente():
                codigo, motivo = 130, "parada aceptada durante " + paso
            elif time.monotonic() >= fin:
                codigo, motivo = 124, f"se pasó del tope de {TOPE_S} s"
            if codigo is not None:
                plataforma.matar_grupo(proceso)
                proceso.wait(timeout=5)
                break
            try:
                proceso.wait(timeout=min(1, max(0.01, fin - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        # Los hijos locales del script también tienen que terminar antes de
        # pasar al siguiente paso o afirmar que el trabajador se detuvo.
        ident = leer_json(marca)
        if os.name == "nt":
            plataforma.matar_grupo(proceso)
        else:
            plataforma.matar_grupo_pid(proceso.pid, ident["inicio"])
        if not _grupo_ausente(proceso.pid, ident["inicio"], ident["job"]):
            raise RuntimeError("el grupo del script todavía no confirma su salida")
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(
            argv, proceso.returncode if codigo is None else codigo,
            out.read().decode("utf-8", errors="replace"),
            err.read().decode("utf-8", errors="replace") + motivo,
        ), True


def _git(espacio: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(espacio), *args], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if p.returncode:
        raise ValueError("no se pudo preparar la copia del SHA integrado: " + p.stderr[-1200:])
    return p.stdout.strip()


def _maquina(base: Path):
    maquina = de_base(base)
    if "ssh-goncloud" not in maquina.tiene:
        raise ValueError(f"la máquina {maquina.nombre} no tiene la capacidad ssh-goncloud")
    return maquina


def _rutas(receta: dict) -> None:
    for paso in ("desplegar", "checklist", "rollback"):
        valor = receta.get(paso, "")
        ruta = Path(valor)
        if (not valor or ruta.is_absolute() or ".." in ruta.parts
                or "\\" in valor or ":" in valor or valor.startswith("-")):
            raise ValueError(f"falta receta real: {paso} debe ser una ruta relativa dentro del repo")


def _leer(path: Path, identidad: dict) -> dict:
    datos = leer_json(path)
    if any(datos.get(k) != v for k, v in identidad.items()):
        raise ValueError("el recibo de despliegue no corresponde a esta fase, SHA y receta")
    if datos.get("estado") == "aplicado":
        pasos = datos.get("pasos", [])
        if ([p.get("paso") for p in pasos] != ["desplegar", "checklist"]
                or any(p.get("codigo") != 0 for p in pasos)
                or any(p.get("script") != identidad["receta"][p["paso"]] for p in pasos)):
            raise ValueError("el recibo no demuestra despliegue y checklist con salida 0")
    return datos


def comprobar_recibo(enc) -> dict:
    fase = enc.fase_actual
    if fase is None or not fase.desplegado_recibo:
        raise ValueError("falta el recibo de ejecución real del despliegue")
    identidad = {"encargo": enc.id, "fase": fase.id, "repo": enc.entrega.repo,
                 "sha": fase.integrado_sha,
                 "receta": {k: getattr(fase, k) for k in ("desplegar", "checklist", "rollback")}}
    clave = hashlib.sha256(json.dumps(identidad, sort_keys=True).encode()).hexdigest()
    path = Path(fase.desplegado_recibo)
    if path.name != "recibo.json" or path.parent.name != clave or path.parent.parent.name != "despliegues":
        raise ValueError("un recibo de ensayo no acredita un despliegue real")
    try:
        datos = _leer(path, identidad)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"no se puede verificar el recibo de despliegue: {exc}") from exc
    if datos.get("estado") != "aplicado":
        raise ValueError("el recibo de despliegue no terminó con checklist aprobado")
    return datos


def observacion(registro, enc, fase=None) -> str:
    """Una parada espera también al trabajador de despliegue y su reversa.

    Con `fase`, mira el trabajador de esa fase y no el puntero del encargo: el
    reintento de una fase bloqueada corre fuera del turno de la activa.
    """
    if fase is not None:
        directorio = _directorio_de_fase(registro, enc, fase)
        if directorio is None:
            return "ausente"
        return _observacion_en(registro, enc, directorio)
    puntero = registro.base / "despliegues" / (enc.id + ".actual")
    if not puntero.exists():
        return "ausente"
    try:
        clave = leer_texto(puntero).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", clave):
            return "incierto"
        return _observacion_en(registro, enc, puntero.parent / clave)
    except (OSError, ValueError, KeyError, TypeError):
        return "incierto"


def _observacion_en(registro, enc, directorio: Path) -> str:
    try:
        datos = leer_json((directorio / "recibo.json"))
        proceso = leer_json((directorio / "proceso.json"))
        if datos.get("encargo") != enc.id:
            return "incierto"
        if plataforma.estado_proc(proceso) == "vivo":
            return "vivo"
        comando_path = directorio / "comando.json"
        if not comando_path.exists():
            return "ausente"
        comando = leer_json(comando_path)
        if comando.get("estado") == "no_enviado":
            return "ausente"
        if not comando.get("inicio") or not comando.get("pid"):
            return "incierto"
        if comando.get("boot_id") != plataforma.boot_id():
            return "ausente"
        presencia = plataforma.presencia_grupo(
            int(comando["pid"]), comando["inicio"], comando.get("job", ""),
        )
        if presencia == "vivo" and not _autorizado_ahora(registro, enc.id, not autoridad_ausente(registro.base)):
            plataforma.matar_grupo_pid(int(comando["pid"]), comando["inicio"])
        return presencia
    except (OSError, ValueError, KeyError, TypeError):
        return "incierto"


def iniciar(registro, enc, espacio: Path, politica, hora: str, fase=None) -> dict:
    """Inicia una vez o lee el recibo de la misma operación; no espera al script.

    Con `fase`, corre o concilia el despliegue de esa fase aunque no sea la
    activa: el reintento de una fase bloqueada no cambia el turno de la que
    está en curso.
    """
    fase = fase or enc.fase_actual
    sha = fase.integrado_sha
    receta = {k: getattr(politica, k) for k in ("desplegar", "checklist", "rollback")}
    try:
        _rutas(receta)
        if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
            raise ValueError("falta el SHA del merge para ejecutar la receta")
        _maquina(registro.base)
    except ValueError as exc:
        return {"estado": "esperando", "motivo": str(exc)}
    identidad = {"encargo": enc.id, "fase": fase.id,
                 "repo": enc.entrega.repo, "sha": sha, "receta": receta}
    clave = hashlib.sha256(json.dumps(identidad, sort_keys=True).encode()).hexdigest()
    directorio = registro.base / "despliegues" / clave
    recibo = directorio / "recibo.json"
    esperar_cierre = False
    continuar_hasta = 0
    with turno(registro.base):
        # Cada lanzamiento fotografia el conteo de órdenes de continuar: solo
        # una orden aplicada después de ese momento autoriza una corrida nueva.
        ordenes = continuaciones(registro, enc.id)
        continuar_hasta = ordenes
        if recibo.exists():
            try:
                datos = _leer(recibo, identidad)
                observado = observacion(registro, enc, fase)
                if datos["estado"] != "ejecutando" and observado == "vivo":
                    return {**datos, "estado": "ejecutando", "recibo": str(recibo),
                            "motivo": "ejecutando receta de despliegue: finalizando trabajador"}
                if datos["estado"] == "ejecutando":
                    proceso = leer_json((directorio / "proceso.json"))
                    if plataforma.estado_proc(proceso) != "vivo":
                        # El trabajador pudo terminar entre la lectura del recibo
                        # y la comprobación del proceso. Su última escritura es la
                        # verdad; la espera va fuera del turno para no bloquear al
                        # propio trabajador que tiene que publicarla.
                        esperar_cierre = True
                    else:
                        return {**datos, "recibo": str(recibo)}
                if not esperar_cierre:
                    pasos = datos.get("pasos", [])
                    consumidas = int(datos.get("continuadas") or 0)
                    pendiente = orden_pendiente(registro, enc, fase, consumidas, espacio)
                    fallido_reintentable = (
                        datos["estado"] == "fallido" and pendiente
                    )
                    # La fase activa puede esperar por otra causa: la receta de
                    # una fase bloqueada distinta corre igual, sin destrabar la
                    # espera ajena. Si la que espera es la propia fase, la espera
                    # sigue siendo el freno del relanzamiento.
                    estados_de_encargo = ("recibido", "ejecutando")
                    if fase.id != enc.fase_activa:
                        estados_de_encargo += ("esperando_reintento",)
                    reintentable = (
                        (datos["estado"] in ("esperando", "ocupado") or fallido_reintentable)
                        and enc.estado in estados_de_encargo
                        and (
                            all(p.get("enviado") is False for p in pasos)
                            if datos["estado"] == "esperando"
                            else all(p.get("enviado") for p in pasos)
                        )
                        and observado == "ausente"
                    )
                    if not reintentable:
                        return {**datos, "recibo": str(recibo)}
                    # La orden consumida queda fotografada en el recibo nuevo:
                    # una espera o un fallo repetido no reensayan solos.
                    continuar_hasta = ordenes
                    previo = directorio / ("preparacion-" + str(time.time_ns()) + ".json")
                    _guardar(previo, datos)
            except (OSError, ValueError, KeyError) as exc:
                return {"estado": "incierto", "motivo": f"recibo de despliegue no verificable: {exc}"}
        if not esperar_cierre:
            if not _autorizado_ahora(registro, enc.id, not autoridad_ausente(registro.base)):
                return {"estado": "esperando", "motivo": "despliegue sin autorización vigente"}
            directorio.mkdir(parents=True, exist_ok=True)
            datos = {**identidad, "estado": "ejecutando", "hora": hora, "pasos": [],
                     "motivo": "ejecutando receta de despliegue del SHA integrado",
                     "continuadas": continuar_hasta}
            _guardar(recibo, datos)
            # El puntero es del encargo, no de una fase: solo la activa lo mueve.
            if fase.id == enc.fase_activa:
                _atomico(directorio.parent / (enc.id + ".actual"), clave)
            _guardar(directorio / "pedido.json", {
                "base": str(registro.base.resolve()), "espacio": str(espacio.resolve()),
                "identidad": identidad,
            })
            try:
                proceso = plataforma.lanzar_separado(
                    [sys.executable, "-P", str(Path(__file__).resolve()), str(directorio / "pedido.json")],
                    directorio, os.environ.copy(), directorio / "trabajador.log",
                )
                _guardar(directorio / "proceso.json", proceso)
            except OSError as exc:
                datos.update(estado="incierto", motivo=f"no se pudo confirmar el arranque del despliegue: {exc}")
                _guardar(recibo, datos)
            return {**datos, "recibo": str(recibo)}
    # El trabajador pudo terminar entre la lectura del recibo y la comprobación
    # del proceso. Su última escritura es la verdad: espera fuera del turno a que
    # publique el estado final antes de declarar incierto un recibo en curso.
    for _ in range(100):
        time.sleep(0.05)
        try:
            datos = _leer(recibo, identidad)
        except (OSError, ValueError, KeyError):
            break
        if datos["estado"] != "ejecutando":
            break
    with turno(registro.base):
        try:
            datos = _leer(recibo, identidad)
        except (OSError, ValueError, KeyError) as exc:
            return {"estado": "incierto", "motivo": f"recibo de despliegue no verificable: {exc}"}
        if datos["estado"] == "ejecutando":
            datos.update(estado="incierto", motivo="la receta se interrumpió; conciliar producción y rollback antes de reintentar")
            _guardar(recibo, datos)
    return {**datos, "recibo": str(recibo)}


def _salida(texto: str, maquina) -> str:
    for _, secreto in (*maquina.secretos, *maquina.env):
        if secreto:
            texto = texto.replace(secreto, "[oculto]")
    # Credenciales de DSN y tokens habituales, incluso si vienen del servidor.
    texto = re.sub(r"(://[^\s:/]+:)[^\s@]+(@)", r"\1[oculto]\2", texto)
    texto = re.sub(r"(?i)((?:token|password|secret|api_key)\s*[=:]\s*)[^\s]+", r"\1[oculto]", texto)
    return texto


def ejecutar(pedido: Path) -> None:
    spec = leer_json(pedido)
    base, espacio = Path(spec["base"]), Path(spec["espacio"])
    identidad = spec["identidad"]
    recibo, copia = pedido.parent / "recibo.json", pedido.parent / "repo"
    datos = _leer(recibo, identidad)
    reg = Registro(base)
    try:
        maquina = _maquina(base)
        receta = identidad["receta"]
        # La copia contiene el merge, no los archivos mutables del trabajador.
        with _lock_despliegue(base, identidad["repo"]):
            if not _autorizado_ahora(reg, identidad["encargo"], not autoridad_ausente(base)):
                raise ValueError("parada aceptada antes del despliegue")
            sha = identidad["sha"]
            try:
                _git(espacio, "cat-file", "-e", sha + "^{commit}")
            except ValueError:
                _git(espacio, "fetch", "origin", sha)
            if copia.exists():
                _git(espacio, "worktree", "remove", "--force", str(copia))
            _git(espacio, "worktree", "add", "--detach", str(copia), sha)
            for nombre in receta.values():
                script = copia / nombre
                if not script.is_file() or script.is_symlink() or copia.resolve() not in script.resolve().parents:
                    raise ValueError(f"falta script real en el SHA integrado: {nombre}")
            env = entorno_maquina(maquina, {"MOTOR_SHA": sha, "MOTOR_RAIZ": str(copia)})

            def correr(paso):
                indice = len(datos["pasos"])
                datos["pasos"].append({"paso": paso, "script": receta[paso], "enviado": None})
                _guardar(recibo, datos)
                try:
                    p, enviado = _correr(reg, identidad["encargo"], paso, receta[paso], copia, env, maquina.bash)
                except Exception as exc:
                    # El recibo no deja un paso a medias: si el trabajador no
                    # pudo cerrar el paso, el código 125 lo dice y el recibo
                    # sigue siendo legible para conciliar.
                    datos["pasos"][indice] = {
                        "paso": paso, "script": receta[paso], "codigo": 125,
                        "stdout": "", "stderr": str(exc), "enviado": None,
                    }
                    _guardar(recibo, datos)
                    raise
                salida = {"paso": paso, "script": receta[paso], "codigo": p.returncode,
                          "stdout": _salida(p.stdout, maquina), "stderr": _salida(p.stderr, maquina),
                          "enviado": enviado}
                datos["pasos"][indice] = salida
                _guardar(recibo, datos)
                return salida["codigo"]

            codigo = correr("desplegar")
            autorizado = _autorizado_ahora(reg, identidad["encargo"], not autoridad_ausente(base))
            if codigo == 0 and autorizado:
                codigo = correr("checklist")
            if codigo != 0 or not autorizado:
                rollback = correr("rollback") if datos["pasos"][0]["enviado"] else "no requerido"
                desplegar = datos["pasos"][0]
                diagnostico = _reporte_de_receta(desplegar)
                if (desplegar.get("codigo") == SALIDA_OCUPADO and autorizado
                        and rollback == 0 and diagnostico):
                    # El paso desplegar declaró ocupado el destino en su propia
                    # salida y la reversa no hizo cambios: producción quedó
                    # intacta. Es transitorio; el ciclo reintenta el mismo SHA
                    # más tarde con espera creciente. Un 75 del checklist, o un
                    # 75 sin ese diagnóstico, no es ocupado: sigue fallido.
                    libro = _registrar_ocupado(
                        pedido.parent, str(datos.get("hora") or ""), diagnostico
                    )
                    datos.update(estado="ocupado", motivo=(
                        f"despliegue ocupado (salida {desplegar.get('codigo')}, "
                        f"intento {libro['intentos']}); "
                        f"la receta reporta: {libro['proceso']}; "
                        "rollback sin cambios (salida 0); se reintenta solo más tarde"
                    ))
                else:
                    datos.update(estado="fallido" if datos["pasos"][0]["enviado"] else "esperando", motivo=(
                        f"despliegue sin verificar (salida {codigo}); rollback salida {rollback}; "
                        "revisar el recibo antes de un nuevo despliegue"
                    ))
            else:
                datos.update(estado="aplicado", motivo="despliegue y checklist terminaron con salida 0")
                # El éxito de la identidad reinicia su ventana: el libro del
                # tope no sobrevive a un despliegue aplicado.
                try:
                    (pedido.parent / "ocupado.json").unlink()
                except OSError:
                    pass
            _guardar(recibo, datos)
    except Exception as exc:
        datos.update(estado="incierto" if datos["pasos"] else "esperando",
                     motivo=f"no se completó la receta de despliegue: {exc}")
        _guardar(recibo, datos)
    finally:
        reg.close()
        if copia.exists():
            try:
                _git(espacio, "worktree", "remove", "--force", str(copia))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass


if __name__ == "__main__":
    ejecutar(Path(sys.argv[1]))
