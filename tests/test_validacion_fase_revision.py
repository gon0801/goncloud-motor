"""Una escritura se valida, pero un hecho de fase ya guardado se pliega."""

from __future__ import annotations
from tests.soporte_situacion import evolucionar

import unittest
from dataclasses import asdict

from motor.encargos.dominio import Fase, TransicionIlegal, reducir
from motor.encargos.reductor import TABLA


FASE = {
    "bloqueo_fase", "ci_pr", "cierre_forzado", "conciliacion_fase",
    "entrada_incorporada", "entrega_bloqueada", "entrega_externa",
    "fase_desplegada", "fase_reabierta", "fase_reactivada", "fases",
    "lectura_fallida", "nivel_efectivo", "politica_despliegue",
    "producto_listo", "resultado_directo",
}
REVISION = {
    "ci_sondeo", "comprobaciones_supervisor", "deepseek_cerrado",
    "deepseek_lectura", "entrega_devuelta", "hallazgo_fuera_de_alcance",
    "inspeccion_entregada", "inspeccion_solicitud", "nota_pr",
    "observacion_supervisor", "resolucion", "respuesta", "revision",
}


class ValidacionFaseRevision(unittest.TestCase):
    def setUp(self) -> None:
        self.previo = reducir(None, {"kind": "entrega", "data": {
            "id": "x", "clave": "k", "huella": "h", "texto": "pedido",
        }}, nuevo=False)

    def test_las_filas_de_fase_y_revision_aceptan_un_historico_vacio(self) -> None:
        esperados = FASE | REVISION
        presentes = {
            kind for kind, fila in TABLA.items()
            if fila.aplicar.__module__.split(".")[-1] in {"fase", "revision"}
        }
        self.assertEqual(presentes, esperados)
        for kind in sorted(esperados):
            with self.subTest(kind=kind):
                resultado = reducir(
                    self.previo, {"kind": kind, "data": {}}, nuevo=False,
                )
                self.assertIsNotNone(resultado)

    def test_conciliacion_ilegible_no_interrumpe_una_historia(self) -> None:
        previo = replace(self.previo, fases=(Fase("F", "resultado"),), fase_activa="F")
        casos = (
            {"paso": "en_curso", "intentos": float("inf")},
            {"paso": "en_curso", "intentos": float("nan")},
            {"paso": "reintento", "ordenes": float("inf")},
            {"paso": "integrado", "recibo": "sha", "numero": float("inf")},
            {"paso": "integrado", "recibo": "sha",
             "correspondencia": {"archivo": "a", "fases": [["otra", "b"]]}},
        )
        for datos in casos:
            with self.subTest(datos=datos):
                hecho = {"kind": "conciliacion_fase", "data": {"fase": "F", **datos}}
                self.assertEqual(asdict(reducir(previo, hecho, nuevo=False)), asdict(previo))
        with self.assertRaises(OverflowError):
            reducir(previo, {"kind": "conciliacion_fase", "data": {
                "fase": "F", "paso": "en_curso", "intentos": float("inf"),
            }}, nuevo=True)

    def test_rechazos_al_escribir_conservan_sus_mensajes(self) -> None:  # no-roja-en-base: fija la validación que se mueve
        casos = (
            ("nivel_efectivo", {"nivel": "raro"}, "nivel efectivo desconocido: raro"),
            ("politica_despliegue", {}, "política de despliegue sin fuente comprobable"),
            ("bloqueo_fase", {}, "bloqueo de fase sin evidencia"),
            ("cierre_forzado", {}, "cierre forzado sin evidencia"),
            ("entrada_incorporada", {}, "la entrada incorporada necesita fase, dep y versión"),
            ("fase_desplegada", {}, "la fase ? no existe"),
            ("fase_reabierta", {}, "solo se reabre una fase preparada"),
            ("fase_reactivada", {}, "la fase ? no está bloqueada"),
            ("conciliacion_fase", {}, "la conciliación nombra una fase inexistente: ?"),
            ("ci_pr", {}, "estado de CI del PR desconocido"),
            ("resultado_directo", {}, "el resultado directo solo cierra la via directa"),
            ("inspeccion_solicitud", {}, "la inspección necesita su solicitud"),
            ("inspeccion_entregada", {}, "la entrega de la inspección necesita su marca"),
            ("resolucion", {}, "la resolución no tiene solicitud"),
        )
        for kind, datos, mensaje in casos:
            with self.subTest(kind=kind):
                with self.assertRaises(TransicionIlegal) as exc:
                    reducir(self.previo, {"kind": kind, "data": datos}, nuevo=True)
                self.assertEqual(str(exc.exception), mensaje)

    def test_politica_sobre_fase_implicita_conserva_la_correspondencia(self) -> None:  # no-roja-en-base: conserva la fase implícita de la base
        resultado = reducir(self.previo, {"kind": "politica_despliegue", "data": {
            "fuente": "plan", "huella": "h", "requiere_despliegue": False,
            "correspondencia": {"archivo": "plan", "fases": [["fase_unica", "A"]]},
        }}, nuevo=True)
        self.assertEqual(resultado.despliegue_mapa, (("fase_unica", "A"),))

    def test_hechos_invalidos_no_crean_fase_antes_de_solicitud(self) -> None:
        solicitud = {"kind": "solicitud", "data": {
            "id": "s", "fase": "", "intento": "i", "operacion": "leer",
        }}
        for kind in ("ci_pr", "politica_despliegue"):
            with self.subTest(kind=kind):
                ignorado = reducir(self.previo, {"kind": kind, "data": {}}, nuevo=False)
                self.assertEqual(asdict(ignorado), asdict(self.previo))
                registrado = reducir(ignorado, solicitud, nuevo=False)
                self.assertEqual(len(registrado.solicitudes), 1)

    def test_hechos_de_cierre_y_producto_ignorados_no_derivan_metadatos(self) -> None:
        cerrado = evolucionar(self.previo, estado="terminado", hora_cierre="2026-11-01")
        casos = (
            (cerrado, "cierre_forzado", {"hora": "2026-11-05"}),
            (cerrado, "bloqueo_fase", {"hora": "2026-11-05"}),
            (self.previo, "producto_listo", {"interno": "no aceptado", "manifiesto": 1}),
        )
        for previo, kind, datos in casos:
            with self.subTest(kind=kind):
                resultado = reducir(previo, {"kind": kind, "data": datos}, nuevo=False)
                self.assertEqual(asdict(resultado), asdict(previo))


if __name__ == "__main__":
    unittest.main()
