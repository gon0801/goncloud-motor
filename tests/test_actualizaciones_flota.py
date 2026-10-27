from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from motor.flota.actualizaciones import _crear_generacion
from motor.flota.control import aplicar, asignar, revertir
from motor.flota.distribuidores import _descargar, _instalar_npm, _validar_componente


class ActualizacionesFlota(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="componentes-flota-")
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def artefacto(self):
        paquete = io.BytesIO()
        with tarfile.open(fileobj=paquete, mode="w:gz") as archivo:
            archivos = {
                "package/package.json": json.dumps(
                    {
                        "name": "openclaw",
                        "version": "0.0.0-ensayo",
                        "bin": {"openclaw": "cli.js"},
                    }
                ),
                "package/cli.js": "#!/usr/bin/env node\nconsole.log('0.0.0-ensayo')\n",
            }
            for nombre, texto in archivos.items():
                contenido = texto.encode()
                entrada = tarfile.TarInfo(nombre)
                entrada.size, entrada.mode = len(contenido), 0o755
                archivo.addfile(entrada, io.BytesIO(contenido))
        contenido = paquete.getvalue()
        sri = "sha512-" + base64.b64encode(hashlib.sha512(contenido).digest()).decode()
        doc = {
            "nombre": "openclaw",
            "tipo": "npm",
            "paquete": "openclaw",
            "version": "0.0.0-ensayo",
            "fuente": "https://registry.npmjs.org/openclaw/latest",
            "tarball": "https://registry.npmjs.org/openclaw/-/openclaw-ensayo.tgz",
            "integridad": sri,
        }
        cache = self.base / "cache"
        cache.mkdir()
        archivo = cache / (hashlib.sha256(sri.encode()).hexdigest() + ".tgz")
        archivo.write_bytes(contenido)
        return doc, cache, archivo

    def test_instala_paquete_real_en_prefijo_y_rechaza_corrupcion(self):
        # Artefacto local declarado: prueba npm real, no acredita un release upstream.
        doc, cache, archivo = self.artefacto()
        _validar_componente(doc)
        self.assertEqual(_descargar(doc, cache), archivo)
        argv = _instalar_npm(
            doc,
            archivo,
            self.base / "generacion/herramientas/openclaw",
            self.base / "npm-cache",
        )
        hecho = subprocess.run(
            [*argv, "--version"], capture_output=True, text=True, check=True
        )
        self.assertEqual(hecho.stdout.strip(), doc["version"])
        self.assertTrue(
            (
                self.base
                / "generacion/herramientas/openclaw/node_modules/openclaw/package.json"
            ).is_file()
        )
        archivo.write_bytes(b"descarga truncada")
        with self.assertRaisesRegex(ValueError, "integridad oficial"):
            _descargar(doc, cache)
        self.assertFalse(archivo.exists())

    def test_candidata_de_componentes_se_aplica_y_revierte_con_recibo(self):
        origen = self.base / "origen"
        origen.mkdir()
        for args in (
            ("init", "-q"),
            ("config", "user.email", "ensayo@localhost"),
            ("config", "user.name", "Ensayo"),
        ):
            subprocess.run(
                ["git", "-C", str(origen), *args], check=True, capture_output=True
            )
        (origen / "comprobar.py").write_text("raise SystemExit(0)\n")
        subprocess.run(["git", "-C", str(origen), "add", "."], check=True)
        subprocess.run(["git", "-C", str(origen), "commit", "-qm", "base"], check=True)
        home = self.base / "home"
        home.mkdir()
        a, _ = _crear_generacion(home, origen, [], {}, 'generacion="A"\n')
        self.assertEqual(aplicar(home, a)["accion"], "aplicada")
        b, gen = _crear_generacion(
            home,
            origen,
            [{"nombre": "herramienta", "version": "B"}],
            {},
            'generacion="B"\n',
        )
        self.assertEqual(aplicar(home, b)["accion"], "aplicada")
        self.assertEqual(asignar(home), b)
        (gen / "comprobar.py").write_text("raise SystemExit(1)\n")
        # El recibo de la instalación completa permite volver aunque B ya falle.
        self.assertEqual(revertir(home)["accion"], "revertido")
        self.assertEqual(asignar(home), a)
        self.assertTrue((home / "historial" / f"instalacion-{b}.json").is_file())


if __name__ == "__main__":
    unittest.main()
