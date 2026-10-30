"""Puente de necesidades durante la sesión.

El trabajador escribe una solicitud en el buzón de su espacio y el supervisor,
en el mismo ciclo que ya observa el proceso, la valida contra el registro,
ejecuta la capacidad concedida y deja la respuesta en una ruta de lectura. La
identidad sale del host (`sesion.json`), nunca del texto del agente. Nada aquí
decide el trabajo: conserva hechos y devuelve datos o la causa de la espera.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from . import capacidades
from .dominio import Solicitud, TransicionIlegal
from .frontera import leer_parada_local
from .registro import Registro

BUZON = Path(".interno") / "buzon"
PETICIONES = "peticiones"
RESPUESTAS = "respuestas"
ENTREGAS = Path(".interno") / "entregas"
CLIENTE = "cliente.py"
HERRAMIENTAS = "herramientas"

MAX_MOTIVO = 2_000
MAX_PARAMETROS = 32 * 1024
MAX_RESPUESTA = 256 * 1024
TIMEOUT_S = float(os.environ.get("MOTOR_HERRAMIENTA_TIMEOUT") or 120)
LEASE_S = 30.0

_AFIRMATIVAS = (
    "si",
    "sí",
    "sí,",
    "si,",
    "yes",
    "ok",
    "autorizo",
    "autorizado",
    "adelante",
    "de acuerdo",
    "procede",
)


class SolicitudInvalida(ValueError):
    pass


def _publica(path: Path, doc: dict, raiz: Path | None = None) -> None:
    datos = json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")
    tmp = path.with_name(path.name + ".tmp")
    capacidades._escribir_seguro(tmp, datos, raiz=raiz)
    if raiz is not None:
        capacidades._padre_seguro(path, raiz)
    os.replace(tmp, path)


def _leer(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def preparar(
    espacio: Path, enc, conductor: str, recursos: dict[str, dict]
) -> None:
    """Deja el cliente, la identidad concedida y el catálogo público en el espacio."""
    buzon = Path(espacio) / BUZON
    for sub in (PETICIONES, RESPUESTAS):
        _sin_enlaces(espacio, buzon / sub)
    for sub in (PETICIONES, RESPUESTAS):
        (buzon / sub).mkdir(parents=True, exist_ok=True)
    _publica(
        buzon / "sesion.json",
        {
            "encargo": enc.id,
            "fase": enc.fase_activa,
            "intento": conductor,
            "generacion": enc.generacion,
            "conductor": conductor,
            "rol": enc.rol,
        },
        raiz=Path(espacio),
    )
    _publica(
        buzon / "catalogo.json",
        {
            "recursos": capacidades.catalogo(recursos),
            "operaciones_sin_recurso": [capacidades.DECISION],
            "nota": (
                "Solo estas operaciones y recursos están concedidos. "
                "Una capacidad ausente no está disponible."
            ),
        },
        raiz=Path(espacio),
    )
    capacidades._escribir_seguro(
        buzon / CLIENTE,
        Path(__file__).with_name("cliente_necesidad.py").read_bytes(),
        raiz=Path(espacio),
    )


def bloque_capacidades(espacio: Path, recursos: dict[str, dict]) -> str:
    """Texto de instrucciones con el cliente y el catálogo real del host."""
    cliente = Path(espacio) / BUZON / CLIENTE
    filas = capacidades.catalogo(recursos)
    if filas:
        lista = "\n".join(
            "- {recurso} ({tipo}): {operaciones}{aviso}".format(
                recurso=f["recurso"],
                tipo=f["tipo"],
                operaciones=", ".join(f["operaciones"]),
                aviso=(
                    " — requiere autorización del propietario"
                    if f["autorizacion"] == "propietario"
                    else ""
                ),
            )
            for f in filas
        )
    else:
        lista = "- El host no concedió recursos externos para este encargo."
    return f"""Puente de herramientas durante la sesión.
Cuando necesites un dato o un acceso que no tienes dentro del espacio, pídelo al motor con
`python3 {cliente} pedir`. El cliente escribe la solicitud y espera la respuesta del supervisor;
no inventes datos ni pidas al usuario que ejecute comandos.
Catálogo concedido:
{lista}
Operaciones:
- `sqlite.tablas --recurso <r>`: tablas y columnas del recurso.
- `sqlite.consultar --recurso <r> --parametro sql="SELECT ..."`: solo lectura, una sentencia.
- `sqlite.snapshot --recurso <r>`: copia consistente con origen y huella; la ruta queda en la respuesta.
- `remoto.leer --recurso <r> --parametro ruta="archivo/relativo"`: recurso remoto autorizado.
Añade `--motivo` con para qué la necesitas. Si la respuesta es `pregunta`, queda una autorización
pendiente con su id: sigue con lo demás o espera con `python3 {cliente} ver <id>`. Una respuesta
`error` trae la causa y la alternativa; corrige la solicitud en lugar de repetirla igual.
Registra cada decisión técnica que tomes con
`python3 {cliente} pedir --operacion decision.registrar --motivo "..." --parametro eleccion="..." --parametro motivo="..." --parametro evidencia="..."`.
La decisión no autoriza escrituras ni sustituye al pedido. Las herramientas locales que ya tienes
siguen disponibles; no cambies de agente ni de modo."""


def pedir_inspeccion(espacio: Path, enc, crudo: dict) -> None:
    """El motor deja en el buzón la inspección que declaró el revisor.

    Pasa por la misma validación de `_solicitud_de` que una petición del
    agente: el buzón es el único camino de ejecución.
    """
    sol = _solicitud_de(enc, crudo)
    buzon = Path(espacio) / BUZON
    _sin_enlaces(espacio, buzon / PETICIONES)
    (buzon / PETICIONES).mkdir(parents=True, exist_ok=True)
    _publica(buzon / PETICIONES / f"{sol.id}.json", crudo, raiz=Path(espacio))


def _parametros(crudo) -> tuple[tuple[str, str], ...]:
    if crudo is None:
        return ()
    if not isinstance(crudo, dict):
        raise SolicitudInvalida("parametros debe ser un objeto")
    salida = []
    total = 0
    for clave, valor in crudo.items():
        nombre = str(clave)
        if not nombre or len(nombre) > 64:
            raise SolicitudInvalida("nombre de parámetro inválido")
        if not isinstance(valor, (str, int, float)) or isinstance(valor, bool):
            raise SolicitudInvalida(f"el parámetro {nombre} debe ser texto")
        texto = str(valor)
        total += len(texto)
        salida.append((nombre, texto))
    if total > MAX_PARAMETROS:
        raise SolicitudInvalida(f"los parámetros superan {MAX_PARAMETROS} caracteres")
    return tuple(sorted(salida))


def _solicitud_de(enc, crudo: dict) -> Solicitud:
    ident = str(crudo.get("id") or "").strip()
    if not ident or len(ident) > 64 or not all(c.isalnum() or c in "-_" for c in ident):
        raise SolicitudInvalida("id de solicitud inválido")
    if str(crudo.get("encargo") or "") != enc.id:
        raise SolicitudInvalida("la solicitud no pertenece a este encargo")
    operacion = str(crudo.get("operacion") or "").strip()
    conocidas = (
        capacidades.TABLAS,
        capacidades.CONSULTAR,
        capacidades.SNAPSHOT,
        capacidades.REMOTO,
        capacidades.DECISION,
    )
    if operacion not in conocidas:
        raise SolicitudInvalida(
            "operación no concedida; disponibles: " + ", ".join(conocidas)
        )
    motivo = str(crudo.get("motivo") or "")
    if len(motivo) > MAX_MOTIVO:
        raise SolicitudInvalida(f"el motivo supera {MAX_MOTIVO} caracteres")
    generacion_cruda = crudo.get("generacion")
    if generacion_cruda is None:
        generacion_cruda = 0
    if isinstance(generacion_cruda, bool) or not isinstance(generacion_cruda, int):
        raise SolicitudInvalida("la generación de la solicitud no es un entero")
    return Solicitud(
        id=ident,
        operacion=operacion,
        recurso=str(crudo.get("recurso") or ""),
        fase=str(crudo.get("fase") or ""),
        intento=str(crudo.get("intento") or ""),
        generacion=generacion_cruda,
        conductor=str(crudo.get("conductor") or ""),
        motivo=motivo,
        parametros=_parametros(crudo.get("parametros")),
    )


def _mismo_pedido(a: Solicitud, b: Solicitud) -> bool:
    return (
        a.operacion == b.operacion
        and a.recurso == b.recurso
        and a.fase == b.fase
        and a.intento == b.intento
        and a.generacion == b.generacion
        and a.conductor == b.conductor
        and a.motivo == b.motivo
        and a.parametros == b.parametros
    )


def _buscar(enc, ident: str) -> Solicitud | None:
    return next((s for s in enc.solicitudes if s.id == ident), None)


def _afirmativa(texto: str) -> bool:
    limpio = " ".join(str(texto or "").lower().strip().split())
    if not limpio:
        return False
    return any(
        limpio == palabra or limpio.startswith(palabra + " ")
        for palabra in _AFIRMATIVAS
    )


def _vigente(enc, sol: Solicitud) -> bool:
    """La solicitud sigue correspondiendo al trabajo que se va a ejecutar."""
    if sol.fase and sol.fase != enc.fase_activa:
        return False
    if enc.intento and sol.intento and sol.intento != enc.intento:
        return False
    return True


def _autorizado(enc, recurso: str, operacion: str, fase: str, intento: str) -> bool:
    """Solo la operación explicada, en su fase e intento, queda autorizada."""
    return any(
        a[0] == recurso
        and a[1] == operacion
        and a[2] == fase
        and a[3] == intento
        for a in enc.autorizaciones
    )


def _respuesta(
    sol: Solicitud,
    estado: str,
    datos: dict | None = None,
    motivo: str = "",
    alternativa: str = "",
    pregunta: str = "",
    respuesta: str = "",
    espacio: Path | None = None,
) -> dict:
    doc: dict[str, object] = {
        "id": sol.id,
        "estado": estado,
        "operacion": sol.operacion,
        "recurso": sol.recurso,
        "fase": sol.fase,
        "intento": sol.intento,
        "motivo": motivo,
        "pregunta": pregunta,
        "respuesta": respuesta,
        "evidencia": sol.evidencia,
        "siguiente": "",
    }
    if datos is not None:
        doc["datos"] = datos
    if alternativa:
        doc["alternativa"] = alternativa
    texto = json.dumps(doc, ensure_ascii=False, default=str)
    if len(texto) > MAX_RESPUESTA:
        doc.pop("datos", None)
        doc["recortado"] = "la respuesta completa quedó en la evidencia"
        if datos is not None and espacio is not None:
            destino = Path(espacio) / ENTREGAS / sol.id / "datos.json"
            try:
                capacidades._escribir_seguro(
                    destino,
                    json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8"),
                    raiz=Path(espacio),
                )
                doc["recortado"] = f"la respuesta completa quedó en {destino}"
                doc["datos_archivo"] = str(destino)
            except (OSError, ValueError) as exc:
                doc["publicacion_fallo"] = str(exc)
    return doc


def _destino_host(directorio: Path, solicitud_id: str) -> Path:
    """Instrucciones y resultados confiables, fuera del espacio del trabajador."""
    return Path(directorio) / HERRAMIENTAS / solicitud_id


def _marca_herramienta(directorio: Path, solicitud_id: str) -> Path:
    return Path(directorio) / HERRAMIENTAS / f"{solicitud_id}.json"


def _respuesta_de_registro(
    espacio: Path, sol: Solicitud, directorio: Path | None = None
) -> dict:
    """Reconstruye la respuesta desde el resultado durable del host.

    El espacio del trabajador no es fuente de verdad: si el host no tiene el
    resultado, la respuesta no inventa datos.
    """
    resultado: dict | None = None
    if directorio is not None:
        resultado = _leer(_destino_host(directorio, sol.id) / "resultado.json")
    datos = (resultado or {}).get("datos")
    if isinstance(datos, dict) and sol.operacion == capacidades.SNAPSHOT:
        publico = Path(espacio) / ENTREGAS / sol.id / "snapshot.db"
        host = Path(str(datos.get("snapshot_host") or ""))
        if publico.is_file() and host.is_file() and _mismo_contenido(publico, host):
            datos = {**datos, "snapshot": str(publico)}
        elif publico.is_file():
            datos = {
                **datos,
                "snapshot": "",
                "snapshot_invalido": "la copia pública no coincide con el snapshot del host",
            }
    return _respuesta(
        sol,
        sol.estado,
        datos=datos,
        motivo=sol.detalle,
        pregunta=sol.pregunta,
        respuesta=sol.respuesta,
        espacio=espacio,
    )


def _resultado_huerfano(
    espacio: Path, sol: Solicitud, directorio: Path | None = None
) -> dict | None:
    """Resultado que el host escribió aunque el supervisor no lo anotara.

    Solo cuenta el archivo bajo control del host. Un archivo del espacio no
    acredita que la herramienta haya ejecutado la operación.
    """
    if directorio is None:
        return None
    return _leer(_destino_host(directorio, sol.id) / "resultado.json")


def _escribir_respuesta(
    espacio: Path, sol: Solicitud, doc: dict, preservar: bool = False
) -> None:
    carpeta = Path(espacio) / BUZON / RESPUESTAS
    destino = carpeta / f"{sol.id}.json"
    if preservar and destino.exists():
        destino = carpeta / f"{sol.id}.{doc.get('estado') or 'aviso'}-{int(time.time())}.json"
    _publica(destino, doc, raiz=Path(espacio))


def _archivar(path: Path) -> None:
    """La petición queda como recibo del pedido; el id no se reutiliza."""
    if capacidades._es_enlace(Path(path)):
        return
    try:
        path.replace(path.with_suffix(".hecha"))
    except OSError:
        path.unlink(missing_ok=True)


def _sin_enlaces(espacio: Path, ruta: Path) -> None:
    raiz = Path(os.path.abspath(espacio))
    actual = Path(os.path.abspath(ruta))
    if raiz not in actual.parents and actual != raiz:
        raise ValueError(f"la ruta {ruta} queda fuera del espacio")
    previo = actual
    while previo != raiz and raiz in previo.parents:
        if previo.exists() and capacidades._es_enlace(previo):
            raise ValueError(f"la ruta {ruta} pasa por un enlace simbólico")
        previo = previo.parent


def _anotar(
    registro: Registro, encargo_id: str, evento: dict, ukey: str
) -> str:
    return registro.anotar(encargo_id, evento, ukey)


def _anotar_resolucion(
    registro: Registro,
    enc,
    sol: Solicitud,
    estado: str,
    evidencia: str = "",
    pregunta: str = "",
    detalle: str = "",
    hora: str = "",
) -> str:
    previas = sum(1 for s in enc.solicitudes if s.id == sol.id)
    indice = 1
    for ev in registro.eventos(enc.id):
        if ev["kind"] == "resolucion" and ev["data"].get("id") == sol.id:
            indice += 1
    return _anotar(
        registro,
        enc.id,
        {
            "kind": "resolucion",
            "data": {
                "id": sol.id,
                "estado": estado,
                "fase": sol.fase,
                "intento": sol.intento,
                "evidencia": evidencia,
                "pregunta": pregunta,
                "detalle": detalle,
                "hora": hora,
            },
        },
        f"resolucion:{enc.id}:{sol.id}:{max(previas, 1)}:{indice}",
    )


def _detenida_ahora(registro: Registro, encargo_id: str) -> bool:
    if leer_parada_local(registro.base) is not None:
        return True
    fila = registro.db.execute(
        "SELECT data FROM events WHERE encargo = ? AND kind = 'orden'"
        " ORDER BY id DESC LIMIT 1",
        (encargo_id,),
    ).fetchone()
    if not fila:
        return False
    try:
        accion = json.loads(fila[0]).get("accion")
    except ValueError:
        return False
    return accion in ("parar", "cancelar")


def _herramienta_viva(marca: dict) -> bool:
    from motor import plataforma

    try:
        pid = int(marca.get("pid") or 0)
        inicio = str(marca.get("inicio") or "")
    except (TypeError, ValueError):
        return False
    if not pid or not inicio or not plataforma.pid_vivo(pid):
        return False
    return plataforma.hora_inicio(pid) == inicio


def _esperar_herramienta(
    registro: Registro, enc, destino: Path, marca: dict
) -> dict:
    """Espera a la herramienta que ya estaba en marcha; no lanza otra."""
    from motor import plataforma

    pid = int(marca.get("pid") or 0)
    inicio = str(marca.get("inicio") or "")
    fin = time.time() + TIMEOUT_S
    while time.time() < fin:
        resultado = _leer(destino / "resultado.json")
        if resultado is not None:
            return resultado
        if _detenida_ahora(registro, enc.id):
            plataforma.matar_proc(pid, inicio)
            return {
                "estado": "detenida",
                "motivo": "el encargo se detuvo mientras la herramienta anterior seguía viva",
                "alternativa": "el supervisor conserva lo ya escrito; no se repite solo",
            }
        if not _herramienta_viva(marca):
            break
        time.sleep(0.2)
    resultado = _leer(destino / "resultado.json")
    if resultado is not None:
        return resultado
    if _herramienta_viva(marca):
        return {
            "estado": "pendiente",
            "motivo": "la herramienta sigue en curso; el resultado se recogerá en el siguiente paso",
        }
    return {
        "estado": "error",
        "motivo": "la herramienta anterior no dejó un resultado legible",
        "alternativa": "revisar el marcador antes de repetir la operación",
    }


def _mismo_contenido(a: Path, b: Path) -> bool:
    """La copia pública solo vale si coincide con el archivo confiable del host."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return capacidades._huella(a) == capacidades._huella(b)
    except OSError:
        return False


def _copiar_seguro(origen: Path, destino: Path, raiz: Path) -> None:
    destino = capacidades._padre_seguro(destino, raiz)
    try:
        destino.unlink()
    except FileNotFoundError:
        pass
    banderas = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destino, banderas, 0o600)
    with os.fdopen(fd, "wb") as salida, open(origen, "rb") as entrada:
        while True:
            bloque = entrada.read(1024 * 1024)
            if not bloque:
                break
            salida.write(bloque)


def _publicar_snapshot(
    espacio: Path, sol: Solicitud, resultado: dict, directorio: Path
) -> dict:
    """Publica el snapshot completo y repara una copia parcial antes de entregar."""
    datos = resultado.get("datos")
    if not isinstance(datos, dict):
        return resultado
    host = str(datos.get("snapshot_host") or "")
    origen = Path(host) if host else Path(str(datos.get("snapshot") or ""))
    if not origen.is_file():
        return resultado
    if not host and origen.resolve().is_relative_to(Path(espacio).resolve()):
        return {
            "estado": "error",
            "motivo": "no hay una copia confiable del host para reparar el snapshot",
            "alternativa": "volver a solicitar el snapshot en un intento nuevo",
        }
    copia = Path(espacio) / ENTREGAS / sol.id / "snapshot.db"
    if copia.is_symlink():
        copia.unlink()
    if not copia.is_file() or not _mismo_contenido(copia, origen):
        temporal = copia.with_name(copia.name + ".tmp")
        try:
            _copiar_seguro(origen, temporal, espacio)
            os.replace(temporal, copia)
        except (OSError, ValueError) as exc:
            return {
                "estado": "pendiente",
                "motivo": f"no se pudo publicar el snapshot completo en el espacio: {exc}",
                "alternativa": "corregir el destino del entregable y reintentar",
            }
        if not _mismo_contenido(copia, origen):
            return {
                "estado": "pendiente",
                "motivo": "la copia publicada no coincide con el snapshot del host",
                "alternativa": "corregir el destino del entregable y reintentar",
            }
    publico = {**datos, "snapshot": str(copia), "snapshot_host": str(origen)}
    actualizado = {**resultado, "datos": publico}
    try:
        _publica(_destino_host(directorio, sol.id) / "resultado.json", actualizado)
    except OSError:
        pass
    return actualizado


def _completar_entrega(
    espacio: Path, sol: Solicitud, resultado: dict, directorio: Path
) -> dict:
    """Deja el snapshot en el espacio también cuando llega por recuperación."""
    if sol.operacion == capacidades.SNAPSHOT and resultado.get("estado") == "ok":
        return _publicar_snapshot(espacio, sol, resultado, directorio)
    return resultado


def _ejecutar(
    registro: Registro,
    enc,
    sol: Solicitud,
    recursos: dict[str, dict],
    espacio: Path,
    directorio: Path,
    autorizado: bool,
    hora: str,
) -> dict:
    from motor import plataforma
    from .frontera import turno

    destino = _destino_host(directorio, sol.id)
    destino.mkdir(parents=True, exist_ok=True)
    marca = _marca_herramienta(directorio, sol.id)
    spec_path = destino / "spec.json"
    argv = [
        sys.executable,
        "-P",
        str(Path(__file__).with_name("capacidades.py")),
        "--spec",
        str(spec_path),
    ]

    previa = _leer(marca)
    if previa and _herramienta_viva(previa):
        return _completar_entrega(
            espacio, sol, _esperar_herramienta(registro, enc, destino, previa), directorio
        )

    _publica(
        spec_path,
        {
            "operacion": sol.operacion,
            "recurso": sol.recurso,
            "recurso_config": recursos.get(sol.recurso, {}),
            "parametros": dict(sol.parametros),
            "destino": str(destino),
            "autorizado": autorizado,
        },
    )

    # El despacho se coordina con la parada sin retener la exclusión durante la espera.
    proc = None
    with turno(registro.base):
        if _detenida_ahora(registro, enc.id):
            return {
                "estado": "detenida",
                "motivo": "el encargo se detuvo antes de despachar",
                "alternativa": "el supervisor no lanzó la operación",
            }
        previa = _leer(marca)
        if previa and _herramienta_viva(previa):
            proc = None
        else:
            proc = subprocess.Popen(
                argv,
                cwd=str(destino),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            _publica(
                marca,
                {
                    "pid": proc.pid,
                    "inicio": plataforma.hora_inicio(proc.pid),
                    "solicitud": sol.id,
                    "hora": hora,
                },
            )
    if proc is None and previa is not None:
        return _completar_entrega(
            espacio, sol, _esperar_herramienta(registro, enc, destino, previa), directorio
        )
    if proc is None:
        return {
            "estado": "error",
            "motivo": "no se pudo despachar la herramienta",
            "alternativa": "revisar el marcador y reintentar",
        }

    fin = time.time() + TIMEOUT_S
    detenida = False
    try:
        while proc.poll() is None:
            if _detenida_ahora(registro, enc.id):
                detenida = True
                break
            if time.time() > fin:
                proc.kill()
                proc.wait(5)
                return {
                    "estado": "error",
                    "motivo": f"la operación excedió {int(TIMEOUT_S)}s",
                    "alternativa": "acotar la consulta o pedir un snapshot",
                }
            time.sleep(0.2)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)
        actual = _leer(marca)
        if actual and int(actual.get("pid") or 0) == proc.pid:
            marca.unlink(missing_ok=True)
    if detenida:
        return {
            "estado": "detenida",
            "motivo": "el encargo se detuvo durante la operación",
            "alternativa": "el supervisor conserva lo ya escrito; no se repite solo",
        }
    resultado = _leer(destino / "resultado.json") or {
        "estado": "error",
        "motivo": "la herramienta terminó sin resultado legible",
        "alternativa": "volver a pedir la operación con un id nuevo",
    }
    if sol.operacion == capacidades.SNAPSHOT and resultado.get("estado") == "ok":
        resultado = _publicar_snapshot(espacio, sol, resultado, directorio)
    return resultado


def _registrar_solicitud(registro: Registro, enc, sol: Solicitud, hora: str) -> str:
    return _anotar(
        registro,
        enc.id,
        {
            "kind": "solicitud",
            "data": {
                "id": sol.id,
                "operacion": sol.operacion,
                "recurso": sol.recurso,
                "fase": sol.fase,
                "intento": sol.intento,
                "generacion": sol.generacion,
                "conductor": sol.conductor,
                "motivo": sol.motivo,
                "parametros": [list(x) for x in sol.parametros],
                "hora": hora,
            },
        },
        f"solicitud:{enc.id}:{sol.id}",
    )


def _atender_decision(
    registro: Registro, enc, sol: Solicitud, espacio: Path, hora: str
) -> dict:
    parametros = dict(sol.parametros)
    eleccion = parametros.get("eleccion", "").strip()
    motivo = parametros.get("motivo", "").strip()
    if not eleccion or not motivo:
        return {
            "estado": "error",
            "motivo": "la decisión necesita eleccion y motivo",
            "alternativa": "volver a pedir decision.registrar con ambos campos",
        }
    resultado = _anotar(
        registro,
        enc.id,
        {
            "kind": "decision",
            "data": {
                "id": sol.id,
                "eleccion": eleccion,
                "motivo": motivo,
                "evidencia": parametros.get("evidencia", ""),
                "fases": [sol.fase] if sol.fase else [],
                "hora": hora,
            },
        },
        f"decision:{enc.id}:{sol.id}",
    )
    if resultado == "ajena":
        return {
            "estado": "conflicto",
            "motivo": "ya hay una decisión con ese id y otro contenido",
            "alternativa": "usar un id nuevo para una decisión distinta",
        }
    if resultado == "ilegal":
        return {"estado": "error", "motivo": "la decisión no se pudo registrar"}
    return {"estado": "registrada", "datos": {"decision": sol.id}}


def atender(
    registro: Registro, enc, espacio: Path, directorio: Path, hora: str
) -> dict:
    """Un paso del buzón: registra, resuelve y responde lo pendiente del intento."""
    buzon = Path(espacio) / BUZON
    _sin_enlaces(espacio, buzon / PETICIONES)
    _sin_enlaces(espacio, buzon / RESPUESTAS)
    pendientes = sorted(
        p for p in (buzon / PETICIONES).glob("*.json") if not capacidades._es_enlace(p)
    )
    resumen = {"atendidas": 0, "preguntas": 0, "errores": 0}
    if not pendientes:
        return resumen
    try:
        recursos = capacidades.cargar(registro.base)
    except capacidades.ConfigInvalida as exc:
        recursos = {}
        error_config = str(exc)
    else:
        error_config = ""
    for path in pendientes:
        crudo = _leer(path)
        if crudo is None:
            _publica(
                buzon / RESPUESTAS / f"{path.stem}.json",
                {"id": path.stem, "estado": "error", "motivo": "la solicitud no es JSON válido"},
                raiz=Path(espacio),
            )
            _archivar(path)
            resumen["errores"] += 1
            continue
        try:
            sol = _solicitud_de(enc, crudo)
        except (SolicitudInvalida, ValueError) as exc:
            _publica(
                buzon / RESPUESTAS / f"{path.stem}.json",
                {"id": path.stem, "estado": "error", "motivo": str(exc)},
                raiz=Path(espacio),
            )
            _archivar(path)
            resumen["errores"] += 1
            continue
        previa = _buscar(enc, sol.id)
        if previa is not None and not _mismo_pedido(previa, sol):
            _publica(
                buzon / RESPUESTAS / f"{sol.id}.conflicto-{int(time.time())}.json",
                {
                    "id": sol.id,
                    "estado": "conflicto",
                    "motivo": "esa identidad ya tiene otro contenido; no se ejecutó",
                },
                raiz=Path(espacio),
            )
            _archivar(path)
            resumen["errores"] += 1
            continue
        if previa is not None and previa.estado in (
            "resuelta",
            "error",
            "denegada",
            "detenida",
        ):
            reparacion = None
            if previa.estado == "resuelta" and previa.operacion == capacidades.SNAPSHOT:
                durable = _leer(
                    _destino_host(directorio, previa.id) / "resultado.json"
                )
                if durable and durable.get("estado") == "ok":
                    reparacion = _publicar_snapshot(
                        espacio, previa, durable, directorio
                    )
            if reparacion is not None and reparacion.get("estado") != "ok":
                _escribir_respuesta(
                    espacio,
                    previa,
                    _respuesta(
                        previa,
                        "pendiente",
                        motivo=str(
                            reparacion.get("motivo")
                            or "no se pudo completar la entrega del snapshot"
                        ),
                    ),
                )
                resumen["pendientes"] = resumen.get("pendientes", 0) + 1
                continue
            doc = _respuesta_de_registro(espacio, previa, directorio)
            if doc.get("publicacion_fallo"):
                _escribir_respuesta(
                    espacio,
                    previa,
                    _respuesta(
                        previa,
                        "pendiente",
                        motivo=(
                            "no se pudo publicar el archivo de datos en el espacio: "
                            f"{doc['publicacion_fallo']}"
                        ),
                        alternativa=(
                            "corregir el destino del entregable; la entrega se "
                            "reintentará sin repetir la consulta"
                        ),
                    ),
                )
                resumen["pendientes"] = resumen.get("pendientes", 0) + 1
                continue
            _escribir_respuesta(espacio, previa, doc)
            _archivar(path)
            continue
        if previa is not None and previa.estado == "pregunta" and not previa.respuesta:
            _escribir_respuesta(
                espacio,
                previa,
                _respuesta(
                    previa,
                    "pregunta",
                    pregunta=previa.pregunta,
                    motivo="hace falta una autorización del propietario",
                ),
            )
            resumen["preguntas"] += 1
            continue
        if error_config:
            _escribir_respuesta(
                espacio,
                sol,
                _respuesta(sol, "error", motivo=error_config, alternativa="corregir capacidades.json en el host"),
            )
            _archivar(path)
            resumen["errores"] += 1
            continue
        if _detenida_ahora(registro, enc.id):
            _anotar(
                registro,
                enc.id,
                {
                    "kind": "solicitud_tardia",
                    "data": {
                        "id": sol.id,
                        "fase": sol.fase,
                        "intento": sol.intento,
                        "motivo": "el encargo se detuvo antes de despachar",
                        "hora": hora,
                    },
                },
                f"detenida:{enc.id}:{sol.id}",
            )
            _escribir_respuesta(
                espacio,
                sol,
                _respuesta(sol, "detenida", motivo="el encargo se detuvo antes de despachar"),
            )
            _archivar(path)
            resumen["errores"] += 1
            continue
        if sol.operacion == capacidades.DECISION:
            if sol.fase != enc.fase_activa or (enc.intento and sol.intento != enc.intento):
                _escribir_respuesta(
                    espacio,
                    sol,
                    _respuesta(
                        sol,
                        "tardia",
                        motivo="la decisión llegó fuera de su fase o intento; no se registró",
                    ),
                )
                _archivar(path)
                resumen["errores"] += 1
                continue
            resultado = _atender_decision(registro, enc, sol, espacio, hora)
            estado = resultado["estado"]
            _escribir_respuesta(
                espacio,
                sol,
                _respuesta(
                    sol,
                    "registrada" if estado == "registrada" else estado,
                    datos=resultado.get("datos"),
                    motivo=resultado.get("motivo", ""),
                    alternativa=resultado.get("alternativa", ""),
                    espacio=espacio,
                ),
                preservar=estado != "registrada",
            )
            _archivar(path)
            resumen["atendidas" if estado == "registrada" else "errores"] += 1
            continue
        autorizado = previa is not None and previa.estado == "respuesta" and _afirmativa(previa.respuesta)
        if previa is not None and previa.estado == "respuesta" and not autorizado:
            _anotar_resolucion(
                registro,
                enc,
                previa,
                "denegada",
                detalle=f"el propietario respondió: {previa.respuesta}",
                hora=hora,
            )
            actual = registro.fold(enc.id) or enc
            nuevo = _buscar(actual, sol.id) or sol
            _escribir_respuesta(
                espacio,
                nuevo,
                _respuesta(
                    nuevo,
                    "denegada",
                    motivo="la autorización no fue concedida",
                    respuesta=previa.respuesta,
                ),
            )
            _archivar(path)
            resumen["errores"] += 1
            continue
        if previa is None:
            resultado = _registrar_solicitud(registro, enc, sol, hora)
            if resultado == "ajena":
                _escribir_respuesta(
                    espacio,
                    sol,
                    _respuesta(sol, "conflicto", motivo="esa identidad ya tiene otro contenido"),
                )
                _archivar(path)
                resumen["errores"] += 1
                continue
            if resultado == "ilegal":
                _anotar(
                    registro,
                    enc.id,
                    {
                        "kind": "solicitud_tardia",
                        "data": {
                            "id": sol.id,
                            "fase": sol.fase,
                            "intento": sol.intento,
                            "motivo": "no pertenece a la fase o al intento vigente",
                            "hora": hora,
                        },
                    },
                    f"tardia:{enc.id}:{sol.id}",
                )
                _escribir_respuesta(
                    espacio,
                    sol,
                    _respuesta(
                        sol,
                        "tardia",
                        motivo="la solicitud llegó fuera de su fase o intento; no se ejecutó",
                        alternativa="pedirla de nuevo en el intento vigente si sigue haciendo falta",
                    ),
                )
                _archivar(path)
                resumen["errores"] += 1
                continue
            if resultado == "igual":
                try:
                    edad = time.time() - path.stat().st_mtime
                except OSError:
                    continue
                if edad < LEASE_S:
                    continue
            enc = registro.fold(enc.id) or enc
        if not _vigente(enc, sol):
            _escribir_respuesta(
                espacio,
                sol,
                _respuesta(
                    sol,
                    "tardia",
                    motivo="la solicitud ya no corresponde a la fase o al intento vigente",
                    alternativa="pedirla de nuevo en el intento vigente si sigue haciendo falta",
                ),
            )
            _archivar(path)
            resumen["errores"] += 1
            continue
        autorizado = autorizado or _autorizado(
            enc, sol.recurso, sol.operacion, sol.fase, sol.intento
        )
        previo = (
            _resultado_huerfano(espacio, sol, directorio) if previa is not None else None
        )
        if previo is not None and str(previo.get("estado") or "") != "pregunta":
            resultado = previo
            if sol.operacion == capacidades.SNAPSHOT and resultado.get("estado") == "ok":
                resultado = _publicar_snapshot(espacio, sol, resultado, directorio)
            _anotar(
                registro,
                enc.id,
                {
                    "kind": "procedimiento",
                    "data": {
                        "nota": f"la respuesta de {sol.id} se recuperó del resultado ya escrito; no se repitió",
                        "hora": hora,
                    },
                },
                f"recuperado:{enc.id}:{sol.id}",
            )
        else:
            resultado = _ejecutar(
                registro, enc, sol, recursos, espacio, directorio, autorizado, hora
            )
        estado = str(resultado.get("estado") or "error")
        if estado == "pendiente":
            _escribir_respuesta(
                espacio,
                sol,
                _respuesta(
                    sol,
                    "pendiente",
                    motivo=str(resultado.get("motivo") or "la herramienta sigue en curso"),
                ),
            )
            resumen["pendientes"] = resumen.get("pendientes", 0) + 1
            continue
        if estado == "pregunta":
            _anotar_resolucion(
                registro, enc, sol, "pregunta", pregunta=str(resultado.get("pregunta") or ""), hora=hora
            )
            actual = registro.fold(enc.id) or enc
            nuevo = _buscar(actual, sol.id) or sol
            _escribir_respuesta(
                espacio,
                nuevo,
                _respuesta(
                    nuevo,
                    "pregunta",
                    pregunta=nuevo.pregunta,
                    motivo="falta una autorización real del propietario",
                ),
            )
            resumen["preguntas"] += 1
            continue
        final = "resuelta" if estado == "ok" else estado
        evidencia = json.dumps(resultado.get("evidencia") or {}, ensure_ascii=False, sort_keys=True)
        # La entrega del archivo precede a la resolución: sin archivo publicado no hay
        # consulta resuelta.
        doc = _respuesta(
            replace(sol, evidencia=evidencia),
            final,
            datos=resultado.get("datos"),
            motivo=str(resultado.get("motivo") or ""),
            alternativa=str(resultado.get("alternativa") or ""),
            espacio=espacio,
        )
        if final == "resuelta" and doc.get("publicacion_fallo"):
            _escribir_respuesta(
                espacio,
                sol,
                _respuesta(
                    sol,
                    "pendiente",
                    motivo=(
                        "no se pudo publicar el archivo de datos en el espacio: "
                        f"{doc['publicacion_fallo']}"
                    ),
                    alternativa=(
                        "el resultado quedó guardado en el host; la entrega se "
                        "reintentará en el siguiente paso sin repetir la consulta"
                    ),
                ),
            )
            resumen["pendientes"] = resumen.get("pendientes", 0) + 1
            continue
        _anotar_resolucion(
            registro,
            enc,
            sol,
            final if final in ("resuelta", "detenida") else "error",
            evidencia=evidencia,
            detalle=str(resultado.get("motivo") or ""),
            hora=hora,
        )
        actual = registro.fold(enc.id) or enc
        nuevo = _buscar(actual, sol.id) or sol
        _escribir_respuesta(espacio, nuevo, doc)
        _archivar(path)
        resumen["atendidas" if final == "resuelta" else "errores"] += 1
    return resumen


def continuar_respuestas(
    registro: Registro,
    enc,
    espacio: Path,
    directorio: Path,
    hora: str,
) -> dict:
    """Sigue el trabajo que esperaba una respuesta. Una ya resuelta no se repite."""
    resumen = {"continuadas": 0, "denegadas": 0, "omitidas": 0}
    try:
        recursos = capacidades.cargar(registro.base)
    except capacidades.ConfigInvalida:
        recursos = {}
    actual = registro.fold(enc.id) or enc
    for sol in tuple(actual.solicitudes):
        if sol.estado != "respuesta":
            resumen["omitidas"] += 1
            continue
        if not _afirmativa(sol.respuesta):
            _anotar_resolucion(
                registro,
                actual,
                sol,
                "denegada",
                detalle=f"el propietario respondió: {sol.respuesta}",
                hora=hora,
            )
            nuevo = _buscar(registro.fold(enc.id) or actual, sol.id) or sol
            _escribir_respuesta(
                espacio,
                nuevo,
                _respuesta(nuevo, "denegada", motivo="la autorización no fue concedida", respuesta=sol.respuesta),
            )
            resumen["denegadas"] += 1
            actual = registro.fold(enc.id) or actual
            continue
        previo = _resultado_huerfano(espacio, sol, directorio)
        if previo is not None and str(previo.get("estado") or "") not in ("", "pregunta"):
            resultado = previo
        else:
            resultado = _ejecutar(
                registro, actual, sol, recursos, espacio, directorio, True, hora
            )
        estado = str(resultado.get("estado") or "error")
        if estado in ("pregunta", "pendiente"):
            resumen["omitidas"] += 1
            continue
        final = "resuelta" if estado == "ok" else estado
        if final not in ("resuelta", "detenida", "error", "denegada"):
            final = "error"
        evidencia = json.dumps(
            resultado.get("evidencia") or {}, ensure_ascii=False, sort_keys=True
        )
        _anotar_resolucion(
            registro,
            actual,
            sol,
            final if final in ("resuelta", "detenida") else "error",
            evidencia=evidencia,
            detalle=str(resultado.get("motivo") or ""),
            hora=hora,
        )
        nuevo = _buscar(registro.fold(enc.id) or actual, sol.id) or sol
        _escribir_respuesta(
            espacio,
            nuevo,
            _respuesta(
                nuevo,
                final,
                datos=resultado.get("datos"),
                motivo=str(resultado.get("motivo") or ""),
                respuesta=sol.respuesta,
                espacio=espacio,
            ),
        )
        resumen["continuadas"] += 1
        actual = registro.fold(enc.id) or actual
    return resumen


def responder(
    registro: Registro,
    encargo_id: str,
    solicitud_id: str,
    texto: str,
    autor: str,
    clave: str,
    hora: str,
) -> dict:
    """Respuesta del propietario a una pregunta identificable. Deduplica por mensaje."""
    enc = registro.fold(encargo_id)
    if enc is None:
        raise TransicionIlegal("no hay encargo")
    sol = _buscar(enc, solicitud_id)
    if sol is None:
        raise TransicionIlegal("no hay una solicitud con ese id")
    if not str(texto or "").strip():
        raise TransicionIlegal("la respuesta está vacía")
    ukey = f"respuesta:{encargo_id}:{clave}"
    resultado = _anotar(
        registro,
        encargo_id,
        {
            "kind": "respuesta",
            "data": {
                "solicitud": solicitud_id,
                "texto": str(texto),
                "autor": autor or "propietario",
                "clave": clave,
                "hora": hora,
            },
        },
        ukey,
    )
    if resultado == "ajena":
        return {"conflicto": True, "motivo": "esa clave ya respondió otra cosa"}
    actual = registro.fold(encargo_id)
    nueva = _buscar(actual, solicitud_id)
    aplicada = bool(nueva and nueva.estado == "respuesta" and nueva.respuesta == texto)
    if aplicada and nueva is not None and nueva.recurso and _afirmativa(texto):
        _anotar(
            registro,
            encargo_id,
            {
                "kind": "autorizacion",
                "data": {
                    "recurso": nueva.recurso,
                    "operacion": nueva.operacion,
                    "fase": nueva.fase,
                    "intento": nueva.intento,
                    "texto": texto,
                    "autor": autor or "propietario",
                    "hora": hora,
                },
            },
            f"autorizacion:{encargo_id}:{nueva.recurso}:{nueva.operacion}:{clave}",
        )
    return {
        "solicitud": solicitud_id,
        "aplicada": aplicada,
        "retransmision": resultado == "igual",
        "estado": (nueva.estado if nueva else ""),
    }


def cerrar_herramientas(directorio: Path) -> int:
    """Cierra las herramientas que el motor lanzó para este intento."""
    from motor import plataforma

    cerrados = 0
    for path in sorted((Path(directorio) / "herramientas").glob("*.json")):
        doc = _leer(path) or {}
        pid, inicio = doc.get("pid"), doc.get("inicio")
        if pid and inicio:
            plataforma.matar_proc(int(pid), str(inicio))
            cerrados += 1
        _archivar(path)
    return cerrados
