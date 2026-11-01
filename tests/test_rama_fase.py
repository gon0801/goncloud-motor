"""Traba 8 del 31 de octubre (enc_d07becdd91d69ea6): las fases se declararon
después de empezar, con trabajo sin commit en la rama sin fase. El motor hizo
`git checkout -B <rama-de-fase> origin/<base>`, git abortó por los cambios
locales y el motor reintentó cada 5 minutos sin fin."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import _asegurar_repo, ahora_iso, barrer
from tests.test_flujo_revisiones import Recorrido

FASES = [
    {"id": "integrar-main", "resultado": "la rama integra main"},
    {"id": "entrega", "resultado": "el PR queda listo", "requiere": ["integrar-main"]},
]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@l", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class _Repo(unittest.TestCase):
    def encargo(self, clave: str) -> Recorrido:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        raiz = Path(tmp.name)
        bare = raiz / "repo.git"
        semilla = raiz / "semilla"
        semilla.mkdir()
        (semilla / "base.txt").write_text("base\n")
        subprocess.run(["git", "init", "--bare", "-b", "main", "--quiet", str(bare)], check=True)
        _git(semilla, "init", "-b", "main", "--quiet")
        _git(semilla, "add", "-A")
        _git(semilla, "commit", "--quiet", "-m", "base")
        _git(semilla, "push", "--quiet", str(bare), "main")
        r = Recorrido(
            tmp.name, clave, "integra main en la rama",
            entrega_nivel="pr", repo=str(bare), base_rama="main",
        )
        self.addCleanup(r.reg.close)
        self.assertEqual(_asegurar_repo(r.fold(), r.espacio), "")
        self.bare = bare
        return r

    def declarar_fases(self, r: Recorrido) -> None:
        r.reg.anotar(
            r.enc,
            {"kind": "fases", "data": {"fases": FASES, "hora": ahora_iso()}},
            "fases-despues",
        )


class RamaSinFaseSeRenombra(_Repo):
    def _trabajo_sin_commit_sobre_un_merge(self, r: Recorrido) -> str:
        """El merge de main vive solo en la rama sin fase, y encima un arreglo
        sin commit: justo lo que había en enc_d07becdd91d69ea6."""
        (r.espacio / "integrado.txt").write_text("viene del merge\n")
        (r.espacio / "base.txt").write_text("base del merge\n")
        _git(r.espacio, "add", "-A")
        _git(r.espacio, "commit", "--quiet", "-m", "merge de main")
        merge = _git(r.espacio, "rev-parse", "HEAD")
        (r.espacio / "arreglo.txt").write_text("sin commit\n")
        (r.espacio / "base.txt").write_text("base editada\n")
        return merge

    def test_la_primera_fase_renombra_la_rama_y_conserva_el_trabajo(self):
        r = self.encargo("rama-1")
        sin_fase = r.fold().entrega.rama
        merge = self._trabajo_sin_commit_sobre_un_merge(r)
        self.declarar_fases(r)
        enc = r.fold()
        self.assertEqual(enc.fase_activa, "integrar-main")
        rama = f"{sin_fase}-integrar-main"

        causa = _asegurar_repo(enc, r.espacio)

        self.assertEqual(causa, "")
        self.assertEqual(_git(r.espacio, "rev-parse", "--abbrev-ref", "HEAD"), rama)
        self.assertEqual(_git(r.espacio, "rev-parse", "HEAD"), merge)
        self.assertEqual(_git(r.espacio, "branch", "--list", sin_fase), "")
        self.assertEqual((r.espacio / "arreglo.txt").read_text(), "sin commit\n")
        self.assertEqual((r.espacio / "base.txt").read_text(), "base editada\n")
        self.assertTrue((r.espacio / "integrado.txt").is_file())

    def test_con_el_arbol_limpio_tampoco_pierde_el_merge(self):
        r = self.encargo("rama-2")
        sin_fase = r.fold().entrega.rama
        merge = self._trabajo_sin_commit_sobre_un_merge(r)
        _git(r.espacio, "add", "-A")
        _git(r.espacio, "commit", "--quiet", "-m", "arreglo")
        cabeza = _git(r.espacio, "rev-parse", "HEAD")
        self.declarar_fases(r)

        self.assertEqual(_asegurar_repo(r.fold(), r.espacio), "")

        self.assertEqual(
            _git(r.espacio, "rev-parse", "--abbrev-ref", "HEAD"),
            f"{sin_fase}-integrar-main",
        )
        self.assertEqual(_git(r.espacio, "rev-parse", "HEAD"), cabeza)
        self.assertIn(merge, _git(r.espacio, "rev-list", "HEAD").split())

    def test_otra_rama_no_se_renombra(self):
        r = self.encargo("rama-3")
        _git(r.espacio, "checkout", "--quiet", "-b", "ajena")
        (r.espacio / "ajena.txt").write_text("x\n")
        self.declarar_fases(r)
        enc = r.fold()
        sin_fase = enc.entrega.rama

        _asegurar_repo(enc, r.espacio)

        self.assertEqual(_git(r.espacio, "branch", "--list", "ajena").strip(), "ajena")
        self.assertNotEqual(sin_fase, "ajena")

    def test_una_fase_que_depende_de_otra_no_renombra(self):
        r = self.encargo("rama-4")
        sin_fase = r.fold().entrega.rama
        self.declarar_fases(r)
        enc = r.fold()
        segunda = next(f for f in enc.fases if f.id == "entrega")
        self.assertFalse(
            ciclo._es_rama_sin_fase(enc, segunda, sin_fase, f"{sin_fase}-entrega")
        )


def _esperas(r: Recorrido) -> int:
    return len([e for e in r.reg.eventos(r.enc) if e["kind"] == "espera"])


def _reintentar(r: Recorrido, lanzamientos: list, reloj: list) -> None:
    """Barre con el reloj adelantado hasta que el motor vuelve a esperar por el
    fallo o deja de reintentar: terminar la espera y reintentar son dos barridos."""
    antes = _esperas(r)

    def lanzador(enc, prompt, espacio, sesion):
        lanzamientos.append(enc.rol)
        return None

    for _ in range(4):
        reloj[0] += 400
        desde = time.time() + reloj[0]
        hora = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(desde))
        barrer(r.reg, hora, desde, lanzador)
        if _esperas(r) > antes or r.fold().estado == "necesita_usuario":
            return


class UnFalloDeGitRepetidoPideAyuda(_Repo):
    CAUSA = "no se pudo crear la rama x desde origin/main: error: tu arbol tiene cambios"

    def setUp(self):
        sonda = mock.patch(
            "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
        )
        sonda.start()
        self.addCleanup(sonda.stop)
        self.reloj = [0]
        self.lanzamientos: list[str] = []
        # Dos esperas en el mismo segundo de reloj real comparten identidad; en
        # producción van 5 minutos aparte.
        cuenta = iter(range(1, 1000))
        reanuda = mock.patch.object(
            ciclo,
            "_reanuda_en",
            side_effect=lambda seg: time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + next(cuenta))
            ),
        )
        reanuda.start()
        self.addCleanup(reanuda.stop)

    def reintentar(self, r: Recorrido) -> None:
        _reintentar(r, self.lanzamientos, self.reloj)

    def test_el_mismo_fallo_tres_veces_deja_de_reintentarse_y_pide_ayuda(self):
        r = self.encargo("rama-5")
        with mock.patch.object(ciclo, "_asegurar_repo", return_value=self.CAUSA):
            for vuelta in (1, 2):
                self.reintentar(r)
                enc = r.fold()
                self.assertEqual(_esperas(r), vuelta)
                self.assertEqual(enc.motivo, self.CAUSA)
                self.assertNotEqual(enc.estado, "necesita_usuario")

            self.reintentar(r)
            enc = r.fold()
            self.assertEqual(enc.estado, "necesita_usuario")
            self.assertIn("3 veces seguidas", enc.motivo)
            self.assertIn(self.CAUSA, enc.motivo)
            self.assertEqual(_esperas(r), 3)

            self.reintentar(r)
            self.reintentar(r)
            self.assertEqual(_esperas(r), 3)
            self.assertEqual(r.fold().estado, "necesita_usuario")
        self.assertEqual(self.lanzamientos, [])

    def test_causas_distintas_no_se_suman(self):
        r = self.encargo("rama-6")
        causas = iter(["causa uno", "causa dos", "causa tres", "causa cuatro"])
        with mock.patch.object(
            ciclo, "_asegurar_repo", side_effect=lambda *a, **k: next(causas)
        ):
            for _ in range(4):
                self.reintentar(r)
        enc = r.fold()
        self.assertNotEqual(enc.estado, "necesita_usuario")
        self.assertEqual(enc.motivo, "causa cuatro")

    def test_el_mismo_fallo_con_detalle_variable_cuenta_como_el_mismo(self):
        r = self.encargo("rama-10")
        causas = iter(
            [
                "no se pudo crear la rama x desde origin/main: error: lock "
                "/tmp/clon-1a2b3c/.git/index.lock (pid 4411) a1b2c3d4e5f60718",
                "no se pudo crear la rama x desde origin/main: error: lock "
                "/tmp/clon-9f8e7d/.git/index.lock (pid 5920) 0f9e8d7c6b5a4938",
                "no se pudo crear la rama x desde origin/main: error: lock "
                "/tmp/clon-5c4d3e/.git/index.lock (pid 6107) 1122334455667788",
            ]
        )
        with mock.patch.object(
            ciclo, "_asegurar_repo", side_effect=lambda *a, **k: next(causas)
        ):
            for _ in range(3):
                self.reintentar(r)
        enc = r.fold()
        self.assertEqual(enc.estado, "necesita_usuario")
        self.assertIn("3 veces seguidas", enc.motivo)
        self.assertIn("1122334455667788", enc.motivo)

    def test_otra_operacion_que_falla_no_suma_a_la_anterior(self):
        r = self.encargo("rama-11")
        causas = iter(
            [
                "no se pudo crear la rama x desde origin/main: error uno",
                "no se pudo clonar gon0801/repo: error dos",
                "no se pudo crear la rama x desde origin/main: error tres",
            ]
        )
        with mock.patch.object(
            ciclo, "_asegurar_repo", side_effect=lambda *a, **k: next(causas)
        ):
            for _ in range(3):
                self.reintentar(r)
        self.assertNotEqual(r.fold().estado, "necesita_usuario")

    def test_un_ajuste_del_propietario_vuelve_a_intentarlo(self):
        r = self.encargo("rama-7")
        with mock.patch.object(ciclo, "_asegurar_repo", return_value=self.CAUSA):
            for _ in range(3):
                self.reintentar(r)
        self.assertEqual(r.fold().estado, "necesita_usuario")

        ciclo.corregir(r.reg, r.enc, "Ya deje el repo en orden.", "aj-1", ahora_iso())
        self.assertEqual(r.fold().estado, "recibido")
        with mock.patch.object(ciclo, "_asegurar_repo", return_value=""):
            self.reintentar(r)
        # El repo ya no es la causa: el reintento pasó de ahí (lo siguiente que
        # falta en este entorno de prueba es el paquete de poteto-mode).
        enc = r.fold()
        self.assertNotIn(self.CAUSA, enc.motivo)
        self.assertNotEqual(enc.estado, "necesita_usuario")

    def _escalado(self, clave: str) -> Recorrido:
        r = self.encargo(clave)
        with mock.patch.object(ciclo, "_asegurar_repo", return_value=self.CAUSA):
            for _ in range(3):
                self.reintentar(r)
        self.assertEqual(r.fold().estado, "necesita_usuario")
        return r

    def _el_reintento_pasa_del_repo(self, r: Recorrido) -> None:
        with mock.patch.object(ciclo, "_asegurar_repo", return_value=""):
            self.reintentar(r)
        enc = r.fold()
        self.assertNotIn(self.CAUSA, enc.motivo)
        self.assertNotEqual(enc.estado, "necesita_usuario")

    def test_la_pregunta_nombra_solo_salidas_que_el_motor_acepta(self):
        r = self._escalado("rama-8")
        motivo = r.fold().motivo
        self.assertIn("director corregir", motivo)
        self.assertIn("director parar", motivo)
        self.assertIn("director continuar", motivo)
        self.assertNotIn("usa continuar", motivo)
        self.assertNotIn("responde", motivo)

        rechazo = ciclo.ordenar(r.reg, r.enc, "continuar", "c-0", ahora_iso())
        self.assertTrue(rechazo.get("rechazada"), rechazo)
        self.assertEqual(r.fold().estado, "necesita_usuario")
        self.assertEqual(r.fold().solicitudes, ())

    def test_parar_y_continuar_devuelven_el_trabajo_y_reintentan(self):
        r = self._escalado("rama-9")

        ciclo.ordenar(r.reg, r.enc, "parar", "p-1", ahora_iso())
        self._barrer_una_vez(r)
        self.assertEqual(r.fold().estado, "detenido")
        ciclo.ordenar(r.reg, r.enc, "continuar", "c-1", ahora_iso())
        self.assertEqual(r.fold().estado, "recibido")

        self._el_reintento_pasa_del_repo(r)

    def _barrer_una_vez(self, r: Recorrido) -> None:
        self.reloj[0] += 400
        desde = time.time() + self.reloj[0]
        hora = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(desde))
        barrer(r.reg, hora, desde, lambda *a: None)


if __name__ == "__main__":
    unittest.main()
