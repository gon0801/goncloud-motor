"""Resumen legible de los encargos activos y su envio periodico por el canal de avisos.

El resumen es texto plano, como los demas avisos: cada transporte lo escapa antes de
mandarlo a Telegram como HTML, asi que una etiqueta propia llegaria a la vista.
"""

from __future__ import annotations

import hashlib
import json
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .dominio import FASE_IMPLICITA, Encargo, reducir
from .registro import Registro

ESTADOS_ACTIVOS = (
    "recibido",
    "ejecutando",
    "esperando_reintento",
    "conciliando",
    "necesita_usuario",
    "deteniendo",
)
# Telegram admite 4096 caracteres; el transporte escapa `<`, `>` y `&` y el texto crece.
TOPE_MENSAJE = 3800
ALERTA_RECIBIDO_S = 15 * 60
ALERTA_ESPERA_S = 2 * 60 * 60
ALERTA_ESPERA = f"la misma espera lleva más de {ALERTA_ESPERA_S // 3600} h"
TOPE_TITULO = 70
TOPE_FRASE = 220

# Hora de David: Vancouver, UTC-7 todo el año. Fija para que las tres máquinas
# muestren la misma hora; la PC no trae base de zonas para `America/Vancouver`.
ZONA = timezone(timedelta(hours=-7))
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
NUMEROS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")

# Se busca cada clave dentro del nombre del modelo sin proveedor: `zai-coding-plan/glm-5.3-flash` es GLM.
NOMBRES = {
    "deepseek": "DeepSeek",
    "sonnet": "Sonnet",
    "opus": "Opus",
    "haiku": "Haiku",
    "fable": "Fable",
    "kimi": "Kimi",
    "k3": "Kimi",
    "glm": "GLM",
    "grok": "Grok",
    "qwen": "Qwen",
    "muse": "Muse",
    "gemini": "Gemini",
    "codex": "Codex",
    "gpt": "GPT",
}

_AVANCES = frozenset(
    (
        "producto_listo",
        "revision_aprobada",
        "entrega_externa",
        "actividad",
        "entrada_incorporada",
        "fase_reabierta",
        "resultado_directo",
        "impedimento_resuelto",
    )
)


@dataclass(frozen=True)
class Entrada:
    encargo: str
    titulo: str
    id_corto: str
    estado: str
    fase: str
    pr: str
    paso: str
    desde: str
    alertas: tuple[str, ...]


@dataclass(frozen=True)
class Resumen:
    cuerpo: str
    firma: str
    activos: int
    entradas: tuple[Entrada, ...]
    decisiones: tuple[tuple[str, str], ...]


def _frase(texto: str, tope: int = TOPE_FRASE) -> str:
    t = " ".join(str(texto or "").split())
    return t if len(t) <= tope else t[: tope - 1].rstrip() + "…"


def _titulo(enc: Encargo) -> str:
    lineas = [linea.strip() for linea in str(enc.texto or "").splitlines() if linea.strip()]
    titulo = lineas[0].lstrip("#").strip() if lineas else str(enc.clave or "")
    return _frase(titulo or enc.clave or enc.id, TOPE_TITULO)


def _epoch(hora: str) -> float | None:
    try:
        return datetime.fromisoformat(str(hora).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hora_legible(epoch: float) -> str:
    momento = datetime.fromtimestamp(epoch, ZONA)
    return f"{momento.day} {MESES[momento.month - 1]}, {momento:%H:%M}"


def _maquina(host: str) -> str:
    nombre = str(host or "").removesuffix(".local")
    llano = nombre.lower()
    if "mini" in llano:
        return "Mac Mini"
    if "macbook" in llano:
        return "MacBook"
    if llano == "pc" or llano.endswith("-pc"):
        return "PC"
    return nombre


def _nombre_modelo(modelo: str) -> str:
    pelado = str(modelo or "").strip().rsplit("/", 1)[-1]
    llano = pelado.lower()
    for clave, nombre in NOMBRES.items():
        if clave in llano:
            return nombre
    return pelado


def _hace(segundos: float) -> str:
    segundos = max(0, int(segundos))
    if segundos < 60:
        return "hace menos de un minuto"
    minutos = segundos // 60
    if minutos < 60:
        return f"hace {minutos} min"
    horas, minutos = divmod(minutos, 60)
    if horas < 24:
        return f"hace {horas} h" + (f" {minutos} min" if minutos else "")
    dias, horas = divmod(horas, 24)
    return f"hace {dias} d" + (f" {horas} h" if horas else "")


def _modelo(enc: Encargo, rol: str) -> str:
    for registro_rol, _agente, modelo in reversed(enc.modelos):
        if registro_rol == rol and modelo:
            return _nombre_modelo(modelo)
    for _registro_rol, _agente, modelo in reversed(enc.modelos):
        if modelo:
            return _nombre_modelo(modelo)
    return "un agente"


def _fase(enc: Encargo) -> str:
    fases = enc.fases
    if not fases:
        return "sin fases"
    actual = enc.fase_actual
    total = len(fases)
    posicion = total
    if actual is not None:
        posicion = next(
            (indice for indice, fase in enumerate(fases, 1) if fase.id == actual.id),
            total,
        )
    nombre = actual.id if actual is not None else ""
    if nombre == FASE_IMPLICITA:
        nombre = "" if total > 1 else "única"
    return f"{posicion} de {total} · {nombre}" if nombre else f"{posicion} de {total}"


def _pr(enc: Encargo) -> str:
    if not enc.pr_numero:
        return "sin abrir"
    repo = _repo_corto(enc.entrega.repo)
    return f"{repo} #{enc.pr_numero}" if repo else f"#{enc.pr_numero}"


def _repo_corto(valor: object) -> str:
    """`goncloud-motor` desde `git@github.com:o/goncloud-motor.git`, una URL u `o/nombre`."""
    texto = str(valor or "").strip().rstrip("/").removesuffix(".git")
    return texto.replace(":", "/").rsplit("/", 1)[-1]


def _espera_en_palabras(motivo: str) -> str:
    texto = str(motivo or "")
    llano = texto.lower()
    if "revisión externa" in llano or "revision externa" in llano:
        return "esperando la revisión de DeepSeek o checks"
    if "revisión de deepseek" in llano or "revision de deepseek" in llano:
        return "esperando la revisión de DeepSeek"
    if "checks requeridos" in llano:
        return "esperando checks"
    if not texto.strip():
        return "esperando para reintentar"
    return "esperando para reintentar: " + _frase(texto, 160)


def _paso(enc: Encargo) -> str:
    fase = enc.fase_actual
    espera_checks = bool(fase is not None and fase.checks_esperando_sha)
    if enc.estado == "necesita_usuario":
        motivo = _frase(enc.motivo or (enc.preguntas[-1] if enc.preguntas else ""), 160)
        return f"necesita tu decisión: {motivo}" if motivo else "necesita tu decisión"
    if enc.estado == "deteniendo":
        motivo = _frase(enc.motivo, 120)
        return f"deteniéndose: {motivo}" if motivo else "deteniéndose"
    if enc.estado == "conciliando":
        motivo = _frase(enc.motivo, 140)
        return f"conciliando: {motivo}" if motivo else "conciliando un efecto incierto"
    if enc.estado == "esperando_reintento":
        if espera_checks:
            return "esperando checks"
        return _espera_en_palabras(enc.impedimento_causa or enc.motivo)
    if enc.estado == "recibido":
        if espera_checks:
            return "esperando checks"
        if enc.proc is not None:
            return f"{_modelo(enc, enc.rol or 'responsable')} arrancando"
        return "recibido, sin agente todavía"
    if enc.estado == "ejecutando":
        if espera_checks:
            return "esperando checks"
        rol = enc.rol or "responsable"
        modelo = _modelo(enc, rol)
        return f"{modelo} revisando" if rol == "revisor" else f"{modelo} implementando"
    return _frase(enc.estado)


def _clase_de_espera(motivo: object) -> str:
    operacion = str(motivo or "").split(": ", 1)[0]
    limpio = " ".join(operacion.lower().split())
    for marca in ("sha ", "pr #"):
        if marca in limpio:
            limpio = limpio.split(marca, 1)[0]
    return limpio.strip(" .,;:-")


def _espera_sostenida(enc: Encargo, ahora: float, desde: float | None) -> bool:
    """La misma espera visible se ha sostenido más allá del umbral. El rearme
    normal (plazo y otra vez espera) conserva `desde`; el texto de la alerta es
    fijo para que la firma no cambie en cada vuelta."""
    if desde is None or ahora - desde < ALERTA_ESPERA_S:
        return False
    return _paso(enc).startswith("esperando ")


def _es_avance(evento: dict) -> bool:
    kind = str(evento.get("kind") or "")
    if kind in _AVANCES:
        return True
    if kind == "comprobacion":
        clase = str((evento.get("data") or {}).get("clase") or "")
        return clase in ("confirmado", "cierre_comprobado", "producto_aprobado")
    if kind == "revision":
        return str((evento.get("data") or {}).get("veredicto") or "") == "aprobado"
    return False


def _alerta_instrumento(eventos: list[dict]) -> str:
    ultimo_fallo: dict | None = None
    ultimo_avance = 0
    for evento in eventos:
        if evento.get("kind") == "instrumento_fallo":
            ultimo_fallo = evento
        elif _es_avance(evento):
            ultimo_avance = int(evento.get("id") or 0)
    if ultimo_fallo is None or int(ultimo_fallo.get("id") or 0) <= ultimo_avance:
        return ""
    causa = (ultimo_fallo.get("data") or {}).get("causa") or "fallo del instrumento"
    return "fallo de instrumento: " + _frase(causa, 140)


def _alerta_solicitud_tardia(eventos: list[dict]) -> str:
    ultima: dict | None = None
    ultimo_avance = 0
    for evento in eventos:
        if evento.get("kind") == "solicitud_tardia":
            ultima = evento
        elif _es_avance(evento) or str(evento.get("kind") or "") in (
            "solicitud",
            "resolucion",
            "respuesta",
            "autorizacion",
        ):
            ultimo_avance = int(evento.get("id") or 0)
    if ultima is None or int(ultima.get("id") or 0) <= ultimo_avance:
        return ""
    motivo = (ultima.get("data") or {}).get("motivo") or "fuera de la fase o del intento"
    return "solicitud tardía: " + _frase(motivo, 140)


def _alertas(
    enc: Encargo,
    eventos: list[dict],
    ahora: float,
    desde_estado: float | None = None,
    desde_paso: float | None = None,
) -> tuple[str, ...]:
    alertas: list[str] = []
    if enc.estado == "recibido" and enc.proc is None:
        inicio = desde_estado if desde_estado is not None else _epoch(enc.hora)
        if inicio is not None and ahora - inicio > ALERTA_RECIBIDO_S:
            alertas.append("recibido sin agente hace más de 15 minutos")
    if enc.no_concluyentes >= 2:
        alertas.append(f"{enc.no_concluyentes} revisiones no concluyentes seguidas")
    if _espera_sostenida(enc, ahora, desde_paso):
        alertas.append(ALERTA_ESPERA)
    instrumento = _alerta_instrumento(eventos)
    if instrumento:
        alertas.append(instrumento)
    for impedimento in enc.impedimentos:
        causa = impedimento.causa or impedimento.clase
        alertas.append("impedimento pendiente: " + _frase(causa, 140))
    if not enc.impedimentos and enc.impedimento:
        alertas.append("impedimento pendiente: " + _frase(enc.impedimento_causa or enc.impedimento, 140))
    if any(solicitud.tardias for solicitud in enc.solicitudes):
        alertas.append("hay una respuesta tardía que no se aplicó")
    tardia = _alerta_solicitud_tardia(eventos)
    if tardia:
        alertas.append(tardia)
    return tuple(dict.fromkeys(alertas))


def _desde(enc: Encargo, eventos: list[dict]) -> float | None:
    """Desde cuándo está en el paso que se muestra. Un rearme de la misma espera
    (el supervisor vence el plazo y vuelve a marcar la misma espera) no empieza
    un paso nuevo: conserva la marca de cuando empezó la espera."""
    anterior: Encargo | None = None
    marca: float | None = None
    clave: str | None = None
    rearme: tuple[str | None, float | None] | None = None
    try:
        for evento in eventos:
            actual = reducir(anterior, evento)
            visible = _paso(actual)
            kind = str(evento.get("kind") or "")
            if anterior is not None and visible != clave:
                momento = _epoch(str(evento.get("ts") or ""))
                if momento is None:
                    momento = marca
                if kind == "plazo":
                    rearme = (clave, marca)
                    marca = momento
                elif kind == "espera" and rearme is not None and visible == rearme[0]:
                    marca = rearme[1]
                    rearme = None
                else:
                    marca = momento
                    rearme = None
            clave = visible
            anterior = actual
    except Exception:  # noqa: BLE001  un tramo ilegible no oculta el resumen
        return _epoch(enc.hora)
    if marca is None and eventos:
        marca = _epoch(str(eventos[0].get("ts") or ""))
    return marca if marca is not None else _epoch(enc.hora)


def _desde_estado(enc: Encargo, eventos: list[dict]) -> float | None:
    anterior: Encargo | None = None
    marca: str | None = None
    try:
        for evento in eventos:
            actual = reducir(anterior, evento)
            if (anterior.estado if anterior else None) != actual.estado:
                marca = str(evento.get("ts") or "") or marca
            anterior = actual
    except Exception:  # noqa: BLE001  un tramo ilegible no oculta el resumen
        return _epoch(enc.hora)
    return _epoch(marca or enc.hora)


def _decisiones_de(enc: Encargo) -> list[tuple[str, str]]:
    salida: list[tuple[str, str]] = []
    titulo = _titulo(enc)
    if enc.estado == "necesita_usuario":
        motivo = _frase(enc.motivo or (enc.preguntas[-1] if enc.preguntas else ""), 200)
        salida.append((titulo, motivo or "hace falta una decisión"))
    for solicitud in enc.solicitudes:
        if solicitud.estado != "pregunta":
            continue
        texto = _frase(solicitud.pregunta or solicitud.motivo, 200)
        salida.append((titulo, texto or "hay una pregunta pendiente"))
    return salida


def _entrada(enc: Encargo, eventos: list[dict], ahora: float) -> Entrada:
    desde = _desde(enc, eventos)
    return Entrada(
        encargo=enc.id,
        titulo=_titulo(enc),
        id_corto=enc.id[:12],
        estado=enc.estado,
        fase=_fase(enc),
        pr=_pr(enc),
        paso=_paso(enc),
        desde=_hace(ahora - desde) if desde is not None else "desde el inicio",
        alertas=_alertas(
            enc, eventos, ahora, _desde_estado(enc, eventos), desde
        ),
    )


def _componer(
    entradas: tuple[Entrada, ...],
    decisiones: tuple[tuple[str, str], ...],
    host: str,
    ahora: float,
) -> str:
    activos = len(entradas)
    cabecera = "\n".join(
        (
            "📋 Resumen de encargos",
            f"🖥️ {_maquina(host)} · {_hora_legible(ahora)} · "
            + (f"{activos} activo" if activos == 1 else f"{activos} activos"),
        )
    )
    bloques = [cabecera]
    for indice, entrada in enumerate(entradas, 1):
        numero = NUMEROS[indice - 1] if indice <= len(NUMEROS) else f"{indice}."
        filas = [
            f"{numero} {entrada.titulo} ({entrada.id_corto})",
            f"📍 Fase: {entrada.fase}",
            f"🔗 PR: {entrada.pr}",
            f"⏳ Ahora: {entrada.paso}",
            f"🕐 Desde: {entrada.desde}",
        ]
        for alerta in entrada.alertas:
            filas.append(f"⚠️ Alerta: {alerta}")
        bloques.append("\n".join(filas))
    if decisiones:
        pie = ["🙋 Necesitan tu decisión"]
        for titulo, motivo in decisiones:
            pie.append(f"• {titulo}: {motivo}")
    else:
        pie = ["✅ Nada espera tu decisión."]
    bloques.append("\n".join(pie))
    return "\n\n".join(bloques)


def _firma(entradas: tuple[Entrada, ...], decisiones: tuple[tuple[str, str], ...]) -> str:
    datos = {
        "encargos": [
            {
                "encargo": entrada.encargo,
                "titulo": entrada.titulo,
                "estado": entrada.estado,
                "fase": entrada.fase,
                "pr": entrada.pr,
                "paso": entrada.paso,
                "alertas": list(entrada.alertas),
            }
            for entrada in entradas
        ],
        "decisiones": list(decisiones),
    }
    return hashlib.sha256(
        json.dumps(datos, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def construir(registro: Registro, ahora: float | None = None, host: str = "") -> Resumen:
    momento = time.time() if ahora is None else float(ahora)
    entradas: list[Entrada] = []
    decisiones: list[tuple[str, str]] = []
    vistos: set[tuple[str, str]] = set()
    for ident in registro.ids():
        enc = registro.fold(ident)
        if enc is None or enc.estado not in ESTADOS_ACTIVOS:
            continue
        eventos = registro.eventos(ident)
        entradas.append(_entrada(enc, eventos, momento))
        for titulo, motivo in _decisiones_de(enc):
            clave = (titulo.lower(), motivo.lower())
            if clave in vistos:
                continue
            vistos.add(clave)
            decisiones.append((titulo, motivo))
    tuplas = tuple(entradas)
    decididas = tuple(decisiones)
    return Resumen(
        cuerpo=_componer(tuplas, decididas, host or socket.gethostname(), momento),
        firma=_firma(tuplas, decididas),
        activos=len(tuplas),
        entradas=tuplas,
        decisiones=decididas,
    )


def texto(registro: Registro, ahora: float | None = None, host: str = "") -> str:
    return construir(registro, ahora=ahora, host=host).cuerpo


def _trocear(texto: str, tope: int) -> list[str]:
    salida: list[str] = []
    resto = texto
    while len(resto) > tope:
        corte = resto.rfind("\n", 0, tope)
        if corte <= 0:
            corte = resto.rfind(" ", 0, tope)
        if corte <= 0:
            corte = tope
        salida.append(resto[:corte].rstrip())
        resto = resto[corte:].lstrip("\n")
    if resto:
        salida.append(resto)
    return salida


def partir(texto: str, tope: int = TOPE_MENSAJE) -> list[str]:
    if len(texto) <= tope:
        return [texto] if texto else []
    partes: list[str] = []
    actual: list[str] = []
    for bloque in str(texto).split("\n\n"):
        if len(bloque) > tope:
            if actual:
                partes.append("\n\n".join(actual))
                actual = []
            partes.extend(_trocear(bloque, tope))
            continue
        candidato = "\n\n".join([*actual, bloque])
        if actual and len(candidato) > tope:
            partes.append("\n\n".join(actual))
            actual = []
        actual.append(bloque)
    if actual:
        partes.append("\n\n".join(actual))
    return [parte for parte in partes if parte]


def _canal_con_transporte(valor: object) -> bool:
    return isinstance(valor, dict) and bool(valor.get("comando"))


def config_envio(base: Path) -> tuple[str, int]:
    """Canal e intervalo del envío. `activo: false` lo apaga y un canal pedido
    sin transporte no se usa: no hay envío en vez de un envío a un canal fantasma."""
    cfg = _leer_json(Path(base) / "resumen-config.json")
    if not cfg:
        return "", 60
    intervalo = cfg.get("intervalo_min", cfg.get("intervalo_minutos", 60))
    try:
        intervalo = max(1, int(intervalo))
    except (TypeError, ValueError):
        intervalo = 60
    if cfg.get("activo") is False:
        return "", intervalo
    canales = _leer_json(Path(base) / "canal.json")
    if "canal" in cfg:
        canal = str(cfg.get("canal") or "")
        return (canal, intervalo) if _canal_con_transporte(canales.get(canal)) else ("", intervalo)
    for nombre, valor in canales.items():
        if _canal_con_transporte(valor):
            return str(nombre), intervalo
    return "", intervalo


def asegurar_config(base: Path) -> bool:
    """La primera puesta en marcha del supervisor activa el resumen en el primer canal con transporte."""
    base = Path(base)
    destino = base / "resumen-config.json"
    if destino.exists():
        return False
    canales = _leer_json(base / "canal.json")
    for nombre, valor in canales.items():
        if not isinstance(valor, dict) or not valor.get("comando"):
            continue
        _atomico(destino, {"canal": str(nombre), "intervalo_min": 60})
        return True
    return False


def _leer_json(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _atomico(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(path.suffix + ".tmp")
    temporal.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporal.replace(path)


def estado(base: Path) -> dict:
    return _leer_json(Path(base) / "resumen.json")


def _avisos(base: Path) -> list[dict]:
    carpeta = Path(base) / "avisos"
    if not carpeta.is_dir():
        return []
    salida: list[dict] = []
    for path in sorted(carpeta.glob("*.json")):
        doc = _leer_json(path)
        if doc.get("tipo") == "resumen":
            salida.append(doc)
    return salida


def _descartar(base: Path, pendientes: list[dict], motivo: str) -> None:
    for doc in pendientes:
        doc["estado"] = "descartado"
        doc["motivo"] = motivo
        for campo in ("proximo_intento", "fallos"):
            doc.pop(campo, None)
        _atomico(Path(base) / "avisos" / f"{doc['id']}.json", doc)


def _faltan_partes(existentes: dict[str, dict], firma: str, episodio: int) -> bool:
    docs = [
        doc
        for doc in existentes.values()
        if str(doc.get("firma") or "") == firma
        and int(doc.get("episodio") or 0) == int(episodio)
        and doc.get("estado") != "descartado"
    ]
    if not docs:
        return True
    total = max(int(doc.get("partes") or 1) for doc in docs)
    publicadas = {
        int(doc.get("parte") or 1)
        for doc in docs
        if doc.get("estado") == "publicado"
        and doc.get("recibo")
        and doc.get("alcance") == "externo"
    }
    return bool(set(range(1, total + 1)) - publicadas)


def preparar(registro: Registro, ahora: float | None = None) -> list[tuple[dict, str]]:
    momento = time.time() if ahora is None else float(ahora)
    base = Path(registro.base)
    canal, intervalo = config_envio(base)
    if not canal:
        return []
    existentes = {str(doc.get("id") or ""): doc for doc in _avisos(base)}
    pendientes = [
        doc
        for doc in existentes.values()
        if doc.get("estado") not in ("publicado", "descartado")
    ]
    guardado = estado(base)
    estado_ultimo = _epoch(str(guardado.get("ultimo_envio") or "")) or 0
    publicados = [
        doc
        for doc in existentes.values()
        if doc.get("estado") == "publicado"
        and doc.get("recibo")
        and doc.get("alcance") == "externo"
    ]
    ultimo_publicado = (
        max(publicados, key=lambda doc: int(doc.get("episodio") or 0))
        if publicados
        else None
    )
    ultimo_real = max(
        (
            int(ultimo_publicado.get("episodio") or 0) / 1_000_000_000
            if ultimo_publicado is not None
            else 0
        ),
        estado_ultimo,
    )
    if not pendientes and ultimo_real and momento - ultimo_real < intervalo * 60:
        return []
    actual = construir(registro, ahora=momento)
    if actual.activos == 0:
        if pendientes:
            _descartar(base, pendientes, "no hay encargos activos")
        return []
    ultima_firma = str(
        (ultimo_publicado or {}).get("firma")
        or guardado.get("ultima_firma")
        or ""
    )
    ultimo_episodio = int((ultimo_publicado or {}).get("episodio") or 0)
    pend_actual = [
        doc for doc in pendientes if str(doc.get("firma") or "") == actual.firma
    ]
    otros_pend = [
        doc for doc in pendientes if str(doc.get("firma") or "") != actual.firma
    ]
    if otros_pend:
        _descartar(base, otros_pend, "hay un resumen más nuevo")
    if actual.firma == ultima_firma:
        mismo_episodio = [
            doc for doc in pend_actual if int(doc.get("episodio") or 0) == ultimo_episodio
        ]
        sobran = [
            doc
            for doc in pend_actual
            if str(doc.get("id") or "") not in {str(x.get("id") or "") for x in mismo_episodio}
        ]
        if sobran:
            _descartar(base, sobran, "hay un resumen más nuevo")
        if mismo_episodio:
            return [
                (doc, str(doc.get("cuerpo") or ""))
                for doc in mismo_episodio
                if str(doc.get("cuerpo") or "")
            ]
        if not _faltan_partes(existentes, actual.firma, ultimo_episodio):
            cerrar(registro, momento, actual.firma, ultimo_episodio)
            return []
        episodio_actual = ultimo_episodio
    else:
        if pend_actual:
            max_pend = max(int(doc.get("episodio") or 0) for doc in pend_actual)
            if max_pend > ultimo_episodio:
                retener = [
                    doc
                    for doc in pend_actual
                    if int(doc.get("episodio") or 0) == max_pend
                ]
                retener_ids = {str(doc.get("id") or "") for doc in retener}
                sobran = [
                    doc
                    for doc in pend_actual
                    if str(doc.get("id") or "") not in retener_ids
                ]
                if sobran:
                    _descartar(base, sobran, "hay un resumen más nuevo")
                return [
                    (doc, str(doc.get("cuerpo") or ""))
                    for doc in retener
                    if str(doc.get("cuerpo") or "")
                ]
            _descartar(base, pend_actual, "hay un resumen más nuevo")
        if ultimo_real and momento - ultimo_real < intervalo * 60:
            return []
        maximo = max(
            [
                ultimo_episodio,
                *[int(doc.get("episodio") or 0) for doc in existentes.values()],
                0,
            ]
        )
        episodio_actual = max(int(momento * 1_000_000_000), maximo + 1)
    partes = partir(actual.cuerpo)
    salida: list[tuple[dict, str]] = []
    for indice, parte in enumerate(partes, 1):
        ident = "av_" + hashlib.sha256(
            f"resumen:{actual.firma}:{indice}:{episodio_actual}".encode()
        ).hexdigest()[:16]
        previo = existentes.get(ident)
        if previo is not None and previo.get("estado") == "publicado":
            continue
        if previo is not None and previo.get("estado") not in ("descartado",):
            salida.append((previo, str(previo.get("cuerpo") or parte)))
            continue
        aviso = {
            "id": ident,
            "encargo": "",
            "canal": canal,
            "evidencia": f"resumen:{actual.firma}:{indice}:{episodio_actual}",
            "estado": "pendiente",
            "recibo": "",
            "tipo": "resumen",
            "firma": actual.firma,
            "episodio": episodio_actual,
            "parte": indice,
            "partes": len(partes),
            "cuerpo": parte,
        }
        _atomico(base / "avisos" / f"{ident}.json", aviso)
        salida.append((aviso, parte))
    return salida


def cerrar(
    registro: Registro, ahora: float, firma: str, episodio: int | None = None
) -> bool:
    if not firma:
        return False
    base = Path(registro.base)
    guardado = estado(base)
    if (
        str(guardado.get("ultima_firma") or "") == firma
        and guardado.get("ultimo_envio")
        and (
            episodio is None
            or int(guardado.get("ultimo_episodio") or 0) == int(episodio)
        )
    ):
        return True
    avisos = [
        doc
        for doc in _avisos(base)
        if str(doc.get("firma") or "") == firma and doc.get("estado") != "descartado"
    ]
    if not avisos:
        return False
    if episodio is None:
        episodio = max(int(doc.get("episodio") or 0) for doc in avisos)
    avisos = [
        doc for doc in avisos if int(doc.get("episodio") or 0) == int(episodio)
    ]
    if not avisos:
        return False
    total = max(int(doc.get("partes") or 1) for doc in avisos)
    partes = {int(doc.get("parte") or 1): doc for doc in avisos}
    if set(range(1, total + 1)) - set(partes):
        return False
    for doc in partes.values():
        if not (
            doc.get("estado") == "publicado"
            and doc.get("recibo")
            and doc.get("alcance") == "externo"
        ):
            return False
    _atomico(
        base / "resumen.json",
        {
            "ultima_firma": firma,
            "ultimo_envio": _iso(ahora),
            "ultimo_id": avisos[-1].get("id") or "",
            "ultimo_episodio": int(episodio),
        },
    )
    return True
