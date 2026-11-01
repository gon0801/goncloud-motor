"""Traba 2 del 30 y 31 de octubre: el revisor pide evidencia de CI (Linux real,
bwrap, WSL) que el motor no podía conseguir y el operador armaba a mano: commit
temporal con índice aparte, rama `ci-prueba/<algo>`, PR borrador, esperar el job y
bajar el log. La capacidad `ci.correr` lo hace el puente, con el `gh` y el git
que el supervisor ya tiene. Aquí `gh` es un guion falso y el remoto es un repo
bare real."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import capacidades as cap
from motor.encargos import necesidades as nec
from motor.encargos.dominio import Solicitud
from tests.test_respuestas_buzon import _Flujo

LOG = (
    "jaula-linux\tPrueba\t2026-10-31T12:00:00Z \x1b[32mok\x1b[0m test_jaula_linux\r\n"
    "jaula-linux\tPrueba\t2026-10-31T12:00:01Z Ran 31 tests in 4.2s\n"
    "jaula-linux\tPrueba\t2026-10-31T12:00:01Z OK (acentos: señal, ñandú)\n"
)

GH_FALSO = r'''#!{python}
import json, os, subprocess, sys
d = os.environ["FAKE_GH_DIR"]
bare = os.environ["FAKE_GH_BARE"]
args = sys.argv[1:]
modo = os.environ.get("FAKE_GH_MODO", "")
ruta = os.path.join(d, "estado.json")
estado = json.load(open(ruta)) if os.path.exists(ruta) else {"llamadas": []}
estado["llamadas"].append(args)
def val(flag):
    return args[args.index(flag) + 1]
def guardar():
    json.dump(estado, open(ruta, "w"))
def git(*a):
    return subprocess.run(["git", "--git-dir", bare, *a], capture_output=True, text=True).stdout
if modo == "red":
    guardar()
    sys.stderr.write("error connecting to api.github.com\n")
    sys.exit(1)
if args[:2] == ["pr", "create"]:
    rama = val("--head")
    estado.update(rama=rama, pr_abierto=True, borrador="--draft" in args, base=val("--base"))
    estado["arbol"] = git("ls-tree", "-r", "--name-only", rama).split()
    estado["base_txt"] = git("show", rama + ":base.txt")
    estado["commit"] = git("rev-parse", rama).strip()
    guardar()
    print("https://github.com/o/r/pull/7")
elif args[:2] == ["run", "list"]:
    guardar()
    print(json.dumps([{{"databaseId": 1234, "status": "completed", "conclusion": "success",
        "headSha": val("--commit"), "event": "pull_request",
        "url": "https://github.com/o/r/actions/runs/1234", "workflowName": "Pruebas"}}]))
elif args[:2] == ["run", "view"] and "--log" not in args:
    guardar()
    print(json.dumps({{"status": "completed", "conclusion": "success",
        "url": "https://github.com/o/r/actions/runs/1234",
        "jobs": [{{"name": "jaula-linux", "databaseId": 99, "conclusion": "success"}},
                 {{"name": "otro", "databaseId": 100, "conclusion": "success"}}]}}))
elif args[:2] == ["run", "view"]:
    guardar()
    sys.stdout.write(open(os.path.join(d, "log-" + val("--job") + ".txt"), newline="").read())
elif args[:2] == ["pr", "close"]:
    estado["pr_abierto"] = False
    if "--delete-branch" in args:
        subprocess.run(["git", "--git-dir", bare, "update-ref", "-d", "refs/heads/" + estado["rama"]])
    guardar()
else:
    guardar()
    sys.exit(2)
'''


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@l", *args],
        check=True, capture_output=True, text=True,
    ).stdout


class _Ci(unittest.TestCase):
    def setUp(self):
        self._montar_ci()

    def _montar_ci(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.bare = self.raiz / "repo.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", "--quiet", str(self.bare)], check=True)
        semilla = self.raiz / "semilla"
        semilla.mkdir()
        (semilla / "base.txt").write_text("base\n")
        _git(semilla, "init", "-b", "main", "--quiet")
        _git(semilla, "add", "-A")
        _git(semilla, "commit", "--quiet", "-m", "base")
        _git(semilla, "push", "--quiet", str(self.bare), "main")
        self.espacio = self.raiz / "base" / "espacio" / "enc_x"
        subprocess.run(["git", "clone", "--quiet", str(self.bare), str(self.espacio)], check=True)
        (self.espacio / "base.txt").write_text("base editada sin commit\n")
        (self.espacio / "nuevo.txt").write_text("archivo nuevo\n")
        (self.espacio / ".interno").mkdir()
        (self.espacio / ".interno" / "contexto.md").write_text("interno\n")
        self.destino = self.raiz / "destino"

        self.fake = self.raiz / "fake"
        self.fake.mkdir()
        (self.fake / "log-99.txt").write_bytes(LOG.encode("utf-8"))
        (self.fake / "log-100.txt").write_text("otro job\n")
        bin_ = self.raiz / "bin"
        bin_.mkdir()
        gh = bin_ / "gh"
        gh.write_text(GH_FALSO.replace("{{", "{").replace("}}", "}").replace("#!{python}", "#!" + sys.executable))
        gh.chmod(0o755)
        self.bin = bin_
        entorno = mock.patch.dict(
            os.environ,
            {
                "PATH": str(bin_) + os.pathsep + os.environ.get("PATH", ""),
                "FAKE_GH_DIR": str(self.fake),
                "FAKE_GH_BARE": str(self.bare),
                "FAKE_GH_MODO": "",
            },
        )
        entorno.start()
        self.addCleanup(entorno.stop)
        for nombre in ("CI_SONDEO_S",):
            parche = mock.patch.object(cap, nombre, 0.01)
            parche.start()
            self.addCleanup(parche.stop)
        self.recursos = {
            "ci-linux": {
                "tipo": "ci",
                "autorizacion": "",
                "repo": "o/r",
                "workflow": "pruebas.yml",
                "base": "main",
            }
        }

    def pedir(self, job: str = "jaula-linux", recursos: dict | None = None) -> dict:
        return cap.ejecutar(
            recursos or self.recursos, cap.CI, "ci-linux", {"job": job},
            self.destino, espacio=self.espacio,
        )

    def estado_gh(self) -> dict:
        return json.loads((self.fake / "estado.json").read_text())

    def ramas_remotas(self) -> list[str]:
        salida = subprocess.run(
            ["git", "--git-dir", str(self.bare), "for-each-ref", "--format=%(refname)", "refs/heads/ci-prueba"],
            capture_output=True, text=True,
        ).stdout
        return salida.split()


class CorreElCiSobreLaVersionVigente(_Ci):
    def test_devuelve_el_log_literal_con_el_sha_y_el_numero_de_run(self):
        r = self.pedir()

        self.assertEqual(r["estado"], "ok", r)
        datos = r["datos"]
        gh = self.estado_gh()
        self.assertEqual(datos["sha"], gh["commit"])
        self.assertEqual(datos["run"], "1234")
        self.assertEqual(datos["job"], "jaula-linux")
        self.assertEqual(datos["conclusion"], "success")
        self.assertEqual(Path(datos["log"]).read_bytes(), LOG.encode("utf-8"))
        self.assertEqual(datos["log_nombre"], f"jaula-linux-{datos['sha'][:7]}-run1234.log")
        self.assertEqual(r["evidencia"]["run"], "1234")
        self.assertEqual(r["evidencia"]["sha"], datos["sha"])
        self.assertTrue(gh["borrador"])
        self.assertEqual(gh["base"], "main")

    def test_el_commit_lleva_el_trabajo_sin_commit_y_deja_fuera_lo_interno(self):
        self.pedir()

        gh = self.estado_gh()
        self.assertIn("nuevo.txt", gh["arbol"])
        self.assertEqual(gh["base_txt"], "base editada sin commit\n")
        self.assertFalse([x for x in gh["arbol"] if ".interno" in x])

    def test_el_espacio_queda_como_estaba(self):
        antes = (
            _git(self.espacio, "status", "--porcelain"),
            _git(self.espacio, "rev-parse", "HEAD"),
            _git(self.espacio, "branch", "--list"),
            (self.espacio / "base.txt").read_text(),
        )

        self.pedir()

        despues = (
            _git(self.espacio, "status", "--porcelain"),
            _git(self.espacio, "rev-parse", "HEAD"),
            _git(self.espacio, "branch", "--list"),
            (self.espacio / "base.txt").read_text(),
        )
        self.assertEqual(antes, despues)

    def test_limpia_la_rama_y_el_pr_temporales(self):
        r = self.pedir()

        self.assertEqual(self.ramas_remotas(), [])
        self.assertFalse(self.estado_gh()["pr_abierto"])
        self.assertEqual(
            {k: r["datos"]["limpieza"][k] for k in ("pr_cerrado", "rama_borrada")},
            {"pr_cerrado": True, "rama_borrada": True},
        )

    def test_borra_las_ramas_que_dejo_un_run_anterior_muerto(self):
        _git(self.espacio, "push", "--quiet", str(self.bare), "HEAD:refs/heads/ci-prueba/enc_x-abc1234-1")
        self.assertEqual(len(self.ramas_remotas()), 1)

        self.pedir()

        self.assertEqual(self.ramas_remotas(), [])


class CausaClaraSiFalta(_Ci):
    def test_sin_gh_dice_que_gh_no_esta(self):
        (self.bin / "gh").unlink()
        sin_gh = self.raiz / "sin-gh"
        sin_gh.mkdir()
        git = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
        (sin_gh / "git").symlink_to(git)
        with mock.patch.dict(os.environ, {"PATH": str(sin_gh)}):
            r = self.pedir()

        self.assertEqual(r["estado"], "error")
        self.assertIn("gh no está instalado", r["motivo"])
        self.assertTrue(r["alternativa"])
        self.assertEqual(self.ramas_remotas(), [])

    def test_sin_red_en_gh_dice_sin_red_y_no_deja_la_rama(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_MODO": "red"}):
            r = self.pedir()

        self.assertEqual(r["estado"], "error")
        self.assertIn("sin red hacia GitHub", r["motivo"])
        self.assertIn("error connecting", r["motivo"])
        self.assertEqual(self.ramas_remotas(), [])

    def test_sin_red_al_subir_dice_sin_red(self):
        recursos = {"ci-linux": {**self.recursos["ci-linux"], "remoto": "http://127.0.0.1:9/x.git"}}

        r = self.pedir(recursos=recursos)

        self.assertEqual(r["estado"], "error")
        self.assertIn("sin red hacia GitHub", r["motivo"])
        self.assertFalse((self.fake / "estado.json").exists())

    def test_un_job_que_no_existe_lista_los_que_hay(self):
        r = self.pedir(job="no-existe")

        self.assertEqual(r["estado"], "error")
        self.assertIn("jaula-linux", r["motivo"])
        self.assertIn("otro", r["motivo"])
        self.assertEqual(self.ramas_remotas(), [])
        self.assertFalse(self.estado_gh()["pr_abierto"])

    def test_sin_el_parametro_job_dice_cual_falta(self):
        r = cap.ejecutar(self.recursos, cap.CI, "ci-linux", {}, self.destino, espacio=self.espacio)

        self.assertEqual(r["estado"], "error")
        self.assertIn("parámetro job", r["motivo"])


class ElPuenteLaConoce(_Ci):
    def test_el_catalogo_la_ofrece_y_no_es_observable(self):
        recursos = {"ci-linux": {**self.recursos["ci-linux"]}}
        self.assertEqual(cap.operaciones_de(recursos, "ci-linux"), (cap.CI,))
        self.assertEqual(cap.recursos_observables(recursos), ())
        self.assertEqual(
            cap.huella_recurso(recursos, cap.CI, "ci-linux", {"job": "jaula-linux"}), ""
        )

    def test_el_host_la_concede_con_tipo_ci(self):
        base = self.raiz / "host"
        base.mkdir()
        (base / "capacidades.json").write_text(
            json.dumps({"recursos": {"ci-linux": {"tipo": "ci", "repo": "o/r"}}})
        )
        self.assertEqual(cap.cargar(base)["ci-linux"]["tipo"], "ci")

    def test_el_bloque_de_capacidades_explica_como_pedirla(self):
        texto = nec.bloque_capacidades(self.raiz, self.recursos)
        self.assertIn("ci.correr --recurso <r> --parametro job=", texto)
        self.assertIn("ci-linux (ci): ci.correr", texto)

    def test_una_peticion_ci_correr_es_una_solicitud_valida(self):
        enc = SimpleNamespace(id="enc_x")
        sol = nec._solicitud_de(
            enc,
            {
                "id": "ci-1", "encargo": "enc_x", "operacion": "ci.correr",
                "recurso": "ci-linux", "fase": "f", "intento": "i", "generacion": 1,
                "conductor": "", "parametros": {"job": "jaula-linux"},
            },
        )
        self.assertEqual(sol.operacion, cap.CI)

    def test_el_supervisor_publica_el_log_completo_en_el_espacio(self):
        r = self.pedir()
        espacio_trabajador = self.raiz / "trabajador"
        espacio_trabajador.mkdir()
        directorio = self.raiz / "ejecucion"
        sol = Solicitud(
            id="ci-1", operacion=cap.CI, recurso="ci-linux", fase="f", intento="i",
            generacion=1, conductor="",
        )

        publicado = nec._publicar_snapshot(espacio_trabajador, sol, r, directorio)

        copia = Path(publicado["datos"]["log"])
        self.assertTrue(copia.is_relative_to(espacio_trabajador))
        self.assertEqual(copia.read_bytes(), LOG.encode("utf-8"))
        self.assertEqual(publicado["datos"]["log_host"], r["datos"]["log_host"])


class ElRevisorLaPideYLaRecibe(_Flujo):
    def setUp(self):
        _Ci._montar_ci(self)

    def test_el_pedido_del_revisor_acaba_en_el_log_literal_dentro_del_espacio(self):
        r = self.recorrido("ci-e2e")
        for args in (
            ["init", "-q", "-b", "main"],
            ["remote", "add", "origin", str(self.bare)],
            ["fetch", "-q", "origin"],
            ["reset", "-q", "origin/main"],
        ):
            _git(r.espacio, *args)
        (r.base / "capacidades.json").write_text(json.dumps({"recursos": self.recursos}))

        def como_la_herramienta(registro, enc, sol, recursos, espacio, directorio, autorizado, hora):
            destino = nec._destino_host(directorio, sol.id)
            resultado = cap.ejecutar(
                recursos, sol.operacion, sol.recurso, dict(sol.parametros), destino,
                autorizado, espacio=espacio,
            )
            return nec._completar_entrega(espacio, sol, resultado, directorio)

        with mock.patch.object(nec, "_ejecutar", side_effect=como_la_herramienta):
            self.abrir_turno(r)
            r.juicio(
                {
                    "veredicto": "no_concluyente",
                    "evidencia": "falta el log del CI de Linux",
                    "inspeccion_faltante": "el log del job jaula-linux",
                    "inspeccion": {
                        "operacion": "ci.correr",
                        "recurso": "ci-linux",
                        "parametros": {"job": "jaula-linux"},
                    },
                }
            )
            r.incorporar()
            self.cerrar_turno(r)
            ident = r.fold().fases[0].inspeccion_solicitud
            self.barrer(r)

        sol = next(s for s in r.fold().solicitudes if s.id == ident)
        self.assertEqual(sol.operacion, cap.CI)
        self.assertEqual(sol.estado, "resuelta")
        doc = self.buzon(r, ident)
        publico = Path(doc["datos"]["log"])
        self.assertTrue(publico.is_relative_to(r.espacio))
        self.assertEqual(publico.read_bytes(), LOG.encode("utf-8"))
        self.assertEqual(doc["datos"]["run"], "1234")
        self.assertEqual(self.ramas_remotas(), [])
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])

    def ramas_remotas(self):
        return _Ci.ramas_remotas(self)


class UnRunLargoNoSeMata(unittest.TestCase):
    def test_pasado_el_plazo_la_herramienta_de_ci_sigue_y_queda_pendiente(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = Path(tmp)
            sol = Solicitud(
                id="ci-1", operacion=cap.CI, recurso="ci-linux", fase="f", intento="i",
                generacion=1, conductor="", parametros=(("job", "jaula-linux"),),
            )
            registro = SimpleNamespace(base=raiz)
            enc = SimpleNamespace(id="enc_x")
            lenta = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            self.addCleanup(lenta.kill)
            with mock.patch.object(nec, "TIMEOUT_S", 0.3), mock.patch.object(
                nec, "_detenida_ahora", return_value=False
            ), mock.patch.object(nec.subprocess, "Popen", return_value=lenta), mock.patch(
                "motor.plataforma.hora_inicio", return_value="t0"
            ):
                resultado = nec._ejecutar(
                    registro, enc, sol, {"ci-linux": {"tipo": "ci"}},
                    raiz / "espacio", raiz / "ejecucion", False, "2026-10-31T12:00:00Z",
                )

            self.assertEqual(resultado["estado"], "pendiente")
            self.assertIsNone(lenta.poll(), "la herramienta de CI debe seguir viva")
            marca = nec._marca_herramienta(raiz / "ejecucion", "ci-1")
            self.assertTrue(marca.is_file())

    def test_otra_operacion_que_pasa_del_plazo_si_se_mata(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = Path(tmp)
            sol = Solicitud(
                id="r-1", operacion=cap.REMOTO, recurso="x", fase="f", intento="i",
                generacion=1, conductor="", parametros=(("ruta", "a.txt"),),
            )
            registro = SimpleNamespace(base=raiz)
            lenta = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            self.addCleanup(lenta.kill)
            with mock.patch.object(nec, "TIMEOUT_S", 0.3), mock.patch.object(
                nec, "_detenida_ahora", return_value=False
            ), mock.patch.object(nec.subprocess, "Popen", return_value=lenta), mock.patch(
                "motor.plataforma.hora_inicio", return_value="t0"
            ):
                resultado = nec._ejecutar(
                    registro, SimpleNamespace(id="enc_x"), sol, {"x": {"tipo": "remoto"}},
                    raiz / "espacio", raiz / "ejecucion", False, "2026-10-31T12:00:00Z",
                )
            self.assertEqual(resultado["estado"], "error")
            self.assertIn("excedió", resultado["motivo"])


if __name__ == "__main__":
    unittest.main()
