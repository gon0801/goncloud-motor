"""Capacidades comprobadas del host. Operaciones acotadas, nunca una shell.

El catálogo sale de la configuración del host (`capacidades.json` en la base del
registro). El agente nombra un recurso y una operación; la ruta, la credencial y
el destino los decide el host. Una conexión SQLite concedida se abre en solo
lectura y con autorizador: no escribe, no adjunta otra base y no abre archivos
fuera del recurso.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

CONFIG = "capacidades.json"

TABLAS = "sqlite.tablas"
CONSULTAR = "sqlite.consultar"
SNAPSHOT = "sqlite.snapshot"
REMOTO = "remoto.leer"
DECISION = "decision.registrar"

OPERACIONES_SIN_RECURSO = (DECISION,)

MAX_SQL = 20_000
MAX_FILAS = 500
MAX_DATOS = 1024 * 1024
MAX_TEXTO = 64 * 1024
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 120

_SQLITE_OK = 0
_SQLITE_DENY = 1
_SQLITE_PRAGMA = 19
# READ, SELECT, FUNCTION y RECURSIVE. Todo lo demás (ATTACH, PRAGMA, escrituras,
# transacciones) se deniega aunque `query_only` ya bloquee una escritura.
_ACCIONES_PERMITIDAS = frozenset({20, 21, 31, 33})
# Introspección de esquema: no cambia estado ni abre otro archivo.
_PRAGMAS_PERMITIDOS = frozenset(
    {
        "table_info",
        "table_xinfo",
        "table_list",
        "index_list",
        "index_info",
        "foreign_key_list",
    }
)


class ConfigInvalida(RuntimeError):
    pass


def _autorizador(accion: int, arg1, arg2, dbname, origen) -> int:
    if accion == _SQLITE_PRAGMA:
        return (
            _SQLITE_OK
            if str(arg1 or "").lower() in _PRAGMAS_PERMITIDOS
            else _SQLITE_DENY
        )
    return _SQLITE_OK if accion in _ACCIONES_PERMITIDAS else _SQLITE_DENY


def _ruta_absoluta(valor: str) -> Path:
    ruta = Path(str(valor)).expanduser()
    if not ruta.is_absolute():
        raise ConfigInvalida(f"la ruta del recurso debe ser absoluta: {valor}")
    return ruta


def _padre_seguro(path: Path, raiz: Path | None = None) -> Path:
    """Rechaza un destino dentro de la raíz que pase por un enlace del trabajador."""
    path = Path(path)
    if raiz is not None:
        raiz_abs = Path(os.path.abspath(raiz))
        actual = Path(os.path.abspath(path.parent))
        previo = actual
        while previo != raiz_abs and raiz_abs in previo.parents:
            if previo.is_symlink():
                raise ValueError(f"el destino {path} pasa por un enlace simbólico")
            previo = previo.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    if raiz is None:
        return path
    raiz = Path(os.path.abspath(raiz))
    actual = Path(os.path.abspath(path.parent))
    while actual != raiz and raiz in actual.parents:
        if actual.is_symlink():
            raise ValueError(f"el destino {path} pasa por un enlace simbólico")
        actual = actual.parent
    return path


def _escribir_seguro(path: Path, datos: bytes, raiz: Path | None = None) -> None:
    """Sustituye el archivo sin seguir un enlace que haya dejado el trabajador."""
    path = _padre_seguro(path, raiz)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    banderas = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, banderas, 0o600)
    with os.fdopen(fd, "wb") as salida:
        salida.write(datos)


class _SinRedireccion(HTTPRedirectHandler):
    """Una redirección no cambia el destino concedido ni arrastra la credencial."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def cargar(base: Path) -> dict[str, dict]:
    """Recursos concedidos por el host. Sin archivo no hay capacidades externas."""
    path = Path(base) / CONFIG
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigInvalida(f"{CONFIG} no se puede leer: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("recursos", {}), dict):
        raise ConfigInvalida(f"{CONFIG} necesita un objeto recursos")
    recursos: dict[str, dict] = {}
    for nombre, crudo in (doc.get("recursos") or {}).items():
        clave = str(nombre).strip()
        if not clave or not all(c.isalnum() or c in "-_." for c in clave):
            raise ConfigInvalida(f"nombre de recurso inválido: {nombre!r}")
        if not isinstance(crudo, dict):
            raise ConfigInvalida(f"el recurso {clave} debe ser un objeto")
        tipo = str(crudo.get("tipo") or "").strip()
        if tipo not in ("sqlite", "remoto"):
            raise ConfigInvalida(f"el recurso {clave} tiene un tipo desconocido: {tipo!r}")
        autorizacion = str(crudo.get("autorizacion") or "").strip()
        if autorizacion not in ("", "propietario"):
            raise ConfigInvalida(
                f"el recurso {clave} solo admite autorizacion vacía o propietario"
            )
        recursos[clave] = {**crudo, "tipo": tipo, "autorizacion": autorizacion}
    return recursos


def catalogo(recursos: dict[str, dict]) -> list[dict]:
    """Vista pública: operaciones y recursos, sin rutas ni credenciales."""
    salida = []
    for nombre in sorted(recursos):
        recurso = recursos[nombre]
        if recurso["tipo"] == "sqlite":
            operaciones = [TABLAS, CONSULTAR, SNAPSHOT]
        else:
            operaciones = [REMOTO]
        salida.append(
            {
                "recurso": nombre,
                "tipo": recurso["tipo"],
                "operaciones": operaciones,
                "autorizacion": recurso.get("autorizacion") or "",
            }
        )
    return salida


def operaciones_de(recursos: dict[str, dict], recurso: str) -> tuple[str, ...]:
    if recurso not in recursos:
        return ()
    return tuple(catalogo({recurso: recursos[recurso]})[0]["operaciones"])


def _resolver(recurso: str, recursos: dict[str, dict]) -> dict:
    if recurso not in recursos:
        disponibles = ", ".join(sorted(recursos)) or "ninguno"
        raise KeyError(
            f"el host no concede el recurso {recurso!r}; recursos concedidos: {disponibles}"
        )
    return recursos[recurso]


def abrir_lectura(ruta: Path) -> sqlite3.Connection:
    """Conexión de solo lectura. Cualquier acción fuera de leer niega la sentencia."""
    destino = Path(ruta).resolve()
    db = sqlite3.connect(
        "file:" + quote(str(destino)) + "?mode=ro",
        uri=True,
        isolation_level=None,
        timeout=10,
    )
    db.execute("PRAGMA query_only=ON")
    db.set_authorizer(_autorizador)
    return db


def _esquema(ruta: Path) -> list[dict]:
    db = abrir_lectura(ruta)
    try:
        tablas = db.execute(
            "SELECT name, type FROM sqlite_master"
            " WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
            " ORDER BY name"
        ).fetchall()
        salida = []
        for nombre, clase in tablas:
            columnas = db.execute(
                "SELECT name, type, \"notnull\", pk FROM pragma_table_info(?)", (nombre,)
            ).fetchall()
            salida.append(
                {
                    "tabla": nombre,
                    "clase": clase,
                    "columnas": [
                        {"nombre": c[0], "tipo": c[1] or "", "obligatoria": bool(c[2]), "pk": bool(c[3])}
                        for c in columnas
                    ],
                }
            )
        return salida
    finally:
        db.close()


def _sentencia_leible(sql: str) -> str:
    texto = str(sql or "").strip()
    if not texto:
        raise ValueError("la consulta está vacía")
    if len(texto) > MAX_SQL:
        raise ValueError(f"la consulta supera {MAX_SQL} caracteres")
    cuerpo = texto[:-1] if texto.endswith(";") else texto
    if ";" in cuerpo:
        raise ValueError("una solicitud admite una sola sentencia")
    primera = cuerpo.lstrip().split(None, 1)[0].lower() if cuerpo.strip() else ""
    if primera not in ("select", "with"):
        raise ValueError("la consulta debe empezar por SELECT o WITH")
    return cuerpo


def _huella(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _consultar(ruta: Path, sql: str, destino: Path) -> dict:
    cuerpo = _sentencia_leible(sql)
    db = abrir_lectura(ruta)
    truncado = False
    visibles = []
    total_bytes = 0
    try:
        cursor = db.execute(cuerpo)
        columnas = [d[0] for d in cursor.description or ()]
        for fila in cursor:
            if len(visibles) >= MAX_FILAS:
                truncado = True
                break
            valores = []
            for v in fila:
                if isinstance(v, (str, bytes)) and len(v) > 4096:
                    valores.append(v[:4096])
                    truncado = True
                else:
                    valores.append(v)
            estimada = len(
                json.dumps(valores, ensure_ascii=False, default=str).encode("utf-8")
            )
            if total_bytes + estimada > MAX_DATOS:
                truncado = True
                break
            visibles.append(valores)
            total_bytes += estimada
    finally:
        db.close()
    datos = {
        "columnas": columnas,
        "filas": [list(f) for f in visibles],
        "total_visible": len(visibles),
        "truncado": truncado,
    }
    ruta_datos = destino / "datos.json"
    _escribir_seguro(
        ruta_datos,
        json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8"),
    )
    return {
        "estado": "ok",
        "datos": datos,
        "evidencia": {
            "tipo": "consulta",
            "sql": cuerpo,
            "filas": len(visibles),
            "truncado": truncado,
            "ruta_datos": str(ruta_datos),
            "sha256": _huella(ruta_datos),
        },
    }


def _snapshot(ruta: Path, destino: Path) -> dict:
    copia = destino / "snapshot.db"
    temporal = destino / "snapshot.db.tmp"
    try:
        temporal.unlink()
    except FileNotFoundError:
        pass
    origen = abrir_lectura(ruta)
    try:
        nuevo = sqlite3.connect(str(temporal))
        try:
            origen.backup(nuevo)
        finally:
            nuevo.close()
        tablas = [f[0] for f in origen.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()]
    finally:
        origen.close()
    os.replace(temporal, copia)
    return {
        "estado": "ok",
        "datos": {
            "snapshot": str(copia),
            "bytes": copia.stat().st_size,
            "tablas": tablas,
        },
        "evidencia": {
            "tipo": "snapshot",
            "origen": str(Path(ruta).resolve()),
            "origen_mtime": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(Path(ruta).stat().st_mtime)
            ),
            "ruta": str(copia),
            "sha256": _huella(copia),
            "bytes": copia.stat().st_size,
            "tablas": tablas,
        },
    }


def _ruta_relativa(valor: str) -> str:
    texto = str(valor or "").strip()
    if not texto:
        raise ValueError("la ruta del recurso remoto está vacía")
    relativa = Path(texto)
    if relativa.is_absolute() or ".." in relativa.parts or "\\" in texto:
        raise ValueError("la ruta debe ser relativa y no puede salir del recurso")
    return relativa.as_posix()


def _leer_archivo(raiz: Path, relativa: str, destino: Path) -> dict:
    origen = (raiz / relativa).resolve()
    if not origen.is_relative_to(raiz.resolve()):
        raise ValueError("la ruta sale del recurso concedido")
    if not origen.is_file():
        raise FileNotFoundError(f"el recurso no tiene el archivo {relativa}")
    if origen.stat().st_size > MAX_BYTES:
        raise ValueError(f"el archivo supera {MAX_BYTES} bytes")
    copia = destino / "remoto" / origen.name
    crudo = origen.read_bytes()
    _escribir_seguro(copia, crudo)
    texto = ""
    try:
        texto = crudo.decode("utf-8")[:MAX_TEXTO]
    except UnicodeDecodeError:
        texto = ""
    return {
        "estado": "ok",
        "datos": {
            "ruta": relativa,
            "bytes": len(crudo),
            "texto": texto,
            "copia": str(copia),
        },
        "evidencia": {
            "tipo": "remoto",
            "transporte": "fichero",
            "ruta": relativa,
            "ruta_copia": str(copia),
            "sha256": _huella(copia),
            "bytes": len(crudo),
        },
    }


def _leer_http(recurso: dict, relativa: str, destino: Path) -> dict:
    base = str(recurso.get("url") or "").strip()
    if not base:
        raise ConfigInvalida("el recurso remoto http no declara url")
    destino_url = urljoin(base.rstrip("/") + "/", quote(relativa))
    if not destino_url.startswith(base.rstrip("/") + "/"):
        raise ValueError("la ruta sale del recurso concedido")
    cabeceras = {"Accept": "application/json, text/plain, */*"}
    nombre = str(recurso.get("credencial_env") or "").strip()
    if nombre:
        valor = os.environ.get(nombre)
        if not valor:
            raise ConfigInvalida(f"falta la credencial {nombre} en el host")
        cabeceras["Authorization"] = "Bearer " + valor
    try:
        apertura = build_opener(_SinRedireccion())
        with apertura.open(Request(destino_url, headers=cabeceras), timeout=20) as resp:
            crudo = resp.read(MAX_BYTES + 1)
            tipo = resp.headers.get("Content-Type", "")
    except HTTPError as exc:
        if 300 <= int(getattr(exc, "code", 0) or 0) < 400:
            raise RuntimeError(
                f"el recurso intentó redirigir a otro destino ({exc.code}); no se siguió"
            ) from exc
        raise RuntimeError(f"el recurso respondió {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"no se pudo alcanzar el recurso: {exc.reason}") from exc
    if len(crudo) > MAX_BYTES:
        raise ValueError(f"la respuesta supera {MAX_BYTES} bytes")
    copia = destino / "remoto" / (Path(quote(relativa)).name or "respuesta")
    _escribir_seguro(copia, crudo)
    texto = ""
    if "json" in tipo or "text" in tipo or not tipo:
        try:
            texto = crudo.decode("utf-8")[:MAX_TEXTO]
        except UnicodeDecodeError:
            texto = ""
    return {
        "estado": "ok",
        "datos": {
            "ruta": relativa,
            "bytes": len(crudo),
            "texto": texto,
            "copia": str(copia),
        },
        "evidencia": {
            "tipo": "remoto",
            "transporte": "http",
            "ruta": relativa,
            "ruta_copia": str(copia),
            "sha256": _huella(copia),
            "bytes": len(crudo),
        },
    }


def ejecutar(
    recursos: dict[str, dict],
    operacion: str,
    recurso: str,
    parametros: dict,
    destino: Path,
    autorizado: bool = False,
) -> dict:
    """Ejecuta una operación conocida. Devuelve resultado, pregunta o error con causa."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    if operacion == DECISION:
        return {"estado": "error", "motivo": "la decisión no la ejecuta el adaptador"}
    try:
        concedido = _resolver(recurso, recursos)
    except KeyError as exc:
        return {
            "estado": "error",
            "motivo": str(exc.args[0]),
            "alternativa": "pedir al host que conceda el recurso con su ruta real",
        }
    permitidas = operaciones_de(recursos, recurso)
    if operacion not in permitidas:
        return {
            "estado": "error",
            "motivo": f"el recurso {recurso} no concede la operación {operacion}",
            "alternativa": "usar una de: " + ", ".join(permitidas),
        }
    if concedido.get("autorizacion") == "propietario" and not autorizado:
        return {
            "estado": "pregunta",
            "pregunta": f"¿autorizas {operacion} sobre el recurso {recurso}?",
        }
    try:
        if operacion == TABLAS:
            ruta = _ruta_absoluta(concedido.get("ruta") or "")
            if not ruta.is_file():
                return {
                    "estado": "error",
                    "motivo": f"el recurso {recurso} no está disponible en el host",
                    "alternativa": "comprobar la ruta concedida o pedir su concesión",
                }
            tablas = _esquema(ruta)
            return {
                "estado": "ok",
                "datos": {"recurso": recurso, "tablas": tablas},
                "evidencia": {
                    "tipo": "esquema",
                    "recurso": recurso,
                    "origen": str(ruta),
                    "tablas": [t["tabla"] for t in tablas],
                },
            }
        if operacion == CONSULTAR:
            ruta = _ruta_absoluta(concedido.get("ruta") or "")
            return _consultar(ruta, str(parametros.get("sql") or ""), destino)
        if operacion == SNAPSHOT:
            ruta = _ruta_absoluta(concedido.get("ruta") or "")
            return _snapshot(ruta, destino)
        if operacion == REMOTO:
            relativa = _ruta_relativa(str(parametros.get("ruta") or ""))
            if str(concedido.get("transporte") or "fichero") == "http":
                return _leer_http(concedido, relativa, destino)
            return _leer_archivo(_ruta_absoluta(concedido.get("raiz") or ""), relativa, destino)
    except (ValueError, FileNotFoundError, ConfigInvalida) as exc:
        return {
            "estado": "error",
            "motivo": str(exc),
            "alternativa": "corregir los parámetros de la operación y volver a pedirla",
        }
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        return {
            "estado": "error",
            "motivo": f"{type(exc).__name__}: {exc}",
            "alternativa": "revisar el esquema o el recurso antes de reintentar",
        }
    return {"estado": "error", "motivo": f"operación desconocida: {operacion}"}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "--spec":
        print("uso: capacidades.py --spec <archivo>", file=sys.stderr)
        return 2
    spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    destino = Path(spec["destino"])
    try:
        recurso = spec["recurso"]
        concedido = spec["recurso_config"]
        if not isinstance(concedido, dict):
            raise ConfigInvalida("el recurso no llegó resuelto desde el supervisor")
        recursos = {str(recurso): {**concedido, "tipo": str(concedido["tipo"])}}
    except (KeyError, TypeError, ConfigInvalida) as exc:
        resultado = {"estado": "error", "motivo": f"configuración de recurso inválida: {exc}"}
    else:
        resultado = ejecutar(
            recursos,
            str(spec["operacion"]),
            str(recurso),
            {str(k): str(v) for k, v in (spec.get("parametros") or {}).items()},
            destino,
            autorizado=bool(spec.get("autorizado")),
        )
    destino.mkdir(parents=True, exist_ok=True)
    _escribir_seguro(
        destino / "resultado.json",
        json.dumps(resultado, ensure_ascii=False, default=str).encode("utf-8"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
