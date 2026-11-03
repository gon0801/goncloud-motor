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

# El script se lanza con `-P`, que no agrega la raíz del repo al camino de
# importación. Sin esta línea `import motor` falla dentro del subproceso real.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from motor.repositorios import resolver_repositorio

CONFIG = "capacidades.json"

TABLAS = "sqlite.tablas"
CONSULTAR = "sqlite.consultar"
SNAPSHOT = "sqlite.snapshot"
REMOTO = "remoto.leer"
CI = "ci.correr"
CI_LOG = "ci.log"
DECISION = "decision.registrar"

OPERACIONES_SIN_RECURSO = (DECISION,)
# Operaciones cuyo resultado incluye un archivo que el supervisor publica en el espacio.
CON_ARCHIVO = (SNAPSHOT, CI, CI_LOG, REMOTO)
# Operaciones que pueden durar más que un barrido: el supervisor las deja
# terminar y recoge el resultado en el paso siguiente.
ASINCRONAS = (CI, CI_LOG)

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


def _es_enlace(path: Path) -> bool:
    if path.is_symlink():
        return True
    es_junction = getattr(os.path, "isjunction", None)
    return bool(es_junction and es_junction(path))


def _padre_seguro(path: Path, raiz: Path | None = None) -> Path:
    """Rechaza un destino dentro de la raíz que pase por un enlace del trabajador."""
    path = Path(path)
    if raiz is not None:
        raiz_abs = Path(os.path.abspath(raiz))
        actual = Path(os.path.abspath(path.parent))
        previo = actual
        while previo != raiz_abs and raiz_abs in previo.parents:
            if _es_enlace(previo):
                raise ValueError(f"el destino {path} pasa por un enlace simbólico")
            previo = previo.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    if raiz is None:
        return path
    raiz = Path(os.path.abspath(raiz))
    actual = Path(os.path.abspath(path.parent))
    while actual != raiz and raiz in actual.parents:
        if _es_enlace(actual):
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
        if tipo not in ("sqlite", "remoto", "ci"):
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
        elif recurso["tipo"] == "ci":
            operaciones = [CI, CI_LOG]
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


def _texto_de(crudo: bytes, legible: bool = True) -> tuple[str, bool]:
    """Los primeros MAX_TEXTO caracteres del archivo y si eso no es todo: un
    archivo que no es texto no llega en `texto` y cuenta como recortado."""
    if legible:
        try:
            completo = crudo.decode("utf-8")
        except UnicodeDecodeError:
            return "", bool(crudo)
        return completo[:MAX_TEXTO], len(completo) > MAX_TEXTO
    return "", bool(crudo)


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
    texto, recortado = _texto_de(crudo)
    return {
        "estado": "ok",
        "datos": {
            "ruta": relativa,
            "bytes": len(crudo),
            "texto": texto,
            "texto_recortado": recortado,
            "copia": str(copia),
            "copia_host": str(copia),
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
    texto, recortado = _texto_de(crudo, "json" in tipo or "text" in tipo or not tipo)
    return {
        "estado": "ok",
        "datos": {
            "ruta": relativa,
            "bytes": len(crudo),
            "texto": texto,
            "texto_recortado": recortado,
            "copia": str(copia),
            "copia_host": str(copia),
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


CI_SONDEO_S = float(os.environ.get("MOTOR_CI_SONDEO") or 10)
CI_MAX_S = float(os.environ.get("MOTOR_CI_MAX") or 45 * 60)
CI_BUSCA_RUN_S = float(os.environ.get("MOTOR_CI_BUSCA_RUN") or 5 * 60)
CI_MEZCLA_S = float(os.environ.get("MOTOR_CI_MEZCLA") or 30)
CI_MEZCLA_SONDEO_S = float(os.environ.get("MOTOR_CI_MEZCLA_SONDEO") or 2)
CI_QUALITY_S = float(os.environ.get("MOTOR_CI_QUALITY") or 60)
MAX_LOG = 64 * 1024 * 1024
_RED = (
    "could not resolve host",
    "failed to connect",
    "connection refused",
    "connection reset",
    "connection timed out",
    "timed out",
    "network is unreachable",
    "no route to host",
    "error connecting",
    "unable to access",
    "temporary failure in name resolution",
    "tls handshake",
    "dial tcp",
)
_SESION = (
    "gh auth login",
    "authentication",
    "bad credentials",
    "http 401",
    "http 403",
    "permission denied",
    "could not read username",
    "terminal prompts disabled",
)


class CiFallo(RuntimeError):
    """Causa legible para el revisor; `alternativa` dice qué hacer."""

    def __init__(self, motivo: str, alternativa: str = ""):
        super().__init__(motivo)
        self.motivo = motivo
        self.alternativa = alternativa


def _causa_de(salida: str, paso: str) -> CiFallo:
    """Convierte la salida de gh o git en una causa que el revisor entienda."""
    texto = " ".join(str(salida or "").split())[-400:]
    bajo = texto.lower()
    if any(p in bajo for p in _RED):
        return CiFallo(
            f"sin red hacia GitHub al {paso}: {texto}",
            "reintentar cuando el host del supervisor tenga red, o pedir al operador el log",
        )
    if any(p in bajo for p in _SESION):
        return CiFallo(
            f"el acceso de gh o git del supervisor no basta para {paso}: {texto}",
            "pedir al operador que renueve la sesión de gh o git del supervisor",
        )
    return CiFallo(f"no se pudo {paso}: {texto or 'sin salida'}", "revisar la causa antes de reintentar")


def _correr(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict | None = None,
    plazo: float = 120,
    binario: bool = False,
) -> "subprocess.CompletedProcess":
    """`binario` entrega la salida tal cual, sin traducir saltos de línea ni
    decodificar: el log de un job es evidencia literal."""
    import subprocess

    texto = {} if binario else {"text": True, "encoding": "utf-8", "errors": "replace"}
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env={**os.environ, **(env or {})},
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=plazo,
            **texto,
        )
    except subprocess.TimeoutExpired as exc:
        raise CiFallo(
            f"sin respuesta de {Path(argv[0]).name} tras {int(plazo)}s ({' '.join(argv[1:3])})",
            "reintentar cuando el host del supervisor tenga red",
        ) from exc
    except OSError as exc:
        raise CiFallo(f"no se pudo ejecutar {argv[0]}: {exc}", "revisar la instalación en el host") from exc


def _git_ci(espacio: Path, *args: str, env: dict | None = None, plazo: float = 120) -> str:
    r = _correr(["git", "-C", str(espacio), *args], env=env, plazo=plazo)
    if r.returncode != 0:
        raise _causa_de(r.stderr or r.stdout, "git " + args[0])
    return r.stdout


def _gh(gh: str, repo: str, *args: str, plazo: float = 120, binario: bool = False):
    r = _correr([gh, *args, "--repo", repo], plazo=plazo, binario=binario)
    if r.returncode != 0:
        fallo = r.stderr or r.stdout
        if binario:
            fallo = (fallo or b"").decode("utf-8", errors="replace")
        raise _causa_de(fallo, "gh " + " ".join(args[:2]))
    return r.stdout


def _repo_github(valor: str) -> str:
    """owner/nombre para gh; acepta owner/nombre, SSH, HTTPS o una ruta local."""
    texto = str(valor or "").strip()
    if not texto:
        return ""
    return str(resolver_repositorio(texto).github or texto)


def _repo_de(
    concedido: dict, espacio: Path, contexto: dict | None = None
) -> tuple[str, str]:
    """(owner/nombre para gh, remoto de git) del encargo, no de otro repo.

    Manda el repo de entrega del encargo y, si no viene, el origin del espacio.
    Un `repo` declarado en el recurso solo sirve cuando coincide o cuando no hay
    otro dato: el 3-nov el recurso fijo abrió el PR temporal en goncloud-motor
    para un encargo de Orbit."""
    import re

    espacio = Path(espacio) if espacio is not None else None
    remoto = str(concedido.get("remoto") or "").strip()
    origen = ""
    if espacio is not None and (espacio / ".git").exists():
        origen = _git_ci(espacio, "remote", "get-url", "origin").strip()
    if not remoto:
        remoto = origen
    entrega = ""
    if isinstance(contexto, dict):
        entrega = _repo_github(str(contexto.get("repo") or "").strip())
    if not entrega:
        for url in (origen, remoto):
            m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", url or "")
            if m:
                entrega = m.group(1)
                break
    declarado = _repo_github(str(concedido.get("repo") or "").strip())
    repo = entrega or declarado
    if not repo:
        raise CiFallo(
            "el encargo no dice de qué repo es y el origin del espacio no es de GitHub",
            "preparar el clon del repo de entrega del encargo",
        )
    return _repo_github(repo), remoto


REPO_MOTOR = "gon0801/goncloud-motor"
WORKFLOWS_POR_OMISION = {REPO_MOTOR: "pruebas.yml"}
RE_AI_REVIEW = "ai-review"


def _mapa_de_workflows(concedido: dict) -> dict[str, str]:
    """El mapa opcional `workflows` del recurso, con el repo normalizado."""
    mapa = concedido.get("workflows")
    if not isinstance(mapa, dict):
        return {}
    salida = {}
    for clave, valor in mapa.items():
        repo = _repo_github(str(clave or ""))
        archivo = str(valor or "").strip()
        if repo and archivo:
            salida[repo] = archivo
    return salida


def _workflow_de(concedido: dict, repo: str) -> str:
    """El workflow del repo del encargo; '' espera todos los de pull_request.

    Un `workflow` suelto solo vale para el repo que el propio recurso declara
    (o para cualquiera si no declara repo). goncloud-motor conserva
    `pruebas.yml` porque su reuso por rutas y sus jobs dependen de ese
    workflow; cualquier otro repo sin mapa espera los que corran en el PR."""
    mapa = _mapa_de_workflows(concedido)
    if mapa:
        return mapa.get(repo, WORKFLOWS_POR_OMISION.get(repo, ""))
    solo = str(concedido.get("workflow") or "").strip()
    declarado = _repo_github(str(concedido.get("repo") or "").strip())
    if solo and (not declarado or declarado == repo):
        return solo
    return WORKFLOWS_POR_OMISION.get(repo, "")


_INTERNO = (".interno", "__pycache__", ".runtime")


def _es_interno(ruta: str) -> bool:
    """Lo que no es producto: lo interno del encargo y lo generado."""
    return any(parte in _INTERNO for parte in ruta.split("/"))


def arbol_producto(espacio: Path, destino: Path) -> str:
    """Tree de git con la versión vigente del producto (con o sin commit propio).

    Usa un índice aparte: ni el índice ni la rama ni el árbol del espacio se
    tocan. El tree es determinista para el mismo contenido y sirve para comparar
    versiones y reusar el verde de un job cuyas rutas no cambiaron."""
    indice = Path(destino) / "indice-ci"
    indice.unlink(missing_ok=True)
    env = {"GIT_INDEX_FILE": str(indice)}
    try:
        cabeza = _git_ci(espacio, "rev-parse", "--verify", "HEAD").strip()
        _git_ci(espacio, "read-tree", cabeza, env=env)
        _git_ci(
            espacio, "add", "-A", "--", ".",
            ":(exclude).interno", ":(exclude)**/.interno",
            ":(exclude)**/__pycache__", ":(exclude)**/.runtime",
            env=env,
        )
        # `read-tree` trae lo que HEAD ya rastrea y un pathspec de exclusión no
        # lo quita: un `.interno` que alguien commiteó saldría publicado.
        rastreados = [p for p in _git_ci(espacio, "ls-files", "-z", env=env).split("\0") if p]
        fuera = [p for p in rastreados if _es_interno(p)]
        for i in range(0, len(fuera), 200):
            _git_ci(
                espacio, "rm", "--cached", "-q", "--ignore-unmatch", "--", *fuera[i : i + 200],
                env=env,
            )
        return _git_ci(espacio, "write-tree", env=env).strip()
    finally:
        indice.unlink(missing_ok=True)


def _arbol_vacio(espacio: Path, destino: Path) -> str:
    """Tree vacío: la primera corrida no tiene verde previo y corre todo."""
    indice = Path(destino) / "indice-vacio"
    indice.unlink(missing_ok=True)
    env = {"GIT_INDEX_FILE": str(indice)}
    try:
        _git_ci(espacio, "read-tree", "--empty", env=env)
        return _git_ci(espacio, "write-tree", env=env).strip()
    finally:
        indice.unlink(missing_ok=True)


def _identidad_ci() -> dict:
    return {
        "GIT_AUTHOR_NAME": "motor-ci",
        "GIT_AUTHOR_EMAIL": "motor-ci@localhost",
        "GIT_COMMITTER_NAME": "motor-ci",
        "GIT_COMMITTER_EMAIL": "motor-ci@localhost",
    }


def _arbol_de_base(espacio: Path, destino: Path, base_arbol: str) -> str:
    """El árbol verde si existe; si falta o no es un tree, uno vacío.

    Nunca se cae a HEAD: un base ausente tiene que correr todo, no comparar
    solo contra el último commit y saltar jobs de un rojo anterior."""
    if base_arbol:
        try:
            if _git_ci(espacio, "cat-file", "-t", base_arbol).strip() == "tree":
                return base_arbol
        except CiFallo:
            pass
    return _arbol_vacio(espacio, destino)


def _commit_base(
    espacio: Path, destino: Path, ident: str, base_arbol: str = ""
) -> tuple[str, str]:
    """Commit con el árbol verde (o vacío) del que se compara la corrida."""
    arbol = _arbol_de_base(espacio, destino, base_arbol)
    cabeza = _git_ci(espacio, "rev-parse", "--verify", "HEAD").strip()
    sha = _git_ci(
        espacio, "commit-tree", arbol, "-p", cabeza,
        "-m", f"ci-base {ident}", env=_identidad_ci(),
    ).strip()
    return sha, arbol


def _commit_temporal(
    espacio: Path, destino: Path, ident: str, mensaje: str = "", padre: str = ""
) -> tuple[str, str]:
    """Commit con la versión vigente del producto en un índice aparte.

    El padre es el commit base que arma `_commit_base`; si no se pasa, se
    arma uno con un árbol vacío para que el diff corra todo y no compare contra
    HEAD. El workflow compara base y head; no lee el mensaje."""
    arbol = arbol_producto(espacio, destino)
    if not padre:
        padre, _ = _commit_base(espacio, destino, ident, "")
    texto = mensaje or f"ci-prueba {ident}"
    sha = _git_ci(
        espacio, "commit-tree", arbol, "-p", padre, "-m", texto,
        env=_identidad_ci(),
    ).strip()
    return sha, arbol


def _json_gh(gh: str, repo: str, *args: str) -> object:
    salida = _gh(gh, repo, *args)
    try:
        return json.loads(salida or "null")
    except ValueError as exc:
        raise CiFallo(f"gh devolvió algo que no es JSON: {salida[-200:]}", "reintentar") from exc


def _esperar_run(
    gh: str, repo: str, workflow: str, sha: str, limite: float, vigilar=None
) -> dict:
    fin = time.time() + min(CI_BUSCA_RUN_S, limite)
    while True:
        if vigilar is not None:
            vigilar()
        argumentos = ["run", "list", "--commit", sha, "--json",
                      "databaseId,status,conclusion,headSha,event,url,workflowName", "--limit", "20"]
        if workflow:
            argumentos += ["--workflow", workflow]
        runs = [r for r in (_json_gh(gh, repo, *argumentos) or []) if r.get("headSha") == sha]
        if runs:
            return max(runs, key=lambda r: int(r.get("databaseId") or 0))
        if time.time() > fin:
            raise CiFallo(
                f"GitHub no inició el workflow {workflow or '(cualquiera)'} para el commit {sha[:12]}",
                "comprobar que el workflow corre en pull_request y que Actions está habilitado",
            )
        time.sleep(CI_SONDEO_S)


def _runs_de(gh: str, repo: str, sha: str, workflow: str = "") -> list[dict]:
    argumentos = [
        "run", "list", "--commit", sha, "--json",
        "databaseId,status,conclusion,headSha,event,url,workflowName", "--limit", "50",
    ]
    if workflow:
        argumentos += ["--workflow", workflow]
    return [
        r for r in (_json_gh(gh, repo, *argumentos) or [])
        if str(r.get("headSha") or "").lower() == str(sha).lower()
    ]


def _es_ai_review(run: dict) -> bool:
    return RE_AI_REVIEW in str(run.get("workflowName") or "").lower()


def _esperar_runs(
    gh: str, repo: str, workflows: list[str], sha: str, limite: float, vigilar=None
) -> list[dict]:
    """Los runs que el CI del encargo debe esperar.

    Con un workflow explícito devuelve su run. Sin workflow espera a todos los
    que corran en `pull_request` para ese SHA, salvo los de ai-review, y no
    devuelve hasta que todos terminan: el veredicto es verde solo si todos
    acaban en verde."""
    if workflows:
        return [
            _esperar_run(gh, repo, workflow, sha, limite, vigilar=vigilar)
            for workflow in workflows
        ]
    tope = time.time() + max(0.0, limite)
    fin_busqueda = time.time() + min(
        CI_BUSCA_RUN_S, max(0.0, limite)
    )
    while True:
        if vigilar is not None:
            vigilar()
        runs = [
            r for r in _runs_de(gh, repo, sha)
            if str(r.get("event") or "") in ("", "pull_request")
            and not _es_ai_review(r)
        ]
        if runs:
            break
        if time.time() > fin_busqueda:
            raise CiFallo(
                f"GitHub no inició ningún workflow de pull_request para el commit {sha[:12]}",
                "comprobar que el repo tiene un workflow que corre en pull_request "
                "y que Actions está habilitado",
            )
        time.sleep(CI_SONDEO_S)
    while any(str(r.get("status") or "") != "completed" for r in runs):
        if time.time() > tope:
            raise CiFallo(
                f"los workflows de pull_request del commit {sha[:12]} no terminaron "
                f"en {int(CI_MAX_S // 60)} minutos",
                "reintentar más tarde; el PR temporal se cerró",
            )
        time.sleep(CI_SONDEO_S)
        if vigilar is not None:
            vigilar()
        vistos = {str(r.get("databaseId")): r for r in runs}
        for run in _runs_de(gh, repo, sha):
            if str(run.get("event") or "") in ("", "pull_request") and not _es_ai_review(run):
                vistos[str(run.get("databaseId"))] = run
        runs = list(vistos.values())
    return runs


def _vista_completa(
    gh: str, repo: str, run: dict, limite: float
) -> tuple[str, dict]:
    numero_run = str(run["databaseId"])
    while True:
        vista = _json_gh(
            gh, repo, "run", "view", numero_run,
            "--json", "status,conclusion,jobs,url",
        )
        if not isinstance(vista, dict):
            raise CiFallo("gh no devolvió la corrida", "reintentar")
        if str(vista.get("status")) == "completed":
            return numero_run, vista
        if time.time() > limite:
            raise CiFallo(
                f"el run {numero_run} no terminó en {int(CI_MAX_S // 60)} minutos "
                f"({vista.get('url') or ''})",
                "reintentar más tarde; el PR temporal se cerró",
            )
        time.sleep(CI_SONDEO_S)


def _vista_mezcla(gh: str, repo: str, numero: str, limite: float) -> dict:
    """GitHub calcula `mergeable` después de crear el PR: UNKNOWN no es respuesta."""
    fin = time.time() + max(0.0, limite)
    while True:
        vista = _json_gh(
            gh, repo, "pr", "view", numero,
            "--json", "mergeable,mergeStateStatus",
        )
        if not isinstance(vista, dict):
            raise CiFallo("gh no devolvió el estado del PR temporal", "reintentar")
        estado = str(vista.get("mergeable") or "").strip().upper()
        if estado and estado != "UNKNOWN":
            return vista
        if time.time() >= fin:
            return vista
        time.sleep(CI_MEZCLA_SONDEO_S)


def _archivos_en_conflicto(
    espacio: Path, remoto: str, base: str, cabeza: str
) -> list[str] | None:
    """Nombres del choque con `base`; `[]` si no choca y `None` si no se pudo
    comprobar. Se calcula en el clon del host, sin tocar la rama ni el árbol
    del espacio. Un fallo al comprobar no se toma como "sin choque"."""
    try:
        _git_ci(
            espacio, "fetch", "--quiet", "--no-tags", remoto,
            f"refs/heads/{base}", plazo=60,
        )
    except CiFallo:
        return None
    for referencia in ("FETCH_HEAD", f"refs/remotes/origin/{base}"):
        resultado = _correr(
            [
                "git", "-C", str(espacio), "merge-tree", "--write-tree",
                "--no-messages", "--name-only", referencia, cabeza,
            ],
            plazo=60,
        )
        if resultado.returncode == 0:
            return []
        if resultado.returncode != 1:
            continue
        lineas = [linea.strip() for linea in (resultado.stdout or "").splitlines()]
        nombres = [linea for linea in lineas[1:] if linea]
        return nombres or ["(el choque no dice qué archivos)"]
    return None


def _comprobar_mezcla(
    gh: str, repo: str, remoto: str, base: str, numero: str, cabeza: str,
    espacio: Path, limite: float,
) -> None:
    """Un PR que choca con su base no dispara `pull_request`, y el PR temporal es
    un borrador: GitHub puede dejarlo en `mergeStateStatus: DRAFT`, pero
    `mergeable` sigue siendo `CONFLICTING`. Se corta aquí con los archivos del
    choque antes de esperar un run que no va a llegar."""
    vista = _vista_mezcla(gh, repo, numero, limite)
    mergeable = str(vista.get("mergeable") or "").strip().upper()
    estado = str(vista.get("mergeStateStatus") or "").strip().upper()
    if mergeable != "CONFLICTING" and estado != "DIRTY":
        return
    choques = _archivos_en_conflicto(espacio, remoto, base, cabeza)
    detalle = ", ".join(choques) if choques else "GitHub no detalló los archivos"
    raise CiFallo(
        f"el PR temporal contra {base} no se puede mezclar: chocan {detalle}",
        f"la versión del encargo necesita traer {base} a su rama y resolver el choque; "
        "después volver a pedir ci.correr",
    )


def _limpiar_ci(
    gh: str, repo: str, remoto: str, espacio: Path, rama: str, numero: str,
    base_rama: str = "",
) -> dict:
    """Cierra el PR y borra la rama temporal y la base del diff. Cada paso dice
    si funcionó."""
    limpieza = {
        "rama": rama,
        "base_rama": base_rama,
        "pr": numero,
        "pr_cerrado": False,
        "rama_borrada": False,
        "base_borrada": False,
    }
    if numero:
        try:
            _gh(gh, repo, "pr", "close", numero, "--delete-branch", plazo=60)
            limpieza["pr_cerrado"] = True
        except Exception as exc:  # la limpieza sigue aunque falle un paso
            limpieza["pr_error"] = getattr(exc, "motivo", None) or f"{type(exc).__name__}: {exc}"
    for clave, ref in (("rama", rama), ("base", base_rama)):
        if not ref:
            continue
        try:
            presente = _git_ci(
                espacio, "ls-remote", "--heads", remoto, f"refs/heads/{ref}", plazo=60
            ).strip()
            if presente:
                _git_ci(espacio, "push", "--quiet", remoto, "--delete", f"refs/heads/{ref}", plazo=60)
            limpieza[f"{clave}_borrada"] = True
        except Exception as exc:
            limpieza[f"{clave}_error"] = getattr(exc, "motivo", None) or f"{type(exc).__name__}: {exc}"
    return limpieza


def _barrer_restos(remoto: str, espacio: Path, ident: str) -> None:
    """Un run anterior que murió sin limpiar deja su rama (y con ella su PR, que
    GitHub cierra al borrarla) y su rama base. Se borran las de este encargo
    que ya no pueden pertenecer a un run vivo: el nombre lleva la hora de
    salida, y un run vivo no pasa de CI_MAX_S."""
    import re

    for familia in ("ci-prueba", "ci-base"):
        prefijo = f"refs/heads/{familia}/" + re_sub_ci(ident) + "-"
        try:
            filas = _git_ci(
                espacio, "ls-remote", "--heads", remoto, prefijo + "*", plazo=60
            ).splitlines()
            for fila in filas:
                ref = fila.split("\t")[-1].strip()
                salida = re.search(r"-(\d{9,})$", ref)
                if not ref.startswith(prefijo) or not salida:
                    continue
                if time.time() - int(salida.group(1)) > CI_MAX_S + 300:
                    _git_ci(espacio, "push", "--quiet", remoto, "--delete", ref, plazo=60)
        except CiFallo:
            pass


def re_sub_ci(texto: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.-]", "-", texto)


def _manejar_sigterm():
    """Un SIGTERM (la parada del encargo) se vuelve una salida normal para que
    corra la limpieza. Solo en el hilo principal, que es donde corre la herramienta."""
    import signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        return None

    def interrumpir(signum, frame):
        raise CiFallo(
            "el supervisor detuvo la herramienta de CI; la rama y el PR temporales se cerraron",
            "volver a pedir ci.correr cuando el encargo continúe",
        )

    return signal.signal(signal.SIGTERM, interrumpir)


def _restaurar_sigterm(previo) -> None:
    import signal
    import threading

    if previo is not None and threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, previo)


ROJO_CI = (
    "failure",
    "timed_out",
    "startup_failure",
    "cancelled",
    "action_required",
    "stale",
)
# Un job solo acredita el árbol si terminó en alguno de estos estados. Un
# `cancelled` o un `action_required` no es un verde: la corrida no terminó.
VERDE_CI = ("success", "skipped", "neutral")
COLA_CI = 8 * 1024


def _jobs_pedidos(parametros: dict) -> tuple[str, ...]:
    """Solo puede añadir jobs (`jobs_extra`); el recorte lo decide el diff."""
    texto = str(
        parametros.get("jobs_extra") or parametros.get("jobs") or ""
    ).strip()
    if not texto:
        return ()
    return tuple(x.strip() for x in texto.split(",") if x.strip())


def _log_de_job(
    gh: str,
    repo: str,
    numero_run: str,
    trabajo: dict,
    destino: Path,
    sha: str,
    forzar: bool = False,
    espacio: Path | None = None,
) -> dict:
    """Baja el log literal de un job rojo y deja la copia en el host.

    Para el CI completo solo interesan los logs de los jobs rojos; el revisor
    que pide un job exacto también recibe el suyo aunque esté verde."""
    import re

    nombre = str(trabajo.get("name") or "")
    datos = {
        "nombre": nombre,
        "conclusion": str(trabajo.get("conclusion") or ""),
        "reusado": False,
        "job_id": str(trabajo.get("databaseId") or ""),
        "url": str(trabajo.get("url") or trabajo.get("html_url") or ""),
        "log": "",
        "bytes": 0,
        "cola": "",
    }
    if not datos["job_id"]:
        return datos
    if not forzar and datos["conclusion"] not in ROJO_CI:
        return datos
    try:
        log = _gh(
            gh, repo, "run", "view", numero_run, "--job", datos["job_id"], "--log",
            plazo=300, binario=True,
        )
    except CiFallo as exc:
        datos["error_log"] = getattr(exc, "motivo", None) or str(exc)
        return datos
    if len(log) > MAX_LOG:
        datos["error_log"] = f"el log del job supera {MAX_LOG} bytes"
        return datos
    archivo = f"{re.sub(r'[^A-Za-z0-9_.-]', '-', nombre)}-{sha[:7]}-run{numero_run}.log"
    copia = destino / "remoto" / archivo
    _escribir_seguro(copia, log)
    texto = log.decode("utf-8", errors="replace")
    datos.update(
        {
            "log": str(copia),
            "log_nombre": archivo,
            "log_host": str(copia),
            "bytes": copia.stat().st_size,
            "cola": texto[-COLA_CI:],
        }
    )
    if espacio is not None:
        # El revisor y el responsable leen el espacio, no el host. Los logs de
        # los jobs rojos se publican ahí además de la copia del host.
        publico = Path(espacio) / ".interno" / "logs" / archivo
        try:
            _escribir_seguro(publico, log, raiz=Path(espacio))
        except (OSError, ValueError) as exc:
            datos["error_publicacion"] = str(exc)
        else:
            datos["log"] = str(publico)
    return datos


def _correr_ci(
    concedido: dict,
    parametros: dict,
    espacio: Path,
    destino: Path,
    contexto: dict | None = None,
) -> dict:
    """Corre el workflow de pruebas sobre la versión vigente del producto.

    Con `job` exacto devuelve el log literal de ese job, como siempre. Con
    `job="todos"` devuelve la conclusión de cada job, los logs de los rojos y el
    árbol del commit temporal. `base_arbol` es el último árbol verde: el commit
    temporal se cuelga de él y el workflow corre solo los jobs que la
    corrección toca. El mensaje no puede recortar; `jobs_extra` solo añade.
    Usa el acceso de gh y git que ya tiene el supervisor; las ramas base y
    temporal y el PR se cierran siempre."""
    import re
    import shutil

    job = str(parametros.get("job") or "").strip()
    todos = job == "todos"
    if not job or (not todos and not re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", job)):
        raise CiFallo(
            "ci.correr necesita el parámetro job con el nombre exacto del job "
            "(por ejemplo windows) o todos",
            "volver a pedirla con --parametro job=<nombre>",
        )
    extra = _jobs_pedidos(parametros)
    for nombre in extra:
        if not re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", nombre):
            raise CiFallo(
                f"el parámetro jobs_extra trae un nombre inválido: {nombre!r}",
                "usar los nombres exactos de los jobs separados por comas",
            )
    base_arbol = str(parametros.get("base_arbol") or "").strip()
    if espacio is None or not (Path(espacio) / ".git").exists():
        raise CiFallo("el espacio del encargo no es un clon git", "pedir al host que prepare el repo de entrega")
    espacio = Path(espacio)
    gh = shutil.which("gh")
    if not gh:
        raise CiFallo(
            "gh no está instalado en el host del supervisor: no hay forma de correr el CI de GitHub",
            "instalar y autenticar gh en el host, o pedir al operador el log del job",
        )
    if not shutil.which("git"):
        raise CiFallo("git no está instalado en el host del supervisor", "instalar git en el host")
    repo, remoto = _repo_de(concedido, espacio, contexto)
    base = str(concedido.get("base") or "main")
    workflow = _workflow_de(concedido, repo)
    limite = time.time() + CI_MAX_S

    ident = f"{espacio.name}"
    _barrer_restos(remoto, espacio, ident)
    from motor.encargos import trabajos_ci

    mensaje = f"ci-prueba {ident}"
    pedidos = list(extra)
    if not todos:
        canonico = trabajos_ci.trabajo(job)
        if canonico in trabajos_ci.TRABAJOS and canonico not in pedidos:
            pedidos.append(canonico)
    if extra or (not todos and pedidos):
        mensaje += " jobs_extra=" + ",".join(dict.fromkeys(pedidos))
    if not todos and not base_arbol:
        # La inspección de un job exacto no acredita el verde del producto:
        # compara contra HEAD para que solo corra lo que cambió más ese job.
        base_arbol = _git_ci(
            espacio, "rev-parse", "--verify", "HEAD^{tree}"
        ).strip()
    base_sha, base_arbol = _commit_base(espacio, destino, ident, base_arbol)
    sha, arbol = _commit_temporal(espacio, destino, ident, mensaje, base_sha)
    marca = int(time.time())
    rama = f"ci-prueba/{re_sub_ci(ident)}-{sha[:7]}-{marca}"
    base_rama = f"ci-base/{re_sub_ci(ident)}-{sha[:7]}-{marca}"
    numero = ""
    previo = _manejar_sigterm()
    try:
        _git_ci(
            espacio, "push", "--quiet", remoto,
            f"{base_sha}:refs/heads/{base_rama}", plazo=180,
        )
        _git_ci(espacio, "push", "--quiet", remoto, f"{sha}:refs/heads/{rama}", plazo=180)
        url_pr = _gh(
            gh, repo, "pr", "create", "--draft", "--base", base_rama, "--head", rama,
            "--title", f"ci-prueba: {ident} {sha[:7]}",
            "--body", "Corrida temporal del CI. Se cierra sola.",
            plazo=120,
        ).strip().splitlines()[-1]
        numero = url_pr.rstrip("/").rsplit("/", 1)[-1]

        def vigilar_conflicto() -> None:
            # GitHub puede calcular `mergeable` tarde: se revisa mientras se
            # busca el run para no cambiar un choque por un "no inició".
            _comprobar_mezcla(gh, repo, remoto, base_rama, numero, sha, espacio, 0)

        _comprobar_mezcla(
            gh, repo, remoto, base_rama, numero, sha, espacio,
            min(CI_MEZCLA_S, limite - time.time()),
        )
        choques = _archivos_en_conflicto(espacio, remoto, base, sha)
        if choques is None:
            raise CiFallo(
                f"no se pudo comprobar si el producto choca con {base}",
                "reintentar; el CI no se lanza sobre un árbol sin saber si choca",
            )
        if choques:
            raise CiFallo(
                f"el producto no se puede mezclar con {base}: chocan {', '.join(choques)}",
                f"traer {base} a la rama y resolver el choque; después volver a pedir ci.correr",
            )
        runs = _esperar_runs(
            gh, repo, [workflow] if workflow else [], sha,
            limite - time.time(), vigilar=vigilar_conflicto,
        )
        vistas = [_vista_completa(gh, repo, run, limite) for run in runs]
        url_run = str(
            (vistas[0][1].get("url") if vistas else "")
            or (runs[0].get("url") if runs else "")
        )
        if not todos:
            elegido = None
            for numero_vista, vista in vistas:
                for trabajo in vista.get("jobs") or []:
                    if str(trabajo.get("name")) == job:
                        elegido = (numero_vista, trabajo, vista)
                        break
                if elegido is not None:
                    break
            if elegido is None:
                nombres = ", ".join(
                    sorted(
                        str(j.get("name"))
                        for _, vista in vistas
                        for j in vista.get("jobs") or []
                    )
                ) or "ninguno"
                raise CiFallo(
                    f"los runs del commit no tienen un job llamado {job!r}; "
                    f"jobs: {nombres}",
                    "volver a pedirla con el nombre exacto de uno de esos jobs",
                )
            numero_run, trabajo_elegido, vista_elegida = elegido
            datos_job = _log_de_job(
                gh, repo, numero_run, trabajo_elegido, destino, sha, forzar=True
            )
            if datos_job.get("error_log"):
                raise CiFallo(datos_job["error_log"], "pedir otro job")
            resultado = {
                "job": job,
                "conclusion": datos_job["conclusion"],
                "sha": sha,
                "arbol": arbol,
                "run": numero_run,
                "url": str(vista_elegida.get("url") or url_run),
                "rama": rama,
                "base": base,
                "base_rama": base_rama,
                "jobs": [datos_job],
                "fallas": (
                    [job] if datos_job["conclusion"] in ROJO_CI else []
                ),
                "log_nombre": datos_job.get("log_nombre", ""),
                "log_host": datos_job.get("log_host", ""),
                "log": datos_job.get("log", ""),
                "bytes": datos_job.get("bytes", 0),
                "cola": datos_job.get("cola", ""),
            }
            copia = Path(resultado["log"]) if resultado["log"] else None
        else:
            from motor.encargos import trabajos_ci

            resumen = []
            for numero_vista, vista in vistas:
                for trabajo in vista.get("jobs") or []:
                    datos_job = _log_de_job(
                        gh, repo, numero_vista, trabajo, destino, sha, espacio=espacio
                    )
                    canonico = trabajos_ci.trabajo(datos_job["nombre"])
                    datos_job["trabajo"] = canonico
                    datos_job["run"] = numero_vista
                    datos_job["reusado"] = datos_job["conclusion"] == "skipped"
                    resumen.append(datos_job)
            gate_intentado = False
            tiene_quality = False
            if workflow == "pruebas.yml":
                try:
                    _git_ci(
                        espacio,
                        "cat-file",
                        "-e",
                        f"{sha}:.github/workflows/quality.yml",
                    )
                    tiene_quality = True
                except CiFallo:
                    tiene_quality = False
            if tiene_quality:
                # `quality.yml` corre en el mismo PR temporal. Se espera para
                # que el CI del producto incluya `gate`; si no aparece, el PR
                # real lo correrá y el motor no lo dará por verde.
                try:
                    run_calidad = _esperar_run(
                        gh,
                        repo,
                        "quality.yml",
                        sha,
                        min(CI_QUALITY_S, max(1.0, limite - time.time())),
                    )
                    num_calidad, vista_calidad = _vista_completa(
                        gh, repo, run_calidad, limite
                    )
                    gate_intentado = True
                    vistos = {
                        str(j.get("trabajo") or trabajos_ci.trabajo(j["nombre"]))
                        for j in resumen
                    }
                    for trabajo in vista_calidad.get("jobs") or []:
                        datos_job = _log_de_job(
                            gh, repo, num_calidad, trabajo, destino, sha,
                            espacio=espacio,
                        )
                        canonico = trabajos_ci.trabajo(datos_job["nombre"])
                        if canonico != "gate" or canonico in vistos:
                            continue
                        datos_job["trabajo"] = canonico
                        resumen.append(datos_job)
                        vistos.add(canonico)
                except CiFallo:
                    gate_intentado = False
            esperados = list(
                trabajos_ci.TRABAJOS if workflow == "pruebas.yml" else ()
            )
            if gate_intentado:
                esperados.append("gate")
            presentes = {
                str(j.get("trabajo") or trabajos_ci.trabajo(j["nombre"]))
                for j in resumen
            }
            for falta in esperados:
                if falta in presentes:
                    continue
                resumen.append(
                    {
                        "nombre": falta,
                        "trabajo": falta,
                        "conclusion": "",
                        "reusado": False,
                        "job_id": "",
                        "url": "",
                        "log": "",
                        "bytes": 0,
                        "cola": "",
                        "error_log": "el run no reportó este job",
                    }
                )
            # Un job acredita el árbol solo si terminó verde. `cancelled` no es
            # un verde: la corrida no completó y su resultado no sirve como
            # base para reusar jobs ni para cerrar el PR.
            fallas = list(
                dict.fromkeys(
                    j["nombre"]
                    for j in resumen
                    if not j.get("reusado")
                    and j["conclusion"] not in ("success", "neutral")
                )
            )
            if not workflow:
                # Sin workflow explícito el veredicto cubre todos los runs de
                # pull_request del SHA. Un run sin jobs que no acabó verde
                # tampoco puede contar como verde.
                for numero_vista, vista_run in vistas:
                    if (
                        str(vista_run.get("conclusion") or "") not in VERDE_CI
                        and not any(j.get("run") == numero_vista for j in resumen)
                    ):
                        fallas.append(f"workflow:{numero_vista}")
            if fallas:
                conclusion = "failure"
            elif all(
                j.get("reusado") or j["conclusion"] in VERDE_CI
                for j in resumen
            ):
                conclusion = "success"
            else:
                conclusion = str(
                    (vistas[0][1].get("conclusion") if vistas else "") or ""
                )
            resultado = {
                "job": "todos",
                "conclusion": conclusion,
                "sha": sha,
                "arbol": arbol,
                "run": vistas[0][0] if vistas else "",
                "url": url_run,
                "rama": rama,
                "base": base,
                "base_rama": base_rama,
                "jobs": resumen,
                "fallas": fallas,
                "pedidos": list(pedidos),
                "log_nombre": "",
                "log_host": "",
                "log": "",
                "bytes": 0,
                "cola": "",
            }
            copia = None
    finally:
        # Siempre, sea cual sea la salida: un CiFallo, un error inesperado o un
        # SIGTERM del supervisor. La rama y el PR temporales no se quedan.
        limpieza = _limpiar_ci(gh, repo, remoto, espacio, rama, numero, base_rama)
        _restaurar_sigterm(previo)
    resultado["limpieza"] = limpieza
    evidencia = {
        "tipo": "ci",
        "sha": sha,
        "arbol": arbol,
        "run": resultado["run"],
        "job": job,
        "conclusion": resultado["conclusion"],
        "url": resultado["url"],
        "jobs": [
            {
                "nombre": j["nombre"],
                "conclusion": j["conclusion"],
                "reusado": bool(j.get("reusado")),
                "log": j.get("log", ""),
                "url": j.get("url", ""),
                "cola": j.get("cola", "") if j["nombre"] in resultado["fallas"] else "",
            }
            for j in resultado["jobs"]
        ],
        "fallas": resultado["fallas"],
        "limpieza": resultado["limpieza"],
    }
    if copia is not None:
        evidencia.update(
            {
                "ruta_copia": str(copia),
                "sha256": _huella(copia),
                "bytes": resultado["bytes"],
            }
        )
    return {"estado": "ok", "datos": resultado, "evidencia": evidencia}


def _entero_pr(valor) -> int:
    texto = str(valor or "").strip()
    if not texto.isdigit() or int(texto) <= 0 or len(texto) > 12:
        raise CiFallo(
            "ci.log necesita el parámetro pr con el número del PR del encargo",
            "volver a pedirla con --parametro pr=<numero>",
        )
    return int(texto)


def _sha_esperado(valor) -> str:
    import re

    texto = str(valor or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", texto):
        raise CiFallo(
            "ci.log necesita el parámetro sha con el SHA esperado (7 a 40 hex)",
            "volver a pedirla con --parametro sha=<commit>",
        )
    return texto


def _correr_ci_log(
    concedido: dict,
    parametros: dict,
    espacio: Path | None,
    destino: Path,
    contexto: dict | None,
) -> dict:
    """Lee el log de un job de una corrida del PR del encargo. No crea ramas ni
    PRs: solo consulta el recurso ci y publica la copia en el espacio."""
    import re
    import shutil

    job = str(parametros.get("job") or "").strip()
    if not job or not re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", job):
        raise CiFallo(
            "ci.log necesita el parámetro job con el nombre exacto del job",
            "volver a pedirla con --parametro job=<nombre>",
        )
    pr = _entero_pr(parametros.get("pr"))
    sha_pedido = _sha_esperado(parametros.get("sha"))
    gh = shutil.which("gh")
    if not gh:
        raise CiFallo("gh no está instalado en el host del supervisor", "instalar y autenticar gh")
    repo, _remoto = _repo_de(concedido, espacio or Path.cwd(), contexto)
    workflow = _workflow_de(concedido, repo)
    if not isinstance(contexto, dict):
        raise CiFallo(
            "ci.log necesita el PR del encargo y el host no lo trajo",
            "pedir la operación por el buzón del encargo",
        )
    autorizado = int(contexto.get("pr") or 0)
    if not autorizado:
        raise CiFallo(
            "el encargo todavía no tiene un PR de entrega; ci.log solo lee el PR del encargo",
            "abrir la entrega del encargo y volver a pedir el log",
        )
    if pr != autorizado:
        raise CiFallo(
            f"el PR #{pr} no es el del encargo (#{autorizado}); ci.log no lee PRs ajenos",
            "pedir el log del PR del encargo",
        )
    rama = str(contexto.get("rama") or "").strip()
    repo_contexto = _repo_github(str(contexto.get("repo") or ""))
    if repo_contexto and repo_contexto != repo:
        raise CiFallo(
            f"el PR del encargo es de {repo_contexto}, no de {repo}; ci.log no cruza repos",
            "pedir el log con el recurso ci del repo del encargo",
        )
    vista_pr = _json_gh(
        gh, repo, "pr", "view", str(pr),
        "--json", "number,state,headRefName,headRefOid,headRepository",
    )
    if not isinstance(vista_pr, dict):
        raise CiFallo("gh no devolvió el PR del encargo", "reintentar")
    rama_pr = str(vista_pr.get("headRefName") or "").strip()
    if rama and rama_pr != rama:
        raise CiFallo(
            f"el PR #{pr} es de la rama {rama_pr or '(vacia)'}, no de {rama}; ci.log no lee PRs ajenos",
            "pedir el log del PR de la rama del encargo",
        )
    repo_pr = ""
    if isinstance(vista_pr.get("headRepository"), dict):
        repo_pr = _repo_github(
            str(vista_pr["headRepository"].get("nameWithOwner") or "")
        )
    if repo_pr and repo_pr != repo:
        raise CiFallo(
            f"el PR #{pr} viene de {repo_pr}, no de {repo}; ci.log no cruza repos",
            "pedir el log del PR del encargo",
        )
    head = str(vista_pr.get("headRefOid") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise CiFallo("gh no devolvió el SHA vigente del PR", "reintentar")
    if len(sha_pedido) < 40:
        if not head.startswith(sha_pedido):
            raise CiFallo(
                f"el PR #{pr} está en {head[:12]}; no hay corrida para {sha_pedido}",
                "pedir el log del SHA vigente del PR",
            )
        sha_buscar = head
    else:
        sha_buscar = sha_pedido

    def runs_de(sha: str) -> list[dict]:
        argumentos = [
            "run", "list", "--commit", sha,
            "--json", "databaseId,status,conclusion,headSha,headBranch,event,url,workflowName",
            "--limit", "20",
        ]
        if workflow:
            argumentos += ["--workflow", workflow]
        return [
            r for r in (_json_gh(gh, repo, *argumentos) or [])
            if str(r.get("headSha") or "").lower() == sha
        ]

    runs = [
        r for r in runs_de(sha_buscar)
        if str(r.get("event") or "") in ("", "pull_request")
        and (workflow or not _es_ai_review(r))
    ]
    if rama_pr:
        runs = [
            r for r in runs
            if str(r.get("headBranch") or "") in ("", rama_pr)
        ]
    if not runs:
        disponibles = sorted(
            {
                str(r.get("headSha") or "")[:12]
                for r in runs_de(head)
                if r.get("headSha")
            }
        )
        detalle = f"; corridas del PR: {', '.join(disponibles)}" if disponibles else ""
        raise CiFallo(
            f"no hay corrida del SHA {sha_buscar[:12]} en el PR #{pr}; "
            f"el PR está en {head[:12]}{detalle}",
            "pedir el log del SHA vigente del PR",
        )
    elegido = None
    vista = None
    numero_run = ""
    nombres = set()
    for run in sorted(
        runs, key=lambda r: int(r.get("databaseId") or 0), reverse=True
    ):
        numero = str(run["databaseId"])
        vista_run = _json_gh(
            gh, repo, "run", "view", numero,
            "--json", "status,conclusion,jobs,url",
        )
        if not isinstance(vista_run, dict):
            raise CiFallo("gh no devolvió la corrida del PR", "reintentar")
        trabajos = vista_run.get("jobs") or []
        nombres.update(str(j.get("name")) for j in trabajos)
        trabajo = next((j for j in trabajos if str(j.get("name")) == job), None)
        if trabajo is not None:
            elegido, vista, numero_run = trabajo, vista_run, numero
            break
    if elegido is None:
        raise CiFallo(
            f"los runs del PR #{pr} no tienen un job llamado {job!r}; "
            f"jobs: {', '.join(sorted(nombres)) or 'ninguno'}",
            "volver a pedirla con el nombre exacto de uno de esos jobs",
        )
    log = _gh(
        gh, repo, "run", "view", numero_run, "--job", str(elegido["databaseId"]), "--log",
        plazo=300, binario=True,
    )
    if len(log) > MAX_LOG:
        raise CiFallo(f"el log del job supera {MAX_LOG} bytes", "pedir otro job")
    nombre = f"{re.sub(r'[^A-Za-z0-9_.-]', '-', job)}-{sha_buscar[:7]}-run{numero_run}.log"
    copia = destino / "remoto" / nombre
    _escribir_seguro(copia, log)
    datos = {
        "pr": pr,
        "job": job,
        "sha": sha_buscar,
        "run": numero_run,
        "estado": str(vista.get("status") or ""),
        "conclusion": str(elegido.get("conclusion") or vista.get("conclusion") or ""),
        "url": str(vista.get("url") or ""),
        "log_nombre": nombre,
        "log_host": str(copia),
        "log": str(copia),
        "bytes": copia.stat().st_size,
        "cola": log[-MAX_TEXTO:].decode("utf-8", errors="replace"),
    }
    return {
        "estado": "ok",
        "datos": datos,
        "evidencia": {
            "tipo": "ci_log",
            "repo": repo,
            "pr": pr,
            "sha": sha_buscar,
            "run": numero_run,
            "job": job,
            "estado": datos["estado"],
            "conclusion": datos["conclusion"],
            "url": datos["url"],
            "ruta_copia": str(copia),
            "sha256": _huella(copia),
            "bytes": datos["bytes"],
        },
    }


def recursos_observables(recursos: dict[str, dict]) -> tuple[str, ...]:
    """Recursos cuyo cambio detecta `huella_recurso`: sqlite y remotos por fichero."""
    return tuple(
        sorted(
            nombre
            for nombre, concedido in recursos.items()
            if concedido.get("tipo") == "sqlite"
            or (
                concedido.get("tipo") == "remoto"
                and str(concedido.get("transporte") or "fichero") != "http"
            )
        )
    )


def huella_recurso(
    recursos: dict[str, dict], operacion: str, recurso: str, parametros: dict
) -> str:
    """Estado observable de lo que la operación leería: tamaño, fecha y hash.

    Vacío si el host no puede observarlo (recurso http, ruta ausente, ilegible).
    Pedir de nuevo un recurso cuya huella cambió no es repetir la inspección.
    """
    concedido = recursos.get(recurso)
    if concedido is None:
        return ""
    try:
        if concedido.get("tipo") == "sqlite":
            origen = _ruta_absoluta(concedido.get("ruta") or "")
        elif operacion == REMOTO and str(concedido.get("transporte") or "fichero") != "http":
            raiz = _ruta_absoluta(concedido.get("raiz") or "")
            origen = (raiz / _ruta_relativa(str(parametros.get("ruta") or ""))).resolve()
            if not origen.is_relative_to(raiz.resolve()):
                return ""
        else:
            return ""
        info = origen.stat()
        if not origen.is_file():
            return ""
        digest = _huella(origen) if info.st_size <= MAX_BYTES else ""
    except (ValueError, OSError, ConfigInvalida):
        return ""
    return f"{info.st_size}:{info.st_mtime_ns}:{digest}"


def ejecutar(
    recursos: dict[str, dict],
    operacion: str,
    recurso: str,
    parametros: dict,
    destino: Path,
    autorizado: bool = False,
    espacio: Path | None = None,
    contexto: dict | None = None,
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
        if operacion == CI:
            try:
                return _correr_ci(concedido, parametros, espacio, destino, contexto)
            except (CiFallo, ConfigInvalida):
                raise
            except Exception as exc:
                return {
                    "estado": "error",
                    "motivo": f"fallo inesperado en ci.correr: {type(exc).__name__}: {exc}",
                    "alternativa": "la rama y el PR temporales se cerraron; reintentar o pedir el log al operador",
                }
        if operacion == CI_LOG:
            try:
                return _correr_ci_log(concedido, parametros, espacio, destino, contexto)
            except (CiFallo, ConfigInvalida):
                raise
            except Exception as exc:
                return {
                    "estado": "error",
                    "motivo": f"fallo inesperado en ci.log: {type(exc).__name__}: {exc}",
                    "alternativa": "reintentar o pedir el log al operador",
                }
        if operacion == REMOTO:
            relativa = _ruta_relativa(str(parametros.get("ruta") or ""))
            if str(concedido.get("transporte") or "fichero") == "http":
                return _leer_http(concedido, relativa, destino)
            return _leer_archivo(_ruta_absoluta(concedido.get("raiz") or ""), relativa, destino)
    except CiFallo as exc:
        return {
            "estado": "error",
            "motivo": exc.motivo,
            "alternativa": exc.alternativa or "revisar la causa antes de reintentar",
        }
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
            espacio=Path(spec["espacio"]) if spec.get("espacio") else None,
            contexto=spec.get("contexto"),
        )
    destino.mkdir(parents=True, exist_ok=True)
    _escribir_seguro(
        destino / "resultado.json",
        json.dumps(resultado, ensure_ascii=False, default=str).encode("utf-8"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
