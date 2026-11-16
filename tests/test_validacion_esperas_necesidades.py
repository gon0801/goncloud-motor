from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from motor.encargos.dominio import Fase, TransicionIlegal, reducir
from motor.encargos.reductor import TABLA

ESPERAS = set("""
    afirmacion comprobacion efecto espera excepcion incorporado pendiente_entrega
    plazo quietud_vigilada sin_progreso
""".split())
NECESIDADES = set("""
    autoridad autorizacion decision estrategia herramienta impedimento
    impedimento_resuelto instrumento_fallo instrumento_no_disponible pregunta
    quietud solicitud solicitud_tardia via
""".split())


def _pedido():
    return reducir(None, {"kind": "entrega", "data": {
        "id": "p", "clave": "p", "huella": "h", "texto": "pedido",
    }}, nuevo=False)


class ValidacionEsperasNecesidades(unittest.TestCase):
    def test_ajustes_no_impiden_cerrar_fase_intermedia_ni_esperar_despliegue(self) -> None:  # no-roja-en-base: preserva los retornos previos al cierre terminal
        e = _pedido()
        intermedia = replace(
            e, fases=(Fase("a", "a"), Fase("b", "b")),
            fase_activa="a", ajustes=("ajuste",),
        )
        pendiente = replace(
            e,
            entrega=replace(e.entrega, nivel="desplegado"),
            fases=(
                Fase("a", "a", ultimo_paso_entrega="desplegado"),
                Fase(
                    "b", "b", estado="bloqueada", evidencia="bloqueo",
                    bloqueo_causa="falla", ultimo_paso_entrega="integrado",
                    requiere_despliegue=True, despliegue_espera_en="mañana",
                ),
            ),
            fase_activa="a", ajustes=("ajuste",),
        )
        for nombre, previo, fase, siguiente in (
            ("intermedia", intermedia, "b", "trabajar la fase b"),
            ("despliegue", pendiente, "a", "esperar la conciliación del despliegue pendiente"),
        ):
            with self.subTest(caso=nombre):
                resultado = reducir(previo, {"kind": "comprobacion", "data": {
                    "clase": "confirmado", "evidencia": "ok", "ajustes_vistos": 0,
                }}, nuevo=True)
                self.assertEqual((resultado.estado, resultado.fase_activa, resultado.siguiente),
                                 ("recibido", fase, siguiente))

    def test_validadores_en_las_filas_que_antes_lanzaban(self) -> None:
        con_validacion = {
            "afirmacion", "comprobacion", "efecto", "espera", "incorporado",
            "quietud_vigilada", "autoridad", "autorizacion", "decision",
            "estrategia", "herramienta", "impedimento", "impedimento_resuelto",
            "instrumento_no_disponible", "quietud", "solicitud", "via",
        }
        for kind in con_validacion:
            with self.subTest(kind=kind):
                self.assertTrue(callable(TABLA[kind].validar))

    def test_todos_los_kinds_de_ambos_grupos_aplican_datos_vacios(self) -> None:
        esperados = ESPERAS | NECESIDADES
        self.assertEqual(
            {kind for kind, fila in TABLA.items()
             if fila.aplicar.__module__.rsplit(".", 1)[-1] in {"esperas", "necesidades"}},
            esperados,
        )
        e = _pedido()
        for kind in esperados:
            with self.subTest(kind=kind):
                self.assertIsNotNone(reducir(e, {"kind": kind, "data": {}}, nuevo=False))

    def test_los_mensajes_legados_permanecen_al_escribir(self) -> None:  # no-roja-en-base: fija los raises movidos
        e = _pedido()
        solicitud = reducir(e, {"kind": "solicitud", "data": {
            "id": "s", "fase": "", "intento": "i", "operacion": "leer",
        }}, nuevo=True)
        decision = reducir(e, {"kind": "decision", "data": {
            "id": "d", "eleccion": "sí", "motivo": "prueba",
        }}, nuevo=True)
        casos = (
            ("incorporado", e, {"generacion": -1},
             "la incorporación necesita una generación"),
            ("incorporado", e, {"generacion": "invalida"},
             "la incorporación necesita una generación"),
            ("quietud_vigilada", e, {"evento": "invalido"},
             "la quietud vigilada necesita números"),
            ("espera", e, {}, "la espera necesita motivo y reanudación"),
            ("comprobacion", e, {"clase": "confirmado"}, "confirmado sin evidencia"),
            ("comprobacion", e, {"clase": "confirmado", "evidencia": "ok",
                                "fase": "ajena"}, "el cierre pertenece a otra fase"),
            ("comprobacion", replace(e, intento="i"),
             {"clase": "confirmado", "evidencia": "ok", "intento": "j"},
             "el cierre pertenece a otro intento"),
            ("comprobacion", replace(e, ajustes=("ajuste",)),
             {"clase": "confirmado", "evidencia": "ok", "ajustes_vistos": 0},
             "llegó una corrección durante el cierre"),
            ("comprobacion", e, {"clase": "desconocida"},
             "comprobación desconocida: desconocida"),
            ("impedimento", e, {}, "el impedimento necesita clase, causa y condicion"),
            ("impedimento_resuelto", e, {},
             "el impedimento no se resuelve sin clase y evidencia"),
            ("instrumento_no_disponible", e, {},
             "instrumento_no_disponible sin clase"),
            ("quietud", e, {}, "la quietud necesita causa"),
            ("via", e, {"via": "otra"}, "via desconocida"),
            ("via", replace(e, estado="cancelado"), {"via": "directa"},
             "el encargo ya cerró"),
            ("via", replace(e, reserva=True), {"via": "directa"},
             "el ejecutor anterior sigue abierto"),
            ("solicitud", e, {}, "la solicitud necesita id"),
            ("solicitud", replace(e, estado="cancelado"), {},
             "el encargo no admite solicitudes en este estado"),
            ("solicitud", solicitud, {"id": "s"}, "la solicitud s ya existe"),
            ("solicitud", e, {"id": "s", "fase": "ajena"},
             "la solicitud no pertenece a la fase activa"),
            ("solicitud", e, {"id": "s"}, "la solicitud no pertenece al intento vigente"),
            ("solicitud", e, {"id": "s", "fase": "", "intento": "i", "estado": "terminada"},
             "una solicitud nace pendiente o a la espera del propietario"),
            ("herramienta", e, {}, "la herramienta no tiene solicitud"),
            ("herramienta", solicitud, {"id": "s"},
             "la herramienta necesita su carpeta"),
            ("autorizacion", e, {}, "la autorización necesita recurso"),
            ("autorizacion", e, {"recurso": "repo"},
             "la autorización necesita la operación explicada"),
            ("decision", e, {}, "la decisión necesita id"),
            ("decision", decision, {"id": "d"}, "la decisión d ya existe"),
            ("decision", e, {"id": "d"}, "la decisión necesita elección y motivo"),
        )
        for kind, previo, datos, mensaje in casos:
            with self.subTest(kind=kind, mensaje=mensaje), self.assertRaises(
                TransicionIlegal
            ) as exc:
                reducir(previo, {"kind": kind, "data": datos}, nuevo=True)
            self.assertEqual(str(exc.exception), mensaje)

    def test_cargas_incompletas_nuevas_cambian_a_error_de_transicion(self) -> None:
        e = _pedido()
        casos = (
            ("afirmacion", e, {}),
            ("comprobacion", e, {}),
            ("efecto", e, {}),
            ("autoridad", e, {}),
            ("estrategia", e, {}),
            ("solicitud", e, {"id": "s", "fase": "", "intento": "i"}),
            ("solicitud", e, {"id": "s", "fase": "", "intento": "i",
                              "operacion": "leer", "generacion": "invalida"}),
            ("decision", e, {"id": "d", "eleccion": "sí", "motivo": "prueba", "fases": 1}),
        )
        for kind, previo, datos in casos:
            with self.subTest(kind=kind), self.assertRaises(TransicionIlegal):
                reducir(previo, {"kind": kind, "data": datos}, nuevo=True)

    def test_decision_historica_con_fases_ilegibles_no_lanza(self) -> None:
        e = _pedido()
        self.assertIs(
            TABLA["decision"].aplicar(e, {
                "id": "d", "eleccion": "sí", "motivo": "prueba", "fases": 1,
            }, "", False), e,
        )

    def test_el_cierre_sin_fase_conserva_el_mensaje(self) -> None:  # no-roja-en-base: fija un raise inalcanzable con asegurar_fase real
        e = _pedido()
        with patch("motor.encargos.dominio.asegurar_fase", lambda anterior: anterior):
            with self.assertRaises(TransicionIlegal) as exc:
                reducir(e, {"kind": "comprobacion", "data": {
                    "clase": "confirmado", "evidencia": "ok",
                }}, nuevo=True)
        self.assertEqual(str(exc.exception), "no hay fase que cerrar")
