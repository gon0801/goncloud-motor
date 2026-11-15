"""La herramienta de plegado dice qué encargo guardado se rompe o cambia.

Cada prueba arma un registro chico en un temporal, con la misma tabla que
escribe el motor, y corre `tools/plegado.py` como lo corre una persona.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.registro import ClaveOcupada, Evento, Registro
from tools.plegado import campos_de

RAIZ = Path(__file__).resolve().parents[1]
HERRAMIENTA = RAIZ / "tools" / "plegado.py"

_ENTREGA = ("entrega", {"id": "enc_a", "clave": "k", "huella": "h", "texto": "haz algo", "hora": "t0"})
_CANCELAR = ("orden", {"accion": "cancelar"})


def _intento(host: str | None) -> list[tuple[str, dict]]:
    con_host = {"host": host} if host else {}
    return [
        ("reserva", {"rol": "responsable", "hora": "t1", **con_host}),
        ("intento_abierto", {"id": "A", "pid": 1, "inicio": "x", "rol": "responsable", **con_host}),
    ]


# Guardado con las reglas de su día; una escritura nueva hoy lo rechazaría.
_FASES_VACIAS = ("fases", {"fases": []})


def _armar(ruta: Path, encargos: dict[str, list[tuple[str, dict | str]]]) -> None:
    """Un `data` en texto se guarda tal cual, para armar filas que no son JSON."""
    db = sqlite3.connect(ruta)
    db.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
        " encargo TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
    )
    for encargo, eventos in encargos.items():
        for kind, data in eventos:
            db.execute(
                "INSERT INTO events (ts, encargo, kind, data) VALUES ('t', ?, ?, ?)",
                (encargo, kind, data if isinstance(data, str) else json.dumps(data)),
            )
    db.commit()
    db.close()


def _sin_lectura(ruta: Path) -> Path:
    _armar(ruta, {"enc_a": [_ENTREGA]})
    ruta.chmod(0)
    return ruta


_SIN_PERMISOS_POSIX = unittest.skipIf(
    os.name == "nt" or os.geteuid() == 0, "necesita permisos de POSIX sin ser root")


def _ejecutar(*args: str | Path, herramienta: Path = HERRAMIENTA,
              env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(herramienta), *map(str, args)],
        capture_output=True, encoding="utf-8", check=False, env=env,
    )


def _insertar_al_plegar(registro: Path, kind: str, encargo: str, nuevo: str) -> str:
    """Una línea de `reducir` que, al plegar `kind`, agrega a `encargo` un evento `nuevo`."""
    insertar = ("INSERT INTO events (ts, encargo, kind, data)"
                f" VALUES ('t', '{encargo}', '{nuevo}', '{{}}')")
    return (f'if evento["kind"] == {kind!r}: __import__("sqlite3").connect('
            f'{str(registro)!r}, isolation_level=None).execute({insertar!r})')


def _ejecutar_sin_utf8(*args: str | Path, herramienta: Path) -> subprocess.CompletedProcess[bytes]:
    """Como una tubería de Windows, que escribe en cp1252 si nadie dice otra cosa.

    `PYTHONIOENCODING` alcanza al proceso que se lanza; el hijo de `comparar`
    corre con `-I` y lo ignora, pero sigue a `LC_ALL` donde ese locale existe.
    """
    return subprocess.run(
        [sys.executable, str(herramienta), *map(str, args)],
        capture_output=True, check=False,
        env={**os.environ, "PYTHONIOENCODING": "cp1252", "LC_ALL": "en_US.ISO8859-1"},
    )


_FUERA_DE_CP1252 = "≤ ✅"
_ROMPE_CON_EL_TEXTO = 'if evento["kind"] == "cierre": raise ValueError(evento["data"]["texto"])'


def _correr(*args: str | Path, herramienta: Path = HERRAMIENTA,
            env: dict[str, str] | None = None) -> tuple[int, dict]:
    hecho = _ejecutar(*args, herramienta=herramienta, env=env)
    try:
        return hecho.returncode, json.loads(hecho.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"salida no JSON ({hecho.returncode}):\n{hecho.stdout}\n{hecho.stderr}") from None


class Comprobar(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.registro = self.dir / "registro.sqlite"

    def test_un_registro_que_pliega_completo_sale_con_cero(self):
        _armar(self.registro, {
            "enc_a": [_ENTREGA, _FASES_VACIAS, *_intento("hostA")],
            "enc_b": [("entrega", {**_ENTREGA[1], "id": "enc_b"})],
        })
        codigo, salida = _correr("comprobar", "--json", self.registro)
        self.assertEqual(codigo, 0)
        (reg,) = salida["registros"]
        self.assertEqual((reg["encargos"], reg["pliegan"], reg["fallos"]), (2, 2, []))

    def test_un_evento_que_el_reductor_rechaza_sale_con_uno_y_se_nombra(self):
        _armar(self.registro, {
            "enc_a": [_ENTREGA, ("orden", {"accion": "inventada"}), *_intento("hostA")],
            "enc_b": [("entrega", {**_ENTREGA[1], "id": "enc_b"})],
        })
        codigo, salida = _correr("comprobar", "--json", self.registro)
        self.assertEqual(codigo, 1)
        (reg,) = salida["registros"]
        self.assertEqual((reg["encargos"], reg["pliegan"]), (2, 1))
        (fallo,) = reg["fallos"]
        self.assertEqual((fallo["encargo"], fallo["evento"], fallo["tipo"]), ("enc_a", 2, "orden"))
        self.assertIn("TransicionIlegal", fallo["error"])

    def test_un_fallo_interno_que_guardo_la_version_siguiente_pliega(self):
        fallo = ("fallo_interno", {"rechazo": "ilegal", "kind": "estrategia",
                                   "ukey": "estrategia:*", "hora": "t2"})
        _armar(self.registro, {
            "quieto": [_ENTREGA, fallo],
            "vivo": [_ENTREGA, *_intento("hostA"), fallo,
                     ("arbol", {"observacion": "ausente", "hora": "t3"})],
        })
        codigo, salida = _correr("comprobar", "--json", self.registro)
        (reg,) = salida["registros"]
        self.assertEqual((codigo, reg["pliegan"], reg["fallos"], reg["violaciones"]), (0, 2, [], []))

    def test_un_evento_sin_host_viola_la_invariante_de_nombre_de_maquina(self):
        _armar(self.registro, {"sin_host": [_ENTREGA, *_intento(None)],
                               "con_host": [_ENTREGA, *_intento("hostA")]})
        codigo, salida = _correr("comprobar", "--json", self.registro)
        self.assertEqual(codigo, 0)
        (reg,) = salida["registros"]
        violadas = [(v["invariante"], v["encargo"], v["evento"], v["tipo"]) for v in reg["violaciones"]]
        self.assertEqual(violadas, [("mismo_estado_con_otro_host", "sin_host", 2, "reserva")])

    def test_un_cancelado_que_vuelve_a_deteniendo_viola_la_cancelacion_definitiva(self):
        _armar(self.registro, {
            "revive": [_ENTREGA, _CANCELAR, ("arbol", {"observacion": "ausente"}), _CANCELAR],
            "queda": [_ENTREGA, _CANCELAR, *[("arbol", {"observacion": "ausente"})] * 2],
        })
        codigo, salida = _correr("comprobar", "--json", self.registro)
        self.assertEqual(codigo, 0)
        (reg,) = salida["registros"]
        violadas = [(v["invariante"], v["encargo"], v["evento"]) for v in reg["violaciones"]
                    if v["invariante"] == "cancelacion_definitiva"]
        self.assertEqual(violadas, [("cancelacion_definitiva", "revive", 4)])

    def test_una_fila_con_json_invalido_sale_con_dos_y_nombra_el_evento(self):
        _armar(self.registro, {"enc_a": [_ENTREGA, ("orden", "{no es json")]})
        for opciones in ((), ("--json",)):
            with self.subTest(opciones=opciones):
                hecho = _ejecutar("comprobar", *opciones, self.registro)
                self.assertEqual(hecho.returncode, 2, hecho.stderr)
                self.assertEqual(hecho.stdout, "")
                self.assertIn("evento 2", hecho.stderr)

    @_SIN_PERMISOS_POSIX
    def test_un_registro_que_no_se_puede_abrir_sale_con_dos_y_lo_nombra(self):
        _sin_lectura(self.registro)
        for opciones in ((), ("--json",)):
            with self.subTest(opciones=opciones):
                hecho = _ejecutar("comprobar", *opciones, self.registro)
                self.assertEqual(hecho.returncode, 2, hecho.stderr)
                self.assertEqual(hecho.stdout, "")
                self.assertIn(str(self.registro), hecho.stderr)

    def test_un_evento_anidado_mas_alla_de_lo_que_json_decodifica_sale_con_dos(self):
        _armar(self.registro, {"enc_a": [("entrega", "[" * 200_000 + "]" * 200_000)]})
        hecho = _ejecutar("comprobar", "--json", self.registro)
        self.assertEqual(hecho.returncode, 2, hecho.stderr)
        self.assertEqual(hecho.stdout, "")
        self.assertIn("evento 1", hecho.stderr)

    def test_el_texto_de_un_registro_sano_dice_cuantos_pliegan_y_cada_invariante(self):
        _armar(self.registro, {"enc_a": [_ENTREGA, *_intento("hostA")]})
        hecho = _ejecutar("comprobar", self.registro)
        self.assertEqual(hecho.returncode, 0, hecho.stderr)
        self.assertEqual(hecho.stdout.splitlines(), [
            str(self.registro),
            "  Leí 1 encargos; pliegan 1.",
            "  Invariantes, revisadas en cada prefijo (no cambian la salida):",
            "    cancelacion_definitiva: sin violaciones.",
            "    mismo_estado_al_replegar: sin violaciones.",
            "    mismo_estado_con_otro_host: sin violaciones.",
        ])


class _RepoFalso(unittest.TestCase):
    """Un repo propio con la herramienta y un `motor/encargos/dominio.py` que escribe la prueba."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.repo = self.dir / "repo"
        (self.repo / "tools").mkdir(parents=True)
        (self.repo / "motor" / "encargos").mkdir(parents=True)
        for paquete in ("motor", "motor/encargos"):
            (self.repo / paquete / "__init__.py").touch()
        shutil.copy(HERRAMIENTA, self.repo / "tools" / "plegado.py")
        self.herramienta = self.repo / "tools" / "plegado.py"
        self.registro = self.dir / "registro.sqlite"

    def _escribir_dominio(self, texto: str) -> None:
        (self.repo / "motor" / "encargos" / "dominio.py").write_text(texto)

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=prueba", "-c", "user.email=prueba@example.invalid", *args],
            cwd=self.repo, check=True, capture_output=True,
        )


# Cada pasada completa (la empieza `estado is None`) toma el valor siguiente de
# `valores`: el plegado, el repliegue y el de otro host ven valores alternos.
_DOMINIO_QUE_ALTERNA = """
from dataclasses import dataclass

ESTADOS = ("recibido",)
_pasadas = [0]


@dataclass(frozen=True)
class Encargo:
    estado: str
    orden_parada: str
    escrituras: object


def reducir(estado, evento, nuevo=True):
    if estado is None:
        _pasadas[0] += 1
    valores = {valores!r}
    return Encargo("recibido", "", valores[_pasadas[0] % 2])
"""


class ComprobarConReductorFalso(_RepoFalso):
    def _violaciones(self, valores: tuple[object, object]) -> list[tuple[str, str, int, str]]:
        self._escribir_dominio(_DOMINIO_QUE_ALTERNA.format(valores=valores))
        codigo, salida = _correr("comprobar", "--json", self.registro, herramienta=self.herramienta)
        self.assertEqual(codigo, 0)
        return [(v["invariante"], v["encargo"], v["evento"], v["detalle"])
                for v in salida["registros"][0]["violaciones"]]

    def test_un_reductor_que_cambia_entre_pasadas_viola_el_mismo_estado_al_replegar(self):
        _armar(self.registro, {"enc_a": [("entrega", {}), ("cierre", {})]})
        self.assertEqual(self._violaciones((0, 0)), [])
        self.assertEqual(self._violaciones((0, False)),
                         [("mismo_estado_al_replegar", "enc_a", 1, "difieren escrituras")])

    def test_un_evento_que_llega_mientras_comprueba_queda_para_la_siguiente(self):
        """Como un encargo activo que escribe mientras se comprueba el registro.

        Plegar el avance de `enc_a` agrega a `enc_b` un cierre que el reductor no
        sabe plegar, antes de que se lean los eventos de `enc_b`.
        """
        _armar(self.registro, {"enc_a": [("entrega", {}), ("avance", {})],
                               "enc_b": [("entrega", {})]})
        self._escribir_dominio(_DOMINIO_FALSO.format(
            final="fuera de ESTADOS", hallazgos=(), encargo='    orden_parada: str = ""', hallazgo="",
            rompe=_insertar_al_plegar(self.registro, "avance", "enc_b", "cierre")))
        codigo, salida = _correr("comprobar", "--json", self.registro, herramienta=self.herramienta)
        db = sqlite3.connect(self.registro)
        self.addCleanup(db.close)
        (escritos,) = db.execute("SELECT count(*) FROM events WHERE encargo = 'enc_b'").fetchone()
        self.assertGreater(escritos, 1, "el evento llegó al registro durante la comprobación")
        (reg,) = salida["registros"]
        self.assertEqual((codigo, reg["pliegan"], reg["fallos"]), (0, 2, []))


_DOMINIO_FALSO = """
from dataclasses import dataclass

ESTADOS = ("recibido", "terminado")


@dataclass(frozen=True)
class Hallazgo:
    id: str
{hallazgo}

@dataclass(frozen=True)
class Encargo:
    estado: str
    hallazgos: tuple = ()
{encargo}

def reducir(estado, evento, nuevo=True):
    {rompe}
    final = {final!r} if evento["kind"] == "cierre" else "recibido"
    return Encargo(final, hallazgos=tuple(Hallazgo(i) for i in {hallazgos!r}))
"""


class Comparar(_RepoFalso):
    """`comparar` con dos versiones de un reductor mínimo: la de HEAD y la del árbol."""

    def setUp(self) -> None:
        super().setUp()
        self._git("init", "-q")
        self._base()
        _armar(self.registro, {"enc_a": [("entrega", {}), ("cierre", {})],
                               "enc_b": [("entrega", {})]})

    def _dominio(self, final: str = "recibido", hallazgos: tuple[str, ...] = ("h1",),
                 encargo: str = "", hallazgo: str = "", rompe: str = "pass") -> None:
        self._escribir_dominio(_DOMINIO_FALSO.format(
            final=final, hallazgos=hallazgos, rompe=rompe,
            encargo=f"    {encargo}" if encargo else "",
            hallazgo=f"    {hallazgo}" if hallazgo else ""))

    def _base(self, encargo: str = "", rompe: str = "pass", final: str = "recibido") -> None:
        self._dominio(encargo=encargo, rompe=rompe, final=final)
        self._git("add", ".")
        self._git("commit", "-q", "--allow-empty", "-m", "base")

    def _comparar(self, *opciones: str) -> tuple[int, dict]:
        return _correr("comparar", "--base", "HEAD", "--json", *opciones, self.registro,
                       herramienta=self.herramienta)

    def test_el_mismo_reductor_en_las_dos_refs_no_difiere(self):  # no-roja-en-base: control; muestra que comparar no reporta por defecto
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 0)
        self.assertEqual(salida["registros"][0]["diferencias"], [])

    def test_un_reductor_que_cambia_el_estado_final_se_reporta(self):
        self._dominio(final="terminado")
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 1)
        (reg,) = salida["registros"]
        self.assertEqual(reg["diferencias"], [{"encargo": "enc_a", "campos": ["estado"], "detalle": ""}])

    def _cambio_de_tipo(self, antes: str, despues: str) -> None:
        self._base(encargo=f"escrituras: object = {antes}")
        self._dominio(encargo=f"escrituras: object = {despues}")
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 1)
        (reg,) = salida["registros"]
        self.assertEqual([(d["encargo"], d["campos"]) for d in reg["diferencias"]],
                         [("enc_a", ["escrituras"]), ("enc_b", ["escrituras"])])
        self.assertEqual(reg["forma"], [])

    def test_un_cero_que_pasa_a_false_es_diferencia_de_valor(self):
        self._cambio_de_tipo("0", "False")

    def test_un_uno_que_pasa_a_true_es_diferencia_de_valor(self):
        self._cambio_de_tipo("1", "True")

    def test_un_entero_que_pasa_a_flotante_es_diferencia_de_valor(self):
        self._cambio_de_tipo("1", "1.0")

    def test_un_campo_que_solo_existe_en_el_arbol_es_forma_y_sale_con_cero(self):
        self._dominio(encargo='prioridad: str = "alta"')
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 0)
        (reg,) = salida["registros"]
        self.assertEqual(reg["diferencias"], [])
        self.assertEqual(reg["forma"], [{"ruta": "prioridad", "solo_en": "arbol", "encargos": 2}])

    def test_un_campo_que_el_arbol_quita_es_forma_del_lado_de_la_base(self):
        self._base(encargo='vieja: int = 0')
        self._dominio()
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 0)
        self.assertEqual(salida["registros"][0]["forma"],
                         [{"ruta": "vieja", "solo_en": "base", "encargos": 2}])

    def test_una_clave_nueva_dentro_de_un_objeto_de_una_lista_es_forma_con_su_ruta(self):
        self._dominio(hallazgo='severidad: str = "media"')
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 0)
        (reg,) = salida["registros"]
        self.assertEqual(reg["diferencias"], [])
        self.assertEqual(reg["forma"], [{"ruta": "hallazgos[].severidad", "solo_en": "arbol", "encargos": 2}])

    def test_un_valor_anidado_distinto_sale_con_uno_aunque_tambien_cambie_la_forma(self):
        self._dominio(hallazgos=("otro",), encargo='prioridad: str = "alta"')
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 1)
        (reg,) = salida["registros"]
        self.assertEqual([(d["encargo"], d["campos"]) for d in reg["diferencias"]],
                         [("enc_a", ["hallazgos[].id"]), ("enc_b", ["hallazgos[].id"])])
        self.assertEqual([f["ruta"] for f in reg["forma"]], ["prioridad"])

    def test_una_lista_que_cambia_de_largo_es_diferencia_de_valor(self):
        self._dominio(hallazgos=("h1", "h2"))
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 1)
        (reg,) = salida["registros"]
        self.assertEqual([d["campos"] for d in reg["diferencias"]], [["hallazgos"], ["hallazgos"]])
        self.assertEqual(reg["forma"], [])

    def test_la_opcion_estricta_vuelve_fallo_una_diferencia_de_forma(self):
        self._dominio(encargo='prioridad: str = "alta"')
        codigo, salida = self._comparar("--estricto")
        self.assertEqual(codigo, 1)
        self.assertFalse(salida["ok"])
        (reg,) = salida["registros"]
        self.assertEqual((reg["diferencias"], [f["ruta"] for f in reg["forma"]]), ([], ["prioridad"]))

    def test_el_mismo_fallo_con_otro_mensaje_no_es_diferencia_y_se_dice_que_no_pliega(self):
        self._base(rompe='if evento["kind"] == "cierre": raise ValueError("texto viejo")')
        self._dominio(rompe='if evento["kind"] == "cierre": raise ValueError("texto nuevo")')
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 0)
        (reg,) = salida["registros"]
        self.assertEqual(reg["diferencias"], [])
        self.assertEqual([(f["encargo"], f["evento"], f["excepcion"]) for f in reg["no_pliegan"]],
                         [("enc_a", 2, "ValueError")])
        hecho = _ejecutar("comparar", "--base", "HEAD", self.registro, herramienta=self.herramienta)
        self.assertEqual(hecho.returncode, 0, hecho.stderr)
        self.assertIn("  No pliegan en ningún lado (no cuentan como diferencia): 1 encargos.",
                      hecho.stdout.splitlines())

    def test_otra_excepcion_en_el_mismo_evento_es_diferencia_de_plegado(self):
        self._base(rompe='if evento["kind"] == "cierre": raise ValueError("x")')
        self._dominio(rompe='if evento["kind"] == "cierre": raise KeyError("x")')
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 1)
        (reg,) = salida["registros"]
        self.assertEqual([(d["encargo"], d["campos"]) for d in reg["diferencias"]],
                         [("enc_a", ["plegado"])])
        self.assertEqual(reg["no_pliegan"], [])

    def test_un_evento_que_llega_entre_los_dos_plegados_no_es_diferencia(self):
        """Como un encargo activo que escribe mientras la compuerta compara.

        El reductor de la base corre en su proceso y, al plegar el cierre, agrega un
        evento al registro: llega después de que leyó la base y antes de que lea el árbol.
        """
        escribe = _insertar_al_plegar(self.registro, "cierre", "enc_a", "entrega")
        self._base(final="terminado", rompe=escribe)
        self._dominio(final="terminado")
        codigo, salida = self._comparar()
        db = sqlite3.connect(self.registro)
        self.addCleanup(db.close)
        (escritos,) = db.execute("SELECT count(*) FROM events WHERE encargo = 'enc_a'").fetchone()
        self.assertEqual(escritos, 3, "el evento llegó al registro durante la comparación")
        self.assertEqual((codigo, salida["registros"][0]["diferencias"]), (0, []))

    @unittest.skipIf(os.name == "nt", "el git falso es un guion de shell")
    def test_un_evento_que_llega_antes_de_que_lea_la_base_tampoco_es_diferencia(self):  # no-roja-en-base: la base no corta en ningún lado y los dos leen este evento; la prueba fija el corte que recibe el proceso de la base
        """El evento llega después de leer el último id y antes de que el proceso de la base lea.

        `comparar` saca el reductor de la base con `git archive`; un `git` falso
        en el PATH agrega el cierre de `enc_b` justo antes de sacarlo.
        """
        self._base(final="terminado")
        self._dominio(final="terminado")
        inserta = self.dir / "inserta.py"
        inserta.write_text(
            "import sqlite3\n"
            f"sqlite3.connect({str(self.registro)!r}, isolation_level=None).execute(\n"
            "    \"INSERT INTO events (ts, encargo, kind, data) VALUES ('t', 'enc_b', 'cierre', '{}')\")\n")
        falsos = self.dir / "bin"
        falsos.mkdir()
        git = falsos / "git"
        git.write_text(
            "#!/bin/sh\n"
            'for arg in "$@"; do\n'
            f'  [ "$arg" = archive ] && "{sys.executable}" "{inserta}"\n'
            "done\n"
            f'exec "{shutil.which("git")}" "$@"\n')
        git.chmod(0o755)
        entorno = {**os.environ, "PATH": f"{falsos}{os.pathsep}{os.environ['PATH']}"}
        codigo, salida = _correr("comparar", "--base", "HEAD", "--json", self.registro,
                                 herramienta=self.herramienta, env=entorno)
        db = sqlite3.connect(self.registro)
        self.addCleanup(db.close)
        (escritos,) = db.execute("SELECT count(*) FROM events WHERE encargo = 'enc_b'").fetchone()
        self.assertEqual(escritos, 2, "el evento llegó al registro durante la comparación")
        self.assertEqual((codigo, salida["registros"][0]["diferencias"]), (0, []))

    def test_una_fila_con_json_invalido_sale_con_dos(self):
        _armar(self.dir / "roto.sqlite", {"enc_a": [("entrega", "{no es json")]})
        hecho = _ejecutar("comparar", "--base", "HEAD", self.dir / "roto.sqlite",
                          herramienta=self.herramienta)
        self.assertEqual(hecho.returncode, 2, hecho.stderr)
        self.assertEqual(hecho.stdout, "")

    @_SIN_PERMISOS_POSIX
    def test_un_registro_que_no_se_puede_abrir_sale_con_dos_y_lo_nombra(self):
        cerrado = _sin_lectura(self.dir / "cerrado.sqlite")
        hecho = _ejecutar("comparar", "--base", "HEAD", cerrado, herramienta=self.herramienta)
        self.assertEqual(hecho.returncode, 2, hecho.stderr)
        self.assertEqual(hecho.stdout, "")
        self.assertIn(str(cerrado), hecho.stderr)

    def test_el_texto_sin_diferencias_nombra_la_ref_y_cuantos_encargos_compara(self):
        hecho = _ejecutar("comparar", "--base", "HEAD", self.registro, herramienta=self.herramienta)
        self.assertEqual(hecho.returncode, 0, hecho.stderr)
        (cabeza, linea) = hecho.stdout.splitlines()
        self.assertEqual(cabeza, str(self.registro))
        self.assertRegex(linea, r"^  Comparé 2 encargos con el reductor de HEAD \([0-9a-f]{12}\): sin diferencias\.$")

    def _registro_con_texto(self) -> Path:
        registro = self.dir / "texto.sqlite"
        _armar(registro, {"enc_a": [("entrega", {}), ("cierre", {"texto": _FUERA_DE_CP1252})]})
        return registro

    def test_plegar_entrega_su_documento_aunque_la_consola_no_sea_utf8(self):
        """`plegar` es la orden que `comparar` lanza con el reductor de la base."""
        self._dominio(rompe=_ROMPE_CON_EL_TEXTO)
        registro = self._registro_con_texto()
        hecho = _ejecutar_sin_utf8("plegar", "--raiz", self.repo, registro, herramienta=self.herramienta)
        self.assertEqual(hecho.returncode, 0, hecho.stderr.decode("utf-8", "replace"))
        (plegado,) = json.loads(hecho.stdout)[str(registro)]
        self.assertEqual(plegado["fallo"]["error"], f"ValueError: {_FUERA_DE_CP1252}")

    def test_comparar_en_una_consola_sin_utf8_muestra_el_texto_del_registro(self):
        self._base(rompe=_ROMPE_CON_EL_TEXTO)
        self._dominio(rompe=_ROMPE_CON_EL_TEXTO)
        registro = self._registro_con_texto()
        como_json = _ejecutar_sin_utf8("comparar", "--base", "HEAD", "--json", registro,
                                       herramienta=self.herramienta)
        self.assertEqual(como_json.returncode, 0, como_json.stderr.decode("utf-8", "replace"))
        (reg,) = json.loads(como_json.stdout)["registros"]
        self.assertEqual([f["error"] for f in reg["no_pliegan"]], [f"ValueError: {_FUERA_DE_CP1252}"])
        texto = _ejecutar_sin_utf8("comparar", "--base", "HEAD", registro, herramienta=self.herramienta)
        self.assertEqual(texto.returncode, 0, texto.stderr.decode("utf-8", "replace"))
        self.assertIn(f"    enc_a: evento 2 (cierre): ValueError: {_FUERA_DE_CP1252}",
                      texto.stdout.decode("utf-8").splitlines())


def _corpus_sin_entrega(raiz: Path) -> Path:
    corpus = raiz / "corpus"
    (corpus / "registros").mkdir(parents=True)
    (corpus / "rechazos.jsonl").write_text("", encoding="utf-8")
    ruta = corpus / "registros" / "uno.sqlite"
    with sqlite3.connect(ruta) as db:
        db.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
            " encargo TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
        )
        db.execute(
            "INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?)",
            ("", "sin_entrega", "recibo", "{}", "recibo:sin_entrega"),
        )
    return corpus


class CorpusPlegado(unittest.TestCase):
    def test_verificar_cobertura_muestra_lineas_no_ejecutadas(self) -> None:
        from tools.corpus_plegado import _referencia

        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            corpus = raiz / "corpus"
            (corpus / "registros").mkdir(parents=True)
            (corpus / "rechazos.jsonl").write_text("", encoding="utf-8")
            registro = Registro(raiz / "origen")
            registro.append_many("enc_a", (Evento("entrega", _ENTREGA[1], "entrega:enc_a"),))
            registro.close()
            shutil.copy2(raiz / "origen" / "registro.sqlite", corpus / "registros" / "uno.sqlite")
            _referencia(corpus, RAIZ, "prueba")
            meta = json.loads((corpus / "meta.json").read_text(encoding="utf-8"))
            (corpus / "meta.json").write_text(
                json.dumps({**meta, "pytest_exit": 0, "pytest_fallos": []}), encoding="utf-8",
            )
            resultado = _ejecutar(
                "verificar", corpus, "--cobertura",
                herramienta=RAIZ / "tools" / "corpus_plegado.py",
            )
            self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
            fuente = (RAIZ / "motor" / "encargos" / "dominio.py").read_text(encoding="utf-8")
            linea = next(i for i, texto in enumerate(fuente.splitlines(), 1)
                         if 'raise TransicionIlegal(f"evento desconocido: {kind}")' in texto)
            self.assertIn(f"motor/encargos/dominio.py:{linea}:", resultado.stdout)
            self.assertIn("estado igual en 1 de 1", resultado.stdout)

    def test_tabla_de_pedido_y_ordenes_tiene_filas_aplicables(self) -> None:
        from motor.encargos.reductor import TABLA

        esperados = {
            "recibo", "ruta_agentes", "contexto_entrega", "proveedor_fallo",
            "proveedor_disponible", "falla_sesion_local", "orden", "ajuste",
            "procedimiento", "migracion",
        }
        self.assertTrue(esperados <= TABLA.keys())
        self.assertTrue(all(callable(TABLA[kind].aplicar) for kind in esperados))

    def test_referencia_conserva_los_descartados_tras_la_semilla(self) -> None:
        from tools.corpus_plegado import _descartar, _referencia

        with tempfile.TemporaryDirectory() as temporal:
            corpus = _corpus_sin_entrega(Path(temporal))
            _, descartados, _, _ = _referencia(corpus, RAIZ, "prueba")
            self.assertEqual(descartados, 1)
            with (corpus / "meta.json").open(encoding="utf-8") as archivo:
                original = json.load(archivo)["descartados_detalle"]
            self.assertEqual(original[0]["encargo"], "sin_entrega")
            self.assertIn("no hay encargo", original[0]["fallo"]["error"])
            _descartar(corpus)
            _referencia(corpus, RAIZ, "prueba", descartados)
            with (corpus / "meta.json").open(encoding="utf-8") as archivo:
                self.assertEqual(json.load(archivo)["descartados_detalle"], original)

    def test_verificar_detecta_descartado_que_empieza_a_plegar(self) -> None:
        from tools.corpus_plegado import _descartar
        from tools.plegado import leer_encargos, ultimo_evento

        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            corpus = _corpus_sin_entrega(raiz)
            registro = corpus / "registros" / "uno.sqlite"
            secuencia = next(leer_encargos(registro, ultimo_evento(registro)))[1]
            (corpus / "estado-base").mkdir()
            (corpus / "estado-base" / "uno.json").write_text("{}", encoding="utf-8")
            _descartar(corpus)
            (corpus / "meta.json").write_text(json.dumps({
                "ref": "prueba", "archivos": 1, "encargos": 0, "descartados": 1,
                "eventos": 0, "rechazos": 0, "pytest_exit": 0, "pytest_fallos": [],
                "descartados_detalle": [{
                    "archivo": "uno.sqlite", "encargo": "sin_entrega", "eventos": secuencia,
                    "fallo": {"evento": secuencia[0]["id"], "tipo": "recibo",
                              "excepcion": "TransicionIlegal",
                              "error": "TransicionIlegal: no hay encargo"},
                }],
            }), encoding="utf-8")

            def verificar(arbol: Path) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(RAIZ / "tools" / "corpus_plegado.py"),
                     "verificar", str(corpus), "--raiz", str(arbol)],
                    capture_output=True, text=True, check=False,
                )

            sano = verificar(RAIZ)
            self.assertEqual(sano.returncode, 0, sano.stdout + sano.stderr)
            ruta_meta = corpus / "meta.json"
            meta_sana = ruta_meta.read_text(encoding="utf-8")
            meta_incompleta = json.loads(meta_sana)
            meta_incompleta["descartados"] = 2
            ruta_meta.write_text(json.dumps(meta_incompleta), encoding="utf-8")
            descuadre = verificar(RAIZ)
            self.assertNotEqual(descuadre.returncode, 0, descuadre.stdout)
            ruta_meta.write_text(meta_sana, encoding="utf-8")
            copia = raiz / "copia"
            shutil.copytree(RAIZ / "motor", copia / "motor")
            dominio = copia / "motor" / "encargos" / "dominio.py"
            texto = dominio.read_text(encoding="utf-8")
            original = '        raise TransicionIlegal("no hay encargo")'
            mutado = ('        return _reducir_evento(None, {"kind": "entrega", "data": '
                      '{"id": "x", "clave": "x", "huella": "h", "texto": "x"}}, nuevo)')
            self.assertIn(original, texto)
            dominio.write_text(texto.replace(original, mutado, 1), encoding="utf-8")
            resultado = verificar(copia)
            self.assertNotEqual(resultado.returncode, 0, resultado.stdout)
            self.assertIn("sin_entrega", resultado.stdout)
            dominio.write_text(
                texto.replace(original, '        raise TransicionIlegal("otro error")', 1),
                encoding="utf-8",
            )
            otro_error = verificar(copia)
            self.assertNotEqual(otro_error.returncode, 0, otro_error.stdout)
            self.assertIn("sin_entrega", otro_error.stdout)

    def test_generar_limpia_un_worktree_de_corpus_sin_carpeta(self) -> None:
        from tools import corpus_plegado

        with (tempfile.TemporaryDirectory(prefix="corpus-ref-") as temporal,
              tempfile.TemporaryDirectory(prefix="otro-ref-") as ajeno,
              tempfile.TemporaryDirectory(prefix="corpus-ref-") as nombre_alterno,
              tempfile.TemporaryDirectory(prefix="corpus-ref-") as activo):
            arbol = Path(temporal) / "arbol"
            ajenos = (Path(ajeno) / "arbol", Path(nombre_alterno) / "otro")
            vivo = Path(activo) / "arbol"
            for ruta in (arbol, *ajenos, vivo):
                subprocess.run(
                    ["git", "-C", str(RAIZ), "worktree", "add", "--detach", str(ruta), "HEAD"],
                    check=True, capture_output=True,
                )
            for ruta in (arbol, *ajenos):
                shutil.rmtree(ruta)
            try:
                registrados = subprocess.check_output(
                    ["git", "-C", str(RAIZ), "worktree", "list", "--porcelain"], text=True,
                )
                for ruta in (arbol, *ajenos, vivo):
                    self.assertIn(str(ruta), registrados)
                with (mock.patch.object(corpus_plegado.subprocess, "check_output",
                                        side_effect=RuntimeError("fin del ensayo")),
                      mock.patch.object(corpus_plegado.tempfile, "gettempdir",
                                        return_value=str(Path(temporal).parent / "otro-temporal"))):
                    with self.assertRaisesRegex(RuntimeError, "fin del ensayo"):
                        corpus_plegado.generar("origin/main", Path(temporal) / "corpus")
                registrados = subprocess.run(
                    ["git", "-C", str(RAIZ), "worktree", "list", "--porcelain"],
                    capture_output=True, text=True, check=True,
                ).stdout
                self.assertNotIn(str(arbol), registrados)
                self.assertIn(str(RAIZ), registrados)
                for ruta in (*ajenos, vivo):
                    self.assertIn(str(ruta), registrados)
            finally:
                for ruta in (arbol, *ajenos, vivo):
                    subprocess.run(
                        ["git", "-C", str(RAIZ), "worktree", "remove", "--force", str(ruta)],
                        check=False, capture_output=True,
                    )

    def test_generar_reintenta_solo_el_fallo_ajeno_y_anota_el_resultado(self) -> None:
        from tools import corpus_plegado

        ajeno = "tests/test_canario_lanzador.py::LanzadorEnUnaMaquinaDePrueba::test_repetir_la_corrida"
        with tempfile.TemporaryDirectory() as temporal:
            (Path(temporal) / "hijo").mkdir()
            alias = str(Path(temporal) / "hijo" / "..")
            for falla_reintento in (False, True):
                with self.subTest(falla_reintento=falla_reintento):
                    vistos = []

                    def correr(args, *, falla_reintento=falla_reintento, vistos=vistos, **opciones):
                        if args[3:5] == ["worktree", "list"]:
                            return subprocess.CompletedProcess(args, 0, "", "")
                        if args[3:5] == ["worktree", "add"]:
                            (Path(args[-2]) / "tools").mkdir(parents=True)
                            return subprocess.CompletedProcess(args, 0, "", "")
                        if args[3:5] == ["worktree", "remove"]:
                            return subprocess.CompletedProcess(args, 0, "", "")
                        if args[1:3] == ["-m", "pytest"]:
                            vistos.append((args, opciones["env"]))
                            if "-p" in args:
                                return subprocess.CompletedProcess(
                                    args, 1, f"FAILED {corpus_plegado.FALLO_BASE}\n"
                                    f"FAILED {ajeno}\n2 failed, 2800 passed", "",
                                )
                            return subprocess.CompletedProcess(
                                args, int(falla_reintento),
                                f"FAILED {ajeno}\n1 failed" if falla_reintento else "1 passed", "",
                            )
                        raise AssertionError(args)

                    def referencia(salida, raiz, ref, descartados=0):
                        corpus_plegado._json(salida / "meta.json", {
                            "ref": ref, "archivos": 1, "encargos": 1000,
                            "descartados": descartados, "eventos": 12000,
                            "rechazos": 0, "kinds": ["entrega"],
                        })
                        return 1000, descartados, 12000, {"entrega"}

                    destino = Path(temporal) / f"corpus-{int(falla_reintento)}"
                    with (mock.patch.object(corpus_plegado.subprocess, "check_output",
                                            return_value="base\n"),
                          mock.patch.object(corpus_plegado.subprocess, "run", side_effect=correr),
                          mock.patch.object(corpus_plegado, "_referencia",
                                            side_effect=referencia),
                          mock.patch.object(corpus_plegado, "_descartar"),
                          mock.patch.object(corpus_plegado, "_kinds", return_value={"entrega"}),
                          mock.patch.object(corpus_plegado.tempfile, "gettempdir",
                                            return_value=alias),
                          mock.patch.dict(os.environ, {"TMPDIR": alias})):
                        if falla_reintento:
                            with self.assertRaises(RuntimeError):
                                corpus_plegado.generar("origin/main", destino)
                        else:
                            self.assertEqual(corpus_plegado.generar("origin/main", destino), 0)
                            meta = json.loads((destino / "meta.json").read_text(encoding="utf-8"))
                            self.assertEqual(meta["pytest_fallos"], [corpus_plegado.FALLO_BASE])
                            self.assertEqual(meta["pytest_reintentos"], [ajeno])
                    self.assertEqual(len(vistos), 2)
                    self.assertEqual(vistos[1][0][-1], ajeno)
                    self.assertNotIn("-p", vistos[1][0])
                    self.assertNotIn("MOTOR_CORPUS_DIR", vistos[1][1])
                    self.assertEqual(vistos[0][1]["TMPDIR"], str(Path(temporal).resolve()))
                    self.assertEqual(vistos[1][1]["TMPDIR"], str(Path(temporal).resolve()))

    def test_generar_frena_antes_de_descartar_y_sembrar_sin_cobertura_real(self) -> None:
        from tools import corpus_plegado

        with tempfile.TemporaryDirectory() as temporal:
            def correr(args, **_):
                if args[3:5] == ["worktree", "list"]:
                    return subprocess.CompletedProcess(args, 0, "", "")
                if args[3:5] == ["worktree", "add"]:
                    (Path(args[-2]) / "tools").mkdir(parents=True)
                    return subprocess.CompletedProcess(args, 0, "", "")
                if args[3:5] == ["worktree", "remove"]:
                    return subprocess.CompletedProcess(args, 0, "", "")
                if args[1:3] == ["-m", "pytest"]:
                    return subprocess.CompletedProcess(
                        args, 1,
                        f"FAILED {corpus_plegado.FALLO_BASE}\n1 failed, 2800 passed", "",
                    )
                raise AssertionError(f"no se debía sembrar: {args}")

            with (mock.patch.object(corpus_plegado.subprocess, "check_output",
                                    return_value="base\n"),
                  mock.patch.object(corpus_plegado.subprocess, "run",
                                    side_effect=correr) as ejecuciones,
                  mock.patch.object(corpus_plegado, "_referencia",
                                    return_value=(1, 0, 1, {"entrega"})),
                  mock.patch.object(corpus_plegado, "_descartar") as descartar):
                with self.assertRaisesRegex(RuntimeError, "corpus insuficiente antes de sembrar"):
                    corpus_plegado.generar("origin/main", Path(temporal) / "corpus")
                descartar.assert_not_called()
                self.assertEqual(ejecuciones.call_count, 4)

    def test_una_falla_ajena_de_pytest_invalida_el_corpus(self) -> None:
        from tools.corpus_plegado import _comprobar_cobertura, _fallos_pytest

        conocido = ("tests/test_rama_fase.py::UnFalloDeGitRepetidoPideAyuda"
                    "::test_un_ajuste_del_propietario_vuelve_a_intentarlo")
        self.assertEqual(_fallos_pytest(1, f"FAILED {conocido}\n1 failed, 2800 passed"),
                         [conocido])
        with self.assertRaises(RuntimeError):
            _fallos_pytest(1, "FAILED tests/test_otro.py::test_roto\n1 failed, 2800 passed")
        with self.assertRaises(RuntimeError):
            _fallos_pytest(1, "1 failed, 2800 passed")
        _comprobar_cobertura(900, 10000)
        with self.assertRaises(RuntimeError):
            _comprobar_cobertura(1, 10000)
        with self.assertRaises(RuntimeError):
            _comprobar_cobertura(900, 1)

    def test_el_plugin_inyecta_el_bootstrap_en_un_hijo_sin_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            arbol = raiz / "arbol"
            (arbol / "tools").mkdir(parents=True)
            (arbol / "tests").mkdir()
            shutil.copytree(RAIZ / "motor", arbol / "motor")
            shutil.copy2(RAIZ / "tools" / "corpus_pytest.py", arbol / "tools" / "corpus_pytest.py")
            shutil.copy2(RAIZ / "tools" / "corpus_sitecustomize.py", arbol / "sitecustomize.py")
            corpus = raiz / "corpus"
            (corpus / "registros").mkdir(parents=True)
            hijo = (
                "from pathlib import Path\n"
                "from motor.encargos.registro import Registro, Evento, ClaveOcupada\n"
                "import sys\n"
                "r = Registro(Path(sys.argv[1]))\n"
                "r.append_many('hijo', [Evento('entrega', "
                "{'id': 'hijo', 'clave': 'hijo', 'huella': 'h', 'texto': 'prueba'}, 'entrega:hijo')])\n"
                "try:\n"
                "    r.append_many('hijo', [Evento('procedimiento', {'nota': 'otra'}, 'entrega:hijo')])\n"
                "except ClaveOcupada:\n"
                "    pass\n"
            )
            (arbol / "tests" / "test_hijo.py").write_text(
                "import os, subprocess, sys\n\n"
                "def test_hijo():\n"
                f"    subprocess.run([sys.executable, '-c', {hijo!r}, "
                f"{str(raiz / 'origen')!r}], check=True)\n\n"
                "def test_entorno_vacio():\n"
                "    os.environ['MOTOR_CORPUS_SENAL'] = 'marcador'\n"
                "    resultado = subprocess.run([sys.executable, '-c', "
                "'import os; print(os.environ.get(\"MOTOR_CORPUS_SENAL\", \"\"), "
                "os.environ.get(\"PYTHONPATH\", \"\"), sep=\"|\")'], "
                "env={}, capture_output=True, text=True, check=True)\n"
                "    assert resultado.stdout.strip() == '|'\n",
                encoding="utf-8",
            )
            entorno = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            hecho = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "tools.corpus_pytest",
                 "tests/test_hijo.py"],
                cwd=arbol, env={**entorno, "MOTOR_CORPUS_DIR": str(corpus),
                                "MOTOR_CORPUS_REPO": str(arbol)},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(hecho.returncode, 0, hecho.stdout + hecho.stderr)
            archivos = list((corpus / "registros").glob("*.sqlite"))
            escritos = []
            for archivo in archivos:
                with sqlite3.connect(archivo) as db:
                    escritos.append(db.execute(
                        "SELECT COUNT(*) FROM events WHERE kind = 'entrega'"
                    ).fetchone()[0])
            self.assertIn(1, escritos)
            rechazos = (corpus / "rechazos.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rechazos), 1)

    def test_el_bootstrap_no_importa_el_motor_antes_del_hijo(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            (raiz / "registros").mkdir()
            shutil.copy2(RAIZ / "tools" / "corpus_sitecustomize.py", raiz / "sitecustomize.py")
            hecho = subprocess.run(
                [sys.executable, "-c", "import sys; print('motor' in sys.modules)"],
                cwd=RAIZ, capture_output=True, text=True, check=False,
                env={**os.environ, "MOTOR_CORPUS_DIR": str(raiz),
                     "MOTOR_CORPUS_REPO": str(RAIZ),
                     "PYTHONPATH": str(raiz) + os.pathsep + str(RAIZ)},
            )
            self.assertEqual(hecho.returncode, 0, hecho.stderr)
            self.assertEqual(hecho.stdout.strip(), "False")

    def test_captura_el_registro_y_el_rechazo_de_un_hijo_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            corpus = raiz / "corpus"
            (corpus / "registros").mkdir(parents=True)
            shutil.copy2(RAIZ / "tools" / "corpus_sitecustomize.py", raiz / "sitecustomize.py")
            hijo = (
                "from motor.encargos.cli import main\n"
                "from pathlib import Path\n"
                "import sys\n"
                "registro = sys.modules['motor.encargos.registro']\n"
                "r = registro.Registro(Path(sys.argv[1]))\n"
                "r.append_many('hijo', [registro.Evento('procedimiento', {'nota': 'uno'}, 'nota:hijo')])\n"
                "try:\n"
                "    r.append_many('hijo', [registro.Evento('procedimiento', {'nota': 'otro'}, 'nota:hijo')])\n"
                "except registro.ClaveOcupada:\n"
                "    pass\n"
                "r.close()\n"
            )
            padre = (
                "from pathlib import Path\n"
                "from motor.encargos.registro import Registro, Evento\n"
                "import subprocess, sys\n"
                "r = Registro(Path(sys.argv[1]))\n"
                "r.append_many('hijo', [Evento('entrega', "
                "{'id': 'hijo', 'clave': 'hijo', 'huella': 'h', 'texto': 'prueba'}, "
                "'entrega:hijo')])\n"
                "r.close()\n"
                f"subprocess.run([sys.executable, '-c', {hijo!r}, sys.argv[1]], check=True)\n"
            )
            hecho = subprocess.run(
                [sys.executable, "-c", padre, str(raiz / "origen")],
                cwd=RAIZ, capture_output=True, text=True, check=False,
                env={**os.environ, "MOTOR_CORPUS_DIR": str(corpus),
                     "MOTOR_CORPUS_REPO": str(RAIZ),
                     "PYTHONPATH": str(raiz) + os.pathsep + str(RAIZ)},
            )
            self.assertEqual(hecho.returncode, 0, hecho.stderr)
            archivos = list((corpus / "registros").glob("*.sqlite"))
            self.assertEqual(len(archivos), 2)
            historias = []
            for archivo in archivos:
                with sqlite3.connect(archivo) as db:
                    historias.append([kind for (kind,) in db.execute(
                        "SELECT kind FROM events ORDER BY id"
                    )])
            self.assertIn(["entrega", "procedimiento"], historias)
            rechazos = [json.loads(linea) for linea in
                        (corpus / "rechazos.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(r["tipo"], r["indice"], len(r["lote"]))
                              for r in rechazos], [("ClaveOcupada", 0, 1)])

    def test_verificar_detecta_un_cambio_invisible_en_la_vista(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            raiz = Path(temporal)
            corpus = raiz / "corpus"
            (corpus / "registros").mkdir(parents=True)
            (corpus / "estado-base").mkdir()
            registro = Registro(raiz / "origen")
            encargo = "corpus_prueba"
            registro.append_many(encargo, (
                Evento("entrega", {
                    "id": encargo, "clave": encargo, "huella": "h", "texto": "prueba",
                }, "entrega:corpus"),
                Evento("espera", {
                    "motivo": "git falló: prueba", "reanuda_en": "mañana",
                }, "espera:corpus"),
                Evento("plazo", {}, "plazo:corpus"),
            ))
            with self.assertRaises(ClaveOcupada) as rechazo:
                registro.append_many(encargo, (
                    Evento("procedimiento", {"nota": "nueva"}, "entrega:corpus"),
                ))
            estado = campos_de(registro.fold(encargo))
            self.assertEqual(estado["fallos_repo"], ["git falló", 1])
            registro.close()
            shutil.copy2(raiz / "origen" / "registro.sqlite", corpus / "registros" / "uno.sqlite")
            (corpus / "estado-base" / "uno.json").write_text(
                json.dumps({encargo: estado}), encoding="utf-8",
            )
            (corpus / "rechazos.jsonl").write_text(json.dumps({
                "archivo": "uno.sqlite", "encargo": encargo, "conteo": 3,
                "indice": rechazo.exception.indice, "tipo": "ClaveOcupada",
                "lote": [{"kind": "procedimiento", "data": {"nota": "nueva"},
                          "ukey": "entrega:corpus", "ts": ""}],
            }) + "\n", encoding="utf-8")
            (corpus / "meta.json").write_text(json.dumps({
                "ref": "prueba", "kinds": ["entrega", "espera", "plazo"],
                "pytest_exit": 1, "pytest_fallos": [
                    "tests/test_rama_fase.py::UnFalloDeGitRepetidoPideAyuda"
                    "::test_un_ajuste_del_propietario_vuelve_a_intentarlo",
                ],
                "archivos": 1, "encargos": 1, "eventos": 3, "rechazos": 1,
                "descartados": 0, "descartados_detalle": [],
            }), encoding="utf-8")

            def verificar(arbol: Path) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(RAIZ / "tools" / "corpus_plegado.py"),
                     "verificar", str(corpus), "--raiz", str(arbol)],
                    cwd=RAIZ, capture_output=True, text=True, check=False,
                )

            sano = verificar(RAIZ)
            self.assertEqual(sano.returncode, 0, sano.stderr + sano.stdout)
            self.assertIn("estado igual en 1 de 1", sano.stdout)
            ruta_meta = corpus / "meta.json"
            meta_sana = ruta_meta.read_text(encoding="utf-8")
            meta_sin_fallos = {**json.loads(meta_sana), "pytest_exit": 0, "pytest_fallos": []}
            ruta_meta.write_text(json.dumps(meta_sin_fallos), encoding="utf-8")
            self.assertEqual(verificar(RAIZ).returncode, 0)
            for campo in ("pytest_exit", "pytest_fallos", "archivos",
                          "encargos", "eventos", "rechazos", "descartados",
                          "descartados_detalle"):
                meta_incompleta = json.loads(meta_sana)
                del meta_incompleta[campo]
                ruta_meta.write_text(json.dumps(meta_incompleta), encoding="utf-8")
                incompleto = verificar(RAIZ)
                self.assertNotEqual(incompleto.returncode, 0, campo)
                self.assertIn("metadatos", incompleto.stdout)
            ruta_meta.write_text(meta_sana, encoding="utf-8")
            meta_rota = json.loads(meta_sana)
            meta_rota.update(pytest_exit=1, pytest_fallos=["tests/test_otro.py::test_roto"])
            ruta_meta.write_text(json.dumps(meta_rota), encoding="utf-8")
            self.assertNotEqual(verificar(RAIZ).returncode, 0)
            ruta_meta.write_text(meta_sana, encoding="utf-8")
            ruta_rechazos = corpus / "rechazos.jsonl"
            original = ruta_rechazos.read_text(encoding="utf-8")
            alterado = json.loads(original)
            alterado["indice"] += 1
            ruta_rechazos.write_text(json.dumps(alterado) + "\n", encoding="utf-8")
            mal_rechazo = verificar(RAIZ)
            self.assertNotEqual(mal_rechazo.returncode, 0, mal_rechazo.stdout)
            self.assertIn("cambió el rechazo", mal_rechazo.stdout)
            ruta_rechazos.write_text((json.dumps(alterado) + "\n") * 256, encoding="utf-8")
            muchos_rechazos = verificar(RAIZ)
            self.assertNotEqual(muchos_rechazos.returncode, 0, muchos_rechazos.stdout)
            self.assertEqual(muchos_rechazos.stdout.count("cambió el rechazo"), 256)
            ruta_rechazos.write_text(original, encoding="utf-8")
            registro_corpus = corpus / "registros" / "uno.sqlite"
            copia_sana = raiz / "uno-sano.sqlite"
            shutil.copy2(registro_corpus, copia_sana)
            with sqlite3.connect(corpus / "registros" / "uno.sqlite") as db:
                db.execute(
                    "INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?)",
                    ("", "sin_base", "entrega", json.dumps({
                        "id": "sin_base", "clave": "sin_base", "huella": "h", "texto": "nuevo",
                    }), "entrega:sin_base"),
                )
            sin_referencia = verificar(RAIZ)
            self.assertNotEqual(sin_referencia.returncode, 0, sin_referencia.stdout)
            self.assertIn("sin referencia", sin_referencia.stdout)
            shutil.copy2(copia_sana, registro_corpus)
            copia = raiz / "copia"
            shutil.copytree(RAIZ / "motor", copia / "motor")
            dominio = copia / "motor" / "encargos" / "dominio.py"
            texto = dominio.read_text(encoding="utf-8")
            self.assertIn('    "plazo",\n', texto)
            dominio.write_text(texto.replace('    "plazo",\n', "", 1), encoding="utf-8")
            mutado = verificar(copia)
            self.assertNotEqual(mutado.returncode, 0, mutado.stdout)
            self.assertIn("fallos_repo", mutado.stdout)


if __name__ == "__main__":
    unittest.main()
