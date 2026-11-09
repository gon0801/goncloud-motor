"""La entrega no se traba: comprueba al recibir y no espera lo permanente.

Los casos vienen de los registros: `enc_3b93139e` esperó 17 veces un push que
la llave rechazaba, `enc_fb735845` chocó con «No commits between» al publicar
con pendientes y `enc_a36123bf` pidió logs de CI que todavía no existían. Cada
prueba nombra la conducta que cambia; las que describen algo que ya estaba se
declaran caracterización.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import capacidades as cap
from motor.encargos import ciclo
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import ahora_iso, barrer, recibir
from motor.encargos.registro import Registro
from motor.github import CapacidadRemota
from tests.test_flujo_revisiones import (
    Recorrido,
    _entrega_pr,
    sonda_sin_aislamiento_del_host,
)
from tests.dobles import github_con_hallazgos
from tests.test_respuestas_buzon import _Flujo


def setUpModule():
    sonda_sin_aislamiento_del_host()


class _Puerto:
    """El puerto solo necesita la mirada de solo lectura para la recepción."""

    def __init__(self, capacidad=None, falla: Exception | None = None):
        self._capacidad = capacidad
        self._falla = falla

    def capacidad(self, repo, base):
        if self._falla is not None:
            raise self._falla
        return self._capacidad


def _bare(tmp: Path) -> Path:
    bare = tmp / "repo.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", "--quiet", str(bare)],
        check=True,
    )
    return bare


def _bare_con_base(tmp: Path) -> Path:
    bare = _bare(tmp)
    semilla = tmp / "semilla"
    semilla.mkdir()
    (semilla / "base.txt").write_text("base\n", encoding="utf-8")
    for args in (
        ["init", "-b", "main", "--quiet"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@l", "commit", "--quiet", "-m", "base"],
        ["push", "--quiet", str(bare), "main"],
    ):
        subprocess.run(["git", "-C", str(semilla), *args], check=True)
    return bare


class ComprobarAlRecibir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)

    def _recibir(self, repo: str, puerto, clave: str = "entrega-1"):
        return recibir(
            self.reg,
            "cambia el saludo",
            clave,
            ahora_iso(),
            entrega_nivel="pr",
            repo=repo,
            base_rama="main",
            gh=puerto,
        )

    def test_gh_ausente_queda_como_impedimento_al_recibir(self):
        repo = _bare(Path(self._tmp.name))
        puerto = _Puerto(
            CapacidadRemota(False, None, None, "gh: command not found")
        )
        salida = self._recibir(str(repo), puerto)
        enc = self.reg.fold(salida["encargo"])

        clases = [i.clase for i in enc.impedimentos]
        self.assertIn("entrega:github", clases)
        causa = next(i.causa for i in enc.impedimentos if i.clase == "entrega:github")
        self.assertIn("command not found", causa)
        # El impedimento de entrega no es la causa principal: no frena el
        # producto. El pendiente se decide al publicar, cuando ya hay trabajo.
        self.assertEqual(enc.impedimento, "")
        self.assertEqual(enc.pendientes_entrega, ())
        self.assertEqual(
            [e for e in self.reg.eventos(enc.id) if e["kind"] == "espera"], []
        )

    def test_gh_sin_autenticar_queda_como_impedimento_al_recibir(self):
        repo = _bare(Path(self._tmp.name))
        puerto = _Puerto(
            CapacidadRemota(
                False,
                None,
                None,
                "repo no legible: To get started with GitHub CLI, please run: "
                " gh auth login",
            )
        )
        salida = self._recibir(str(repo), puerto, clave="entrega-gh-login")
        enc = self.reg.fold(salida["encargo"])

        clases = [i.clase for i in enc.impedimentos]
        self.assertIn("entrega:github", clases)
        causa = next(i.causa for i in enc.impedimentos if i.clase == "entrega:github")
        self.assertIn("gh auth login", causa)
        self.assertEqual(enc.impedimento, "")
        self.assertEqual(enc.pendientes_entrega, ())

    def test_push_rechazado_por_llave_queda_como_impedimento_al_recibir(self):
        ssh = Path(self._tmp.name) / "ssh-falso"
        ssh.write_text(
            "#!/bin/sh\n"
            "echo 'git@github.com: Permission denied (publickey).' >&2\n"
            "exit 255\n",
            encoding="utf-8",
        )
        ssh.chmod(0o755)
        puerto = _Puerto(CapacidadRemota(True, True, False, "sin reglas fijas"))
        with mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": str(ssh)}):
            salida = self._recibir(
                "git@github.com:gon0801/goncloud-motor.git",
                puerto,
                clave="entrega-ssh",
            )
        enc = self.reg.fold(salida["encargo"])

        clases = [i.clase for i in enc.impedimentos]
        self.assertIn("entrega:ssh", clases)
        ssh_causa = next(
            i.causa for i in enc.impedimentos if i.clase == "entrega:ssh"
        )
        self.assertIn("publickey", ssh_causa)
        self.assertEqual(enc.pendientes_entrega, ())
        self.assertEqual(
            [e for e in self.reg.eventos(enc.id) if e["kind"] == "espera"], []
        )

    def test_el_preflight_usa_el_remoto_configurado_sin_forzar_github(self):
        tmp = Path(self._tmp.name)
        local = tmp / "repo-local"
        local.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main", str(local)], check=True
        )
        remoto = "https://example.test/grupo/proyecto.git"
        subprocess.run(
            ["git", "-C", str(local), "remote", "add", "origin", remoto],
            check=True,
        )
        registro = tmp / "git-remoto.txt"
        real = shutil.which("git")
        assert real is not None
        falso = tmp / "git-falso"
        falso.mkdir()
        git = falso / "git"
        git.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"ls-remote\" ]; then\n"
            f"  echo \"$@\" >> '{registro}'\n"
            "  echo \"fatal: could not read Username for 'https://example.test': "
            "terminal prompts disabled\" >&2\n"
            "  exit 1\n"
            "fi\n"
            f"exec '{real}' \"$@\"\n",
            encoding="utf-8",
        )
        git.chmod(0o755)
        puerto = _Puerto(CapacidadRemota(True, True, False, "sin reglas fijas"))
        with mock.patch.dict(
            os.environ, {"PATH": f"{falso}{os.pathsep}{os.environ.get('PATH', '')}"}
        ):
            salida = self._recibir(str(local), puerto, clave="entrega-remoto")
        enc = self.reg.fold(salida["encargo"])

        self.assertIn("entrega:ssh", [i.clase for i in enc.impedimentos])
        causa = next(i.causa for i in enc.impedimentos if i.clase == "entrega:ssh")
        self.assertIn("example.test", causa)
        usado = registro.read_text(encoding="utf-8")
        self.assertIn(remoto, usado)
        self.assertNotIn("github.com", usado)

    def test_un_impedimento_de_entrega_no_es_la_causa_principal(self):
        repo = _bare(Path(self._tmp.name))
        puerto = _Puerto(CapacidadRemota(False, None, None, "gh: command not found"))
        salida = self._recibir(str(repo), puerto, clave="entrega-principal")
        enc = self.reg.fold(salida["encargo"])
        self.assertIn("entrega:github", [i.clase for i in enc.impedimentos])

        ciclo._anotar_impedimento(
            self.reg,
            enc,
            "github",
            "la herramienta fallo",
            "reintentar la herramienta",
            ahora_iso(),
        )
        enc = self.reg.fold(salida["encargo"])
        self.assertEqual(enc.impedimento, "github")
        self.assertEqual(enc.impedimento_causa, "la herramienta fallo")
        self.assertIn("entrega:github", [i.clase for i in enc.impedimentos])

    def test_una_falla_transitoria_no_deja_impedimento(self):  # no-roja-en-base: caracterización de que una caída de red no cambia el nivel
        repo = _bare(Path(self._tmp.name))
        puerto = _Puerto(falla=RuntimeError("connection reset by peer"))
        salida = self._recibir(str(repo), puerto, clave="entrega-2")
        enc = self.reg.fold(salida["encargo"])

        self.assertEqual(enc.impedimentos, ())
        self.assertEqual(enc.pendientes_entrega, ())

    def test_un_timeout_ssh_al_recibir_no_es_impedimento(self):  # no-roja-en-base: caracterización de que la base tampoco marcaba la red como impedimento
        ssh = Path(self._tmp.name) / "ssh-red"
        ssh.write_text(
            "#!/bin/sh\n"
            "echo 'ssh: connect to host github.com port 22: Operation timed out' >&2\n"
            "echo 'fatal: Could not read from remote repository.' >&2\n"
            "exit 255\n",
            encoding="utf-8",
        )
        ssh.chmod(0o755)
        puerto = _Puerto(CapacidadRemota(True, True, False, "sin reglas fijas"))
        with mock.patch.dict(os.environ, {"GIT_SSH_COMMAND": str(ssh)}):
            salida = self._recibir(
                "git@github.com:gon0801/goncloud-motor.git",
                puerto,
                clave="entrega-red",
            )
        enc = self.reg.fold(salida["encargo"])

        # «Could not read from remote repository» también aparece con la red
        # caída: no puede clasificar la causa como permanente.
        self.assertEqual(enc.impedimentos, ())
        self.assertEqual(enc.pendientes_entrega, ())


class NoCierraSinTrabajo(unittest.TestCase):
    def test_una_falla_permanente_al_recibir_lanza_el_turno(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            sonda = mock.patch(
                "motor.encargos.aislamiento.sondear",
                return_value=SondaEntorno(True),
            )
            sonda.start()
            self.addCleanup(sonda.stop)
            poteto = mock.patch(
                "motor.flota.runtime.exigir_poteto",
                return_value=(Path(tmp) / "poteto.md", "digest"),
            )
            poteto.start()
            self.addCleanup(poteto.stop)
            bare = _bare_con_base(Path(tmp))
            puerto = _Puerto(
                CapacidadRemota(False, None, None, "gh: command not found")
            )
            r = Recorrido(
                tmp,
                "sin-gh",
                "Crea dato.txt con 42",
                entrega_nivel="pr",
                repo=str(bare),
                base_rama="main",
                gh=puerto,
            )
            self.addCleanup(r.reg.close)
            lanzados: list[str] = []

            def lanzador(enc, prompt, espacio, sesion):
                lanzados.append(enc.id)
                return None

            for _ in range(3):
                barrer(r.reg, ahora_iso(), time.time(), lanzador)

            enc = r.fold()
            self.assertTrue(lanzados, "el agente no se lanzó")
            self.assertNotEqual(enc.estado, "terminado")
            self.assertEqual(r.eventos("bloqueo_fase"), [])
            self.assertTrue(enc.impedimentos)
            self.assertIn("entrega:github", [i.clase for i in enc.impedimentos])


class FallaPermanenteDeEntrega(unittest.TestCase):
    def test_el_push_con_publickey_no_espera_y_deja_pendiente(self):
        causa = (
            "git push fallo: git@github.com: Permission denied (publickey).\n"
            "fatal: Could not read from remote repository."
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "push-publickey", github_con_hallazgos
            )
            with mock.patch.object(ciclo, "_commit_push", return_value=("", causa)):
                enc = vuelta("hola")

            self.assertEqual(
                [e for e in r.reg.eventos(r.enc) if e["kind"] == "espera"], []
            )
            self.assertTrue(
                any(i.clase == "entrega:publicar" for i in enc.impedimentos)
            )
            self.assertTrue(
                any("permission denied" in p.lower() for p in enc.pendientes_entrega)
            )

    def test_un_push_por_red_sigue_esperando_y_no_deja_pendiente(self):  # no-roja-en-base: caracterización de que la base ya esperaba una caída de red
        causa = (
            "git push fallo: ssh: connect to host github.com port 22: "
            "Operation timed out\n"
            "fatal: Could not read from remote repository."
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "push-red", github_con_hallazgos)
            with mock.patch.object(ciclo, "_commit_push", return_value=("", causa)):
                enc = vuelta("hola")

            self.assertTrue(
                [e for e in r.reg.eventos(r.enc) if e["kind"] == "espera"]
            )
            self.assertFalse(
                any(i.clase == "entrega:publicar" for i in enc.impedimentos)
            )
            self.assertFalse(
                any("permanente" in p.lower() for p in enc.pendientes_entrega)
            )

    def test_un_impedimento_de_entrega_se_resuelve_al_publicar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "resuelve-entrega", github_con_hallazgos
            )
            for clase in ("entrega:github", "entrega:ssh", "entrega:publicar"):
                ciclo._anotar_impedimento(
                    r.reg,
                    r.fold(),
                    clase,
                    f"{clase}: prueba",
                    "reintentar la operación",
                    ahora_iso(),
                )
            enc = vuelta("hola")

            resueltos = {
                ev["data"]["clase"]
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "impedimento_resuelto"
            }
            self.assertEqual(
                resueltos,
                {"entrega:github", "entrega:ssh", "entrega:publicar"},
            )
            self.assertEqual(
                [
                    i.clase
                    for i in enc.impedimentos
                    if i.clase.startswith("entrega:")
                ],
                [],
            )

    def test_no_commits_between_termina_en_sin_cambios(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "sin-commits", github_con_hallazgos
            )
            with mock.patch.object(
                vuelta.gh,
                "abrir_pr",
                side_effect=RuntimeError(
                    "No commits between main and encargo/enc_x"
                ),
            ):
                vuelta("hola")

            pasos = [
                ev["data"].get("paso")
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "entrega_externa"
            ]
            self.assertIn("sin_cambios", pasos)
            self.assertEqual(
                [e for e in r.reg.eventos(r.enc) if e["kind"] == "espera"], []
            )


class LogDeCiEsperaLoQuePide(unittest.TestCase):
    SHA = "abc1234"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name) / "ci"
        self.raiz.mkdir()
        self.destino = Path(self._tmp.name) / "destino"
        self.recursos = {"ci-github": {"tipo": "remoto", "raiz": str(self.raiz)}}

    def _leer(self, ruta: str, contexto: dict | None = None) -> dict:
        return cap.ejecutar(
            self.recursos,
            cap.REMOTO,
            "ci-github",
            {"ruta": ruta},
            self.destino,
            contexto=contexto,
        )

    def test_un_job_de_la_matriz_conserva_su_numero_de_parte(self):
        (self.raiz / f"bateria-parte-1-{self.SHA}-unittest.txt").write_text(
            "parte 1\n", encoding="utf-8"
        )
        resultado = self._leer(
            f"bateria-parte-3-{self.SHA}-unittest.txt",
            {"pr": 7, "sha": self.SHA},
        )
        self.assertEqual(resultado["estado"], "error")
        self.assertEqual(resultado["datos"]["jobs"], ["bateria-parte-1"])

    def test_sin_pr_todavia_es_pendiente_y_luego_llega(self):
        ruta = f"bateria-{self.SHA}-unittest.txt"
        pendiente = self._leer(ruta, {"pr": 0, "sha": ""})
        self.assertEqual(pendiente["estado"], "pendiente")

        (self.raiz / ruta).write_text("todo ok\n", encoding="utf-8")
        leido = self._leer(ruta, {"pr": 0, "sha": ""})
        self.assertEqual(leido["estado"], "ok")
        self.assertEqual(leido["datos"]["texto"], "todo ok\n")

    def test_un_job_mal_nombrado_lista_los_jobs_reales(self):
        (self.raiz / f"jaula-linux-{self.SHA}-unittest.txt").write_text(
            "linux\n", encoding="utf-8"
        )
        resultado = self._leer(
            f"jaula-windows-{self.SHA}-unittest.txt",
            {"pr": 7, "sha": self.SHA},
        )
        self.assertEqual(resultado["estado"], "error")
        self.assertEqual(resultado["datos"]["jobs"], ["jaula-linux"])
        self.assertIn("jobs reales: jaula-linux", resultado["motivo"])

    def test_encuentra_el_mismo_job_en_otro_run_del_mismo_sha(self):
        (self.raiz / f"jaula-linux-{self.SHA}-run2-unittest.txt").write_text(
            "linux run 2\n", encoding="utf-8"
        )
        resultado = self._leer(
            f"jaula-linux-{self.SHA}-run1-unittest.txt",
            {"pr": 7, "sha": self.SHA},
        )
        self.assertEqual(resultado["estado"], "ok")
        self.assertEqual(
            resultado["datos"]["encontrado"],
            f"jaula-linux-{self.SHA}-run2-unittest.txt",
        )
        self.assertEqual(resultado["datos"]["texto"], "linux run 2\n")

    def test_el_sha_del_pr_sin_log_todavia_es_pendiente(self):
        resultado = self._leer(
            f"bateria-{self.SHA}-unittest.txt",
            {"pr": 7, "sha": self.SHA + "ff"},
        )
        self.assertEqual(resultado["estado"], "pendiente")
        self.assertIn(self.SHA, resultado["motivo"])


class LogPedidoAntesDelPrLlegaDespues(_Flujo):
    def test_el_mismo_pedido_espera_y_recibe_el_log_cuando_aparece(self):
        import json as _json

        r = self.recorrido("ci-pendiente", con_recurso=True)
        ruta = "bateria-abc1234-unittest.txt"
        ident = self.pedir(r, ruta=ruta)
        self.barrer(r)
        respuestas = r.espacio / ".interno" / "buzon" / "respuestas"
        pendiente = _json.loads(
            (respuestas / f"{ident}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(pendiente["estado"], "pendiente")

        (self.raiz_ci / ruta).write_text("bateria ok\n", encoding="utf-8")
        self.barrer(r)
        resuelta = _json.loads(
            (respuestas / f"{ident}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(resuelta["estado"], "resuelta", resuelta)
        self.assertEqual(resuelta["datos"]["texto"], "bateria ok\n")


if __name__ == "__main__":
    unittest.main()
