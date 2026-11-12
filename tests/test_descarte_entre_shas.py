"""Un descarte del revisor independiente sigue valiendo en los SHA siguientes
mientras la región que el hallazgo ancla no cambie.

Caso real (enc_fd795218afa32147, PR #442, 11-nov): DeepSeek repitió F3 en cada
SHA nuevo del PR, el descarte anterior —guardado por SHA en
`Encargo.excepciones`— ya no aplicaba, y el motor devolvía el trabajo al
responsable a "corregir F3" aunque no había nada que corregir. Dos vueltas sin
progreso con la misma huella, un relevo sin otro modelo autorizado y un
`bloqueo_fase` a las 00:05:04Z.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock

from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.check_merge import decidir
from motor.encargos.ciclo import _paso_entrega, ahora_iso
from motor.encargos.dominio import bloqueantes_abiertos
from tests.dobles import github_por_numero
from tests.test_flujo_revisiones import _entrega_pr, Recorrido

TITULO_F3 = "Tres returns nuevos exceden la línea base de pyright"


def setUpModule():
    # Estas pruebas no ejercen la jaula: la sonda se da por buena también donde
    # sandbox-exec existe pero no se puede anidar. El parche se retira al
    # terminar el módulo para no ensuciar al resto del banco.
    parche = mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    )
    parche.start()
    globals()["_PARCHE_SONDA"] = parche


def tearDownModule():
    parche = globals().pop("_PARCHE_SONDA", None)
    if parche is not None:
        parche.stop()


def _hallazgo(ident: str = "F3", linea: int = 1, estado: str = "open") -> dict:
    return {
        "id": ident,
        "severity": "medium",
        "status": estado,
        "title": TITULO_F3,
        "file": "saludo.txt",
        "line": linea,
    }


def _comentario(*hallazgos: dict) -> str:
    return (
        "DeepSeek: CHANGES_REQUESTED\nai-review:findings="
        + json.dumps({"findings": list(hallazgos)})
    )


def _comentarios(directorio):
    return github_por_numero(directorio, "")


def _ids(e) -> list[str]:
    return [h.id for h in bloqueantes_abiertos(e, "deepseek")]


def _sticky(sha: str) -> dict:
    return {
        "autor": "github-actions[bot]",
        "sha": sha,
        "texto": (
            "<!-- ai-review:sticky -->\n"
            "ai-review:sha=" + sha + "\n"
            "ai-review:findings=" + json.dumps({"findings": [_hallazgo()]})
        ),
    }


def _cuerpo_del_pr(vuelta) -> str:
    prs = vuelta.gh._cargar()["prs"]
    return next(iter(prs.values()))["cuerpo"]


def _descarta_el_revisor(r: Recorrido, evidencia: str) -> None:
    (r.espacio / ".interno" / "disputa.json").write_text(
        json.dumps({"hallazgo": "F3", "evidencia": evidencia}),
        encoding="utf-8",
    )
    r.incorporar()
    r.juicio(
        {
            "veredicto": "aprobado",
            "evidencia": evidencia,
            "resueltos": ["F3"],
        }
    )
    r.incorporar()


class DescarteEntreShas(unittest.TestCase):
    def _con_descarte_en_sha_a(self, tmp, clave):
        vuelta, r = _entrega_pr(self, tmp, clave, _comentarios)
        vuelta.gh.textos[None] = _comentario(_hallazgo())
        e = vuelta("uno")
        self.assertEqual(_ids(e), ["F3"])
        self.assertEqual(e.estrategia, "investigar")
        sha_a = e.pr_sha
        _descarta_el_revisor(
            r,
            "el candado del repo da sin errores nuevos (hoy 246, base 306)",
        )
        e = r.fold()
        self.assertEqual(_ids(e), [])
        self.assertEqual(
            [(x[0], x[1], x[2]) for x in e.excepciones],
            [("descarte", sha_a, "F3")],
        )
        return vuelta, r, sha_a

    def test_descarte_vigente_tras_commit_que_no_toca_la_region(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, sha_a = self._con_descarte_en_sha_a(tmp, "descarte-vigente")
            antes_estrategias = [
                ev
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "estrategia"
            ]

            # El responsable hace un commit B que no toca la región de F3.
            (r.espacio / "otro.txt").write_text("dato\n", encoding="utf-8")
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "lei otro.txt; saludo.txt queda intacto",
                }
            )
            r.incorporar()
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())

            sha_b = e.pr_sha
            self.assertNotEqual(sha_b, sha_a)
            self.assertEqual(_ids(e), [])
            self.assertEqual(
                [
                    ev
                    for ev in r.reg.eventos(r.enc)
                    if ev["kind"] == "estrategia" and ev not in antes_estrategias
                    and "F3" in str(ev["data"].get("siguiente") or "")
                ],
                [],
                "el motor volvió a mandar al responsable a corregir F3",
            )
            self.assertEqual(
                [ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "sin_progreso"],
                [],
            )
            excepciones = [x for x in r.fold().excepciones if x[2] == "F3"]
            self.assertIn(("descarte", sha_b, "F3"), [
                (x[0], x[1], x[2]) for x in excepciones
            ])
            cuerpo = _cuerpo_del_pr(vuelta)
            veredicto = decidir([_sticky(sha_b)], cuerpo, sha_b)
            self.assertEqual(veredicto["estado"], "pasa")

    def test_la_region_cambiada_hace_caducar_el_descarte(self):  # no-roja-en-base: la base ya cuenta el hallazgo de nuevo cuando su archivo cambió; la prueba guarda el límite del descarte
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, sha_a = self._con_descarte_en_sha_a(tmp, "descarte-caduco")

            # El commit B reescribe el archivo que F3 ancla.
            (r.espacio / "saludo.txt").write_text("dos\n", encoding="utf-8")
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "lei saludo.txt con dos",
                }
            )
            r.incorporar()
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())

            sha_b = e.pr_sha
            self.assertNotEqual(sha_b, sha_a)
            self.assertEqual(_ids(e), ["F3"], "el hallazgo debe volver a contar")
            self.assertTrue(any(
                ev["kind"] == "estrategia"
                and "F3" in str(ev["data"].get("siguiente") or "")
                for ev in r.reg.eventos(r.enc)
            ))
            self.assertEqual(
                [x for x in r.fold().excepciones if x[1] == sha_b],
                [],
            )
            veredicto = decidir(
                [_sticky(sha_b)], _cuerpo_del_pr(vuelta), sha_b
            )
            self.assertEqual(veredicto["estado"], "falla")

    def test_el_descarte_sigue_vigente_cuando_la_region_se_desplaza(self):
        # El commit B agrega líneas por encima y la línea anclada de F3 se
        # desplaza: el contenido que el hallazgo señala sigue igual, así que
        # el descarte no caduca por un cambio de número de línea.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, sha_a = self._con_descarte_en_sha_a(tmp, "descarte-desplazado")
            antes_estrategias = [
                ev
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "estrategia"
            ]

            contenido = (r.espacio / "saludo.txt").read_text(encoding="utf-8")
            (r.espacio / "saludo.txt").write_text(
                "encabezado\n" + contenido, encoding="utf-8"
            )
            r.incorporar()
            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "lei saludo.txt; el encabezado no toca la línea anclada",
                }
            )
            r.incorporar()
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())

            sha_b = e.pr_sha
            self.assertNotEqual(sha_b, sha_a)
            self.assertEqual(
                _ids(e),
                [],
                "la línea anclada solo se desplazó; el descarte sigue valiendo",
            )
            self.assertEqual(
                [
                    ev
                    for ev in r.reg.eventos(r.enc)
                    if ev["kind"] == "estrategia" and ev not in antes_estrategias
                    and "F3" in str(ev["data"].get("siguiente") or "")
                ],
                [],
                "el motor volvió a mandar al responsable a corregir F3",
            )
            veredicto = decidir(
                [_sticky(sha_b)], _cuerpo_del_pr(vuelta), sha_b
            )
            self.assertEqual(veredicto["estado"], "pasa")


if __name__ == "__main__":
    unittest.main()
