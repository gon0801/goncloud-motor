"""ops/prepara_pc.py deja la PC lista y su modo comprobación dice qué falta.

Todo corre con dobles: un `correr` que responde lo que reg.exe y setx
responderían, un descargador que sirve bytes de prueba y un catálogo Adoptium
falso. Nada toca el registro real ni la red; la carpeta base es temporal.
"""

import io
import json
import tempfile
import unittest
import zipfile
from hashlib import sha256
from pathlib import Path
from unittest import mock

from ops import prepara_pc

RELEASE_FALSA = "jdk-25.0.5+8"
JAR_FALSO = b"PK jar de prueba"
ZIP_BYTES = _zip_bytes = None


def _zip_de_temurin() -> bytes:
    memoria = io.BytesIO()
    with zipfile.ZipFile(memoria, "w") as z:
        z.writestr(f"{RELEASE_FALSA}/bin/java.exe", "MZ java falso")
        z.writestr(f"{RELEASE_FALSA}/release", 'JAVA_VERSION="25.0.5"')
    return memoria.getvalue()


ZIP_BYTES = _zip_de_temurin()
CATALOGO_FALSO = [{
    "release_name": RELEASE_FALSA,
    "binary": {"package": {
        "link": "https://adoptium.example/jdk.zip",
        "checksum": sha256(ZIP_BYTES).hexdigest(),
    }},
}]
LLAVE_SUPERVISOR_DIRECTO = (
    '"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
    '-File "C:\\Users\\ehven\\.motor-encargos\\supervisor.ps1"'
)


def _reg_falso(entorno=None, llave="", sin_powershell=False):
    """Un correr que guarda HKCU\\Environment y la llave Run en memoria, con la
    misma cara que le toca a cada herramienta: PowerShell para leer el registro
    (con reg.exe de respaldo) y setx/reg add para escribir."""
    import re as _re

    estado = {"env": dict(entorno or {}), "llave": llave, "setx": [], "reg_add": []}

    def correr(argv, timeout=prepara_pc.TIMEOUT_CORTO):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        r = R()
        if argv[0] == "powershell":
            if sin_powershell:
                raise FileNotFoundError("powershell")
            guion = argv[-1]
            clave = _re.search(r"-LiteralPath '([^']+)'", guion).group(1)
            nombre = _re.search(r"\$p\.(\w+);", guion).group(1)
            if "Environment" in clave:
                valor = estado["env"].get(nombre)
                if valor is not None:
                    r.stdout = valor + "\n"
                return r
            if nombre == prepara_pc.NOMBRE_LLAVE and estado["llave"]:
                r.stdout = estado["llave"] + "\n"
            return r
        if argv[:2] == ["reg", "query"] and "Environment" in argv[2]:
            nombre = argv[-1]
            valor = estado["env"].get(nombre)
            if valor is None:
                r.returncode = 1
            else:
                r.stdout = f"\n    {nombre}    REG_SZ    {valor}\n"
        elif argv[:2] == ["reg", "query"]:
            if not estado["llave"]:
                r.returncode = 1
            else:
                r.stdout = f"\n    {prepara_pc.NOMBRE_LLAVE}    REG_SZ    {estado['llave']}\n"
        elif argv[:2] == ["reg", "add"]:
            estado["reg_add"].append(list(argv))
            estado["llave"] = argv[argv.index("/d") + 1]
        elif argv[:1] == ["setx"]:
            estado["setx"].append(list(argv))
            estado["env"][argv[1]] = argv[2]
        return r

    correr.estado = estado
    return correr


class DoblesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prepara-pc-"))
        self.addCleanup(_limpiar, self.tmp)
        self.base = self.tmp / ".motor-encargos"
        self.base.mkdir()
        self.secreto = self.base / "secretos.env"
        self.secreto.write_text("MOTOR_CANARIO=no-borrar\n", encoding="utf-8")
        self.catalogo = lambda: json.loads(json.dumps(CATALOGO_FALSO))
        self.jar_sha = sha256(JAR_FALSO).hexdigest()

    def descargador(self, url):
        return ZIP_BYTES if url.endswith("jdk.zip") else JAR_FALSO

    def _instalar(self, corredor, descargador=None):
        with mock.patch.object(prepara_pc, "TLC_SHA256", self.jar_sha):
            return prepara_pc.instalar(
                self.base, correr=corredor, catalogo=self.catalogo,
                descargador=descargador or self.descargador,
            )


def _limpiar(tmp):
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


class ModoComprobacionDetectaLoQueFalta(DoblesTest):
    def test_base_vacia_reporta_seis_faltas_y_no_toca_nada(self):
        antes = sorted(str(p.relative_to(self.base)) for p in self.base.rglob("*"))
        filas = prepara_pc.comprobar(self.base, correr=_reg_falso())
        despues = sorted(str(p.relative_to(self.base)) for p in self.base.rglob("*"))
        self.assertEqual(antes, despues)
        self.assertEqual({f["estado"] for f in filas}, {"falta"})
        self.assertEqual(
            {f["paso"] for f in filas},
            {"java", "tla2tools", "entorno", "capacidades", "evidencia-ci", "llave-run"},
        )

    def test_llave_con_supervisor_directo_es_falta(self):
        filas = prepara_pc.comprobar(
            self.base, correr=_reg_falso(llave=LLAVE_SUPERVISOR_DIRECTO)
        )
        llave = next(f for f in filas if f["paso"] == "llave-run")
        self.assertEqual(llave["estado"], "falta")
        self.assertIn("supervisor.ps1", llave["detalle"])

    def test_main_comprobar_sale_distinto_de_cero_con_faltas(self):
        with mock.patch.object(prepara_pc, "correr", _reg_falso()):
            with mock.patch(
                "sys.argv",
                ["prepara_pc.py", "--base", str(self.base), "--comprobar", "--json"],
            ):
                self.assertEqual(prepara_pc.main(), 1)


class InstalacionDejaLoPrometido(DoblesTest):
    def test_queda_java_jar_capacidades_evidencia_y_llave_del_vigilante(self):
        filas = self._instalar(_reg_falso(llave=LLAVE_SUPERVISOR_DIRECTO))
        self.assertEqual({f["estado"] for f in filas}, {"hecho"})
        java = prepara_pc.java_instalado(self.base)
        self.assertEqual(java["release"], RELEASE_FALSA)
        self.assertTrue(Path(java["java"]).is_file())
        self.assertEqual(prepara_pc.sha256_de(prepara_pc.jar_tlc(self.base)), self.jar_sha)
        deseado = {
            "recursos": {
                "ci-github": {
                    "tipo": "remoto",
                    "transporte": "fichero",
                    "raiz": (self.base / "evidencia-ci").resolve().as_posix(),
                },
                "ci-linux": {
                    "tipo": "ci",
                    "repo": "gon0801/goncloud-motor",
                    "workflow": "pruebas.yml",
                    "base": "main",
                },
            }
        }
        self.assertEqual(
            json.loads((self.base / "capacidades.json").read_text(encoding="utf-8")),
            deseado,
        )
        self.assertTrue((self.base / "evidencia-ci").is_dir())
        self.assertEqual(
            self.secreto.read_text(encoding="utf-8"), "MOTOR_CANARIO=no-borrar\n"
        )

    def test_la_llave_queda_apuntando_al_vigilante_y_supervisor_ps1_no_aparece(self):
        corredor = _reg_falso(llave=LLAVE_SUPERVISOR_DIRECTO)
        self._instalar(corredor)
        self.assertEqual(len(corredor.estado["reg_add"]), 1)
        argv = corredor.estado["reg_add"][0]
        valor = argv[argv.index("/d") + 1]
        self.assertIn("iniciar_encargos_pc.py", valor)
        self.assertNotIn("supervisor.ps1", valor)
        script = prepara_pc._script_de_la_llave(valor)
        self.assertIsNotNone(script)
        self.assertTrue(script.is_file())

    def test_segunda_corrida_no_cambia_nada(self):
        corredor = _reg_falso(llave=LLAVE_SUPERVISOR_DIRECTO)
        self._instalar(corredor)
        capacidades_antes = (self.base / "capacidades.json").read_bytes()
        marker_antes = prepara_pc._ruta_java_marker(self.base).read_bytes()
        mutantes_antes = len(corredor.estado["setx"]) + len(corredor.estado["reg_add"])
        descargas = []

        def descargador(url):
            descargas.append(url)
            return self.descargador(url)

        with mock.patch.object(prepara_pc, "TLC_SHA256", self.jar_sha):
            filas = prepara_pc.instalar(
                self.base, correr=corredor, catalogo=self.catalogo,
                descargador=descargador,
            )
        self.assertEqual({f["estado"] for f in filas}, {"ok"})
        self.assertEqual(descargas, [])
        self.assertEqual(
            len(corredor.estado["setx"]) + len(corredor.estado["reg_add"]),
            mutantes_antes,
        )
        self.assertEqual((self.base / "capacidades.json").read_bytes(), capacidades_antes)
        self.assertEqual(prepara_pc._ruta_java_marker(self.base).read_bytes(), marker_antes)


class SumasYVerificacion(DoblesTest):
    def test_un_jar_con_otra_suma_que_fija_verificar_sh_no_se_instala(self):
        filas = prepara_pc.instalar(
            self.base,
            correr=_reg_falso(),
            catalogo=self.catalogo,
            descargador=lambda url: ZIP_BYTES if url.endswith("jdk.zip") else b"otro jar",
        )
        tla = next(f for f in filas if f["paso"] == "tla2tools")
        self.assertEqual(tla["estado"], "error")
        self.assertFalse(prepara_pc.jar_tlc(self.base).exists())
        self.assertIn(prepara_pc.TLC_SHA256, tla["detalle"])

    def test_un_zip_con_otra_suma_que_la_api_declara_no_se_instala(self):
        filas = prepara_pc.instalar(
            self.base,
            correr=_reg_falso(),
            catalogo=self.catalogo,
            descargador=lambda url: b"zip corrupto" if url.endswith("jdk.zip") else JAR_FALSO,
        )
        java = next(f for f in filas if f["paso"] == "java")
        self.assertEqual(java["estado"], "error")
        self.assertIsNone(prepara_pc.java_instalado(self.base))

    def test_verificar_sin_herramientas_no_corre_bash(self):
        self.assertEqual(prepara_pc.verificar("main.cfg", self.base, self.tmp), 2)

    def test_verificar_no_usa_el_bash_de_wsl(self):
        prepara_pc._ruta_java_marker(self.base).parent.mkdir(parents=True, exist_ok=True)
        prepara_pc._ruta_java_marker(self.base).write_text(
            json.dumps({"release": RELEASE_FALSA, "java": str(self.tmp / "java.exe")}),
            encoding="utf-8",
        )
        Path(str(self.tmp / "java.exe")).write_bytes(b"MZ")
        prepara_pc.jar_tlc(self.base).parent.mkdir(parents=True, exist_ok=True)
        prepara_pc.jar_tlc(self.base).write_bytes(b"jar")
        capturadas = []

        def falso_run(argv, **kwargs):
            capturadas.append(list(argv))

            class R:
                returncode = 0
                stdout = ""
                stderr = ""

            return R()

        con_wsl = r"C:\Windows\System32\bash.exe"
        con_mock = mock.patch.object(prepara_pc.subprocess, "run", falso_run)
        with con_mock, mock.patch.object(prepara_pc, "BASH_POR_OMISION", "git-bash.exe"):
            prepara_pc.verificar("main.cfg", self.base, self.tmp, bash=con_wsl)
            self.assertEqual(capturadas[-1][0], "git-bash.exe")
            prepara_pc.verificar("main.cfg", self.base, self.tmp, bash="bash-real.exe")
            self.assertEqual(capturadas[-1][0], "bash-real.exe")


if __name__ == "__main__":
    unittest.main()
