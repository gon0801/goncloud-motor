"""`calidad.py tamano` frena que un archivo o una función de `motor/` crezca.

Cada prueba arma un repo de git temporal con su propio `motor/`, guarda una base
en un commit y corre la herramienta como la corre el CI, contra esa base.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
HERRAMIENTA = RAIZ / "tools" / "calidad.py"
POLITICA = "motor/encargos/politica.py"
FLUJO = RAIZ / ".github" / "workflows" / "quality.yml"
PASO_DE_TAMANO = "      - name: Ningún archivo ni función de motor/ crece por encima de su límite"


def _funcion(nombre: str, lineas: int, sangria: str = "") -> str:
    cuerpo = f"{sangria}    x = 1\n" * (lineas - 1)
    return f"{sangria}def {nombre}():\n{cuerpo}"


def _relleno(lineas: int) -> str:
    return "x = 1\n" * lineas


class Repo:
    def __init__(self, raiz: Path):
        self.raiz = raiz

    @classmethod
    def nuevo(cls, raiz: Path) -> Repo:
        (raiz / "tools").mkdir(parents=True)
        repo = cls(raiz)
        repo.git("init", "-q", "-b", "main")
        shutil.copy(HERRAMIENTA, raiz / "tools" / "calidad.py")
        repo.escribir(POLITICA, (RAIZ / POLITICA).read_text(encoding="utf-8"))
        return repo

    def clonar(self, *opciones: str, nombre: str = "clon") -> Repo:
        destino = self.raiz.parent / nombre
        subprocess.run(
            ["git", "clone", "-q", *opciones, self.raiz.as_uri(), str(destino)],
            capture_output=True,
            check=True,
        )
        return Repo(destino)

    def correr_consejo(self, mensaje: str) -> None:
        consejo = re.search(r"`(git [^`]+)`", mensaje)
        if consejo is None:
            raise AssertionError(f"el mensaje no trae un comando de git: {mensaje}")
        subprocess.run(shlex.split(consejo.group(1)), cwd=self.raiz, check=True)

    def git(self, *args: str) -> str:
        return subprocess.run(
            [
                "git",
                "-c", "user.name=prueba",
                "-c", "user.email=prueba@example.invalid",
                "-c", "commit.gpgsign=false",
                *args,
            ],
            cwd=self.raiz,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def escribir(self, ruta: str, texto: str) -> None:
        destino = self.raiz / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")

    def enlazar(self, ruta: str, destino: str) -> None:
        enlace = self.raiz / ruta
        if enlace.is_dir() and not enlace.is_symlink():
            shutil.rmtree(enlace)
        enlace.unlink(missing_ok=True)
        enlace.parent.mkdir(parents=True, exist_ok=True)
        enlace.symlink_to(destino)

    def commit(self) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        return self.git("rev-parse", "HEAD")

    def tamano(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.raiz / "tools" / "calidad.py"), "tamano", *extra],
            cwd=self.raiz,
            capture_output=True,
            text=True,
            check=False,
        )


def _repo(prueba: unittest.TestCase) -> Repo:
    directorio = tempfile.TemporaryDirectory()
    prueba.addCleanup(directorio.cleanup)
    return Repo.nuevo(Path(directorio.name) / "origen")


class LimiteDeArchivo(unittest.TestCase):
    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir("motor/grande.py", _relleno(1200))
        self.base = self.repo.commit()

    def test_sin_cambios_pasa_y_dice_contra_que_comparo_y_cuantos_pasan(self):
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn(self.base, corrida.stdout)
        self.assertIn("1 archivo(s)", corrida.stdout)
        self.assertIn("0 función(es)", corrida.stdout)

    def test_un_archivo_que_ya_pasaba_y_crece_una_linea_falla(self):
        self.repo.escribir("motor/grande.py", _relleno(1201))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/grande.py: archivo de 1201 líneas", corrida.stderr)
        self.assertIn("mide 1200", corrida.stderr)
        self.assertIn("Saca código", corrida.stderr)

    def test_un_archivo_nuevo_de_1001_lineas_falla(self):
        self.repo.escribir("motor/encargos/nuevo.py", _relleno(1001))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/encargos/nuevo.py: archivo de 1001 líneas", corrida.stderr)
        self.assertIn("no existe", corrida.stderr)

    def test_un_archivo_nuevo_de_1000_lineas_cabe(self):
        self.repo.escribir("motor/nuevo.py", _relleno(1000))
        self.assertEqual(self.repo.tamano("--base", self.base).returncode, 0)

    def test_un_archivo_que_cabia_en_la_base_no_puede_pasar_del_limite(self):
        self.repo.escribir("motor/chico.py", _relleno(900))
        base = self.repo.commit()
        self.repo.escribir("motor/chico.py", _relleno(1001))
        corrida = self.repo.tamano("--base", base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/chico.py: archivo de 1001 líneas", corrida.stderr)
        self.assertIn("mide 900", corrida.stderr)

    def test_renombrar_el_archivo_no_hereda_su_tamano_en_la_base(self):
        (self.repo.raiz / "motor" / "grande.py").rename(
            self.repo.raiz / "motor" / "otro.py"
        )
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/otro.py: archivo de 1200 líneas", corrida.stderr)

    def test_lo_que_se_encoge_no_deja_holgura_para_volver_a_crecer(self):
        self.repo.escribir("motor/grande.py", _relleno(1100))
        self.assertEqual(self.repo.tamano("--base", self.base).returncode, 0)
        nueva_base = self.repo.commit()

        self.repo.escribir("motor/grande.py", _relleno(1200))
        corrida = self.repo.tamano("--base", nueva_base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/grande.py: archivo de 1200 líneas", corrida.stderr)
        self.assertIn("mide 1100", corrida.stderr)

    def test_por_omision_compara_contra_origin_main(self):
        self.repo.git("update-ref", "refs/remotes/origin/main", self.base)
        self.repo.escribir("motor/grande.py", _relleno(1201))
        corrida = self.repo.tamano()
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("en origin/main mide 1200", corrida.stderr)

    def test_sin_la_rama_del_remoto_el_consejo_la_trae(self):
        clon = self.repo.clonar()
        clon.git("update-ref", "-d", "refs/remotes/origin/main")
        corrida = clon.tamano("--base", "origin/main")
        self.assertEqual(corrida.returncode, 1)

        clon.correr_consejo(corrida.stderr)
        corrida = clon.tamano("--base", "origin/main")
        self.assertEqual(corrida.returncode, 0, corrida.stderr)

    def test_en_un_clon_superficial_el_consejo_trae_el_primer_padre(self):
        self.repo.escribir("README", "otro commit\n")
        self.repo.commit()
        clon = self.repo.clonar("--depth", "1")
        corrida = clon.tamano("--base", "HEAD^1")
        self.assertEqual(corrida.returncode, 1)
        self.assertNotIn("git fetch origin HEAD^1", corrida.stderr)

        clon.correr_consejo(corrida.stderr)
        corrida = clon.tamano("--base", "HEAD^1")
        self.assertEqual(corrida.returncode, 0, corrida.stderr)

    def test_una_referencia_que_no_existe_no_sugiere_traerla_del_remoto(self):
        corrida = self.repo.tamano("--base", "HEAD^1")
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("HEAD^1", corrida.stderr)
        self.assertNotIn("git fetch", corrida.stderr)
        self.assertNotIn("Traceback", corrida.stderr)


class BaseDelWorkflow(unittest.TestCase):
    """El paso del job `gate` elige la base según el evento de GitHub."""

    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir("motor/grande.py", _relleno(1200))
        self.antes = self.repo.commit()
        self.repo.escribir("motor/grande.py", _relleno(1201))
        self.repo.commit()
        self.repo.escribir("README", "no toca motor/\n")
        self.repo.commit()

    def _paso(self, contexto: dict[str, str]) -> subprocess.CompletedProcess:
        lineas = FLUJO.read_text(encoding="utf-8").splitlines()
        inicio = lineas.index(PASO_DE_TAMANO)
        fin = next(i for i in range(inicio + 1, len(lineas)) if lineas[i].startswith("      - "))
        paso = lineas[inicio + 1 : fin]
        entorno = dict(os.environ)
        for linea in paso:
            variable = re.fullmatch(r"\s*(\w+): \$\{\{ ([\w.]+) \}\}", linea)
            if variable:
                entorno[variable.group(1)] = contexto.get(variable.group(2), "")
        corre = paso.index("        run: |")
        guion = textwrap.dedent("\n".join(paso[corre + 1 :]))

        binarios = self.repo.raiz.parent / "bin"
        binarios.mkdir(exist_ok=True)
        python = binarios / "python"
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        python.chmod(0o755)
        entorno["PATH"] = f"{binarios}{os.pathsep}{entorno['PATH']}"
        return subprocess.run(
            ["bash", "-e", "-c", guion],
            cwd=self.repo.raiz,
            env=entorno,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_un_push_de_dos_commits_compara_contra_donde_estaba_la_rama(self):
        corrida = self._paso({"github.base_ref": "", "github.event.before": self.antes})
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn("motor/grande.py: archivo de 1201 líneas", corrida.stderr)
        self.assertIn(f"en {self.antes} mide 1200", corrida.stderr)

    def test_un_push_sin_commit_previo_compara_el_ultimo_y_lo_avisa(self):
        for antes in ("0" * 40, "", "f" * 40):
            with self.subTest(antes=antes):
                corrida = self._paso({"github.base_ref": "", "github.event.before": antes})
                self.assertEqual(corrida.returncode, 0, corrida.stderr)
                self.assertIn("::warning", corrida.stdout)
                self.assertIn("HEAD^1", corrida.stdout)

    def test_un_pr_compara_contra_su_rama_base(self):
        self.repo.git("update-ref", "refs/remotes/origin/main", self.antes)
        corrida = self._paso({"github.base_ref": "main", "github.event.before": ""})
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn("en origin/main mide 1200", corrida.stderr)

    def test_no_hay_linea_base_que_actualizar(self):
        corrida = self.repo.tamano("--actualizar")
        self.assertEqual(corrida.returncode, 2)
        self.assertIn("--base", corrida.stderr)
        self.assertFalse((self.repo.raiz / ".github").exists())


class LimiteDeFuncion(unittest.TestCase):
    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir(
            "motor/m.py", _funcion("chica", 10) + _funcion("larga", 150)
        )
        self.base = self.repo.commit()

    def test_la_funcion_que_ya_pasaba_y_no_crece_pasa(self):
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn("1 función(es)", corrida.stdout)

    def test_la_funcion_que_ya_pasaba_y_crece_falla(self):
        self.repo.escribir("motor/m.py", _funcion("chica", 10) + _funcion("larga", 151))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::larga: función de 151 líneas", corrida.stderr)
        self.assertIn("mide 150", corrida.stderr)

    def test_un_metodo_nuevo_de_101_lineas_falla(self):
        self.repo.escribir(
            "motor/m.py",
            _funcion("chica", 10)
            + _funcion("larga", 150)
            + "class Caja:\n"
            + _funcion("metodo", 101, sangria="    "),
        )
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::Caja.metodo: función de 101 líneas", corrida.stderr)

    def test_mover_la_funcion_dentro_del_archivo_conserva_su_tamano_en_la_base(self):
        self.repo.escribir("motor/m.py", _funcion("larga", 150) + _funcion("chica", 10))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn("nada crece", corrida.stdout)

    def test_renombrar_la_funcion_no_hereda_su_tamano_en_la_base(self):
        self.repo.escribir("motor/m.py", _funcion("chica", 10) + _funcion("otra", 150))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::otra: función de 150 líneas", corrida.stderr)

    def test_una_segunda_definicion_con_el_mismo_nombre_no_se_cuela(self):
        self.repo.escribir(
            "motor/m.py",
            _funcion("chica", 10) + _funcion("larga", 150) + _funcion("larga", 20),
        )
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::larga: función de 170 líneas", corrida.stderr)


class CentroCongelado(unittest.TestCase):
    """Decisión 6 de la reconstrucción: ninguna función nueva en `ciclo.py` ni
    en `dominio.py`, por chica que sea."""

    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir("motor/encargos/ciclo.py", _funcion("vieja", 3))
        self.repo.escribir("motor/encargos/dominio.py", "class Fase:\n" + _funcion("a", 3, "    "))
        self.base = self.repo.commit()

    def test_una_funcion_nueva_en_ciclo_o_en_dominio_falla(self):
        for ruta, texto, nombre in (
            ("motor/encargos/ciclo.py", _funcion("vieja", 3) + _funcion("nueva", 3), "nueva"),
            (
                "motor/encargos/dominio.py",
                "class Fase:\n" + _funcion("a", 3, "    ") + _funcion("b", 3, "    "),
                "Fase.b",
            ),
        ):
            with self.subTest(ruta=ruta):
                self.repo.git("checkout", "-q", "--", ".")
                self.repo.escribir(ruta, texto)
                corrida = self.repo.tamano("--base", self.base)
                self.assertEqual(corrida.returncode, 1, corrida.stdout)
                self.assertIn(f"{ruta}::{nombre}: función nueva", corrida.stderr)

    def test_sin_congelamientos_en_la_politica_una_funcion_nueva_en_ciclo_pasa(self):  # no-roja-en-base: el candado del centro nace en este PR; en main no hay candado que frene
        self.repo.escribir(POLITICA, "CONGELAMIENTOS = ()\n")
        self.repo.escribir("motor/encargos/ciclo.py", _funcion("vieja", 3) + _funcion("nueva", 3))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)

    def test_una_funcion_nueva_en_otro_modulo_y_quitar_una_de_ciclo_pasan(self):  # no-roja-en-base: fija que el candado del centro no frena lo que no es una función nueva en él
        self.repo.escribir("motor/encargos/ciclo.py", "x = 1\n")
        self.repo.escribir("motor/encargos/revisor.py", _funcion("nueva", 3))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)


GRANDE = (
    _relleno(1100)
    + "\n"
    + "# nota que explica algo\n"
    + _funcion("chica", 5)
    + _relleno(100)
)


class CantidadDeCodigo(unittest.TestCase):
    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir("motor/grande.py", GRANDE)
        self.base = self.repo.commit()

    def _sin_crecer_en_lineas_falla_por_tokens(self, texto: str) -> None:
        self.assertEqual(texto.count("\n"), GRANDE.count("\n"))
        self.repo.escribir("motor/grande.py", texto)
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn("motor/grande.py: archivo de", corrida.stderr)
        self.assertIn("tokens de código; en " + self.base + " mide", corrida.stderr)
        self.assertNotIn("líneas; en", corrida.stderr)
        self.assertIn("Saca código", corrida.stderr)

    def test_cambiar_una_linea_en_blanco_por_codigo_falla(self):
        self._sin_crecer_en_lineas_falla_por_tokens(
            GRANDE.replace("\n\n# nota", "\ny = 2\n# nota", 1)
        )

    def test_cambiar_un_comentario_por_codigo_falla(self):
        self._sin_crecer_en_lineas_falla_por_tokens(
            GRANDE.replace("# nota que explica algo\n", "y = 2\n", 1)
        )

    def test_una_lista_larga_en_una_sola_linea_falla(self):
        datos = "datos = [" + ", ".join(str(n) for n in range(5000)) + "]\n"
        self._sin_crecer_en_lineas_falla_por_tokens(GRANDE.replace("x = 1\n", datos, 1))

    def test_sacar_una_funcion_pasa_y_dice_contra_que_compara_cada_medida(self):
        self.repo.escribir("motor/grande.py", GRANDE.replace(_funcion("chica", 5), ""))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn(f"líneas: ninguno supera lo que mide en {self.base}", corrida.stdout)
        self.assertIn(
            f"tokens de código: ninguno supera lo que mide en {self.base}", corrida.stdout
        )

    def test_entra_tanto_codigo_como_sale_y_ni_un_token_mas(self):
        self.repo.escribir("motor/grande.py", GRANDE.replace("def chica():", "def otra():"))
        self.assertEqual(self.repo.tamano("--base", self.base).returncode, 0)

        self.repo.escribir("motor/grande.py", GRANDE.replace("def chica():", "def otra(a):"))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("tokens de código", corrida.stderr)

    def test_una_funcion_que_pasa_del_limite_tampoco_crece_en_tokens(self):
        larga = "def larga():\n" + "    x = 1\n" * 100 + "\n" + "    x = 1\n" * 49
        self.repo.escribir("motor/m.py", larga)
        base = self.repo.commit()
        self.repo.escribir("motor/m.py", larga.replace("\n\n", "\n    y = 2\n", 1))
        corrida = self.repo.tamano("--base", base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/m.py::larga: función de", corrida.stderr)
        self.assertIn("tokens de código", corrida.stderr)


class ArchivoQueNoEsUtf8(unittest.TestCase):
    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir("motor/m.py", _relleno(10))
        self.latin = "nombre = 'Peña'\n".encode("latin-1")

    def test_en_el_arbol_falla_nombrando_el_archivo(self):
        base = self.repo.commit()
        (self.repo.raiz / "motor" / "latin.py").write_bytes(self.latin)
        corrida = self.repo.tamano("--base", base)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("motor/latin.py", corrida.stderr)
        self.assertIn("UTF-8", corrida.stderr)
        self.assertNotIn("Traceback", corrida.stderr)

    def test_en_la_base_no_traba_el_pr_que_lo_quita(self):
        (self.repo.raiz / "motor" / "latin.py").write_bytes(self.latin)
        base = self.repo.commit()
        (self.repo.raiz / "motor" / "latin.py").unlink()
        corrida = self.repo.tamano("--base", base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn(f"motor/latin.py en {base} no es UTF-8", corrida.stderr)


class EnlacesYSubmodulosEnElArbol(unittest.TestCase):
    """Un enlace o un submódulo bajo `motor/` saca código de la medida."""

    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir("motor/grande.py", _relleno(1200))
        self.base = self.repo.commit()

    def test_una_carpeta_de_motor_enlazada_fuera_falla_nombrandola(self):
        self.repo.escribir("fuera/encargos/ciclo.py", _relleno(1300))
        self.repo.enlazar("motor/encargos", "../fuera/encargos")
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn("motor/encargos es un enlace simbólico", corrida.stderr)
        self.assertNotIn("Traceback", corrida.stderr)

    def test_un_submodulo_sin_iniciar_bajo_motor_falla_nombrandolo(self):
        self.repo.git("update-index", "--add", "--cacheinfo", f"160000,{self.base},motor/vendor")
        self.repo.git("commit", "-q", "-m", "submódulo")
        checkout = self.repo.clonar()
        vendor = checkout.raiz / "motor" / "vendor"
        self.assertEqual(list(vendor.iterdir()), [], "así deja el checkout del CI un submódulo")
        corrida = checkout.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn("motor/vendor es un submódulo", corrida.stderr)

    def _submodulo_en_el_indice(self) -> Path:
        self.repo.git("update-index", "--add", "--cacheinfo", f"160000,{self.base},motor/vendor")
        self.repo.git("commit", "-q", "-m", "submódulo")
        return self.repo.raiz / "motor" / "vendor"

    def test_un_submodulo_reemplazado_por_archivos_sin_agregar_se_mide_como_arbol(self):
        vendor = self._submodulo_en_el_indice()
        self.repo.escribir("motor/vendor/propio.py", _relleno(10))
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)

        (vendor / "propio.py").write_text(_relleno(1001), encoding="utf-8")
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn("motor/vendor/propio.py: archivo de 1001 líneas", corrida.stderr)
        self.assertNotIn("submódulo", corrida.stderr)

    def test_un_submodulo_sin_carpeta_en_el_disco_falla_nombrandolo(self):  # no-roja-en-base: guarda que leer el disco no deje pasar un gitlink cuyo camino no existe
        vendor = self._submodulo_en_el_indice()
        self.assertFalse(vendor.exists())
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn("motor/vendor es un submódulo", corrida.stderr)

    def test_un_submodulo_iniciado_bajo_motor_falla_nombrandolo(self):  # no-roja-en-base: guarda que leer el disco no deje pasar un submódulo con su propio .git
        vendor = self._submodulo_en_el_indice()
        self.repo.escribir("motor/vendor/ajeno.py", _relleno(10))
        (vendor / ".git").write_text("gitdir: ../../.git/modules/vendor\n", encoding="utf-8")
        corrida = self.repo.tamano("--base", self.base)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn("motor/vendor es un submódulo", corrida.stderr)


class LoQueLaBaseNoPuedeMedir(unittest.TestCase):
    """La base solo pone el techo: lo que ella no puede medir no traba al PR que lo arregla."""

    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir("motor/grande.py", _relleno(1200))
        self.inicio = self.repo.commit()

    def _juzgar(self, base: str) -> subprocess.CompletedProcess:
        return self.repo.tamano("--base", base, "--cabeza", self.repo.git("rev-parse", "HEAD"))

    def test_un_enlace_en_la_base_no_traba_el_pr_que_lo_quita(self):
        self.repo.enlazar("motor/enlace.py", "grande.py")
        base = self.repo.commit()
        (self.repo.raiz / "motor" / "enlace.py").unlink()
        self.repo.commit()
        corrida = self._juzgar(base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn(f"motor/enlace.py en {base} es un enlace simbólico", corrida.stderr)

    def test_un_submodulo_en_la_base_no_traba_el_pr_que_lo_quita(self):
        self.repo.git("update-index", "--add", "--cacheinfo", f"160000,{self.inicio},motor/sub")
        self.repo.git("commit", "-q", "-m", "submódulo")
        base = self.repo.git("rev-parse", "HEAD")
        self.repo.git("rm", "-q", "--cached", "motor/sub")
        self.repo.git("commit", "-q", "-m", "sin submódulo")
        corrida = self._juzgar(base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn(f"motor/sub en {base} es un submódulo", corrida.stderr)

    def test_un_archivo_que_python_no_analiza_en_la_base_no_traba_el_pr_que_lo_arregla(self):
        self.repo.escribir("motor/roto.py", "def roto(:\n")
        base = self.repo.commit()
        self.repo.escribir("motor/roto.py", _funcion("roto", 3))
        self.repo.commit()
        corrida = self._juzgar(base)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn(f"motor/roto.py en {base} no se puede analizar", corrida.stderr)

    def test_lo_que_la_base_no_mide_se_juzga_como_si_no_existiera(self):
        self.repo.enlazar("motor/enlace.py", "grande.py")
        base = self.repo.commit()
        (self.repo.raiz / "motor" / "enlace.py").unlink()
        self.repo.escribir("motor/enlace.py", _relleno(1001))
        self.repo.commit()
        corrida = self._juzgar(base)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn(
            f"motor/enlace.py: archivo de 1001 líneas; no existe en {base}", corrida.stderr
        )


class CabezaLeidaDeGit(unittest.TestCase):
    """Con `--cabeza`, los dos lados salen de git y el árbol de trabajo no cuenta."""

    def setUp(self):
        self.repo = _repo(self)
        self.repo.escribir("motor/grande.py", _relleno(1200))
        self.base = self.repo.commit()

    def _cabeza(self, ruta: str, texto: str) -> str:
        self.repo.escribir(ruta, texto)
        cabeza = self.repo.commit()
        self.repo.git("checkout", "-q", self.base)
        return cabeza

    def test_la_cabeza_que_crece_falla_aunque_el_arbol_no_crezca(self):
        cabeza = self._cabeza("motor/grande.py", _relleno(1201))
        corrida = self.repo.tamano("--base", self.base, "--cabeza", cabeza)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn("motor/grande.py: archivo de 1201 líneas", corrida.stderr)
        self.assertIn(f"en {self.base} mide 1200", corrida.stderr)

    def test_la_cabeza_que_no_crece_pasa_aunque_el_arbol_crezca(self):
        cabeza = self._cabeza("README", "no toca motor/\n")
        self.repo.escribir("motor/grande.py", _relleno(1300))
        corrida = self.repo.tamano("--base", self.base, "--cabeza", cabeza)
        self.assertEqual(corrida.returncode, 0, corrida.stderr)
        self.assertIn(f"en {cabeza} contra {self.base}", corrida.stdout)

    def test_un_archivo_nuevo_sobre_el_limite_en_la_cabeza_falla(self):
        cabeza = self._cabeza("motor/encargos/nuevo.py", _relleno(1001))
        corrida = self.repo.tamano("--base", self.base, "--cabeza", cabeza)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn("motor/encargos/nuevo.py: archivo de 1001 líneas", corrida.stderr)
        self.assertIn("no existe", corrida.stderr)

    def test_una_cabeza_que_no_esta_en_el_clon_falla_y_el_consejo_la_trae(self):
        self.repo.git("branch", "pr", self._cabeza("README", "otro\n"))
        clon = self.repo.clonar()
        clon.git("update-ref", "-d", "refs/remotes/origin/pr")
        corrida = clon.tamano("--base", self.base, "--cabeza", "origin/pr")
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("la cabeza origin/pr no existe", corrida.stderr)
        self.assertNotIn("Traceback", corrida.stderr)

        clon.correr_consejo(corrida.stderr)
        corrida = clon.tamano("--base", self.base, "--cabeza", "origin/pr")
        self.assertEqual(corrida.returncode, 0, corrida.stderr)

    def test_un_submodulo_en_la_cabeza_falla_nombrandolo(self):
        self.repo.git("update-index", "--add", "--cacheinfo", f"160000,{self.base},motor/sub.py")
        self.repo.git("commit", "-q", "-m", "submódulo")
        cabeza = self.repo.git("rev-parse", "HEAD")
        corrida = self.repo.tamano("--base", self.base, "--cabeza", cabeza)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn(f"motor/sub.py en {cabeza} es un submódulo", corrida.stderr)

    def test_motor_entero_enlazado_a_otra_carpeta_en_la_cabeza_falla(self):
        self.repo.escribir("otro/grande.py", _relleno(1300))
        shutil.rmtree(self.repo.raiz / "motor")
        self.repo.enlazar("motor", "otro")
        cabeza = self.repo.commit()
        self.repo.git("checkout", "-q", self.base)
        corrida = self.repo.tamano("--base", self.base, "--cabeza", cabeza)
        self.assertEqual(corrida.returncode, 1, corrida.stdout)
        self.assertIn(f"motor en {cabeza} es un enlace simbólico", corrida.stderr)


def _numerado(desde: int, hasta: int) -> str:
    return "".join(f"x{n} = {n}\n" for n in range(desde, hasta))


FLUJO_DE_LA_BASE = RAIZ / ".github" / "workflows" / "tamano-desde-la-base.yml"


def _bloque_run(lineas: list[str]) -> tuple[list[str], str]:
    corre = lineas.index("        run: |")
    return lineas[:corre], textwrap.dedent("\n".join(lineas[corre + 1 :]))


class JobDeLaBase(unittest.TestCase):
    """El job de `pull_request_target` mide el PR con el `calidad.py` de la base."""

    def setUp(self):
        self.origen = _repo(self)
        self.origen.escribir("motor/grande.py", _numerado(0, 1200))
        self.origen.escribir("README", "base\n")
        self.base = self.origen.commit()

    def _pr(
        self, cambios: dict[str, str], enlaces: dict[str, str] | None = None, rama: str = "pr"
    ) -> str:
        self.origen.git("checkout", "-q", "-b", rama, self.base)
        for ruta, texto in cambios.items():
            self.origen.escribir(ruta, texto)
        for ruta, destino in (enlaces or {}).items():
            self.origen.enlazar(ruta, destino)
        cabeza = self.origen.commit()
        self.origen.git("checkout", "-q", "main")
        return cabeza

    def _avanzar_la_base(self, ruta: str, texto: str) -> str:
        self.origen.escribir(ruta, texto)
        return self.origen.commit()

    def _corrida_repetida(self, cabeza: str) -> subprocess.CompletedProcess:
        """Un re-run de GitHub: checkout nuevo, pero en el mismo `GITHUB_SHA` del evento."""
        nombre = f"repetida-{len(list(self.origen.raiz.parent.glob('repetida-*')))}"
        checkout = self.origen.clonar("--single-branch", "--branch", "main", nombre=nombre)
        checkout.git("reset", "-q", "--hard", self.base)
        return self._guion(checkout, cabeza)

    def _clon(self) -> Repo:
        """El checkout de la base: solo `main`, sin la rama ni los objetos del PR."""
        checkout = Repo(self.origen.raiz.parent / "clon")
        if not checkout.raiz.exists():
            checkout = self.origen.clonar("--single-branch", "--branch", "main")
        return checkout

    def _job(self, cabeza: str) -> subprocess.CompletedProcess:
        checkout = self._clon()
        ya_esta = subprocess.run(
            ["git", "cat-file", "-e", f"{cabeza}^{{commit}}"],
            cwd=checkout.raiz,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(
            ya_esta.returncode, 0, "la cabeza del PR solo puede llegar por el fetch del guion"
        )
        return self._guion(checkout, cabeza)

    def _guion(self, checkout: Repo, cabeza: str) -> subprocess.CompletedProcess:
        _, guion = _bloque_run(FLUJO_DE_LA_BASE.read_text(encoding="utf-8").splitlines())
        binarios = checkout.raiz.parent / "bin"
        binarios.mkdir(exist_ok=True)
        python = binarios / "python3"
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        python.chmod(0o755)
        entorno = dict(os.environ, CABEZA_DEL_PR=cabeza, GITHUB_REF="refs/heads/main")
        entorno["PATH"] = f"{binarios}{os.pathsep}{entorno['PATH']}"
        return subprocess.run(
            ["bash", "-e", "-c", guion],
            cwd=checkout.raiz,
            env=entorno,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_un_pr_que_crece_y_apaga_su_propio_calidad_falla(self):
        cabeza = self._pr(
            {"motor/grande.py": _numerado(0, 1201), "tools/calidad.py": "raise SystemExit(0)\n"}
        )
        corrida = self._job(cabeza)
        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertIn("motor/grande.py: archivo de 1201 líneas", corrida.stderr)
        self.assertIn(f"en {self.base} mide 1200", corrida.stderr)

    def test_un_pr_que_enlaza_un_archivo_de_motor_a_uno_mas_grande_fuera_falla(self):
        cabeza = self._pr(
            {"fuera/grande.py": _numerado(0, 1300)},
            enlaces={"motor/grande.py": "../fuera/grande.py"},
        )
        corrida = self._job(cabeza)
        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertIn("motor/grande.py en ", corrida.stderr)
        self.assertIn(" es un enlace simbólico", corrida.stderr)

    def test_un_pr_atrasado_que_no_toca_motor_pasa_aunque_la_base_se_encogio(self):
        cabeza = self._pr({"README": "pr\n"})
        self._avanzar_la_base("motor/grande.py", _numerado(100, 1200))
        corrida = self._job(cabeza)
        self.assertEqual(corrida.returncode, 0, corrida.stdout + corrida.stderr)
        self.assertIn("nada crece", corrida.stdout)

    def test_un_pr_atrasado_que_crece_falla_contra_lo_que_mide_la_base_hoy(self):
        cabeza = self._pr({"motor/grande.py": _numerado(0, 1200) + "y = 2\n"})
        self._avanzar_la_base("motor/grande.py", _numerado(100, 1200))
        corrida = self._job(cabeza)
        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertIn("motor/grande.py: archivo de 1101 líneas", corrida.stderr)
        self.assertIn("mide 1100", corrida.stderr)

    def test_dos_pr_que_juntos_cruzan_el_limite_el_que_sigue_abierto_falla_al_repetirse(self):
        self.base = self._avanzar_la_base("motor/medio.py", _numerado(0, 999))
        self._pr({"motor/medio.py": "a = 0\n" + _numerado(0, 999)}, rama="primero")
        segundo = self._pr({"motor/medio.py": _numerado(0, 999) + "z = 0\n"}, rama="segundo")
        corrida = self._job(segundo)
        self.assertEqual(corrida.returncode, 0, corrida.stdout + corrida.stderr)

        self.origen.git("merge", "-q", "--no-ff", "-m", "entra el primero", "primero")
        corrida = self._corrida_repetida(segundo)
        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertIn("motor/medio.py: archivo de 1001 líneas", corrida.stderr)
        self.assertIn("mide 1000", corrida.stderr)

    def test_un_pr_atrasado_que_no_toca_motor_sigue_pasando_al_repetirse(self):
        cabeza = self._pr({"README.pr": "pr\n"})
        self.assertEqual(self._job(cabeza).returncode, 0)
        punta = self._avanzar_la_base("motor/grande.py", _numerado(0, 1100))
        corrida = self._corrida_repetida(cabeza)
        self.assertEqual(corrida.returncode, 0, corrida.stdout + corrida.stderr)
        self.assertIn(f"contra {punta}", corrida.stdout)

    def test_al_repetirse_juzga_con_el_calidad_de_la_punta_de_la_base(self):
        cabeza = self._pr({"motor/nuevo.py": _numerado(0, 950)})
        self.assertEqual(self._job(cabeza).returncode, 0)
        herramienta = (self.origen.raiz / "tools" / "calidad.py").read_text(encoding="utf-8")
        limite = 'LIMITES_DE_TAMANO = {"archivo": 1000,'
        self.assertIn(limite, herramienta)
        self._avanzar_la_base(
            "tools/calidad.py", herramienta.replace(limite, limite.replace("1000", "900"), 1)
        )
        corrida = self._corrida_repetida(cabeza)
        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertIn("motor/nuevo.py: archivo de 950 líneas", corrida.stderr)
        self.assertIn("el límite es 900", corrida.stderr)

    def test_un_pr_que_choca_con_la_base_falla_y_pide_actualizar_la_rama(self):
        cabeza = self._pr({"README": "pr\n"})
        self._avanzar_la_base("README", "main\n")
        corrida = self._job(cabeza)
        self.assertEqual(corrida.returncode, 1)
        self.assertIn("actualiza la rama", corrida.stderr)

    def test_una_cabeza_que_no_es_un_sha_no_llega_a_git(self):
        for cabeza in ("main", "$(touch pwn)", "a" * 39):
            with self.subTest(cabeza=cabeza):
                corrida = self._guion(self._clon(), cabeza)
                self.assertEqual(corrida.returncode, 1)
                self.assertIn("no es un SHA válido", corrida.stderr)

    def test_el_check_se_publica_con_el_nombre_que_dice_agents(self):
        lineas = FLUJO_DE_LA_BASE.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lineas[lineas.index("jobs:") + 1], "  tamano-desde-la-base:")
        self.assertEqual(
            [linea for linea in lineas if linea.startswith("    name:")],
            [],
            "GitHub publica el check con el name: del job; sin él, con su id",
        )
        agentes = (RAIZ / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("job `tamano-desde-la-base`", agentes)

    def test_el_job_no_recibe_secretos_ni_trae_el_codigo_del_pr(self):
        texto = FLUJO_DE_LA_BASE.read_text(encoding="utf-8")
        lineas = texto.splitlines()
        disparo = lineas.index("on:")
        self.assertEqual(lineas[disparo + 1], "  pull_request_target:")
        self.assertFalse(lineas[disparo + 2].startswith("  "), "un solo disparador")
        permisos = lineas.index("permissions:")
        self.assertEqual(lineas[permisos + 1 : permisos + 3], ["  contents: read", ""])
        self.assertNotIn("secrets.", texto)
        self.assertNotIn("github.token", texto)
        self.assertIn("          persist-credentials: false", lineas)
        self.assertNotIn("ref:", texto, "el checkout es el de la base, nunca el del PR")
        previo, guion = _bloque_run(lineas)
        self.assertNotIn("${{", guion, "lo que viene del evento entra por env, validado")
        expresiones = [linea.strip() for linea in previo if "${{" in linea]
        self.assertEqual(
            expresiones, ["CABEZA_DEL_PR: ${{ github.event.pull_request.head.sha }}"]
        )


FLUJO_AL_AVANZAR = RAIZ / ".github" / "workflows" / "tamano-al-avanzar-la-base.yml"
INICIO_DEL_JOB = 1_700_000_000

# Un `gh` falso con la forma de la API de GitHub (vista en solo lectura contra
# este repo). Contesta los dos caminos a la corrida de un PR: las corridas del
# workflow y los checks publicados en la cabeza. Aplica `--jq` con `jq`, como
# `gh`, página por página, y anota cada llamada. El tiempo es simulado: cada
# llamada tarda un segundo, `sleep` lo avanza y una corrida termina cuando el
# reloj llega a su fin.
GH_FALSO = """
import json, os, re, subprocess, sys, urllib.parse
escenario = json.load(open(os.environ["ESCENARIO"]))
args = sys.argv[1:]
with open(os.environ["LLAMADAS"], "a") as log:
    log.write(json.dumps(args) + "\\n")
with open(os.environ["RELOJ"], "r+") as reloj:
    ahora = int(reloj.read()) + 1
    reloj.seek(0)
    reloj.write(str(ahora))
    reloj.truncate()
url = next(a for a in args if a.startswith("repos/"))
camino, _, consulta = url.partition("?")
parametros = dict(urllib.parse.parse_qsl(consulta))
jq = args[args.index("--jq") + 1] if "--jq" in args else None


def estado(corrida):
    fin = escenario["terminan"].get(str(corrida["id"]), 0)
    return "completed" if fin is not None and ahora >= fin else "in_progress"


def vista(corrida):
    return {**{k: v for k, v in corrida.items() if k != "job"}, "status": estado(corrida)}


def la_corrida(id):
    return next(c for c in escenario["corridas"] if str(c["id"]) == id)


def contestar(paginas):
    for pagina in paginas:
        if jq is None:
            print(json.dumps(pagina))
        else:
            sys.stdout.flush()
            subprocess.run(["jq", "-r", jq], input=json.dumps(pagina), text=True, check=True)


if (set(camino.split("/")) | set(parametros.values())) & set(escenario["fallan"]):
    sys.exit(f"gh: Server Error (HTTP 502) en {url}")
if camino.endswith("/pulls"):
    prs = [p for p in escenario["prs"] if parametros.get("base", p["base"]["ref"]) == p["base"]["ref"]]
    tope = min(int(parametros.get("per_page", 30)), escenario["tope_por_pagina"])
    paginas = [prs[i : i + tope] for i in range(0, len(prs), tope)] or [[]]
    contestar(paginas if "--paginate" in args else paginas[:1])
elif m := re.fullmatch(r"repos/[^/]+/[^/]+/actions/workflows/([^/]+)/runs", camino):
    corridas = [
        vista(c)
        for c in escenario["corridas"]
        if c["path"] == ".github/workflows/" + m.group(1)
        and parametros.get("event", c["event"]) == c["event"]
        and parametros.get("head_sha", c["head_sha"]) == c["head_sha"]
    ]
    contestar([{"total_count": len(corridas), "workflow_runs": corridas}])
elif m := re.fullmatch(r"repos/[^/]+/[^/]+/commits/(\\w+)/check-runs", camino):
    checks = [
        {
            "name": c["job"],
            "head_sha": c["head_sha"],
            "status": estado(c),
            "details_url": f"https://github.com/duena/repo/actions/runs/{c['id']}/job/9{c['id']}",
            "app": {"slug": "github-actions"},
        }
        for c in escenario["corridas"]
        if c["head_sha"] == m.group(1) and parametros.get("check_name", c["job"]) == c["job"]
    ]
    contestar([{"total_count": len(checks), "check_runs": checks}])
elif m := re.fullmatch(r"repos/[^/]+/[^/]+/actions/runs/(\\d+)/rerun", camino):
    if m.group(1) in escenario["no_se_repiten"]:
        sys.exit("gh: Forbidden (HTTP 403)")
    if estado(la_corrida(m.group(1))) != "completed":
        sys.exit("gh: This workflow is already running (HTTP 403)")
elif m := re.fullmatch(r"repos/[^/]+/[^/]+/actions/runs/(\\d+)", camino):
    contestar([vista(la_corrida(m.group(1)))])
else:
    sys.exit(f"gh falso: no conozco {args}")
"""

DORMIR_FALSO = """#!/bin/sh
ahora=$(( $(cat "$RELOJ") + ${1%s} ))
echo "$ahora" > "$RELOJ"
# GitHub mata el job cuando pasa su timeout-minutes.
[ "$ahora" -lt "$LIMITE_DEL_JOB" ] || kill -9 0
"""

FECHA_FALSA = f"""#!/bin/sh
[ "$1" = "+%s" ] || exec /bin/date "$@"
echo $(( {INICIO_DEL_JOB} + $(cat "$RELOJ") ))
"""


def _pr_abierto(cabeza: str, base: str = "main") -> dict:
    return {"head": {"sha": cabeza}, "base": {"ref": base}}


def _corrida(
    id: str,
    cabeza: str,
    creada: str = "2026-11-13T07:00:00Z",
    evento: str = "pull_request_target",
    flujo: str = "tamano-desde-la-base.yml",
) -> dict:
    """Una corrida como la devuelve la API; `job` es el nombre del check que publica."""
    return {
        "id": int(id),
        "event": evento,
        "path": f".github/workflows/{flujo}",
        "head_sha": cabeza,
        "created_at": creada,
        "run_attempt": 1,
        "job": "tamano-desde-la-base",
    }


def _escenario(
    prs: list[dict],
    corridas: list[dict],
    terminan: dict[str, int | None] | None = None,
    no_se_repiten: tuple[str, ...] = (),
    fallan: tuple[str, ...] = (),
    tope_por_pagina: int = 100,
) -> dict:
    """`terminan`: segundos del job en que termina cada corrida; `None` nunca. Sin dato, ya terminó."""
    return {
        "prs": prs,
        "corridas": corridas,
        "terminan": terminan or {},
        "no_se_repiten": list(no_se_repiten),
        "fallan": list(fallan),
        "tope_por_pagina": tope_por_pagina,
    }


A, B, C, D, E = ("a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40)


class ReevaluacionAlAvanzarLaBase(unittest.TestCase):
    """En cada push a la base, cada PR abierto vuelve a correr su check de tamaño."""

    def setUp(self):
        self.assertIsNotNone(shutil.which("jq"), "el gh falso aplica --jq con jq, como gh")
        directorio = tempfile.TemporaryDirectory()
        self.addCleanup(directorio.cleanup)
        self.dir = Path(directorio.name)

    def _job(self, escenario: dict) -> tuple[subprocess.CompletedProcess, list[list[str]]]:
        texto = FLUJO_AL_AVANZAR.read_text(encoding="utf-8")
        _, guion = _bloque_run(texto.splitlines())
        plazo = re.search(r"^    timeout-minutes: (\d+)$", texto, re.MULTILINE)
        assert plazo, "el job fija su timeout-minutes"
        corrida_dir = Path(tempfile.mkdtemp(dir=self.dir))
        binarios = corrida_dir / "bin"
        binarios.mkdir()
        for nombre, cuerpo in (
            ("gh", f"#!{sys.executable}\n{GH_FALSO}"),
            ("sleep", DORMIR_FALSO),
            ("date", FECHA_FALSA),
        ):
            ejecutable = binarios / nombre
            ejecutable.write_text(cuerpo, encoding="utf-8")
            ejecutable.chmod(0o755)
        (corrida_dir / "escenario.json").write_text(json.dumps(escenario), encoding="utf-8")
        (corrida_dir / "reloj").write_text("0", encoding="utf-8")
        entorno = dict(
            os.environ,
            PATH=f"{binarios}{os.pathsep}{os.environ['PATH']}",
            ESCENARIO=str(corrida_dir / "escenario.json"),
            LLAMADAS=str(corrida_dir / "llamadas"),
            RELOJ=str(corrida_dir / "reloj"),
            LIMITE_DEL_JOB=str(int(plazo.group(1)) * 60),
            GITHUB_REPOSITORY="duena/repo",
            GITHUB_REF_NAME="main",
        )
        corrida = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", guion],
            cwd=corrida_dir,
            env=entorno,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            start_new_session=True,
        )
        llamadas = (corrida_dir / "llamadas").read_text(encoding="utf-8").splitlines()
        return corrida, [json.loads(linea) for linea in llamadas]

    @staticmethod
    def _repetidas(llamadas: list[list[str]]) -> list[str]:
        return [
            url.split("/")[-2]
            for args in llamadas
            for url in args
            if url.endswith("/rerun") and "POST" in args
        ]

    def test_repite_la_ultima_corrida_de_cada_pr_abierto_despues_de_que_termina(self):
        corrida, llamadas = self._job(
            _escenario(
                [_pr_abierto(A), _pr_abierto(B)],
                [_corrida("11", A), _corrida("22", B)],
                terminan={"11": 30},
            )
        )
        self.assertEqual(corrida.returncode, 0, corrida.stdout + corrida.stderr)
        self.assertEqual(sorted(self._repetidas(llamadas)), ["11", "22"])

    def test_repite_la_corrida_del_workflow_de_la_base_y_no_la_de_un_job_con_su_nombre(self):
        verdadera = _corrida("11", A)
        falsas = [
            _corrida("66", A, creada="2026-11-13T08:00:00Z", evento="pull_request"),
            _corrida(
                "77", A, creada="2026-11-13T08:00:00Z", evento="pull_request", flujo="falso.yml"
            ),
        ]
        for orden, corridas in (
            ("el falso antes", [*falsas, verdadera]),
            ("el falso después", [verdadera, *falsas]),
        ):
            with self.subTest(orden):
                corrida, llamadas = self._job(_escenario([_pr_abierto(A)], corridas))
                self.assertEqual(corrida.returncode, 0, corrida.stdout + corrida.stderr)
                self.assertEqual(self._repetidas(llamadas), ["11"])

    def test_de_varias_corridas_del_workflow_de_la_base_repite_la_mas_nueva(self):
        corrida, llamadas = self._job(
            _escenario(
                [_pr_abierto(A)],
                [
                    _corrida("11", A, creada="2026-11-13T07:00:00Z"),
                    _corrida("13", A, creada="2026-11-13T09:00:00Z"),
                    _corrida("12", A, creada="2026-11-13T08:00:00Z"),
                ],
            )
        )
        self.assertEqual(corrida.returncode, 0, corrida.stdout + corrida.stderr)
        self.assertEqual(self._repetidas(llamadas), ["13"])

    def test_un_pr_abierto_sin_corrida_del_workflow_de_la_base_deja_el_job_en_rojo(self):
        corrida, llamadas = self._job(
            _escenario(
                [_pr_abierto(A), _pr_abierto(B)],
                [_corrida("66", A, evento="pull_request"), _corrida("22", B)],
            )
        )
        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertEqual(self._repetidas(llamadas), ["22"])
        self.assertRegex(corrida.stderr, rf"::error::tamano: {A} no tiene corrida")

    def test_si_la_api_falla_al_buscar_la_corrida_el_job_queda_en_rojo_y_sigue_con_los_demas(self):
        corrida, llamadas = self._job(
            _escenario(
                [_pr_abierto(A), _pr_abierto(B)],
                [_corrida("11", A), _corrida("22", B)],
                fallan=(A,),
            )
        )
        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertEqual(self._repetidas(llamadas), ["22"])
        self.assertRegex(corrida.stderr, rf"::error::tamano: .*{A}")

    def test_tres_corridas_colgadas_no_dejan_sin_repetir_a_los_pr_de_atras(self):
        colgadas = {A: "11", B: "22", C: "33"}
        corrida, llamadas = self._job(
            _escenario(
                [_pr_abierto(cabeza) for cabeza in (A, B, C, D, E)],
                [_corrida(id, cabeza) for cabeza, id in colgadas.items()]
                + [_corrida("44", D), _corrida("55", E)],
                terminan={id: None for id in colgadas.values()},
            )
        )
        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertEqual(sorted(self._repetidas(llamadas)), ["44", "55"])
        for cabeza, id in colgadas.items():
            self.assertRegex(
                corrida.stderr, rf"::error::tamano: la corrida {id} de {cabeza} no terminó"
            )

    def test_si_la_api_de_la_corrida_falla_se_marca_y_sigue_con_los_demas(self):
        corrida, llamadas = self._job(
            _escenario(
                [_pr_abierto(A), _pr_abierto(B)],
                [_corrida("11", A), _corrida("22", B)],
                terminan={"11": None},
                fallan=("11",),
            )
        )
        self.assertEqual(corrida.returncode, 1, corrida.stdout + corrida.stderr)
        self.assertEqual(self._repetidas(llamadas), ["22"])
        self.assertRegex(corrida.stderr, rf"::error::tamano: no se pudo leer la corrida 11 de {A}")

    def test_una_corrida_que_no_se_puede_repetir_tumba_el_job_despues_de_intentar_todas(self):
        corrida, llamadas = self._job(
            _escenario(
                [_pr_abierto(A), _pr_abierto(B)],
                [_corrida("11", A), _corrida("22", B)],
                no_se_repiten=("11",),
            )
        )
        self.assertEqual(corrida.returncode, 1)
        self.assertEqual(sorted(self._repetidas(llamadas)), ["11", "22"])
        self.assertRegex(corrida.stderr, rf"::error::tamano: .*{A}")

    def test_solo_repite_los_pr_cuya_base_es_la_rama_que_avanzo(self):
        corrida, llamadas = self._job(
            _escenario(
                [_pr_abierto(A), _pr_abierto(B, base="otra")],
                [_corrida("11", A), _corrida("22", B)],
            )
        )
        self.assertEqual(corrida.returncode, 0, corrida.stdout + corrida.stderr)
        self.assertEqual(self._repetidas(llamadas), ["11"])

    def test_repite_los_pr_de_todas_las_paginas(self):
        corrida, llamadas = self._job(
            _escenario(
                [_pr_abierto(A), _pr_abierto(B)],
                [_corrida("11", A), _corrida("22", B)],
                tope_por_pagina=1,
            )
        )
        self.assertEqual(corrida.returncode, 0, corrida.stdout + corrida.stderr)
        self.assertEqual(self._repetidas(llamadas), ["11", "22"])

    def test_corre_en_el_push_a_main_con_el_permiso_mas_chico_y_sin_codigo(self):
        texto = FLUJO_AL_AVANZAR.read_text(encoding="utf-8")
        lineas = texto.splitlines()
        disparo = lineas.index("on:")
        self.assertEqual(lineas[disparo + 1 : disparo + 4], ["  push:", "    branches: [main]", ""])
        permisos = lineas.index("permissions:")
        self.assertEqual(
            lineas[permisos + 1 : permisos + 4], ["  actions: write", "  pull-requests: read", ""]
        )
        concurrencia = lineas.index("concurrency:")
        self.assertEqual(
            lineas[concurrencia + 1 : concurrencia + 4],
            ["  group: tamano-al-avanzar-la-base", "  cancel-in-progress: true", ""],
            "un push nuevo cancela al anterior: dos jobs no repiten la misma corrida a la vez",
        )
        self.assertNotIn("uses:", texto, "no hace checkout ni corre acciones de terceros")
        self.assertNotIn("secrets.", texto)
        previo, guion = _bloque_run(lineas)
        self.assertNotIn("${{", guion)
        self.assertEqual(
            [linea.strip() for linea in previo if "${{" in linea],
            ["GH_TOKEN: ${{ github.token }}"],
        )
        self.assertIn("tamano-desde-la-base", guion)
        self.assertTrue(FLUJO_DE_LA_BASE.is_file())


if __name__ == "__main__":
    unittest.main()
