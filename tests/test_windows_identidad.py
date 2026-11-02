"""Regresión: un PID reciclado no mantiene pendiente la parada de su dueño anterior."""

import unittest
from unittest.mock import MagicMock, patch

from motor.plataforma import windows


class SinJobMataSoloElArbolConfirmadoTest(unittest.TestCase):
    def test_no_llama_taskkill_para_el_proceso_viejo_adoptado(self):
        snapshot = {
            10: (0, 100),
            20: (10, 110),
            30: (20, 50),
            40: (20, 120),
        }
        proceso = MagicMock(pid=10)
        proceso.poll.return_value = None
        with (
            patch.dict(windows._jobs, {}, clear=True),
            patch.object(
                windows,
                "_padres_procesos",
                return_value={pid: ppid for pid, (ppid, _) in snapshot.items()},
            ),
            patch.object(
                windows,
                "hora_inicio",
                side_effect=lambda pid: (
                    str(snapshot[pid][1]) if snapshot[pid][1] is not None else ""
                ),
            ),
            patch.object(windows.subprocess, "run") as correr,
        ):
            windows.matar_grupo(proceso)
        matados = [
            llamada.args[0][2]
            for llamada in correr.call_args_list
            if llamada.args and llamada.args[0][0] == "taskkill"
        ]
        self.assertNotIn("30", matados)
        self.assertIn("20", matados)
        self.assertIn("40", matados)
        self.assertIn("10", matados)

    def test_matar_grupo_pid_tampoco_mata_ni_usa_t_sobre_el_viejo(self):
        snapshot = {
            10: (0, 100),
            20: (10, 110),
            30: (20, 50),
            40: (20, 120),
        }
        with (
            patch.object(
                windows,
                "_padres_procesos",
                return_value={pid: ppid for pid, (ppid, _) in snapshot.items()},
            ),
            patch.object(
                windows,
                "hora_inicio",
                side_effect=lambda pid: (
                    str(snapshot[pid][1]) if snapshot[pid][1] is not None else ""
                ),
            ),
            patch.object(windows.subprocess, "run") as correr,
        ):
            windows.matar_grupo_pid(10, "100")
        ordenes = [llamada.args[0] for llamada in correr.call_args_list]
        matados = [argv[2] for argv in ordenes if argv[0] == "taskkill"]
        self.assertNotIn("30", matados)
        self.assertIn("20", matados)
        self.assertIn("40", matados)
        self.assertIn("10", matados)
        self.assertFalse(any("/T" in argv for argv in ordenes))


class IdentidadGrupoTest(unittest.TestCase):
    def test_pid_reutilizado_distingue_descendientes_anteriores_y_nuevos(self):
        for hijos, horas, esperado in (
            ([], {10: "300"}, "ausente"),
            ([20], {10: "300", 20: "350"}, "ausente"),
            ([20], {10: "300", 20: "200"}, "vivo"),
            ([20], {10: "300", 20: ""}, "incierto"),
            (None, {10: "300"}, "incierto"),
        ):
            with (
                self.subTest(hijos=hijos, horas=horas),
                patch.dict(windows._jobs, {}, clear=True),
                patch.object(windows, "_consulta_pid", return_value="vivo"),
                patch.object(windows, "hora_inicio", side_effect=horas.get),
                patch.object(windows, "_abrir_job", return_value=(None, "ausente")),
                patch.object(windows, "_descendientes", return_value=hijos),
            ):
                self.assertEqual(windows.presencia_grupo(10, "100"), esperado)
