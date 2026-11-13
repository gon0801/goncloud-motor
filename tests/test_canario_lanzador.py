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
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from motor import plataforma
from motor.github import FakeGitHub
from ops import canario_lanzador
from ops.canario_encargos import Fila
from ops.canario_lanzador import Borrado, Informe, Pedido, main, preparar
from tests.test_compuerta_e2e import _git

RAIZ = Path(__file__).resolve().parents[1]
FALSO = RAIZ / "tests" / "canario_supervisor_falso.py"

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


@unittest.skipIf(os.name == "nt", "el supervisor falso deja un sleep en su grupo de POSIX")
class LanzadorEnUnaMaquinaDePrueba(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="canario-lanzador-"))
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

    def correr(self, *extra: str, modo: tuple[str, ...] = ()) -> tuple[int, dict]:
        hijo = self.tmp / "hijo.pid"
        self.addCleanup(_matar, hijo)
        argv = [
            "--repo", str(self.repo), "--base", str(self.base), "--base-viva", str(self.viva),
            "--despliegue", str(self.destino), "--corrida", "prueba", "--intervalo-s", "0.2",
            *extra,
        ]
        supervisor = [
            sys.executable, "-B", str(FALSO), "--base", str(self.base), "--hijo", str(hijo), *modo,
        ]
        salida, avances = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(avances):
            codigo = main(argv, gh=FakeGitHub(self.fake), supervisor=supervisor)
        self.hijo = int(hijo.read_text(encoding="utf-8")) if hijo.is_file() else 0
        self.avances = avances.getvalue()
        return codigo, json.loads(salida.getvalue())

    def filas(self, informe: dict) -> dict[str, dict]:
        return {f["nombre"]: f for f in informe["filas"]}

    def sin_procesos_suyos(self, informe: dict) -> None:
        self.assertEqual(informe["supervisor"], "ausente", self.avances)
        self.assertTrue(self.hijo, "el supervisor falso no llegó a dejar su hijo")
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
        self.assertEqual(informe["parada"], "")
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
        self.assertEqual(informe["parada"], "detenido")
        self.assertNotIn("falló", {b["resultado"] for b in informe["limpieza"]})

    def test_un_supervisor_que_muere_a_media_corrida_no_deja_su_grupo(self):
        codigo, informe = self.correr(modo=("--muere-tras", "2"))

        self.assertEqual(informe["fin"], "supervisor_muerto", self.avances)
        self.assertNotEqual(informe["estado"], "terminado")
        self.assertEqual(self.filas(informe)["termina"]["resultado"], "falla")
        self.assertEqual(codigo, 1)
        self.sin_procesos_suyos(informe)
        self.assertEqual(informe["parada"], "detenido")
        self.assertFalse((self.base / "espacio" / informe["encargo"]).exists())

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
            repo=str(self.repo), arbol=RAIZ, base=self.base, base_viva=self.viva,
            despliegue=self.destino, corrida="prueba", maquina="", tope_s=None,
            intervalo_s=1.0, esperado=RAIZ / "ops" / "canario_encargos.esperado.json",
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


class ElCodigoDeSalida(unittest.TestCase):
    """0 solo si la tabla coincide, el supervisor no dejó a nadie y la limpieza terminó."""

    def informe(self, *, resultado="pasa", supervisor="ausente", limpieza="borrado") -> Informe:
        fila = Fila("termina", "1", resultado, "pasa", "", "")  # type: ignore[arg-type]
        hora = datetime.now(timezone.utc).isoformat()
        return Informe(
            "banco", "a" * 40, hora, "clave", "enc_0", "terminado", "terminado", 1,
            supervisor, (fila,), "", (Borrado("espacio/enc_0", limpieza),),  # type: ignore[arg-type]
        )

    def test_cada_condicion_cuenta(self):
        self.assertEqual(self.informe().codigo, 0)
        self.assertEqual(self.informe(resultado="falla").codigo, 1)
        self.assertEqual(self.informe(supervisor="vivo").codigo, 1)
        self.assertEqual(self.informe(supervisor="incierto").codigo, 1)
        self.assertEqual(self.informe(limpieza="falló").codigo, 1)
        self.assertEqual(self.informe(limpieza="no estaba").codigo, 0)


if __name__ == "__main__":
    unittest.main()
