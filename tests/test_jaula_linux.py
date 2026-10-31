"""Jaula de bubblewrap. Las pruebas con jaula real corren en Linux con bwrap (CI instala
bubblewrap y exporta MOTOR_EXIGE_BWRAP=1: allí una jaula que no arranca es un fallo, no un skip)."""

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from motor.plataforma import jaula_linux
from motor.plataforma.jaula_linux import RED_ABIERTA, RED_NINGUNA, Jaula

CAUSA = (
    jaula_linux.disponible()
    if sys.platform.startswith("linux")
    else "la jaula de bubblewrap solo corre en Linux"
)
EXIGIDA = os.environ.get("MOTOR_EXIGE_BWRAP") == "1"

PUSH = (
    "git init -q a && git init -q --bare b.git && cd a && "
    "git -c user.email=x@x -c user.name=x commit -q --allow-empty -m uno && "
    "git push -q ../b.git HEAD:main && echo ok"
)


class DisponibilidadTest(unittest.TestCase):
    @unittest.skipUnless(EXIGIDA, "solo donde CI exige bubblewrap")
    def test_ci_tiene_una_jaula_que_arranca(self):
        self.assertEqual(CAUSA, "")


class ArgvTest(unittest.TestCase):
    """El argv se arma en cualquier sistema; que la jaula cierre lo prueba JaulaRealTest."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="jaula-argv-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.espacio = self.base / "espacio" / "enc_1"
        self.runtime = self.base / "runtime" / "enc_1"

    def argv(self, **opciones):
        jaula = Jaula(str(self.espacio), str(self.runtime), **opciones)
        return jaula_linux.argv_jaula(jaula, ["bash", "-c", "true"])

    def montado(self, argv, ruta):
        return [
            argv[i]
            for i in range(len(argv) - 2)
            if argv[i] in ("--bind", "--ro-bind") and argv[i + 2] == str(ruta)
        ]

    def test_el_responsable_escribe_en_el_espacio_y_el_revisor_solo_lee(self):
        self.assertEqual(self.montado(self.argv(), self.espacio), ["--bind"])
        self.assertEqual(
            self.montado(self.argv(lectura=True), self.espacio), ["--ro-bind"]
        )
        self.assertEqual(
            self.montado(self.argv(lectura=True), self.runtime), ["--bind"]
        )

    def test_la_raiz_queda_de_solo_lectura_despues_de_montar_todo(self):
        argv = self.argv()
        ultimo_montaje = max(i for i, x in enumerate(argv) if x.endswith("bind"))
        self.assertGreater(argv.index("--remount-ro"), ultimo_montaje)

    def test_no_monta_ningun_tmpfs_que_el_remount_ro_de_la_raiz_no_cubre(self):
        self.assertNotIn("--tmpfs", self.argv())
        self.assertNotIn("--tmpfs", self.argv(lectura=True))

    def test_no_monta_el_home_ni_mnt(self):
        argv = self.argv(lecturas=(str(self.base / "fuente"),))
        montados = [argv[i + 1] for i, x in enumerate(argv) if x.endswith("bind")]
        for ruta in montados:
            self.assertFalse(ruta.startswith(("/home", "/mnt", "/root")), ruta)

    def test_la_red_ninguna_no_comparte_la_red(self):
        self.assertIn("--share-net", self.argv(red=RED_ABIERTA))
        self.assertNotIn("--share-net", self.argv(red=RED_NINGUNA))

    def test_rechaza_rutas_relativas_y_runtime_dentro_del_espacio(self):
        with self.assertRaises(ValueError):
            jaula_linux.argv_jaula(Jaula("espacio", str(self.runtime)), ["true"])
        with self.assertRaises(ValueError):
            jaula_linux.argv_jaula(
                Jaula(str(self.espacio), str(self.espacio / "runtime")), ["true"]
            )

    def test_un_ejecutable_enlazado_entra_con_su_destino_y_no_con_su_carpeta(self):
        real = self.base / "instalacion" / "versiones" / "2.1"
        real.parent.mkdir(parents=True)
        real.write_text("#!/bin/sh\n")
        enlace = self.base / "bin" / "claude"
        enlace.parent.mkdir()
        enlace.symlink_to(real)
        argv = self.argv(ejecutables=(str(enlace),))
        self.assertEqual(self.montado(argv, real.resolve()), ["--ro-bind"])
        self.assertEqual(self.montado(argv, real.parent), [])
        i = argv.index("--symlink")
        self.assertEqual(argv[i : i + 3], ["--symlink", str(real.resolve()), str(enlace)])


@unittest.skipIf(CAUSA, CAUSA)
class JaulaRealTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="jaula-real-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.espacio = self.base / "espacio" / "enc_1"
        self.runtime = self.base / "runtime" / "enc_1"
        self.otro = self.base / "otro"
        for carpeta in (self.espacio, self.runtime / "tmp", self.otro):
            carpeta.mkdir(parents=True)
        (self.espacio / "leeme").write_text("producto")

    def jaula(self, **opciones):
        return Jaula(str(self.espacio), str(self.runtime), **opciones)

    def correr(self, script, extra=None, **opciones):
        jaula = self.jaula(**opciones)
        return subprocess.run(
            jaula_linux.argv_jaula(jaula, ["/bin/bash", "-c", script]),
            env=jaula_linux.entorno_jaula(jaula, extra),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_escribe_en_el_espacio_y_en_el_runtime(self):
        r = self.correr(f"echo a > {self.espacio}/nuevo && echo b > $TMPDIR/nuevo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.espacio / "nuevo").read_text(), "a\n")
        self.assertEqual((self.runtime / "tmp" / "nuevo").read_text(), "b\n")

    def test_escribir_fuera_del_espacio_falla(self):
        # base, espacio/ y runtime/ existen dentro de la jaula solo como padres que bwrap crea
        # para montar enc_1: ahí tampoco se escribe, ni se crea la carpeta que falta.
        destinos = [
            self.otro / "x",
            self.base / "x",
            self.base / "espacio" / "x",
            self.base / "espacio" / "enc_2" / "x",
            self.base / "runtime" / "enc_2",
            Path("/nuevo"),
            Path("/tmp/x"),
            Path("/usr/x"),
            Path("/etc/x"),
            Path("/proc/x"),
        ]
        for destino in destinos:
            with self.subTest(destino=str(destino)):
                r = self.correr(f"echo x > {destino}")
                self.assertNotEqual(r.returncode, 0)
                r = self.correr(f"mkdir -p {destino.parent} && echo x > {destino}")
                self.assertNotEqual(r.returncode, 0)
        self.assertEqual(list(self.otro.iterdir()), [])
        self.assertEqual(
            sorted(p.name for p in self.base.iterdir()), ["espacio", "otro", "runtime"]
        )
        self.assertFalse(Path("/nuevo").exists())

    def test_el_revisor_no_escribe_en_el_espacio_y_si_lee_y_escribe_en_su_runtime(self):
        r = self.correr(f"cat {self.espacio}/leeme", lectura=True)
        self.assertEqual((r.returncode, r.stdout), (0, "producto"), r.stderr)
        for orden in (
            f"echo x > {self.espacio}/nuevo",
            f"echo x >> {self.espacio}/leeme",
            f"rm {self.espacio}/leeme",
        ):
            with self.subTest(orden=orden):
                self.assertNotEqual(self.correr(orden, lectura=True).returncode, 0)
        self.assertEqual((self.espacio / "leeme").read_text(), "producto")
        self.assertEqual(self.correr("echo v > $TMPDIR/juicio", lectura=True).returncode, 0)

    def test_leer_el_home_falla(self):
        home = Path.home()
        if not os.access(home, os.W_OK):
            self.skipTest("el home no admite crear el secreto de la prueba")
        carpeta = Path(tempfile.mkdtemp(prefix="secreto-", dir=home))
        self.addCleanup(shutil.rmtree, carpeta, True)
        (carpeta / "llave").write_text("no se lee")
        for orden in (f"cat {carpeta}/llave", f"ls {home}", f"ls {carpeta}"):
            with self.subTest(orden=orden):
                r = self.correr(orden)
                self.assertNotEqual(r.returncode, 0)
                self.assertNotIn("no se lee", r.stdout)

    def test_no_existe_nada_del_anfitrion_que_no_se_concedio(self):
        for ruta in ("/mnt/c", "/mnt", "/home", "/root", "/var"):
            with self.subTest(ruta=ruta):
                self.assertEqual(self.correr(f"test -e {ruta}").returncode, 1)

    def test_bash_y_git_push_a_un_bare_local_funcionan(self):
        r = self.correr(f"cd {self.espacio} && {PUSH}")
        self.assertEqual((r.returncode, r.stdout.split()[-1:]), (0, ["ok"]), r.stderr)
        subido = subprocess.run(
            ["git", "--git-dir", str(self.espacio / "b.git"), "rev-parse", "main"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(subido.returncode, 0, subido.stderr)

    def test_el_revisor_hace_git_push_en_su_temporal(self):
        r = self.correr(f"cd $TMPDIR && {PUSH}", lectura=True)
        self.assertEqual((r.returncode, r.stdout.split()[-1:]), (0, ["ok"]), r.stderr)

    def test_las_fuentes_autorizadas_se_leen_y_no_se_escriben(self):
        fuente = self.base / "fuentes" / "repo"
        fuente.mkdir(parents=True)
        (fuente / "dato").write_text("autorizado")
        (self.base / "fuentes" / "vecina").mkdir()
        r = self.correr(f"cat {fuente}/dato", lecturas=(str(fuente),))
        self.assertEqual((r.returncode, r.stdout), (0, "autorizado"), r.stderr)
        self.assertNotEqual(
            self.correr(f"echo x > {fuente}/dato", lecturas=(str(fuente),)).returncode, 0
        )
        self.assertNotEqual(
            self.correr(f"ls {fuente.parent}/vecina", lecturas=(str(fuente),)).returncode, 0
        )

    def test_un_ejecutable_enlazado_corre_y_su_carpeta_no_se_ve(self):
        real = self.base / "instalacion" / "versiones" / "2.1"
        real.parent.mkdir(parents=True)
        real.write_text("#!/bin/sh\necho agente-ok\n")
        real.chmod(0o755)
        (real.parent / "hermano").write_text("no entra")
        enlace = self.base / "bin" / "agente"
        enlace.parent.mkdir()
        enlace.symlink_to(real)
        r = self.correr(str(enlace), ejecutables=(str(enlace),))
        self.assertEqual((r.returncode, r.stdout), (0, "agente-ok\n"), r.stderr)
        r = self.correr(f"cat {real.parent}/hermano", ejecutables=(str(enlace),))
        self.assertNotEqual(r.returncode, 0)

    def test_el_entorno_no_hereda_del_anfitrion(self):
        r = subprocess.run(
            jaula_linux.argv_jaula(self.jaula(), ["/usr/bin/env"]),
            env={**jaula_linux.entorno_jaula(self.jaula()), "PATH": jaula_linux.PATH_JAULA},
            capture_output=True,
            text=True,
        )
        self.assertIn(f"HOME={self.runtime}", r.stdout.splitlines())

    def test_con_red_ninguna_no_se_llega_ni_al_loopback_del_anfitrion(self):
        servidor = socket.socket()
        self.addCleanup(servidor.close)
        servidor.bind(("127.0.0.1", 0))
        servidor.listen(1)
        puerto = servidor.getsockname()[1]
        script = (
            "python3 -c \"import socket;socket.create_connection(('127.0.0.1',"
            f"{puerto}),3);print('conecto')\""
        )
        abierta = self.correr(script, red=RED_ABIERTA)
        self.assertEqual((abierta.returncode, abierta.stdout.strip()), (0, "conecto"), abierta.stderr)
        cerrada = self.correr(script, red=RED_NINGUNA)
        self.assertNotEqual(cerrada.returncode, 0)

    def test_matar_a_bwrap_no_deja_nada_vivo_dentro(self):
        marca = f"31{os.getpid()}.5"
        jaula = self.jaula()
        p = subprocess.Popen(
            jaula_linux.argv_jaula(
                jaula,
                ["/bin/bash", "-c", f"(setsid sleep {marca} &) ; sleep {marca} & wait"],
            ),
            env=jaula_linux.entorno_jaula(jaula),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def vivos():
            hallados = []
            for d in Path("/proc").iterdir():
                if d.name.isdigit():
                    try:
                        if marca in (d / "cmdline").read_text().replace("\0", " "):
                            hallados.append(int(d.name))
                    except OSError:
                        pass
            return [x for x in hallados if x != os.getpid()]

        fin = time.time() + 10
        while len(vivos()) < 2 and time.time() < fin:
            time.sleep(0.05)
        self.addCleanup(lambda: [os.kill(x, signal.SIGKILL) for x in vivos()])
        self.assertGreaterEqual(len(vivos()), 2)
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
        fin = time.time() + 10
        while vivos() and time.time() < fin:
            time.sleep(0.05)
        self.assertEqual(vivos(), [])


if __name__ == "__main__":
    unittest.main()
