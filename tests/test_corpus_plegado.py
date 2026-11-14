from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from motor.encargos.registro import ClaveOcupada, Evento, Registro
from tools.plegado import campos_de

RAIZ = Path(__file__).resolve().parents[1]
HERRAMIENTA = RAIZ / "tools" / "corpus_plegado.py"


class CorpusPlegado(unittest.TestCase):
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
            }), encoding="utf-8")

            def verificar(arbol: Path) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(HERRAMIENTA), "verificar", str(corpus), "--raiz", str(arbol)],
                    cwd=RAIZ, capture_output=True, text=True, check=False,
                )

            sano = verificar(RAIZ)
            self.assertEqual(sano.returncode, 0, sano.stderr + sano.stdout)
            self.assertIn("estado igual en 1 de 1", sano.stdout)
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
