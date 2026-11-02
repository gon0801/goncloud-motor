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


class HoraIlegibleNoEsAusenciaTest(unittest.TestCase):
    """F1: una hora de creación ilegible deja el árbol en duda, no sin descendientes."""

    def test_presencia_con_candidato_ilegible_es_incierto(self):
        with (
            patch.dict(windows._jobs, {}, clear=True),
            patch.object(windows, "_consulta_pid", return_value="muerto"),
            patch.object(windows, "_abrir_job", return_value=(None, "ausente")),
            patch.object(windows, "_padres_procesos", return_value={20: 10}),
            patch.object(windows, "hora_inicio", return_value=""),
        ):
            self.assertEqual(windows.presencia_grupo(10, "100"), "incierto")

    def test_descendientes_con_raiz_ilegible_es_incierto(self):
        with patch.object(windows, "_padres_procesos", return_value={20: 10}):
            self.assertIsNone(windows._descendientes(10, "no-es-hora"))


class MatarSinTocarPidReutilizadoTest(unittest.TestCase):
    """F2: reenumerar y volver a datar antes de matar, sin /T."""

    def test_reenumera_y_alcanza_al_hijo_nacido_durante_el_cierre(self):
        pasadas = 0

        def padres_por_pasada():
            nonlocal pasadas
            pasadas += 1
            return {10: 0, 20: 10, 50: 20} if pasadas > 1 else {10: 0, 20: 10}

        horas = {10: "100", 20: "110", 50: "130"}
        with (
            patch.object(windows, "_padres_procesos", side_effect=padres_por_pasada),
            patch.object(windows, "hora_inicio", side_effect=lambda pid: horas.get(pid, "")),
            patch.object(windows, "_matar_pid_confirmado") as matar,
            patch.object(windows.time, "sleep"),
        ):
            windows._matar_arbol_confirmado(10, "100")
        self.assertIn(50, [llamada.args[0] for llamada in matar.call_args_list])
        self.assertIn(10, [llamada.args[0] for llamada in matar.call_args_list])

    def test_no_mata_un_pid_cuya_hora_cambio_antes_del_kill(self):
        with (
            patch.object(windows, "hora_inicio", return_value="999"),
            patch.object(windows.subprocess, "run") as correr,
        ):
            self.assertFalse(windows._matar_pid_confirmado(40, 120))
        self.assertEqual(correr.call_args_list, [])

    def test_mata_el_pid_confirmado_sin_t(self):
        with (
            patch.object(windows, "hora_inicio", return_value="120"),
            patch.object(windows.subprocess, "run") as correr,
        ):
            self.assertTrue(windows._matar_pid_confirmado(40, 120))
        ordenes = [llamada.args[0] for llamada in correr.call_args_list]
        self.assertEqual(ordenes, [["taskkill", "/PID", "40", "/F"]])

