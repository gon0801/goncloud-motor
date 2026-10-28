"""MOTOR 06 conserva el producto y el efecto al reparar la comprobación."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from motor.encargos.ciclo import _incorporar, ahora_iso, recibir
from motor.encargos.comprobacion import firma_producto
from motor.encargos.frontera import DestinoEnsayo, despachar
from motor.encargos.registro import Registro

TOML = """
[modelos.k3]
nombre = "kimi-code/k3"
provider = "managed:kimi-code"

[modelos.k31]
nombre = "kimi-code/k3.1-pro"
provider = "managed:kimi-code"
"""


class Motor06(unittest.TestCase):
    def test_el_grep_no_cambia_el_producto_ni_repite_el_efecto(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            reg = Registro(base)
            self.addCleanup(reg.close)
            hora = ahora_iso()
            enc = recibir(
                reg,
                "conserva las definiciones kimi-code/k3 y kimi-code/k3.1-pro",
                "origen-06",
                hora,
            )["encargo"]
            espacio = base / "espacio" / enc
            espacio.mkdir(parents=True)
            producto = espacio / "modelos.toml"
            producto.write_text(TOML, encoding="utf-8")
            antes = producto.read_bytes()
            destino = DestinoEnsayo(base / "destino")
            primero = despachar(
                reg, enc, destino, "pr-26", "merge", "pr-26", "integrado", hora
            )
            self.assertEqual(primero["n"], 1)
            interno = espacio / ".interno"
            interno.mkdir()
            (interno / "pedido-efecto.json").write_text(
                json.dumps(
                    {
                        "id": "pr-26",
                        "operacion": "merge",
                        "clave": "pr-26",
                        "cuerpo": "integrado",
                    }
                )
            )
            comprobaciones = interno / "comprobaciones.json"
            comprobaciones.write_text(
                json.dumps(
                    {
                        "comprobaciones": [
                            {
                                "objetivo": "las dos definiciones Kimi",
                                "argv": ["grep", "[z-a]", "modelos.toml"],
                            }
                        ]
                    }
                )
            )
            d = base / "ejecucion" / enc
            d.mkdir(parents=True)

            def incorporar():
                e = reg.fold(enc)
                n = e.generacion + 1
                reg.anotar(
                    enc,
                    {
                        "kind": "reserva",
                        "data": {"rol": e.rol or "responsable", "generacion": n},
                    },
                    f"reserva:{n}",
                )
                reg.anotar(enc, {"kind": "reserva_cerrada", "data": {}}, f"cerrada:{n}")
                reg.anotar(
                    enc,
                    {
                        "kind": "estrategia",
                        "data": {
                            "rol": e.rol or "responsable",
                            "estrategia": e.estrategia,
                            "motivo": e.motivo,
                            "siguiente": e.siguiente,
                        },
                    },
                    f"rol:{n}",
                )
                if e.rol != "revisor":
                    (d / "observaciones.json").unlink(missing_ok=True)
                    _incorporar(reg, reg.fold(enc), espacio, hora, d)
                    fin = time.time() + 10
                    while (
                        not (d / "observaciones.json").is_file() and time.time() < fin
                    ):
                        time.sleep(0.05)
                return _incorporar(reg, reg.fold(enc), espacio, hora, d)

            e = incorporar()
            observacion = json.loads((d / "observaciones.json").read_text())
            self.assertNotEqual(observacion["ejecuciones"][0].get("rc"), 0)
            self.assertEqual(e.rol, "revisor")
            resultado = d / "resultado.json"
            (d / "revision.json").write_text(
                json.dumps(
                    {
                        "observaciones": str(d / "observaciones.json"),
                        "resultado": str(resultado),
                        "version": firma_producto(espacio),
                    }
                )
            )
            # Doble del revisor. Las observaciones siguen viniendo de procesos reales del supervisor.
            resultado.write_text(
                json.dumps(
                    {
                        "veredicto": "no_concluyente",
                        "version": firma_producto(espacio),
                        "evidencia": "el comando de comprobacion no examino el archivo",
                        "instrumento": {
                            "causa": "el patron grep es invalido, el archivo no fue examinado"
                        },
                    }
                )
            )
            e = incorporar()
            self.assertEqual(
                e.motivo, "el patron grep es invalido, el archivo no fue examinado"
            )
            self.assertEqual(e.rol, "responsable")
            self.assertNotEqual(e.estado, "terminado")
            codigo = (
                "import tomllib; from pathlib import Path; "
                "d=tomllib.loads(Path('modelos.toml').read_text()); "
                "v={x['nombre'] for x in d['modelos'].values()}; "
                "assert {'kimi-code/k3','kimi-code/k3.1-pro'} <= v; print(sorted(v))"
            )
            comprobaciones.write_text(
                json.dumps(
                    {
                        "comprobaciones": [
                            {
                                "objetivo": "ambas definiciones Kimi presentes",
                                "argv": ["python3", "-c", codigo],
                            }
                        ]
                    }
                )
            )
            e = incorporar()
            observacion = json.loads((d / "observaciones.json").read_text())
            self.assertEqual(observacion["ejecuciones"][0].get("rc"), 0)
            resultado.write_text(
                json.dumps(
                    {
                        "veredicto": "aprobado",
                        "version": firma_producto(espacio),
                        "evidencia": observacion["ejecuciones"][0]["stdout"],
                        "efectos_pendientes": ["pr-26"],
                    }
                )
            )
            e = incorporar()
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(producto.read_bytes(), antes)
            self.assertEqual(destino.consultar("pr-26")["n"], 1)
            self.assertEqual(
                next(x for x in e.efectos if x.id == "pr-26").recibo, primero["recibo"]
            )


if __name__ == "__main__":
    unittest.main()
