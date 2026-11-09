"""El supervisor se reinicia sin cortar los turnos en curso.

Un despliegue no puede esperar a que no haya agentes: la orden de reinicio llega
a mitad de un turno. El supervisor deja de lanzar, espera a que los turnos
cierren o queden retomables, y sale con el codigo que el servicio entiende como
"vuelve a lanzarme". El supervisor nuevo retoma lo que siguio vivo.

Las pruebas arrancan un supervisor real en un temporal con un agente de mentira
de turno largo; solo se sustituye el lanzador del agente. El archivo de orden es
el mismo camino en las tres maquinas: en Windows no hay senales POSIX y
`taskkill /F` no se puede atrapar.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor import plataforma
from motor.__main__ import main as motor_main
from motor.encargos import cli
from motor.encargos.ciclo import ahora_iso, recibir
from motor.encargos.registro import Registro

RAIZ = Path(__file__).resolve().parent.parent

DRIVER = """
import json, sys
from motor.__main__ import main as motor_main
from motor.encargos import cli

config = json.loads(sys.argv[1])
lanzamientos = config["lanzamientos"]
turno_s = config["turno_s"]

codigo = (
    "import json, pathlib, time\\n"
    "espacio = pathlib.Path.cwd()\\n"
    "base = espacio.parent.parent\\n"
    "actual = base / 'ejecucion' / espacio.name / 'actual'\\n"
    "intento = actual.read_text(encoding='utf-8').strip() if actual.is_file() else ''\\n"
    "with open(" + repr(lanzamientos) + ", 'a', encoding='utf-8') as f:\\n"
    "    f.write(json.dumps({'encargo': espacio.name, 'intento': intento}) + '\\\\n')\\n"
    "    f.flush()\\n"
    "time.sleep(" + repr(turno_s) + ")\\n"
)


def fabrica(agente, modelo="", sesion_modo="nueva"):
    return lambda enc, prompt, espacio, sesion: [sys.executable, "-c", codigo]


cli._lanzador = fabrica
raise SystemExit(motor_main(config["argv"]))
"""


def _codigo_agente(lanzamientos: Path, turno_s: float) -> str:
    return (
        "import json, pathlib, time\n"
        "espacio = pathlib.Path.cwd()\n"
        "base = espacio.parent.parent\n"
        "actual = base / 'ejecucion' / espacio.name / 'actual'\n"
        "intento = actual.read_text(encoding='utf-8').strip() if actual.is_file() else ''\n"
        f"with open({str(lanzamientos)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'encargo': espacio.name, 'intento': intento}) + '\\n')\n"
        "    f.flush()\n"
        f"time.sleep({turno_s!r})\n"
    )


def _lanzador_falso(lanzamientos: Path, turno_s: float):
    codigo = _codigo_agente(lanzamientos, turno_s)

    def fabrica(agente, modelo="", sesion_modo="nueva"):
        return lambda enc, prompt, espacio, sesion: [sys.executable, "-c", codigo]

    return fabrica


class ReinicioOrdenado(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reinicio-ordenado-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.base = self.tmp / "encargos"
        self.base.mkdir()
        poteto = self.base / "politica" / "poteto" / "skills" / "poteto-mode"
        poteto.mkdir(parents=True)
        (poteto / "SKILL.md").write_text("# poteto-mode (banco)\n", encoding="utf-8")
        self.lanzamientos = self.tmp / "lanzamientos.jsonl"
        self.driver = self.tmp / "driver.py"
        self.driver.write_text(DRIVER, encoding="utf-8")
        self.env = {**os.environ, "PYTHONPATH": str(RAIZ), "PYTHONUTF8": "1"}
        self.procesos: list[subprocess.Popen] = []

    def tearDown(self):
        for proceso in self.procesos:
            if proceso.poll() is None:
                proceso.kill()
                proceso.communicate()

    def arrancar(self, turno_s: float) -> subprocess.Popen:
        config = {
            "argv": [
                "supervisor",
                "--base",
                str(self.base),
                "--agente",
                "ensayo",
                "--intervalo",
                "0.1",
            ],
            "lanzamientos": str(self.lanzamientos),
            "turno_s": turno_s,
        }
        proceso = subprocess.Popen(
            [sys.executable, str(self.driver), json.dumps(config)],
            cwd=RAIZ,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.procesos.append(proceso)
        return proceso

    def esperar_lanzamiento(self, tope: float = 30.0) -> dict:
        fin = time.time() + tope
        while time.time() < fin:
            if self.lanzamientos.is_file():
                lineas = [
                    linea for linea in self.lanzamientos.read_text().splitlines() if linea
                ]
                if lineas:
                    return json.loads(lineas[0])
            time.sleep(0.05)
        self.fail("el supervisor no lanzo el turno de mentira antes del tope")

    def lanzados(self) -> list[dict]:
        if not self.lanzamientos.is_file():
            return []
        return [
            json.loads(linea)
            for linea in self.lanzamientos.read_text().splitlines()
            if linea
        ]

    def encargar(self, clave: str, texto: str) -> str:
        registro = Registro(self.base)
        try:
            salida = recibir(registro, texto, clave, ahora_iso())
        finally:
            registro.close()
        return str(salida["encargo"])

    def orden(self, tope_s: int, clave: str) -> None:
        (self.base / "reiniciar-supervisor.json").write_text(
            json.dumps({"clave": clave, "hora": ahora_iso(), "tope_s": tope_s}),
            encoding="utf-8",
        )

    def esperar_consumida(self, tope: float = 10.0) -> None:
        ruta = self.base / "reiniciar-supervisor.json"
        fin = time.time() + tope
        while time.time() < fin:
            if not ruta.exists():
                return
            time.sleep(0.05)
        self.fail(
            "el supervisor no tomo la orden de reinicio: el archivo sigue en la base "
            "y los turnos nuevos seguirian lanzandose"
        )

    def esperar_fin(self, proceso: subprocess.Popen, tope: float = 40.0) -> str:
        try:
            _, error = proceso.communicate(timeout=tope)
        except subprocess.TimeoutExpired:
            proceso.kill()
            _, error = proceso.communicate()
            self.fail(
                "el supervisor no salio tras la orden de reinicio (codigo "
                f"{proceso.returncode}); stderr: {error[-2000:]}"
            )
        return error

    def recibo(self) -> dict:
        return json.loads(
            (self.base / "supervisor-reinicio.json").read_text(encoding="utf-8")
        )

    def pasada(self, turno_s: float = 0) -> None:
        with mock.patch.object(
            cli, "_lanzador", _lanzador_falso(self.lanzamientos, turno_s)
        ):
            cli.main(
                [
                    "supervisor",
                    "--base",
                    str(self.base),
                    "--agente",
                    "ensayo",
                    "--una-vez",
                ]
            )
        time.sleep(0.8)

    def pasada_sin_lanzar(self) -> None:
        with mock.patch.object(cli, "_lanzador", lambda *a, **k: lambda *x: None):
            cli.main(
                [
                    "supervisor",
                    "--base",
                    str(self.base),
                    "--agente",
                    "ensayo",
                    "--una-vez",
                ]
            )

    def test_el_reinicio_drena_el_turno_y_no_lanza_nada_nuevo(self):
        self.encargar("primero", "trabajo en curso")
        proceso = self.arrancar(turno_s=2)
        primero = self.esperar_lanzamiento()
        self.orden(tope_s=20, clave="drena")
        self.esperar_consumida()
        segundo = self.encargar("segundo", "trabajo que llega durante el reinicio")
        error = self.esperar_fin(proceso)
        self.assertEqual(
            proceso.returncode,
            75,
            "el supervisor no salio con el codigo de relanzamiento: "
            f"{proceso.returncode}; stderr: {error[-2000:]}",
        )
        recibo = self.recibo()
        self.assertEqual(recibo["motivo"], "drenado", "el turno no cerro antes del tope")
        self.assertEqual(recibo["vivos"], [], "quedo un turno vivo sin tope vencido")
        lanzados = self.lanzados()
        self.assertEqual(
            [x["intento"] for x in lanzados],
            [primero["intento"]],
            "el supervisor lanzo un turno nuevo despues de la orden",
        )
        self.assertFalse(
            any(x["encargo"] == segundo for x in lanzados),
            "se lanzo el encargo que llego durante el drenaje",
        )
        self.pasada()
        lanzados = self.lanzados()
        self.assertEqual(
            sum(1 for x in lanzados if x["intento"] == primero["intento"]),
            1,
            "el supervisor nuevo relanzo el turno que ya estaba cerrado",
        )

    def test_el_tope_vencido_deja_el_turno_vivo_y_el_nuevo_lo_retoma(self):
        self.encargar("primero", "trabajo en curso")
        proceso = self.arrancar(turno_s=6)
        primero = self.esperar_lanzamiento()
        self.orden(tope_s=0, clave="tope")
        self.esperar_consumida()
        error = self.esperar_fin(proceso)
        self.assertEqual(
            proceso.returncode,
            75,
            "el supervisor no salio con el codigo de relanzamiento al vencer el tope: "
            f"{proceso.returncode}; stderr: {error[-2000:]}",
        )
        recibo = self.recibo()
        self.assertEqual(recibo["motivo"], "tope", "el tope vencido no quedo anotado")
        self.assertEqual(
            [v["encargo"] for v in recibo["vivos"]],
            [primero["encargo"]],
            "el recibo no dejo dicho que turno siguio vivo",
        )
        vivo = recibo["vivos"][0]
        pid, inicio = int(vivo["pid"]), str(vivo["inicio"])
        self.assertEqual(
            plataforma.estado_proc(
                {"pid": pid, "inicio": inicio, "boot_id": plataforma.boot_id()}
            ),
            "vivo",
            "el turno anotado como vivo ya no estaba vivo",
        )
        self.pasada()
        time.sleep(0.3)
        self.assertEqual(
            sum(1 for x in self.lanzados() if x["intento"] == primero["intento"]),
            1,
            "el supervisor nuevo relanzo el turno que seguia vivo",
        )
        self.assertEqual(
            plataforma.estado_proc(
                {"pid": pid, "inicio": inicio, "boot_id": plataforma.boot_id()}
            ),
            "vivo",
            "el supervisor nuevo corto el turno que debia retomar",
        )
        fin = time.time() + 30
        while time.time() < fin and plataforma.pid_vivo(pid):
            time.sleep(0.1)
        self.assertNotEqual(
            plataforma.estado_proc(
                {"pid": pid, "inicio": inicio, "boot_id": plataforma.boot_id()}
            ),
            "vivo",
            "el turno de mentira no termino",
        )
        self.pasada_sin_lanzar()
        registro = Registro(self.base)
        try:
            enc = registro.fold(primero["encargo"])
        finally:
            registro.close()
        self.assertIsNotNone(enc, "el encargo desaparecio del registro")
        assert enc is not None
        self.assertIsNone(enc.proc, "el turno terminado no quedo cerrado")

    def test_la_orden_por_archivo_es_el_camino_de_windows_sin_senales(self):
        if shutil.which("bash") is None:
            self.skipTest("el agente de ensayo necesita bash (Git Bash en Windows)")
        self.encargar("primero", "trabajo en curso")
        proceso = self.arrancar(turno_s=1)
        primero = self.esperar_lanzamiento()
        try:
            codigo = motor_main(
                [
                    "reiniciar-supervisor",
                    "--base",
                    str(self.base),
                    "--clave",
                    "pc",
                    "--tope",
                    "10",
                ]
            )
        except SystemExit as exc:
            codigo = int(exc.code or 0)
        self.assertEqual(
            codigo,
            0,
            "el motor no tiene una orden de reinicio que el vigilante de la PC "
            "pueda dejar como archivo (codigo "
            f"{codigo})",
        )
        self.esperar_consumida()
        error = self.esperar_fin(proceso)
        self.assertEqual(
            proceso.returncode,
            75,
            "el supervisor no salio por el codigo de relanzamiento en el camino del "
            f"archivo de orden: {proceso.returncode}; stderr: {error[-2000:]}",
        )
        recibo = self.recibo()
        self.assertEqual(recibo["motivo"], "drenado")
        self.assertEqual(
            [x["intento"] for x in self.lanzados()],
            [primero["intento"]],
            "el camino del archivo de orden lanzo un turno nuevo",
        )


if __name__ == "__main__":
    unittest.main()
