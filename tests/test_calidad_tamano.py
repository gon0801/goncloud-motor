"""`calidad.py tamano` mide si un archivo o una función de `motor/` crece; ya no frena nada.

Cada prueba arma un repo de git temporal con su propio `motor/`, guarda una base
en un commit y corre la herramienta como la corría el CI, contra esa base.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
HERRAMIENTA = RAIZ / "tools" / "calidad.py"
POLITICA = "motor/encargos/politica.py"


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


if __name__ == "__main__":
    unittest.main()
