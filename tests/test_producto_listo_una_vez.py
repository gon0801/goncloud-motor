"""El producto que entregó un intento se anota una vez, aunque se vuelva a mirar.

Con comprobaciones declaradas, `_incorporar_responsable` anota `producto_listo`,
lanza la comprobación y sale sin `incorporado`. La pasada siguiente vuelve a
leer el árbol cuando la comprobación ya corrió y, si sus comandos tocaron
`.interno` o el índice de git, arma otro `producto_listo` con la misma ukey.
El registro lo rechaza como `ajena` y lo cuenta por stderr: en la PC salió
`rechazo=ajena kind=producto_listo` dos veces en un encargo largo. Cuando
`anotar` sea estricto, ese rechazo detendría un encargo sano.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from unittest import mock

from motor.encargos import registro as modulo_registro
from motor.encargos.ciclo import _digest_interno
from tests.dobles import github_por_commit
from tests.test_flujo_revisiones import Recorrido, _bloqueante, _entrega_pr, _interno
from tests.test_freno_vueltas import _devolver_el_trabajo
from tests.test_lectura_deepseek import _comentario, _hallazgo

_RECHAZO = "rechazo=ajena kind=producto_listo"


def _declarar(r: Recorrido, codigo: str) -> None:
    (_interno(r) / "comprobaciones.json").write_text(json.dumps({"comprobaciones": [{
        "objetivo": "la comprobación declarada corre en el espacio",
        "argv": [sys.executable, "-c", codigo],
    }]}))


def _incorporar(r: Recorrido) -> tuple[str, list[dict]]:
    """Las dos pasadas del intento, antes y después de la comprobación: su stderr y
    los productos que dejaron en el registro."""
    antes = len(r.reg.eventos(r.enc))
    err = io.StringIO()
    with mock.patch.object(modulo_registro, "_CONTEO", modulo_registro._Conteo()), \
            contextlib.redirect_stderr(err):
        r.incorporar()
    nuevos = r.reg.eventos(r.enc)[antes:]
    return err.getvalue(), [ev["data"] for ev in nuevos if ev["kind"] == "producto_listo"]


class ProductoListoUnaVez(unittest.TestCase):
    def test_lo_que_la_comprobacion_escribe_en_interno_no_reescribe_el_producto(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Recorrido(tmp, "producto-interno", "Crea dato.txt con 42")
            self.addCleanup(r.reg.close)
            (r.espacio / "dato.txt").write_text("41")
            r.incorporar()
            r.juicio({"veredicto": "cambios", "evidencia": "cat dato.txt -> 41",
                      "bloqueantes": [_bloqueante("dato.txt no es 42", "cat dato.txt")]})
            r.incorporar()
            self.assertEqual(r.fold().rol, "responsable")

            (r.espacio / "dato.txt").write_text("42")
            _declarar(r, "open('.interno/salida.txt', 'w').write('ok')")
            err, productos = _incorporar(r)

            self.assertNotIn(_RECHAZO, err)
            (r.espacio / ".interno" / "salida.txt").unlink()
            self.assertEqual(
                [p["interno"] for p in productos], [_digest_interno(r.espacio)],
                "lo guardado es la huella de .interno sin lo que escribió la comprobación",
            )
            self.assertEqual(r.fold().rol, "revisor")

    def test_lo_que_la_comprobacion_cambia_en_git_no_reescribe_el_commit_entregado(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "producto-git", github_por_commit)
            vuelta.gh.textos = [_comentario(_hallazgo("F1"))]
            commit = vuelta("uno").producto.sha_git
            self.assertTrue(commit)
            _devolver_el_trabajo(r, "uno")

            # El responsable vuelve con el mismo commit; la comprobación solo
            # cambia el modo de un archivo, que la firma no ve y git sí.
            _declarar(r, "import os; os.chmod('saludo.txt', 0o755)")
            err, productos = _incorporar(r)

            self.assertNotIn(_RECHAZO, err)
            self.assertEqual(
                [p["sha_git"] for p in productos], [commit],
                "lo guardado es el commit que entregó el responsable",
            )
            self.assertEqual(r.fold().producto.sha_git, commit)
