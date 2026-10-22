import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from motor import plataforma
from motor.plataforma import posix, windows


class ArranqueTest(unittest.TestCase):
    def setUp(self):
        posix._cache_boottime[:] = [None, None]
        self.addCleanup(
            lambda: posix._cache_boottime.__setitem__(slice(None), [None, None])
        )

    def test_posix_dos_lecturas_menor_120_es_mismo_arranque(self):
        lecturas = iter(
            [
                "{ sec = 1700000000, usec = 0 }",
                "{ sec = 1700000060, usec = 0 }",
            ]
        )
        a = posix.boot_id_sistema(lectura=lambda: next(lecturas))
        b = posix.boot_id_sistema(lectura=lambda: next(lecturas))
        self.assertEqual(a, b)

    def test_posix_lecturas_lejanas_son_arranques_distintos(self):
        lecturas = iter(
            [
                "{ sec = 1700000000, usec = 0 }",
                "{ sec = 1700001000, usec = 0 }",
            ]
        )
        a = posix.boot_id_sistema(lectura=lambda: next(lecturas))
        b = posix.boot_id_sistema(lectura=lambda: next(lecturas))
        self.assertNotEqual(a, b)

    def test_windows_usa_last_boot_sin_depender_de_otro_reloj(self):
        fijo = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with mock.patch("time.time", return_value=1000.0):
            a = windows.boot_id_sistema(consulta=lambda: fijo)
        with mock.patch("time.time", return_value=9999999.0):
            b = windows.boot_id_sistema(consulta=lambda: fijo)
        self.assertEqual(a, b)
        self.assertEqual(a, fijo.isoformat())

    def test_windows_fuente_es_win32_operating_system(self):
        import inspect

        fuente = inspect.getsource(windows._last_boot_up_time)
        self.assertIn("Win32_OperatingSystem", fuente)
        self.assertIn("LastBootUpTime", fuente)
        self.assertNotIn("GetTickCount64", inspect.getsource(windows))

    def test_marca_distinta_menor_120_no_es_corte(self):
        with mock.patch.dict(os.environ, {"MOTOR_BOOT_ID": "1700000060"}):
            proc = {"pid": os.getpid(), "boot_id": "1700000000"}
            self.assertNotEqual(plataforma.estado_proc(proc), "corte")

    def test_marca_lejana_si_es_corte(self):
        with mock.patch.dict(os.environ, {"MOTOR_BOOT_ID": "1700002000"}):
            proc = {"pid": os.getpid(), "boot_id": "1700000000"}
            self.assertEqual(plataforma.estado_proc(proc), "corte")

    def test_lectura_fallida_de_last_boot_no_es_corte(self):
        pid = os.getpid()
        proc = {
            "pid": pid,
            "boot_id": windows.boot_id_sistema(
                consulta=lambda: "2026-10-20T08:15:30.5000000-07:00"
            ),
            "inicio": plataforma.hora_inicio(pid),
        }
        with mock.patch.dict(os.environ):
            os.environ.pop("MOTOR_BOOT_ID", None)
            with mock.patch.object(
                plataforma,
                "boot_id_sistema",
                lambda: windows.boot_id_sistema(consulta=lambda: ""),
            ):
                self.assertEqual(plataforma.estado_proc(proc), "vivo")

    def test_marcas_incomparables_no_son_corte(self):
        self.assertTrue(
            plataforma.mismo_arranque(
                "desconocido", "2026-10-20T08:15:30.5000000-07:00"
            )
        )
        self.assertTrue(
            plataforma.mismo_arranque("170000000", "2026-10-20T08:15:30-07:00")
        )
        self.assertTrue(plataforma.mismo_arranque(None, "170000000"))
        self.assertTrue(plataforma.mismo_arranque("", "170000000"))
        self.assertFalse(plataforma.mismo_arranque("1700000000", "1700002000"))
        self.assertFalse(plataforma.mismo_arranque("arranque-1", "arranque-2"))


class ShellTest(unittest.TestCase):
    def test_por_omision_es_git_bash(self):
        argv, shell, _exe = windows.argv_shell("echo ok")
        self.assertEqual(argv[0], r"C:\Program Files\Git\bin\bash.exe")
        self.assertEqual(argv[1:], ["-c", "echo ok"])
        self.assertFalse(shell)

    def test_clave_bash_de_maquina_cambia_la_ruta(self):
        from motor.conf import valida_maquina

        valida_maquina({"nombre": "pc", "conf": "x", "bash": r"D:\Git\bin\bash.exe"})
        argv, _shell, _exe = windows.argv_shell("echo ok", r"D:\Git\bin\bash.exe")
        self.assertEqual(argv[0], r"D:\Git\bin\bash.exe")

    def test_nunca_cmd_ni_bash_de_wsl(self):
        argv, _shell, _exe = windows.argv_shell(
            "echo ok", r"C:\Windows\System32\bash.exe"
        )
        self.assertEqual(argv[0], r"C:\Program Files\Git\bin\bash.exe")
        self.assertNotIn("cmd.exe", argv[0].lower())
        self.assertFalse(windows.es_bash_de_wsl(argv[0]))

    def test_python_sin_stub_de_la_tienda(self):
        path = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps;C:\Python312;C:\Windows"
        limpio = windows.path_sin_stub_tienda(path)
        self.assertNotIn("WindowsApps", limpio)
        self.assertIn(r"C:\Python312", limpio)
        self.assertTrue(
            windows.es_stub_tienda(
                r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"
            )
        )

    def test_maquina_rechaza_bash_vacio_si_viene(self):
        from motor.conf import ConfError, valida_maquina

        with self.assertRaises(ConfError):
            valida_maquina({"nombre": "pc", "conf": "x", "bash": ""})

    @unittest.skipIf(os.name == "nt", "la PC lo prueba por _run con Git Bash")
    def test_path_con_python_real_pone_interprete_que_funciona(self):
        import subprocess

        limpio = windows.path_con_python_real(
            r"C:\Users\x\AppData\Local\Microsoft\WindowsApps;C:\Windows"
        )
        self.assertNotIn("WindowsApps", limpio)
        carpeta = limpio.split(";")[0]
        for nombre in ("python", "python3"):
            r = subprocess.run(
                [nombre, "-c", "print(40+2)"],
                env={**os.environ, "PATH": carpeta},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("42", r.stdout)

    def test_path_del_agente_en_windows_conserva_windowsapps(self):
        from motor.effects import con_python

        path = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps;C:\Python314;C:\Windows"
        self.assertEqual(con_python({"PATH": path}, en_windows=True)["PATH"], path)

    def test_path_de_comando_en_windows_quita_windowsapps(self):
        from motor.effects import con_python_de_comando

        path = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps;C:\Python314;C:\Windows"
        salida = con_python_de_comando({"PATH": path}, en_windows=True)["PATH"]
        self.assertNotIn("WindowsApps", salida)
        self.assertIn("motor-python", salida.split(";")[0])

    @unittest.skipUnless(os.name == "nt", "en la PC corre con cmd real")
    def test_pc_env_del_agente_con_shims_python_y_gh(self):
        import subprocess
        import types

        from motor.effects import RAIZ, Effects

        mq = types.SimpleNamespace(env=(), secretos=(), path_extra=())
        env = Effects.env_base(
            types.SimpleNamespace(cx=types.SimpleNamespace(maquina=mq))
        )
        env["PATH"] = os.pathsep.join(
            [str(RAIZ / "motor" / "shims"), env.get("PATH", "")]
        )
        py = subprocess.run(
            ["cmd.exe", "/c", "python", "-c", "print(40+2)"],
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        self.assertEqual(py.stdout.strip(), "42", py.stdout + py.stderr)
        gh = subprocess.run(
            ["cmd.exe", "/c", "gh", "pr", "merge", "1"],
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        self.assertEqual(gh.returncode, 3, gh.stdout + gh.stderr)
        self.assertIn("prohibido", gh.stderr)

    @unittest.skipUnless(os.name == "nt", "en la PC corre con Git Bash real")
    def test_pc_test_f_sustitucion_y_python3(self):
        import tempfile
        from pathlib import Path

        from motor.effects import _run

        with tempfile.TemporaryDirectory() as t:
            marca = Path(t) / "marca.txt"
            marca.write_text("ok", encoding="utf-8")
            cmd = f'test -f "{marca}" && echo "$(python3 -c \'print(40+2)\')"'
            r = _run(cmd, Path(t), dict(os.environ))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("42", r.stdout)


class WslTest(unittest.TestCase):
    def _repo(self, tmp):
        import subprocess
        from pathlib import Path

        repo = Path(tmp) / "repo"
        repo.mkdir()

        def corre(*a):
            return subprocess.run(
                ["git", *a], cwd=repo, capture_output=True, text=True, check=True
            )

        corre("init", "-q", "-b", "main")
        corre("config", "user.email", "t@t")
        corre("config", "user.name", "t")
        (repo / "a.txt").write_text("hola\n", encoding="utf-8")
        corre("add", ".")
        corre("commit", "-qm", "uno")
        return repo

    def test_prepara_guarda_ruta_relativa_en_git(self):
        import tempfile
        from pathlib import Path

        from motor.workspace import Workspace, git

        with tempfile.TemporaryDirectory() as t:
            repo = self._repo(t)
            home = Path(t) / "home"
            ws = Workspace(home, "pc")
            w = ws.prepara(str(repo), "planx", "I", "main")
            texto = (w / ".git").read_text(encoding="utf-8").strip()
            self.assertTrue(texto.startswith("gitdir:"))
            ruta = texto.removeprefix("gitdir:").strip()
            self.assertFalse(os.path.isabs(ruta), texto)
            espejo = ws.espejo(str(repo))
            self.assertEqual(
                git("config", "worktree.useRelativePaths", cwd=espejo).stdout.strip(),
                "true",
            )
            self.assertEqual(git("rev-parse", "HEAD", cwd=w).returncode, 0)

    def test_carpeta_existente_se_convierte_a_relativa(self):
        import tempfile
        from pathlib import Path

        from motor.workspace import Workspace, git

        with tempfile.TemporaryDirectory() as t:
            repo = self._repo(t)
            home = Path(t) / "home"
            ws = Workspace(home, "pc")
            w = ws.prepara(str(repo), "planx", "I", "main")
            espejo = ws.espejo(str(repo))
            git("worktree", "remove", "--force", str(w), cwd=espejo)
            git("config", "worktree.useRelativePaths", "false", cwd=espejo)
            git(
                "worktree",
                "add",
                "--no-relative-paths",
                "-q",
                "-B",
                ws.rama("planx", "I"),
                str(w),
                "HEAD",
                cwd=espejo,
            )
            antes = (w / ".git").read_text(encoding="utf-8")
            self.assertTrue(os.path.isabs(antes.removeprefix("gitdir:").strip()))
            ws.prepara(str(repo), "planx", "I", "main")
            despues = (w / ".git").read_text(encoding="utf-8").strip()
            self.assertFalse(os.path.isabs(despues.removeprefix("gitdir:").strip()))

    def test_argv_wsl_lleva_guardian_y_tope(self):
        argv = windows.argv_wsl("Ubuntu", ["muse", "exec"], timeout=30, ahora=1000.0)
        self.assertEqual(argv[:3], ["wsl.exe", "-d", "Ubuntu"])
        self.assertIn(windows.GUARDIAN_WSL, argv)
        self.assertIn("1030.0", argv)
        self.assertEqual(argv[-2:], ["muse", "exec"])
        self.assertIn("SIGKILL", windows.GUARDIAN_WSL)

    def test_env_wsl_pasa_motor_meta_y_shims_sin_path(self):
        env = windows.env_wsl(
            {
                "MOTOR_RESULT": r"C:\x\result.json",
                "MOTOR_BLOCK": "I",
                "MOTOR_SHIMS": r"C:\motor\shims",
                "META_API_KEY": "k",
                "PATH": r"C:\motor\shims;C:\Windows",
                "OTRA": "no",
            }
        )
        nombres = env["WSLENV"].split(":")
        self.assertIn("MOTOR_RESULT/p", nombres)
        self.assertIn("MOTOR_BLOCK", nombres)
        self.assertIn("MOTOR_SHIMS/p", nombres)
        self.assertIn("META_API_KEY", nombres)
        self.assertFalse(
            any(n == "PATH" or n.startswith("PATH/") for n in nombres), nombres
        )
        self.assertNotIn("OTRA", nombres)

    def test_lanzamiento_wsl_pasa_shims_sin_path_en_wslenv(self):
        argv, env = windows.lanzamiento_wsl(
            ["muse", "exec", "--prompt-file", r"C:\x\brief.md"],
            {"MOTOR_RESULT": r"C:\x\r.json", "META_API_KEY": "k", "PATH": "/usr/bin"},
            timeout=60,
            ahora=2000.0,
        )
        self.assertIn(windows.GUARDIAN_WSL, argv)
        self.assertIn("2060.0", argv)
        self.assertIn("/mnt/c/x/brief.md", argv)
        self.assertTrue(env["MOTOR_SHIMS"].endswith("shims"))
        self.assertIn("gh", os.listdir(env["MOTOR_SHIMS"]))
        self.assertIn("muse", os.listdir(env["MOTOR_SHIMS"]))
        nombres = env["WSLENV"].split(":")
        self.assertIn("MOTOR_SHIMS/p", nombres)
        self.assertFalse(
            any(n == "PATH" or n.startswith("PATH/") for n in nombres), nombres
        )

    @unittest.skipUnless(os.name == "nt", "lanza wsl.exe real en la PC")
    def test_wsl_python3_y_shims_resuelven_en_linux(self):
        import subprocess

        argv, env = windows.lanzamiento_wsl(
            ["/bin/sh", "-c", "command -v python3 && command -v muse && command -v gh"],
            {
                **os.environ,
                "MOTOR_RESULT": r"C:\motor-test\result.json",
                "META_API_KEY": "k",
            },
            timeout=60,
        )
        r = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        lineas = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        self.assertEqual(len(lineas), 3, lineas)
        self.assertNotIn("shims", lineas[0])
        self.assertTrue(lineas[1].endswith("/shims/muse"), lineas)
        self.assertTrue(lineas[2].endswith("/shims/gh"), lineas)

    def test_doctor_y_start_attempt_usan_el_mismo_constructor(self):
        import inspect

        from motor import doctor, effects

        self.assertIn("lanzamiento_wsl", inspect.getsource(doctor.prueba_de_ruta))
        self.assertIn(
            "lanzamiento_wsl", inspect.getsource(effects.Effects.start_attempt)
        )

    def test_copia_guarda_ruta_relativa_en_git(self):
        import tempfile
        from pathlib import Path

        from motor.workspace import Workspace, git

        with tempfile.TemporaryDirectory() as t:
            repo = self._repo(t)
            home = Path(t) / "home"
            ws = Workspace(home, "pc")
            sha = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
            donde = Path(t) / "copia"
            ws.copia(str(repo), sha, donde)
            texto = (donde / ".git").read_text(encoding="utf-8").strip()
            ruta = texto.removeprefix("gitdir:").strip()
            self.assertFalse(os.path.isabs(ruta), texto)


if __name__ == "__main__":
    unittest.main()
