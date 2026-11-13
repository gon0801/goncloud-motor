"""La herramienta de plegado dice qué encargo guardado se rompe o cambia.

Cada prueba arma un registro chico en un temporal, con la misma tabla que
escribe el motor, y corre `tools/plegado.py` como lo corre una persona.
"""

from __future__ import annotations

import json
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


def _intento(host: str | None) -> list[tuple[str, dict]]:
    con_host = {"host": host} if host else {}
    return [
        ("reserva", {"rol": "responsable", "hora": "t1", **con_host}),
        ("intento_abierto", {"id": "A", "pid": 1, "inicio": "x", "rol": "responsable", **con_host}),
    ]


# Guardado con las reglas de su día; una escritura nueva hoy lo rechazaría.
_FASES_VACIAS = ("fases", {"fases": []})


def _armar(ruta: Path, encargos: dict[str, list[tuple[str, dict]]]) -> None:
    db = sqlite3.connect(ruta)
    db.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
        " encargo TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
    )
    for encargo, eventos in encargos.items():
        for kind, data in eventos:
            db.execute(
                "INSERT INTO events (ts, encargo, kind, data) VALUES ('t', ?, ?, ?)",
                (encargo, kind, json.dumps(data)),
            )
    db.commit()
    db.close()


def _correr(*args: str | Path, herramienta: Path = HERRAMIENTA) -> tuple[int, dict]:
    hecho = subprocess.run(
        [sys.executable, str(herramienta), *map(str, args)],
        capture_output=True, text=True, check=False,
    )
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


_DOMINIO_FALSO = """
from dataclasses import dataclass

ESTADOS = ("recibido", "terminado")


@dataclass(frozen=True)
class Encargo:
    estado: str


def reducir(estado, evento, nuevo=True):
    return Encargo(FINAL if evento["kind"] == "cierre" else "recibido")
"""


class Comparar(unittest.TestCase):
    """`comparar` en un repo propio con dos versiones de un reductor mínimo."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.repo = self.dir / "repo"
        (self.repo / "tools").mkdir(parents=True)
        (self.repo / "motor" / "encargos").mkdir(parents=True)
        for paquete in ("motor", "motor/encargos"):
            (self.repo / paquete / "__init__.py").touch()
        shutil.copy(HERRAMIENTA, self.repo / "tools" / "plegado.py")
        self._dominio("recibido")
        self._git("init", "-q")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "base")
        self.registro = self.dir / "registro.sqlite"
        _armar(self.registro, {"enc_a": [("entrega", {}), ("cierre", {})]})

    def _dominio(self, final: str) -> None:
        texto = f"FINAL = {final!r}\n{_DOMINIO_FALSO}"
        (self.repo / "motor" / "encargos" / "dominio.py").write_text(texto)

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=prueba", "-c", "user.email=prueba@example.invalid", *args],
            cwd=self.repo, check=True, capture_output=True,
        )

    def _comparar(self) -> tuple[int, dict]:
        return _correr("comparar", "--base", "HEAD", "--json", self.registro,
                       herramienta=self.repo / "tools" / "plegado.py")

    def test_el_mismo_reductor_en_las_dos_refs_no_difiere(self):  # no-roja-en-base: control; muestra que comparar no reporta por defecto
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 0)
        self.assertEqual(salida["registros"][0]["diferencias"], [])

    def test_un_reductor_que_cambia_el_estado_final_se_reporta(self):
        self._dominio("terminado")
        codigo, salida = self._comparar()
        self.assertEqual(codigo, 1)
        (reg,) = salida["registros"]
        self.assertEqual(reg["diferencias"], [{"encargo": "enc_a", "campos": ["estado"], "detalle": ""}])


if __name__ == "__main__":
    unittest.main()
