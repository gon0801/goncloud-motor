"""El supervisor de la PC se vuelve a levantar solo.

El 1-nov el supervisor terminó y en la PC nadie lo relanzó: `supervisor.ps1` corría una
sola vez. `ops/iniciar_encargos_pc.py` ahora deja un vigilante que lo relanza con espera
creciente. Lo que necesita Windows (Explorer COM, CreateProcess, el Job de SSH, msvcrt,
tasklist y taskkill) no se puede ejecutar aquí; se prueba con dobles y la parte pura sí
corre de verdad.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

RUTA = Path(__file__).resolve().parents[1] / "ops" / "iniciar_encargos_pc.py"
_spec = importlib.util.spec_from_file_location("iniciar_encargos_pc", RUTA)
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

BREAKAWAY = 0x01000000


class Reloj:
    def __init__(self):
        self.ahora = 0.0
        self.dormidos: list[float] = []

    def __call__(self):
        return self.ahora

    def dormir(self, segundos):
        self.dormidos.append(segundos)
        self.ahora += segundos


class Proceso:
    """Un supervisor que vive `vive_s` segundos del reloj y termina con `codigo`."""

    def __init__(self, reloj, pid, vive_s=1, codigo=1, al_esperar=None):
        self.reloj, self.pid, self.codigo = reloj, pid, codigo
        self.muere_en = reloj.ahora + vive_s
        self.al_esperar = al_esperar

    def wait(self, timeout=None):
        if self.al_esperar:
            self.al_esperar()
        if self.reloj.ahora >= self.muere_en:
            return self.codigo
        self.reloj.ahora += 1
        raise subprocess.TimeoutExpired("supervisor", timeout)


def vigilar(procesos, reloj, parar=lambda: False, **kw):
    cola = list(procesos)
    registro: list[str] = []
    cerrados: list[int] = []

    def lanzar():
        siguiente = cola.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente

    lanzamientos = pc.vigilar(
        lanzar, parar, reloj, reloj.dormir, registro.append, lambda p: cerrados.append(p.pid), **kw
    )
    return lanzamientos, registro, cerrados


class Espera(unittest.TestCase):
    def test_crece_hasta_el_tope(self):
        self.assertEqual(
            [pc.espera_creciente(n) for n in range(1, 9)], [5, 10, 20, 40, 80, 160, 300, 300]
        )


class Relanzamiento(unittest.TestCase):
    def test_si_el_supervisor_termina_se_vuelve_a_levantar_con_espera_creciente(self):
        reloj = Reloj()
        parar = {"ya": False}
        procesos = [
            Proceso(reloj, 11), Proceso(reloj, 12), Proceso(reloj, 13),
            Proceso(reloj, 14, al_esperar=lambda: parar.update(ya=True)),
        ]
        lanzamientos, registro, _ = vigilar(procesos, reloj, parar=lambda: parar["ya"])
        self.assertEqual(lanzamientos, 4)
        esperas = [int(r.split(" en ")[1].split(" s")[0]) for r in registro if "levantar" in r]
        self.assertEqual(esperas[:3], [5, 10, 20])
        self.assertEqual(sum(reloj.dormidos), 5 + 10 + 20)

    def test_un_supervisor_que_aguanto_reinicia_la_espera(self):
        reloj = Reloj()
        fin = {"ya": False}
        procesos = [
            Proceso(reloj, 1), Proceso(reloj, 2), Proceso(reloj, 3, vive_s=pc.ESTABLE_S + 100),
            Proceso(reloj, 4, al_esperar=lambda: fin.update(ya=True)),
        ]
        _, registro, _ = vigilar(procesos, reloj, parar=lambda: fin["ya"])
        esperas = [int(r.split(" en ")[1].split(" s")[0]) for r in registro if "levantar" in r]
        self.assertEqual(esperas, [5, 10, 5, 10])

    def test_un_lanzamiento_que_falla_no_mata_al_vigilante_y_reintenta(self):
        reloj = Reloj()
        fin = {"ya": False}
        procesos = [
            RuntimeError("Supervisor rechazado: Job incorrecto"),
            OSError("no hay powershell"),
            Proceso(reloj, 3, al_esperar=lambda: fin.update(ya=True)),
        ]
        lanzamientos, registro, _ = vigilar(procesos, reloj, parar=lambda: fin["ya"])
        self.assertEqual(lanzamientos, 1)
        self.assertTrue(any("RuntimeError" in r for r in registro))
        self.assertTrue(any("OSError" in r for r in registro))
        self.assertEqual(reloj.dormidos[:5], [1] * 5)
        self.assertEqual(sum(reloj.dormidos), 5 + 10)

    def test_parar_cierra_el_supervisor_vivo_y_no_lo_relanza(self):
        reloj = Reloj()
        pedido = {"ya": False}
        vivo = Proceso(reloj, 21, vive_s=10**6, al_esperar=lambda: pedido.update(ya=True))
        lanzamientos, _, cerrados = vigilar([vivo, Proceso(reloj, 22)], reloj, parar=lambda: pedido["ya"])
        self.assertEqual((lanzamientos, cerrados), (1, [21]))

    def test_parar_durante_la_espera_no_lanza_otro(self):
        reloj = Reloj()
        pedido = {"ya": False}
        muerto = Proceso(reloj, 31, al_esperar=lambda: None)
        original = reloj.dormir

        def dormir(s):
            original(s)
            pedido["ya"] = True

        reloj.dormir = dormir
        lanzamientos, _, _ = vigilar([muerto, Proceso(reloj, 32)], reloj, parar=lambda: pedido["ya"])
        self.assertEqual(lanzamientos, 1)


class FueraDelJob(unittest.TestCase):
    def test_solo_se_acepta_un_supervisor_de_la_sesion_y_el_usuario_fuera_de_un_job(self):
        bueno = {"pid": 5, "sid": "S-1", "session": 1, "in_job": False}
        self.assertTrue(pc.identidad_valida(bueno, "S-1", 1))
        self.assertFalse(pc.identidad_valida({**bueno, "in_job": True}, "S-1", 1))
        self.assertFalse(pc.identidad_valida({**bueno, "session": 0}, "S-1", 0))
        self.assertFalse(pc.identidad_valida({**bueno, "session": 2}, "S-1", 1))
        self.assertFalse(pc.identidad_valida({**bueno, "sid": "S-2"}, "S-1", 1))

    def _correr(self, carpeta, identidad, procesos, al_lanzar=None, mata=True):
        class Windows:
            def identity(self, pid):
                return {**identidad, "pid": pid}

            def start_time(self, pid):
                return 1000 + pid

        lanzados = []
        muertos = []
        reloj = Reloj()

        def popen(comando, **kw):
            lanzados.append(kw)
            proceso = procesos.pop(0)
            proceso.pid = 100 + len(lanzados)
            if al_lanzar:
                al_lanzar()
            return proceso

        with mock.patch.object(pc.subprocess, "Popen", side_effect=popen), mock.patch.object(
            pc, "matar_arbol", side_effect=lambda pid: muertos.append(pid) or mata
        ), mock.patch.object(pc, "imagen_de", return_value=""), mock.patch.object(
            pc.time, "sleep", side_effect=reloj.dormir
        ), mock.patch.object(pc.time, "monotonic", reloj):
            codigo = pc.correr_vigilante(
                carpeta, ["powershell", "-File", "supervisor.ps1"], carpeta, "S-1", 1, Windows()
            )
        return codigo, lanzados, muertos

    def test_el_supervisor_relanzado_se_crea_sin_breakaway_desde_un_vigilante_fuera_del_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)
            reloj_ref = Reloj()

            def detener():
                (carpeta / "vigilante.parar").write_text("parar")

            class Muere:
                pid = 0

                def poll(self):
                    return 1

                def __init__(self, al_esperar=None):
                    self.al_esperar = al_esperar

                def wait(self, timeout=None):
                    if self.al_esperar:
                        self.al_esperar()
                    return 1

            procesos = [Muere(), Muere(), Muere(detener)]
            valida = {"sid": "S-1", "session": 1, "in_job": False}
            codigo, lanzados, _ = self._correr(carpeta, valida, procesos)
            self.assertEqual(codigo, 0)
            self.assertEqual(len(lanzados), 3)
            for kw in lanzados:
                self.assertEqual(kw["creationflags"], pc.CREATE_NO_WINDOW)
                self.assertFalse(kw["creationflags"] & BREAKAWAY)
            estado = json.loads((carpeta / "vigilante.json").read_text())
            self.assertEqual((estado["lanzamientos"], estado["parado"]), (3, True))
            self.assertFalse((carpeta / "vigilante.parar").exists())
            log = (carpeta / "vigilante.log").read_text()
            self.assertIn("se vuelve a levantar en 5 s", log)
            self.assertIn("se vuelve a levantar en 10 s", log)
            del reloj_ref

    def test_un_supervisor_que_nace_dentro_de_un_job_se_cierra_y_no_cuenta_como_en_marcha(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)

            class Nace:
                pid = 0

                def poll(self):
                    return 1

                def wait(self, timeout=None):
                    (carpeta / "vigilante.parar").write_text("parar")
                    return 1

            en_job = {"sid": "S-1", "session": 1, "in_job": True}
            _, lanzados, muertos = self._correr(
                carpeta, en_job, [Nace()],
                al_lanzar=lambda: (carpeta / "vigilante.parar").write_text("parar"),
            )
            self.assertEqual(len(lanzados), 1)
            self.assertEqual(muertos, [101])
            self.assertNotIn("supervisor", json.loads((carpeta / "vigilante.json").read_text()))
            self.assertIn("RuntimeError", (carpeta / "vigilante.log").read_text())


    def _vivo(self, cierra_con_terminate):
        class Vivo:
            pid = 0
            terminados = 0

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("supervisor", timeout)

            def terminate(self):
                Vivo.terminados += 1
                self.cerrado = cierra_con_terminate

            cerrado = False

            def poll(self):
                return 1 if self.cerrado else None

        return Vivo()

    def test_si_taskkill_falla_al_parar_se_intenta_con_el_proceso_y_se_guarda_si_sigue_vivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)
            valida = {"sid": "S-1", "session": 1, "in_job": False}
            vivo = self._vivo(cierra_con_terminate=False)
            self._correr(
                carpeta, valida, [vivo], mata=False,
                al_lanzar=lambda: (carpeta / "vigilante.parar").write_text("parar"),
            )
            log = (carpeta / "vigilante.log").read_text()
            self.assertIn("taskkill no pudo cerrar el supervisor 101", log)
            self.assertIn("sigue vivo", log)
            estado = json.loads((carpeta / "vigilante.json").read_text())
            self.assertEqual((estado["supervisor_pid"], estado["supervisor_inicio"]), (101, 1101))
            self.assertGreaterEqual(type(vivo).terminados, 1)

    def test_si_taskkill_falla_pero_terminate_cierra_no_queda_nada_vivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)
            valida = {"sid": "S-1", "session": 1, "in_job": False}
            self._correr(
                carpeta, valida, [self._vivo(cierra_con_terminate=True)], mata=False,
                al_lanzar=lambda: (carpeta / "vigilante.parar").write_text("parar"),
            )
            log = (carpeta / "vigilante.log").read_text()
            self.assertNotIn("sigue vivo", log)
            self.assertIsNone(json.loads((carpeta / "vigilante.json").read_text())["supervisor_pid"])

    def test_un_supervisor_rechazado_que_no_se_puede_cerrar_se_informa(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)
            en_job = {"sid": "S-1", "session": 1, "in_job": True}
            self._correr(
                carpeta, en_job, [self._vivo(cierra_con_terminate=False)], mata=False,
                al_lanzar=lambda: (carpeta / "vigilante.parar").write_text("parar"),
            )
            self.assertIn("rechazado y no se pudo cerrar", (carpeta / "vigilante.log").read_text())

    def test_matar_arbol_solo_da_por_cerrado_lo_que_taskkill_cerro(self):
        for codigo, esperado in ((0, True), (128, True), (1, False), (5, False)):
            with mock.patch.object(
                pc.subprocess, "run", return_value=mock.Mock(returncode=codigo)
            ):
                self.assertEqual(pc.matar_arbol(7), esperado, codigo)
        for fallo in (subprocess.TimeoutExpired("taskkill", 20), OSError("no hay taskkill")):
            with mock.patch.object(pc.subprocess, "run", side_effect=fallo):
                self.assertFalse(pc.matar_arbol(7))


class EsperasDelArranque(unittest.TestCase):
    def test_cada_espera_cubre_los_timeouts_de_lo_que_espera(self):
        # El vigilante, antes de su primer supervisor, puede comprobar (tasklist) y cerrar
        # (taskkill) a un huérfano; quien lo espera no puede rendirse antes.
        self.assertGreater(pc.ESPERA_ARRANQUE_S, pc.TIMEOUT_TASKLIST_S + pc.TIMEOUT_TASKKILL_S)
        self.assertGreater(pc.ESPERA_PUENTE_S, pc.ESPERA_ARRANQUE_S)
        self.assertGreater(pc.ESPERA_INFORME_S, pc.ESPERA_PUENTE_S)

    def test_main_usa_esas_esperas_y_no_numeros_sueltos(self):
        fuente = RUTA.read_text(encoding="utf-8")
        self.assertIn("timeout=ESPERA_PUENTE_S", fuente)
        self.assertIn("time.monotonic() + ESPERA_INFORME_S", fuente)
        self.assertIn("timeout=TIMEOUT_TASKLIST_S", fuente)
        self.assertIn("timeout=TIMEOUT_TASKKILL_S", fuente)


class UnSoloVigilante(unittest.TestCase):
    def test_un_segundo_vigilante_no_arranca_mientras_haya_otro(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "vigilante.lock"
            primero = pc.adquirir_exclusivo(ruta)
            self.assertIsNotNone(primero)
            self.assertIsNone(pc.adquirir_exclusivo(ruta))
            primero.close()
            otro = pc.adquirir_exclusivo(ruta)
            self.assertIsNotNone(otro)
            otro.close()

    def test_el_segundo_vigilante_sale_con_el_codigo_de_ya_activo(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp)
            candado = pc.adquirir_exclusivo(carpeta / "vigilante.lock")
            with mock.patch.object(pc.subprocess, "Popen") as popen:
                self.assertEqual(
                    pc.correr_vigilante(carpeta, ["x"], carpeta, "S-1", 1, mock.Mock()),
                    pc.CODIGO_YA_ACTIVO,
                )
            popen.assert_not_called()
            candado.close()

    def _huerfano(self, previo, imagen="powershell.exe", inicio=555, mata=True):
        muertos: list[int] = []
        notas: list[str] = []

        def inicio_de(_pid):
            if isinstance(inicio, Exception):
                raise inicio
            return inicio

        def matar(pid):
            muertos.append(pid)
            return mata

        cerrado = pc.limpiar_huerfano(previo, lambda _p: imagen, inicio_de, matar, notas.append)
        return cerrado, muertos, notas

    def test_un_supervisor_huerfano_se_cierra_solo_si_es_el_mismo_proceso(self):
        previo = {"supervisor_pid": 77, "supervisor_inicio": 555}
        self.assertEqual(self._huerfano(previo)[:2], (True, [77]))
        self.assertEqual(self._huerfano(previo, imagen="PowerShell.exe")[:2], (True, [77]))

    def test_un_pid_reutilizado_no_se_toca_aunque_sea_otro_powershell(self):
        previo = {"supervisor_pid": 77, "supervisor_inicio": 555}
        self.assertEqual(self._huerfano(previo, inicio=999)[:2], (False, []))
        self.assertEqual(self._huerfano(previo, imagen="notepad.exe")[:2], (False, []))
        self.assertEqual(self._huerfano(previo, imagen="")[:2], (False, []))
        self.assertEqual(self._huerfano(previo, inicio=None)[:2], (False, []))

    def test_sin_instante_guardado_o_sin_poder_comprobar_no_se_cierra_nada(self):
        self.assertEqual(self._huerfano({"supervisor_pid": 77})[:2], (False, []))
        self.assertEqual(self._huerfano({"supervisor_inicio": 555})[:2], (False, []))
        cerrado, muertos, notas = self._huerfano(
            {"supervisor_pid": 77, "supervisor_inicio": 555}, inicio=OSError("acceso denegado")
        )
        self.assertEqual((cerrado, muertos), (False, []))
        self.assertTrue(any("no se pudo comprobar" in n for n in notas))

    def test_si_taskkill_no_cierra_al_huerfano_no_se_da_por_cerrado(self):
        previo = {"supervisor_pid": 77, "supervisor_inicio": 555}
        cerrado, muertos, notas = self._huerfano(previo, mata=False)
        self.assertEqual((cerrado, muertos), (False, [77]))
        self.assertTrue(any("taskkill no pudo" in n for n in notas))


class ArranqueDelVigilante(unittest.TestCase):
    class Hijo:
        def __init__(self, pid, codigo=None):
            self.pid, self.codigo, self.terminado = pid, codigo, False

        def poll(self):
            return self.codigo

        def terminate(self):
            self.terminado = True

    def _esperar(self, estado, hijo, segundos=1):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "vigilante.json"
            if estado is not None:
                ruta.write_text(json.dumps(estado))
            reloj = Reloj()
            return pc.esperar_vigilante(ruta, hijo, segundos, reloj, reloj.dormir)

    def test_acepta_al_vigilante_que_dejo_un_supervisor_valido(self):
        estado = {"vigilante_pid": 9, "supervisor": {"pid": 10}}
        visto = self._esperar(estado, self.Hijo(9))
        self.assertFalse(visto["ya_activo"])

    def test_si_ya_habia_un_vigilante_informa_el_suyo(self):
        estado = {"vigilante_pid": 5, "supervisor": {"pid": 6}}
        visto = self._esperar(estado, self.Hijo(9, pc.CODIGO_YA_ACTIVO))
        self.assertTrue(visto["ya_activo"])

    def test_el_estado_de_un_vigilante_anterior_no_vale(self):
        hijo = self.Hijo(9)
        with self.assertRaisesRegex(RuntimeError, "a tiempo"):
            self._esperar({"vigilante_pid": 5, "supervisor": {"pid": 6}}, hijo)
        self.assertTrue(hijo.terminado)

    def test_un_vigilante_que_muere_se_informa(self):
        with self.assertRaisesRegex(RuntimeError, "código 1"):
            self._esperar(None, self.Hijo(9, 1))


class ParadaDelOperador(unittest.TestCase):
    def _main(self, home, *argv):
        with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}), mock.patch.object(
            sys, "argv", ["iniciar_encargos_pc.py", *argv]
        ), redirect_stdout(io.StringIO()) as salida:
            pc.main()
        return json.loads(salida.getvalue())

    def test_parar_deja_la_peticion_y_estado_muestra_el_vigilante(self):
        with tempfile.TemporaryDirectory() as home:
            carpeta = Path(home) / ".motor-encargos"
            self.assertEqual(self._main(home, "--estado"), {})
            carpeta.mkdir()
            (carpeta / "vigilante.json").write_text(json.dumps({"vigilante_pid": 4}))
            self.assertEqual(self._main(home, "--parar"), {"vigilante_pid": 4})
            self.assertTrue((carpeta / "vigilante.parar").is_file())


if __name__ == "__main__":
    unittest.main()
