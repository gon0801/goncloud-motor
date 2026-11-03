from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import tomllib

ESFUERZOS = frozenset({"xhigh", "high", "medium", "low", "off"})
_PROHIBIDO = ("token", "secret", "password", "passwd", "credential", "authorization")


class ManifiestoInvalido(ValueError):
    pass


@dataclass(frozen=True)
class Puesto:
    funcion: str
    cadena: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Herramienta:
    nombre: str
    canal: str
    version: str
    integridad: str


@dataclass(frozen=True)
class Cuenta:
    runtime: str
    referencia: str


@dataclass(frozen=True)
class Respaldo:
    funcion: str
    modelo: str
    esfuerzo: str
    hasta: str
    motivo: str


@dataclass(frozen=True)
class Politica:
    generacion: str
    puestos: tuple[Puesto, ...]
    herencia: tuple[tuple[str, str], ...]
    herramientas: tuple[Herramienta, ...]
    cuentas: tuple[Cuenta, ...]
    respaldo: Respaldo | None
    skills: tuple[str, ...]


def _exigir_texto(valor: object, campo: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise ManifiestoInvalido(f"{campo} vacío")
    bajo = valor.lower()
    if any(marca in bajo for marca in _PROHIBIDO) or "@" in valor:
        raise ManifiestoInvalido(f"{campo} parece un secreto")
    return valor.strip()


def cargar(path: Path) -> Politica:
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifiestoInvalido(str(exc)) from exc
    return desde_doc(doc)


def desde_doc(doc: dict) -> Politica:
    if not isinstance(doc, dict):
        raise ManifiestoInvalido("el manifiesto no es una tabla")
    generacion = _exigir_texto(doc.get("generacion"), "generacion")
    crudos = doc.get("puesto") or []
    if not isinstance(crudos, list) or not crudos:
        raise ManifiestoInvalido("faltan los puestos")
    puestos = []
    vistos = set()
    for fila in crudos:
        funcion = _exigir_texto(fila.get("funcion"), "funcion")
        if funcion in vistos:
            raise ManifiestoInvalido(f"puesto repetido: {funcion}")
        vistos.add(funcion)
        modelos = fila.get("modelo") or []
        esfuerzos = fila.get("esfuerzo") or []
        if len(modelos) != len(esfuerzos) or not modelos:
            raise ManifiestoInvalido(f"{funcion} trae cadena incompleta")
        cadena = []
        for modelo, esfuerzo in zip(modelos, esfuerzos, strict=True):
            modelo = _exigir_texto(modelo, "modelo")
            esfuerzo = _exigir_texto(esfuerzo, "esfuerzo")
            if "/" not in modelo:
                raise ManifiestoInvalido(f"modelo sin proveedor: {modelo}")
            if esfuerzo not in ESFUERZOS:
                raise ManifiestoInvalido(f"esfuerzo desconocido: {esfuerzo}")
            cadena.append((modelo, esfuerzo))
        puestos.append(Puesto(funcion, tuple(cadena)))
    herencia = tuple(sorted((str(k), str(v)) for k, v in (doc.get("herencia") or {}).items()))
    funciones = {p.funcion for p in puestos}
    for perfil, funcion in herencia:
        if funcion not in funciones:
            raise ManifiestoInvalido(f"{perfil} hereda {funcion}, que no existe")
    herramientas = tuple(
        Herramienta(
            _exigir_texto(h.get("nombre"), "herramienta"),
            _exigir_texto(h.get("canal"), "canal"),
            str(h.get("version") or ""),
            str(h.get("integridad") or ""),
        )
        for h in doc.get("herramienta") or []
    )
    cuentas = tuple(
        Cuenta(
            _exigir_texto(c.get("runtime"), "runtime"),
            _exigir_texto(c.get("referencia"), "referencia"),
        )
        for c in doc.get("cuenta") or []
    )
    respaldo = None
    if doc.get("respaldo"):
        r = doc["respaldo"]
        respaldo = Respaldo(
            _exigir_texto(r.get("funcion"), "respaldo"),
            _exigir_texto(r.get("modelo"), "modelo"),
            _exigir_texto(r.get("esfuerzo"), "esfuerzo"),
            _exigir_texto(r.get("hasta"), "hasta"),
            _exigir_texto(r.get("motivo"), "motivo"),
        )
        if respaldo.esfuerzo not in ESFUERZOS:
            raise ManifiestoInvalido("esfuerzo de respaldo desconocido")
    skills = tuple(_exigir_texto(s, "skill") for s in doc.get("skill") or [])
    return Politica(
        generacion, tuple(puestos), herencia, herramientas, cuentas, respaldo, skills
    )


def emitir(politica: Politica) -> str:
    lineas = [f"generacion = {json.dumps(politica.generacion, ensure_ascii=False)}"]
    if politica.skills:
        skills = ", ".join(json.dumps(s, ensure_ascii=False) for s in politica.skills)
        lineas.append(f"skill = [{skills}]")
    lineas.append("")
    for puesto in politica.puestos:
        lineas.append("[[puesto]]")
        lineas.append(f"funcion = {json.dumps(puesto.funcion, ensure_ascii=False)}")
        modelos = ", ".join(json.dumps(m, ensure_ascii=False) for m, _ in puesto.cadena)
        esfuerzos = ", ".join(json.dumps(e, ensure_ascii=False) for _, e in puesto.cadena)
        lineas.append(f"modelo = [{modelos}]")
        lineas.append(f"esfuerzo = [{esfuerzos}]")
        lineas.append("")
    if politica.herencia:
        lineas.append("[herencia]")
        for perfil, funcion in politica.herencia:
            lineas.append(
                f"{perfil} = {json.dumps(funcion, ensure_ascii=False)}"
            )
        lineas.append("")
    for herramienta in politica.herramientas:
        lineas.append("[[herramienta]]")
        lineas.append(f"nombre = {json.dumps(herramienta.nombre, ensure_ascii=False)}")
        lineas.append(f"canal = {json.dumps(herramienta.canal, ensure_ascii=False)}")
        lineas.append(f"version = {json.dumps(herramienta.version, ensure_ascii=False)}")
        lineas.append(
            f"integridad = {json.dumps(herramienta.integridad, ensure_ascii=False)}"
        )
        lineas.append("")
    for cuenta in politica.cuentas:
        lineas.append("[[cuenta]]")
        lineas.append(f"runtime = {json.dumps(cuenta.runtime, ensure_ascii=False)}")
        lineas.append(
            f"referencia = {json.dumps(cuenta.referencia, ensure_ascii=False)}"
        )
        lineas.append("")
    if politica.respaldo:
        r = politica.respaldo
        lineas.extend(
            [
                "[respaldo]",
                f"funcion = {json.dumps(r.funcion, ensure_ascii=False)}",
                f"modelo = {json.dumps(r.modelo, ensure_ascii=False)}",
                f"esfuerzo = {json.dumps(r.esfuerzo, ensure_ascii=False)}",
                f"hasta = {json.dumps(r.hasta, ensure_ascii=False)}",
                f"motivo = {json.dumps(r.motivo, ensure_ascii=False)}",
                "",
            ]
        )
    return "\n".join(lineas)


def guardar(path: Path, politica: Politica) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    texto = emitir(politica)
    desde_doc(tomllib.loads(texto))
    temporal = path.with_suffix(path.suffix + ".tmp")
    temporal.write_text(texto, encoding="utf-8")
    temporal.replace(path)


def sembrar_tabla(tabla: dict, generacion: str) -> Politica:
    cadenas = tabla.get("chains") or {}
    if not isinstance(cadenas, dict) or not cadenas:
        raise ManifiestoInvalido("la tabla no trae chains")
    puestos = []
    for funcion, etiquetas in cadenas.items():
        cadena = []
        for etiqueta in etiquetas:
            modelo, _, resto = str(etiqueta).partition(" (")
            if not resto.endswith(")"):
                raise ManifiestoInvalido(f"{funcion} trae una etiqueta sin esfuerzo")
            esfuerzo = resto[:-1]
            cadena.append((modelo.strip(), esfuerzo.strip()))
        huella = tuple(cadena)
        puestos.append(Puesto(str(funcion), huella))
    herencia_tabla = tabla.get("profile_inheritance") or {}
    herencia = tuple(sorted((str(k), str(v)) for k, v in herencia_tabla.items()))
    politica = Politica(
        generacion,
        tuple(puestos),
        herencia,
        (),
        (
            Cuenta("claude", "suscripcion"),
            Cuenta("codex", "suscripcion"),
            Cuenta("grok", "suscripcion"),
        ),
        None,
        ("poteto-mode",),
    )
    nombres = {puesto.funcion for puesto in politica.puestos}
    if {"dirigir", "dirigir-claw"} <= nombres and not misma_cadena(
        politica, "dirigir", "dirigir-claw"
    ):
        raise ManifiestoInvalido("dirigir y dirigir-claw no comparten cadena")
    return politica


def proyectar_tabla(politica: Politica) -> dict:
    chains = {}
    for puesto in politica.puestos:
        chains[puesto.funcion] = [
            f"{modelo} ({esfuerzo})" for modelo, esfuerzo in puesto.cadena
        ]
    return {
        "schema_version": 1,
        "source": "flota/manifesto.toml",
        "columns": ["Primary"]
        + [f"F{i}" for i in range(1, max(len(p.cadena) for p in politica.puestos))],
        "profile_inheritance": dict(politica.herencia),
        "chains": chains,
    }


def misma_cadena(politica: Politica, izquierda: str, derecha: str) -> bool:
    buscados = {izquierda: None, derecha: None}
    for puesto in politica.puestos:
        if puesto.funcion in buscados:
            buscados[puesto.funcion] = puesto.cadena
    if None in buscados.values():
        raise ManifiestoInvalido("falta un puesto para comparar Hermes y Claw")
    return buscados[izquierda] == buscados[derecha]


def etiqueta(modelo: str, esfuerzo: str) -> str:
    return f"{modelo} ({esfuerzo})"
