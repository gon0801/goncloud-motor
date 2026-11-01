"""motor director capacidades reporta una máquina completa y una incompleta.

Todas las sondas corren con dobles (un `correr` y un `which` de memoria y
carpetas temporales), así que el reporte no depende del host que corre la
batería. Ninguna prueba imprime un valor de secretos.
"""

import io
import json
import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor import capacidades_maquina as cm
from motor.integraciones import cli


def _conf_de_prueba(raiz: Path) -> tuple[Path, Path]:
    """(home, base): motor home con maquina.toml, conf y secretos; base con recursos."""
    home = raiz / ".motor"
    conf = raiz / "conf"
    (conf / "reglas").mkdir(parents=True)
    (conf / "papeles.toml").write_text('[papeles]\nimplementar = "claro"\n', encoding="utf-8")
    (conf / "modelos.toml").write_text('[carriles]\nclaro = ["falso"]\n', encoding="utf-8")
    (conf / "rutas.toml").write_text(
        '[rutas.falso]\ncuenta = "yo"\ncli = "falso"\nargv = ["falso", "{brief}"]\n',
        encoding="utf-8",
    )
    (conf / "general.toml").write_text(
        '[avisos]\nchat = "@david"\ntoken_env = "MOTOR_TELEGRAM_TOKEN"\n',
        encoding="utf-8",
    )
    home.mkdir()
    (home / "maquina.toml").write_text(
        f'nombre = "maquina-de-prueba"\nconf = "{conf.as_posix()}"\n', encoding="utf-8"
    )
    secretos = home / "secretos.env"
    secretos.write_text(
        "MOTOR_TELEGRAM_TOKEN=token-secreto-123\n"
        "MOTOR_KUMA_URL=http://10.0.0.9/api/push/abc123\n",
        encoding="utf-8",
    )
    if os.name == "posix":
        os.chmod(secretos, 0o600)
    base = raiz / ".motor-encargos"
    base.mkdir()
    (base / "capacidades.json").write_text(json.dumps({
        "recursos": {
            "ci-github": {
                "tipo": "remoto", "transporte": "fichero",
                "raiz": (base / "evidencia-ci").as_posix(),
            },
            "ci-linux": {
                "tipo": "ci", "repo": "gon0801/goncloud-motor",
                "workflow": "pruebas.yml", "base": "main",
            },
        }
    }), encoding="utf-8")
    (base / "evidencia-ci").mkdir()
    return home, base


class Dobles:
    """Un `correr` que responde como bwrap, gh, java y los CLIs de agentes."""

    def __init__(self, gh_ok=True, jar=True):
        self.jar = jar
        self.gh_ok = gh_ok

    def correr(self, argv, timeout=20):
        class R:
            returncode = 0
            stdout = b""
            stderr = b""

        r = R()
        programa = Path(argv[0]).name
        if programa == "bwrap":
            r.stdout = b"bubblewrap 0.9.0\n"
        elif programa == "gh":
            if not self.gh_ok:
                r.returncode = 1
                r.stderr = b"HTTP 403: recurso no alcanzable"
            else:
                r.stdout = b'{"nameWithOwner": "gon0801/goncloud-motor"}'
        elif programa == "java":
            if not self.jar:
                r.returncode = 1
            else:
                r.stderr = b'openjdk version "25.0.5" 2025-10-21\n'
        elif programa in ("codex", "grok", "muse", "kimi", "claude", "opencode", "qwen"):
            r.stdout = f"{programa} 1.2.3\n".encode()
        return r

    def which(self, nombre):
        if nombre == "gh" and not self.gh_ok:
            return None
        if nombre in ("codex", "grok", "muse", "kimi", "claude", "opencode", "qwen", "gh"):
            return f"/falso/bin/{nombre}"
        return None


class ReporteCompletoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cap-maquina-"))
        self.addCleanup(_limpiar, self.tmp)
        self.home, self.base = _conf_de_prueba(self.tmp)

    def _reporte(self, dobles=None):
        dobles = dobles or Dobles()
        secciones_reales = (cm._jaula, cm._arranque)
        with mock.patch.dict(
            os.environ,
            {"TLA2TOOLS": str(self.base / "tla2tools.jar"), "JAVA": "/falso/bin/java"},
        ):
            (self.base / "tla2tools.jar").write_bytes(b"jar")
            with mock.patch.object(cm, "_jaula", lambda *a, **k: {
                "mecanismo": "seatbelt", "estado": "ok", "detalle": "seatbelt listo",
            }), mock.patch.object(cm, "_arranque", lambda *a, **k: {
                "mecanismo": "launchd", "estado": "ok", "detalle": "plist con KeepAlive",
            }):
                return cm.reporte(self.base, self.home, dobles.correr, dobles.which)

    def test_maquina_completa_no_tiene_faltas(self):
        datos = self._reporte()
        self.assertEqual(datos["faltas"], [])
        self.assertTrue(datos["ok"])
        self.assertEqual(datos["maquina"]["nombre"], "maquina-de-prueba")
        self.assertIn(datos["maquina"]["sistema"], ("macos", "linux", "windows"))
        for clave in ("jaula", "java_tlc", "avisos", "senal", "recursos", "agentes", "arranque"):
            self.assertEqual(datos[clave]["estado"], "ok", clave)
        agentes = {f["agente"]: f["estado"] for f in datos["agentes"]["filas"]}
        self.assertEqual(set(agentes), {"claude", "codex", "grok", "kimi", "muse", "opencode", "qwen"})

    def test_nunca_imprime_un_valor_de_secretos(self):
        for formato in ("texto", "json"):
            datos = self._reporte()
            salida = io.StringIO()
            with contextlib.redirect_stdout(salida):
                if formato == "json":
                    print(json.dumps(datos, ensure_ascii=False))
                else:
                    print(cm.texto(datos))
            texto = salida.getvalue()
            self.assertNotIn("token-secreto-123", texto)
            self.assertNotIn("http://10.0.0.9/api/push/abc123", texto)
            self.assertIn("MOTOR_KUMA_URL", texto)

    def test_texto_es_legible_y_nombra_las_faltas(self):
        dobles = Dobles(gh_ok=False)
        datos = self._reporte(dobles)
        self.assertFalse(datos["ok"])
        self.assertTrue(any("ci-linux" in f for f in datos["faltas"]))
        self.assertIn("faltas:", cm.texto(datos))


class ReporteIncompletoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cap-maquina-"))
        self.addCleanup(_limpiar, self.tmp)
        self.base = self.tmp / ".motor-encargos"
        self.base.mkdir()
        self.home = self.tmp / ".motor-vacia"
        self.home.mkdir()

    def _reporte(self, dobles):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(cm, "_jaula", lambda *a, **k: {
                "mecanismo": "seatbelt", "estado": "falta", "detalle": "falta sandbox-exec",
            }), mock.patch.object(cm, "_arranque", lambda *a, **k: {
                "mecanismo": "launchd", "estado": "falta", "detalle": "sin plist",
            }):
                return cm.reporte(self.base, self.home, dobles.correr, dobles.which)

    def test_base_vacia_y_home_vacio_acumula_faltas(self):
        datos = self._reporte(Dobles(gh_ok=False))
        self.assertFalse(datos["ok"])
        texto = " ".join(datos["faltas"])
        for esperado in ("recursos", "java_tlc", "avisos", "senal", "arranque", "jaula"):
            self.assertIn(esperado, texto)
        self.assertIn("sin capacidades.json", datos["recursos"]["detalle"])

    def test_capacidades_json_roto_es_falta_con_causa(self):
        (self.base / "capacidades.json").write_text("{roto", encoding="utf-8")
        datos = self._reporte(Dobles())
        recursos = datos["recursos"]
        self.assertEqual(recursos["estado"], "falta")
        self.assertIn("no se puede leer", recursos["detalle"])

    def test_recurso_ci_sin_gh_o_inalcanzable_es_falta(self):
        (self.base / "capacidades.json").write_text(json.dumps({
            "recursos": {"ci-linux": {
                "tipo": "ci", "repo": "gon0801/goncloud-motor",
                "workflow": "pruebas.yml", "base": "main",
            }}
        }), encoding="utf-8")
        sin_gh = self._reporte(Dobles(gh_ok=False))
        self.assertEqual(sin_gh["recursos"]["estado"], "falta")
        dobles = Dobles()
        dobles.gh_ok = False
        with mock.patch.dict(os.environ, {}, clear=True):
            datos = cm.reporte(self.base, self.home, dobles.correr, dobles.which)
        fila = next(f for f in datos["recursos"]["filas"] if f["recurso"] == "ci-linux")
        self.assertEqual(fila["estado"], "falta")
        self.assertIn("gh", fila["detalle"])


class SeccionesPorSistemaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cap-secciones-"))
        self.addCleanup(_limpiar, self.tmp)

    def test_jaula_windows_con_distro_wsl(self):
        dobles = Dobles()

        def correr(argv, timeout=20):
            class R:
                returncode = 0
                stdout = "Ubuntu\r\n\x00".encode("utf-16-le")
                stderr = b""

            return R()

        with mock.patch.object(cm, "sistema", lambda: "windows"):
            jaula = cm._jaula(correr)
        self.assertEqual((jaula["estado"], jaula["mecanismo"]), ("ok", "wsl"))

    def test_jaula_windows_sin_distro_es_falta(self):
        def correr(argv, timeout=20):
            class R:
                returncode = 1
                stdout = "Windows Subsystem for Linux has no installed distributions.".encode("utf-16-le")
                stderr = b""

            return R()

        with mock.patch.object(cm, "sistema", lambda: "windows"):
            jaula = cm._jaula(correr)
        self.assertEqual(jaula["estado"], "falta")
        self.assertIn("no installed", jaula["detalle"])

    def test_arranque_windows_llave_del_vigilante(self):
        vigia = Path(__file__).resolve().parents[1] / "ops" / "iniciar_encargos_pc.py"

        def correr(argv, timeout=20):
            class R:
                returncode = 0
                stdout = (
                    f"\n    {cm.NOMBRE_LLAVE}    REG_SZ    "
                    f'"C:\\\\py\\\\python.exe" -B "{vigia}"\n'
                ).encode("utf-8")
                stderr = b""

            return R()

        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(correr)
        self.assertEqual((arranque["estado"], arranque["mecanismo"]), ("ok", "vigilante"))

    def test_arranque_windows_con_vigilante_inexistente_es_falta(self):
        def correr(argv, timeout=20):
            class R:
                returncode = 0
                stdout = (
                    f"\n    {cm.NOMBRE_LLAVE}    REG_SZ    "
                    '"C:\\\\py\\\\python.exe" -B "C:\\\\borrado\\\\iniciar_encargos_pc.py"\n'
                ).encode("utf-8")
                stderr = b""

            return R()

        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(correr)
        self.assertEqual(arranque["estado"], "falta")

    def test_arranque_windows_con_supervisor_directo_es_falta(self):
        def correr(argv, timeout=20):
            class R:
                returncode = 0
                stdout = (
                    f"\n    {cm.NOMBRE_LLAVE}    REG_SZ    "
                    '"C:\\\\...\\\\powershell.exe" -File "C:\\\\u\\\\.motor-encargos\\\\supervisor.ps1"\n'
                ).encode("utf-8")
                stderr = b""

            return R()

        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(correr)
        self.assertEqual(arranque["estado"], "falta")
        self.assertIn("supervisor.ps1", arranque["detalle"])

    def test_arranque_launchd_con_keepalive(self):
        import plistlib

        dir_fake = self.tmp / "LaunchDaemons"
        dir_fake.mkdir()
        (dir_fake / "com.goncloud.motor.plist").write_bytes(
            plistlib.dumps({"Label": "com.goncloud.motor", "KeepAlive": True})
        )
        with mock.patch.object(cm, "sistema", lambda: "macos"):
            arranque = cm._arranque(lambda *a, **k: None, dir_fake)
        self.assertEqual((arranque["estado"], arranque["mecanismo"]), ("ok", "launchd"))

    def test_arranque_launchd_sin_keepalive_es_falta(self):
        import plistlib

        dir_fake = self.tmp / "LaunchDaemons"
        dir_fake.mkdir()
        (dir_fake / "com.goncloud.motor.plist").write_bytes(
            plistlib.dumps({"Label": "com.goncloud.motor"})
        )
        with mock.patch.object(cm, "sistema", lambda: "macos"):
            arranque = cm._arranque(lambda *a, **k: None, dir_fake)
        self.assertEqual(arranque["estado"], "falta")
        self.assertIn("sin KeepAlive", arranque["detalle"])

    def test_arranque_linux_es_aviso_y_no_falta(self):
        with mock.patch.object(cm, "sistema", lambda: "linux"):
            arranque = cm._arranque(lambda *a, **k: None)
        self.assertEqual(arranque["estado"], "aviso")


class CliTest(unittest.TestCase):
    def _correr_cli(self, datos, formato):
        salida = io.StringIO()
        argv = ["capacidades", "--base", "/tmp/la-base", "--home", "/tmp/el-home"]
        if formato == "json":
            argv.append("--json")
        with mock.patch.object(cli, "reporte_capacidades", lambda *a, **k: datos):
            with contextlib.redirect_stdout(salida):
                codigo = cli.main(argv)
        return codigo, salida.getvalue()

    def test_maquina_completa_sale_cero_y_json_o_texto(self):
        datos = {"maquina": {}, "faltas": [], "ok": True}
        codigo, texto = self._correr_cli(datos, "json")
        self.assertEqual(codigo, 0)
        self.assertEqual(json.loads(texto), datos)
        codigo, texto = self._correr_cli(datos, "texto")
        self.assertEqual(codigo, 0)

    def test_maquina_incompleta_sale_distinto_de_cero(self):
        datos = {"maquina": {}, "faltas": ["java_tlc: falta"], "ok": False}
        codigo, _ = self._correr_cli(datos, "texto")
        self.assertEqual(codigo, 1)


def _limpiar(tmp):
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
