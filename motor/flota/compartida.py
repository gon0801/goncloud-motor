"""Distribuye una política y sus artefactos fijados sin crear otro publicador."""

from __future__ import annotations
from motor.io import escribir_texto, leer_json

import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

from .cerrojo import _cerrojo_flota
from .distribuidores import HERMES, NPM, REGISTRY, _validar_componente
from .manifiesto import cargar, emitir


class SnapshotInvalido(ValueError):
    pass


class PublicacionRechazada(SnapshotInvalido):
    """El snapshot es valido pero no es mas nuevo que el que la replica ya aplica."""


class _SinClaw(Exception):
    """La busqueda automatica no hallo claw-routing: se publica como antes."""


SCHEMA = "goncloud.flota.compartida.v1"
SCHEMA_V2 = "goncloud.flota.compartida.v2"
SCHEMA_V3 = "goncloud.flota.compartida.v3"
_CAMPOS = {"schema", "publicador", "generacion", "manifiesto", "sha256", "catalogo"}
_CLAW = "claw_routing"
_NUMERO = "publicacion"
_CONTADOR = "ultima-publicacion.json"
_CLAW_ARCHIVO = "claw-routing.json"
_CLAW_TOPE = 1 << 20
_REVISION = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_EXTRA = {SCHEMA_V2: {_CLAW}, SCHEMA_V3: {_CLAW, _NUMERO}}


def _campos(schema) -> set[str]:
    """v1 es el snapshot de siempre; v2 suma claw_routing; v3 suma el numero de
    publicacion y lleva claw_routing siempre, nulo si el publicador no pudo
    leerlo de un checkout."""
    return _CAMPOS | (_EXTRA.get(schema, set()) if isinstance(schema, str) else set())


def _json_bytes(doc: dict) -> bytes:
    return (
        json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def _escribir(path: Path, contenido: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == contenido:
        return
    temporal = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".flota-", delete=False
        ) as archivo:
            temporal = Path(archivo.name)
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        temporal.replace(path)
    finally:
        if temporal is not None:
            temporal.unlink(missing_ok=True)


def _politica(texto: str):
    if not isinstance(texto, str):
        raise SnapshotInvalido("el snapshot no contiene un manifiesto TOML")
    with tempfile.TemporaryDirectory(prefix="flota-politica-") as temporal:
        path = Path(temporal) / "manifiesto.toml"
        escribir_texto(path, texto)
        politica = cargar(path)
    if emitir(politica) != texto:
        raise SnapshotInvalido("el manifiesto no es la salida canonica del publicador")
    return politica


def _catalogo(doc: dict, politica) -> dict:
    if not isinstance(doc, dict) or set(doc) - {"componentes", "no_disponibles"}:
        raise SnapshotInvalido("catalogo compartido invalido")
    componentes = doc.get("componentes")
    if not isinstance(componentes, list):
        raise SnapshotInvalido("falta el catalogo de componentes fijados")
    nombres = set()
    for componente in componentes:
        if not isinstance(componente, dict):
            raise SnapshotInvalido("componente compartido invalido")
        if any(not isinstance(valor, str) for valor in componente.values()):
            raise SnapshotInvalido("los campos del componente deben ser texto")
        _validar_componente(componente)
        nombre = componente["nombre"]
        if nombre in nombres:
            raise SnapshotInvalido("componente compartido repetido")
        nombres.add(nombre)
        campos = {"nombre", "tipo", "version", "fuente", "integridad"}
        campos |= (
            {"paquete", "tarball"} if nombre in NPM else {"repositorio", "revision"}
        )
        if set(componente) != campos:
            raise SnapshotInvalido("el componente contiene campos no publicables")
        for campo in ("fuente", "tarball", "repositorio"):
            if campo not in componente:
                continue
            url = urllib.parse.urlsplit(componente[campo])
            if (
                url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
            ):
                raise SnapshotInvalido(
                    "el origen contiene datos ajenos al artefacto publico"
                )
        if nombre == "hermes":
            if componente["repositorio"] != HERMES or componente["fuente"] != (
                "https://api.github.com/repos/NousResearch/hermes-agent/releases/latest"
            ):
                raise SnapshotInvalido("el canal de Hermes no es oficial")
        else:
            fuente = REGISTRY + urllib.parse.quote(NPM[nombre], safe="") + "/"
            if componente["fuente"] not in (
                fuente + "latest",
                fuente + urllib.parse.quote(componente["version"], safe=""),
            ):
                raise SnapshotInvalido(
                    "el canal npm no corresponde a la version declarada"
                )
    herramientas = {h.nombre: h for h in politica.herramientas}
    if len(herramientas) != len(politica.herramientas) or set(herramientas) != nombres:
        raise SnapshotInvalido(
            "el catalogo no corresponde a todas las herramientas de la politica"
        )
    for componente in componentes:
        herramienta = herramientas[componente["nombre"]]
        if (herramienta.canal, herramienta.version, herramienta.integridad) != (
            componente["fuente"],
            componente["version"],
            componente["integridad"],
        ):
            raise SnapshotInvalido(
                "version, canal o integridad no coinciden con la politica"
            )
    ausentes = doc.get("no_disponibles") or []
    if not isinstance(ausentes, list):
        raise SnapshotInvalido("lista de capacidades no disponibles invalida")
    for ausente in ausentes:
        if not isinstance(ausente, dict) or set(ausente) - {
            "nombre",
            "estado",
            "motivo",
        }:
            raise SnapshotInvalido("capacidad no disponible invalida")
        if not isinstance(ausente.get("nombre"), str) or ausente["nombre"] not in {
            *NPM,
            "hermes",
        }:
            raise SnapshotInvalido("capacidad no registrada")
    return {
        "componentes": componentes,
        "no_disponibles": [
            {
                "nombre": a["nombre"],
                "estado": "no_disponible",
                "motivo": "canal no comprobado",
            }
            for a in ausentes
        ],
    }


def _claw_routing(doc) -> None:
    if not isinstance(doc, dict) or set(doc) != {"contenido", "sha256", "revision"}:
        raise SnapshotInvalido("claw-routing publicado invalido")
    contenido, digest, revision = doc["contenido"], doc["sha256"], doc["revision"]
    if not all(isinstance(v, str) for v in (contenido, digest, revision)):
        raise SnapshotInvalido("los campos de claw-routing deben ser texto")
    if not _REVISION.fullmatch(revision):
        raise SnapshotInvalido("la revision de origen de claw-routing es invalida")
    datos = contenido.encode("utf-8")
    if len(datos) > _CLAW_TOPE:
        raise SnapshotInvalido("claw-routing publicado excede el tamano admitido")
    if _sha256(datos) != digest:
        raise SnapshotInvalido("la integridad de claw-routing no coincide")
    try:
        routing = json.loads(contenido)
    except ValueError as exc:
        raise SnapshotInvalido("claw-routing publicado no es JSON") from exc
    if not isinstance(routing, dict) or not isinstance(routing.get("agents"), dict):
        raise SnapshotInvalido("claw-routing publicado no declara agents")


def _validar_snapshot(snapshot: dict):
    if not isinstance(snapshot, dict):
        raise SnapshotInvalido("estructura del snapshot compartido invalida")
    schema = snapshot.get("schema")
    if set(snapshot) != _campos(schema) | {"snapshot_sha256"}:
        raise SnapshotInvalido("estructura del snapshot compartido invalida")
    if schema not in (SCHEMA, SCHEMA_V2, SCHEMA_V3):
        raise SnapshotInvalido("version del snapshot compartido no admitida")
    if schema == SCHEMA_V3 and (
        type(snapshot[_NUMERO]) is not int or snapshot[_NUMERO] < 1
    ):
        raise SnapshotInvalido("el numero de publicacion debe ser un entero positivo")
    publicador = snapshot["publicador"]
    if (
        not isinstance(publicador, str)
        or not publicador
        or len(publicador) > 255
        or any(not (c.isalnum() or c in ".-_") for c in publicador)
    ):
        raise SnapshotInvalido("publicador invalido")
    contenido = {campo: snapshot[campo] for campo in _campos(schema)}
    if _sha256(_json_bytes(contenido)) != snapshot["snapshot_sha256"]:
        raise SnapshotInvalido("la integridad del snapshot no coincide")
    texto = snapshot["manifiesto"]
    if (
        not isinstance(texto, str)
        or _sha256(texto.encode("utf-8")) != snapshot["sha256"]
    ):
        raise SnapshotInvalido("la integridad del manifiesto no coincide")
    politica = _politica(texto)
    if politica.generacion != snapshot["generacion"]:
        raise SnapshotInvalido("la generacion del snapshot no coincide")
    catalogo = _catalogo(snapshot["catalogo"], politica)
    if catalogo != snapshot["catalogo"]:
        raise SnapshotInvalido("el catalogo no es una salida publica canonica")
    if schema == SCHEMA_V2 or snapshot.get(_CLAW) is not None:
        _claw_routing(snapshot[_CLAW])
    return politica


def leer_fuente(home: Path) -> dict | None:
    """Recupera la proyección local desde el único archivo canónico de réplica."""
    home = Path(home).resolve()
    with _cerrojo_flota(home):
        path = home / "fuente-compartida.json"
        if not path.exists():
            return None
        fuente = leer_json(path)
        if not isinstance(fuente, dict) or fuente.get("modo") != "replica":
            raise SnapshotInvalido("modo de fuente compartida invalido")
        campos = _campos(fuente.get("schema")) | {"snapshot_sha256"}
        if set(fuente) != campos | {"modo", "origen", "hash"}:
            raise SnapshotInvalido("fuente compartida invalida")
        snapshot = {campo: fuente[campo] for campo in campos}
        _validar_snapshot(snapshot)
        if (
            fuente["origen"] != snapshot["publicador"]
            or fuente["hash"] != snapshot["snapshot_sha256"]
        ):
            raise SnapshotInvalido("la referencia al publicador no coincide")
        _escribir(home / "manifiesto.toml", snapshot["manifiesto"].encode("utf-8"))
        return fuente


def _catalogo_preparado(home: Path, politica) -> dict:
    actual = home / "actual" / "runtime.json"
    candidatos = []
    if actual.is_file():
        runtime = leer_json(actual)
        candidatos.append(
            {
                "componentes": runtime.get("componentes"),
                "no_disponibles": runtime.get("no_disponibles") or [],
            }
        )
    carpeta = home / "preparaciones"
    for path in sorted(
        carpeta.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True
    ):
        recibo = leer_json(path)
        if recibo.get("estado") == "preparada" and isinstance(
            recibo.get("catalogo"), dict
        ):
            candidatos.append(recibo["catalogo"])
    for candidato in candidatos:
        try:
            return _catalogo(candidato, politica)
        except (ValueError, TypeError, KeyError):
            continue
    if not politica.herramientas:
        return {"componentes": [], "no_disponibles": []}
    raise SnapshotInvalido(
        "no existe un catalogo preparado que corresponda a la politica"
    )


def _git_bytes(repo: Path, *args: str) -> bytes:
    hecho = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )
    if hecho.returncode != 0:
        raise ValueError(
            f"git {args[0]} fallo en {repo}: "
            + hecho.stderr.decode("utf-8", "replace").strip()[:200]
        )
    return hecho.stdout


def _leer_claw_routing(repo: Path) -> dict:
    """claw-routing.json tal como esta en HEAD del checkout, no en el arbol de trabajo."""
    if not (repo / ".git").exists():
        raise ValueError(f"{repo} no es un checkout git")
    revision = _git_bytes(repo, "rev-parse", "HEAD").decode("ascii").strip()
    datos = _git_bytes(repo, "cat-file", "blob", f"{revision}:{_CLAW_ARCHIVO}")
    try:
        contenido = datos.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{_CLAW_ARCHIVO} de {repo} no es UTF-8") from exc
    return {"contenido": contenido, "sha256": _sha256(datos), "revision": revision}


def _candidatos_hermes(motor_origen: Path) -> list[Path]:
    """Checkouts de goncloud-hermes junto al motor, en orden de preferencia."""
    from .actualizaciones import _opciones_repo

    padre = motor_origen.parent
    opciones = [
        *_opciones_repo(padre, "goncloud-hermes"),
        padre / "motor-config" / "hermes",
    ]
    return [p for p in opciones if (p / ".git").exists()]


def _informe_claw(claw: dict | None, motivo: str = "") -> dict:
    if claw is None:
        return {"incluido": False, "motivo": motivo}
    return {"incluido": True, "revision": claw["revision"], "sha256": claw["sha256"]}


def _claw_publicable(repo_hermes: Path | None, motor_origen: Path) -> dict | None:
    """El claw-routing a publicar. Un --repo-hermes explicito que no sirve falla."""
    if repo_hermes is not None:
        return _leer_claw_routing(Path(repo_hermes).resolve())
    candidatos = _candidatos_hermes(motor_origen)
    if not candidatos:
        raise _SinClaw("no se encontro un checkout git de goncloud-hermes")
    fallos = []
    for repo in candidatos:
        try:
            return _leer_claw_routing(repo.resolve())
        except ValueError as exc:
            fallos.append(str(exc))
    raise _SinClaw("; ".join(fallos))


def _leer_doc(path: Path) -> object:
    try:
        return leer_json(path)
    except ValueError:
        return None


def _leer_contador(contador: Path) -> tuple[int, str] | None:
    """(numero, contenido_sha256) de la ultima publicacion, o None si no hay."""
    if not contador.exists():
        return None
    previo = _leer_doc(contador)
    if isinstance(previo, dict):
        numero, huella = previo.get("numero"), previo.get("contenido_sha256")
        if type(numero) is int and isinstance(huella, str):
            return numero, huella
    raise SnapshotInvalido(
        f"el contador de publicaciones no se puede leer ({contador}); renumera "
        "con: python -m motor flota publicar --home <flota> --salida <snapshot> "
        "--desde N, con N mayor que la publicacion que aplican las replicas"
    )


def _numerar(home: Path, huella: str, desde: int | None) -> int:
    """El numero de la publicacion: sube solo si cambia el contenido canonico."""
    try:
        previo = _leer_contador(home / _CONTADOR)
    except SnapshotInvalido:
        if desde is None:
            raise
        previo = None
    if desde is not None:
        if previo is not None and desde <= previo[0]:
            raise SnapshotInvalido(
                f"el publicador ya va en la publicacion {previo[0]}; "
                "--desde solo sirve para subir el numero"
            )
        return desde
    if previo is None:
        return 1
    numero, anterior = previo
    return numero if anterior == huella else numero + 1


def exportar(
    home: Path,
    salida: Path,
    repo_hermes: Path | None = None,
    motor_origen: Path | None = None,
    desde: int | None = None,
) -> dict:
    """Publica el snapshot v3. El contenido canonico es todo lo que la replica
    aplica (manifiesto, catalogo con las versiones de herramientas y
    claw_routing) mas el publicador; ``desde`` renumera a mano."""
    home, salida = Path(home).resolve(), Path(salida).resolve()
    if salida in (
        home / "manifiesto.toml",
        home / "fuente-compartida.json",
        home / _CONTADOR,
    ):
        raise SnapshotInvalido("la salida no puede reemplazar la fuente de la politica")
    motor_origen = Path(motor_origen or Path(__file__).resolve().parents[2]).resolve()
    with _cerrojo_flota(home):
        if leer_fuente(home) is not None:
            raise SnapshotInvalido(
                "una replica no puede republicar la politica compartida"
            )
        politica = cargar(home / "manifiesto.toml")
        texto = emitir(politica)
        try:
            claw, motivo = _claw_publicable(repo_hermes, motor_origen), ""
        except _SinClaw as exc:
            claw, motivo = None, str(exc)
        canonico = {
            "publicador": socket.gethostname(),
            "generacion": politica.generacion,
            "manifiesto": texto,
            "sha256": _sha256(texto.encode("utf-8")),
            "catalogo": _catalogo_preparado(home, politica),
            _CLAW: claw,
        }
        huella = _sha256(_json_bytes(canonico))
        numero = _numerar(home, huella, desde)
        contenido = {"schema": SCHEMA_V3, _NUMERO: numero, **canonico}
        snapshot = {**contenido, "snapshot_sha256": _sha256(_json_bytes(contenido))}
        _validar_snapshot(snapshot)
        # El contador va antes que el snapshot: si el proceso cae entre los dos,
        # la corrida siguiente da el mismo numero al mismo contenido en vez de
        # dar un numero ya entregado a un contenido distinto.
        contador = {"numero": numero, "contenido_sha256": huella}
        _escribir(home / _CONTADOR, _json_bytes(contador))
        _escribir(salida, _json_bytes(snapshot))
        return {
            "accion": "publicada",
            "salida": str(salida),
            "origen": canonico["publicador"],
            "hash": snapshot["snapshot_sha256"],
            "generacion": politica.generacion,
            _NUMERO: numero,
            "claw_routing": _informe_claw(claw, motivo),
        }


def _vigente(path: Path) -> tuple[int, str, str] | None:
    """(publicacion, hash, origen) de la fuente vigente, o None si aun no tiene
    numero. Lee solo esos campos sin validar la fuente: una fuente que una
    version nueva ya no acepta sigue ordenando y la cura la siguiente
    publicacion."""
    if not path.exists():
        return None
    vigente = _leer_doc(path)
    if isinstance(vigente, dict) and _NUMERO not in vigente:
        return None
    if isinstance(vigente, dict):
        numero, digest, origen = (vigente.get(k) for k in (_NUMERO, "hash", "origen"))
        if type(numero) is int and isinstance(digest, str) and isinstance(origen, str):
            return numero, digest, origen
    raise SnapshotInvalido(
        f"la fuente vigente de la replica no se puede leer ({path}); borrala y "
        "vuelve a importar el snapshot vigente del publicador"
    )


def _ante_la_vigente(home: Path, snapshot: dict) -> str:
    """La accion de importar: sincronizada si el snapshot es mas nuevo y
    sin_cambios si es el mismo. Si es mas viejo o choca con el vigente, lanza."""
    vigente = _vigente(home / "fuente-compartida.json")
    if vigente is None:
        return "sincronizada"
    actual, digest, origen = vigente
    numero, publicador = snapshot.get(_NUMERO), snapshot["publicador"]
    if numero is None:
        raise PublicacionRechazada(
            f"la replica ya aplica la publicacion {actual} de {origen} "
            f"y este snapshot {snapshot['schema']} no trae numero: actualiza el "
            f"motor de {publicador} a la version de la flota y vuelve a publicar"
        )
    if numero > actual:
        return "sincronizada"
    if numero == actual and snapshot["snapshot_sha256"] == digest:
        return "sin_cambios"
    causa = (
        f"{publicador} entrego otro contenido con el numero {numero}"
        if numero == actual
        else f"la publicacion {numero} de {publicador} es anterior a la {actual}; "
        "si es una copia vieja, vuelve a copiar la vigente del publicador"
    )
    raise PublicacionRechazada(
        f"{causa}; la replica conserva la {actual} de {origen}. Si el "
        f"publicador perdio su {_CONTADOR}, renumera en esa maquina con: python -m "
        f"motor flota publicar --home <flota> --salida <snapshot> --desde {actual + 1}"
    )


def importar(home: Path, archivo: Path) -> dict:
    snapshot = leer_json(Path(archivo))
    _validar_snapshot(snapshot)
    home = Path(home).resolve()
    fuente = {
        **snapshot,
        "modo": "replica",
        "origen": snapshot["publicador"],
        "hash": snapshot["snapshot_sha256"],
    }
    with _cerrojo_flota(home):
        accion = _ante_la_vigente(home, snapshot)
        if accion == "sincronizada":
            _escribir(home / "fuente-compartida.json", _json_bytes(fuente))
        leer_fuente(home)
    return {
        "accion": accion,
        _NUMERO: snapshot.get(_NUMERO),
        "origen": fuente["origen"],
        "hash": fuente["hash"],
        "generacion": fuente["generacion"],
        "componentes": [c["nombre"] for c in fuente["catalogo"]["componentes"]],
        "claw_routing": _informe_claw(
            fuente.get(_CLAW),
            "el publicador no publico claw-routing: no hallo un checkout "
            "de goncloud-hermes del que pudiera leerlo"
            if _CLAW in fuente
            else "el snapshot no lleva claw-routing",
        ),
    }
