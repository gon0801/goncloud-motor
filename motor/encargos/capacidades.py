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
CI = "ci.correr"
DECISION = "decision.registrar"

OPERACIONES_SIN_RECURSO = (DECISION,)
# Operaciones cuyo resultado incluye un archivo que el supervisor publica en el espacio.
CON_ARCHIVO = (SNAPSHOT, CI, REMOTO)

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
            operaciones = [CI]
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


def _repo_de(concedido: dict, espacio: Path) -> tuple[str, str]:
    """(owner/nombre para gh, remoto de git). Sale del recurso o del origin del espacio."""
    import re

    remoto = str(concedido.get("remoto") or "").strip()
    if not remoto:
        remoto = _git_ci(espacio, "remote", "get-url", "origin").strip()
    repo = str(concedido.get("repo") or "").strip()
    if not repo:
        m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", remoto)
        if not m:
            raise CiFallo(
                "el recurso ci no declara repo y el origin del espacio no es de GitHub",
                "pedir al host que declare repo en el recurso ci",
            )
        repo = m.group(1)
    return repo, remoto


_INTERNO = (".interno", "__pycache__", ".runtime")


def _es_interno(ruta: str) -> bool:
    """Lo que no es producto: lo interno del encargo y lo generado."""
    return any(parte in _INTERNO for parte in ruta.split("/"))


def _commit_temporal(espacio: Path, destino: Path, ident: str) -> tuple[str, str]:
    """Commit con la versión vigente del producto (con o sin commit propio) en un
    índice aparte: ni el índice ni la rama ni el árbol del espacio se tocan."""
    indice = destino / "indice-ci"
    indice.unlink(missing_ok=True)
    env = {
        "GIT_INDEX_FILE": str(indice),
        "GIT_AUTHOR_NAME": "motor-ci",
        "GIT_AUTHOR_EMAIL": "motor-ci@localhost",
        "GIT_COMMITTER_NAME": "motor-ci",
        "GIT_COMMITTER_EMAIL": "motor-ci@localhost",
    }
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
        arbol = _git_ci(espacio, "write-tree", env=env).strip()
        sha = _git_ci(
            espacio, "commit-tree", arbol, "-p", cabeza, "-m", f"ci-prueba {ident}", env=env
        ).strip()
    finally:
        indice.unlink(missing_ok=True)
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
) -> list[str]:
    """Nombres del choque que GitHub ya reportó. Se calculan en el clon del host
    contra la base vigente, sin tocar la rama ni el árbol del espacio."""
    try:
        _git_ci(
            espacio, "fetch", "--quiet", "--no-tags", remoto,
            f"refs/heads/{base}", plazo=60,
        )
    except CiFallo:
        pass
    for referencia in ("FETCH_HEAD", f"refs/remotes/origin/{base}"):
        resultado = _correr(
            [
                "git", "-C", str(espacio), "merge-tree", "--write-tree",
                "--no-messages", "--name-only", referencia, cabeza,
            ],
            plazo=60,
        )
        if resultado.returncode not in (0, 1):
            continue
        lineas = [linea.strip() for linea in (resultado.stdout or "").splitlines()]
        nombres = [linea for linea in lineas[1:] if linea]
        if nombres:
            return nombres
    return []


def _comprobar_mezcla(
    gh: str, repo: str, remoto: str, base: str, numero: str, cabeza: str,
    espacio: Path, limite: float,
) -> None:
    """Un PR que choca con su base no dispara `pull_request`: esperar el run
    gastaría el plazo sin causa. Se corta aquí con los archivos del choque."""
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


def _limpiar_ci(gh: str, repo: str, remoto: str, espacio: Path, rama: str, numero: str) -> dict:
    """Cierra el PR y borra la rama temporal. Cada paso dice si funcionó."""
    limpieza = {"rama": rama, "pr": numero, "pr_cerrado": False, "rama_borrada": False}
    if numero:
        try:
            _gh(gh, repo, "pr", "close", numero, "--delete-branch", plazo=60)
            limpieza["pr_cerrado"] = True
        except Exception as exc:  # la limpieza sigue aunque falle un paso
            limpieza["pr_error"] = getattr(exc, "motivo", None) or f"{type(exc).__name__}: {exc}"
    try:
        presente = _git_ci(espacio, "ls-remote", "--heads", remoto, f"refs/heads/{rama}", plazo=60).strip()
        if presente:
            _git_ci(espacio, "push", "--quiet", remoto, "--delete", f"refs/heads/{rama}", plazo=60)
        limpieza["rama_borrada"] = True
    except Exception as exc:
        limpieza["rama_error"] = getattr(exc, "motivo", None) or f"{type(exc).__name__}: {exc}"
    return limpieza


def _barrer_restos(remoto: str, espacio: Path, ident: str) -> None:
    """Un run anterior que murió sin limpiar deja su rama (y con ella su PR, que
    GitHub cierra al borrarla). Se borran las de este encargo que ya no pueden
    pertenecer a un run vivo: el nombre lleva la hora de salida, y un run vivo
    no pasa de CI_MAX_S. La rama de un run en curso no se toca."""
    import re

    prefijo = "refs/heads/ci-prueba/" + re_sub_ci(ident) + "-"
    try:
        filas = _git_ci(espacio, "ls-remote", "--heads", remoto, prefijo + "*", plazo=60).splitlines()
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


def _correr_ci(concedido: dict, parametros: dict, espacio: Path, destino: Path) -> dict:
    """Corre el workflow de pruebas sobre la versión vigente del producto y
    devuelve el log literal del job pedido, con el SHA y el número de run. Usa el
    acceso de gh y git que ya tiene el supervisor; la rama y el PR temporales se
    cierran siempre."""
    import re
    import shutil

    job = str(parametros.get("job") or "").strip()
    if not job or not re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", job):
        raise CiFallo(
            "ci.correr necesita el parámetro job con el nombre exacto del job (por ejemplo jaula-linux)",
            "volver a pedirla con --parametro job=<nombre>",
        )
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
    repo, remoto = _repo_de(concedido, espacio)
    base = str(concedido.get("base") or "main")
    workflow = str(concedido.get("workflow") or "pruebas.yml")
    limite = time.time() + CI_MAX_S

    ident = f"{espacio.name}"
    _barrer_restos(remoto, espacio, ident)
    sha, arbol = _commit_temporal(espacio, destino, ident)
    rama = f"ci-prueba/{re_sub_ci(ident)}-{sha[:7]}-{int(time.time())}"
    numero = ""
    previo = _manejar_sigterm()
    try:
        _git_ci(espacio, "push", "--quiet", remoto, f"{sha}:refs/heads/{rama}", plazo=180)
        url_pr = _gh(
            gh, repo, "pr", "create", "--draft", "--base", base, "--head", rama,
            "--title", f"ci-prueba: {ident} {sha[:7]}",
            "--body", "Corrida temporal del CI pedida por el revisor. Se cierra sola.",
            plazo=120,
        ).strip().splitlines()[-1]
        numero = url_pr.rstrip("/").rsplit("/", 1)[-1]

        def vigilar_conflicto() -> None:
            # GitHub puede calcular `mergeable` tarde: se revisa mientras se
            # busca el run para no cambiar un choque por un "no inició".
            _comprobar_mezcla(gh, repo, remoto, base, numero, sha, espacio, 0)

        _comprobar_mezcla(
            gh, repo, remoto, base, numero, sha, espacio,
            min(CI_MEZCLA_S, limite - time.time()),
        )
        run = _esperar_run(
            gh, repo, workflow, sha, limite - time.time(), vigilar=vigilar_conflicto
        )
        numero_run = str(run["databaseId"])
        while True:
            vista = _json_gh(gh, repo, "run", "view", numero_run, "--json", "status,conclusion,jobs,url")
            if str(vista.get("status")) == "completed":
                break
            if time.time() > limite:
                raise CiFallo(
                    f"el run {numero_run} no terminó en {int(CI_MAX_S // 60)} minutos ({vista.get('url') or ''})",
                    "reintentar más tarde; el PR temporal se cerró",
                )
            time.sleep(CI_SONDEO_S)
        trabajos = vista.get("jobs") or []
        elegido = next((j for j in trabajos if str(j.get("name")) == job), None)
        if elegido is None:
            nombres = ", ".join(sorted(str(j.get("name")) for j in trabajos)) or "ninguno"
            raise CiFallo(
                f"el run {numero_run} no tiene un job llamado {job!r}; jobs: {nombres}",
                "volver a pedirla con el nombre exacto de uno de esos jobs",
            )
        log = _gh(
            gh, repo, "run", "view", numero_run, "--job", str(elegido["databaseId"]), "--log",
            plazo=300, binario=True,
        )
        if len(log) > MAX_LOG:
            raise CiFallo(f"el log del job supera {MAX_LOG} bytes", "pedir otro job")
        nombre = f"{re.sub(r'[^A-Za-z0-9_.-]', '-', job)}-{sha[:7]}-run{numero_run}.log"
        copia = destino / "remoto" / nombre
        _escribir_seguro(copia, log)
        resultado = {
            "job": job,
            "conclusion": str(elegido.get("conclusion") or vista.get("conclusion") or ""),
            "sha": sha,
            "arbol": arbol,
            "run": numero_run,
            "url": str(vista.get("url") or run.get("url") or ""),
            "rama": rama,
            "base": base,
            "log_nombre": nombre,
            "log_host": str(copia),
            "log": str(copia),
            "bytes": copia.stat().st_size,
            "cola": log[-MAX_TEXTO:].decode("utf-8", errors="replace"),
        }
    finally:
        # Siempre, sea cual sea la salida: un CiFallo, un error inesperado o un
        # SIGTERM del supervisor. La rama y el PR temporales no se quedan.
        limpieza = _limpiar_ci(gh, repo, remoto, espacio, rama, numero)
        _restaurar_sigterm(previo)
    resultado["limpieza"] = limpieza
    return {
        "estado": "ok",
        "datos": resultado,
        "evidencia": {
            "tipo": "ci",
            "sha": sha,
            "run": resultado["run"],
            "job": job,
            "conclusion": resultado["conclusion"],
            "url": resultado["url"],
            "ruta_copia": str(copia),
            "sha256": _huella(copia),
            "bytes": resultado["bytes"],
            "limpieza": resultado["limpieza"],
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
                return _correr_ci(concedido, parametros, espacio, destino)
            except (CiFallo, ConfigInvalida):
                raise
            except Exception as exc:
                return {
                    "estado": "error",
                    "motivo": f"fallo inesperado en ci.correr: {type(exc).__name__}: {exc}",
                    "alternativa": "la rama y el PR temporales se cerraron; reintentar o pedir el log al operador",
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
        )
    destino.mkdir(parents=True, exist_ok=True)
    _escribir_seguro(
        destino / "resultado.json",
        json.dumps(resultado, ensure_ascii=False, default=str).encode("utf-8"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
