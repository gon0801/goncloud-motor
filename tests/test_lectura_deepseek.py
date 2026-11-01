"""El motor lee el veredicto vigente de DeepSeek, no solo el primero por SHA.

Casos del 1-nov: un descarte (`ai-review: descartar Fn`) o una re-revisión sobre
el mismo commit nunca llegaban al motor (la lectura se anotaba una vez por SHA),
y una revisión que DeepSeek publicaba después de que el motor leyó cerraba la
fase, o el encargo, con un Medium abierto (enc_19680d773791f339, PR #147)."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.ciclo import ahora_iso
from motor.encargos.dominio import bloqueantes_abiertos, prompt_responsable, vista
from motor.github import FakeGitHub
from tests.test_entrega_misma_version import _barrido, _pasos
from motor.encargos.aislamiento import SondaEntorno
from tests.test_flujo_revisiones import _entrega_pr


def setUpModule():
    # Estas pruebas no ejercen la jaula: la sonda se da por buena también donde
    # sandbox-exec existe pero no se puede anidar (sesión de agente, jaula del comprobador).
    parche = mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    )
    parche.start()
    unittest.addModuleCleanup(parche.stop)


def _hallazgo(ident: str, severidad: str = "high", estado: str = "open") -> dict:
    return {
        "id": ident,
        "severity": severidad,
        "status": estado,
        "title": f"hallazgo {ident}",
        "file": "saludo.txt",
    }


def _comentario(*hallazgos: dict, nota: str = "DeepSeek") -> str:
    return nota + "\nai-review:findings=" + json.dumps(list(hallazgos))


class _GhComentarios(FakeGitHub):
    """DeepSeek publica el texto de `textos[numero]`, o el de `textos[None]`.
    Un texto puede ser una función del número de llamada, para que cambie entre
    dos lecturas."""

    def __init__(self, d):
        super().__init__(d)
        self.textos: dict = {}
        self.llamadas: list = []

    def comentarios_deepseek(self, repo, numero, sha=""):
        self.llamadas.append((numero, sha))
        texto = self.textos.get(numero, self.textos.get(None))
        if callable(texto):
            texto = texto(len(self.llamadas))
        return [{"texto": texto, "autor": "DeepSeek", "sha": sha}] if texto else []


def _ids(e) -> list[str]:
    return [h.id for h in bloqueantes_abiertos(e, "deepseek")]


class RelecturaDelMismoSha(unittest.TestCase):
    def _devuelto_por_f1(self, tmp, clave):
        vuelta, r = _entrega_pr(self, tmp, clave, _GhComentarios)
        vuelta.gh.textos[None] = _comentario(_hallazgo("F1"))
        e = vuelta("uno")
        self.assertEqual(e.estrategia, "investigar")
        self.assertEqual(_ids(e), ["F1"])
        self.assertEqual(e.deepseek_sha, e.pr_sha)
        return vuelta, r

    def test_un_descarte_sobre_el_mismo_sha_llega_en_el_siguiente_barrido(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._devuelto_por_f1(tmp, "ds-descarte")
            vuelta.gh.textos[None] = _comentario(_hallazgo("F1", estado="dismissed"))
            e = _barrido(r, vuelta.gh, time.time())
            self.assertEqual(_ids(e), [])
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertEqual(len(_pasos(r, "deepseek_leido")), 2)

    def test_una_resolucion_sobre_el_mismo_sha_llega_en_el_siguiente_barrido(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._devuelto_por_f1(tmp, "ds-resuelto")
            vuelta.gh.textos[None] = _comentario(_hallazgo("F1", estado="resolved"))
            e = _barrido(r, vuelta.gh, time.time())
            self.assertEqual(_ids(e), [])

    def test_un_hallazgo_nuevo_sobre_el_mismo_sha_llega_y_devuelve_el_trabajo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._devuelto_por_f1(tmp, "ds-nuevo")
            vuelta.gh.textos[None] = _comentario(_hallazgo("F1"), _hallazgo("F2", "medium"))
            e = _barrido(r, vuelta.gh, time.time())
            self.assertEqual(_ids(e), ["F1", "F2"])
            self.assertEqual(e.estrategia, "investigar")

    def test_el_mismo_comentario_no_anota_eventos_repetidos(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._devuelto_por_f1(tmp, "ds-igual")
            antes = len(r.reg.eventos(r.enc))
            llamadas = len(vuelta.gh.llamadas)
            t0 = time.time()
            for i in range(3):
                e = _barrido(r, vuelta.gh, t0 + 61 * i)
            self.assertEqual(len(vuelta.gh.llamadas) - llamadas, 3)
            self.assertEqual(len(r.reg.eventos(r.enc)), antes)
            self.assertEqual(_ids(e), ["F1"])

    def test_barridos_seguidos_no_golpean_a_github(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._devuelto_por_f1(tmp, "ds-cuota")
            llamadas = len(vuelta.gh.llamadas)
            t0 = time.time()
            for i in range(10):
                _barrido(r, vuelta.gh, t0 + i)
            self.assertEqual(len(vuelta.gh.llamadas) - llamadas, 1)

    def test_una_re_revision_con_los_mismos_hallazgos_se_lee_una_vez_por_comentario(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._devuelto_por_f1(tmp, "ds-rerevision")
            t0 = time.time()
            vuelta.gh.textos[None] = _comentario(_hallazgo("F1"), nota="DeepSeek: generacion 2")
            _barrido(r, vuelta.gh, t0)
            self.assertEqual(len(_pasos(r, "deepseek_leido")), 2)
            _barrido(r, vuelta.gh, t0 + 61)
            self.assertEqual(len(_pasos(r, "deepseek_leido")), 2)
            vuelta.gh.textos[None] = _comentario(_hallazgo("F1"))
            e = _barrido(r, vuelta.gh, t0 + 122)
            self.assertEqual(len(_pasos(r, "deepseek_leido")), 3)
            self.assertEqual(_ids(e), ["F1"])


class VeredictoVigenteAlCerrar(unittest.TestCase):
    def test_un_medium_publicado_tras_la_lectura_impide_cerrar_la_fase(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-tardio", _GhComentarios)
            vuelta.gh.textos[None] = lambda n: (
                _comentario() if n == 1 else _comentario(_hallazgo("F6", "medium"))
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(_ids(e), ["F6"])
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.nivel_alcanzado, "pr_abierto")

    def test_sin_cambios_en_el_comentario_la_fase_cierra(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-limpio", _GhComentarios)
            vuelta.gh.textos[None] = _comentario()
            e = vuelta("uno")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(_ids(e), [])

    def test_si_deepseek_no_reviso_el_sha_final_el_motor_espera_y_no_integra(self):
        for nivel in ("pr", "integrado", "desplegado"):
            with self.subTest(nivel=nivel), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                vuelta, r = _entrega_pr(
                    self, tmp, f"ds-espera-{nivel}", _GhComentarios, nivel=nivel
                )
                e = vuelta("uno")
                self.assertNotEqual(e.estado, "terminado")
                self.assertEqual(e.estado, "esperando_reintento")
                self.assertIn("esperando revisión de DeepSeek", e.motivo)
                self.assertEqual(e.deepseek_sha, "")
                self.assertEqual(_pasos(r, "integrado"), [])
                self.assertEqual(vuelta.gh.ver(e.entrega.repo, e.pr_numero).estado, "abierto")

    def test_cerrar_sin_pasar_por_la_entrega_no_se_salta_el_veredicto(self):
        # `_cerrar_con_juicio` y el reintento de barrer llaman a `_intentar_cierre`
        # sin puerto de GitHub: con el nivel pr alcanzado no deben cerrar solos.
        def texto(n):
            if n == 2:
                raise RuntimeError("gh api: 502")
            return _comentario()

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-directo", _GhComentarios)
            vuelta.gh.textos[None] = texto
            e = vuelta("uno")
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertEqual(e.estado, "esperando_reintento")

            vuelta.gh.textos[None] = _comentario(_hallazgo("F6", "medium"))
            e = ciclo._intentar_cierre(r.reg, e, r.espacio, r.d, ahora_iso())
            self.assertNotEqual(e.estado, "terminado")

            e = ciclo._paso_entrega(r.reg, e, vuelta.gh, ahora_iso())
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(_ids(e), ["F6"])
            self.assertEqual(e.nivel_alcanzado, "pr_abierto")

    def test_un_medium_en_un_pr_ya_fusionado_no_termina_el_encargo_en_silencio(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-fusionado", _GhComentarios, nivel="integrado"
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "uno", "resultado": "saludo.txt con uno"},
                            {"id": "dos", "resultado": "saludo.txt con dos", "requiere": ["uno"]},
                        ],
                        "hora": r.hora,
                    },
                },
                "fases-dos",
            )
            vuelta.gh.textos[None] = _comentario()
            e = vuelta("uno")
            self.assertEqual(e.fase_activa, "dos")
            primera = e.fases[0]
            self.assertEqual(primera.nivel_alcanzado, "integrado")

            vuelta.gh.textos[primera.pr_numero] = _comentario(_hallazgo("F9", "medium"))
            e = vuelta("dos")
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertEqual(e.fase_activa, "dos")
            self.assertEqual(len(e.preguntas), 1)
            self.assertIn("ya fusionado", e.preguntas[0])
            self.assertIn("hallazgo F9", e.preguntas[0])

            # La pregunta ya se hizo: una segunda comprobación no vuelve a bloquear
            # (si bloqueara, el encargo no cerraría ni tras la respuesta del propietario)
            # ni a preguntar.
            self.assertIsNone(
                ciclo._veredicto_antes_de_cerrar(r.reg, e, vuelta.gh, ahora_iso())
            )
            self.assertEqual(len(r.fold().preguntas), 1)

    def test_sin_puerto_de_github_no_se_da_por_comprobado_el_veredicto(self):
        def texto(n):
            if n == 2:
                raise RuntimeError("gh api: 502")
            return _comentario()

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-sin-puerto", _GhComentarios)
            vuelta.gh.textos[None] = texto
            e = vuelta("uno")
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertNotEqual(e.estado, "terminado")

            e = ciclo._paso_entrega(r.reg, e, None, ahora_iso())
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertIn("sin puerto GitHub", e.motivo)

    def test_un_medium_en_el_pr_de_una_fase_anterior_la_reabre_al_cerrar_el_encargo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-fases", _GhComentarios)
            r.reg.anotar(
                r.enc,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "uno", "resultado": "saludo.txt con uno"},
                            {"id": "dos", "resultado": "saludo.txt con dos", "requiere": ["uno"]},
                        ],
                        "hora": r.hora,
                    },
                },
                "fases-dos",
            )
            vuelta.gh.textos[None] = _comentario()
            e = vuelta("uno")
            self.assertEqual(e.fase_activa, "dos")
            primera = e.fases[0]
            self.assertEqual(primera.estado, "preparada")
            self.assertTrue(primera.pr_numero)

            vuelta.gh.textos[primera.pr_numero] = _comentario(_hallazgo("F9", "medium"))
            e = vuelta("dos")
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.fase_activa, "uno")
            self.assertEqual(e.fases[0].estado, "entrega")
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(_ids(e), ["F9"])
            self.assertEqual(e.fases[1].estado, "entrega")

    def test_si_la_fase_no_se_puede_reabrir_el_encargo_no_termina(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-fases-ilegal", _GhComentarios)
            r.reg.anotar(
                r.enc,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "uno", "resultado": "saludo.txt con uno"},
                            {"id": "dos", "resultado": "saludo.txt con dos", "requiere": ["uno"]},
                        ],
                        "hora": r.hora,
                    },
                },
                "fases-dos",
            )
            vuelta.gh.textos[None] = _comentario()
            e = vuelta("uno")
            vuelta.gh.textos[e.fases[0].pr_numero] = _comentario(_hallazgo("F9", "medium"))

            anotar = r.reg.anotar

            def sin_aplicar(encargo, evento, ukey):
                if evento["kind"] == "fase_reabierta":
                    return "ilegal"
                return anotar(encargo, evento, ukey)

            with mock.patch.object(r.reg, "anotar", sin_aplicar):
                e = vuelta("dos")
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.fase_activa, "dos")
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertIn("antes de cerrar", e.motivo)

    def test_un_medium_tardio_impide_el_merge_con_entrega_integrada_o_desplegada(self):
        for nivel in ("integrado", "desplegado"):
            with self.subTest(nivel=nivel), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                vuelta, r = _entrega_pr(
                    self, tmp, f"ds-{nivel}", _GhComentarios, nivel=nivel
                )
                vuelta.gh.textos[None] = lambda n: (
                    _comentario() if n == 1 else _comentario(_hallazgo("F6", "medium"))
                )
                e = vuelta("uno")
                self.assertNotIn(e.nivel_alcanzado, ("integrado", "desplegado"))
                self.assertNotEqual(e.estado, "terminado")
                self.assertEqual(_ids(e), ["F6"])
                self.assertEqual(e.nivel_alcanzado, "pr_abierto")
                self.assertEqual(_pasos(r, "integrado"), [])
                pr = vuelta.gh.ver(e.entrega.repo, e.pr_numero)
                self.assertEqual(pr.estado, "abierto")


class _GhQueFalla(_GhComentarios):
    """GitHub falla en las lecturas de `fallos` y luego responde."""

    fallos = 0

    def comentarios_deepseek(self, repo, numero, sha=""):
        if self.fallos > 0:
            self.fallos -= 1
            self.llamadas.append((numero, sha))
            raise RuntimeError("gh api: 502")
        return super().comentarios_deepseek(repo, numero, sha)


class RelecturaConGithubCaido(unittest.TestCase):
    def test_un_fallo_no_consume_el_espaciado_y_queda_a_la_vista(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-gh-caido", _GhQueFalla)
            gh = vuelta.gh
            gh.textos[None] = _comentario(_hallazgo("F1"))
            e = vuelta("uno")
            self.assertEqual(_ids(e), ["F1"])
            antes = (e.motivo, e.siguiente, e.causa, e.ultimo_hecho, e.estrategia)
            instruccion = prompt_responsable(e)
            gh.textos[None] = _comentario(_hallazgo("F1", estado="dismissed"))
            gh.fallos = 1
            t0 = time.time()
            e = _barrido(r, gh, t0)
            fallos = [
                ev for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "lectura_fallida"
                and "releer el comentario de DeepSeek" in ev["data"]["causa"]
            ]
            self.assertEqual(
                (e.motivo, e.siguiente, e.causa, e.ultimo_hecho, e.estrategia), antes
            )
            self.assertEqual(prompt_responsable(e), instruccion)
            self.assertNotIn("reintentar la relectura", prompt_responsable(e))
            self.assertEqual(len(fallos), 1)
            self.assertIn("502", fallos[0]["data"]["causa"])
            self.assertEqual(_ids(e), ["F1"])
            self.assertNotEqual(e.estado, "esperando_reintento")
            self.assertIn("502", e.deepseek_error)
            e = _barrido(r, gh, t0 + 16)
            self.assertEqual(_ids(e), [])
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertEqual(e.deepseek_error, "")

    def test_un_github_caido_un_rato_deja_un_solo_aviso_por_tramo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ds-gh-tramo", _GhQueFalla)
            gh = vuelta.gh
            gh.textos[None] = _comentario(_hallazgo("F1"))
            vuelta("uno")
            gh.fallos = 3
            t0 = time.time()
            for i in range(3):
                e = _barrido(r, gh, t0 + 16 * i)
            avisos = [
                ev for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "lectura_fallida" and ev["data"]["causa"]
            ]
            self.assertEqual(len(avisos), 1)
            self.assertIn("502", e.deepseek_error)
            self.assertEqual(vista(e)["entrega"]["deepseek"]["error"], e.deepseek_error)


if __name__ == "__main__":
    unittest.main()

