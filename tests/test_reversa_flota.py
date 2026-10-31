from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.entorno import HAY_AISLAMIENTO, SIN_AISLAMIENTO

from motor.flota.control import (
    anotar_trabajo,
    aplicar,
    asignar,
    leer_trabajo,
    recuperar,
)

RAIZ = Path(__file__).resolve().parents[1]
REVERSA = RAIZ / "ops" / "reversa_ensayo.sh"


def _git(motor: Path, *args: str) -> str:
    hecho = subprocess.run(
        ["git", "-C", str(motor), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return hecho.stdout.strip()


def _commit(motor: Path, archivos: dict[str, str], mensaje: str) -> str:
    for nombre, texto in archivos.items():
        path = motor / nombre
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(texto, encoding="utf-8")
    _git(motor, "add", ".")
    _git(motor, "commit", "-m", mensaje)
    return _git(motor, "rev-parse", "HEAD")


class ReversaFlota(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="flota-ensayo-"))
        self.home = self.tmp / "home"
        self.motor = self.home / "motor"
        self.motor.mkdir(parents=True)
        _git(self.motor, "init", "-b", "main")
        _git(self.motor, "config", "user.email", "ensayo@local")
        _git(self.motor, "config", "user.name", "ensayo")
        self.buena = _commit(
            self.motor,
            {"comprobar.py": "raise SystemExit(0)\n", "marca.txt": "buena\n"},
            "buena",
        )
        self.mala = _commit(
            self.motor,
            {"comprobar.py": "raise SystemExit(1)\n"},
            "mala",
        )
        self.nueva = _commit(
            self.motor,
            {"comprobar.py": "raise SystemExit(0)\n", "marca.txt": "nueva\n"},
            "nueva",
        )
        _git(self.motor, "checkout", "-q", "--detach", self.buena)

    def tearDown(self) -> None:
        for gen in (self.home / "generaciones").glob("*"):
            if gen.is_dir():
                subprocess.run(
                    ["git", "-C", str(self.motor), "worktree", "remove", "--force", str(gen)],
                    check=False,
                    capture_output=True,
                )
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_candidata_invalida_conserva_la_generacion(self) -> None:
        self.assertEqual(aplicar(self.home, self.buena)["accion"], "aplicada")
        antes = (self.home / "actual").resolve()
        salida = aplicar(self.home, self.mala)
        self.assertEqual(salida["accion"], "rechazada")
        self.assertEqual((self.home / "actual").resolve(), antes)
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())
        cand = json.loads((self.home / "candidata.json").read_text(encoding="utf-8"))
        self.assertEqual(cand["estado"], "rechazada")
        self.assertEqual(cand["destino"], self.mala)

    def test_fallo_en_la_tercera_conserva_la_segunda_y_reversa_tras_completa(self) -> None:
        self.assertEqual(aplicar(self.home, self.buena)["accion"], "aplicada")
        self.assertEqual(aplicar(self.home, self.nueva)["accion"], "aplicada")
        salida = aplicar(self.home, self.mala)
        self.assertEqual(salida["accion"], "rechazada")
        self.assertEqual(salida["vigente"], self.nueva)
        self.assertEqual(asignar(self.home), self.nueva)
        self.assertTrue((self.home / "instalacion.json").exists())
        reversa = subprocess.run(
            ["sh", str(REVERSA), "--home", str(self.home)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("revertido", reversa.stdout)
        self.assertEqual(asignar(self.home), self.buena)
        self.assertFalse((self.home / "instalacion.json").exists())
        self.assertTrue(list((self.home / "historial").glob("instalacion-*.json")))
        otra = subprocess.run(
            ["sh", str(REVERSA), "--home", str(self.home)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(otra.stdout.strip(), "nada")
        self.assertEqual(asignar(self.home), self.buena)

    def test_el_trabajo_activo_conserva_su_generacion(self) -> None:
        aplicar(self.home, self.buena)
        proc = subprocess.Popen(["/bin/sleep", "300"])
        self.addCleanup(proc.kill)
        from motor import plataforma

        anotar_trabajo(
            self.home,
            "uno",
            self.buena,
            "codex",
            {
                "pid": proc.pid,
                "inicio": plataforma.hora_inicio(proc.pid),
                "boot_id": plataforma.boot_id(),
            },
        )
        salida = aplicar(self.home, self.nueva)
        self.assertEqual(salida["accion"], "ocupada")
        self.assertEqual(leer_trabajo(self.home, "uno")["generacion"], self.buena)
        self.assertEqual(asignar(self.home), self.buena)
        self.assertTrue((self.home / "generaciones" / self.buena).is_dir())
        self.assertTrue((self.home / "generaciones" / self.nueva).is_dir())
        self.assertEqual(
            ((self.home / "generaciones" / self.buena) / "marca.txt").read_text(encoding="utf-8"),
            "buena\n",
        )
        (self.home / "trabajos" / "uno.json").unlink()
        self.assertEqual(aplicar(self.home, self.nueva)["accion"], "aplicada")
        self.assertEqual(asignar(self.home), self.nueva)

    def test_recuperar_completa_lo_que_ya_quedo_comprobado(self) -> None:
        aplicar(self.home, self.buena)
        cortado = aplicar(self.home, self.nueva, "worktree")
        self.assertEqual(cortado["accion"], "cortado")
        self.assertTrue((self.home / "instalacion-pendiente.json").exists())
        self.assertEqual(asignar(self.home), self.buena)
        self.assertEqual(recuperar(self.home)["accion"], "completo")
        self.assertEqual(asignar(self.home), self.nueva)
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())
        self.assertEqual(recuperar(self.home)["accion"], "nada")

    def test_recuperar_revierte_si_la_candidata_no_llego_a_instalarse(self) -> None:
        aplicar(self.home, self.buena)
        self.assertEqual(aplicar(self.home, self.nueva, "pendiente")["accion"], "cortado")
        self.assertFalse((self.home / "generaciones" / self.nueva).exists())
        self.assertEqual(recuperar(self.home)["accion"], "revertido")
        self.assertEqual(asignar(self.home), self.buena)
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())

    def _encargo_con_trabajador(self, clave: str):
        from motor.encargos.ciclo import ahora_iso, recibir
        from motor.encargos.registro import Registro

        aplicar(self.home, self.buena)
        base = self.tmp / "base"
        reg = Registro(base)
        (base / "flota-home.json").write_text(
            json.dumps({"home": str(self.home)}), encoding="utf-8"
        )
        hora = ahora_iso()
        eid = recibir(reg, "reserva concurrente", clave, hora)["encargo"]
        espacio = base / "espacio" / eid
        espacio.mkdir(parents=True)
        script = espacio / "trabajo.py"
        marca = str(self.home / "actual" / "marca.txt")
        script.write_text(
            "from pathlib import Path\nimport time,json,os\n"
            'while not Path("seguir").exists(): time.sleep(.02)\n'
            'Path("observado.json").write_text(json.dumps({"marca":Path('
            + repr(marca)
            + ').read_text().strip(),"generacion":os.environ.get("MOTOR_GENERACION")}))\n',
            encoding="utf-8",
        )
        return reg, base, eid, espacio, hora, script

    def _esperar_visto(self, espacio: Path) -> dict:
        (espacio / "seguir").touch()
        for _ in range(100):
            if (espacio / "observado.json").exists():
                return json.loads((espacio / "observado.json").read_text(encoding="utf-8"))
            time.sleep(0.05)
        self.fail("el trabajador no observo su generacion")

    def _cerrar_intento(self, base: Path, eid: str) -> None:
        from motor.encargos.sesion import cerrar

        for intento in (base / "ejecucion" / eid).glob("intento-*"):
            proc = intento / "proc.json"
            if proc.exists():
                cerrar(intento, json.loads(proc.read_text(encoding="utf-8")))

    @unittest.skipUnless(HAY_AISLAMIENTO, SIN_AISLAMIENTO)
    def test_arranque_inyectado_en_el_enlace_conserva_su_generacion(self) -> None:
        from motor.encargos.ciclo import _lanzar_uno
        from motor.flota import control

        reg, base, eid, espacio, hora, script = self._encargo_con_trabajador("r4-enlace")
        original = control._enlace
        try:
            def antes_del_enlace(home, gen):
                _lanzar_uno(
                    reg,
                    reg.fold(eid),
                    base / "ejecucion" / eid,
                    espacio,
                    hora,
                    lambda *args: [sys.executable, str(script)],
                )
                original(home, gen)

            control._enlace = antes_del_enlace
            salida = control.aplicar(self.home, self.nueva)
            visto = self._esperar_visto(espacio)
            self.assertEqual(salida["accion"], "ocupada")
            self.assertEqual(salida["vigente"], self.buena)
            self.assertTrue((self.home / "generaciones" / self.nueva).is_dir())
            registrada = control.leer_trabajo(self.home, eid)["generacion"]
            self.assertEqual(registrada, self.buena)
            self.assertEqual(visto["generacion"], registrada)
            self.assertEqual(visto["marca"], "buena")
            self.assertEqual(asignar(self.home), self.buena)
        finally:
            control._enlace = original
            self._cerrar_intento(base, eid)
            reg.close()

    @unittest.skipUnless(HAY_AISLAMIENTO, SIN_AISLAMIENTO)
    def test_pausa_tras_asignar_registra_la_generacion_vigente(self) -> None:
        from motor.encargos.ciclo import _lanzar_uno
        from motor.flota import control

        reg, base, eid, espacio, hora, script = self._encargo_con_trabajador("r4-pausa")
        original = control.asignar
        leida = threading.Event()
        liberar = threading.Event()
        errores: list[BaseException] = []
        pausada = False

        def asignar_pausado(home):
            nonlocal pausada
            gen = original(home)
            if threading.current_thread().name == "lanzador-r4" and not pausada:
                pausada = True
                leida.set()
                liberar.wait(10)
            return gen

        def lanzar():
            from motor.encargos.registro import Registro

            reg_hilo = Registro(base)
            try:
                _lanzar_uno(
                    reg_hilo,
                    reg_hilo.fold(eid),
                    base / "ejecucion" / eid,
                    espacio,
                    hora,
                    lambda *args: [sys.executable, str(script)],
                )
            except BaseException as exc:
                errores.append(exc)
            finally:
                reg_hilo.close()

        hilo = threading.Thread(target=lanzar, name="lanzador-r4")
        try:
            control.asignar = asignar_pausado
            hilo.start()
            self.assertTrue(leida.wait(10))
            aplicada = control.aplicar(self.home, self.nueva)
            liberar.set()
            hilo.join(10)
            self.assertFalse(hilo.is_alive())
            self.assertEqual(errores, [])
            self.assertEqual(aplicada["accion"], "aplicada")
            visto = self._esperar_visto(espacio)
            registrada = control.leer_trabajo(self.home, eid)["generacion"]
            self.assertEqual(registrada, self.nueva)
            self.assertEqual(visto["generacion"], registrada)
            self.assertEqual(visto["marca"], "nueva")
        finally:
            liberar.set()
            control.asignar = original
            if hilo.is_alive():
                hilo.join(10)
            self._cerrar_intento(base, eid)
            reg.close()

    @unittest.skipUnless(HAY_AISLAMIENTO, SIN_AISLAMIENTO)
    def test_recuperar_y_revertir_respetan_un_trabajador_real(self) -> None:
        from motor.encargos.ciclo import _lanzar_uno
        from motor.flota.control import recuperar, revertir

        for operacion in ("recuperar", "revertir", "script"):
            with self.subTest(operacion=operacion):
                # Cada recorrido termina sin procesos ni recibos pendientes.
                reg, base, eid, espacio, hora, script = self._encargo_con_trabajador(operacion)
                if operacion == "recuperar":
                    aplicar(self.home, self.nueva, "worktree")
                else:
                    aplicar(self.home, self.nueva)
                if operacion == "script":
                    # La reversa observa con el runtime anterior, no con el roto.
                    shutil.copytree(Path(__file__).resolve().parents[1] / "motor",
                                    self.home / "generaciones" / self.buena / "motor",
                                    ignore=shutil.ignore_patterns("__pycache__"))
                try:
                    _lanzar_uno(reg, reg.fold(eid), base / "ejecucion" / eid,
                                espacio, hora, lambda *a, archivo=script: [sys.executable, str(archivo)])
                    vigente = asignar(self.home)
                    if operacion == "script":
                        salida = subprocess.run(["sh", str(REVERSA), "--home", str(self.home)],
                                                capture_output=True, text=True, check=True)
                        self.assertIn("ocupada", salida.stdout)
                    else:
                        funcion = recuperar if operacion == "recuperar" else revertir
                        self.assertEqual(funcion(self.home)["accion"], "ocupada")
                    self.assertEqual(asignar(self.home), vigente)
                    visto = self._esperar_visto(espacio)
                    self.assertEqual(visto["generacion"], vigente)
                    self.assertEqual(visto["marca"], "buena" if operacion == "recuperar" else "nueva")
                finally:
                    self._cerrar_intento(base, eid)
                    reg.close()
                if operacion == "script":
                    salida = subprocess.run(["sh", str(REVERSA), "--home", str(self.home)],
                                            capture_output=True, text=True, check=True)
                    self.assertIn("revertido", salida.stdout)
                else:
                    self.assertIn(funcion(self.home)["accion"], ("completo", "revertido"))
                # La próxima variante usa un registro nuevo.
                shutil.rmtree(base)

    def test_la_reversa_no_arranca_el_motor_nuevo(self) -> None:
        self.assertEqual(aplicar(self.home, self.buena)["accion"], "aplicada")
        self.assertEqual(aplicar(self.home, self.nueva)["accion"], "aplicada")
        self.assertEqual(
            (self.home / "actual").resolve(),
            (self.home / "generaciones" / self.nueva).resolve(),
        )
        roto = self.home / "arranque-del-motor"
        _commit(
            self.motor,
            {
                "comprobar.py": "raise SystemExit(0)\n",
                "motor/__init__.py": "",
                "motor/__main__.py": (
                    "import os\n"
                    "from pathlib import Path\n"
                    "Path(os.environ['MOTOR_MARCA']).write_text('arranque')\n"
                    "raise SystemExit(1)\n"
                ),
            },
            "rota",
        )
        mala = _git(self.motor, "rev-parse", "HEAD")
        _git(self.motor, "checkout", "-q", "--detach", mala)
        (self.home / "instalacion-pendiente.json").write_text(
            json.dumps(
                {
                    "anterior": {"codigo": self.buena},
                    "destino": {"codigo": mala},
                    "etapa": "codigo",
                }
            ),
            encoding="utf-8",
        )
        hecho = subprocess.run(
            ["python3", "-m", "motor"],
            cwd=self.motor,
            capture_output=True,
            text=True,
            env={"MOTOR_MARCA": str(roto), "PATH": "/usr/bin:/bin"},
        )
        self.assertNotEqual(hecho.returncode, 0)
        self.assertTrue(roto.is_file())
        roto.unlink()
        reversa = subprocess.run(
            ["sh", str(REVERSA), "--home", str(self.home)],
            check=True,
            capture_output=True,
            text=True,
            env={"MOTOR_MARCA": str(roto), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
        )
        self.assertIn("revertido", reversa.stdout)
        self.assertFalse(roto.exists())
        self.assertEqual(_git(self.motor, "rev-parse", "HEAD"), self.buena)
        self.assertEqual(
            (self.home / "actual").resolve(),
            (self.home / "generaciones" / self.buena).resolve(),
        )
        self.assertFalse((self.home / "instalacion-pendiente.json").exists())
        otra = subprocess.run(
            ["sh", str(REVERSA), "--home", str(self.home)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(otra.stdout.strip(), "nada")
        self.assertEqual(_git(self.motor, "rev-parse", "HEAD"), self.buena)


if __name__ == "__main__":
    unittest.main()
