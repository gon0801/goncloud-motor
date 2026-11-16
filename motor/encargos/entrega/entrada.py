"""Prepara el pedido que recibe el comando `encargar`."""

from __future__ import annotations

from motor.io import leer_texto

from ..ciclo import recibir, resolver_entrega
from ..seleccion import solicitar_modelos


def desde_cli(args, registro, hora, puerto):
    texto = args.texto
    adjuntos: tuple[str, ...] = ()
    if args.archivo:
        contenido = leer_texto(args.archivo)
        texto = f"{texto}\n\n{contenido}" if texto else contenido
        adjuntos = (str(args.archivo.resolve()),)
    if not texto.strip():
        return None
    return recibir(
        registro, texto, args.clave, hora,
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
        gh=puerto(args.base),
    )
