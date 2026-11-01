"""Comandos del encargo. El texto del pedido no entra al argv remoto."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

from .agentes import _disponible, _lanzador, _modelos_para, _sesion_modo
from .catalogo_agentes import ADAPTADORES, identidad_modelo
from .ciclo import (
    afirmar,
    ahora_iso,
    barrer,
    consultar,
    corregir,
    marcar_espera,
    marcar_pregunta,
    ordenar,
    parada_local,
    recibir,
    resolver_entrega,
)
from .frontera import DestinoEnsayo, despachar
from .registro import BaseProhibida, Registro, exigir_base
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

    for nombre in ("parar", "continuar", "cancelar"):
        s = sub.add_parser(nombre)
        s.add_argument("encargo")
        s.add_argument("--base", type=Path, required=True)
        s.add_argument("--clave", required=True)

    s = sub.add_parser("corregir")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--clave", required=True)
    s.add_argument("--texto", required=True)

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
    s.add_argument("--bloquea", action="store_true")

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
    if args.cmd == "encargar":
        texto = args.texto
        adjuntos: tuple[str, ...] = ()
        if args.archivo:
            texto = args.archivo.read_text(encoding="utf-8")
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
                canal=args.canal,
                via=args.via,
                entrega_nivel=resolver_entrega(args.entrega, args.repo, args.via),
                repo=args.repo,
                rama=args.rama,
                base_rama=args.base_rama,
                modelos_solicitados=solicitar_modelos(args.responsable, args.revisor),
            )
        )
    if args.cmd == "ver":
        return _json(consultar(reg, args.encargo))
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
        return _json(corregir(reg, args.encargo, args.texto, args.clave, hora))
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
        return _json(marcar_pregunta(reg, args.encargo, args.texto, args.bloquea, hora))
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
        doc = json.loads(path.read_text(encoding="utf-8"))
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


def _supervisor(reg: Registro, args) -> int:
    from .frontera import turno
    from .rutas import candidatas_de, elegir, proximo, solicitado_de

    if args.respaldo and not args.agente:
        raise ValueError("--respaldo necesita --agente como ruta principal")
    ruta = tuple(dict.fromkeys([args.agente, *args.respaldo])) if args.agente else ()
    if not ruta and not any(
        reg.fold(i).agentes or reg.fold(i).modelos_solicitados for i in reg.ids()
    ):
        raise ValueError(
            "supervisor sin ruta de agentes: configura --agente o una ruta "
            "persistida en los encargos; sin ruta los pedidos quedarian "
            "en recibido indefinidamente"
        )

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
            generacion = Path(json.loads(referencia.read_text(encoding="utf-8"))["ruta"])
        disponibles = {
            agente: (solicitado[1],) if solicitado else _modelos_para(agente, rol, generacion)
            for agente in candidatas
            if _disponible(agente, generacion)
        }
        excluir = (
            frozenset(m for r, _a, m in enc.modelos if r == "responsable")
            if rol == "revisor"
            else frozenset()
        )
        elegido = elegir(
            replace(enc, agentes=candidatas), hora, disponibles, rol=rol, excluir_modelos=excluir
        )
        if elegido:
            agente, modelo = elegido
            modo = _sesion_modo(enc, agente, rol)
            argv = _lanzador(agente, modelo, modo)(enc, prompt, espacio, sesion)
            return argv, {"agente": agente, "modelo": modelo, "sesion_modo": modo}
        if rol == "revisor" and disponibles and all(
            identidad_modelo(m) in {identidad_modelo(x) for x in excluir}
            for modelos in disponibles.values() for m in modelos
        ):
            motivo = "sin modelo de revisión distinto disponible"
        else:
            motivos = [
                p.causa for p in enc.pausas if p.agente in disponibles and p.hasta > hora
            ]
            motivo = (
                "; ".join(motivos)
                or (
                    f"el modelo solicitado {solicitado[1]} no tiene runtime, suscripcion "
                    "o aislamiento disponibles en este host"
                    if solicitado else
                    "las rutas autorizadas no tienen runtime, suscripcion o aislamiento disponibles en este host"
                )
            )
        marcar_espera(reg, enc.id, motivo,
                     proximo(replace(enc, agentes=candidatas), hora, set(disponibles)), hora)
        return None

    def paso() -> list[dict]:
        hora = ahora_iso()
        if ruta:
            with turno(reg.base):
                for ident in reg.ids():
                    enc = reg.fold(ident)
                    if enc.agentes != ruta and enc.estado not in (
                        "terminado",
                        "cancelado",
                    ):
                        reg.anotar(
                            ident,
                            {
                                "kind": "ruta_agentes",
                                "data": {"agentes": list(ruta), "hora": hora},
                            },
                            f"ruta:{ident}:{len(reg.eventos(ident))}",
                        )
        habilitado = ruta or any(
            reg.fold(ident).agentes or reg.fold(ident).modelos_solicitados for ident in reg.ids()
        )
        salida = barrer(
            reg,
            hora,
            time.time(),
            lanzar if habilitado else None,
            umbral_s=args.umbral,
            gh=_puerto_github(reg.base),
        )
        try:
            from motor.integraciones.directores import drenar

            drenar(reg)
        except Exception:
            pass
        try:
            from .ciclo import _directorio, _espacio
            from .necesidades import continuar_respuestas

            for ident in reg.ids():
                enc = reg.fold(ident)
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
            pass
        try:
            from motor.integraciones.directores import conciliar_autoridad

            conciliar_autoridad(reg)
        except Exception:
            pass
        return salida

    if args.una_vez:
        print(json.dumps(paso(), ensure_ascii=False))
        return 0
    from motor.senal import Senal

    senal = Senal(reg.base, lambda: {})
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
