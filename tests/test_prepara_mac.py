"""ops/prepara_pc.py prepara las Mac igual que la PC, con la rama macOS.

Todo corre con dobles: un catálogo Adoptium falso, un descargador que sirve un
tar.gz de prueba y una base temporal. No toca el registro, la red ni secretos.
"""

import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest import mock

from ops import prepara_pc

RELEASE_FALSA = "jdk-25.0.5+8"
JAR_FALSO = b"PK jar de prueba"
RAIZ = Path(__file__).resolve().parents[1]


def _tar_de_temurin() -> bytes:
    memoria = io.BytesIO()
    with tarfile.open(fileobj=memoria, mode="w:gz") as t:
        for nombre, dato in {
            f"{RELEASE_FALSA}/Contents/Home/bin/java": b"#!/bin/sh\nexit 0\n",
            f"{RELEASE_FALSA}/Contents/Home/release": b'JAVA_VERSION="25.0.5"\n',
        }.items():
            info = tarfile.TarInfo(nombre)
            info.size = len(dato)
            info.mode = 0o755
            t.addfile(info, io.BytesIO(dato))
    return memoria.getvalue()


TAR_BYTES = _tar_de_temurin()
CATALOGO_MAC = [{
    "release_name": RELEASE_FALSA,
    "binary": {"package": {
        "link": "https://adoptium.example/jdk.tar.gz",
        "checksum": sha256(TAR_BYTES).hexdigest(),
    }},
}]


def _limpiar(tmp):
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipIf(os.name == "nt", "la preparación de macOS deja un enlace POSIX")
class PreparacionMacTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prepara-mac-"))
        self.addCleanup(_limpiar, self.tmp)
        self.base = self.tmp / ".motor-encargos"
        self.base.mkdir()
        self.secreto = self.base / "secretos.env"
        self.secreto.write_text("MOTOR_CANARIO=no-borrar\n", encoding="utf-8")
        self.jar_sha = sha256(JAR_FALSO).hexdigest()
        self.catalogo = lambda: json.loads(json.dumps(CATALOGO_MAC))

    def descargador(self, url):
        return TAR_BYTES if url.endswith("jdk.tar.gz") else JAR_FALSO

    def _instalar(self, descargador=None):
        with mock.patch.object(prepara_pc, "TLC_SHA256", self.jar_sha), mock.patch.object(
            prepara_pc, "sistema", lambda: "macos"
        ):
            return prepara_pc.instalar(
                self.base,
                catalogo=self.catalogo,
                descargador=descargador or self.descargador,
            )

    def _comprobar(self):
        with mock.patch.object(prepara_pc, "TLC_SHA256", self.jar_sha), mock.patch.object(
            prepara_pc, "sistema", lambda: "macos"
        ):
            return prepara_pc.comprobar(self.base)

    def test_comprobar_vacio_no_toca_nada_y_nombra_las_cinco_faltas(self):
        antes = sorted(str(p.relative_to(self.base)) for p in self.base.rglob("*"))
        filas = self._comprobar()
        despues = sorted(str(p.relative_to(self.base)) for p in self.base.rglob("*"))
        self.assertEqual(antes, despues)
        self.assertEqual({f["estado"] for f in filas}, {"falta"})
        self.assertEqual(
            {f["paso"] for f in filas},
            {"java", "tla2tools", "entorno", "capacidades", "evidencia-ci"},
        )

    def test_instalar_deja_java_jar_capacidades_y_entorno_del_servicio(self):
        filas = self._instalar()
        estados = {f["paso"]: f["estado"] for f in filas}
        for paso in ("java", "tla2tools", "capacidades"):
            self.assertEqual(estados[paso], "hecho", paso)
        self.assertIn(estados["entorno"], ("ok", "hecho"))
        java = prepara_pc.java_instalado(self.base)
        self.assertEqual(java["release"], RELEASE_FALSA)
        self.assertTrue(Path(java["java"]).is_file())
        actual = prepara_pc.herramientas(self.base) / "java" / "actual" / "bin" / "java"
        self.assertTrue(actual.is_file())
        self.assertEqual(prepara_pc.sha256_de(prepara_pc.jar_tlc(self.base)), self.jar_sha)
        deseado = prepara_pc.capacidades_deseadas(self.base)
        self.assertEqual(
            json.loads((self.base / "capacidades.json").read_text(encoding="utf-8")),
            deseado,
        )
        self.assertTrue((self.base / "evidencia-ci").is_dir())
        self.assertEqual(
            self.secreto.read_text(encoding="utf-8"), "MOTOR_CANARIO=no-borrar\n"
        )

    def test_segunda_corrida_es_idempotente_y_no_descarga(self):
        self._instalar()
        antes = (self.base / "capacidades.json").read_bytes()
        descargas = []

        def descargador(url):
            descargas.append(url)
            return self.descargador(url)

        filas = self._instalar(descargador=descargador)
        self.assertEqual({f["estado"] for f in filas}, {"ok"})
        self.assertEqual(descargas, [])
        self.assertEqual((self.base / "capacidades.json").read_bytes(), antes)

    def test_comprobar_despues_de_instalar_queda_todo_ok(self):
        self._instalar()
        self.assertEqual({f["estado"] for f in self._comprobar()}, {"ok"})

    def test_conserva_los_recursos_que_el_operador_ya_declaro(self):
        propio = {"mi-base": {"tipo": "sqlite", "ruta": str(self.base / "x.db")}}
        (self.base / "capacidades.json").write_text(
            json.dumps({"recursos": propio}), encoding="utf-8"
        )
        self._instalar()
        datos = json.loads((self.base / "capacidades.json").read_text(encoding="utf-8"))
        self.assertEqual(datos["recursos"]["mi-base"], propio["mi-base"])
        for nombre, recurso in prepara_pc.capacidades_deseadas(self.base)["recursos"].items():
            self.assertEqual(datos["recursos"][nombre], recurso)

    def test_repreparar_quita_repo_workflow_y_base_fijos_y_conserva_lo_del_operador(self):
        (self.base / "capacidades.json").write_text(
            json.dumps(
                {
                    "recursos": {
                        "ci-linux": {
                            "tipo": "ci",
                            "repo": "gon0801/goncloud-motor",
                            "workflow": "pruebas.yml",
                            "base": "main",
                            "nota": "la puso el operador",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        prepara_pc.instalar_capacidades(self.base)

        ci = json.loads(
            (self.base / "capacidades.json").read_text(encoding="utf-8")
        )["recursos"]["ci-linux"]
        self.assertNotIn("repo", ci)
        self.assertNotIn("workflow", ci)
        self.assertNotIn("base", ci)
        self.assertEqual(ci["nota"], "la puso el operador")

    def test_el_sha256_del_jar_es_el_que_fija_verificar_sh(self):
        texto = (RAIZ / "verificacion" / "inspeccion" / "verificar.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"sha256:{prepara_pc.TLC_SHA256}", texto)

    def test_la_api_de_temurin_para_mac_pide_la_25_de_mac(self):
        with mock.patch.object(prepara_pc, "sistema", lambda: "macos"):
            url = prepara_pc.temurin_api()
        self.assertIn("/latest/25/", url)
        self.assertIn("os=mac", url)

    def test_main_comprobar_sale_distinto_de_cero_con_faltas(self):
        with mock.patch.object(prepara_pc, "sistema", lambda: "macos"), mock.patch(
            "sys.argv", ["prepara_pc.py", "--base", str(self.base), "--comprobar", "--json"]
        ):
            self.assertEqual(prepara_pc.main(), 1)


@unittest.skipUnless(os.name == "posix", "verificar.sh corre con bash POSIX")
class VerificarHallaLasHerramientasSinEntorno(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="verifica-mac-"))
        self.addCleanup(_limpiar, self.tmp)
        self.base = self.tmp / ".motor-encargos"
        self.base.mkdir()

    def test_verificar_sh_usa_la_base_sin_tla2tools_ni_java(self):
        herramientas = prepara_pc.herramientas(self.base)
        jar = herramientas / "tla2tools" / prepara_pc.TLC_VERSION / "tla2tools.jar"
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b"jar")
        java = herramientas / "java" / "actual" / "bin" / "java"
        java.parent.mkdir(parents=True)
        registro = self.tmp / "argumentos.txt"
        java.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" > " + str(registro) + "\nexit 0\n",
            encoding="utf-8",
        )
        java.chmod(0o755)
        entorno = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.tmp / "home"),
            "MOTOR_BASE": str(self.base),
        }
        (self.tmp / "home").mkdir()
        r = subprocess.run(
            ["/bin/bash", str(RAIZ / "verificacion" / "inspeccion" / "verificar.sh")],
            cwd=str(RAIZ),
            env=entorno,
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("tlc2.TLC", registro.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
