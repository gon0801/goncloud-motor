"""Una parada no pierde el comando vivo ni consume una receta nunca enviada."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from motor import plataforma
from motor.encargos import ciclo, despliegue as ejecutor, politica_despliegue
from tests import test_encargos_despliegue_real as base


class RecuperacionDespliegue(unittest.TestCase):
    setUp = base.DespliegueReal.setUp

    def test_parada_antes_de_enviar_permite_desplegar_mismo_sha_al_continuar(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        sha = integrar()
        preparar_real = ejecutor._git
        parada = False

        def preparar(repo, *args):
            nonlocal parada
            resultado = preparar_real(repo, *args)
            if args[:2] == ("worktree", "add") and not parada:
                parada = True
                ciclo.ordenar(reg, ident, "parar", "parar-preparacion", base.HORA)
            return resultado

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        with mock.patch.object(ejecutor, "_git", side_effect=preparar), mock.patch.object(
            plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso,
        ):
            base.paso(self.despliegue)
            self.assertFalse((destino / "pasos").exists())
            ciclo.barrer(reg, base.HORA, time.time(), None)
            self.assertEqual(reg.fold(ident).estado, "detenido")
            ciclo.ordenar(reg, ident, "continuar", "seguir-preparacion", base.HORA)
            final = base.paso(self.despliegue)

        self.assertEqual(final.nivel_alcanzado, "desplegado")
        self.assertEqual(final.fase_actual.integrado_sha, sha)
        self.assertEqual((destino / "pasos").read_text(encoding="utf-8").splitlines(), [
            f"desplegar:{sha}", f"checklist:{sha}",
        ])
        recibos = list((reg.base / "despliegues").rglob("recibo.json"))
        self.assertEqual(len(recibos), 1)
        self.assertEqual(json.loads(recibos[0].read_text(encoding="utf-8"))["estado"], "aplicado")

    def test_worker_muerto_no_da_por_detenido_su_comando_vivo(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        integrar()
        worker = plataforma.crear_proceso(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            plataforma.Propiedad.COMANDO_CONTENIDO,
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        worker.liberar()
        marca_worker = self.marca(worker)
        worker.proceso.communicate(timeout=5)
        self.assertEqual(plataforma.estado_proc(marca_worker), "muerto")

        comando = plataforma.crear_proceso(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            plataforma.Propiedad.COMANDO_CONTENIDO,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        comando.liberar()
        self.addCleanup(self.terminar, comando)
        marca_comando = self.marca(comando)
        with mock.patch.object(plataforma, "lanzar_separado", return_value=marca_worker):
            resultado = ejecutor.iniciar(
                reg, reg.fold(ident), espacio,
                politica_despliegue.resolver(espacio, reg.fold(ident)), base.HORA,
            )
        recibo = Path(resultado["recibo"])
        (recibo.parent / "comando.json").write_text(json.dumps(marca_comando), encoding="utf-8")
        ciclo.ordenar(reg, ident, "parar", "parar-huerfano", base.HORA)

        # Un cierre que aún no consigue matar el grupo no acredita la parada.
        with mock.patch.object(plataforma, "matar_grupo_pid"), mock.patch.object(
            plataforma, "cerrar_huerfanos",
        ):
            self.assertNotEqual(ejecutor.observacion(reg, reg.fold(ident)), "ausente")
            ciclo.barrer(reg, base.HORA, time.time(), None)
            self.assertIsNone(comando.proceso.poll())
            self.assertEqual(reg.fold(ident).estado, "deteniendo")

        self.terminar(comando)
        self.assertEqual(ejecutor.observacion(reg, reg.fold(ident)), "ausente")
        ciclo.barrer(reg, base.HORA, time.time(), None)
        self.assertEqual(reg.fold(ident).estado, "detenido")
        ciclo.ordenar(reg, ident, "continuar", "seguir-huerfano", base.HORA)
        final = base.paso(self.despliegue)
        self.assertEqual(final.estado, "necesita_usuario")
        self.assertEqual(final.nivel_alcanzado, "integrado")
        self.assertEqual(json.loads(recibo.read_text(encoding="utf-8"))["estado"], "incierto")
        self.assertFalse((destino / "pasos").exists())

    def test_el_worker_guarda_la_identidad_del_comando_mientras_esta_vivo(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        liberar = destino / "liberar"
        with (espacio / "d.sh").open("a", encoding="utf-8") as script:
            script.write(f'while [ ! -f "{liberar.as_posix()}" ]; do sleep 0.05; done\n')
        integrar()
        try:
            ciclo._paso_despliegue(reg, reg.fold(ident), espacio, base.HORA)
            fin = time.monotonic() + 10
            while not (destino / "pasos").exists():
                self.assertLess(time.monotonic(), fin)
                time.sleep(0.05)
            recibo, = (reg.base / "despliegues").rglob("recibo.json")
            marca = json.loads((recibo.parent / "comando.json").read_text(encoding="utf-8"))
            self.assertTrue(marca["inicio"])
            self.assertEqual(marca["boot_id"], plataforma.boot_id())
            self.assertEqual(plataforma.presencia_grupo(marca["pid"], marca["inicio"], marca["job"]), "vivo")
        finally:
            liberar.touch()
            base.paso(self.despliegue)

    def test_error_despues_de_un_efecto_queda_incierto_sin_reintentar(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        integrar()
        efecto = destino / "efecto"

        def efecto_sin_respuesta(*args):
            with efecto.open("a", encoding="utf-8") as f:
                f.write("enviado\n")
            raise OSError("se perdió la lectura después del envío")

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        with mock.patch.object(ejecutor, "_correr", side_effect=efecto_sin_respuesta), mock.patch.object(
            plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso,
        ):
            final = base.paso(self.despliegue)
            recibo, = (reg.base / "despliegues").rglob("recibo.json")
            datos = json.loads(recibo.read_text(encoding="utf-8"))
            self.assertEqual(datos["estado"], "incierto")
            self.assertIsNone(datos["pasos"][0]["enviado"])
            self.assertEqual(final.nivel_alcanzado, "integrado")
            self.assertEqual(final.estado, "necesita_usuario")
            ciclo.ordenar(reg, ident, "parar", "parar-efecto-incierto", base.HORA)
            ciclo.barrer(reg, base.HORA, time.time(), None)
            self.assertEqual(reg.fold(ident).estado, "detenido")
            ciclo.ordenar(reg, ident, "continuar", "seguir-efecto-incierto", base.HORA)
            final = reg.fold(ident)
            self.assertEqual(final.estado, "recibido")
            resultado = ejecutor.iniciar(
                reg, final, espacio, politica_despliegue.resolver(espacio, final), base.HORA,
            )
        self.assertEqual(resultado["estado"], "incierto")
        self.assertEqual(efecto.read_text(encoding="utf-8"), "enviado\n")
        self.assertFalse((destino / "pasos").exists())

    @unittest.skipIf(os.name == "nt", "la supervivencia a SIGTERM se comprueba en POSIX")
    def test_hijo_que_ignora_term_muere_antes_del_checklist(self):
        reg, ident, espacio, destino, integrar = self.despliegue
        pidfile = destino / "hijo.pid"
        (espacio / "hijo.py").write_text(
            "import os, signal, time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"Path({str(pidfile)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(60)\n", encoding="utf-8",
        )
        with (espacio / "d.sh").open("a", encoding="utf-8") as script:
            script.write(
                f"{shlex.quote(sys.executable)} hijo.py &\n"
                f'while [ ! -s "{pidfile.as_posix()}" ]; do sleep 0.01; done\n'
            )
        integrar()
        correr_real = ejecutor._correr
        hijo = {}
        checklists = []

        def correr(registro, encargo, paso, *args):
            if paso == "checklist":
                self.assertFalse(plataforma.pid_vivo(hijo["pid"]))
                anterior = json.loads((args[1].parent / "comando.json").read_text(encoding="utf-8"))
                self.assertEqual(plataforma.presencia_grupo(
                    anterior["pid"], anterior["inicio"], anterior["job"],
                ), "ausente")
                checklists.append(paso)
            try:
                return correr_real(registro, encargo, paso, *args)
            finally:
                if paso == "desplegar" and pidfile.exists():
                    hijo["pid"] = int(pidfile.read_text(encoding="utf-8"))
                    hijo["inicio"] = plataforma.hora_inicio(hijo["pid"])

        def ejecutar_en_proceso(cmd, cwd, env, log):
            ejecutor.ejecutar(Path(cmd[-1]))
            return {"pid": 99999999, "inicio": "", "boot_id": plataforma.boot_id(), "job": ""}

        try:
            with mock.patch.object(ejecutor, "_correr", side_effect=correr), mock.patch.object(
                plataforma, "lanzar_separado", side_effect=ejecutar_en_proceso,
            ), mock.patch.object(ejecutor, "TOPE_S", 5):
                final = base.paso(self.despliegue)
            self.assertTrue(hijo)
            self.assertFalse(plataforma.pid_vivo(hijo["pid"]))
            self.assertEqual(final.nivel_alcanzado, "desplegado")
            self.assertEqual(checklists, ["checklist"])
        finally:
            if (hijo.get("inicio") and plataforma.pid_vivo(hijo["pid"])
                    and plataforma.hora_inicio(hijo["pid"]) == hijo["inicio"]):
                try:
                    os.kill(hijo["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @staticmethod
    def marca(creado):
        return {"pid": creado.proceso.pid, "inicio": plataforma.hora_inicio(creado.proceso.pid),
                "boot_id": plataforma.boot_id(), "job": creado.job}

    @staticmethod
    def terminar(creado):
        if creado.proceso.poll() is None:
            plataforma.matar_grupo(creado.proceso)
        creado.proceso.wait(timeout=5)
