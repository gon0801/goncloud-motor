"""El PR que choca con su base se dice y se corrige, sin esperar 90 minutos.

El doble de GitHub declara `mergeable`/`mergeStateStatus` en `repos.json`. El
repo es real: la rama y `main` se tocan con git para que la mezcla de verdad
publique o choque.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace as dataclasses_replace
from pathlib import Path

from motor.encargos import ciclo
from motor.encargos.ciclo import ahora_iso
from motor.encargos.dominio import Fase
from motor.github import GhGitHub
from tests.test_flujo_revisiones import _entrega_pr, sonda_sin_aislamiento_del_host

from tests.dobles import github_con_corridas


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@l", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _rama(bare: Path, rama: str, archivo: str) -> str:
    salida = subprocess.run(
        ["git", "--git-dir", str(bare), "show", f"{rama}:{archivo}"],
        capture_output=True,
        text=True,
    )
    return salida.stdout if salida.returncode == 0 else ""


def _avanzar_main(raiz: Path, archivo: str, texto: str) -> None:
    bare = raiz / "repo.git"
    otro = raiz / "otro"
    if not otro.exists():
        subprocess.run(["git", "clone", "--quiet", str(bare), str(otro)], check=True)
    (otro / archivo).write_text(texto)
    _git(otro, "add", "-A")
    _git(otro, "commit", "--quiet", "-m", f"main cambia {archivo}")
    _git(otro, "push", "--quiet", "origin", "main")


def _rama_desde_main(raiz: Path, rama: str, archivo: str, texto: str) -> None:
    bare = raiz / "repo.git"
    otro = raiz / "otro"
    if not otro.exists():
        subprocess.run(["git", "clone", "--quiet", str(bare), str(otro)], check=True)
    _git(otro, "fetch", "--quiet", "origin")
    _git(otro, "checkout", "--quiet", "-B", rama, "origin/main")
    (otro / archivo).write_text(texto)
    _git(otro, "add", "-A")
    _git(otro, "commit", "--quiet", "-m", f"{rama} cambia {archivo}")
    _git(otro, "push", "--quiet", "-u", "origin", rama)
    _git(otro, "checkout", "--quiet", "main")


def _cambiar_base_del_pr(vuelta, numero: int, base: str) -> None:
    ruta = vuelta.gh.d / "github.json"
    guardado = json.loads(ruta.read_text())
    for pr in guardado["prs"].values():
        if pr["numero"] == numero:
            pr["base"] = base
    ruta.write_text(json.dumps(guardado))


def _config(vuelta, repo: str, datos: dict) -> None:
    ruta = vuelta.gh.d / "repos.json"
    actual = json.loads(ruta.read_text()) if ruta.exists() else {}
    actual[repo] = {**actual.get(repo, {}), **datos}
    ruta.write_text(json.dumps(actual))


def _notas(r) -> list[str]:
    return [
        str(ev["data"].get("nota") or "")
        for ev in r.reg.eventos(r.enc)
        if ev["kind"] == "procedimiento"
    ]


def _estrategias(r) -> list[dict]:
    return [ev["data"] for ev in r.reg.eventos(r.enc) if ev["kind"] == "estrategia"]


class ChoqueAntesDelMerge(unittest.TestCase):
    def test_conflicto_dice_que_el_pr_choca_y_devuelve_los_archivos(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "choque-main", github_con_corridas,
                config={"mergeable": "CONFLICTING"}, nivel="integrado",
            )
            _avanzar_main(Path(tmp), "saludo.txt", "desde main\n")

            e = vuelta("hola\n")

            self.assertNotEqual(e.estado, "terminado")
            estrategias = _estrategias(r)
            self.assertEqual(estrategias[-1]["estrategia"], "integrar_main")
            self.assertIn("el PR choca con main", estrategias[-1]["motivo"])
            self.assertIn(
                "conflicto en saludo.txt",
                " ".join(_notas(r)),
                "la corrección nombra los archivos en conflicto",
            )
            self.assertTrue(
                _git(r.espacio, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").strip(),
                "la mezcla que chocó queda en el espacio para resolverla",
            )

    def test_rama_atras_con_mezcla_limpia_publica_y_deja_seguir(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "atras-main", github_con_corridas,
                config={
                    "mergeable": "MERGEABLE",
                    "merge_state_status": "BEHIND",
                },
                nivel="integrado",
            )
            _avanzar_main(Path(tmp), "main.txt", "desde main\n")

            e = vuelta("hola\n")

            self.assertNotEqual(e.estado, "terminado", "el CI sigue sobre el SHA nuevo")
            rama = str(e.entrega.rama)
            self.assertEqual(
                _rama(Path(tmp) / "repo.git", rama, "main.txt"),
                "desde main\n",
                "la mezcla limpia se publicó en la rama del PR",
            )
            self.assertIn("mezcla limpia", " ".join(_notas(r)))

    def test_el_merge_que_choca_en_el_ciclo_tambien_vuelve_al_responsable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "choque-merge", github_con_corridas,
                config={}, nivel="integrado",
            )
            _avanzar_main(Path(tmp), "saludo.txt", "desde main\n")

            e = vuelta("hola\n")

            self.assertNotEqual(e.estado, "terminado")
            estrategias = _estrategias(r)
            self.assertEqual(estrategias[-1]["estrategia"], "integrar_main")
            self.assertIn("conflicto en saludo.txt", " ".join(_notas(r)))


class LaMezclaSinResolverNoSePublica(unittest.TestCase):
    def test_entregar_con_pendientes_no_publica_marcadores_y_dice_por_que(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "choque-pendientes", github_con_corridas,
                config={"mergeable": "CONFLICTING"}, nivel="integrado",
            )
            _avanzar_main(Path(tmp), "saludo.txt", "desde main\n")
            e = vuelta("hola\n")
            self.assertTrue(
                _git(r.espacio, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").strip()
            )
            bare = Path(tmp) / "repo.git"
            rama = str(e.entrega.rama)
            punta = _git(bare, "rev-parse", rama).strip()

            e = ciclo._entregar_con_pendientes(r.reg, r.fold(), vuelta.gh, ahora_iso())

            self.assertEqual(
                _git(bare, "rev-parse", rama).strip(), punta,
                "la rama remota no cambia",
            )
            self.assertNotIn("<<<<<<<", _rama(bare, rama, "saludo.txt"))
            self.assertTrue(
                any("mezcla sin resolver" in nota for nota in e.notas_pr),
                e.notas_pr,
            )
            self.assertTrue(
                any("mezcla sin resolver" in x for x in e.evidencia), e.evidencia
            )
            self.assertIn("<<<<<<<", (r.espacio / "saludo.txt").read_text())


class MergeableAunSinCalcular(unittest.TestCase):
    def test_la_lectura_de_produccion_reintenta_ante_unknown(self):
        gh = GhGitHub(env={})
        respuestas = iter(
            [
                json.dumps({"mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"}),
                json.dumps({"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"}),
            ]
        )
        lecturas = []

        def responder(*args):
            lecturas.append(args)
            return next(respuestas)

        gh._gh = responder
        gh.dormir = lambda _s: None

        estado = gh.estado_merge("o/r", 7)

        self.assertEqual(estado.estado, "choca")
        self.assertEqual(
            len(lecturas), 2, "la primera respuesta UNKNOWN vuelve a preguntar"
        )

    def test_reintenta_hasta_que_github_calcula_el_choque(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "mergeable-tarde", github_con_corridas, nivel="pr",
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            _avanzar_main(Path(tmp), "saludo.txt", "desde main\n")
            repo = str(e.entrega.repo)
            _config(
                vuelta,
                repo,
                {
                    "mergeables": [
                        {"mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"},
                        {"mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"},
                    ]
                },
            )

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())

            self.assertIn("el PR choca con main", e.fase_actual.ci_pr_causa)
            self.assertGreaterEqual(
                getattr(vuelta.gh, "mergeables_llamadas", {}).get(repo, 0),
                2,
                "una respuesta UNKNOWN vuelve a preguntar",
            )
            self.assertEqual(e.fase_actual.ci_pr_estado, "sin_resultado")
            self.assertEqual(
                [ev["data"]["estado"] for ev in r.reg.eventos(r.enc) if ev["kind"] == "ci_pr"],
                ["sin_resultado"],
            )
            self.assertIn(
                "traer main",
                " ".join(_notas(r)),
                "el choque leído por el CI también llama a traer_main",
            )
            self.assertEqual(_estrategias(r)[-1]["estrategia"], "integrar_main")
            self.assertIn("el PR choca con main", _estrategias(r)[-1]["motivo"])
            self.assertIn("conflicto en saludo.txt", " ".join(_notas(r)))


class MezclaLimpiaEnElAtajoDelCi(unittest.TestCase):
    def test_la_mezcla_limpia_del_atajo_del_ci_publica_y_sigue(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ci-limpia", github_con_corridas, nivel="pr",
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            _avanzar_main(Path(tmp), "main.txt", "desde main\n")
            _config(
                vuelta,
                str(e.entrega.repo),
                {"mergeable": "MERGEABLE", "merge_state_status": "BEHIND"},
            )

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())

            self.assertNotEqual(e.estado, "terminado", "el CI sigue sobre el SHA nuevo")
            self.assertEqual(
                _rama(Path(tmp) / "repo.git", str(e.entrega.rama), "main.txt"),
                "desde main\n",
                "la mezcla limpia del atajo del CI se publicó en la rama del PR",
            )
            self.assertIn("mezcla limpia", " ".join(_notas(r)))
            self.assertEqual(e.fase_actual.ci_pr_estado, "sin_resultado")


class BaseRealDelPr(unittest.TestCase):
    def test_el_atajo_del_ci_mezcla_la_base_real_del_pr(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "base-real", github_con_corridas, nivel="pr",
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            raiz = Path(tmp)
            _rama_desde_main(raiz, "fase", "fase.txt", "desde la fase\n")
            _avanzar_main(raiz, "main.txt", "desde main\n")
            _cambiar_base_del_pr(vuelta, e.pr_numero, "fase")
            _config(
                vuelta,
                str(e.entrega.repo),
                {"mergeable": "MERGEABLE", "merge_state_status": "BEHIND"},
            )

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())

            rama = str(e.entrega.rama)
            self.assertEqual(
                _rama(raiz / "repo.git", rama, "fase.txt"),
                "desde la fase\n",
                "mezcla la base real del PR, que es la rama de la fase",
            )
            self.assertEqual(
                _rama(raiz / "repo.git", rama, "main.txt"),
                "",
                "no mezcla main, que no es la base del PR",
            )


class RespaldoDeLaBaseDelPr(unittest.TestCase):
    def test_el_respaldo_usa_la_base_de_la_fase_apilada(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "respaldo-apilado", github_con_corridas, nivel="pr",
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            rama_base = "base-de-la-fase"
            fases = (
                Fase(id="a", resultado="A", rama=rama_base),
                Fase(
                    id="b",
                    resultado="B",
                    requiere=(("a", ""),),
                    rama=str(e.entrega.rama),
                    pr_numero=e.pr_numero,
                    pr_sha=e.pr_sha,
                ),
            )
            apilado = dataclasses_replace(e, fases=fases, fase_activa="b")

            class GhSinVista:
                def ver(self, repo, numero):
                    raise RuntimeError("gh no responde")

            self.assertEqual(
                ciclo._base_del_pr(GhSinVista(), apilado),
                rama_base,
                "el respaldo no vuelve a la base autorizada en un PR apilado",
            )


if __name__ == "__main__":
    unittest.main()
