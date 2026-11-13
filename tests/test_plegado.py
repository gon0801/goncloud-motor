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


def _ejecutar(*args: str | Path, herramienta: Path = HERRAMIENTA) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(herramienta), *map(str, args)],
        capture_output=True, encoding="utf-8", check=False,
    )


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


def _correr(*args: str | Path, herramienta: Path = HERRAMIENTA) -> tuple[int, dict]:
    hecho = _ejecutar(*args, herramienta=herramienta)
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
        insertar = ("INSERT INTO events (ts, encargo, kind, data)"
                    " VALUES ('t', 'enc_a', 'entrega', '{}')")
        escribe = (f'if evento["kind"] == "cierre": __import__("sqlite3").connect('
                   f'{str(self.registro)!r}, isolation_level=None).execute({insertar!r})')
        self._base(final="terminado", rompe=escribe)
        self._dominio(final="terminado")
        codigo, salida = self._comparar()
        db = sqlite3.connect(self.registro)
        self.addCleanup(db.close)
        (escritos,) = db.execute("SELECT count(*) FROM events WHERE encargo = 'enc_a'").fetchone()
        self.assertEqual(escritos, 3, "el evento llegó al registro durante la comparación")
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


if __name__ == "__main__":
    unittest.main()
