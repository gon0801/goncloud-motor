"""El lanzador del canario en una máquina de prueba.

La máquina vive en un temporal: una base viva falsa con los archivos que el
lanzador enlaza, el repo del canario en un git local con el mismo `trabajo.sh`,
escenario y receta de despliegue que el de verdad, una flota y la carpeta del
despliegue. El encargo es real: el lanzador lo recibe con `motor encargar` y lo
mira con `motor ver`. El supervisor es `tests/canario_supervisor_falso.py`, que
barre como el banco de `tests/test_compuerta_e2e.py`, y GitHub es el falso.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from motor import plataforma
from motor.encargos.registro import Registro
from motor.github import FakeGitHub
from ops import canario_lanzador
from ops.canario_encargos import Fila
from ops.canario_lanzador import Borrado, Informe, Pedido, main, preparar
from tests.test_compuerta_e2e import _git

RAIZ = Path(__file__).resolve().parents[1]
FALSO = RAIZ / "tests" / "canario_supervisor_falso.py"
CERRADOS = ("terminado", "detenido", "cancelado")

_TRABAJO_SH = """#!/bin/bash
set -euo pipefail
aqui="$(cd "$(dirname "$0")" && pwd)"
arbol="${MOTOR_CANARIO_ARBOL:?falta MOTOR_CANARIO_ARBOL}"
escenario="$(mktemp "${TMPDIR:-/tmp}/escenario.XXXXXX")"
python3 - "$aqui/escenario.json" "$escenario" <<'PY'
import sys, tomllib
from pathlib import Path
espacio = Path.cwd()
maquina = tomllib.loads((espacio.parent.parent / "maquina.toml").read_text())["nombre"]
texto = Path(sys.argv[1]).read_text().replace("@MAQUINA@", maquina).replace("@CORRIDA@", espacio.name)
Path(sys.argv[2]).write_text(texto)
PY
exec python3 "$arbol/ops/agente_ensayo.py" "$escenario"
"""

_ESCENARIO = {
    "fases": [{
        "id": "uno", "resultado": "corridas/@MAQUINA@.txt con el id de la corrida",
        "requiere_despliegue": True,
    }],
    "producto": {"uno": {"corridas/@MAQUINA@.txt": "@CORRIDA@\n"}},
}


def _matar(pid_en: Path) -> None:
    with contextlib.suppress(OSError, ValueError):
        os.kill(int(pid_en.read_text(encoding="utf-8")), signal.SIGKILL)


def _matar_identidad(ruta: Path) -> None:
    with contextlib.suppress(OSError, ValueError):
        ident = json.loads(ruta.read_text(encoding="utf-8"))
        plataforma.matar_proc(int(ident["pid"]), str(ident["inicio"]))


def _trabajadores(base: Path) -> list[dict]:
    return [
        json.loads(ruta.read_text(encoding="utf-8"))
        for ruta in sorted(base.glob("despliegues/*/proceso.json"))
    ]


def _esperar_trabajadores(base: Path) -> None:
    """Que ningún trabajador de despliegue de la prueba siga vivo al borrar el temporal."""
    vence = time.monotonic() + 60
    for ident in _trabajadores(base):
        while plataforma.estado_proc(ident) == "vivo" and time.monotonic() < vence:
            time.sleep(0.1)
        plataforma.matar_proc(int(ident["pid"]), str(ident["inicio"]))


@unittest.skipIf(os.name == "nt", "el supervisor falso deja un sleep en su grupo de POSIX")
class LanzadorEnUnaMaquinaDePrueba(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="canario-lanzador-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.viva, self.base = self.tmp / "viva", self.tmp / "canario"
        self.flota, self.destino = self.tmp / "flota", self.tmp / "destino"
        self.fake, self.repo = self.tmp / "fake", self.tmp / "repo.git"
        self._viva()
        self._repo()
        poteto = self.base / "politica" / "poteto" / "skills" / "poteto-mode"
        poteto.mkdir(parents=True)
        (poteto / "SKILL.md").write_text("# poteto-mode (banco)\n", encoding="utf-8")

    def _viva(self) -> None:
        self.viva.mkdir()
        archivos = {
            "maquina.toml": 'nombre="banco"\nconf="."\ntiene=["ssh-goncloud"]\n',
            "github.json": json.dumps({"falso": str(self.fake)}),
            "capacidades.json": json.dumps({"recursos": {}}),
            "flota-home.json": json.dumps({"home": str(self.flota)}),
            "instalacion.json": json.dumps({"destino": {"codigo": _git(RAIZ, "rev-parse", "HEAD")}}),
            "secretos.env": "# los secretos de la base viva no se enlazan\n",
            "canal.json": "{}",
        }
        for nombre, texto in archivos.items():
            (self.viva / nombre).write_text(texto, encoding="utf-8")
        (self.flota / "generaciones" / "g1").mkdir(parents=True)
        (self.flota / "actual").symlink_to(self.flota / "generaciones" / "g1")

    def _repo(self) -> None:
        semilla = self.tmp / "semilla"
        destino = self.destino.as_posix()
        archivos = {
            ".gitattributes": "* -text\n",
            "trabajo.sh": _TRABAJO_SH,
            "escenario.json": json.dumps(_ESCENARIO, indent=1),
            ".motor.toml": '[repo]\nprincipal = "main"\n\n[deploy]\ndesplegar = "ops/despliega.sh"\n'
                           'checklist = "ops/checklist.sh"\nrollback = "ops/rollback.sh"\n',
            "ops/despliega.sh": f'set -eu\nmkdir -p "{destino}/corridas"\n'
                                f'cp "$(dirname "$0")"/../corridas/*.txt "{destino}/corridas/"\n',
            "ops/checklist.sh": f'test -d "{destino}/corridas"\n',
            "ops/rollback.sh": "true\n",
        }
        for nombre, texto in archivos.items():
            (semilla / nombre).parent.mkdir(parents=True, exist_ok=True)
            (semilla / nombre).write_text(texto, encoding="utf-8", newline="\n")
        _git(semilla, "init", "-q", "-b", "main")
        _git(semilla, "add", "-A")
        _git(semilla, "-c", "user.name=t", "-c", "user.email=t@local", "commit", "-q", "-m", "semilla")
        _git(semilla, "init", "--bare", "-q", "-b", "main", str(self.repo))
        _git(semilla, "push", "-q", str(self.repo), "main", "main:encargo/enc_ajeno")

    def cambiar_repo(self, archivos: dict[str, str]) -> None:
        """Deja estos archivos en `main` del repo del canario."""
        clon = self.tmp / "clon"
        _git(self.tmp, "clone", "-q", str(self.repo), str(clon))
        for nombre, texto in archivos.items():
            (clon / nombre).write_text(texto, encoding="utf-8", newline="\n")
        _git(clon, "add", "-A")
        _git(clon, "-c", "user.name=t", "-c", "user.email=t@local", "commit", "-q", "-m", "variante")
        _git(clon, "push", "-q", "origin", "main")
        shutil.rmtree(clon)

    def reversa_larga(self, reversa: str) -> dict[str, str]:
        """Un despliegue que no termina solo y esta reversa. Al parar el encargo, el
        trabajador del motor corta el despliegue y corre la reversa hasta el final."""
        return {"ops/despliega.sh": "sleep 600\n", "ops/rollback.sh": f"set -eu\n{reversa}\n"}

    def correr(
        self, *extra: str, modo: tuple[str, ...] = (), supervisor: list[str] | None = None,
    ) -> tuple[int, dict]:
        hijo = self.tmp / "hijo.pid"
        self.addCleanup(_matar, hijo)
        argv = [
            "--repo", str(self.repo), "--base", str(self.base), "--base-viva", str(self.viva),
            "--despliegue", str(self.destino), "--corrida", "prueba", "--intervalo-s", "0.2",
            *extra,
        ]
        supervisor = supervisor or [
            sys.executable, "-B", str(FALSO), "--base", str(self.base), "--hijo", str(hijo), *modo,
        ]
        salida, avances = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(avances):
                codigo = main(argv, gh=FakeGitHub(self.fake), supervisor=supervisor)
        finally:
            self.hijo = int(hijo.read_text(encoding="utf-8")) if hijo.is_file() else 0
            self.avances = avances.getvalue()
        return codigo, json.loads(salida.getvalue())

    def filas(self, informe: dict) -> dict[str, dict]:
        return {f["nombre"]: f for f in informe["filas"]}

    def sin_procesos_suyos(self, informe: dict) -> None:
        self.assertEqual(informe["cierre"]["supervisor"], "ausente", self.avances)
        self.assertTrue(self.hijo, "el supervisor falso no llegó a dejar su hijo")
        self.assertFalse(plataforma.pid_vivo(self.hijo), "el hijo del supervisor sigue vivo")

    def estados(self) -> list[str]:
        """El estado de cada encargo de la base del canario, según el registro."""
        reg = Registro(self.base)
        try:
            return [e.estado for e in (reg.fold(i) for i in reg.ids()) if e is not None]
        finally:
            reg.close()

    def queda_cerrado(self) -> None:
        estados = self.estados()
        self.assertTrue(estados, "no se recibió ningún encargo")
        self.assertTrue(set(estados) <= set(CERRADOS), f"{estados}: un supervisor posterior lo retomaría")
        if self.hijo:
            self.assertFalse(plataforma.pid_vivo(self.hijo), "el hijo del supervisor sigue vivo")

    def test_una_corrida_que_termina_da_la_tabla_esperada_y_borra_solo_lo_suyo(self):
        ajenos = [
            self.base / "espacio" / "enc_ajeno" / "x", self.base / "ejecucion" / "enc_ajeno" / "x",
            self.flota / "trabajos" / "enc_ajeno.json",
        ]
        for ruta in ajenos:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text("{}", encoding="utf-8")

        codigo, informe = self.correr()

        ident = informe["encargo"]
        filas = self.filas(informe)
        self.assertEqual(informe["fin"], "terminado", self.avances)
        self.assertEqual(filas["termina"]["resultado"], "pasa", filas["termina"]["observado"])
        self.assertEqual(filas["entregado"]["resultado"], "pasa", filas["entregado"]["observado"])
        self.assertEqual(
            (self.destino / "corridas" / "banco.txt").read_text(encoding="utf-8").strip(), ident,
        )
        self.assertTrue(informe["coincide"], informe["tabla"])
        self.assertEqual(codigo, 0, informe["tabla"])
        self.sin_procesos_suyos(informe)
        self.assertEqual(informe["cierre"]["parada"], "")
        self.assertEqual(
            {b["resto"]: b["resultado"] for b in informe["limpieza"]},
            {
                f"la rama remota encargo/{ident}": "borrado",
                f"espacio/{ident}": "borrado",
                f"ejecucion/{ident}": "borrado",
                f"trabajos/{ident}.json de la flota": "borrado",
            },
        )
        self.assertEqual(_git(self.repo, "branch", "--list", f"encargo/{ident}"), "")
        for tipo in ("espacio", "ejecucion"):
            self.assertFalse((self.base / tipo / ident).exists(), tipo)
        self.assertFalse((self.flota / "trabajos" / f"{ident}.json").exists())
        for ruta in ajenos:
            self.assertTrue(ruta.is_file(), f"borró {ruta}")
        self.assertIn("encargo/enc_ajeno", _git(self.repo, "branch", "--list", "encargo/enc_ajeno"))
        self.assertEqual(_git(self.repo, "show", "main:corridas/banco.txt"), ident)
        for nombre in canario_lanzador.PROHIBIDOS:
            self.assertFalse(os.path.lexists(self.base / nombre), nombre)

    def test_una_corrida_que_vence_el_tope_para_su_encargo_y_lo_limpia(self):
        codigo, informe = self.correr("--tope-min", "0.05", modo=("--quieto",))

        self.assertEqual(informe["fin"], "tope", self.avances)
        self.assertEqual(self.filas(informe)["termina"]["resultado"], "falla")
        self.assertEqual(codigo, 1)
        self.sin_procesos_suyos(informe)
        self.assertEqual(informe["cierre"]["parada"], "detenido")
        self.assertNotIn("falló", {b["resultado"] for b in informe["limpieza"]})

    def test_un_supervisor_que_muere_a_media_corrida_no_deja_su_grupo(self):
        codigo, informe = self.correr(modo=("--muere-tras", "2"))

        self.assertEqual(informe["fin"], "supervisor_muerto", self.avances)
        self.assertNotEqual(informe["estado"], "terminado")
        self.assertEqual(self.filas(informe)["termina"]["resultado"], "falla")
        self.assertEqual(codigo, 1)
        self.sin_procesos_suyos(informe)
        self.assertEqual(informe["cierre"]["parada"], "detenido")
        self.assertFalse((self.base / "espacio" / informe["encargo"]).exists())

    def test_un_turno_vivo_al_morir_el_supervisor_se_cierra_antes_de_medir(self):
        self.cambiar_repo({"trabajo.sh": "#!/bin/bash\nexec sleep 600\n"})
        turno = self.tmp / "turno.json"
        self.addCleanup(_matar_identidad, turno)

        codigo, informe = self.correr(modo=("--muere-con-turno", str(turno)))

        self.assertTrue(turno.is_file(), "el agente no llegó a correr")
        agente = json.loads(turno.read_text(encoding="utf-8"))
        self.assertNotEqual(plataforma.estado_proc(agente), "vivo", "el agente sigue trabajando")
        self.assertEqual(informe["fin"], "supervisor_muerto", self.avances)
        self.assertIn(
            "quedó detenido", self.filas(informe)["termina"]["observado"],
            "midió el encargo antes de pararlo, con el agente trabajando",
        )
        self.assertEqual(informe["cierre"]["estado"], "detenido", self.avances)
        self.assertEqual(informe["cierre"]["vivos"], [], self.avances)
        self.assertEqual(codigo, 1)
        self.assertFalse((self.base / "espacio" / informe["encargo"]).exists())

    def test_un_trabajador_de_despliegue_que_termina_dentro_del_tope_se_espera(self):
        revertido = self.tmp / "revertido"
        self.cambiar_repo(self.reversa_larga(f'sleep 15\ntouch "{revertido.as_posix()}"'))
        self.addCleanup(_esperar_trabajadores, self.base)

        codigo, informe = self.correr(modo=("--muere-al-desplegar",))

        self.assertTrue(
            revertido.is_file(),
            "la orden volvió antes de que el trabajador de despliegue terminara su reversa",
        )
        self.assertEqual(informe["fin"], "supervisor_muerto", self.avances)
        self.assertEqual(informe["cierre"]["estado"], "detenido", self.avances)
        self.assertEqual(informe["cierre"]["vivos"], [], self.avances)
        self.assertEqual(codigo, 1)
        self.assertFalse((self.base / "espacio" / informe["encargo"]).exists())

    def test_un_trabajador_de_despliegue_vivo_al_vencer_el_tope_no_deja_borrar_nada(self):
        suelta = self.tmp / "suelta-la-reversa"
        self.cambiar_repo(self.reversa_larga(f'while [ ! -e "{suelta.as_posix()}" ]; do sleep 0.1; done'))
        self.addCleanup(_esperar_trabajadores, self.base)
        self.addCleanup(suelta.touch)

        codigo, informe = self.correr("--tope-cierre-s", "2", modo=("--muere-al-desplegar",))

        ident = informe["encargo"]
        self.assertNotEqual(codigo, 0)
        self.assertTrue(
            any("trabajador de despliegue" in v for v in informe["cierre"]["vivos"]), informe["cierre"],
        )
        self.assertEqual(informe["limpieza"], [], "borró con un trabajador vivo")
        self.assertEqual(informe["filas"], [], "midió con un trabajador vivo")
        for tipo in ("espacio", "ejecucion"):
            self.assertTrue((self.base / tipo / ident).is_dir(), f"borró {tipo}/{ident}")
        (trabajador,) = _trabajadores(self.base)
        self.assertEqual(plataforma.estado_proc(trabajador), "vivo", "mató la reversa a medias")

    def test_un_parar_que_no_cierra_el_encargo_no_deja_borrar_nada(self):
        motor = canario_lanzador._motor

        def parar_falla(p, *argv):
            if argv[0] == "parar":
                raise RuntimeError("motor parar salió 1: la base está ocupada")
            return motor(p, *argv)

        with mock.patch.object(canario_lanzador, "_motor", parar_falla):
            codigo, informe = self.correr(modo=("--muere-tras", "2"))

        self.assertNotEqual(codigo, 0)
        (estado,) = self.estados()
        self.assertNotIn(estado, CERRADOS)
        self.assertEqual(informe["limpieza"], [], "borró con el encargo abierto")
        self.assertTrue((self.base / "espacio" / informe["encargo"]).is_dir())
        self.assertIn("no se pudo parar", informe["cierre"]["parada"])

    def test_si_no_puede_lanzar_su_supervisor_cierra_el_encargo(self):
        codigo, salida = self.correr(supervisor=[str(self.tmp / "no-existe")])

        self.assertEqual(codigo, 2, salida)
        self.assertEqual(salida["paso"], "lanzar")
        self.queda_cerrado()

    def test_si_la_espera_se_cae_cierra_el_encargo(self):
        with mock.patch.object(canario_lanzador, "esperar", side_effect=RuntimeError("se cayó")):
            codigo, salida = self.correr()

        self.assertEqual(codigo, 2, salida)
        self.assertIn("se cayó", salida["error"])
        self.queda_cerrado()

    def test_si_interrumpen_la_espera_cierra_el_encargo(self):
        with mock.patch.object(canario_lanzador, "esperar", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.correr()

        self.queda_cerrado()

    def test_si_medir_se_cae_cierra_el_encargo_y_limpia(self):
        with mock.patch.object(canario_lanzador, "medir", side_effect=RuntimeError("no midió")):
            codigo, salida = self.correr("--tope-min", "0.05", modo=("--quieto",))

        self.assertEqual(codigo, 2, salida)
        self.assertIn("no midió", salida["error"])
        self.queda_cerrado()
        self.assertNotIn("falló", {b["resultado"] for b in salida["limpieza"]})

    def test_un_remoto_que_no_se_puede_resolver_no_tumba_la_limpieza(self):
        ciclo = ValueError("los remotos locales del repositorio forman un ciclo")
        with mock.patch.object(canario_lanzador, "resolver_repositorio", side_effect=ciclo):
            codigo, informe = self.correr()

        ident = informe["encargo"]
        limpieza = {b["resto"]: b for b in informe["limpieza"]}
        rama = limpieza[f"la rama remota encargo/{ident}"]
        self.assertEqual(rama["resultado"], "falló")
        self.assertIn("ValueError", rama["causa"])
        for resto in (f"espacio/{ident}", f"ejecucion/{ident}", f"trabajos/{ident}.json de la flota"):
            self.assertEqual(limpieza[resto]["resultado"], "borrado", resto)
        self.assertEqual(codigo, 1)

    def test_un_push_que_no_responde_no_tumba_la_limpieza(self):
        run = subprocess.run

        def sin_respuesta(argv, *a, **k):
            if "push" in argv:
                raise subprocess.TimeoutExpired(argv, 60)
            return run(argv, *a, **k)

        with mock.patch.object(subprocess, "run", sin_respuesta):
            codigo, informe = self.correr()

        ident = informe["encargo"]
        limpieza = {b["resto"]: b for b in informe["limpieza"]}
        rama = limpieza[f"la rama remota encargo/{ident}"]
        self.assertEqual(rama["resultado"], "falló")
        self.assertIn("TimeoutExpired", rama["causa"])
        self.assertEqual(limpieza[f"espacio/{ident}"]["resultado"], "borrado")
        self.assertEqual(codigo, 1)

    def test_las_rutas_relativas_se_leen_desde_donde_se_corre_la_orden(self):
        aparte = self.tmp / f"canario-relativa-{os.getpid()}-{time.monotonic_ns()}"
        self.base.rename(aparte)
        self.base = aparte
        self.addCleanup(shutil.rmtree, RAIZ / aparte.name, True)
        hijo = self.tmp / "hijo.pid"
        self.addCleanup(_matar, hijo)
        supervisor = [sys.executable, "-B", str(FALSO), "--base", str(aparte), "--hijo", str(hijo)]
        argv = [
            "--repo", str(self.repo), "--base", aparte.name, "--base-viva", self.viva.name,
            "--despliegue", self.destino.name, "--corrida", "prueba", "--intervalo-s", "0.2",
            "--tope-min", "1",
        ]
        salida = io.StringIO()
        with contextlib.chdir(self.tmp), contextlib.redirect_stdout(salida), \
                contextlib.redirect_stderr(io.StringIO()):
            codigo = main(argv, gh=FakeGitHub(self.fake), supervisor=supervisor)
        informe = json.loads(salida.getvalue())

        self.assertFalse((RAIZ / aparte.name).exists(), "usó la base relativa al árbol")
        self.assertEqual(informe.get("fin"), "terminado", informe)
        self.assertEqual(self.filas(informe)["entregado"]["resultado"], "pasa")
        self.assertEqual(codigo, 0, informe.get("tabla"))

    def test_no_acepta_esperas_negativas_ni_vacias(self):
        for opcion, valor in (
            ("--intervalo-s", "-1"), ("--intervalo-s", "0"), ("--intervalo-s", "nan"),
            ("--tope-cierre-s", "-5"), ("--tope-cierre-s", "nan"), ("--tope-min", "-1"),
        ):
            with self.assertRaises(SystemExit, msg=f"{opcion} {valor}") as salida:
                self.correr(opcion, valor)
            self.assertEqual(salida.exception.code, 2)
        self.assertFalse((self.base / "registro.sqlite").exists(), "recibió un encargo")

    def test_mide_el_arbol_donde_vive_y_no_acepta_otro(self):
        with self.assertRaises(SystemExit) as salida:
            self.correr("--arbol", str(RAIZ))
        self.assertEqual(salida.exception.code, 2)
        self.assertFalse((self.base / "registro.sqlite").exists(), "recibió un encargo")

    def test_repetir_la_corrida_retoma_el_mismo_encargo(self):
        _, primera = self.correr()
        _, segunda = self.correr()

        ident = segunda["encargo"]
        self.assertEqual(ident, primera["encargo"])
        self.assertEqual(segunda["fin"], "terminado")
        self.assertEqual(self.filas(segunda)["termina"]["resultado"], "pasa")
        self.assertNotIn("falló", {b["resultado"] for b in segunda["limpieza"]})
        self.assertEqual(_git(self.repo, "branch", "--list", f"encargo/{ident}"), "")
        for tipo in ("espacio", "ejecucion"):
            self.assertFalse((self.base / tipo / ident).exists(), tipo)

    def test_no_corre_sobre_la_base_viva_ni_con_sus_secretos(self):
        for argv in (["--base", str(self.viva)], ["--base", str(self.viva / "sub")]):
            codigo, salida = self.correr(*argv)
            self.assertEqual((codigo, salida["paso"]), (2, "preparar"), salida)
        (self.base / "secretos.env").symlink_to(self.viva / "secretos.env")
        codigo, salida = self.correr()
        self.assertEqual((codigo, salida["paso"]), (2, "preparar"), salida)
        self.assertIn("secretos.env", salida["error"])
        self.assertFalse((self.base / "registro.sqlite").exists(), "no recibió el encargo")

    def test_preparar_converge_a_los_mismos_enlaces(self):
        pedido = Pedido(
            repo=str(self.repo), base=self.base, base_viva=self.viva,
            despliegue=self.destino, corrida="prueba", maquina="", tope_s=None,
            intervalo_s=1.0, esperado=RAIZ / "ops" / "canario_encargos.esperado.json",
            tope_cierre_s=1.0,
        )
        (self.base / "github.json").symlink_to(self.tmp / "otro-github.json")
        for _ in range(2):
            self.assertEqual(preparar(pedido)[0], "banco")
            for nombre in canario_lanzador.ENLACES:
                self.assertEqual(os.readlink(self.base / nombre), str(self.viva.resolve() / nombre))
        (self.base / "maquina.toml").unlink()
        (self.base / "maquina.toml").write_text('nombre="otra"\n', encoding="utf-8")
        with self.assertRaises(canario_lanzador.NoSePuede):
            preparar(pedido)

    def test_una_base_donde_no_se_puede_enlazar_se_niega_en_preparar(self):
        if os.geteuid() == 0:
            self.skipTest("root enlaza aunque la carpeta sea de solo lectura")
        self.base.chmod(0o555)
        self.addCleanup(self.base.chmod, 0o755)
        codigo, salida = self.correr()
        self.assertEqual((codigo, salida["paso"]), (2, "preparar"), salida)


class ElCodigoDeSalida(unittest.TestCase):
    """0 solo si la tabla coincide, el cierre dejó todo cerrado y la limpieza terminó;
    2 si un paso no pudo correr."""

    def informe(
        self, *, resultado="pasa", estado="terminado", supervisor="ausente", vivos=(),
        limpieza="borrado", fallo=None, medido=True,
    ) -> Informe:
        from ops.canario_lanzador import Cierre

        fila = Fila("termina", "1", resultado, "pasa", "", "")  # type: ignore[arg-type]
        hora = datetime.now(timezone.utc).isoformat()
        return Informe(
            "banco", "a" * 40, hora, "clave", "enc_0", "terminado", "terminado", 1,
            Cierre(estado, "", supervisor, vivos),  # type: ignore[arg-type]
            (fila,) if medido else (), (Borrado("espacio/enc_0", limpieza),),  # type: ignore[arg-type]
            fallo,
        )

    def test_cada_condicion_cuenta(self):
        from ops.canario_lanzador import Fallo

        self.assertEqual(self.informe().codigo, 0)
        self.assertEqual(self.informe(resultado="falla").codigo, 1)
        self.assertEqual(self.informe(supervisor="vivo").codigo, 1)
        self.assertEqual(self.informe(supervisor="incierto").codigo, 1)
        self.assertEqual(self.informe(estado="deteniendo").codigo, 1)
        self.assertEqual(self.informe(vivos=("algún trabajador de despliegue (vivo)",)).codigo, 1)
        self.assertEqual(self.informe(medido=False).codigo, 1)
        self.assertEqual(self.informe(limpieza="falló").codigo, 1)
        self.assertEqual(self.informe(limpieza="no estaba").codigo, 0)
        self.assertEqual(self.informe(fallo=Fallo("medir", "no midió"), medido=False).codigo, 2)


if __name__ == "__main__":
    unittest.main()
