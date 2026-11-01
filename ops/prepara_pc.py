"""Prepara la PC (Windows) con las mismas capacidades que la Mini para encargos.

Deja, de forma idempotente (correrlo dos veces no cambia nada la segunda):

- Java temurin 25 (zip GA de Adoptium, sha256 verificado por descarga) y
  tla2tools.jar 1.7.4 con el sha256 que fija verificacion/inspeccion/verificar.sh,
  ambos en <base>/herramientas/. La política de versión de Java es la misma que
  `java@temurin-25` en mise: el último GA de la 25. Un parche nuevo de temurin 25
  sí actualiza en la siguiente corrida; el jar está fijado por su suma.
- JAVA y TLA2TOOLS persistentes para el usuario (setx), apuntando a lo anterior:
  es el camino sin mise que el propio verificar.sh documenta (`exporta TLA2TOOLS
  y JAVA`). No toca PATH del sistema ni secretos.
- <base>/capacidades.json con los recursos ci-linux y ci-github, y la carpeta
  <base>/evidencia-ci que declara ci-github.
- La llave HKCU\\...\\Run\\GoncloudMotorEncargos lanzando el vigilante
  (ops/iniciar_encargos_pc.py) en lugar de supervisor.ps1 directo. supervisor.ps1
  no se borra: sigue siendo lo que el vigilante lanza y vigila (fixed_paths).

Modos:
- Sin banderas: deja lo que falte y reporta cada paso.
- --comprobar: solo comprueba y dice qué falta; no toca nada.
- --verificar [cfg]: además corre verificar.sh con las herramientas dejadas
  (por Git Bash si bash no está en PATH).
- --json: imprime el reporte como JSON.

Nunca lee, escribe ni imprime secretos: secretos.env no se toca. Fuera de
herramientas/ solo escribe capacidades.json, evidencia-ci, la llave Run y las
variables TLA2TOOLS/JAVA del usuario.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASE = ".motor-encargos"
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
    "ci-linux": {
        "tipo": "ci",
        "repo": "gon0801/goncloud-motor",
        "workflow": "pruebas.yml",
        "base": "main",
    },
}
TIMEOUT_CORTO = 30
TIMEOUT_VERIFICAR_S = 1800
BASH_POR_OMISION = r"C:\Program Files\Git\bin\bash.exe"


def correr(argv, timeout=TIMEOUT_CORTO):
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def descargar(url: str, descargador=None) -> bytes:
    if descargador is not None:
        return descargador(url)
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


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


def temurin_mas_nuevo(catalogo=None) -> dict:
    """El GA más nuevo de temurin 25 para Windows x64, con el sha256 que la API declara."""
    if catalogo is not None:
        filas = catalogo()
    else:
        with urllib.request.urlopen(TEMURIN_API, timeout=60) as r:
            filas = json.loads(r.read().decode("utf-8"))
    for fila in filas:
        paquete = (fila.get("binary") or {}).get("package") or {}
        if paquete.get("link") and paquete.get("checksum"):
            return {
                "release": fila.get("release_name") or "",
                "url": paquete["link"],
                "sha256": str(paquete["checksum"]).lower(),
            }
    raise RuntimeError("la API de Adoptium no trae un paquete jdk de Windows")


def instalar_java(base: Path, catalogo=None, descargador=None) -> tuple[str, str]:
    previo = java_instalado(base)
    nuevo = temurin_mas_nuevo(catalogo)
    if previo and previo.get("release") == nuevo["release"]:
        return "ok", f"java {previo['release']} ya está en {previo['java']}"
    crudo = descargar(nuevo["url"], descargador)
    suma = hashlib.sha256(crudo).hexdigest()
    if suma != nuevo["sha256"]:
        raise RuntimeError(
            f"el zip de temurin {nuevo['release']} trae sha256 {suma} y la API "
            f"declara {nuevo['sha256']}; no se instala"
        )
    carpeta = herramientas(base) / "java"
    if previo:
        vieja = Path(previo["java"]).parent.parent
        if vieja.parent == carpeta and vieja.name.startswith("jdk-"):
            shutil.rmtree(vieja, ignore_errors=True)
    _extraer_zip(crudo, carpeta)
    exe = next(iter(sorted(carpeta.rglob("bin/java.exe"))), None)
    if exe is None:
        raise RuntimeError("el zip de temurin no trae bin/java.exe")
    marker = {"release": nuevo["release"], "sha256": suma, "java": str(exe)}
    _escribir_atomico(
        _ruta_java_marker(base), json.dumps(marker, ensure_ascii=False, indent=2) + "\n"
    )
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
    salida = {}
    for nombre in ("TLA2TOOLS", "JAVA"):
        try:
            r = correr(["reg", "query", r"HKCU\Environment", "/v", nombre])
        except (OSError, subprocess.TimeoutExpired):
            salida[nombre] = ""
            continue
        salida[nombre] = _campo_de_reg(r.stdout or "", nombre) if r.returncode == 0 else ""
    return salida


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


# ------------------------------------------------------- capacidades.json


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


def instalar_capacidades(base: Path) -> tuple[str, str]:
    base = Path(base)
    deseado = capacidades_deseadas(base)
    (base / "evidencia-ci").mkdir(parents=True, exist_ok=True)
    ruta = base / "capacidades.json"
    try:
        actual = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        actual = None
    if actual == deseado:
        return "ok", "capacidades.json y evidencia-ci ya están"
    _escribir_atomico(ruta, json.dumps(deseado, ensure_ascii=False, indent=2) + "\n")
    return "hecho", "capacidades.json escrito con ci-linux y ci-github"


# ---------------------------------------------------------------- llave Run


def iniciar_vigilante() -> Path:
    return Path(__file__).resolve().parent / VIGILANTE


def valor_llave_deseado(python: str = "") -> str:
    return f'"{python or sys.executable}" -B "{iniciar_vigilante()}"'


def valor_llave(correr=correr) -> str:
    try:
        r = correr(["reg", "query", LLAVE, "/v", NOMBRE_LLAVE])
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return _campo_de_reg(r.stdout or "", NOMBRE_LLAVE) if r.returncode == 0 else ""


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


def comprobar(base: Path, correr=correr) -> list[dict]:
    base = Path(base)
    java = java_instalado(base)
    jar = jar_tlc(base)
    suma_jar = sha256_de(jar) if jar.is_file() else ""
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
            "estado": "ok" if capacidades_actual == deseado else "falta",
            "detalle": "capacidades.json con ci-linux y ci-github"
            if capacidades_actual == deseado
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
    salida = []
    java = java_instalado(base)
    if java is None:
        try:
            estado, detalle = instalar_java(base, catalogo, descargador)
        except RuntimeError as exc:
            estado, detalle = "error", str(exc)
        java = java_instalado(base)
        if java is None:
            estado, detalle = "error", detalle + " (marker no utilizable)"
        salida.append({"paso": "java", "estado": estado, "detalle": detalle})
    else:
        salida.append({
            "paso": "java", "estado": "ok",
            "detalle": f"java {java['release']} en {java['java']}",
        })
    try:
        estado, detalle = instalar_jar(base, descargador)
    except RuntimeError as exc:
        estado, detalle = "error", str(exc)
    salida.append({"paso": "tla2tools", "estado": estado, "detalle": detalle})
    if java is None:
        salida.append({
            "paso": "entorno", "estado": "error",
            "detalle": "sin java utilizable no se fijan JAVA ni TLA2TOOLS",
        })
    else:
        try:
            estado, detalle = fijar_entorno(java["java"], str(jar_tlc(base)), correr)
        except RuntimeError as exc:
            estado, detalle = "error", str(exc)
        salida.append({"paso": "entorno", "estado": estado, "detalle": detalle})
    estado, detalle = instalar_capacidades(base)
    salida.append({"paso": "capacidades", "estado": estado, "detalle": detalle})
    try:
        estado, detalle = fijar_llave(correr, python)
    except RuntimeError as exc:
        estado, detalle = "error", str(exc)
    salida.append({"paso": "llave-run", "estado": estado, "detalle": detalle})
    return salida


def verificar(cfg: str, base: Path, raiz: Path, bash: str = "") -> int:
    jar = jar_tlc(base)
    java = java_instalado(base)
    if java is None or not jar.is_file():
        print("falta java o tla2tools: corre sin --verificar primero", file=sys.stderr)
        return 2
    bash = bash or shutil.which("bash") or BASH_POR_OMISION
    env = {**os.environ, "JAVA": java["java"], "TLA2TOOLS": str(jar)}
    script = (raiz / "verificacion" / "inspeccion" / "verificar.sh").as_posix()
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, default=Path.home() / BASE)
    ap.add_argument("--comprobar", action="store_true")
    ap.add_argument("--verificar", nargs="?", const="main.cfg", default=None)
    ap.add_argument("--bash", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
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
