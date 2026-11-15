from __future__ import annotations

import ast
import unittest
from pathlib import Path

from motor.encargos.reductor import TABLA

RAIZ = Path(__file__).resolve().parents[1]


class TablaCompleta(unittest.TestCase):
    def test_tabla_contiene_los_82_kinds_sin_ramas_pendientes(self) -> None:
        esperados = set("""
            actividad afirmacion ajuste arbol autoridad autoridad_cerrada autorizacion
            bloqueo_fase ci_pr ci_pr_sondeo ci_producto ci_revision_rojo ci_sondeo
            cierre_forzado colgada comprobacion comprobaciones_supervisor concesion
            conciliacion_fase conductor conductor_fase contexto_entrega decision
            deepseek_cerrado deepseek_lectura disputa efecto entrada_incorporada
            entrega entrega_bloqueada entrega_devuelta entrega_externa espera estrategia excepcion
            falla_sesion_local fallo_interno fase_desplegada fase_reabierta
            fase_reactivada fases hallazgo_fuera_de_alcance herramienta impedimento
            impedimento_resuelto incorporado inspeccion_entregada inspeccion_solicitud
            instrumento_fallo instrumento_no_disponible intento_abierto intento_cerrado
            lectura_fallida migracion nivel_efectivo nota_pr observacion_supervisor
            orden pendiente_entrega plazo politica_despliegue pregunta procedimiento
            producto_listo proveedor_disponible proveedor_fallo quietud quietud_vigilada
            recibo reserva reserva_cerrada resolucion respuesta respuesta_revisor_pedida
            resultado_directo revision ruta_agentes sesion_perdida sin_progreso
            solicitud solicitud_tardia via
        """.split())
        self.assertEqual(len(esperados), 82)
        self.assertEqual(set(TABLA) | {"entrega"}, esperados)
        arbol = ast.parse((RAIZ / "motor" / "encargos" / "dominio.py").read_text(encoding="utf-8"))
        funcion = next(n for n in arbol.body
                       if isinstance(n, ast.FunctionDef) and n.name == "_reducir_evento")
        comparaciones = {
            ast.unparse(n) for n in ast.walk(funcion) if isinstance(n, ast.Compare)
            and isinstance(n.left, ast.Name) and n.left.id == "kind"
        }
        self.assertEqual(comparaciones, {"kind == 'entrega'"})
