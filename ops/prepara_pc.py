"""Prepara la PC (Windows) y las Mac con las mismas capacidades para encargos.

Córralo desde el código instalado por el flujo de etiquetas (C:\\Users\\ehven\\motor
tras ops/instala-todo.sh en la PC, ~/motor en las Mac), no desde un checkout a
mano: la llave Run de Windows queda atada al ops/ del directorio desde donde se
corra este script.

Deja, de forma idempotente (correrlo dos veces no cambia nada la segunda):

- Java temurin 25 (zip GA de Adoptium en Windows, tar.gz en Mac) y
  tla2tools.jar 1.7.4 con el sha256 que fija verificacion/inspeccion/verificar.sh,
  ambos en <base>/herramientas/. La política de versión de Java es la misma que
  `java@temurin-25` en mise: el último GA de la 25. Un parche nuevo de temurin 25
  sí actualiza en la siguiente corrida; el jar está fijado por su suma.
- En Windows, JAVA y TLA2TOOLS persistentes para el usuario (setx), apuntando a
  lo anterior: es el camino sin mise que el propio verificar.sh documenta
  (`exporta TLA2TOOLS y JAVA`). No toca PATH del sistema ni secretos.
- En Mac, un enlace estable <base>/herramientas/java/actual al JDK instalado.
  Ni launchd ni un shell de servicio heredan variables de la terminal, así que
  verificar.sh y `motor director capacidades` buscan ahí sin necesitarlas.
- <base>/capacidades.json con el recurso ci-github y la carpeta
  <base>/evidencia-ci que declara, sin pisar los recursos que el operador ya
  haya declarado. Al volver a preparar una máquina se quita ci-linux, que ya no
  se usa: el CI corre en el PR real.
- En Windows, la llave HKCU\\...\\Run\\GoncloudMotorEncargos lanzando el
  vigilante (ops/iniciar_encargos_pc.py) en lugar de supervisor.ps1 directo.
  supervisor.ps1 no se borra: sigue siendo lo que el vigilante lanza y vigila
  (fixed_paths).

Modos:
- Sin banderas: deja lo que falte y reporta cada paso.
- --comprobar: solo comprueba y dice qué falta; no toca nada.
- --verificar [cfg]: además corre verificar.sh con las herramientas dejadas.
  Antes de correrlo, normaliza a LF verificar.sh, el modelo y la cfg si un
  checkout CRLF los trajo así.
- --json: imprime el reporte como JSON.

Nunca lee, escribe ni imprime secretos: secretos.env no se toca. Fuera de
herramientas/ solo escribe capacidades.json, evidencia-ci, la llave Run y las
variables TLA2TOOLS/JAVA del usuario en Windows.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASE = ".motor-encargos"
USER_AGENT = "goncloud-motor-prepara-pc/1.0"
TLC_URL = "https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar"
TLC_SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
TLC_VERSION = "1.7.4"
TEMURIN_API = (
    "https://api.adoptium.net/v3/assets/latest/25/hotspot"
    "?architecture=x64&image_type=jdk&os=windows&vendor=eclipse"
)
LLAVE = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
NOMBRE_LLAVE = "GoncloudMotorEncargos"
VIGILANTE = "iniciar_encargos_pc.py"
RECURSOS = {
    "ci-github": {
        "tipo": "remoto",
        "transporte": "fichero",
        "raiz": "{raiz}/evidencia-ci",
    },
}
# Recursos de preparaciones anteriores que ya no se usan.
RECURSOS_QUE_SE_QUITAN = ("ci-linux",)
TIMEOUT_CORTO = 30
TIMEOUT_VERIFICAR_S = 1800
BASH_POR_OMISION = r"C:\Program Files\Git\bin\bash.exe"


def correr(argv, timeout=TIMEOUT_CORTO):
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def sistema() -> str:
    return "macos" if sys.platform == "darwin" else "windows"


def _arquitectura_temurin() -> str:
    maquina = _platform.machine().lower()
    return "aarch64" if maquina in ("arm64", "aarch64") else "x64"


def temurin_api() -> str:
    """El GA más nuevo de temurin 25 para la plataforma y arquitectura."""
    if sistema() == "macos":
        return (
            "https://api.adoptium.net/v3/assets/latest/25/hotspot"
            f"?architecture={_arquitectura_temurin()}&image_type=jdk&os=mac&vendor=eclipse"
        )
    return TEMURIN_API


def _cita_ps(texto) -> str:
    return "'" + str(texto).replace("'", "''") + "'"


def _leer_registro(clave: str, valor: str, correr=correr) -> str:
    """Un valor de HKCU sin la trampa de la página de códigos (el mismo camino
    que motor/capacidades_maquina.py): PowerShell saca el valor con salida
    UTF-8; reg.exe escribe en la página local y un perfil con acentos llegaría
    mangleado. En PowerShell el registro cuelga de la unidad HKCU:; sin los dos
    puntos la ruta cae en el disco actual y Get-ItemProperty falla con un error
    no terminante que deja el código 0 y la salida vacía, sin respaldo. Si no
    hay PowerShell, reg query tal cual."""
    clave_ps = "HKCU:" + clave[4:] if clave.upper().startswith("HKCU\\") else clave
    guion = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "try {"
        f"$p=Get-ItemProperty -LiteralPath {_cita_ps(clave_ps)} -ErrorAction Stop;"
        f"$v=$p.{valor};"
        "if ($null -ne $v) { [string]$v } else { exit 1 }"
        "} catch { exit 1 }"
    )
    try:
        r = correr([
            "powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", guion,
        ])
    except (OSError, subprocess.TimeoutExpired):
        r = None
    if r is not None and r.returncode == 0:
        return (r.stdout or "").strip()
    try:
        r2 = correr(["reg", "query", clave, "/v", valor])
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if r2.returncode != 0:
        return ""
    return _campo_de_reg(r2.stdout or "", valor)


def _abre(url: str, segundos: int = 300):
    """El user-agent de urllib (Python-urllib/x) recibe 403 de la API de
    Adoptium (run 41364149301); un UA propio pasa."""
    peticion = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(peticion, timeout=segundos)


def descargar(url: str, descargador=None) -> bytes:
    if descargador is not None:
        return descargador(url)
    return _abre(url, 300).read()


def sha256_de(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _escribir_atomico(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, temporal = tempfile.mkstemp(dir=str(ruta.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(temporal, ruta)


def _extraer_zip(crudo: bytes, destino: Path) -> None:
    destino.mkdir(parents=True, exist_ok=True)
    raiz = destino.resolve()
    with zipfile.ZipFile(io.BytesIO(crudo)) as z:
        for miembro in z.infolist():
            if not (raiz / miembro.filename).resolve().is_relative_to(raiz):
                raise RuntimeError(f"el zip trae una ruta fuera de destino: {miembro.filename}")
        z.extractall(destino)


def _extraer_tar(crudo: bytes, destino: Path) -> None:
    """El JDK de Adoptium para Mac es un tar.gz. El filtro `data` de tarfile
    rechaza rutas absolutas, `..` y enlaces que salen del destino."""
    destino.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(crudo), mode="r:gz") as t:
        t.extractall(destino, filter="data")


def _ejecutable_java(raiz: Path) -> Path | None:
    for relativa in ("bin/java.exe", "bin/java", "Contents/Home/bin/java"):
        candidato = raiz / relativa
        if candidato.is_file():
            return candidato
    return None


def _enlazar_actual(base: Path, java: Path) -> None:
    """`herramientas/java/actual` estable para quien no lee el marker: el
    servicio de launchd y verificar.sh. En Windows no se crean enlaces."""
    if os.name == "nt":
        return
    enlace = herramientas(base) / "java" / "actual"
    enlace.parent.mkdir(parents=True, exist_ok=True)
    casa = java.parent.parent.resolve()
    if enlace.is_symlink() and Path(os.readlink(enlace)).resolve() == casa:
        return
    temporal = enlace.with_name(".actual.tmp")
    if temporal.is_symlink() or temporal.exists():
        temporal.unlink()
    os.symlink(casa, temporal, target_is_directory=True)
    os.replace(temporal, enlace)


# --------------------------------------------------------------------- java


def herramientas(base: Path) -> Path:
    return Path(base) / "herramientas"


def _ruta_java_marker(base: Path) -> Path:
    return herramientas(base) / "java" / "instalado.json"


def java_instalado(base: Path) -> dict | None:
    try:
        datos = json.loads(_ruta_java_marker(base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not Path(datos.get("java") or "").is_file():
        return None
    return datos


def temurin_mas_nuevo(catalogo=None, api: str | None = None) -> dict:
    """El GA más nuevo de temurin 25 para esta plataforma, con el sha256 que
    la API declara."""
    if catalogo is not None:
        filas = catalogo()
    else:
        with _abre(api or TEMURIN_API, 60) as r:
            filas = json.loads(r.read().decode("utf-8"))
    for fila in filas:
        paquete = (fila.get("binary") or {}).get("package") or {}
        if paquete.get("link") and paquete.get("checksum"):
            return {
                "release": fila.get("release_name") or "",
                "url": paquete["link"],
                "sha256": str(paquete["checksum"]).lower(),
            }
    raise RuntimeError("la API de Adoptium no trae un paquete jdk para esta plataforma")


def _carpeta_de_release(release: str) -> str:
    """Carpeta determinista por release: el marker siempre apunta a la jdk
    que dejó ESTE zip, no a una jdk sobrante de otra corrida (F9)."""
    limpio = re.sub(r"[^A-Za-z0-9.+-]", "_", release).strip("._") or "jdk"
    return limpio if limpio.startswith("jdk-") else f"jdk-{limpio}"


def instalar_java(base: Path, nuevo: dict, descargador=None) -> tuple[str, str]:
    """Instala el GA `nuevo` (de temurin_mas_nuevo) si difiere del marker.
    Extrae a un staging y renombra a la carpeta de la release, deja el marker
    utilizable y solo entonces borra la jdk vieja: si algo falla a mitad de la
    actualización, la máquina sigue con java utilizable (F7) y el exe del
    marker siempre sale de este zip (F9). En Mac deja además el enlace
    estable `herramientas/java/actual`."""
    previo = java_instalado(base)
    if previo and previo.get("release") == nuevo["release"]:
        _enlazar_actual(base, Path(previo["java"]))
        return "ok", f"java {previo['release']} ya está en {previo['java']}"
    crudo = descargar(nuevo["url"], descargador)
    suma = hashlib.sha256(crudo).hexdigest()
    if suma != nuevo["sha256"]:
        raise RuntimeError(
            f"el paquete de temurin {nuevo['release']} trae sha256 {suma} y la API "
            f"declara {nuevo['sha256']}; no se instala"
        )
    carpeta = herramientas(base) / "java"
    carpeta.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="nueva-", dir=carpeta, ignore_cleanup_errors=True
    ) as staging:
        if str(nuevo["url"]).endswith((".tar.gz", ".tgz")):
            _extraer_tar(crudo, Path(staging))
        else:
            _extraer_zip(crudo, Path(staging))
        raiz = next(
            (
                r for r in sorted(Path(staging).iterdir())
                if r.is_dir() and _ejecutable_java(r) is not None
            ),
            None,
        )
        if raiz is None:
            raise RuntimeError("el paquete de temurin no trae un java ejecutable")
        destino = carpeta / _carpeta_de_release(nuevo["release"])
        if destino.exists():
            shutil.rmtree(destino)
        shutil.move(str(raiz), str(destino))
    exe = _ejecutable_java(destino)
    if exe is None:
        raise RuntimeError("el paquete de temurin no trae un java ejecutable")
    _enlazar_actual(base, exe)
    marker = {"release": nuevo["release"], "sha256": suma, "java": str(exe)}
    _escribir_atomico(
        _ruta_java_marker(base), json.dumps(marker, ensure_ascii=False, indent=2) + "\n"
    )
    if previo:
        vieja = carpeta / _carpeta_de_release(str(previo.get("release") or ""))
        if vieja != destino and vieja.parent == carpeta and vieja.name.startswith("jdk-"):
            shutil.rmtree(vieja, ignore_errors=True)
    return "hecho", f"java {nuevo['release']} instalado en {exe}"


# ---------------------------------------------------------------------- tlc


def jar_tlc(base: Path) -> Path:
    return herramientas(base) / "tla2tools" / TLC_VERSION / "tla2tools.jar"


def instalar_jar(base: Path, descargador=None) -> tuple[str, str]:
    ruta = jar_tlc(base)
    if ruta.is_file() and sha256_de(ruta) == TLC_SHA256:
        return "ok", f"tla2tools {TLC_VERSION} ya está en {ruta}"
    crudo = descargar(TLC_URL, descargador)
    suma = hashlib.sha256(crudo).hexdigest()
    if suma != TLC_SHA256:
        raise RuntimeError(
            f"tla2tools.jar trae sha256 {suma} y verificar.sh fija {TLC_SHA256}; "
            "no se instala"
        )
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".tmp")
    temporal.write_bytes(crudo)
    os.replace(temporal, ruta)
    return "hecho", f"tla2tools {TLC_VERSION} instalado en {ruta}"


# ------------------------------------------------------------------ entorno


def _campo_de_reg(salida: str, nombre: str) -> str:
    for linea in salida.splitlines():
        partes = re.split(r"\s{2,}", linea.strip())
        if partes and partes[0].upper() == nombre.upper() and len(partes) >= 3:
            return partes[-1].strip()
    return ""


def entorno_persistente(correr=correr) -> dict:
    """TLA2TOOLS y JAVA tal como quedó setx (no las de este proceso)."""
    return {
        nombre: _leer_registro(r"HKCU\Environment", nombre, correr)
        for nombre in ("TLA2TOOLS", "JAVA")
    }


def fijar_entorno(java: str, jar: str, correr=correr) -> tuple[str, str]:
    actual = entorno_persistente(correr)
    cambios = []
    for nombre, valor in (("TLA2TOOLS", jar), ("JAVA", java)):
        if os.path.normcase(actual.get(nombre) or "") == os.path.normcase(valor):
            continue
        r = correr(["setx", nombre, valor])
        if r.returncode != 0:
            raise RuntimeError(f"setx {nombre} falló: {(r.stderr or r.stdout).strip()}")
        cambios.append(nombre)
    if not cambios:
        return "ok", "TLA2TOOLS y JAVA persistentes ya apuntan a las herramientas"
    return "hecho", "setx fijó " + " y ".join(cambios)


def java_servicio(base: Path) -> Path:
    return herramientas(base) / "java" / "actual" / "bin" / "java"


def _entorno_servicio_mac(base: Path) -> tuple[str, str]:
    """launchd no hereda variables del shell: verificar.sh y el reporte hallan
    el java y el jar por el enlace estable y la ruta que fija verificar.sh."""
    java = java_instalado(base)
    if java is None:
        return "error", "sin java utilizable no hay enlace para el servicio"
    _enlazar_actual(base, Path(java["java"]))
    actual = java_servicio(base)
    jar = jar_tlc(base)
    if not actual.is_file() or not jar.is_file():
        return "error", f"falta {actual} o {jar} para el servicio"
    return "ok", f"verificar.sh y el reporte hallan {actual} y {jar}"


# ------------------------------------------------------- capacidades.json


# Al volver a preparar una máquina se quita el recurso ci-linux que dejó una
# preparación anterior, sin tocar lo demás que haya declarado el operador.


def capacidades_deseadas(base: Path) -> dict:
    raiz = Path(base).resolve().as_posix()
    return {
        "recursos": {
            nombre: {
                k: v.format(raiz=raiz) if "{raiz}" in str(v) else v
                for k, v in recurso.items()
            }
            for nombre, recurso in RECURSOS.items()
        }
    }


def _recurso_suficiente(nombre: str, actual, deseado: dict) -> bool:
    if not isinstance(actual, dict):
        return False
    return all(actual.get(k) == v for k, v in deseado.items())


def capacidades_suficiente(actual, deseado: dict) -> bool:
    """La base puede tener recursos propios del operador: lo que prepara_pc
    exige es que su recurso esté con los valores que declara, no que el archivo
    sea exactamente el suyo (F3)."""
    recursos = actual.get("recursos") if isinstance(actual, dict) else None
    if not isinstance(recursos, dict):
        return False
    if any(nombre in recursos for nombre in RECURSOS_QUE_SE_QUITAN):
        return False
    return all(
        _recurso_suficiente(nombre, recursos.get(nombre), recurso)
        for nombre, recurso in deseado["recursos"].items()
    )


def instalar_capacidades(base: Path) -> tuple[str, str]:
    base = Path(base)
    deseado = capacidades_deseadas(base)
    (base / "evidencia-ci").mkdir(parents=True, exist_ok=True)
    ruta = base / "capacidades.json"
    try:
        actual = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        actual = None
    if capacidades_suficiente(actual, deseado):
        return "ok", "capacidades.json y evidencia-ci ya están"
    recursos = (
        dict(actual["recursos"])
        if isinstance(actual, dict) and isinstance(actual.get("recursos"), dict)
        else {}
    )
    for nombre in RECURSOS_QUE_SE_QUITAN:
        recursos.pop(nombre, None)
    for nombre, recurso in deseado["recursos"].items():
        previo = recursos.get(nombre)
        fusion = dict(previo) if isinstance(previo, dict) else {}
        fusion.update(recurso)
        recursos[nombre] = fusion
    combinado = dict(actual) if isinstance(actual, dict) else {}
    combinado["recursos"] = recursos
    _escribir_atomico(ruta, json.dumps(combinado, ensure_ascii=False, indent=2) + "\n")
    return "hecho", "capacidades.json escrito con ci-github; ci-linux ya no se usa"


# ---------------------------------------------------------------- llave Run


def iniciar_vigilante() -> Path:
    return Path(__file__).resolve().parent / VIGILANTE


def valor_llave_deseado(python: str = "") -> str:
    return f'"{python or sys.executable}" -B "{iniciar_vigilante()}"'


def valor_llave(correr=correr) -> str:
    return _leer_registro(LLAVE, NOMBRE_LLAVE, correr)


def _script_de_la_llave(valor: str) -> Path | None:
    for parte in re.findall(r'"([^"]+)"', valor):
        if os.path.normcase(parte).endswith(os.path.normcase(VIGILANTE)):
            return Path(parte)
    return None


def fijar_llave(correr=correr, python: str = "") -> tuple[str, str]:
    if not iniciar_vigilante().is_file():
        raise RuntimeError(f"falta {iniciar_vigilante()} junto a este script")
    deseado = valor_llave_deseado(python)
    actual = valor_llave(correr)
    if os.path.normcase(actual) == os.path.normcase(deseado):
        return "ok", "la llave Run ya lanza el vigilante"
    r = correr(["reg", "add", LLAVE, "/v", NOMBRE_LLAVE, "/t", "REG_SZ", "/d", deseado, "/f"])
    if r.returncode != 0:
        raise RuntimeError(f"reg add falló: {(r.stderr or r.stdout).strip()}")
    return "hecho", "la llave Run ahora lanza iniciar_encargos_pc.py"


# ------------------------------------------------------------------- pasos


def _comprobar_mac(base: Path) -> list[dict]:
    base = Path(base)
    java = java_instalado(base)
    jar = jar_tlc(base)
    try:
        suma_jar = sha256_de(jar) if jar.is_file() else ""
    except OSError:
        suma_jar = ""
    actual = java_servicio(base)
    entorno_ok = bool(java) and actual.is_file() and jar.is_file()
    try:
        capacidades_actual = json.loads(
            (base / "capacidades.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        capacidades_actual = None
    deseado = capacidades_deseadas(base)
    return [
        {
            "paso": "java",
            "estado": "ok" if java else "falta",
            "detalle": f"java {java['release']} en {java['java']}" if java
            else "sin temurin 25 utilizable (falta marker o java)",
        },
        {
            "paso": "tla2tools",
            "estado": "ok" if suma_jar == TLC_SHA256 else "falta",
            "detalle": f"{jar} sha256 {suma_jar or 'ausente'}"
            + ("" if suma_jar == TLC_SHA256 else f" (verificar.sh fija {TLC_SHA256})"),
        },
        {
            "paso": "entorno",
            "estado": "ok" if entorno_ok else "falta",
            "detalle": f"enlace del servicio: {actual}"
            if entorno_ok else "falta el enlace herramientas/java/actual o el jar",
        },
        {
            "paso": "capacidades",
            "estado": "ok" if capacidades_suficiente(capacidades_actual, deseado) else "falta",
            "detalle": "capacidades.json con ci-github"
            if capacidades_suficiente(capacidades_actual, deseado)
            else "capacidades.json no coincide con lo esperado",
        },
        {
            "paso": "evidencia-ci",
            "estado": "ok" if (base / "evidencia-ci").is_dir() else "falta",
            "detalle": str(base / "evidencia-ci"),
        },
    ]


def comprobar(base: Path, correr=correr) -> list[dict]:
    base = Path(base)
    if sistema() == "macos":
        return _comprobar_mac(base)
    java = java_instalado(base)
    jar = jar_tlc(base)
    try:
        suma_jar = sha256_de(jar) if jar.is_file() else ""
    except OSError:
        suma_jar = ""
    entorno = entorno_persistente(correr)
    try:
        capacidades_actual = json.loads(
            (base / "capacidades.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        capacidades_actual = None
    deseado = capacidades_deseadas(base)
    llave = valor_llave(correr)
    script_llave = _script_de_la_llave(llave)
    entorno_ok = os.path.normcase(entorno.get("TLA2TOOLS") or "") == os.path.normcase(str(jar)) and (
        bool(java)
        and os.path.normcase(entorno.get("JAVA") or "") == os.path.normcase(str(java["java"]))
    )
    return [
        {
            "paso": "java",
            "estado": "ok" if java else "falta",
            "detalle": f"java {java['release']} en {java['java']}" if java
            else "sin temurin 25 utilizable (falta marker o java.exe)",
        },
        {
            "paso": "tla2tools",
            "estado": "ok" if suma_jar == TLC_SHA256 else "falta",
            "detalle": f"{jar} sha256 {suma_jar or 'ausente'}"
            + ("" if suma_jar == TLC_SHA256 else f" (verificar.sh fija {TLC_SHA256})"),
        },
        {
            "paso": "entorno",
            "estado": "ok" if entorno_ok else "falta",
            "detalle": f"persistentes: TLA2TOOLS={entorno.get('TLA2TOOLS') or ''} "
            f"JAVA={entorno.get('JAVA') or ''}",
        },
        {
            "paso": "capacidades",
            "estado": "ok" if capacidades_suficiente(capacidades_actual, deseado) else "falta",
            "detalle": "capacidades.json con ci-github"
            if capacidades_suficiente(capacidades_actual, deseado)
            else "capacidades.json no coincide con lo esperado",
        },
        {
            "paso": "evidencia-ci",
            "estado": "ok" if (base / "evidencia-ci").is_dir() else "falta",
            "detalle": str(base / "evidencia-ci"),
        },
        {
            "paso": "llave-run",
            "estado": "ok" if script_llave and script_llave.is_file() else "falta",
            "detalle": llave or "sin llave Run GoncloudMotorEncargos",
        },
    ]


def instalar(base: Path, correr=correr, catalogo=None, descargador=None,
             python: str = "") -> list[dict]:
    base = Path(base)
    mac = sistema() == "macos"
    salida = []
    # La política documentada es el último GA de temurin 25: preguntar en cada
    # corrida, instalar solo si hay parche nuevo (F2). No poder consultar la
    # API con java ya utilizable no tumba la corrida; un fallo de la
    # actualización sí se reporta, y la jdk vieja queda utilizable (F7).
    try:
        nuevo = temurin_mas_nuevo(catalogo, temurin_api() if mac else TEMURIN_API)
    except (RuntimeError, OSError, ValueError) as exc:
        nuevo = None
        motivo = f"{type(exc).__name__}: {exc}"
    if nuevo is None:
        java = java_instalado(base)
        if java is not None:
            salida.append({
                "paso": "java", "estado": "ok",
                "detalle": f"java {java['release']} en {java['java']} "
                f"(sin poder consultar parches: {motivo})",
            })
        else:
            salida.append({
                "paso": "java", "estado": "error",
                "detalle": "no se pudo consultar temurin 25 y no hay java "
                f"utilizable: {motivo}",
            })
    else:
        try:
            estado, detalle = instalar_java(base, nuevo, descargador)
        except (RuntimeError, OSError, ValueError) as exc:
            estado, detalle = "error", f"{type(exc).__name__}: {exc}"
        if java_instalado(base) is None:
            estado, detalle = "error", detalle + " (marker no utilizable)"
        salida.append({"paso": "java", "estado": estado, "detalle": detalle})
    java = java_instalado(base)
    try:
        estado, detalle = instalar_jar(base, descargador)
    except (RuntimeError, OSError, ValueError) as exc:
        estado, detalle = "error", f"{type(exc).__name__}: {exc}"
    salida.append({"paso": "tla2tools", "estado": estado, "detalle": detalle})
    if mac:
        try:
            estado, detalle = _entorno_servicio_mac(base)
        except (RuntimeError, OSError) as exc:
            estado, detalle = "error", f"{type(exc).__name__}: {exc}"
        salida.append({"paso": "entorno", "estado": estado, "detalle": detalle})
    elif java is None:
        salida.append({
            "paso": "entorno", "estado": "error",
            "detalle": "sin java utilizable no se fijan JAVA ni TLA2TOOLS",
        })
    else:
        try:
            estado, detalle = fijar_entorno(java["java"], str(jar_tlc(base)), correr)
        except (RuntimeError, OSError) as exc:
            estado, detalle = "error", f"{type(exc).__name__}: {exc}"
        salida.append({"paso": "entorno", "estado": estado, "detalle": detalle})
    estado, detalle = instalar_capacidades(base)
    salida.append({"paso": "capacidades", "estado": estado, "detalle": detalle})
    if not mac:
        try:
            estado, detalle = fijar_llave(correr, python)
        except (RuntimeError, OSError) as exc:
            estado, detalle = "error", f"{type(exc).__name__}: {exc}"
        salida.append({"paso": "llave-run", "estado": estado, "detalle": detalle})
    return salida


def _a_lf(ruta: Path) -> bool:
    """Un checkout CRLF deja '---- MODULE FlujoInspeccion ----\\r' y verificar.sh
    sale 2 en el grep de la línea MODULE antes de llamar a TLC. Normaliza a LF
    solo si hace falta (el archivo queda igual al índice de git) y dice si
    escribió."""
    try:
        crudo = ruta.read_bytes()
    except OSError:
        return False
    if b"\r\n" not in crudo:
        return False
    temporal = ruta.with_name(ruta.name + ".lf.tmp")
    temporal.write_bytes(crudo.replace(b"\r\n", b"\n"))
    os.replace(temporal, ruta)
    return True


def verificar(cfg: str, base: Path, raiz: Path, bash: str = "") -> int:
    jar = jar_tlc(base)
    java = java_instalado(base)
    if java is None or not jar.is_file():
        print("falta java o tla2tools: corre sin --verificar primero", file=sys.stderr)
        return 2
    inspeccion = raiz / "verificacion" / "inspeccion"
    normalizados = [n for n in ("verificar.sh", "FlujoInspeccion.tla", cfg) if _a_lf(inspeccion / n)]
    if normalizados:
        print("normalizados a LF (un checkout CRLF deja verificar.sh en 2): " + ", ".join(normalizados))
    bash = bash or shutil.which("bash") or BASH_POR_OMISION
    baja = bash.lower().replace("/", "\\")
    if baja.endswith(r"\system32\bash.exe") or r"\windowsapps\bash.exe" in baja:
        # El bash de WSL correría el script dentro de Linux con una ruta C:/...
        bash = BASH_POR_OMISION
    env = {**os.environ, "JAVA": java["java"], "TLA2TOOLS": str(jar)}
    script = (inspeccion / "verificar.sh").as_posix()
    try:
        r = subprocess.run(
            [bash, script, cfg], cwd=str(raiz), env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_VERIFICAR_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"verificar.sh no pudo correr: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(r.stdout or "")
    sys.stderr.write(r.stderr or "")
    print(f"verificar.sh {cfg} terminó con código {r.returncode}")
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt":
        for flujo in (sys.stdout, sys.stderr):
            if hasattr(flujo, "reconfigure"):
                flujo.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, default=Path.home() / BASE)
    ap.add_argument("--comprobar", action="store_true")
    ap.add_argument("--verificar", nargs="?", const="main.cfg", default=None)
    ap.add_argument("--bash", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    args.base = Path(args.base).expanduser().resolve()
    filas = (
        comprobar(args.base, correr) if args.comprobar else instalar(args.base, correr)
    )
    if args.json:
        print(json.dumps({"base": str(args.base), "pasos": filas}, ensure_ascii=False, indent=2))
    else:
        ancho = max(len(f["paso"]) for f in filas)
        for f in filas:
            print(f"{f['paso']:<{ancho}}  {f['estado']:<6}  {f['detalle']}")
    faltas = [f for f in filas if f["estado"] in ("falta", "error")]
    if faltas:
        for f in faltas:
            print(f"{f['paso']}: {f['estado']}: {f['detalle']}", file=sys.stderr)
        return 1
    if args.verificar is not None:
        return verificar(
            args.verificar, args.base, Path(__file__).resolve().parent.parent, args.bash
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
