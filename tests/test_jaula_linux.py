"""Jaula de bubblewrap. Las pruebas con jaula real corren en Linux con bwrap (CI instala
bubblewrap y exporta MOTOR_EXIGE_BWRAP=1: allí una jaula que no arranca es un fallo, no un skip)."""

import contextlib
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
from unittest import mock

from motor.plataforma import jaula_linux
from motor.plataforma.jaula_linux import RED_ABIERTA, RED_NINGUNA, RED_PROVEEDOR, Jaula

CAUSA = (
    jaula_linux.causa_no_disponible()
    if sys.platform.startswith("linux")
    else "la jaula de bubblewrap solo corre en Linux"
)
EXIGIDA = os.environ.get("MOTOR_EXIGE_BWRAP") == "1"

PUSH = (
    "git init -q a && git init -q --bare b.git && cd a && "
    "git -c user.email=x@x -c user.name=x commit -q --allow-empty -m uno && "
    "git push -q ../b.git HEAD:main && echo ok"
)


def salida(r):
    return f"rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}"


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

    @staticmethod
    def destinos(argv):
        """Dónde aparece algo dentro de la jaula. El origen no cuenta: el de `resolv.conf`
        en WSL es /mnt/wsl/resolv.conf y se monta sobre /etc/resolv.conf."""
        con_origen = ("--bind", "--ro-bind", "--ro-bind-try", "--symlink")
        solos = ("--dir", "--proc", "--dev", "--tmpfs", "--remount-ro", "--chdir")
        return [
            argv[i + 2] if x in con_origen else argv[i + 1]
            for i, x in enumerate(argv)
            if x in con_origen + solos
        ]

    def test_no_monta_el_home_ni_mnt(self):
        argv = self.argv(lecturas=(str(self.base / "fuente"),))
        destinos = self.destinos(argv)
        for destino in destinos:
            self.assertFalse(destino.startswith(("/home", "/mnt", "/root")), destino)

    def test_con_el_etc_de_wsl_el_origen_de_mnt_no_crea_mnt_en_la_jaula(self):
        etc = self.base / "etc"
        etc.mkdir()
        (etc / "hosts").write_text("127.0.0.1 localhost\n")
        (etc / "resolv.conf").symlink_to("../mnt/wsl/resolv.conf")
        real = os.path.realpath
        es_archivo = os.path.isfile
        original = jaula_linux._etc
        with mock.patch.object(
            jaula_linux.os.path,
            "realpath",
            lambda p: "/mnt/wsl/resolv.conf" if p.endswith("etc/resolv.conf") else real(p),
        ), mock.patch.object(
            jaula_linux.os.path,
            "isfile",
            lambda p: p == "/mnt/wsl/resolv.conf" or es_archivo(p),
        ), mock.patch.object(jaula_linux, "_etc", lambda: original(str(etc))):
            argv = self.argv(lecturas=(str(self.base / "fuente"),))
        self.assertIn("/mnt/wsl/resolv.conf", argv)
        destinos = self.destinos(argv)
        self.assertIn("/etc/resolv.conf", destinos)
        for destino in destinos:
            self.assertFalse(destino.startswith(("/home", "/mnt", "/root")), destino)

    def test_resolv_conf_enlazado_a_mnt_de_wsl_se_monta_sin_crear_mnt(self):
        etc = self.base / "etc"
        etc.mkdir()
        (etc / "hosts").write_text("127.0.0.1 localhost\n")
        (etc / "localtime").symlink_to("/usr/share/zoneinfo/UTC")
        wsl = self.base / "mnt" / "wsl"
        wsl.mkdir(parents=True)
        (wsl / "resolv.conf").write_text("nameserver 10.255.255.254\n")
        (etc / "resolv.conf").symlink_to(wsl / "resolv.conf")
        argv = jaula_linux._etc(str(etc))
        self.assertEqual(argv[:2], ["--dir", "/etc"])
        self.assertIn(
            ["--ro-bind", str((wsl / "resolv.conf").resolve()), "/etc/resolv.conf"],
            [argv[i : i + 3] for i in range(len(argv) - 2)],
        )
        self.assertIn(
            ["--symlink", "/usr/share/zoneinfo/UTC", "/etc/localtime"],
            [argv[i : i + 3] for i in range(len(argv) - 2)],
        )
        destinos = [argv[i + 2] for i, x in enumerate(argv) if x in ("--ro-bind", "--symlink")]
        self.assertTrue(destinos)
        for destino in destinos:
            self.assertTrue(destino.startswith("/etc/"), destino)
        self.assertEqual(argv.count("--dir"), 1)

    def test_la_red_ninguna_no_comparte_la_red(self):
        self.assertIn("--share-net", self.argv(red=RED_ABIERTA))
        self.assertNotIn("--share-net", self.argv(red=RED_NINGUNA))

    def test_la_red_del_proveedor_no_comparte_la_del_anfitrion_y_pasa_por_el_puente(self):
        """Sin `--share-net` la jaula no tiene red propia: lo único que sale es el proxy que
        atiende el guardián, por el socket que entra en `/run/egreso` (#131 F2)."""
        egreso = self.base / "egreso"
        argv = self.argv(red=RED_PROVEEDOR, dominios=("api.anthropic.com",), egreso=str(egreso))
        self.assertNotIn("--share-net", argv)
        self.assertIn("--unshare-all", argv)
        self.assertIn(["--bind", str(egreso), "/run/egreso"], [argv[i : i + 3] for i in range(len(argv) - 2)])
        corte = argv.index("--")
        self.assertEqual(argv[corte + 1 : corte + 5], ["/usr/bin/python3", "-I", "-S", "-c"])
        self.assertEqual(argv[corte + 6 : corte + 9], ["/run/egreso/s", "3128", "--"])
        self.assertEqual(argv[-3:], ["bash", "-c", "true"])

    def test_la_red_del_proveedor_sin_dominios_o_sin_socket_no_arma_la_jaula(self):
        with self.assertRaises(ValueError):
            self.argv(red=RED_PROVEEDOR, egreso=str(self.base))
        with self.assertRaises(ValueError):
            self.argv(red=RED_PROVEEDOR, dominios=("api.anthropic.com",))

    def test_el_agente_de_la_red_del_proveedor_usa_el_proxy_del_puente(self):
        jaula = Jaula(str(self.espacio), str(self.runtime), red=RED_PROVEEDOR,
                      dominios=("a.com",), egreso=str(self.base))
        entorno = jaula_linux.entorno_jaula(jaula)
        self.assertEqual(entorno["HTTPS_PROXY"], "http://127.0.0.1:3128")
        self.assertEqual(entorno["https_proxy"], "http://127.0.0.1:3128")
        self.assertNotIn("HTTPS_PROXY", jaula_linux.entorno_jaula(Jaula(str(self.espacio), str(self.runtime))))

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
        self.assertIn(
            ["--symlink", str(real.resolve()), str(enlace)],
            [argv[i : i + 3] for i in range(len(argv) - 2)],
        )


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
                self.assertNotEqual(r.returncode, 0, salida(r))
                r = self.correr(f"mkdir -p {destino.parent} && echo x > {destino}")
                self.assertNotEqual(r.returncode, 0, salida(r))
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
                r = self.correr(f"test -e {ruta} || exit 1; ls -la {ruta}")
                self.assertEqual(r.returncode, 1, salida(r))

    def test_resolv_conf_se_lee_dentro_de_la_jaula(self):
        host = Path("/etc/resolv.conf")
        if not host.is_file():
            self.skipTest("el anfitrión no tiene /etc/resolv.conf legible")
        r = self.correr("cat /etc/resolv.conf")
        self.assertEqual((r.returncode, r.stdout), (0, host.read_text()), salida(r))

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

    def test_cerrar_la_jaula_no_deja_nada_vivo_dentro(self):
        """El SIGKILL al bwrap exterior no basta: en el bubblewrap 0.9 de Ubuntu 24.04 (CI)
        quedaban el init, bash y los dos sleep. `cerrar` mata primero lo de dentro."""
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
        self.assertTrue(jaula_linux.cerrar(p.pid))
        p.wait()
        fin = time.time() + 10
        while vivos() and time.time() < fin:
            time.sleep(0.05)
        self.assertEqual(vivos(), [])

    def test_cerrar_con_el_bwrap_ya_muerto_mata_lo_que_dejo_reparentado(self):
        """Lo que el guardián vio mientras el bwrap vivía (`ns_de_la_jaula`) cierra la jaula
        aunque el bwrap muera antes, que es cuando el árbol del bwrap ya no la encuentra
        (#113 F6)."""
        marca = f"32{os.getpid()}.5"
        jaula = self.jaula()
        p = subprocess.Popen(
            jaula_linux.argv_jaula(jaula, ["/bin/bash", "-c", f"sleep {marca} & wait"]),
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

        self.addCleanup(lambda: [os.kill(x, signal.SIGKILL) for x in vivos()])
        inicio = jaula_linux._inicio(p.pid)
        fin = time.time() + 10
        ns = ""
        while (not ns or not vivos()) and time.time() < fin:
            ns = jaula_linux.ns_de_la_jaula(p.pid)
            time.sleep(0.05)
        self.assertTrue(ns and vivos())
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
        self.assertTrue(jaula_linux.cerrar(p.pid, inicio=inicio, ns=ns))
        fin = time.time() + 10
        while vivos() and time.time() < fin:
            time.sleep(0.05)
        self.assertEqual(vivos(), [])

    def test_el_guardian_subreaper_cierra_la_jaula_aunque_deje_zombis_en_su_espacio(self):
        """Como el guardián: subreaper, solo recoge al bwrap. Lo que la jaula deja reparentado
        queda zombi en su espacio de pids hasta que alguien lo recoja, y un zombi no es un
        proceso vivo: `cerrar` debe dar la jaula por cerrada."""
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)  # PR_SET_CHILD_SUBREAPER
        self.addCleanup(libc.prctl, 36, 0, 0, 0, 0)
        marca = f"33{os.getpid()}.5"
        jaula = self.jaula()
        p = subprocess.Popen(
            jaula_linux.argv_jaula(jaula, ["/bin/bash", "-c", f"(sleep {marca} &) ; wait"]),
            env=jaula_linux.entorno_jaula(jaula),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        inicio = jaula_linux._inicio(p.pid)
        fin = time.time() + 10
        ns = ""
        while not ns and time.time() < fin:
            ns = jaula_linux.ns_de_la_jaula(p.pid)
            time.sleep(0.05)
        self.assertTrue(ns)
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
        self.assertTrue(jaula_linux.cerrar(p.pid, inicio=inicio, ns=ns, plazo=10))
        self.assertEqual(jaula_linux._en_ns(ns), [])


class DisponibleTest(unittest.TestCase):
    """`disponible()` devuelve bool como `jaula_windows.disponible` y `psec_windows.disponible`;
    la causa va aparte (#113 F2)."""

    def test_sin_bwrap_disponible_es_false_y_la_causa_dice_por_que(self):
        self.assertIs(jaula_linux.disponible("/no/existe/bwrap"), False)
        self.assertIn("/no/existe/bwrap", jaula_linux.causa_no_disponible("/no/existe/bwrap"))

    def test_disponible_es_verdadero_exactamente_cuando_no_hay_causa(self):
        with mock.patch.object(jaula_linux, "causa_no_disponible", return_value=""):
            self.assertIs(jaula_linux.disponible(), True)
        with mock.patch.object(jaula_linux, "causa_no_disponible", return_value="falta algo"):
            self.assertIs(jaula_linux.disponible(), False)


LISTDIR = os.listdir


class TablaDeProcesos:
    """Una tabla de procesos de juguete para `cerrar`: sin /proc, sin señales reales."""

    def __init__(self):
        self.procs = {}
        self.siguiente = 1000

    def nuevo(self, ppid, **rasgos):
        pid, self.siguiente = self.siguiente, self.siguiente + 1
        self.procs[pid] = {"ppid": ppid, "vivo": True, "parado": False, "forker": False,
                           "inmortal": False, "ns": "", "zombi": False, **rasgos}
        return pid

    def ns_pid(self, pid):
        """Como /proc/<pid>/ns/pid: un zombi, que sigue en la tabla, conserva su espacio."""
        d = self.procs.get(pid)
        return d["ns"] if d and d["vivo"] else ""

    def listdir(self, ruta):
        if ruta != "/proc":
            return LISTDIR(ruta)
        return [str(p) for p, d in self.procs.items() if d["vivo"]]

    def vivos(self):
        return [p for p in self.procs if self.vivo(p)]

    def descendientes(self, raiz):
        vivos = {p: d for p, d in self.procs.items() if d["vivo"]}
        vistos, frontera = [], [raiz]
        while frontera:
            hijos = [p for p, d in vivos.items() if d["ppid"] in frontera and p not in vistos]
            vistos += hijos
            frontera = hijos
        for p, d in vivos.items():
            if d["forker"] and not d["parado"]:
                self.nuevo(p)
        return vistos

    def vivo(self, pid):
        d = self.procs.get(pid, {})
        return bool(d.get("vivo") and not d.get("zombi"))

    def inicio(self, pid):
        d = self.procs.get(pid, {})
        return f"t{pid}" if d.get("vivo") else ""

    def kill(self, pid, senal):
        d = self.procs.get(pid)
        if not d or not d["vivo"]:
            raise ProcessLookupError(pid)
        if d["zombi"]:
            return
        if senal == signal.SIGSTOP:
            d["parado"] = True
        elif senal == signal.SIGKILL and not d["inmortal"]:
            d["vivo"] = False
            for hijo in self.procs.values():
                if hijo["ppid"] == pid and hijo["vivo"]:
                    hijo["ppid"] = 1

    def parchear(self):
        return (
            mock.patch.object(jaula_linux, "_descendientes", self.descendientes),
            mock.patch.object(jaula_linux, "_vivo", self.vivo),
            mock.patch.object(jaula_linux, "_inicio", self.inicio),
            mock.patch.object(jaula_linux, "_ns_pid", self.ns_pid),
            mock.patch.object(jaula_linux.os, "listdir", self.listdir),
            mock.patch.object(jaula_linux.os, "kill", self.kill),
            mock.patch.object(jaula_linux.os, "waitpid", side_effect=ChildProcessError),
        )

    def cerrar(self, raiz, **opciones):
        with contextlib.ExitStack() as pila:
            for parche in self.parchear():
                pila.enter_context(parche)
            return jaula_linux.cerrar(raiz, **opciones)


class CerrarTest(unittest.TestCase):
    """`cerrar` no puede dar la jaula por cerrada mientras viva algo que fue suyo (#113 F6)."""

    def test_un_hijo_que_nace_entre_el_recorrido_y_el_kill_no_queda_huerfano(self):
        t = TablaDeProcesos()
        bwrap = t.nuevo(1)
        init = t.nuevo(bwrap)
        t.nuevo(init)
        t.nuevo(init, forker=True)
        self.assertTrue(t.cerrar(bwrap))
        self.assertEqual([p for p in t.procs if t.vivo(p)], [])

    def test_un_proceso_reparentado_que_sigue_vivo_impide_declarar_la_jaula_cerrada(self):
        t = TablaDeProcesos()
        bwrap = t.nuevo(1)
        init = t.nuevo(bwrap)
        sobreviviente = t.nuevo(init, inmortal=True)
        self.assertFalse(t.cerrar(bwrap, plazo=0.1))
        self.assertTrue(t.vivo(sobreviviente))
        self.assertEqual(t.procs[sobreviviente]["ppid"], 1)

    def test_un_bwrap_que_ya_salio_y_cuyo_pid_se_reuso_no_se_toca(self):
        t = TablaDeProcesos()
        ajeno = t.nuevo(1)
        hijo_ajeno = t.nuevo(ajeno)
        self.assertTrue(t.cerrar(ajeno, inicio="t-otro"))
        self.assertTrue(t.vivo(ajeno) and t.vivo(hijo_ajeno))

    def jaula_con_bwrap_muerto(self, t, **rasgos):
        """El bwrap sale antes de que nadie llame a `cerrar`: lo de dentro queda reparentado a 1."""
        bwrap = t.nuevo(1)
        inicio = t.inicio(bwrap)
        init = t.nuevo(bwrap, ns="pid:[7]")
        agente = t.nuevo(init, ns="pid:[7]", **rasgos)
        t.kill(bwrap, signal.SIGKILL)
        self.assertEqual(t.procs[init]["ppid"], 1)
        return bwrap, inicio, init, agente

    def test_un_bwrap_muerto_antes_no_declara_cerrada_la_jaula_que_dejo_reparentada(self):
        t = TablaDeProcesos()
        bwrap, inicio, init, agente = self.jaula_con_bwrap_muerto(t)
        self.assertTrue(t.cerrar(bwrap, inicio=inicio, ns="pid:[7]"))
        self.assertEqual(t.vivos(), [])

    def test_lo_reparentado_que_no_muere_impide_declarar_cerrada_la_jaula_con_el_bwrap_muerto(self):
        t = TablaDeProcesos()
        bwrap, inicio, init, agente = self.jaula_con_bwrap_muerto(t, inmortal=True)
        self.assertFalse(t.cerrar(bwrap, inicio=inicio, ns="pid:[7]", plazo=0.1))
        self.assertTrue(t.vivo(agente))

    def test_un_zombi_del_espacio_de_la_jaula_no_impide_declararla_cerrada(self):
        """El guardián es subreaper: lo que la jaula deja reparentado cae en él, y solo recoge
        al bwrap. Esos hijos quedan zombis, que siguen en el espacio de pids: no son 'vivos'
        (la regresión de 878e2d9 daba 125 en cada cierre)."""
        t = TablaDeProcesos()
        bwrap, inicio, init, agente = self.jaula_con_bwrap_muerto(t)
        t.procs[init]["zombi"] = True
        t.procs[agente]["zombi"] = True
        self.assertEqual(t.vivos(), [])
        self.assertEqual(
            sorted(int(n) for n in t.listdir("/proc") if t.ns_pid(int(n)) == "pid:[7]"),
            [init, agente],
        )
        self.assertTrue(t.cerrar(bwrap, inicio=inicio, ns="pid:[7]", plazo=0.1))

    def test_un_zombi_junto_a_un_proceso_vivo_en_el_espacio_sigue_sin_cerrada(self):
        t = TablaDeProcesos()
        bwrap, inicio, init, agente = self.jaula_con_bwrap_muerto(t, inmortal=True)
        t.procs[init]["zombi"] = True
        self.assertFalse(t.cerrar(bwrap, inicio=inicio, ns="pid:[7]", plazo=0.1))
        self.assertTrue(t.vivo(agente))

    def test_los_zombis_no_cuentan_como_miembros_vivos_del_espacio(self):
        t = TablaDeProcesos()
        bwrap, inicio, init, agente = self.jaula_con_bwrap_muerto(t)
        t.procs[init]["zombi"] = True
        with contextlib.ExitStack() as pila:
            for parche in t.parchear():
                pila.enter_context(parche)
            self.assertEqual(jaula_linux._en_ns("pid:[7]"), [agente])
            t.procs[agente]["zombi"] = True
            self.assertEqual(jaula_linux._en_ns("pid:[7]"), [])

    def test_con_el_pid_del_bwrap_reusado_se_cierra_la_jaula_pero_no_al_ajeno(self):
        t = TablaDeProcesos()
        bwrap, inicio, init, agente = self.jaula_con_bwrap_muerto(t)
        ajeno = t.nuevo(1)
        hijo_ajeno = t.nuevo(ajeno)
        t.procs[ajeno]["ns"] = ""
        self.assertTrue(t.cerrar(ajeno, inicio="t-del-bwrap", ns="pid:[7]"))
        self.assertTrue(t.vivo(ajeno) and t.vivo(hijo_ajeno))
        self.assertFalse(t.vivo(init) or t.vivo(agente))

    def test_sin_inicio_se_toma_el_del_proceso_al_entrar_y_se_mata_su_arbol_una_vez(self):
        t = TablaDeProcesos()
        bwrap = t.nuevo(1)
        hijo = t.nuevo(bwrap)
        self.assertTrue(t.cerrar(bwrap))
        self.assertFalse(t.vivo(bwrap) or t.vivo(hijo))

    def test_el_estado_de_la_identidad_ve_lo_que_sigue_en_la_jaula_sin_guardian_ni_bwrap(self):
        t = TablaDeProcesos()
        bwrap, inicio, init, agente = self.jaula_con_bwrap_muerto(t)
        identidad = {
            "guardian": 0, "bwrap": bwrap, "bwrap_inicio": inicio, "ns_pid": "pid:[7]",
            "ns_init": init, "ns_init_inicio": t.inicio(init),
        }
        with contextlib.ExitStack() as pila:
            for parche in t.parchear():
                pila.enter_context(parche)
            self.assertTrue(jaula_linux.vivo_identidad(identidad))
            self.assertTrue(jaula_linux.cerrar_identidad(identidad, plazo=0.5))
            self.assertFalse(jaula_linux.vivo_identidad(identidad))
        self.assertEqual(t.vivos(), [])

    def test_el_guardian_no_cierra_con_un_espacio_cuyo_init_ya_murio(self):
        """Un comando que termina solo deja el espacio sin init: el kernel lo cerró y su
        número puede ser ya de otra jaula en marcha."""
        t = TablaDeProcesos()
        bwrap, inicio, init, agente = self.jaula_con_bwrap_muerto(t)
        identidad = {
            "bwrap_inicio": inicio, "ns_pid": "pid:[7]",
            "ns_init": init, "ns_init_inicio": t.inicio(init),
        }
        t.kill(init, signal.SIGKILL)
        t.kill(agente, signal.SIGKILL)
        ajena = t.nuevo(1, ns="pid:[7]")
        with contextlib.ExitStack() as pila:
            for parche in t.parchear():
                pila.enter_context(parche)
            self.assertTrue(jaula_linux.cerrar_jaula(bwrap, identidad, plazo=0.2))
        self.assertTrue(t.vivo(ajena))

    def test_el_guardian_cierra_con_el_espacio_mientras_su_init_vive(self):
        t = TablaDeProcesos()
        bwrap, inicio, init, agente = self.jaula_con_bwrap_muerto(t)
        identidad = {
            "bwrap_inicio": inicio, "ns_pid": "pid:[7]",
            "ns_init": init, "ns_init_inicio": t.inicio(init),
        }
        with contextlib.ExitStack() as pila:
            for parche in t.parchear():
                pila.enter_context(parche)
            self.assertTrue(jaula_linux.cerrar_jaula(bwrap, identidad, plazo=0.2))
        self.assertEqual(t.vivos(), [])

    def test_un_numero_de_espacio_reusado_por_otra_jaula_no_se_toca(self):
        """Muerto el init de la jaula anotada, el kernel ya mató a todo su espacio y el número
        puede ser de otra jaula."""
        t = TablaDeProcesos()
        bwrap, inicio, init, agente = self.jaula_con_bwrap_muerto(t)
        identidad = {
            "guardian": 0, "bwrap": bwrap, "bwrap_inicio": inicio, "ns_pid": "pid:[7]",
            "ns_init": init, "ns_init_inicio": t.inicio(init),
        }
        t.kill(init, signal.SIGKILL)
        t.kill(agente, signal.SIGKILL)
        otra = t.nuevo(1, ns="pid:[7]")
        with contextlib.ExitStack() as pila:
            for parche in t.parchear():
                pila.enter_context(parche)
            self.assertFalse(jaula_linux.vivo_identidad(identidad))
            self.assertTrue(jaula_linux.cerrar_identidad(identidad, plazo=0.2))
        self.assertTrue(t.vivo(otra))

    def test_una_identidad_sin_hora_de_arranque_no_mata_al_pid_que_la_reuso(self):
        t = TablaDeProcesos()
        ajeno = t.nuevo(1)
        with contextlib.ExitStack() as pila:
            for parche in t.parchear():
                pila.enter_context(parche)
            jaula_linux.cerrar_identidad({"bwrap": ajeno}, plazo=0.1)
        self.assertTrue(t.vivo(ajeno))


@unittest.skipUnless(sys.platform.startswith("linux"), "usa /proc")
class CerrarRealTest(unittest.TestCase):
    def test_un_arbol_que_no_deja_de_hacer_fork_queda_sin_un_solo_proceso(self):
        marca = f"47{os.getpid()}"
        script = f"(while :; do sleep {marca} & sleep 0.005; done) & wait"
        p = subprocess.Popen(["/bin/sh", "-c", script], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def vivos():
            hallados = []
            for d in Path("/proc").iterdir():
                if d.name.isdigit():
                    try:
                        if f"sleep {marca}" in (d / "cmdline").read_text().replace("\0", " "):
                            hallados.append(int(d.name))
                    except OSError:
                        pass
            return hallados

        self.addCleanup(lambda: [os.kill(x, signal.SIGKILL) for x in vivos()])
        fin = time.time() + 10
        while not vivos() and time.time() < fin:
            time.sleep(0.02)
        self.assertTrue(vivos())
        self.assertTrue(jaula_linux.cerrar(p.pid))
        p.wait()
        time.sleep(0.2)
        self.assertEqual(vivos(), [])


def servidor_de_eco():
    """Un servidor local que devuelve en mayúsculas lo que recibe. (socket, puerto)."""
    import threading

    escucha = socket.socket()
    escucha.bind(("127.0.0.1", 0))
    escucha.listen(4)

    def atender():
        while True:
            try:
                c, _ = escucha.accept()
            except OSError:
                return
            dato = c.recv(1024)
            c.sendall(dato.upper())
            c.close()

    threading.Thread(target=atender, daemon=True).start()
    return escucha, escucha.getsockname()[1]


class EgresoTest(unittest.TestCase):
    """El proxy de salida del guardián: lo único que sale de una jaula con red `proveedor`."""

    def setUp(self):
        self.carpeta = Path(tempfile.mkdtemp(prefix="egr-")).resolve()
        self.addCleanup(shutil.rmtree, self.carpeta, True)
        previo = os.getcwd()
        os.chdir(self.carpeta)
        self.addCleanup(os.chdir, previo)
        self.eco, self.puerto_eco = servidor_de_eco()
        self.addCleanup(self.eco.close)
        self.resueltos = []
        crear = socket.create_connection

        def resolver(host, puerto, **_):
            self.resueltos.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.ip, puerto))]

        def conectar(direccion, timeout=None):
            self.conectados.append(direccion)
            return crear(("127.0.0.1", self.puerto_eco), timeout)

        self.conectados = []
        self.ip = "93.184.216.34"
        self.servidor = jaula_linux.servir_egreso(
            "s", ("api.anthropic.com", "Claude.AI"), resolver
        )
        self.addCleanup(self.servidor.server_close)
        self.addCleanup(self.servidor.shutdown)
        parche = mock.patch.object(socket, "create_connection", conectar)
        parche.start()
        self.addCleanup(parche.stop)

    def pedir(self, linea, enviar=b""):
        c = socket.socket(socket.AF_UNIX)
        c.settimeout(10)
        c.connect("s")
        c.sendall(linea.encode() + b"\r\nHost: x\r\n\r\n")
        respuesta = b""
        while b"\r\n\r\n" not in respuesta:
            trozo = c.recv(1024)
            if not trozo:
                break
            respuesta += trozo
        estado = respuesta.split(b"\r\n", 1)[0].decode()
        eco = b""
        if enviar and b" 200 " in respuesta:
            c.sendall(enviar)
            eco = c.recv(1024)
        c.close()
        return estado, eco

    def test_un_dominio_del_proveedor_con_direccion_publica_pasa_y_se_retransmite(self):
        estado, eco = self.pedir("CONNECT api.anthropic.com:443 HTTP/1.1", b"hola")
        self.assertEqual(estado, "HTTP/1.1 200 Connection Established")
        self.assertEqual(eco, b"HOLA")
        self.assertEqual(self.conectados, [("93.184.216.34", 443)])

    def test_los_dominios_se_comparan_sin_mayusculas_ni_punto_final(self):
        self.assertIn("200", self.pedir("CONNECT claude.ai.:443 HTTP/1.1")[0])

    def test_otro_dominio_se_rechaza_sin_resolverlo_ni_conectar(self):
        self.assertIn("403", self.pedir("CONNECT evil.example:443 HTTP/1.1")[0])
        self.assertIn("403", self.pedir("CONNECT api.anthropic.com.evil.example:443 HTTP/1.1")[0])
        self.assertEqual((self.resueltos, self.conectados), ([], []))

    def test_otro_puerto_se_rechaza(self):
        self.assertIn("403", self.pedir("CONNECT api.anthropic.com:22 HTTP/1.1")[0])
        self.assertEqual(self.conectados, [])

    def test_solo_connect(self):
        self.assertIn("405", self.pedir("GET http://api.anthropic.com/ HTTP/1.1")[0])
        self.assertEqual(self.conectados, [])

    def test_un_dominio_del_proveedor_que_resuelve_a_una_direccion_privada_no_sale(self):
        for ip in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1"):
            with self.subTest(ip=ip):
                self.ip = ip
                self.assertIn("502", self.pedir("CONNECT api.anthropic.com:443 HTTP/1.1")[0])
        self.assertEqual(self.conectados, [])


@unittest.skipIf(CAUSA, CAUSA)
class EgresoRealTest(unittest.TestCase):
    """Con bubblewrap de verdad: la jaula sin red propia, y su única salida es el proxy."""

    def test_la_jaula_no_llega_al_anfitrion_y_el_proxy_solo_deja_pasar_al_proveedor(self):
        base = Path(tempfile.mkdtemp(prefix="egreso-real-")).resolve()
        self.addCleanup(shutil.rmtree, base, True)
        espacio, runtime, egreso = base / "espacio" / "enc_1", base / "runtime" / "enc_1", base / "e"
        for carpeta in (espacio, runtime / "tmp", egreso):
            carpeta.mkdir(parents=True)
        eco, puerto_eco = servidor_de_eco()
        self.addCleanup(eco.close)
        crear = socket.create_connection

        def resolver(host, puerto, **_):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", puerto))]

        servidor = jaula_linux.servir_egreso(str(egreso / "s"), ("api.anthropic.com",), resolver)
        self.addCleanup(servidor.server_close)
        self.addCleanup(servidor.shutdown)
        script = (
            "import socket,os\n"
            f"try:\n socket.create_connection(('127.0.0.1',{puerto_eco}),3);print('directo')\n"
            "except OSError:\n print('sin-red')\n"
            "def pedir(host,dato=b''):\n"
            " c=socket.create_connection(('127.0.0.1',3128),5)\n"
            " c.sendall(('CONNECT %s:443 HTTP/1.1\\r\\n\\r\\n'%host).encode())\n"
            " r=c.recv(1024).split(b'\\r\\n')[0].decode()\n"
            " if dato and ' 200 ' in r:\n  c.sendall(dato);r+=' '+c.recv(1024).decode()\n"
            " return r\n"
            "print(pedir('api.anthropic.com',b'hola'))\n"
            "print(pedir('evil.example'))\n"
            "print(os.environ['HTTPS_PROXY'])\n"
        )
        jaula = Jaula(str(espacio), str(runtime), red=RED_PROVEEDOR,
                      dominios=("api.anthropic.com",), egreso=str(egreso))
        with mock.patch.object(
            socket, "create_connection",
            lambda direccion, timeout=None: crear(("127.0.0.1", puerto_eco), timeout),
        ):
            r = subprocess.run(
                jaula_linux.argv_jaula(jaula, ["/usr/bin/python3", "-c", script]),
                env=jaula_linux.entorno_jaula(jaula), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(
            r.stdout.splitlines(),
            ["sin-red", "HTTP/1.1 200 Connection Established HOLA", "HTTP/1.1 403 Forbidden",
             "http://127.0.0.1:3128"],
            r.stderr,
        )


if __name__ == "__main__":
    unittest.main()
