"""El check de merge `deepseek-sin-abiertos` sobre los stickies reales.

Los cuerpos de `tests/datos/sticky/` se extrajeron del sticky auténtico de
`github-actions[bot]` en #394 (F9 High abierto) y #405 (sin hallazgos
bloqueantes abiertos). Las pruebas hablan con el comando, no importan el
módulo: así la base falla en la aserción que nombra el defecto y no por una
importación rota.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.sticky_reales import STICKY_394, STICKY_405

RAIZ = Path(__file__).resolve().parents[1]
REALES = {394: STICKY_394, 405: STICKY_405}


def _sticky(numero: int) -> dict:
    texto = REALES[numero]
    sha = next(
        linea.split("ai-review:sha=", 1)[1].split(" ", 1)[0]
        for linea in texto.splitlines()
        if "ai-review:sha=" in linea
    )
    return {"texto": texto, "autor": "github-actions[bot]", "sha": sha}


def _excepcion(sha: str, hid: str, tipo: str = "descarte") -> str:
    return (
        "<!-- motor:excepciones="
        + json.dumps(
            [{"sha": sha, "id": hid, "tipo": tipo, "motivo": "probado", "hora": "2026-11-08T10:00:00Z"}]
        )
        + " -->"
    )


def _solo_hallazgo(texto: str, hid: str) -> str:
    """El sticky real con un solo hallazgo, para aislar el caso del Medium."""
    marca = "ai-review:findings="
    inicio = texto.find(marca)
    resto = texto[inicio + len(marca):].strip()
    datos, _ = json.JSONDecoder().raw_decode(resto)
    datos["findings"] = [f for f in datos["findings"] if f.get("id") == hid]
    cabecera = texto[:inicio]
    return cabecera + marca + json.dumps(datos, ensure_ascii=False) + "\n"


class StickyRealTest(unittest.TestCase):
    def _check(self, stickies, cuerpo: str, sha: str) -> tuple[dict, int]:
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)
            (carpeta / "cuerpo.md").write_text(cuerpo, encoding="utf-8")
            (carpeta / "stickies.json").write_text(
                json.dumps(stickies, ensure_ascii=False), encoding="utf-8"
            )
            corrida = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "motor.encargos.check_merge",
                    "--sha",
                    sha,
                    "--cuerpo",
                    str(carpeta / "cuerpo.md"),
                    "--stickies",
                    str(carpeta / "stickies.json"),
                ],
                cwd=RAIZ,
                capture_output=True,
                text=True,
            )
        self.assertTrue(
            corrida.stdout.strip(),
            "el check no respondió: " + corrida.stderr[-800:],
        )
        return json.loads(corrida.stdout), corrida.returncode

    def test_falla_con_el_sticky_real_de_394_por_F9_high_abierto(self):
        sticky = _sticky(394)
        resultado, rc = self._check([sticky], "", sticky["sha"])
        self.assertEqual(resultado["estado"], "falla")
        self.assertEqual(rc, 1)
        self.assertIn("F9", {h["id"] for h in resultado["abiertos"]})

    def test_pasa_con_el_sticky_real_de_405(self):
        sticky = _sticky(405)
        resultado, rc = self._check([sticky], "", sticky["sha"])
        self.assertEqual(resultado["estado"], "pasa")
        self.assertEqual(rc, 0)
        self.assertEqual(resultado["abiertos"], [])

    def test_pasa_con_una_omision_registrada_y_sin_sticky(self):
        sha = _sticky(405)["sha"]
        resultado, rc = self._check([], _excepcion(sha, "", tipo="omision"), sha)
        self.assertEqual(resultado["estado"], "pasa")
        self.assertEqual(rc, 0)
        self.assertIn("omisión", resultado["nota"])

    def test_pasa_con_un_medium_descartado_por_el_revisor_independiente(self):
        sticky = _sticky(394)
        solo_medium = dict(sticky)
        solo_medium["texto"] = _solo_hallazgo(sticky["texto"], "F10")
        cuerpo = _excepcion(sticky["sha"], "F10")
        resultado, rc = self._check([solo_medium], cuerpo, sticky["sha"])
        self.assertEqual(resultado["estado"], "pasa")
        self.assertEqual(rc, 0)
        self.assertEqual(resultado["exceptuados"], ["F10"])

    def test_usa_el_ultimo_marcador_que_escribe_el_motor(self):
        # La prosa del PR puede citar la marca antes; el motor la escribe al
        # final. Solo la última aparición exceptúa.
        sticky = _sticky(394)
        solo_f9 = dict(sticky)
        solo_f9["texto"] = _solo_hallazgo(sticky["texto"], "F9")
        cuerpo = _excepcion(sticky["sha"], "F10") + "\n\n" + _excepcion(sticky["sha"], "F9")
        resultado, rc = self._check([solo_f9], cuerpo, sticky["sha"])
        self.assertEqual(resultado["estado"], "pasa")
        self.assertEqual(rc, 0)
        self.assertEqual(resultado["exceptuados"], ["F9"])

    def test_falla_con_un_descarte_de_otro_sha(self):
        sticky = _sticky(394)
        otro = "0" * 40
        resultado, rc = self._check(
            [sticky], _excepcion(otro, "F9") + _excepcion(otro, "F10"), sticky["sha"]
        )
        self.assertEqual(resultado["estado"], "falla")
        self.assertEqual(rc, 1)

    def test_sin_sticky_y_sin_omision_queda_pendiente(self):
        sha = _sticky(405)["sha"]
        resultado, rc = self._check([], "", sha)
        self.assertEqual(resultado["estado"], "pendiente")
        self.assertEqual(rc, 2)

    def test_un_autor_distinto_no_acredita_el_sticky(self):
        sticky = _sticky(394)
        falso = {**sticky, "autor": "DeepSeek"}
        resultado, _rc = self._check([falso], "", sticky["sha"])
        self.assertEqual(resultado["estado"], "pendiente")

    def test_un_marcador_ilegible_no_exceptua(self):
        sticky = _sticky(394)
        resultado, _rc = self._check([sticky], "<!-- motor:excepciones={roto -->", sticky["sha"])
        self.assertEqual(resultado["estado"], "falla")


class MarcadorEnElCuerpo(unittest.TestCase):
    def test_el_motor_publica_la_excepcion_con_su_sha_y_hora(self):
        from motor.encargos import ciclo
        from motor.encargos.registro import Registro

        cuerpo = ""
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            try:
                ident = ciclo.recibir(
                    reg, "publicar", "marcador", "2026-11-08T10:00:00Z",
                    entrega_nivel="pr", repo="o/r", base_rama="main",
                )["encargo"]
                sha = "a" * 40
                reg.anotar(
                    ident,
                    {
                        "kind": "hallazgo_fuera_de_alcance",
                        "data": {
                            "id": "F9", "titulo": "un hallazgo",
                            "razon": "fuera de alcance", "hora": "2026-11-08T10:01:00Z",
                        },
                    },
                    "fuera-alcance-prueba",
                )

                class Gh:
                    def actualizar_cuerpo(self, repo, numero, texto):
                        nonlocal cuerpo
                        cuerpo = texto

                    def ver(self, repo, numero):
                        raise AssertionError("no debe releer el PR")

                ciclo._actualizar_cuerpo_pr(
                    reg, reg.fold(ident), Gh(), 7, sha,
                    ciclo._espacio(reg.base, ident), "2026-11-08T10:02:00Z",
                )
            finally:
                reg.close()
        self.assertIn("motor:excepciones=", cuerpo)
        marcador, _ = json.JSONDecoder().raw_decode(
            cuerpo.split("motor:excepciones=", 1)[1].lstrip().removeprefix("-->").lstrip()
        )
        self.assertEqual(marcador[0]["sha"], "a" * 40)
        self.assertEqual(marcador[0]["id"], "F9")

    def test_la_excepcion_se_deriva_del_evento_y_llega_al_marcador(self):
        # El dato se anota como evento y se deriva en el dominio: `fold` lo
        # deja en `Encargo.excepciones` y el marcador lo publica sin recorrer
        # los eventos crudos en `ciclo.py`.
        from motor.encargos import ciclo
        from motor.encargos.registro import Registro

        cuerpo = ""
        sha = "b" * 40
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            try:
                ident = ciclo.recibir(
                    reg, "publicar", "marcador-derivado", "2026-11-08T10:00:00Z",
                    entrega_nivel="pr", repo="o/r", base_rama="main",
                )["encargo"]
                reg.anotar(
                    ident,
                    {
                        "kind": "excepcion",
                        "data": {
                            "tipo": "descarte", "sha": sha, "id": "F9",
                            "motivo": "el revisor lo descarto",
                            "hora": "2026-11-08T10:01:00Z",
                        },
                    },
                    "excepcion-prueba",
                )
                encargo = reg.fold(ident)
                self.assertEqual(
                    encargo.excepciones,
                    ((
                        "descarte", sha, "F9", "el revisor lo descarto",
                        "2026-11-08T10:01:00Z",
                    ),),
                )

                class Gh:
                    def actualizar_cuerpo(self, repo, numero, texto):
                        nonlocal cuerpo
                        cuerpo = texto

                    def ver(self, repo, numero):
                        raise AssertionError("no debe releer el PR")

                ciclo._actualizar_cuerpo_pr(
                    reg, encargo, Gh(), 7, sha,
                    ciclo._espacio(reg.base, ident), "2026-11-08T10:02:00Z",
                )
            finally:
                reg.close()
        self.assertIn("motor:excepciones=", cuerpo)
        marcador, _ = json.JSONDecoder().raw_decode(
            cuerpo.split("motor:excepciones=", 1)[1].lstrip().removeprefix("-->").lstrip()
        )
        self.assertEqual(
            marcador,
            [{
                "tipo": "descarte", "sha": sha, "id": "F9",
                "motivo": "el revisor lo descarto",
                "hora": "2026-11-08T10:01:00Z",
            }],
        )


class PublicacionDelCheck(unittest.TestCase):
    """El disparo y la publicación que no cubre el comando aislado."""

    def flujo(self) -> str:
        ruta = RAIZ / ".github" / "workflows" / "deepseek-sin-abiertos.yml"
        self.assertTrue(
            ruta.is_file(),
            "falta el workflow deepseek-sin-abiertos.yml",
        )
        return ruta.read_text(encoding="utf-8")

    def test_reevalua_por_un_camino_que_github_si_dispara(self):
        # El sticky lo escribe GITHUB_TOKEN, así que `issue_comment` nunca
        # dispara. La relectura va por workflow_run y por el calendario.
        texto = self.flujo()
        self.assertIn("workflow_run:", texto)
        self.assertIn("ai-review-publish", texto)
        self.assertIn("schedule:", texto)
        self.assertIn("workflow_dispatch:", texto)
        self.assertIn("types: [opened, synchronize, reopened, edited]", texto)
        self.assertIn("state=open", texto)

    def test_el_titulo_del_workflow_run_no_entra_al_script(self):
        # display_title lo controla el evento: interpolarlo en un run de un job
        # con checks: write permite inyectar comandos. Va por env y variable.
        texto = self.flujo()
        self.assertNotIn('numeros="${{', texto)
        self.assertIn(
            "TITULO_RUN: ${{ github.event.workflow_run.display_title }}", texto
        )
        self.assertIn('numeros="$TITULO_RUN"', texto)

    def test_pendiente_no_satisface_el_check_requerido(self):
        texto = self.flujo()
        self.assertNotIn("conclusion=neutral", texto)
        self.assertIn('publicar "$sha" in_progress ""', texto)
        # El pendiente no se publica con una conclusión.
        self.assertIn('if [ -n "$conclusion" ]', texto)
        self.assertIn('sha="$(gh api "repos/${repo}/pulls/${numero}" --jq .head.sha)"', texto)


if __name__ == "__main__":
    unittest.main()
