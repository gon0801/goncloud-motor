"""Reparte la batería en cuatro partes sin dejar pruebas fuera ni repetirlas.

La parte 0 es solo ``tests.test_motor_e2e``. Las otras tres se llenan por
módulo, el más lento primero, con el peso medido el 3-nov y un peso fijo
para el módulo que todavía no está en la tabla. Una prueba nueva cae en la
parte más ligera sin tocar el workflow: los cuatro jobs preguntan aquí la
lista, y ``comprobar`` falla si la unión no es el ``discover``.
"""

from __future__ import annotations

import argparse
import unittest
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
E2E = "tests.test_motor_e2e"
N_PARTES = 4
PESO_NUEVO = 2.0

# Segundos medidos el 3-nov en la batería verde (689 s en total).
PESOS = {
    "tests.test_motor_e2e": 243.0,
    "tests.test_comprobacion_tope": 71.0,
    "tests.test_runwrap": 50.0,
    "tests.test_sol_remoto": 46.0,
    "tests.test_avisos_barrido_libre": 25.0,
    "tests.test_topes": 21.0,
}


def peso(modulo: str) -> float:
    return PESOS.get(modulo, PESO_NUEVO)


def _hojas(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _hojas(item)
        else:
            yield item


def descubrir() -> list[tuple[str, str]]:
    suite = unittest.defaultTestLoader.discover(
        str(RAIZ / "tests"),
        top_level_dir=str(RAIZ),
    )
    hojas = [(t.__class__.__module__, t.id()) for t in _hojas(suite)]
    # Un modulo con el import roto aparece como _FailedTest de unittest.loader:
    # sin esta guardia, correr() lo deja fuera de su parte y el job sale verde.
    rotas = [ident for modulo, ident in hojas if not modulo.startswith("tests.")]
    if rotas:
        raise SystemExit(
            "un modulo de tests no importa: " + ", ".join(rotas[:3])
        )
    return hojas


def modulos_descubiertos() -> list[str]:
    return sorted({modulo for modulo, _ident in descubrir()})


def partir(modulos: list[str]) -> tuple[tuple[str, ...], ...]:
    """Cuatro listas de módulos, disjuntas. La primera es e2e si está."""
    nombres = sorted(set(modulos))
    resto = [nombre for nombre in nombres if nombre != E2E]
    cajas: list[list[str]] = [[], [], []]
    carga = [0.0, 0.0, 0.0]
    for nombre in sorted(resto, key=lambda m: (-peso(m), m)):
        destino = min(range(3), key=lambda i: (carga[i], i))
        cajas[destino].append(nombre)
        carga[destino] += peso(nombre)
    parte_e2e = (E2E,) if E2E in nombres else ()
    return (parte_e2e, *(tuple(sorted(caja)) for caja in cajas))


def ids_de(modulos: tuple[str, ...] | list[str]) -> list[str]:
    if not modulos:
        return []
    suite = unittest.defaultTestLoader.loadTestsFromNames(list(modulos))
    return [t.id() for t in _hojas(suite)]


def verificar(descubiertas: list[str], union: list[str]) -> None:
    faltan = sorted(set(descubiertas) - set(union))
    sobran = sorted(set(union) - set(descubiertas))
    repetidas = sorted(nombre for nombre, n in Counter(union).items() if n > 1)
    if not faltan and not sobran and not repetidas:
        return
    raise SystemExit(
        "la division no coincide con discover: "
        f"faltan={faltan} sobran={sobran} repetidas={repetidas}"
    )


def comprobar() -> None:
    pruebas = descubrir()
    modulos = sorted({modulo for modulo, _ident in pruebas})
    if not all(modulo.startswith("tests.") for modulo in modulos):
        raise SystemExit(f"discover no uso el paquete tests: {modulos[:3]}")
    partes = partir(modulos)
    if partes[0] != (E2E,):
        raise SystemExit(f"la parte 0 no es solo {E2E}: {partes[0]}")
    union: list[str] = []
    for indice, parte in enumerate(partes):
        ids = ids_de(parte)
        union.extend(ids)
        print(
            f"parte {indice}: {len(parte)} modulos, {len(ids)} pruebas, "
            f"peso {sum(peso(m) for m in parte):.0f}s"
        )
    descubiertas = [ident for _modulo, ident in pruebas]
    verificar(descubiertas, union)
    print(f"discover {len(descubiertas)} union {len(union)}")


def correr(parte: int) -> int:
    if parte == 0:
        elegidos = (E2E,)
    elif 0 < parte < N_PARTES:
        elegidos = partir(modulos_descubiertos())[parte]
    else:
        raise SystemExit(f"parte fuera de rango: {parte}")
    print(f"parte {parte}: {len(elegidos)} modulos")
    for nombre in elegidos:
        print(nombre)
    suite = unittest.defaultTestLoader.loadTestsFromNames(list(elegidos))
    resultado = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if resultado.wasSuccessful() else 1


def exigir(resultado: str) -> None:
    print(f"resultado de las partes: {resultado}")
    if resultado != "success":
        raise SystemExit(f"la bateria no esta verde: {resultado}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    correr_cmd = sub.add_parser("correr")
    correr_cmd.add_argument("--parte", type=int, required=True)
    sub.add_parser("comprobar")
    exigir_cmd = sub.add_parser("exigir")
    exigir_cmd.add_argument("--resultado", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "correr":
        return correr(args.parte)
    if args.cmd == "comprobar":
        comprobar()
        return 0
    exigir(args.resultado)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
