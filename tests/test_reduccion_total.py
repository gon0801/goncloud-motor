from __future__ import annotations

import unittest
from dataclasses import asdict, replace
from unittest.mock import Mock, patch

from motor.encargos.dominio import Fase, PausaAgente, TransicionIlegal, reducir
from motor.encargos.reductor import Fila, TABLA


def _pedido():
    return reducir(None, {"kind": "entrega", "data": {
        "id": "e", "clave": "e", "huella": "h", "texto": "pedido",
    }}, nuevo=False)


class ReduccionTotal(unittest.TestCase):
    def test_fallo_interno_historico_no_cambia_un_encargo_cerrado(self) -> None:
        cerrado = replace(
            _pedido(), estado="terminado", fallos_repo=("git falló", 2),
            fallos_entrega=("git falló", 2), ultima_firma="original",
        )
        dato = {"kind": "fallo_interno", "data": {"firma": "ajena"}}
        self.assertEqual(asdict(reducir(cerrado, dato, nuevo=False)), asdict(cerrado))
        with self.assertRaisesRegex(
            TransicionIlegal, "un encargo cerrado o que ya se detiene no cambia de causa",
        ):
            reducir(cerrado, dato, nuevo=True)

    def test_un_error_de_aplicacion_inesperado_no_se_oculta_al_comparador(self) -> None:  # no-roja-en-base: la rama anterior sí hace visible al comparador un fallo sembrado
        for tipo in (RuntimeError, KeyError, ValueError):
            falla = Mock(side_effect=tipo("fallo sembrado"))
            with self.subTest(tipo=tipo), patch.dict(TABLA, {"recibo": Fila(falla)}):
                with self.assertRaisesRegex(tipo, "fallo sembrado"):
                    reducir(_pedido(), {"kind": "recibo", "data": {}}, nuevo=False)

    def test_dependencia_propia_historica_sigue_aislando_el_registro(self) -> None:  # no-roja-en-base: un ciclo propio ya truncaba la historia en la rama anterior
        hecho = {"kind": "fases", "data": {
            "fases": [{"id": "F", "resultado": "r",
                       "requiere": [{"fase": "F", "version": ""}]}],
        }}
        with self.assertRaisesRegex(TransicionIlegal, "se requiere a sí misma"):
            reducir(_pedido(), hecho, nuevo=False)

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

    def test_proveedor_disponible_sin_pausas_no_necesita_agente(self) -> None:  # no-roja-en-base: conserva la escritura de una carga que no se usa
        previo = _pedido()
        for nuevo in (False, True):
            with self.subTest(nuevo=nuevo):
                resultado = reducir(previo, {"kind": "proveedor_disponible",
                                               "data": {"modelo": "nuevo"}}, nuevo=nuevo)
                self.assertEqual(resultado.pausas, ())

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
            ("entrega_externa", {"fase": "ajena", "paso": "commit"}),
            ("fases", {"fases": [{"id": "F", "resultado": "r",
                                  "requiere_despliegue": "sí"}]}),
            ("fases", {"fases": [{"id": "F", "resultado": "r",
                                  "requiere": [{"fase": "ajena"}]}]}),
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
                fallos = ("", 0) if kind == "solicitud" else ("git falló", 2)
                self.assertEqual(resultado.fallos_repo, fallos)
                self.assertEqual(resultado.fallos_entrega, fallos)
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

    def test_fases_y_entrega_externa_ilegibles_permiten_el_evento_siguiente(self) -> None:
        previo = replace(_pedido(), fases=(Fase("F", "resultado"),), fase_activa="F")
        casos = (
            ("entrega_externa", {"fase": "otra", "paso": "commit"}),
            ("entrega_externa", {"fase": "F", "intento": "ajeno", "paso": "commit"}),
            ("fases", {"fases": [{"id": "F", "resultado": "r",
                                  "requiere_despliegue": "quizá"}]}),
            ("fases", {"fases": [{"id": "F", "resultado": "r", "lane": []}]}),
            ("fases", {"fases": [{"id": "F", "resultado": "r", "requiere": "mal"}]}),
            ("fases", {"fases": [{"id": "F", "resultado": "r",
                                  "requiere": [{"fase": "ajena", "version": "v"}]}]}),
        )
        for kind, data in casos:
            with self.subTest(kind=kind, data=data):
                anterior = replace(previo, intento="vigente") if data.get("intento") else previo
                resultado = reducir(anterior, {"kind": kind, "data": data}, nuevo=False)
                self.assertEqual(asdict(resultado), asdict(anterior))
                siguiente = reducir(resultado, {"kind": "procedimiento",
                                                  "data": {"nota": "continúa"}}, nuevo=False)
                self.assertEqual(siguiente.procedimiento[-1], "continúa")

    def test_entrega_inicial_sin_estado_previo_sigue_rechazando_tipos_malos(self) -> None:  # no-roja-en-base: fija el límite sin estado previo que no se modifica
        with self.assertRaises((TypeError, ValueError, TransicionIlegal)):
            reducir(None, {"kind": "entrega", "data": {
                "id": "e", "clave": "k", "huella": "h",
                "texto": "pedido", "entrega_nivel": 17,
            }}, nuevo=False)

    def test_lecturas_tardias_ilegibles_no_truncan_la_historia(self) -> None:
        previo = replace(_pedido(), fases=(Fase("F", "resultado"),), fase_activa="F")
        casos = (
            ("revision", {"fase": "F", "veredicto": "aprobado", "aprobadas": 1}),
            ("conciliacion_fase", {"fase": "F", "paso": "espera",
                                   "intentos": float("inf")}),
            ("conciliacion_fase", {"fase": "F", "paso": "espera",
                                   "intentos": "sin número"}),
            ("conciliacion_fase", {"fase": "F", "paso": "espera_agotada",
                                   "ordenes": float("inf"), "causa": "sin respuesta"}),
            ("conciliacion_fase", {"fase": "F", "paso": "espera_agotada",
                                   "ordenes": [1], "causa": "sin respuesta"}),
        )
        for kind, data in casos:
            with self.subTest(kind=kind, data=data):
                resultado = reducir(previo, {"kind": kind, "data": data}, nuevo=False)
                self.assertEqual(asdict(resultado), asdict(previo))
                siguiente = reducir(resultado, {"kind": "procedimiento",
                                                  "data": {"nota": "después"}}, nuevo=False)
                self.assertEqual(siguiente.procedimiento[-1], "después")

    def test_noops_no_leen_campos_que_no_consumen(self) -> None:  # no-roja-en-base: preserva los derivados de no-ops que la rama anterior ya conservaba
        previo = replace(_pedido(), fases=(Fase("F", "resultado"),), fase_activa="F",
                         fallos_repo=("git", 2), ultima_firma="anterior")
        casos = (
            ("ci_pr", {"fase": "otra", "estado": "verde", "fallas": 1}, ("", 0)),
            ("conciliacion_fase", {"fase": "otra", "paso": "en_curso",
                                   "intentos": float("inf")}, ("git", 2)),
            ("conciliacion_fase", {"fase": "F", "paso": "integrado",
                                   "numero": float("inf")}, ("git", 2)),
        )
        for kind, data, serie in casos:
            with self.subTest(kind=kind):
                resultado = reducir(previo, {"kind": kind,
                                               "data": {**data, "firma": "nueva"}}, nuevo=False)
                self.assertEqual(resultado.ultima_firma, "nueva")
                self.assertEqual(resultado.fallos_repo, serie)

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
