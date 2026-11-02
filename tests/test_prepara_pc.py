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
            if clave.upper().startswith("HKCU\\"):
                # Sin la unidad HKCU: la ruta cae en el disco actual; con
                # -ErrorAction Stop el error es terminante (exit 1) y sin ella
                # no terminante (código 0, salida vacía), como PowerShell real.
                r.returncode = 1 if "-ErrorAction Stop" in guion else 0
                return r
            if "Environment" in clave:
                valor = estado["env"].get(nombre)
                if valor is not None:
                    r.stdout = valor + "\n"
                elif "-ErrorAction Stop" in guion:
                    r.returncode = 1
                return r
            if nombre == prepara_pc.NOMBRE_LLAVE and estado["llave"]:
                r.stdout = estado["llave"] + "\n"
            elif "-ErrorAction Stop" in guion:
                r.returncode = 1
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

    def _con_herramientas(self):
        prepara_pc._ruta_java_marker(self.base).parent.mkdir(parents=True, exist_ok=True)
        prepara_pc._ruta_java_marker(self.base).write_text(
            json.dumps({"release": RELEASE_FALSA, "java": str(self.tmp / "java.exe")}),
            encoding="utf-8",
        )
        Path(str(self.tmp / "java.exe")).write_bytes(b"MZ")
        prepara_pc.jar_tlc(self.base).parent.mkdir(parents=True, exist_ok=True)
        prepara_pc.jar_tlc(self.base).write_bytes(b"jar")

    def _raiz_de_verificacion(self, fin: bytes) -> Path:
        raiz = self.tmp / "repo"
        inspeccion = raiz / "verificacion" / "inspeccion"
        inspeccion.mkdir(parents=True)
        (inspeccion / "verificar.sh").write_bytes(b"#!/usr/bin/env bash" + fin)
        (inspeccion / "FlujoInspeccion.tla").write_bytes(
            b"---- MODULE FlujoInspeccion ----" + fin
        )
        (inspeccion / "main.cfg").write_bytes(b"Tope = 6" + fin)
        return raiz

    def _instalar(self, corredor, descargador=None):
        with mock.patch.object(prepara_pc, "TLC_SHA256", self.jar_sha):
            return prepara_pc.instalar(
                self.base, correr=corredor, catalogo=self.catalogo,
                descargador=descargador or self.descargador,
            )


def _limpiar(tmp):
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


class LaLlaveSeLeeConLaUnidadHkcu(DoblesTest):
    def test_el_guion_de_powershell_lleva_la_unidad_hkcu(self):
        """F1: sin 'HKCU:' la ruta cae en el disco actual y Get-ItemProperty
        falla con un error no terminante (código 0, salida vacía) que deja una
        llave bien puesta como ausente, sin respaldo por reg query."""
        capturadas = []

        def corredor(argv, timeout=prepara_pc.TIMEOUT_CORTO):
            capturadas.append(list(argv))

            class R:
                returncode = 1
                stdout = ""
                stderr = ""

            return R()

        prepara_pc._leer_registro(prepara_pc.LLAVE, prepara_pc.NOMBRE_LLAVE, corredor)
        guiones = [a[-1] for a in capturadas if a[0] == "powershell"]
        self.assertTrue(guiones)
        self.assertTrue(all("HKCU:" in g for g in guiones), guiones)


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


class LaActualizacionDeJavaCorre(DoblesTest):
    def test_un_parche_nuevo_de_temurin_actualiza_en_la_siguiente_corrida(self):
        """F2: instalar solo preguntaba a Adoptium cuando no había java; el doc
        promete que un parche nuevo de temurin 25 actualiza en la siguiente
        corrida y esa ruta era inalcanzable desde instalar()."""
        vieja = self.base / "herramientas" / "java" / "jdk-25.0.0+1"
        exe_viejo = vieja / "bin" / "java.exe"
        exe_viejo.parent.mkdir(parents=True)
        exe_viejo.write_bytes(b"MZ")
        prepara_pc._ruta_java_marker(self.base).write_text(
            json.dumps({"release": "jdk-25.0.0+1", "java": str(exe_viejo)}),
            encoding="utf-8",
        )
        zip_nuevo = _zip_de_temurin()
        catalogo = lambda: [{
            "release_name": "jdk-25.0.5+9",
            "binary": {"package": {
                "link": "https://adoptium.example/jdk-nuevo.zip",
                "checksum": sha256(zip_nuevo).hexdigest(),
            }},
        }]
        pedidos = []

        def descargador(url):
            pedidos.append(url)
            return zip_nuevo if url.endswith("jdk-nuevo.zip") else JAR_FALSO

        with mock.patch.object(prepara_pc, "TLC_SHA256", self.jar_sha):
            filas = prepara_pc.instalar(
                self.base, correr=_reg_falso(), catalogo=catalogo, descargador=descargador,
            )
        self.assertIn("https://adoptium.example/jdk-nuevo.zip", pedidos)
        java = next(f for f in filas if f["paso"] == "java")
        self.assertEqual(java["estado"], "hecho")
        self.assertIn("jdk-25.0.5+9", java["detalle"])
        self.assertEqual(prepara_pc.java_instalado(self.base)["release"], "jdk-25.0.5+9")
        self.assertFalse(exe_viejo.exists())

    def test_una_extraccion_fallida_deja_la_jdk_vieja_utilizable(self):
        """F7: la actualización borraba la jdk vieja antes de extraer la
        nueva; si la extracción fallaba (disco lleno, zip a medias), la
        máquina quedaba sin java utilizable y sin marker."""
        vieja = self.base / "herramientas" / "java" / "jdk-25.0.0+1"
        exe_viejo = vieja / "bin" / "java.exe"
        exe_viejo.parent.mkdir(parents=True)
        exe_viejo.write_bytes(b"MZ")
        prepara_pc._ruta_java_marker(self.base).write_text(
            json.dumps({"release": "jdk-25.0.0+1", "java": str(exe_viejo)}),
            encoding="utf-8",
        )
        zip_nuevo = _zip_de_temurin()
        catalogo = lambda: [{
            "release_name": "jdk-25.0.5+9",
            "binary": {"package": {
                "link": "https://adoptium.example/jdk-nuevo.zip",
                "checksum": sha256(zip_nuevo).hexdigest(),
            }},
        }]

        def descargador(url):
            return zip_nuevo if url.endswith("jdk-nuevo.zip") else JAR_FALSO

        with mock.patch.object(
            prepara_pc, "_extraer_zip", mock.Mock(side_effect=OSError("disco lleno"))
        ), mock.patch.object(prepara_pc, "TLC_SHA256", self.jar_sha):
            filas = prepara_pc.instalar(
                self.base, correr=_reg_falso(), catalogo=catalogo, descargador=descargador,
            )
        java = next(f for f in filas if f["paso"] == "java")
        self.assertEqual(java["estado"], "error")
        self.assertTrue(exe_viejo.exists())
        self.assertEqual(prepara_pc.java_instalado(self.base)["release"], "jdk-25.0.0+1")

    def test_misma_release_no_descarga_de_nuevo(self):
        self._con_herramientas()
        prepara_pc.jar_tlc(self.base).write_bytes(JAR_FALSO)
        descargas = []

        def descargador(url):
            descargas.append(url)
            return self.descargador(url)

        with mock.patch.object(prepara_pc, "TLC_SHA256", self.jar_sha):
            filas = prepara_pc.instalar(
                self.base, correr=_reg_falso(), catalogo=self.catalogo,
                descargador=descargador,
            )
        java = next(f for f in filas if f["paso"] == "java")
        self.assertEqual(java["estado"], "ok")
        self.assertEqual(descargas, [])

    def test_sin_poder_consultar_la_api_el_java_instalado_sigue_ok(self):
        self._con_herramientas()

        def catalogo():
            raise OSError("sin red")

        with mock.patch.object(prepara_pc, "TLC_SHA256", self.jar_sha):
            filas = prepara_pc.instalar(
                self.base, correr=_reg_falso(), catalogo=catalogo,
                descargador=self.descargador,
            )
        java = next(f for f in filas if f["paso"] == "java")
        self.assertEqual(java["estado"], "ok")
        self.assertIn("consultar", java["detalle"])


class CapacidadesNoPisaLoDelOperador(DoblesTest):
    def _propios(self):
        return {"mi-base": {"tipo": "sqlite", "ruta": str(self.base / "x.db")}}

    def test_agrega_los_suyos_y_conserva_los_del_operador(self):
        """F3: instalar_capacidades escribía el archivo completo y borraba los
        recursos que el operador hubiera declarado en su base."""
        ruta = self.base / "capacidades.json"
        ruta.write_text(json.dumps({"recursos": self._propios()}), encoding="utf-8")
        estado, _ = prepara_pc.instalar_capacidades(self.base)
        self.assertEqual(estado, "hecho")
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        self.assertEqual(datos["recursos"]["mi-base"], self._propios()["mi-base"])
        esperado = prepara_pc.capacidades_deseadas(self.base)["recursos"]
        for nombre, recurso in esperado.items():
            self.assertEqual(datos["recursos"][nombre], recurso)

    def test_comprobar_da_ok_con_recursos_extra_del_operador(self):
        esperado = prepara_pc.capacidades_deseadas(self.base)["recursos"]
        combinado = {"recursos": {**esperado, **self._propios()}}
        (self.base / "capacidades.json").write_text(
            json.dumps(combinado), encoding="utf-8"
        )
        (self.base / "evidencia-ci").mkdir()
        filas = prepara_pc.comprobar(self.base, correr=_reg_falso())
        capacidades = next(f for f in filas if f["paso"] == "capacidades")
        self.assertEqual(capacidades["estado"], "ok")


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
        self._con_herramientas()
        raiz = self._raiz_de_verificacion(b"\n")
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
            prepara_pc.verificar("main.cfg", self.base, raiz, bash=con_wsl)
            self.assertEqual(capturadas[-1][0], "git-bash.exe")
            prepara_pc.verificar("main.cfg", self.base, raiz, bash="bash-real.exe")
            self.assertEqual(capturadas[-1][0], "bash-real.exe")
            self.assertTrue(capturadas[-1][1].replace("\\", "/").endswith("verificar.sh"))
            self.assertEqual(capturadas[-1][2], "main.cfg")


class VerificarConCrlf(DoblesTest):
    def _correr_con(self, fin: bytes) -> tuple[Path, list[list]]:
        self._con_herramientas()
        raiz = self._raiz_de_verificacion(fin)
        capturadas = []

        def falso_run(argv, **kwargs):
            capturadas.append(list(argv))

            class R:
                returncode = 0
                stdout = ""
                stderr = ""

            return R()

        inspeccion = raiz / "verificacion" / "inspeccion"
        with mock.patch.object(prepara_pc.subprocess, "run", falso_run):
            prepara_pc.verificar("main.cfg", self.base, raiz, bash="bash-real.exe")
        return inspeccion, capturadas

    def test_un_checkout_crlf_queda_normalizado_antes_de_correr_bash(self):
        inspeccion, capturadas = self._correr_con(b"\r\n")
        for nombre in ("verificar.sh", "FlujoInspeccion.tla", "main.cfg"):
            crudo = (inspeccion / nombre).read_bytes()
            self.assertNotIn(b"\r\n", crudo, nombre)
            self.assertTrue(crudo.endswith(b"\n"), nombre)
        self.assertEqual(capturadas[-1][0], "bash-real.exe")

    def test_lo_que_ya_esta_en_lf_no_se_toca(self):
        inspeccion, _ = self._correr_con(b"\n")
        antes = {n: (inspeccion / n).read_bytes() for n in ("verificar.sh", "FlujoInspeccion.tla", "main.cfg")}
        despues = {n: (inspeccion / n).read_bytes() for n in antes}
        self.assertEqual(antes, despues)

    def test_la_normalizacion_es_idempotente(self):
        ruta = self.tmp / "uno.tla"
        ruta.write_bytes(b"a\r\nb\r\n")
        self.assertTrue(prepara_pc._a_lf(ruta))
        self.assertEqual(ruta.read_bytes(), b"a\nb\n")
        self.assertFalse(prepara_pc._a_lf(ruta))
        self.assertEqual(ruta.read_bytes(), b"a\nb\n")

    def test_un_archivo_ausente_no_detiene_la_verificacion(self):
        self._con_herramientas()
        raiz = self._raiz_de_verificacion(b"\r\n")
        (raiz / "verificacion" / "inspeccion" / "main.cfg").unlink()
        capturadas = []

        def falso_run(argv, **kwargs):
            capturadas.append(list(argv))

            class R:
                returncode = 0
                stdout = ""
                stderr = ""

            return R()

        with mock.patch.object(prepara_pc.subprocess, "run", falso_run):
            self.assertEqual(prepara_pc.verificar("main.cfg", self.base, raiz, bash="bash-real.exe"), 0)
        self.assertTrue((raiz / "verificacion" / "inspeccion" / "FlujoInspeccion.tla").read_bytes().endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
