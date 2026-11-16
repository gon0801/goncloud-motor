from __future__ import annotations

import unittest
from dataclasses import asdict, replace

from motor.encargos.dominio import Fase, PausaAgente, TransicionIlegal, reducir
from motor.encargos.reductor import TABLA


def _pedido():
    return reducir(None, {"kind": "entrega", "data": {
        "id": "e", "clave": "e", "huella": "h", "texto": "pedido",
    }}, nuevo=False)


class ReduccionTotal(unittest.TestCase):
    def test_fallo_interno_historico_no_cambia_un_encargo_cerrado(self) -> None:
        cerrado = replace(_pedido(), estado="terminado")
        dato = {"kind": "fallo_interno", "data": {}}
        self.assertEqual(asdict(reducir(cerrado, dato, nuevo=False)), asdict(cerrado))
        with self.assertRaisesRegex(
            TransicionIlegal, "un encargo cerrado o que ya se detiene no cambia de causa",
        ):
            reducir(cerrado, dato, nuevo=True)

    def test_rechazos_de_pedido_siguen_iguales_al_escribir(self) -> None:  # no-roja-en-base: fija los guardias que se moverán a validar
        previo = _pedido()
        casos = (
            ("contexto_entrega", {}, TransicionIlegal, "el contexto no puede cambiar una base autorizada"),
            ("orden", {"accion": "inventada"}, TransicionIlegal, "orden desconocida: inventada"),
            ("proveedor_fallo", {}, KeyError, "'agente'"),
            ("ruta_agentes", {}, KeyError, "'agentes'"),
            ("procedimiento", {}, KeyError, "'nota'"),
        )
        for kind, datos, tipo, mensaje in casos:
            with self.subTest(kind=kind):
                with self.assertRaises(tipo) as exc:
                    reducir(previo, {"kind": kind, "data": datos}, nuevo=True)
                self.assertEqual(str(exc.exception), mensaje)

    def test_aplicar_nunca_lanza_ni_con_infinity(self) -> None:
        previo = replace(_pedido(), fases=(Fase("F", "resultado"),), fase_activa="F")
        casos = [(kind, {}) for kind in sorted(set(TABLA) | {"entrega"})]
        casos += [
            ("incorporado", {"generacion": float("inf")}),
            ("quietud_vigilada", {"vueltas": float("inf")}),
            ("solicitud", {"id": "s", "fase": "", "intento": "i",
                           "operacion": "leer", "generacion": float("inf")}),
            ("reserva", {"generacion": float("inf")}),
            ("ci_sondeo", {"sondeos": float("inf")}),
            ("entrega_externa", {"numero": float("inf")}),
            ("ajuste", {"texto": "x", "cierra": float("nan")}),
            ("ci_pr", {"estado": "verde", "fallas": float("inf")}),
            ("comprobaciones_supervisor", {"hallazgos": float("inf")}),
            ("revision", {"fase": "otra"}),
            ("intento_abierto", {"id": "i", "pid": float("inf"), "inicio": "t"}),
        ]
        for kind in TABLA:
            casos.extend((kind, {"fase": "F", "generacion": valor,
                                  "sondeos": valor, "hallazgos": valor})
                         for valor in (float("inf"), float("nan"), [], "no numerico"))
        for kind, datos in casos:
            with self.subTest(kind=kind, datos=datos):
                reducir(previo, {"kind": kind, "data": datos}, nuevo=False)

    def test_noops_conocidos_siguen_actualizando_derivados(self) -> None:  # no-roja-en-base: la rama anterior ya aplica los derivados a los no-ops
        previo = replace(
            _pedido(), fases=(Fase("F", "resultado"),), fase_activa="F",
            fallos_repo=("git falló", 2), fallos_entrega=("git falló", 2),
            ultima_firma="anterior",
        )
        for kind, datos in (
            ("solicitud", {}),
            ("incorporado", {"generacion": "ilegible", "firma": "nueva"}),
            ("quietud_vigilada", {"vueltas": "ilegible", "firma": "nueva"}),
            ("conciliacion_fase", {"fase": "F", "paso": "preparada", "firma": "nueva"}),
        ):
            with self.subTest(kind=kind):
                resultado = reducir(previo, {"kind": kind, "data": datos}, nuevo=False)
                self.assertEqual(resultado.fallos_repo, ("", 0))
                self.assertEqual(resultado.fallos_entrega, ("", 0))
                self.assertEqual(resultado.ultima_firma,
                                 datos.get("firma", "anterior"))

    def test_ajuste_con_cierre_no_iterable_no_oculta_su_error(self) -> None:  # no-roja-en-base: fija la prioridad del error crudo de escritura en la rama anterior
        with self.assertRaises(TypeError) as exc:
            reducir(_pedido(), {"kind": "ajuste", "data": {"cierra": 5}}, nuevo=True)
        self.assertIn("not iterable", str(exc.exception))

    def test_conciliacion_historica_ilegible_no_rompe_el_pliegue(self) -> None:
        previo = replace(_pedido(), fases=(Fase("F", "resultado"),), fase_activa="F",
                         fallos_repo=("git falló", 2), ultima_firma="original")
        casos = (
            {"paso": "en_curso", "intentos": "no es número"},
            {"paso": "en_curso", "intentos": float("inf")},
            {"paso": "reintento", "ordenes": "no es número"},
            {"paso": "reintento", "ordenes": float("inf")},
            {"paso": "integrado", "recibo": "sha", "numero": "no es número"},
            {"paso": "integrado", "recibo": "sha", "numero": float("inf")},
            {"paso": "integrado", "recibo": "sha",
             "correspondencia": {"archivo": "a", "fases": [["otra", "b"]]}},
            {"paso": "integrado", "recibo": "sha", "requiere_despliegue": "quizá"},
        )
        for datos in casos:
            with self.subTest(paso=datos):
                hecho = {"kind": "conciliacion_fase",
                         "data": {"fase": "F", "firma": "ajena", **datos}}
                self.assertEqual(asdict(reducir(previo, hecho, nuevo=False)), asdict(previo))

    def test_kind_desconocido_no_modifica_nada_al_plegar(self) -> None:
        previo = _pedido()
        resultado = reducir(previo, {"kind": "orden_inventada", "data": {}}, nuevo=False)
        self.assertEqual(asdict(resultado), asdict(previo))

    def test_hechos_omitidos_no_cortan_series_ni_cambian_firma(self) -> None:
        previo = replace(
            _pedido(), fallos_repo=("git falló", 2),
            fallos_entrega=("git falló", 2), ultima_firma="original",
            pausas=(PausaAgente("a", "t", "x"),),
        )
        casos = (
            ("orden_inventada", {"firma": "ajena"}),
            ("orden", {"accion": "inventada", "firma": "ajena"}),
            ("proveedor_fallo", {"firma": "ajena"}),
            ("proveedor_disponible", {"firma": "ajena"}),
        )
        for kind, datos in casos:
            with self.subTest(kind=kind):
                resultado = reducir(previo, {"kind": kind, "data": datos}, nuevo=False)
                self.assertEqual(asdict(resultado), asdict(previo))
        recibo = reducir(previo, {"kind": "recibo", "data": {}}, nuevo=False)
        self.assertEqual(recibo.fallos_repo, ("", 0))
