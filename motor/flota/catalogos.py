"""Catálogos oficiales y promoción comprobada de la política de modelos."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from .manifiesto import cargar, guardar, sembrar_tabla

GO_MODELS = "https://opencode.ai/zen/go/v1/models"
CLAUDE_MODELS = "https://platform.claude.com/docs/en/about-claude/models/overview.md"
ZAI_MODELS = "https://docs.z.ai/llms.txt"
_LABEL = re.compile(r"^(?P<ref>[^\s]+) \((?P<effort>[^()]+)\)$")
_VERSION = re.compile(r"\d+(?:[.-]\d+)*")
_UNSTABLE = {"preview", "exp", "alpha", "beta", "free", "latest"}
_SUSCRIPCIONES = {"anthropic", "openai", "xai", "zai", "kimi", "meta", "opencode-go"}


def obtener_json(url: str) -> dict:
    if url != GO_MODELS:
        raise ValueError("canal no registrado")
    solicitud = urllib.request.Request(url, headers={"User-Agent": "goncloud-flota/1"})
    with urllib.request.urlopen(solicitud, timeout=20) as respuesta:
        return json.load(respuesta)


def obtener_texto(url: str) -> str:
    if url not in (CLAUDE_MODELS, ZAI_MODELS):
        raise ValueError("documentacion no registrada")
    solicitud = urllib.request.Request(url, headers={"User-Agent": "goncloud-flota/1"})
    with urllib.request.urlopen(solicitud, timeout=20) as respuesta:
        return respuesta.read(2_000_000).decode("utf-8")


def _claude_publicados(texto: str) -> list[dict]:
    filas = "\n".join(l for l in texto.splitlines() if l.startswith("| Claude API ID"))
    return [
        {"id": modelo}
        for modelo in sorted(
            set(
                re.findall(
                    r"`(claude-[a-z]+-\d+(?:-\d+)*)`",
                    filas,
                )
            )
        )
    ]


def _zai_publicados(texto: str) -> list[dict]:
    return [
        {"id": modelo}
        for modelo in sorted(
            set(
                re.findall(
                    r"https://docs\.z\.ai/guides/(?:llm|vlm)/(glm-\d+(?:\.\d+)*(?:-flash)?)\.md",
                    texto,
                )
            )
        )
    ]


def _argv(ejecutable: str, *args: str) -> list[str]:
    if sys.platform == "win32" and Path(ejecutable).suffix.lower() in (".cmd", ".bat"):
        return [
            os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"),
            "/d",
            "/s",
            "/c",
            ejecutable,
            *args,
        ]
    return [ejecutable, *args]


def _codex_modelos(ejecutable: str) -> list[dict]:
    mensajes: queue.Queue = queue.Queue()
    proceso = subprocess.Popen(
        _argv(ejecutable, "app-server", "--stdio"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )

    def leer():
        for linea in proceso.stdout:
            try:
                doc = json.loads(linea)
                if isinstance(doc, dict):
                    mensajes.put(doc)
            except ValueError:
                continue
        mensajes.put(None)

    lector = threading.Thread(target=leer, daemon=True)
    lector.start()

    def enviar(doc):
        proceso.stdin.write(json.dumps(doc) + "\n")
        proceso.stdin.flush()

    def recibir(identidad):
        limite = time.monotonic() + 20
        while time.monotonic() < limite:
            doc = mensajes.get(timeout=max(0.01, limite - time.monotonic()))
            if doc is None:
                raise RuntimeError("el catalogo de Codex cerro la conexion")
            if doc.get("id") == identidad:
                if "error" in doc:
                    raise RuntimeError("Codex rechazo la consulta de catalogo")
                return doc["result"]
        raise TimeoutError("catalogo de Codex sin respuesta")

    try:
        enviar(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "goncloud-flota", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        recibir(1)
        enviar({"method": "initialized", "params": {}})
        enviar({"id": 2, "method": "model/list", "params": {"includeHidden": True}})
        resultado = recibir(2)
        filas = list(resultado.get("data") or [])
        cursor = resultado.get("nextCursor")
        ident = 3
        vistos = set()
        while cursor:
            if cursor in vistos:
                raise RuntimeError("Codex repitio la pagina del catalogo")
            vistos.add(cursor)
            enviar(
                {
                    "id": ident,
                    "method": "model/list",
                    "params": {
                        "includeHidden": True,
                        "cursor": cursor,
                    },
                }
            )
            resultado = recibir(ident)
            filas.extend(resultado.get("data") or [])
            cursor = resultado.get("nextCursor")
            ident += 1
        return [
            {
                "id": fila["id"],
                "esfuerzos": [
                    e["reasoningEffort"]
                    for e in fila.get("supportedReasoningEfforts") or []
                ],
            }
            for fila in filas
            if isinstance(fila, dict) and fila.get("id")
        ]
    finally:
        proceso.stdin.close()
        try:
            proceso.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(proceso.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                proceso.kill()
            proceso.wait()
        lector.join(timeout=2)
        proceso.stdout.close()


def _etiquetas(tabla: dict) -> dict[str, list[dict]]:
    usos: dict[str, list[dict]] = {}
    for funcion, cadena in tabla["chains"].items():
        for posicion, etiqueta in enumerate(cadena):
            coincide = _LABEL.fullmatch(etiqueta)
            if coincide is None:
                raise ValueError(f"etiqueta invalida en {funcion}:{posicion}")
            ref, esfuerzo = coincide["ref"], coincide["effort"]
            if "/" not in ref:
                raise ValueError(f"modelo sin proveedor en {funcion}:{posicion}")
            usos.setdefault(ref, []).append(
                {
                    "funcion": funcion,
                    "posicion": posicion,
                    "esfuerzo": esfuerzo,
                }
            )
    return usos


def _version(modelo: str):
    if _UNSTABLE & set(re.split(r"[^a-z]+", modelo.lower())):
        return None
    encontrados = list(_VERSION.finditer(modelo))
    if len(encontrados) != 1:
        return None
    numero = encontrados[0]
    partes = re.split(r"[.-]", numero.group())
    if any(len(p) >= 4 for p in partes):
        return None
    return modelo[: numero.start()] + "{}" + modelo[numero.end() :], tuple(
        int(p) for p in partes
    )


def _mas_nuevo(actual: str, modelos: list[dict]) -> str | None:
    base = _version(actual)
    if base is None:
        return None
    candidatos = []
    for fila in modelos:
        modelo = fila["id"]
        version = _version(modelo)
        if version and version[0] == base[0] and fila.get("status") != "deprecated":
            largo = max(len(base[1]), len(version[1]))
            if version[1] + (0,) * (largo - len(version[1])) > base[1] + (0,) * (
                largo - len(base[1])
            ):
                candidatos.append((version[1], modelo))
    return max(candidatos)[1] if candidatos else None


def descubrir_modelos(
    tabla: dict,
    *,
    ejecutables: dict | None = None,
    obtener=None,
    leer_texto=None,
    run=None,
) -> dict:
    """Lee catálogos sin inferencia; un fallo de lectura no demuestra ausencia."""
    usos = _etiquetas(tabla)
    ejecutar = run or subprocess.run
    obtener = obtener or obtener_json
    leer_texto = leer_texto or obtener_texto
    binarios = {nombre: shutil.which(nombre) for nombre in ("codex", "grok", "muse")}
    binarios.update(ejecutables or {})
    fuentes = {}

    def leer(nombre, origen, consulta, alcance="suscripcion"):
        try:
            modelos = consulta()
            if not modelos:
                raise ValueError("catalogo vacio")
            fuentes[nombre] = {
                "estado": "observado",
                "alcance": alcance,
                "origen": origen,
                "modelos": modelos,
            }
        except (
            OSError,
            ValueError,
            RuntimeError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
            queue.Empty,
        ) as exc:
            fuentes[nombre] = {
                "estado": "desconocido",
                "origen": origen,
                "motivo": type(exc).__name__,
                "modelos": [],
            }

    leer(
        "opencode-go",
        GO_MODELS,
        lambda: [{"id": fila["id"]} for fila in obtener(GO_MODELS)["data"]],
        "publicado",
    )
    if any(ref.startswith("anthropic/") for ref in usos):
        leer(
            "anthropic",
            CLAUDE_MODELS,
            lambda: _claude_publicados(leer_texto(CLAUDE_MODELS)),
            "publicado",
        )
    if any(ref.startswith("zai/") for ref in usos):
        leer(
            "zai",
            ZAI_MODELS,
            lambda: _zai_publicados(leer_texto(ZAI_MODELS)),
            "publicado",
        )
    if binarios.get("codex"):
        leer(
            "openai",
            "codex app-server model/list",
            lambda: _codex_modelos(binarios["codex"]),
        )
    if binarios.get("grok"):

        def grok():
            respuesta = ejecutar(
                _argv(binarios["grok"], "models"),
                capture_output=True,
                text=True,
                timeout=25,
                check=True,
            )
            return [
                {"id": modelo}
                for modelo in sorted(
                    set(
                        re.findall(
                            r"^\s*[*-]\s+(grok-[a-zA-Z0-9_.-]+)(?:\s|$)",
                            respuesta.stdout,
                            re.MULTILINE,
                        )
                    )
                )
            ]

        leer("xai", "grok models (suscripcion)", grok)
    for proveedor in sorted({ref.split("/", 1)[0] for ref in usos}):
        fuentes.setdefault(
            proveedor,
            {
                "estado": "desconocido",
                "origen": "sin catalogo oficial legible",
                "modelos": [],
                "motivo": "la configuracion no acredita disponibilidad",
            },
        )
    usados, candidatos = [], []
    for ref, celdas in usos.items():
        proveedor, modelo = ref.split("/", 1)
        fuente = fuentes[proveedor]
        ids = {fila["id"] for fila in fuente["modelos"]}
        estado = (
            "observado"
            if modelo in ids
            else "no_listado"
            if fuente["estado"] == "observado"
            else "desconocido"
        )
        usados.append({"modelo": ref, "estado": estado, "usos": celdas})
        nueva = _mas_nuevo(modelo, fuente["modelos"])
        if nueva:
            candidatos.append(
                {
                    "anterior": ref,
                    "nuevo": proveedor + "/" + nueva,
                    "origen": fuente["origen"],
                    "usos": celdas,
                    "estado": "pendiente_comprobacion",
                }
            )
    return {"fuentes": fuentes, "usados": usados, "candidatos": candidatos}


def _repo_hermes(repo: Path | None) -> Path | None:
    if repo is not None:
        return Path(repo)
    raiz = Path(__file__).resolve().parents[2]
    for candidato in (
        raiz.parent / "goncloud-hermes-encargos-3-4",
        raiz.parent / "goncloud-hermes",
        raiz / "herramientas" / "hermes-config",
        raiz / "repos" / "hermes",
    ):
        if (candidato / "scripts/probe_route.py").is_file():
            return candidato
    return None


def sondar_modelo(
    ref: str,
    esfuerzo: str,
    *,
    posicion=0,
    hermes_repo: Path | None = None,
    python: str | None = None,
    run=None,
) -> dict:
    """Usa la ruta de suscripción del plugin sin escribir su estado de salud."""
    proveedor, _, modelo = ref.partition("/")
    if proveedor not in _SUSCRIPCIONES or not modelo:
        return {
            "confirmado": False,
            "motivo": "no es una ruta de suscripcion autorizada",
        }
    repo = _repo_hermes(hermes_repo)
    if repo is None:
        return {
            "confirmado": False,
            "motivo": "runtime de sonda de Hermes no disponible",
        }
    ejecutar = run or subprocess.run
    try:
        resultado = ejecutar(
            [
                python or sys.executable,
                str(repo / "scripts/probe_route.py"),
                "--position",
                str(posicion),
                "--timeout",
                "120",
                f"{ref} ({esfuerzo})",
            ],
            capture_output=True,
            text=True,
            timeout=150,
            check=False,
        )
        recibo = json.loads(resultado.stdout)
        confirmado = (
            resultado.returncode == 0
            and recibo.get("success") is True
            and recibo.get("marker_exact") is True
            and recibo.get("model") == modelo
            and recibo.get("effort") == (None if esfuerzo == "off" else esfuerzo)
            and recibo.get("adapter")
            in {
                "claude",
                "codex",
                "kimi",
                "muse",
                "grok",
                "opencode_go",
                "opencode_zai",
            }
        )
        return {
            "confirmado": confirmado,
            "modelo": ref,
            "esfuerzo": esfuerzo,
            "adapter": recibo.get("adapter"),
            "segundos": recibo.get("seconds"),
            "motivo": "comprobado" if confirmado else "la sonda no confirmo la ruta",
        }
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        return {"confirmado": False, "motivo": type(exc).__name__}


def preparar_politica_modelos(
    tabla: dict,
    descubrimiento: dict,
    *,
    comprobar=None,
    hermes_repo: Path | None = None,
) -> dict:
    propuesta = copy.deepcopy(tabla)
    usos = _etiquetas(tabla)
    recibos = []
    for candidata in descubrimiento.get("candidatos") or []:
        vieja, nueva = candidata["anterior"], candidata["nuevo"]
        proveedor, _, modelo = nueva.partition("/")
        fuente = descubrimiento["fuentes"].get(proveedor) or {}
        catalogo = fuente.get("modelos") or []
        if (
            vieja not in usos
            or vieja.split("/", 1)[0] != proveedor
            or fuente.get("estado") != "observado"
            or _mas_nuevo(vieja.split("/", 1)[1], catalogo) != modelo
        ):
            recibos.append(
                {
                    "anterior": vieja,
                    "nuevo": nueva,
                    "estado": "rechazado",
                    "motivo": "sin procedencia oficial compatible",
                }
            )
            continue
        celdas = usos[vieja]
        esfuerzos = sorted({celda["esfuerzo"] for celda in celdas})
        sondas = []
        for esfuerzo in esfuerzos:
            posicion = next(c["posicion"] for c in celdas if c["esfuerzo"] == esfuerzo)
            sonda = (
                comprobar(nueva, esfuerzo)
                if comprobar
                else sondar_modelo(
                    nueva,
                    esfuerzo,
                    posicion=posicion,
                    hermes_repo=hermes_repo,
                )
            )
            sondas.append(sonda)
        confirmado = bool(sondas) and all(s.get("confirmado") is True for s in sondas)
        recibos.append(
            {
                "anterior": vieja,
                "nuevo": nueva,
                "estado": "comprobado" if confirmado else "pendiente_comprobacion",
                "sondas": sondas,
            }
        )
        if confirmado:
            for celda in celdas:
                propuesta["chains"][celda["funcion"]][celda["posicion"]] = (
                    f"{nueva} ({celda['esfuerzo']})"
                )
    return {"tabla": propuesta, "recibos": recibos, "cambio": propuesta != tabla}


def publicar_politica_modelos(
    manifiesto: Path,
    tabla: dict,
    descubrimiento: dict,
    *,
    comprobar=None,
    hermes_repo: Path | None = None,
) -> dict:
    """Actualiza la única política; ambas proyecciones derivan de esa misma tabla."""
    vigente = cargar(manifiesto)
    esperada = sembrar_tabla(tabla, vigente.generacion)
    if vigente.puestos != esperada.puestos or vigente.herencia != esperada.herencia:
        raise ValueError("la tabla no corresponde a la politica publicada")
    resultado = preparar_politica_modelos(
        tabla, descubrimiento, comprobar=comprobar, hermes_repo=hermes_repo
    )
    if resultado["cambio"]:
        contenido = json.dumps(
            resultado["tabla"], sort_keys=True, ensure_ascii=False
        ).encode()
        generacion = hashlib.sha256(contenido).hexdigest()[:16]
        nueva = sembrar_tabla(resultado["tabla"], generacion)
        guardar(
            manifiesto, replace(vigente, generacion=generacion, puestos=nueva.puestos)
        )
    return {
        **resultado,
        "manifiesto": str(manifiesto),
        "generacion": cargar(manifiesto).generacion,
    }
