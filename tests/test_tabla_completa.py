from __future__ import annotations

import ast
import unittest
from pathlib import Path

from motor.encargos.reductor import TABLA

RAIZ = Path(__file__).resolve().parents[1]


class TablaCompleta(unittest.TestCase):
    def test_tabla_contiene_los_82_kinds_sin_ramas_pendientes(self) -> None:
        grupos = {
            "pedido": "ajuste contexto_entrega falla_sesion_local migracion orden procedimiento "
                      "proveedor_disponible proveedor_fallo recibo ruta_agentes",
            "intento": "actividad colgada conductor conductor_fase intento_abierto intento_cerrado "
                       "reserva reserva_cerrada sesion_perdida",
            "fase": "bloqueo_fase ci_pr cierre_forzado conciliacion_fase entrada_incorporada "
                    "entrega_bloqueada entrega_externa fase_desplegada fase_reabierta "
                    "fase_reactivada fases lectura_fallida nivel_efectivo politica_despliegue "
                    "producto_listo resultado_directo",
            "revision": "ci_sondeo comprobaciones_supervisor deepseek_cerrado deepseek_lectura "
                        "entrega_devuelta hallazgo_fuera_de_alcance inspeccion_entregada "
                        "inspeccion_solicitud nota_pr observacion_supervisor resolucion respuesta revision",
            "necesidades": "autoridad autorizacion decision estrategia herramienta impedimento "
                           "impedimento_resuelto instrumento_fallo instrumento_no_disponible "
                           "pregunta quietud solicitud solicitud_tardia via",
            "esperas": "afirmacion comprobacion efecto espera excepcion incorporado pendiente_entrega "
                       "plazo quietud_vigilada sin_progreso",
            "detencion": "arbol fallo_interno",
            "retirados": "autoridad_cerrada ci_pr_sondeo ci_producto ci_revision_rojo concesion "
                         "disputa respuesta_revisor_pedida",
        }
        esperados = {
            kind: f"motor.encargos.reductor.{tema}.{'registrar' if tema == 'retirados' else kind}"
            for tema, kinds in grupos.items() for kind in kinds.split()
        }
        self.assertEqual(len(esperados), 81)
        self.assertEqual(set(TABLA) | {"entrega"}, set(esperados) | {"entrega"})
        self.assertEqual(
            {kind: f"{fila.aplicar.__module__}.{fila.aplicar.__name__}"
             for kind, fila in TABLA.items()}, esperados,
        )
        arbol = ast.parse((RAIZ / "motor" / "encargos" / "dominio.py").read_text(encoding="utf-8"))
        funcion = next(n for n in arbol.body
                       if isinstance(n, ast.FunctionDef) and n.name == "_reducir_evento")
        comparaciones = {
            ast.unparse(n) for n in ast.walk(funcion) if isinstance(n, ast.Compare)
            and isinstance(n.left, ast.Name) and n.left.id == "kind"
        }
        self.assertEqual(comparaciones, {"kind == 'entrega'"})

    def test_todas_las_detenciones_tienen_fila(self) -> None:
        from motor.encargos.detencion import DETENCIONES

        self.assertTrue(set(DETENCIONES) <= set(TABLA))
