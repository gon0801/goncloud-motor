"""Traducción de rutas Windows <-> WSL. Funciones puras: corren en cualquier sistema."""

import json
import unittest

from motor.plataforma import rutas_wsl as r
from motor.plataforma import windows

ESPACIO_W = r"C:\Users\ehven\.motor-encargos\espacio\enc_a36123bfb5d142c7"
ESPACIO_L = "/mnt/c/Users/ehven/.motor-encargos/espacio/enc_a36123bfb5d142c7"


class RutaCompletaTest(unittest.TestCase):
    def test_unidad_de_windows_a_wsl(self):
        casos = {
            ESPACIO_W: ESPACIO_L,
            "C:/Users/ehven/x": "/mnt/c/Users/ehven/x",
            "d:\\datos\\a b\\c.txt": "/mnt/d/datos/a b/c.txt",
            "C:\\": "/mnt/c",
            "C:": "/mnt/c",
            "C:\\Users\\\\ehven\\": "/mnt/c/Users/ehven",
            "\\\\?\\C:\\Users\\x": "/mnt/c/Users/x",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(r.a_wsl(entrada), esperado)

    def test_wsl_a_windows(self):
        casos = {
            ESPACIO_L: ESPACIO_W,
            "/mnt/c": "C:\\",
            "/mnt/c/": "C:\\",
            "/mnt/d/datos/a b/c.txt": "D:\\datos\\a b\\c.txt",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(r.a_windows(entrada), esperado)

    def test_ida_y_vuelta_conserva_la_ruta(self):
        for ruta in (
            ESPACIO_W,
            ESPACIO_W + r"\.interno\comprobaciones.json",
            r"C:\Users\ehven\.motor-encargos\runtime\enc_x\ed98\tmp",
        ):
            with self.subTest(ruta=ruta):
                self.assertEqual(r.a_windows(r.a_wsl(ruta)), ruta)
        self.assertEqual(r.a_windows(r.a_wsl("D:\\")), "D:\\")
        self.assertEqual(r.a_wsl(r.a_windows(ESPACIO_L)), ESPACIO_L)

    def test_lo_que_no_tiene_equivalente_queda_igual(self):
        for ruta in (
            "/usr/bin/bash",
            "/home/ehven/.claude",
            "/mnt/wsl/resolv.conf",
            "/mnt/cc/x",
            "relativa/a.py",
            "C:suelta",
            r"\\servidor\recurso\x",
            "https://ejemplo.com/a",
            "",
        ):
            with self.subTest(ruta=ruta):
                self.assertEqual(r.a_wsl(ruta), ruta)
                self.assertEqual(r.a_windows(ruta), ruta)

    def test_ruta_del_sistema_de_archivos_de_la_distro(self):
        self.assertEqual(r.a_wsl(r"\\wsl.localhost\Ubuntu\home\ehven\x"), "/home/ehven/x")
        self.assertEqual(r.a_wsl(r"\\wsl$\Ubuntu\home\ehven"), "/home/ehven")
        self.assertEqual(r.a_wsl(r"\\wsl$\Ubuntu"), "/")
        self.assertEqual(
            r.a_windows("/home/ehven/x", distro="Ubuntu"),
            r"\\wsl.localhost\Ubuntu\home\ehven\x",
        )
        self.assertEqual(r.a_windows("/home/ehven/x"), "/home/ehven/x")

    def test_montaje_distinto_de_mnt(self):
        self.assertEqual(r.a_wsl(r"C:\x", montaje="/c-drives"), "/c-drives/c/x")
        self.assertEqual(r.a_windows("/c-drives/c/x", montaje="/c-drives"), "C:\\x")
        self.assertEqual(r.a_windows("/mnt/c/x", montaje="/c-drives"), "/mnt/c/x")


class ArgvTest(unittest.TestCase):
    def test_argv_traduce_elementos_y_opciones_con_ruta(self):
        argv = [
            "python3",
            "-m",
            "unittest",
            "--start-directory=" + ESPACIO_W + r"\tests",
            "-C",
            ESPACIO_W,
            "--nombre=C",
            "https://x.test/a",
        ]
        self.assertEqual(
            r.argv_a_wsl(argv),
            [
                "python3",
                "-m",
                "unittest",
                "--start-directory=" + ESPACIO_L + "/tests",
                "-C",
                ESPACIO_L,
                "--nombre=C",
                "https://x.test/a",
            ],
        )

    def test_argv_de_wsl_a_windows(self):
        self.assertEqual(
            r.argv_a_windows(["python3", ESPACIO_L + "/tests/x.py", "--out=/mnt/c/t/o"]),
            ["python3", ESPACIO_W + r"\tests\x.py", "--out=C:\\t\\o"],
        )

    def test_el_argv_original_no_cambia(self):
        argv = [ESPACIO_W]
        r.argv_a_wsl(argv)
        self.assertEqual(argv, [ESPACIO_W])

    def test_argv_en_wsl_de_windows_usa_la_misma_traduccion(self):
        self.assertEqual(
            windows.argv_en_wsl(["muse", "exec", "--prompt-file=" + ESPACIO_W + r"\b.md"]),
            ["wsl.exe", "-e", "muse", "exec", "--prompt-file=" + ESPACIO_L + "/b.md"],
        )

    def test_lanzamiento_wsl_traduce_el_argv_del_agente(self):
        argv, _ = windows.lanzamiento_wsl(["muse", ESPACIO_W + r"\brief.md"], {})
        self.assertEqual(argv[-2:], ["muse", ESPACIO_L + "/brief.md"])


class TextoTest(unittest.TestCase):
    def test_prompt_con_rutas_de_windows(self):
        texto = (
            f"Todo lo interno va en {ESPACIO_W}\\.interno:\n"
            f"cliente: python3 {ESPACIO_W}\\.interno\\buzon\\cliente.py pedir.\n"
            f"(ver C:\\Users\\ehven\\a.txt), o 'D:/x/y.md'; fin."
        )
        self.assertEqual(
            r.texto_a_wsl(texto),
            f"Todo lo interno va en {ESPACIO_L}/.interno:\n"
            f"cliente: python3 {ESPACIO_L}/.interno/buzon/cliente.py pedir.\n"
            f"(ver /mnt/c/Users/ehven/a.txt), o '/mnt/d/x/y.md'; fin.",
        )

    def test_texto_de_wsl_a_windows_con_ancla_y_puntuacion(self):
        texto = f"falla en {ESPACIO_L}/motor/a.py:12, y en /mnt/d/x. Mira /usr/bin/git."
        self.assertEqual(
            r.texto_a_windows(texto),
            f"falla en {ESPACIO_W}\\motor\\a.py:12, y en D:\\x. Mira /usr/bin/git.",
        )

    def test_texto_no_toca_urls_ni_letras_pegadas_ni_rutas_de_linux(self):
        for texto in (
            "https://github.com/gon0801/x y http://a/b",
            "ftp://c:/no",
            "abc:/x y xC:\\y",
            "/home/ehven/mnt/c/x y /mnt/wsl/x y /mnt/cc/x",
            "el modo c:",
        ):
            with self.subTest(texto=texto):
                self.assertEqual(r.texto_a_wsl(texto), texto)
                self.assertEqual(r.texto_a_windows(texto), texto)

    def test_texto_plano_con_tildes_y_enie_en_la_ruta(self):
        self.assertEqual(
            r.texto_a_wsl(f"lee {ESPACIO_W}\\diseño\\añadir.md."),
            f"lee {ESPACIO_L}/diseño/añadir.md.",
        )

    def test_texto_ida_y_vuelta(self):
        texto = f"escribe en {ESPACIO_L}/.interno/evidencia.txt y {ESPACIO_L}"
        self.assertEqual(r.texto_a_wsl(r.texto_a_windows(texto)), texto)


class DocumentoTest(unittest.TestCase):
    def test_comprobaciones_del_agente_en_wsl_se_leen_en_windows(self):
        """Lo que el agente deja en .interno/comprobaciones.json desde WSL."""
        doc = {
            "comprobaciones": [
                {
                    "objetivo": "rutas",
                    "argv": ["python3", "-m", "unittest", "--top=" + ESPACIO_L, ESPACIO_L],
                }
            ]
        }
        copia = json.loads(json.dumps(doc))
        a_w = r.valores_a_windows(doc)
        self.assertEqual(
            a_w["comprobaciones"][0]["argv"],
            ["python3", "-m", "unittest", "--top=" + ESPACIO_W, ESPACIO_W],
        )
        self.assertEqual(a_w["comprobaciones"][0]["objetivo"], "rutas")
        self.assertEqual(doc, copia)
        self.assertEqual(r.valores_a_wsl(a_w), doc)

    def test_json_crudo_con_no_ascii_y_saltos_no_pierde_nada(self):
        """json.dumps escapa la ñ como \\u00f1 y el salto como \\n: texto_a_wsl sobre ese texto
        tomaba la barra del escape por separador de ruta (h_e8baf939)."""
        doc = {
            "argv": ["cat", ESPACIO_W + "\\diseño.md"],
            "nota": "ver " + ESPACIO_W + "\nsiguiente\ty ñ",
            "cuerpo": "C:\\literal con\ttab",
            "pegada": ESPACIO_W + "\nsiguiente",
        }
        crudo = json.dumps(doc)
        self.assertIn("\\u00f1", crudo)
        esperado = {
            "argv": ["cat", ESPACIO_L + "/diseño.md"],
            "nota": "ver " + ESPACIO_W + "\nsiguiente\ty ñ",
            "cuerpo": "/mnt/c/literal con\ttab",
            "pegada": ESPACIO_L + "\nsiguiente",
        }
        corrupto = json.loads(r.texto_a_wsl(crudo))
        self.assertNotEqual(corrupto, esperado)
        self.assertEqual(json.loads(r.json_a_wsl(crudo)), esperado)
        self.assertEqual(json.loads(r.json_a_windows(r.json_a_wsl(crudo))), doc)
        self.assertEqual(r.valores_a_wsl(doc), esperado)

    def test_json_invalido_se_rechaza(self):
        with self.assertRaises(ValueError):
            r.json_a_wsl("{no es json")

    def test_las_claves_y_lo_que_no_es_texto_no_se_tocan(self):
        doc = {"C:\\clave": [1, True, None, 2.5, "C:\\x"]}
        self.assertEqual(r.valores_a_wsl(doc), {"C:\\clave": [1, True, None, 2.5, "/mnt/c/x"]})


if __name__ == "__main__":
    unittest.main()
