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
import errno
import io
import json
import os
import shutil
import signal
import sqlite3
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
from motor.servicio import vincular_base
from ops import canario_apagon, canario_lanzador, canario_observar
from ops.canario_juzgar import Fila
from ops.canario_lanzador import Borrado, Informe, Pedido, main, preparar
from tests.dobles import GitHubDelCanario
from tests.test_compuerta_e2e import _git

RAIZ = Path(__file__).resolve().parents[1]
FALSO = RAIZ / "tests" / "canario_supervisor_falso.py"
CERRADOS = ("terminado", "detenido", "cancelado")
INFORME = "canario-informe.json"

# El lanzador como proceso propio, con el GitHub falso y el supervisor que se le pasen,
# y SIGHUP como lo recibe un proceso de ssh aunque quien corre la prueba lo ignore.
_LANZADOR = (
    "import json, signal, sys\n"
    "from pathlib import Path\n"
    "signal.signal(signal.SIGHUP, signal.SIG_DFL)\n"
    "from ops.canario_lanzador import main\n"
    "from tests.dobles import GitHubDelCanario\n"
    "argv, falso, supervisor = json.loads(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])\n"
    "raise SystemExit(main(argv, gh=GitHubDelCanario(falso), supervisor=supervisor))\n"
)


class _SesionQueSeCae(io.StringIO):
    """La salida de ssh en la PC al caerse la sesión: desde la línea que trae `tras`,
    escribir falla con EINVAL."""

    def __init__(self, tras: str = ""):
        super().__init__()
        self.tras, self.caida = tras, not tras

    def write(self, texto: str) -> int:
        if self.caida:
            raise OSError(errno.EINVAL, "Invalid argument")
        self.caida = self.tras in texto
        return super().write(texto)

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


def _matar_grupo(pid_en: Path) -> None:
    with contextlib.suppress(OSError, ValueError):
        os.killpg(int(pid_en.read_text(encoding="utf-8")), signal.SIGKILL)


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
        self.base.mkdir()
        poteto = self.viva / "politica" / "poteto" / "skills" / "poteto-mode"
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

    def argv(self, *extra: str) -> list[str]:
        if "--tope-min" not in extra and "--esperado" not in extra:
            # Un encargo que no avanza falla al tope y no espera los 90 min de lo esperado;
            # una prueba con su propio esperado trae también sus topes.
            extra = (*extra, "--tope-min", "1")
        return [
            "--repo", str(self.repo), "--base", str(self.base), "--base-viva", str(self.viva),
            "--despliegue", str(self.destino), "--corrida", "prueba", "--intervalo-s", "0.2",
            *extra,
        ]

    def correr(
        self, *extra: str, modo: tuple[str, ...] = (), supervisor: list[str] | None = None,
    ) -> tuple[int, dict]:
        hijo = self.tmp / "hijo.pid"
        self.addCleanup(_matar, hijo)
        argv = self.argv(*extra)
        supervisor = supervisor or [
            sys.executable, "-B", str(FALSO), "--base", str(self.base), "--hijo", str(hijo), *modo,
        ]
        salida, avances = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(avances):
                codigo = main(argv, gh=GitHubDelCanario(self.fake), supervisor=supervisor)
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

    def eventos(self, encargo: str) -> list[dict]:
        reg = Registro(self.base)
        try:
            return reg.eventos(encargo)
        finally:
            reg.close()

    def cerro_en(self, encargo: str) -> int:
        """El id del evento con que `encargo` quedó cerrado en el registro."""
        reg = Registro(self.base)
        try:
            return next(
                ev["id"] for ev in reg.eventos(encargo)
                if getattr(reg.fold(encargo, ev["id"]), "estado", "") in CERRADOS
            )
        finally:
            reg.close()

    def queda_cerrado(self) -> None:
        estados = self.estados()
        self.assertTrue(estados, "no se recibió ningún encargo")
        self.assertTrue(set(estados) <= set(CERRADOS), f"{estados}: un supervisor posterior lo retomaría")
        if self.hijo:
            self.assertFalse(plataforma.pid_vivo(self.hijo), "el hijo del supervisor sigue vivo")

    def test_una_corrida_que_termina_da_la_tabla_esperada_y_borra_solo_lo_suyo(self):  # no-roja-en-base: la corrida ahora limpia también el encargo que falla; la prueba sigue mirando solo lo del que termina
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
            {b["resto"]: b["resultado"] for b in informe["limpieza"] if ident in b["resto"]},
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

    def test_el_encargo_que_falla_a_proposito_mide_se_detiene_y_se_limpia(self):
        codigo, informe = self.correr()

        detencion = informe["detencion"]
        ident = detencion["encargo"]
        fila = self.filas(informe)["se detiene"]
        self.assertEqual(codigo, 0, informe["tabla"])
        self.assertEqual((detencion["fin"], detencion["cierre"]["parada"]), ("terminado", ""), self.avances)
        self.assertEqual(fila["resultado"], "pasa", fila["observado"])
        for texto in (
            "quedó como entrega parcial: la fase uno, «", "se paró solo", "dijo por qué: «",
            "revirtió su despliegue (rollback salida 0)", "gastó turnos",
        ):
            self.assertIn(texto, fila["observado"])
        self.assertFalse((self.destino / "se-detiene").exists(), "el rollback no quitó lo desplegado")
        self.assertEqual(_git(self.repo, "show", "main:canario/se-detiene/banco.txt"), ident)
        self.assertEqual(
            {b["resto"]: b["resultado"] for b in informe["limpieza"] if ident in b["resto"]},
            {
                f"la rama remota encargo/{ident}": "borrado",
                f"espacio/{ident}": "borrado",
                f"ejecucion/{ident}": "borrado",
                f"canario-escenarios/{ident}.json": "borrado",
                f"trabajos/{ident}.json de la flota": "borrado",
            },
        )

    def test_el_encargo_con_cortes_mide_no_pierde_y_no_repite_y_se_limpia(self):
        """El lanzador corta su supervisor con el turno del responsable y la receta de
        despliegue esperando su compuerta, lanza otro y la suelta. El supervisor falso barre
        con `ciclo.barrer`, así que lo que retoma tras el corte es lo del motor."""
        codigo, informe = self.correr()

        apagon = informe["apagon"]
        ident = apagon["encargo"]
        filas = self.filas(informe)
        self.assertEqual(codigo, 0, informe["tabla"])
        self.assertEqual((apagon["fin"], apagon["cierre"]["parada"]), ("terminado", ""), self.avances)
        for momento in ("el turno del responsable", "la receta de despliegue"):
            self.assertIn(f"corté el supervisor durante {momento}: el que esperaba siguió vivo", self.avances)
        self.assertEqual(filas["no pierde"]["resultado"], "pasa", filas["no pierde"]["observado"])
        self.assertIn("llegó al mismo final que sin corte", filas["no pierde"]["observado"])
        self.assertEqual(filas["no repite"]["resultado"], "pasa", filas["no repite"]["observado"])
        self.assertIn(
            "sin repetir: responsable 1, revisor 1, desplegar 1, checklist 1 y rollback 0 veces",
            filas["no repite"]["observado"],
        )
        self.assertIn("1 PR y 1 merge", filas["no repite"]["observado"])
        self.assertEqual((self.destino / "apagon" / "banco.txt").read_text(encoding="utf-8"), f"{ident}\n")
        self.assertEqual(_git(self.repo, "show", "main:canario/apagon/banco.txt"), ident)
        self.sin_procesos_suyos(informe)
        self.assertEqual(
            {b["resto"]: b["resultado"] for b in informe["limpieza"] if ident in b["resto"]},
            {
                f"la rama remota encargo/{ident}": "borrado",
                f"espacio/{ident}": "borrado",
                f"ejecucion/{ident}": "borrado",
                f"canario-escenarios/{ident}.json": "borrado",
                f"canario-apagon/{ident}": "borrado",
                f"trabajos/{ident}.json de la flota": "borrado",
            },
        )

    def test_sin_apagon_sus_filas_quedan_sin_medir_y_dicen_por_que(self):
        codigo, informe = self.correr("--sin-apagon")

        for nombre in ("no pierde", "no repite"):
            fila = self.filas(informe)[nombre]
            self.assertEqual(fila["resultado"], "no medido")
            self.assertEqual(fila["observado"], "la corrida se lanzó con --sin-apagon")
        self.assertIsNone(informe["apagon"])
        self.assertFalse((self.base / "canario-apagon").exists())
        self.assertEqual(codigo, 1)

    def test_en_windows_no_corta_y_dice_por_que(self):
        self.assertEqual(canario_apagon.por_que_no_corta(True, "nt"), canario_apagon.EN_WINDOWS)
        self.assertIn("matar_proc cierra el árbol del supervisor", canario_apagon.EN_WINDOWS)
        self.assertEqual(canario_apagon.por_que_no_corta(True, "posix"), "")

    def test_sin_se_detiene_la_fila_queda_sin_medir_y_la_tabla_no_coincide(self):
        codigo, informe = self.correr("--sin-se-detiene", "--sin-apagon")

        fila = self.filas(informe)["se detiene"]
        self.assertEqual((fila["resultado"], fila["discrepancia"]), ("no medido", "dejó de medirse"))
        self.assertIsNone(informe["detencion"])
        self.assertEqual(self.estados(), ["terminado"])
        self.assertEqual(codigo, 1)

    def test_el_encargo_que_falla_se_para_a_su_tope_aunque_el_otro_siga(self):
        """El tope de cada encargo cuenta desde su propio arranque: el que falla no corre
        hasta que el otro termine. Con 0 min, el canario lo para en cuanto lo ve."""
        esperado = self.tmp / "esperado.json"
        datos = json.loads((RAIZ / "ops" / "canario_encargos.esperado.json").read_text(encoding="utf-8"))
        datos["topes"].update(minutos_termina=1, minutos_se_detiene=0)
        esperado.write_text(json.dumps(datos), encoding="utf-8")

        _, informe = self.correr("--esperado", str(esperado))

        falla = informe["detencion"]["encargo"]
        self.assertEqual((informe["fin"], informe["detencion"]["fin"]), ("terminado", "tope"), self.avances)
        ordenes = [ev["id"] for ev in self.eventos(falla) if ev["kind"] == "orden"]
        self.assertTrue(ordenes, "el canario no paró el encargo que falla")
        self.assertLess(ordenes[0], self.cerro_en(informe["encargo"]), "lo paró después de que el otro terminó")
        fila = self.filas(informe)["se detiene"]
        self.assertIn("no se paró solo: recibió la orden parar", fila["observado"])
        self.queda_cerrado()

    def test_una_corrida_que_vence_el_tope_para_su_encargo_y_lo_limpia(self):
        codigo, informe = self.correr("--tope-min", "0.05", modo=("--quieto",))

        self.assertEqual(informe["fin"], "tope", self.avances)
        self.assertEqual(self.filas(informe)["termina"]["resultado"], "falla")
        detenido = self.filas(informe)["se detiene"]
        self.assertEqual(detenido["resultado"], "falla", "el canario contó su propia parada")
        self.assertIn("no se paró solo: recibió la orden parar", detenido["observado"])
        self.assertEqual(codigo, 1)
        self.sin_procesos_suyos(informe)
        self.assertEqual(informe["cierre"]["parada"], "detenido")
        self.assertNotIn("falló", {b["resultado"] for b in informe["limpieza"]})

    def test_un_supervisor_que_muere_a_media_corrida_no_deja_su_grupo(self):
        codigo, informe = self.correr("--sin-apagon", modo=("--muere-tras", "2"))

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

        codigo, informe = self.correr("--sin-se-detiene", "--sin-apagon", modo=("--muere-al-desplegar",))

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

        codigo, informe = self.correr(
            "--tope-cierre-s", "2", "--sin-se-detiene", "--sin-apagon", modo=("--muere-al-desplegar",),
        )

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

    def test_un_parar_que_no_cierra_el_encargo_no_deja_borrar_nada(self):  # no-roja-en-base: la base ya sale con 1; la prueba afirmaba solo "distinto de 0" y ahora fija el 1 que DeepSeek puso en duda
        motor = canario_lanzador._motor

        def parar_falla(p, *argv):
            if argv[0] == "parar":
                raise RuntimeError("motor parar salió 1: la base está ocupada")
            return motor(p, *argv)

        with mock.patch.object(canario_lanzador, "_motor", parar_falla):
            codigo, informe = self.correr("--sin-se-detiene", "--sin-apagon", modo=("--muere-tras", "2"))

        self.assertEqual(codigo, 1, informe)
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
            codigo = main(argv, gh=GitHubDelCanario(self.fake), supervisor=supervisor)
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

    def test_una_sesion_perdida_no_corta_la_corrida(self):
        """Al caerse ssh, escribir en la sesión falla con EPIPE y puede llegar un SIGHUP."""
        hijo, grupo = self.tmp / "hijo.pid", self.tmp / "supervisor.pid"
        self.addCleanup(_matar, hijo)
        self.addCleanup(_matar_grupo, grupo)
        supervisor = [
            "/bin/sh", "-c", 'echo $$ > "$0"; exec "$@"', str(grupo),
            sys.executable, "-B", str(FALSO), "--base", str(self.base), "--hijo", str(hijo),
        ]
        lanzador = subprocess.Popen(
            [sys.executable, "-B", "-c", _LANZADOR, json.dumps(self.argv()), str(self.fake),
             json.dumps(supervisor)],
            cwd=RAIZ, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: lanzador.poll() is None and lanzador.kill())
        assert lanzador.stdout is not None and lanzador.stderr is not None
        for linea in lanzador.stderr:
            if b"canario [lanzar]" in linea:
                break
        lanzador.stdout.close()
        lanzador.stderr.close()
        lanzador.send_signal(signal.SIGHUP)
        codigo = lanzador.wait(timeout=120)
        self.hijo = int(hijo.read_text(encoding="utf-8")) if hijo.is_file() else 0

        self.assertTrue((self.base / INFORME).is_file(), f"salió {codigo} sin dejar su informe")
        informe = json.loads((self.base / INFORME).read_text(encoding="utf-8"))
        ident = informe["encargo"]
        self.assertEqual((informe["corrida"], informe["fin"]), ("prueba", "terminado"), informe)
        self.assertEqual((codigo, informe["codigo"]), (0, 0), informe["tabla"])
        self.assertEqual({b["resultado"] for b in informe["limpieza"]}, {"borrado"}, informe["limpieza"])
        self.assertEqual(_git(self.repo, "branch", "--list", f"encargo/{ident}"), "")
        self.assertFalse((self.base / "espacio" / ident).exists())
        self.queda_cerrado()

    def test_una_salida_que_falla_como_la_de_la_pc_no_corta_la_corrida(self):
        """En la PC, al caerse ssh, escribir en la sesión falla con EINVAL."""
        hijo = self.tmp / "hijo.pid"
        self.addCleanup(_matar, hijo)
        supervisor = [sys.executable, "-B", str(FALSO), "--base", str(self.base), "--hijo", str(hijo)]

        with contextlib.redirect_stdout(_SesionQueSeCae()), \
                contextlib.redirect_stderr(_SesionQueSeCae("canario [lanzar]")):
            codigo = main(self.argv(), gh=GitHubDelCanario(self.fake), supervisor=supervisor)

        informe = json.loads((self.base / INFORME).read_text(encoding="utf-8"))
        self.assertEqual((codigo, informe["codigo"], informe["fin"]), (0, 0, "terminado"), informe)
        self.assertFalse((self.base / "espacio" / informe["encargo"]).exists())

    def test_cada_corrida_recoge_los_restos_de_las_anteriores_ya_cerradas(self):
        with mock.patch.object(canario_lanzador, "limpiar", return_value=()):
            _, vieja = self.correr("--corrida", "vieja")
        viejo = vieja["encargo"]
        restos = [
            self.base / "espacio" / viejo, self.base / "ejecucion" / viejo,
            self.flota / "trabajos" / f"{viejo}.json",
        ]
        self.assertEqual(vieja["fin"], "terminado", self.avances)
        for resto in restos:
            self.assertTrue(os.path.lexists(resto), f"la corrida vieja no dejó {resto}")
        self.assertIn(viejo, _git(self.repo, "branch", "--list", f"encargo/{viejo}"))

        codigo, nueva = self.correr("--corrida", "nueva")

        self.assertNotEqual(nueva["encargo"], viejo)
        for resto in restos:
            self.assertFalse(os.path.lexists(resto), f"no recogió {resto}")
        self.assertEqual(_git(self.repo, "branch", "--list", f"encargo/{viejo}"), "")
        self.assertEqual(
            {b["resto"]: b["resultado"] for b in nueva["recogida"]}[f"espacio/{viejo}"], "borrado",
        )
        self.assertIn("encargo/enc_ajeno", _git(self.repo, "branch", "--list", "encargo/enc_ajeno"))
        self.assertEqual(codigo, 0, nueva["tabla"])

    def test_recoger_no_toca_los_restos_de_un_encargo_abierto(self):
        pedido = self.pedido()
        preparar(pedido)
        abierto = canario_lanzador.encargar(pedido, "canario-abierto")
        restos = [self.base / "espacio" / abierto / "x", self.base / "ejecucion" / abierto / "x"]
        for resto in restos:
            resto.parent.mkdir(parents=True)
            resto.write_text("{}", encoding="utf-8")

        canario_lanzador.recoger(pedido)

        for resto in restos:
            self.assertTrue(resto.is_file(), f"borró {resto} de un encargo abierto")

    def test_recoger_falla_si_no_puede_leer_las_ramas_remotas(self):
        pedido = self.pedido()
        preparar(pedido)
        cerrado = canario_lanzador.encargar(pedido, "canario-cerrado")
        self.assertIn(canario_lanzador._parar(pedido, cerrado), CERRADOS)
        ciclo = ValueError("los remotos locales del repositorio forman un ciclo")

        with mock.patch.object(canario_lanzador, "resolver_repositorio", side_effect=ciclo):
            recogida = canario_lanzador.recoger(pedido)

        self.assertIn("falló", {b.resultado for b in recogida}, "dio por limpia una rama que no pudo mirar")

    def test_recoger_no_borra_los_restos_de_un_encargo_cerrado_con_un_proceso_vivo(self):
        pedido = self.pedido()
        preparar(pedido)
        cerrado = canario_lanzador.encargar(pedido, "canario-cerrado")
        self.assertIn(canario_lanzador._parar(pedido, cerrado), CERRADOS)
        turno = subprocess.Popen(
            ["sleep", "600"], stdin=subprocess.DEVNULL, start_new_session=True,
        )
        self.addCleanup(turno.wait)
        self.addCleanup(turno.kill)
        restos = [self.base / "espacio" / cerrado / "x", self.base / "ejecucion" / cerrado / "proc.json"]
        for resto in restos:
            resto.parent.mkdir(parents=True, exist_ok=True)
        restos[0].write_text("{}", encoding="utf-8")
        restos[1].write_text(json.dumps({
            "pid": turno.pid, "boot_id": plataforma.boot_id(), "inicio": plataforma.hora_inicio(turno.pid),
        }), encoding="utf-8")

        recogida = canario_lanzador.recoger(pedido)

        for resto in restos:
            self.assertTrue(resto.is_file(), f"borró {resto} con un proceso del encargo vivo")
        suyo = {b.resto: b for b in recogida}.get(f"los restos de {cerrado}")
        self.assertEqual(suyo and suyo.resultado, "falló", recogida)
        self.assertIn("(vivo)", suyo.causa if suyo else "", "no dijo que el proceso sigue vivo")

    def test_el_informe_no_escribe_por_un_enlace_que_ya_estaba(self):
        vivo = self.viva / "registro.sqlite"
        vivo.write_bytes(b"registro vivo")
        (self.base / f"{INFORME}.tmp").symlink_to(vivo)
        (self.base / "secretos.env").write_text("", encoding="utf-8")

        codigo, _ = self.correr()

        self.assertEqual(vivo.read_bytes(), b"registro vivo", "el informe cayó en la base viva")
        informe = json.loads((self.base / INFORME).read_text(encoding="utf-8"))
        self.assertEqual((codigo, informe["codigo"], informe["paso"]), (2, 2, "preparar"))

    def test_no_corre_sobre_la_base_viva_ni_con_sus_secretos(self):
        for argv in (["--base", str(self.viva)], ["--base", str(self.viva / "sub")]):
            codigo, salida = self.correr(*argv)
            self.assertEqual((codigo, salida["paso"]), (2, "preparar"), salida)
        (self.base / "secretos.env").symlink_to(self.viva / "secretos.env")
        codigo, salida = self.correr()
        self.assertEqual((codigo, salida["paso"]), (2, "preparar"), salida)
        self.assertIn("secretos.env", salida["error"])
        self.assertFalse((self.base / "registro.sqlite").exists(), "no recibió el encargo")

    def enlazados(self) -> list[Path]:
        return [
            c.ruta for c in canario_lanzador.COMPARTIDOS
            if c.forma == "enlace duro" and c.obligatorio
        ]

    def test_preparar_converge_a_los_mismos_enlaces(self):  # no-roja-en-base: la base ya enlazaba; la prueba ahora mira que sea el mismo archivo que el vivo y no la clase de enlace, que pasa a ser duro
        pedido = self.pedido()
        (self.base / "github.json").symlink_to(self.tmp / "otro-github.json")
        for _ in range(2):
            self.assertEqual(preparar(pedido)[0], "banco")
            for ruta in self.enlazados():
                self.assertTrue(os.path.samefile(self.base / ruta, self.viva / ruta), ruta)
        (self.base / "maquina.toml").unlink()
        (self.base / "maquina.toml").write_text('nombre="otra"\n', encoding="utf-8")
        with self.assertRaises(canario_lanzador.NoSePuede):
            preparar(pedido)

    def test_preparar_enlaza_en_un_host_que_no_deja_crear_enlaces_simbolicos(self):
        sin_privilegio = OSError(errno.EPERM, "A required privilege is not held by the client")
        with mock.patch.object(os, "symlink", side_effect=sin_privilegio):
            self.assertEqual(preparar(self.pedido())[0], "banco")
        for ruta in self.enlazados():
            self.assertTrue(os.path.samefile(self.base / ruta, self.viva / ruta), ruta)

    def test_flota_home_llega_como_copia_que_el_motor_puede_reescribir(self):
        viva = self.viva / "flota-home.json"
        antes = viva.read_bytes()
        os.link(viva, self.base / "flota-home.json")
        otra = self.tmp / "otra-flota"
        (otra / "generaciones").mkdir(parents=True)

        preparar(self.pedido())
        self.assertEqual((self.base / "flota-home.json").read_bytes(), antes)
        vincular_base(self.base, otra)

        self.assertEqual(viva.read_bytes(), antes, "lo que el motor escribió en el canario cayó en la base viva")

    def test_preparar_no_borra_la_politica_viva_por_una_carpeta_enlazada(self):
        skill = self.viva / "politica" / "poteto" / "skills" / "poteto-mode" / "SKILL.md"
        (self.base / "politica").symlink_to(self.viva / "politica")

        with self.assertRaises(canario_lanzador.NoSePuede) as negado:
            preparar(self.pedido())

        self.assertEqual(negado.exception.paso, "preparar")
        self.assertTrue(skill.is_file(), "borró la política de la base viva")

    def test_limpiar_no_borra_fuera_de_su_base_por_una_carpeta_enlazada(self):
        pedido = self.pedido()
        preparar(pedido)
        fuera = self.tmp / "fuera" / "enc_ab" / "x"
        fuera.parent.mkdir(parents=True)
        fuera.write_text("{}", encoding="utf-8")
        (self.base / "espacio").symlink_to(self.tmp / "fuera")

        with mock.patch.object(canario_lanzador, "_ramas", return_value=[]):
            borrados = {b.resto: b for b in canario_lanzador.limpiar(pedido, "enc_ab")}

        self.assertTrue(fuera.is_file(), "borró fuera de su base")
        self.assertEqual(borrados["espacio/enc_ab"].resultado, "falló")

    def test_preparar_trae_la_politica_de_poteto_de_la_base_viva_y_la_quita_si_ya_no_esta(self):
        skill = Path("politica") / "poteto" / "skills" / "poteto-mode" / "SKILL.md"
        preparar(self.pedido())
        self.assertEqual((self.base / skill).read_bytes(), (self.viva / skill).read_bytes())
        self.assertFalse(os.path.samefile(self.base / skill, self.viva / skill), "enlazó la política viva")

        shutil.rmtree(self.viva / "politica")
        preparar(self.pedido())
        self.assertFalse((self.base / "politica" / "poteto").exists())

    def test_preparar_enlaza_el_token_de_los_agentes_si_la_base_viva_lo_tiene_y_sigue_sin_el(self):
        viva = self.viva / "github-agentes.token"
        viva.write_text("token-falso-de-prueba\n", encoding="utf-8")
        viva.chmod(0o600)
        preparar(self.pedido())
        self.assertTrue(os.path.samefile(self.base / "github-agentes.token", viva))

        viva.unlink()
        self.assertEqual(preparar(self.pedido())[0], "banco")
        self.assertFalse(os.path.lexists(self.base / "github-agentes.token"))

    def test_una_base_donde_no_se_puede_enlazar_se_niega_en_preparar(self):
        if os.geteuid() == 0:
            self.skipTest("root enlaza aunque la carpeta sea de solo lectura")
        self.base.chmod(0o555)
        self.addCleanup(self.base.chmod, 0o755)
        codigo, salida = self.correr()
        self.assertEqual((codigo, salida["paso"]), (2, "preparar"), salida)

    def pedido(self) -> Pedido:
        return Pedido(
            repo=str(self.repo), base=self.base, base_viva=self.viva,
            despliegue=self.destino, corrida="prueba", maquina="", tope_s=None,
            intervalo_s=1.0, esperado=RAIZ / "ops" / "canario_encargos.esperado.json",
            tope_cierre_s=1.0,
        )

    def test_no_escribe_en_un_registro_enlazado(self):
        """SQLite escribe en el registro, su -wal y su -shm: ninguno puede ser el de la base viva."""
        pedido = self.pedido()
        Registro(self.viva).close()
        otro = self.tmp / "otro.sqlite"
        otro.write_bytes(b"")
        casos = (
            ("enlace al vivo", lambda vivo, propio: propio.symlink_to(vivo)),
            ("enlace a otro archivo", lambda _vivo, propio: propio.symlink_to(otro)),
            ("enlace duro al vivo", lambda vivo, propio: os.link(vivo, propio)),
        )
        for nombre in ("registro.sqlite", "registro.sqlite-wal", "registro.sqlite-shm"):
            vivo, propio = self.viva / nombre, self.base / nombre
            if not vivo.exists():
                vivo.write_bytes(b"")
            for caso, enlazar in casos:
                with self.subTest(archivo=nombre, caso=caso):
                    enlazar(vivo, propio)
                    try:
                        with self.assertRaises(canario_lanzador.NoSePuede) as negado:
                            preparar(pedido)
                        self.assertEqual(negado.exception.paso, "preparar")
                    finally:
                        propio.unlink()
        Registro(self.base).close()
        self.assertEqual(preparar(pedido)[0], "banco")

    def test_un_registro_que_no_deja_leer_las_ramas_no_tumba_la_limpieza(self):
        bloqueado = sqlite3.OperationalError("database is locked")
        with mock.patch.object(canario_lanzador, "_ramas", side_effect=bloqueado):
            codigo, informe = self.correr()

        ident = informe["encargo"]
        limpieza = {b["resto"]: b for b in informe["limpieza"]}
        ramas = limpieza["las ramas remotas del encargo"]
        self.assertEqual(ramas["resultado"], "falló")
        self.assertIn("database is locked", ramas["causa"])
        for resto in (f"espacio/{ident}", f"ejecucion/{ident}", f"trabajos/{ident}.json de la flota"):
            self.assertEqual(limpieza[resto]["resultado"], "borrado", resto)
        self.assertEqual(codigo, 1)

    def test_limpiar_borra_un_espacio_con_archivos_de_solo_lectura(self):
        """Git deja sus objetos de solo lectura; en Windows eso basta para que rmtree no
        los borre. Aquí lo provoca una carpeta sin permiso de escritura."""
        if os.geteuid() == 0:
            self.skipTest("root borra aunque la carpeta sea de solo lectura")
        pedido = self.pedido()
        preparar(pedido)
        pack = self.base / "espacio" / "enc_ab" / ".git" / "objects" / "pack"
        pack.mkdir(parents=True)
        (pack / "pack-1.idx").write_bytes(b"idx")
        (pack / "pack-1.idx").chmod(0o444)
        pack.chmod(0o555)
        self.addCleanup(lambda: pack.is_dir() and pack.chmod(0o755))

        with mock.patch.object(canario_lanzador, "_ramas", return_value=[]):
            borrados = {b.resto: b for b in canario_lanzador.limpiar(pedido, "enc_ab")}

        espacio = borrados["espacio/enc_ab"]
        self.assertEqual(espacio.resultado, "borrado", espacio.causa)
        self.assertFalse((self.base / "espacio" / "enc_ab").exists())

    def test_limpiar_no_borra_nada_con_lo_que_no_es_un_id(self):
        pedido = self.pedido()
        preparar(pedido)
        for carpeta in (self.base / "espacio", self.base / "ejecucion", self.flota / "trabajos"):
            carpeta.mkdir(parents=True, exist_ok=True)
        victimas = [self.tmp / "victima" / "x", self.tmp / "victima.json"]
        for ruta in victimas:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text("{}", encoding="utf-8")

        with mock.patch.object(canario_lanzador, "_ramas", return_value=[]):
            borrados = canario_lanzador.limpiar(pedido, "../../victima")

        for ruta in victimas:
            self.assertTrue(ruta.is_file(), f"borró {ruta}")
        self.assertEqual({b.resultado for b in borrados}, {"falló"}, borrados)

    def test_limpiar_solo_borra_la_rama_que_el_motor_nombra_para_su_encargo(self):
        pedido = self.pedido()
        preparar(pedido)
        ajenas = ["encargo/enc_abc", "otro-repo/enc_ab"]
        suya = "encargo/enc_ab"
        _git(self.tmp / "semilla", "push", "-q", str(self.repo), *(f"main:{r}" for r in [*ajenas, suya]))

        with mock.patch.object(canario_lanzador, "_ramas", return_value=[*ajenas, suya]):
            borrados = {b.resto: b.resultado for b in canario_lanzador.limpiar(pedido, "enc_ab")}

        for rama in ajenas:
            self.assertIn(rama, _git(self.repo, "branch", "--list", rama), f"borró {rama}")
            self.assertEqual(borrados[f"la rama remota {rama}"], "falló")
        self.assertEqual(_git(self.repo, "branch", "--list", suya), "")
        self.assertEqual(borrados[f"la rama remota {suya}"], "borrado")

    def test_una_comprobacion_que_no_responde_tras_borrar_la_rama_no_tumba_la_limpieza(self):
        pedido = self.pedido()
        preparar(pedido)
        _git(self.tmp / "semilla", "push", "-q", str(self.repo), "main:encargo/enc_ab")
        resolver = canario_observar.resolver_repositorio
        vistas: list[str] = []

        def sin_respuesta_al_volver(repo):
            vistas.append(repo)
            if len(vistas) > 1:
                raise subprocess.TimeoutExpired(["git", "config"], 30)
            return resolver(repo)

        with mock.patch.object(canario_lanzador, "_ramas", return_value=["encargo/enc_ab"]), \
                mock.patch.object(canario_observar, "resolver_repositorio", sin_respuesta_al_volver):
            borrados = {b.resto: b for b in canario_lanzador.limpiar(pedido, "enc_ab")}

        rama = borrados["la rama remota encargo/enc_ab"]
        self.assertEqual(rama.resultado, "falló", rama)
        self.assertIn("espacio/enc_ab", borrados)

    def test_un_detener_que_falla_no_deja_el_encargo_vivo(self):
        detener = canario_lanzador.detener

        def detener_falla(supervisor):
            detener(supervisor)
            raise OSError("ps no respondió")

        with mock.patch.object(canario_lanzador, "detener", detener_falla):
            codigo, informe = self.correr("--tope-min", "0.05", modo=("--quieto",))

        self.assertEqual((codigo, informe["paso"]), (2, "cerrar"), informe)
        self.assertTrue(any("OSError" in f for f in informe["cierre"]["fallas"]), informe["cierre"])
        self.assertEqual(
            (informe["filas"], informe["limpieza"]), ([], []),
            "midió o borró sin saber cómo quedó su supervisor",
        )
        self.queda_cerrado()

    def test_un_segundo_ctrl_c_dentro_del_cierre_no_deja_el_encargo_vivo(self):
        detener = canario_lanzador.detener

        def detener_cortado(supervisor):
            detener(supervisor)
            raise KeyboardInterrupt

        with mock.patch.object(canario_lanzador, "esperar", side_effect=KeyboardInterrupt), \
                mock.patch.object(canario_lanzador, "detener", detener_cortado):
            with self.assertRaises(KeyboardInterrupt):
                self.correr()

        self.queda_cerrado()


class DetenerAlSupervisor(unittest.TestCase):
    def test_un_grupo_incierto_mientras_se_cierra_se_sigue_mirando(self):
        supervisor = {"pid": 424242, "inicio": "100", "job": "Local\\canario"}
        with mock.patch.object(plataforma, "matar_proc"), mock.patch.object(plataforma, "cerrar_huerfanos"):
            vistas = iter(["incierto", "incierto", "ausente"])
            with mock.patch.object(plataforma, "presencia_grupo", lambda *a: next(vistas)):
                self.assertEqual(canario_lanzador.detener(supervisor), "ausente")
            with mock.patch.object(plataforma, "presencia_grupo", lambda *a: "incierto"), \
                    mock.patch.object(canario_lanzador, "ESPERA_GRUPO_S", 0.3):
                self.assertEqual(canario_lanzador.detener(supervisor), "incierto")


class ElCodigoDeSalida(unittest.TestCase):
    """0 solo si la tabla coincide, el cierre dejó todo cerrado y la limpieza terminó;
    2 si un paso no pudo correr."""

    def informe(
        self, *, resultado="pasa", estado="terminado", supervisor="ausente", vivos=(),
        limpieza="borrado", fallo=None, medido=True, recogida="borrado", detencion="terminado",
    ) -> Informe:
        from ops.canario_lanzador import Cierre, Seguimiento

        fila = Fila("termina", "1", resultado, "pasa", "", "")  # type: ignore[arg-type]
        hora = datetime.now(timezone.utc).isoformat()
        cierre = Cierre(detencion, "", supervisor, ())  # type: ignore[arg-type]
        return Informe(
            "banco", "a" * 40, hora, "clave", "enc_0", "terminado", "terminado", 1,
            Cierre(estado, "", supervisor, vivos),  # type: ignore[arg-type]
            (fila,) if medido else (), (Borrado("espacio/enc_0", limpieza),),  # type: ignore[arg-type]
            fallo, recogida=(Borrado("espacio/enc_1", recogida),),  # type: ignore[arg-type]
            detencion=Seguimiento("clave-se-detiene", "enc_2", "terminado", detencion, cierre),
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
        self.assertEqual(self.informe(recogida="falló").codigo, 1)
        self.assertEqual(self.informe(detencion="deteniendo").codigo, 1)
        self.assertEqual(self.informe(fallo=Fallo("medir", "no midió"), medido=False).codigo, 2)


if __name__ == "__main__":
    unittest.main()
