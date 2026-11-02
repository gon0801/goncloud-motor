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


@contextlib.contextmanager
def _puertas_de_windows():
    """Las puertas de Windows con dobles deterministas: sin ellas, en un host
    Windows el reporte consultaría wsl.exe y el registro de verdad y las filas
    de agentes dependerían del host (F3)."""
    with mock.patch.object(cm, "_usa_wsl", lambda agente: False), mock.patch.object(
        cm, "_psec_disponible", lambda: True
    ), mock.patch.object(cm, "_jaula_windows_disponible", lambda: True), mock.patch.object(
        cm, "_causa_jaula_wsl", lambda agente: ""
    ):
        yield


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
                "mecanismo": "directo", "estado": "ok", "detalle": "lanzamiento directo listo",
            }), mock.patch.object(cm, "_arranque", lambda *a, **k: {
                "mecanismo": "launchd", "estado": "ok", "detalle": "plist con KeepAlive",
            }), _puertas_de_windows():
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
                "mecanismo": "directo", "estado": "falta", "detalle": "sin lanzamiento de trabajadores",
            }), mock.patch.object(cm, "_arranque", lambda *a, **k: {
                "mecanismo": "launchd", "estado": "falta", "detalle": "sin plist",
            }), _puertas_de_windows():
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
        with mock.patch.dict(os.environ, {}, clear=True), _puertas_de_windows():
            datos = cm.reporte(self.base, self.home, dobles.correr, dobles.which)
        fila = next(f for f in datos["recursos"]["filas"] if f["recurso"] == "ci-linux")
        self.assertEqual(fila["estado"], "falta")
        self.assertIn("gh", fila["detalle"])


class SeccionesPorSistemaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cap-secciones-"))
        self.addCleanup(_limpiar, self.tmp)

    def _jaula_windows(self, correr, psec=True, appcontainer=False, causa_wsl=""):
        """La sección jaula de Windows con los sondeos del motor en dobles."""
        with mock.patch.object(cm, "sistema", lambda: "windows"), mock.patch.object(
            cm, "_psec_disponible", lambda: psec
        ), mock.patch.object(
            cm, "_jaula_windows_disponible", lambda: appcontainer
        ), mock.patch.object(cm, "_causa_jaula_wsl", lambda agente: causa_wsl):
            return cm._jaula(correr)

    def test_jaula_windows_con_todo_disponible_es_ok(self):
        def correr(argv, timeout=20):
            class R:
                returncode = 0
                stdout = "Ubuntu\r\n\x00".encode("utf-16-le")
                stderr = b""

            return R()

        jaula = self._jaula_windows(correr, psec=True, causa_wsl="")
        self.assertEqual(jaula["estado"], "ok")
        mecanismos = {f["mecanismo"]: f["estado"] for f in jaula["filas"]}
        self.assertEqual(mecanismos, {"psec": "ok", "wsl": "ok"})

    def test_jaula_windows_sin_distro_wsl_es_falta_con_la_causa_del_motor(self):
        def correr(argv, timeout=20):
            class R:
                returncode = 1
                stdout = "Windows Subsystem for Linux has no installed distributions.".encode("utf-16-le")
                stderr = b""

            return R()

        jaula = self._jaula_windows(
            correr, psec=True, causa_wsl="no se pudo consultar a WSL (wsl.exe no respondio)"
        )
        self.assertEqual(jaula["estado"], "aviso")
        wsl = next(f for f in jaula["filas"] if f["mecanismo"] == "wsl")
        self.assertEqual(wsl["estado"], "falta")
        self.assertIn("WSL", wsl["detalle"])

    def test_jaula_windows_sin_jaula_nativa_ni_wsl_es_falta(self):
        jaula = self._jaula_windows(lambda *a, **k: None, psec=False, appcontainer=False,
                                    causa_wsl="sin distro")
        self.assertEqual(jaula["estado"], "falta")
        self.assertIn("ni psec ni AppContainer", jaula["detalle"])

    def test_jaula_windows_solo_appcontainer_queda_aviso(self):
        jaula = self._jaula_windows(lambda *a, **k: None, psec=False, appcontainer=True,
                                    causa_wsl="sin distro WSL")
        self.assertEqual(jaula["estado"], "aviso")
        mecanismos = {f["mecanismo"]: f["estado"] for f in jaula["filas"]}
        self.assertEqual(mecanismos, {"appcontainer": "ok", "wsl": "falta"})

    def test_jaula_windows_distro_listada_no_la_da_por_buena(self):
        """Listar una distro no basta: la jaula de WSL la decide la sonda del motor."""
        def correr(argv, timeout=20):
            class R:
                returncode = 0
                stdout = "Ubuntu\r\n\x00".encode("utf-16-le")
                stderr = b""

            return R()

        jaula = self._jaula_windows(correr, psec=True, causa_wsl="sin bubblewrap en la distro")
        self.assertEqual(jaula["estado"], "aviso")
        wsl = next(f for f in jaula["filas"] if f["mecanismo"] == "wsl")
        self.assertEqual(wsl["estado"], "falta")
        self.assertIn("sin bubblewrap", wsl["detalle"])

    def test_llave_run_de_mas_de_200_caracteres_no_se_recorta(self):
        """h_ca90a1b7: un valor largo (intérprete de uv en AppData + ruta del
        repo) pasaba por _texto, que corta en 200 y colapsa espacios: la
        comilla de cierre y el nombre del vigilante se perdían y el arranque
        salía falta con la llave bien puesta."""
        carpeta = self.tmp / "larga"
        (carpeta / "ops").mkdir(parents=True)
        vigia = carpeta / "ops" / "iniciar_encargos_pc.py"
        vigia.write_bytes(b"x")
        python = "C:\\" + "a" * 150 + "\\python.exe"
        valor = f'"{python}"  -B  "{vigia}"'
        self.assertGreater(len(valor), 200)
        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(self._correr_llave(valor))
        self.assertEqual((arranque["estado"], arranque["mecanismo"]), ("ok", "vigilante"))

    def test_jaula_directa_es_ok_en_macos_aunque_falte_sandbox_exec(self):
        """Decisión del 2-nov: en las Mac los agentes corren sin jaula; el
        reporte no exige sandbox-exec."""
        es_archivo = Path.is_file

        def sin_sandbox(ruta):
            return False if str(ruta) == "/usr/bin/sandbox-exec" else es_archivo(ruta)

        with mock.patch.object(cm, "sistema", lambda: "macos"), mock.patch.object(
            Path, "is_file", sin_sandbox
        ):
            jaula = cm._jaula(lambda *a, **k: None)
        self.assertEqual((jaula["estado"], jaula["mecanismo"]), ("ok", "directo"))
        self.assertIn("sin aislamiento", jaula["detalle"])

    def test_jaula_directa_es_ok_en_linux(self):
        """Decisión del 2-nov: en Linux el motor lanza directo; ya no reporta
        falta por no aislar (bubblewrap sigue siendo jaula solo en la distro WSL)."""
        def correr(argv, timeout=20):
            raise FileNotFoundError("bwrap")

        with mock.patch.object(cm, "sistema", lambda: "linux"):
            jaula = cm._jaula(correr)
        self.assertEqual((jaula["estado"], jaula["mecanismo"]), ("ok", "directo"))
        self.assertIn("sin aislamiento", jaula["detalle"])

    def _correr_llave(self, valor, sin_powershell=False, pagina_reg="cp1252",
                      capturadas=None):
        """Un correr que responde como PowerShell (y como reg.exe de respaldo)
        sobre la llave Run; valor None es llave ausente. PowerShell sale en
        UTF-8 (OutputEncoding=UTF8, como el código lo pide) y reg.exe sale en
        una página de códigos local (cp1252): con el código que decodificaba
        reg.exe como UTF-8, un valor con acentos llegaba mangleado y el
        arranque salía falta. Un doble que codificara UTF-8 en ambos caminos
        pasaría con el código viejo y no reproduciría nada (DeepSeek F8).
        La ruta de PowerShell solo es válida con la unidad HKCU: (F1); con una
        ruta sin unidad, PowerShell real falla con error no terminante (código
        0, salida vacía) y así lo simula el doble."""

        import re as _re

        def correr(argv, timeout=20):
            class R:
                returncode = 0
                stdout = b""
                stderr = b""

            r = R()
            if argv[0] == "powershell":
                if sin_powershell:
                    raise FileNotFoundError("powershell")
                guion = argv[-1]
                if capturadas is not None:
                    capturadas.append(guion)
                clave = _re.search(r"-LiteralPath '([^']+)'", guion).group(1)
                nombre = _re.search(r"\$p\.(\w+);", guion).group(1)
                if clave != "HKCU:" + cm.LLAVE_RUN[4:]:
                    return r
                if nombre == cm.NOMBRE_LLAVE and valor is not None:
                    r.stdout = valor.encode("utf-8") + b"\n"
                else:
                    r.returncode = 1
                return r
            if argv[:2] == ["reg", "query"]:
                if valor is None:
                    r.returncode = 1
                else:
                    r.stdout = (
                        f"\n    {cm.NOMBRE_LLAVE}    REG_SZ    {valor}\n"
                    ).encode(pagina_reg)
            return r

        return correr

    def test_arranque_windows_llave_del_vigilante(self):
        vigia = Path(__file__).resolve().parents[1] / "ops" / "iniciar_encargos_pc.py"
        valor = f'"C:\\py\\python.exe" -B "{vigia}"'
        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(self._correr_llave(valor))
        self.assertEqual((arranque["estado"], arranque["mecanismo"]), ("ok", "vigilante"))

    def test_arranque_windows_con_acentos_en_la_ruta_no_se_manglea(self):
        carpeta = self.tmp / "José"
        carpeta.mkdir()
        vigia = carpeta / "ops" / "iniciar_encargos_pc.py"
        vigia.parent.mkdir()
        vigia.write_bytes(b"x")
        valor = f'"C:\\Users\\José\\py\\python.exe" -B "{vigia}"'
        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(self._correr_llave(valor))
        self.assertEqual((arranque["estado"], arranque["mecanismo"]), ("ok", "vigilante"))

    def test_arranque_windows_sin_powershell_cae_a_reg(self):
        vigia = Path(__file__).resolve().parents[1] / "ops" / "iniciar_encargos_pc.py"
        valor = f'"C:\\py\\python.exe" -B "{vigia}"'
        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(self._correr_llave(valor, sin_powershell=True))
        self.assertEqual((arranque["estado"], arranque["mecanismo"]), ("ok", "vigilante"))

    def test_arranque_windows_llave_ausente_es_falta(self):
        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(self._correr_llave(None))
        self.assertEqual(arranque["estado"], "falta")
        self.assertIn("llave ausente", arranque["detalle"])

    def test_la_ruta_de_powershell_lleva_la_unidad_hkcu(self):
        """F1: sin 'HKCU:' la ruta cae en el disco actual y PowerShell real
        falla con error no terminante (código 0, salida vacía), sin respaldo:
        el arranque saldría falta con la llave bien puesta."""
        capturadas = []
        vigia = Path(__file__).resolve().parents[1] / "ops" / "iniciar_encargos_pc.py"
        valor = f'"C:\\py\\python.exe" -B "{vigia}"'
        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(
                self._correr_llave(valor, capturadas=capturadas)
            )
        self.assertEqual((arranque["estado"], arranque["mecanismo"]), ("ok", "vigilante"))
        self.assertTrue(capturadas)
        self.assertTrue(all("HKCU:" in g for g in capturadas), capturadas)

    def test_java_tlc_lee_el_entorno_persistente_de_windows(self):
        """h_887b7ff6: setx escribe HKCU\\Environment y eso existe en las
        consolas que se abran después, no en la que corrió prepara_pc. El
        reporte corre en la misma consola que setx y sin esto marcaba
        java_tlc falta justo después de dejarlo."""
        import re as _re

        jar = self.tmp / "tla2tools.jar"
        jar.write_bytes(b"jar")
        java = self.tmp / "java.exe"
        java.write_bytes(b"MZ")

        def correr(argv, timeout=20):
            class R:
                returncode = 0
                stdout = b""
                stderr = b""

            r = R()
            if argv[0] == "powershell":
                guion = argv[-1]
                clave = _re.search(r"-LiteralPath '([^']+)'", guion).group(1)
                nombre = _re.search(r"\$p\.(\w+);", guion).group(1)
                if clave == "HKCU:" + cm.ENTORNO_HKCU[4:] and nombre == "TLA2TOOLS":
                    r.stdout = str(jar).encode("utf-8") + b"\n"
                elif clave == "HKCU:" + cm.ENTORNO_HKCU[4:] and nombre == "JAVA":
                    r.stdout = str(java).encode("utf-8") + b"\n"
                else:
                    r.returncode = 1
                return r
            if argv[:1] == [str(java)]:
                r.stderr = b'openjdk version "25" 2025-10-21\n'
                return r
            r.returncode = 1
            return r

        with mock.patch.object(cm, "sistema", lambda: "windows"), mock.patch.dict(
            os.environ, {}, clear=True
        ):
            fila = cm._java_tlc(correr, lambda nombre: None)
        self.assertEqual(fila["estado"], "ok")
        self.assertIn("tla2tools", fila["detalle"])
        self.assertIn("HKCU", fila["detalle"])

    def test_java_tlc_sin_nada_en_windows_sigue_en_falta(self):
        def correr(argv, timeout=20):
            class R:
                returncode = 1
                stdout = b""
                stderr = b""

            return R()

        with mock.patch.object(cm, "sistema", lambda: "windows"), mock.patch.dict(
            os.environ, {}, clear=True
        ):
            fila = cm._java_tlc(correr, lambda nombre: None)
        self.assertEqual(fila["estado"], "falta")
        self.assertIn("sin TLA2TOOLS", fila["detalle"])

    def test_arranque_windows_con_vigilante_inexistente_es_falta(self):
        valor = '"C:\\py\\python.exe" -B "C:\\borrado\\iniciar_encargos_pc.py"'
        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(self._correr_llave(valor))
        self.assertEqual(arranque["estado"], "falta")

    def test_arranque_windows_con_supervisor_directo_es_falta(self):
        valor = (
            '"C:\\...\\powershell.exe" -File "C:\\u\\.motor-encargos\\supervisor.ps1"'
        )
        with mock.patch.object(cm, "sistema", lambda: "windows"):
            arranque = cm._arranque(self._correr_llave(valor))
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

    def test_arranque_plist_que_no_es_diccionario_no_tumba_el_reporte(self):
        import plistlib

        dir_fake = self.tmp / "LaunchDaemons"
        dir_fake.mkdir()
        (dir_fake / "com.goncloud.motor.plist").write_bytes(plistlib.dumps("solo texto"))
        with mock.patch.object(cm, "sistema", lambda: "macos"):
            arranque = cm._arranque(lambda *a, **k: None, dir_fake)
        self.assertEqual(arranque["estado"], "falta")
        self.assertIn("no es un diccionario", arranque["detalle"])

    def test_arranque_plist_mal_formado_es_falta_legible(self):
        dir_fake = self.tmp / "LaunchDaemons"
        dir_fake.mkdir()
        (dir_fake / "com.goncloud.motor.plist").write_bytes(b"esto no es un plist")
        with mock.patch.object(cm, "sistema", lambda: "macos"):
            arranque = cm._arranque(lambda *a, **k: None, dir_fake)
        self.assertEqual(arranque["estado"], "falta")
        self.assertIn("ilegible", arranque["detalle"])

    def test_arranque_plist_recursivo_es_falta_legible_y_no_tumba(self):
        """Un plist binario anidado hace que plistlib.load lance RecursionError,
        que no es OSError ni ValueError (repro real: dumps con límite alto,
        load con el normal). La sección queda falta legible y el reporte sigue."""
        import plistlib
        import sys

        obj = None
        for _ in range(5000):
            obj = [obj]
        limite = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(limite * 40)
            crudo = plistlib.dumps(obj, fmt=plistlib.FMT_BINARY)
        finally:
            sys.setrecursionlimit(limite)
        dir_fake = self.tmp / "LaunchDaemons"
        dir_fake.mkdir()
        (dir_fake / "com.goncloud.motor.plist").write_bytes(crudo)
        with mock.patch.object(cm, "sistema", lambda: "macos"):
            arranque = cm._arranque(lambda *a, **k: None, dir_fake)
        self.assertEqual(arranque["estado"], "falta")
        self.assertIn("ilegible", arranque["detalle"])
        self.assertIn("maximum recursion depth exceeded", arranque["detalle"])

    def test_arranque_linux_es_aviso_y_no_falta(self):
        with mock.patch.object(cm, "sistema", lambda: "linux"):
            arranque = cm._arranque(lambda *a, **k: None)
        self.assertEqual(arranque["estado"], "aviso")


class AgentesEnWindowsTest(unittest.TestCase):
    """h_1ddf17a3: en Windows, los agentes de shell (claude) se deciden con la
    sonda de la jaula de WSL del motor y los nativos exigen su jaula (psec o
    AppContainer según el adaptador) antes de mirar el PATH."""

    def _agentes(self, which, causa_claude="", psec=True, appcontainer=True):
        with mock.patch.object(cm, "sistema", lambda: "windows"), mock.patch.object(
            cm, "_usa_wsl", lambda agente: agente == "claude"
        ), mock.patch.object(
            cm, "_causa_jaula_wsl", lambda agente: causa_claude
        ), mock.patch.object(cm, "_psec_disponible", lambda: psec), mock.patch.object(
            cm, "_jaula_windows_disponible", lambda: appcontainer
        ):
            filas = cm._agentes(which)
        return {f["agente"]: f for f in filas}

    def test_claude_corre_en_la_distro_sin_estar_en_el_path_de_windows(self):
        filas = self._agentes(lambda nombre: None, causa_claude="")
        self.assertEqual(filas["claude"]["estado"], "ok")
        self.assertIn("distro WSL", filas["claude"]["detalle"])

    def test_claude_sin_jaula_wsl_es_falta_con_la_causa_del_motor(self):
        filas = self._agentes(lambda nombre: None, causa_claude="sin bubblewrap en la distro")
        self.assertEqual(filas["claude"]["estado"], "falta")
        self.assertIn("sin bubblewrap", filas["claude"]["detalle"])

    def test_nativo_sin_jaula_de_windows_es_falta_aunque_el_binario_este(self):
        filas = self._agentes(lambda nombre: "/falso/bin/" + nombre, psec=False, appcontainer=False)
        for agente in ("codex", "grok", "kimi", "muse", "opencode", "qwen"):
            self.assertEqual(filas[agente]["estado"], "falta", agente)
            self.assertIn("sin jaula de Windows", filas[agente]["detalle"])

    def test_nativo_con_jaula_y_binario_es_ok(self):
        filas = self._agentes(lambda nombre: "/falso/bin/" + nombre, psec=True)
        for agente in ("codex", "grok", "kimi", "muse", "opencode", "qwen"):
            self.assertEqual(filas[agente]["estado"], "ok", agente)

    def test_codex_exige_appcontainer_y_los_demas_psec(self):
        filas = self._agentes(
            lambda nombre: "/falso/bin/" + nombre, psec=False, appcontainer=True
        )
        self.assertEqual(filas["codex"]["estado"], "ok")
        self.assertEqual(filas["kimi"]["estado"], "falta")
        self.assertIn("sin jaula de Windows", filas["kimi"]["detalle"])


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
