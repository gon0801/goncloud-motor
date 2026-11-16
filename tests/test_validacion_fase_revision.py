"""Una escritura se valida, pero un hecho de fase ya guardado se pliega."""

from __future__ import annotations

import unittest

from motor.encargos.dominio import TransicionIlegal, reducir
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


if __name__ == "__main__":
    unittest.main()
