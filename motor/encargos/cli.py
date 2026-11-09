"""Comandos del encargo. El texto del pedido no entra al argv remoto."""

from __future__ import annotations
from motor.io import leer_json, leer_texto

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

from .agentes import (
    _disponible,
    _lanzador,
    _modelos_para,
    _sesion_modo,
    leer_disponible,
)
from .catalogo_agentes import (
    ADAPTADORES,
    agente_del_modelo,
    identidad_modelo,
)
from .lectura import Error as ErrorLectura
from .lectura import Leido, Lectura
from .politica import relevo_de_sesion
from .ciclo import (
    afirmar,
    ahora_iso,
    barrer,
    consultar,
    corregir,
    marcar_espera,
    marcar_pregunta,
    marcar_relevo_agotado,
    ordenar,
    parada_local,
    plegar_aislado,
    recibir,
    resolver_entrega,
)
from .dominio import relevos_de
from .frontera import DestinoEnsayo, despachar
from .registro import BaseProhibida, Registro, exigir_base
from .rutas import candidatas_de, elegir, proximo, solicitado_de
from .seleccion import solicitar_modelos


def _json(datos: dict) -> int:
    print(json.dumps(datos, ensure_ascii=False, sort_keys=True))
    if datos.get("conflicto") or datos.get("rechazada"):
        return 2
    return 0


def _registro(base: Path) -> Registro:
    return Registro(exigir_base(base))


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt":
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="motor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("encargar")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--texto", default="")
    s.add_argument("--archivo", type=Path)
    s.add_argument("--clave", required=True)
    s.add_argument("--director", default="cli")
    s.add_argument("--restriccion", action="append", default=[])
    s.add_argument("--permitir", action="append", default=[])
    s.add_argument("--prohibir", action="append", default=[])
    s.add_argument("--canal", default="")
    s.add_argument("--via", choices=("directa", "durable"), default="durable")
    s.add_argument(
        "--entrega",
        choices=("terminado", "pr", "integrado", "desplegado"),
        default=None,
    )
    s.add_argument("--repo", default="")
    s.add_argument("--rama", default="")
    s.add_argument("--base-rama", default="")
    s.add_argument("--responsable", default="")
    s.add_argument("--revisor", default="")

    s = sub.add_parser("ver")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)

    for nombre in ("parar", "continuar", "cancelar", "reabrir"):
        s = sub.add_parser(nombre)
        s.add_argument("encargo")
        s.add_argument("--base", type=Path, required=True)
        s.add_argument("--clave", required=True)
        if nombre == "reabrir":
            s.add_argument("--fase", default="")
            s.add_argument("--cierre", default="")

    s = sub.add_parser("corregir")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--clave", required=True)
    s.add_argument("--texto", required=True)
    s.add_argument("--cierra", action="append", default=[])
    s.add_argument("--evidencia", default="")

    s = sub.add_parser("efecto")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--encargo", required=True)
    s.add_argument("--id", required=True)
    s.add_argument("--operacion", required=True)
    s.add_argument("--clave-destino", required=True)
    s.add_argument("--cuerpo", default="")

    s = sub.add_parser("espera")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--motivo", required=True)
    s.add_argument("--reanuda-en", required=True)

    s = sub.add_parser("pregunta")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--texto", required=True)

    s = sub.add_parser("responder")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--solicitud", required=True)
    s.add_argument("--respuesta", required=True)
    s.add_argument("--autor", default="propietario")
    s.add_argument("--clave", required=True)

    s = sub.add_parser("capacidades")
    s.add_argument("--base", type=Path, required=True)

    s = sub.add_parser("afirmacion")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--texto", required=True)

    s = sub.add_parser("parada-local")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--clave", required=True)

    s = sub.add_parser("supervisor")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--una-vez", action="store_true")
    s.add_argument("--intervalo", type=float, default=1.0)
    s.add_argument("--umbral", type=float, default=30.0)
    s.add_argument("--agente", choices=(*ADAPTADORES, "sleep", "ensayo"), default="")
    s.add_argument("--respaldo", choices=tuple(ADAPTADORES), action="append", default=[])

    s = sub.add_parser("traer-main")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--contra", default="")
    s.add_argument("--remoto", default="origin")

    s = sub.add_parser("encargos-aplicar")
    s.add_argument("--base", type=Path)

    s = sub.add_parser("encargos-migrar")
    s.add_argument("--desde", type=Path, required=True)
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--host", required=True)
    s.add_argument("--motor06", action="store_true")

    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return _correr(args)
    except BaseProhibida as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001  la CLI es el borde
        print(str(exc), file=sys.stderr)
        return 1


def _correr(args) -> int:
    hora = ahora_iso()
    if args.cmd == "encargos-aplicar":
        return _aplicar_stdin(args.base)
    reg = _registro(args.base)
    try:
        return _con_registro(args, reg, hora)
    finally:
        reg.close()


def _con_registro(args, reg: Registro, hora: str) -> int:
    if args.cmd == "encargos-migrar":
        from .migracion import crear_motor06, importar_encargos

        relacion = importar_encargos(args.desde, reg, args.host, hora)
        if args.motor06:
            relacion.append(crear_motor06(reg, hora))
        print(json.dumps(relacion, ensure_ascii=False, sort_keys=True))
        return 0
    if args.cmd == "traer-main":
        from .integracion import traer_main

        return _json(
            traer_main(
                reg, args.encargo, hora,
                contra=args.contra, remoto=args.remoto,
            )
        )
    if args.cmd == "encargar":
        texto = args.texto
        adjuntos: tuple[str, ...] = ()
        if args.archivo:
            contenido = leer_texto(args.archivo)
            texto = f"{texto}\n\n{contenido}" if texto else contenido
            adjuntos = (str(args.archivo.resolve()),)
        if not texto.strip():
            print("falta el pedido", file=sys.stderr)
            return 2
        return _json(
            recibir(
                reg,
                texto,
                args.clave,
                hora,
                director=args.director,
                adjuntos=adjuntos,
                restricciones=tuple(args.restriccion),
                permisos=tuple(args.permitir),
                prohibiciones=tuple(args.prohibir),
                canal=args.canal,
                via=args.via,
                entrega_nivel=resolver_entrega(args.entrega, args.repo, args.via),
                repo=args.repo,
                rama=args.rama,
                base_rama=args.base_rama,
                modelos_solicitados=solicitar_modelos(args.responsable, args.revisor),
                gh=_puerto_github(args.base),
            )
        )
    if args.cmd == "ver":
        return _json(consultar(reg, args.encargo))
    if args.cmd == "reabrir":
        return _json(ordenar(
            reg, args.encargo, args.cmd, args.clave, hora,
            fase=args.fase, cierre=args.cierre,
        ))
    if args.cmd in ("parar", "continuar", "cancelar"):
        salida = ordenar(reg, args.encargo, args.cmd, args.clave, hora)
        if (
            args.cmd in ("parar", "cancelar")
            and not salida.get("retransmision")
            and not salida.get("rechazada")
        ):
            recibo = salida["recibo"]
            fin = time.time() + 8
            while time.time() < fin:
                barrer(reg, ahora_iso(), time.time(), None, umbral_s=10**9)
                actual = consultar(reg, args.encargo)
                actual["recibo"] = recibo
                salida = actual
                if actual["estado"] in ("detenido", "cancelado"):
                    break
                time.sleep(0.2)
        return _json(salida)
    if args.cmd == "corregir":
        return _json(
            corregir(
                reg,
                args.encargo,
                args.texto,
                args.clave,
                hora,
                cierra=args.cierra,
                evidencia=args.evidencia,
            )
        )
    if args.cmd == "efecto":
        destino = DestinoEnsayo(reg.base / "destino")
        return _json(
            despachar(
                reg,
                args.encargo,
                destino,
                args.id,
                args.operacion,
                args.clave_destino,
                args.cuerpo,
                hora,
            )
        )
    if args.cmd == "espera":
        return _json(
            marcar_espera(reg, args.encargo, args.motivo, args.reanuda_en, hora)
        )
    if args.cmd == "pregunta":
        # La pregunta es un aviso: no cambia el estado ni espera a una persona.
        return _json(marcar_pregunta(reg, args.encargo, args.texto, False, hora))
    if args.cmd == "responder":
        from .ciclo import _directorio, _espacio
        from .necesidades import continuar_respuestas, responder

        salida = responder(
            reg,
            args.encargo,
            args.solicitud,
            args.respuesta,
            args.autor,
            args.clave,
            hora,
        )
        if salida.get("aplicada"):
            enc = reg.fold(args.encargo)
            if enc is not None:
                salida["continuacion"] = continuar_respuestas(
                    reg,
                    enc,
                    _espacio(reg.base, args.encargo),
                    _directorio(reg.base, args.encargo),
                    hora,
                )
        return _json(salida)
    if args.cmd == "capacidades":
        from .capacidades import ConfigInvalida, cargar, catalogo

        try:
            recursos = cargar(reg.base)
        except ConfigInvalida as exc:
            return _json({"error": str(exc), "recursos": []})
        return _json({"recursos": catalogo(recursos)})
    if args.cmd == "afirmacion":
        return _json(afirmar(reg, args.encargo, args.texto, hora))
    if args.cmd == "parada-local":
        return _json(parada_local(reg.base, args.clave, hora))
    if args.cmd == "supervisor":
        return _supervisor(reg, args)
    print(f"comando desconocido: {args.cmd}", file=sys.stderr)
    return 2


def _puerto_github(base: Path):
    """Puerto GitHub del supervisor desde base/github.json.

    {"falso": "/ruta"} usa FakeGitHub (ensayos y repos locales);
    {"real": true} usa gh de verdad. Sin archivo, los encargos con
    nivel pr o mas esperan visibles en el paso de entrega.
    """
    path = base / "github.json"
    if not path.is_file():
        return None
    try:
        doc = leer_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    from motor.github import FakeGitHub, GhGitHub

    if doc.get("falso"):
        return FakeGitHub(Path(str(doc["falso"])).expanduser())
    if doc.get("real"):
        return GhGitHub()
    return None


_NOMBRE_AGENTE = {
    "claude": "Claude",
    "codex": "Codex",
    "grok": "Grok",
    "muse": "Muse",
    "kimi": "Kimi",
    "opencode": "OpenCode",
    "qwen": "Qwen",
    "antigravity": "Antigravity",
}


def _motivo_de_sesion(agente: str, lectura: Lectura) -> str:
    nombre = _NOMBRE_AGENTE.get(agente, agente)
    if isinstance(lectura, ErrorLectura):
        if agente == "claude":
            return (
                f"no pude leer la sesión de {nombre} del Llavero: "
                f"{lectura.causa}"
            )
        return f"no pude leer la sesión de {nombre}: {lectura.causa}"
    return f"no hay sesión de {nombre} en este host"


def _relevo_para_sesion(
    rol: str,
    solicitado: tuple[str, str] | None,
    autorizados: dict[str, tuple[str, ...]],
    lecturas: dict[str, Lectura],
    generacion: Path | None,
) -> tuple[str, str] | None:
    """El relevo que la política autoriza cuando la sesión no se puede leer.

    Solo actúa sobre la causa que el propietario decidió (Opus ilegible para
    revisar): un modelo que de verdad falta no se sustituye.
    """
    if solicitado:
        preferido_agente, preferido = solicitado
    else:
        preferido_agente, preferido = next(
            (
                (agente, modelos[0])
                for agente, modelos in autorizados.items()
                if modelos and modelos[0]
            ),
            ("", ""),
        )
    sustituto = relevo_de_sesion(rol, preferido)
    if not sustituto:
        return None
    if not isinstance(lecturas.get(preferido_agente), ErrorLectura):
        return None
    agente = agente_del_modelo(sustituto)
    if not agente:
        return None
    if isinstance(leer_disponible(agente, generacion), Leido):
        return agente, sustituto
    return None


def lanzar_de_ruta(reg: Registro, ruta: tuple[str, ...]):
    """El lanzador de producción para una ruta de agentes: elige por rol con
    `_modelos_para`/`_disponible` y lanza por el adaptador elegido. `ruta`
    vacía deja que cada encargo use su cadena persistida (modelos solicitados).
    El banco de compuerta lo usa con una ruta fija, que es lo que el supervisor
    recibe de `--agente`."""

    def lanzar(enc, prompt, espacio, sesion):
        hora = ahora_iso()
        rol = enc.rol or "responsable"
        solicitado = solicitado_de(enc, rol)
        candidatas = candidatas_de(enc, rol)
        if not candidatas:
            marcar_espera(
                reg, enc.id, f"sin ruta de agentes para el rol {rol}",
                proximo(enc, hora, set()), hora,
            )
            return None
        generacion = None
        referencia = reg.base / "ejecucion" / enc.id / f"intento-{enc.generacion}-{enc.conductor}" / "generacion.json"
        if referencia.is_file():
            generacion = Path(leer_json(referencia)["ruta"])
        autorizados = {
            agente: (solicitado[1],) if solicitado else _modelos_para(agente, rol, generacion)
            for agente in candidatas
        }
        lecturas = {
            agente: leer_disponible(agente, generacion) for agente in autorizados
        }
        disponibles = {
            agente: modelos for agente, modelos in autorizados.items()
            if _disponible(agente, generacion)
        }
        excluir = (
            frozenset(m for r, _a, m in enc.modelos if r == "responsable")
            if rol == "revisor"
            else frozenset()
        )
        relevo_del_rol = relevos_de(enc, rol)
        if relevo_del_rol > 0:
            # Relevo: el mismo rol pasa a otro modelo autorizado que todavía
            # no haya corrido en esta fase. La cuenta es del rol: uno del
            # responsable no excluye modelos del revisor ni al revés.
            excluir = frozenset(excluir) | frozenset(
                m for r, _a, m in enc.modelos_fase if r == rol
            )
        elegido = elegir(
            replace(enc, agentes=candidatas), hora, disponibles, rol=rol, excluir_modelos=excluir
        )
        if elegido:
            agente, modelo = elegido
            modo = _sesion_modo(enc, agente, rol)
            argv = _lanzador(agente, modelo, modo)(enc, prompt, espacio, sesion)
            return argv, {"agente": agente, "modelo": modelo, "sesion_modo": modo}
        relevo_por_sesion = _relevo_para_sesion(
            rol, solicitado, autorizados, lecturas, generacion
        )
        if relevo_por_sesion:
            agente, modelo = relevo_por_sesion
            constructor = _lanzador(agente, modelo, _sesion_modo(enc, agente, rol))
            if constructor is not None:
                modo = _sesion_modo(enc, agente, rol)
                argv = constructor(enc, prompt, espacio, sesion)
                return argv, {
                    "agente": agente,
                    "modelo": modelo,
                    "sesion_modo": modo,
                }
        if relevo_del_rol > 0 and all(
            identidad_modelo(m) in {identidad_modelo(x) for x in excluir}
            for modelos in autorizados.values() for m in modelos if m
        ):
            marcar_relevo_agotado(reg, enc, rol, hora)
            return None
        if rol == "revisor" and disponibles and all(
            identidad_modelo(m) in {identidad_modelo(x) for x in excluir}
            for modelos in disponibles.values() for m in modelos
        ):
            motivo = "sin modelo de revisión distinto disponible"
        else:
            motivos = [
                p.causa for p in enc.pausas if p.agente in disponibles and p.hasta > hora
            ]
            causas = [
                _motivo_de_sesion(agente, lecturas[agente])
                for agente in autorizados
                if agente not in disponibles
            ]
            motivo = (
                "; ".join(motivos)
                or "; ".join(causas)
                or "sin ruta de agentes disponible en este host"
            )
        marcar_espera(reg, enc.id, motivo,
                     proximo(replace(enc, agentes=candidatas), hora, set(disponibles)), hora)
        return None

    return lanzar


def marcar_ruta(reg: Registro, ruta: tuple[str, ...], hora: str) -> None:
    """La ruta del supervisor se queda persistida en cada encargo abierto, con
    el mismo ukey de la CLI: si el supervisor muere, la recuperación la lee."""

    from .frontera import turno

    if not ruta:
        return
    with turno(reg.base):
        for ident in reg.ids():
            enc = plegar_aislado(reg, ident)
            if enc is None:
                continue
            if enc.agentes != ruta and enc.estado not in ("terminado", "cancelado"):
                reg.anotar(
                    ident,
                    {"kind": "ruta_agentes", "data": {"agentes": list(ruta), "hora": hora}},
                    f"ruta:{ident}:{len(reg.eventos(ident))}",
                )


def _atender_respuestas_antes_del_barrido(reg: Registro, hora: str) -> None:
    """Una respuesta registrada se ejecuta o se deniega antes de lanzar.

    El barrido puede abrir el turno del revisor en la misma pasada; si la
    respuesta se atendiera después, ese primer turno correría sin su resultado.
    Un encargo que no pliega se aísla y no deja sin respuesta a los demás.
    """
    try:
        from .ciclo import _directorio, _espacio
        from .necesidades import continuar_respuestas
    except Exception:
        return
    for ident in reg.ids():
        try:
            enc = plegar_aislado(reg, ident)
            if enc is None:
                continue
            if any(s.estado == "respuesta" for s in enc.solicitudes):
                continuar_respuestas(
                    reg,
                    enc,
                    _espacio(reg.base, ident),
                    _directorio(reg.base, ident),
                    hora,
                )
        except Exception:
            # El fallo de un encargo no deja sin respuesta a los demás.
            continue


def _paso_del_supervisor(
    reg: Registro,
    hora: str,
    lanzar,
    habilitado: bool,
    umbral,
    gh,
    drenador,
) -> list[dict]:
    _atender_respuestas_antes_del_barrido(reg, hora)
    salida = barrer(
        reg,
        hora,
        time.time(),
        lanzar if habilitado else None,
        umbral_s=umbral,
        gh=gh,
    )
    try:
        if drenador is None:
            from motor.integraciones.directores import drenar

            drenar(reg)
        else:
            drenador.lanzar()
    except Exception:
        pass
    try:
        from motor.integraciones.directores import conciliar_autoridad

        conciliar_autoridad(reg)
    except Exception:
        pass
    return salida


def _hay_ruta(reg: Registro, ruta: tuple[str, ...]) -> bool:
    """Hay una ruta fija o al menos un encargo legible con ruta persistida.

    Un encargo que no pliega no aporta ruta y no impide mirar los demás."""
    if ruta:
        return True
    for ident in reg.ids():
        enc = plegar_aislado(reg, ident)
        if enc is not None and (enc.agentes or enc.modelos_solicitados):
            return True
    return False


def _supervisor(reg: Registro, args) -> int:
    if args.respaldo and not args.agente:
        raise ValueError("--respaldo necesita --agente como ruta principal")
    ruta = tuple(dict.fromkeys([args.agente, *args.respaldo])) if args.agente else ()
    if not _hay_ruta(reg, ruta):
        raise ValueError(
            "supervisor sin ruta de agentes: configura --agente o una ruta "
            "persistida en los encargos; sin ruta los pedidos quedarian "
            "en recibido indefinidamente"
        )

    lanzar = lanzar_de_ruta(reg, ruta)
    # En el bucle los avisos corren en un hilo aparte con tope por pasada: la cadencia del barrido no
    # depende del canal, ni cuando funciona. Con --una-vez no hay cadencia que cuidar ni pasada siguiente
    # que retome lo que un tope dejara sin enviar: drena en línea y por completo.
    from motor.integraciones.directores import DrenadoAparte

    if not args.una_vez:
        from motor.encargos import resumen as resumen_encargos

        resumen_encargos.asegurar_config(reg.base)
    drenador = None if args.una_vez else DrenadoAparte(reg.base)

    def paso() -> list[dict]:
        hora = ahora_iso()
        marcar_ruta(reg, ruta, hora)
        habilitado = _hay_ruta(reg, ruta)
        return _paso_del_supervisor(
            reg,
            hora,
            lanzar,
            habilitado,
            args.umbral,
            _puerto_github(reg.base),
            drenador,
        )

    if args.una_vez:
        print(json.dumps(paso(), ensure_ascii=False))
        return 0
    from motor.senal import Senal, url_configurada

    from motor.integraciones.canales import canales_caidos

    base = reg.base
    # La señal del supervisor lee MOTOR_KUMA_URL del secretos.env de su propia base (en la Mini,
    # ~/.motor-encargos/secretos.env). El daemon del modo planes lee el de ~/.motor: cada proceso, su archivo.
    try:
        configurada = bool(url_configurada(base))
    except Exception:  # noqa: BLE001  un secretos.env ilegible tampoco da señal
        configurada = False
    if not configurada:
        print(
            "motor: sin MOTOR_KUMA_URL en el secretos.env de la base, el supervisor de encargos no manda señal a Kuma "
            "(un canal caído solo se ve en `motor director ver` y en este log)",
            file=sys.stderr,
            flush=True,
        )
    senal = Senal(base, lambda: {}, canales=lambda: canales_caidos(base))
    senal.arranca()
    try:
        fallidas = 0
        while True:
            try:
                informes = paso()
            except Exception:
                # Un paso que no se pudo dar no tumba al supervisor ni cuenta como
                # avance para la señal; el error de un encargo ya queda en su encargo.
                import traceback

                traceback.print_exc()
                fallidas = min(fallidas + 1, 10)
                time.sleep(min(args.intervalo * 2**fallidas, 300))
                continue
            fallidas = 0
            senal.avanzo(
                sum(i["estado"] not in ("terminado", "cancelado") for i in informes)
            )
            time.sleep(args.intervalo)
    finally:
        senal.para()


def _aplicar_stdin(base_fija: Path | None) -> int:
    import os

    crudo = sys.stdin.read()
    try:
        msg = json.loads(crudo)
    except json.JSONDecodeError:
        print(json.dumps({"error": "stdin no es json"}))
        return 2
    env = os.environ.get("MOTOR_ENCARGO_BASE")
    if env:
        base = exigir_base(Path(env))
        if msg.get("base") and Path(msg["base"]).expanduser().resolve() != base:
            print(json.dumps({"error": "la base no es la de este comando"}))
            return 2
    elif base_fija:
        base = exigir_base(base_fija)
    else:
        if not msg.get("base"):
            print(json.dumps({"error": "falta la base"}))
            return 2
        base = exigir_base(Path(msg["base"]))
    op = msg.get("op")
    argv = [op or ""]
    if op == "encargar":
        reg = Registro(base)
        try:
            return _json(
                recibir(
                    reg,
                    msg.get("texto") or "",
                    msg["clave"],
                    ahora_iso(),
                    director=msg.get("director") or "remoto",
                    adjuntos=tuple(msg.get("adjuntos") or ()),
                    restricciones=tuple(msg.get("restricciones") or ()),
                    permisos=tuple(msg.get("permisos") or ()),
                    prohibiciones=tuple(msg.get("prohibiciones") or ()),
                    canal=msg.get("canal") or "",
                    via=msg.get("via") or "durable",
                    entrega_nivel=resolver_entrega(
                        msg.get("entrega_nivel"), msg.get("repo") or "",
                        msg.get("via") or "durable",
                    ),
                    repo=msg.get("repo") or "",
                    rama=msg.get("rama") or "",
                    base_rama=msg.get("base_rama") or msg.get("base") or "",
                    modelos_solicitados=solicitar_modelos(
                        msg.get("responsable", ""), msg.get("revisor", "")
                    ),
                    gh=_puerto_github(base),
                )
            )
        finally:
            reg.close()
    elif op in ("promover", "resultado_directo"):
        from motor.integraciones.directores import (
            aplicar_promover_canonico,
            aplicar_resultado_canonico,
        )

        reg = Registro(base)
        try:
            if op == "promover":
                return _json(
                    aplicar_promover_canonico(
                        reg,
                        msg["encargo"],
                        msg.get("clave") or "",
                        msg.get("hora") or ahora_iso(),
                    )
                )
            return _json(
                aplicar_resultado_canonico(
                    reg,
                    msg["encargo"],
                    msg.get("texto") or "",
                    msg.get("hora") or ahora_iso(),
                    msg.get("evidencia") or "",
                )
            )
        finally:
            reg.close()
    elif op == "ver":
        argv = ["ver", msg["encargo"], "--base", str(base)]
    elif op in ("parar", "continuar", "cancelar"):
        argv = [op, msg["encargo"], "--base", str(base), "--clave", msg["clave"]]
    elif op == "reabrir":
        if any(
            not isinstance(msg.get(campo), str) or not msg[campo].strip()
            for campo in ("fase", "cierre")
        ):
            print(json.dumps({"error": "reabrir necesita fase y cierre no vacíos"}))
            return 2
        argv = [
            op, msg["encargo"], "--base", str(base), "--clave", msg["clave"],
            "--fase", msg["fase"], "--cierre", msg["cierre"],
        ]
    elif op == "resultado":
        from .ciclo import recibir_resultado

        reg = Registro(base)
        try:
            return _json(
                recibir_resultado(
                    reg,
                    msg["encargo"],
                    msg["token"],
                    msg["host"],
                    msg["pedido"],
                    msg["observaciones"],
                    msg["juicio"],
                    ahora_iso(),
                )
            )
        finally:
            reg.close()
    elif op in ("conceder", "cerrar-conductor"):
        from .ciclo import cerrar_conductor, conceder_conductor

        reg = Registro(base)
        try:
            funcion = conceder_conductor if op == "conceder" else cerrar_conductor
            return _json(
                funcion(reg, msg["encargo"], msg["token"], msg["host"], ahora_iso())
            )
        finally:
            reg.close()
    elif op == "intento":
        from .ciclo import observar_intento
        from .dominio import TransicionIlegal

        reg = Registro(base)
        try:
            salida = observar_intento(
                reg,
                msg["encargo"],
                int(msg["pid"]),
                str(msg.get("inicio") or ""),
                ahora_iso(),
                boot_id=str(msg.get("boot_id") or ""),
                job=str(msg.get("job") or ""),
                sesion=str(msg.get("sesion") or ""),
                token=str(msg.get("token") or ""),
                host=str(msg.get("host") or ""),
            )
        except (TransicionIlegal, KeyError, TypeError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 2
        finally:
            reg.close()
        print(json.dumps(salida, ensure_ascii=False))
        return 0
    elif op == "efecto":
        argv = [
            "efecto",
            "--base",
            str(base),
            "--encargo",
            msg["encargo"],
            "--id",
            msg["id"],
            "--operacion",
            msg["operacion"],
            "--clave-destino",
            msg["clave_destino"],
            "--cuerpo",
            msg.get("cuerpo") or "",
        ]
    else:
        print(json.dumps({"error": f"op desconocida: {op}"}))
        return 2
    return main(argv)
