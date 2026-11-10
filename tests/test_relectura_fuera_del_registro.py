"""El cursor de la relectura de DeepSeek en cerrados vive fuera del registro.

Medido el 9-nov a las 10 pm: en la última hora los 56 eventos del registro eran
`deepseek_cerrado` de 27 encargos ya terminados, uno por cada sondeo sin
cambios. La hora de la última lectura es un cursor de trabajo del supervisor,
no un hecho del encargo: vive en estado local del supervisor
(`base/ejecucion/<encargo>/relectura-deepseek.json`, escrito de forma atómica)
y al registro solo llegan los hechos: el arranque del sondeo (`desde`), una
huella nueva de comentarios, una nota, un aviso o una reapertura.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from motor.encargos.ciclo import (
    _epoch_de_hora,
    _paso_entrega,
    _revisar_deepseek_del_cerrado,
)
from motor.encargos.registro import Registro
from tests.dobles import github_por_numero
from tests.test_flujo_revisiones import _entrega_pr


def _medio(ident: str = "F31") -> dict:
    return {
        "id": ident,
        "severity": "medium",
        "status": "open",
        "title": f"hallazgo {ident}",
        "file": "saludo.txt",
    }


def _comentario(*hallazgos: dict) -> str:
    return "DeepSeek: CHANGES_REQUESTED\nai-review:findings=" + json.dumps(
        list(hallazgos)
    )


class RelecturaFueraDelRegistro(unittest.TestCase):
    def _terminado(self, tmp: str):
        vuelta, r = _entrega_pr(
            self, tmp, "ds-relectura-registro", github_por_numero, nivel="integrado"
        )
        e = vuelta("uno")
        hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
        tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = _paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)
        self.assertEqual(e.estado, "terminado")
        vuelta.gh.llamadas.clear()
        return vuelta, r, e

    def _marcas(self, r) -> list[dict]:
        return r.eventos("deepseek_cerrado")

    def test_las_pasadas_sin_cambios_no_dejan_eventos_en_el_registro(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = None
            epoch = _epoch_de_hora(e.hora)
            for suma in (0, 61, 121, 181):
                _revisar_deepseek_del_cerrado(
                    r.reg, r.fold(), vuelta.gh, e.hora, epoch + suma
                )
            self.assertEqual(len(vuelta.gh.llamadas), 4)
            self.assertEqual(
                len(self._marcas(r)),
                1,
                "cada sondeo sin cambios dejó la hora de la lectura en el registro",
            )

    def test_una_huella_nueva_deja_su_evento_y_su_nota_como_hoy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = None
            epoch = _epoch_de_hora(e.hora)
            _revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, e.hora, epoch
            )
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F31"))
            _revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, e.hora, epoch + 61
            )
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F31" in n for n in e.notas_pr), e.notas_pr)
            notas = len(r.eventos("nota_pr"))
            avisos = len(r.eventos("procedimiento"))
            _revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, e.hora, epoch + 122
            )
            self.assertEqual(len(r.eventos("nota_pr")), notas)
            self.assertEqual(len(r.eventos("procedimiento")), avisos)
            self.assertEqual(
                len(self._marcas(r)),
                1,
                "la huella nueva llegó al registro junto con un cursor por sondeo",
            )

    def test_la_cadencia_se_respeta_tras_un_reinicio_simulado(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = None
            epoch = _epoch_de_hora(e.hora)
            hora = e.hora
            for suma in (0, 10, 61):
                _revisar_deepseek_del_cerrado(
                    r.reg, r.fold(), vuelta.gh, hora, epoch + suma
                )
            self.assertEqual(len(vuelta.gh.llamadas), 2)
            otro = Registro(Path(r.reg.base))
            self.addCleanup(otro.close)
            for suma in (70, 122):
                _revisar_deepseek_del_cerrado(
                    otro, otro.fold(e.id), vuelta.gh, hora, epoch + suma
                )
            self.assertEqual(len(vuelta.gh.llamadas), 3)
            self.assertEqual(
                len(self._marcas(r)),
                1,
                "el reinicio releyó desde el registro y dejó cursores por sondeo",
            )

    def test_los_deepseek_cerrado_viejos_se_pliegan_y_no_se_reescriben(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = None
            epoch = _epoch_de_hora(e.hora)
            sha = e.pr_sha
            r.reg.anotar(
                r.enc,
                {
                    "kind": "deepseek_cerrado",
                    "data": {
                        "shas": {sha: epoch},
                        "inicios": {sha: epoch - 60},
                        "desde": epoch - 60,
                        "hora": e.hora,
                    },
                },
                f"viejo:{r.enc}",
            )
            self.assertEqual(len(self._marcas(r)), 1)
            enc = _revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, e.hora, epoch + 61
            )
            self.assertEqual(enc.estado, "terminado")
            self.assertEqual(len(vuelta.gh.llamadas), 1)
            self.assertEqual(
                len(self._marcas(r)),
                1,
                "el sondeo reescribió la marca vieja del registro en cada lectura",
            )


if __name__ == "__main__":
    unittest.main()
