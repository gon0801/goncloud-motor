"""Regresión: un PID reciclado no mantiene pendiente la parada de su dueño anterior."""

import unittest
from unittest.mock import patch

from motor.plataforma import windows


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
