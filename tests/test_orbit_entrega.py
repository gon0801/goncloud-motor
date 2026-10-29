from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from motor.encargos.ciclo import _asegurar_repo, _commit_push, ahora_iso, recibir
from motor.encargos.dominio import alcance_de, revision_independiente_cubre_producto
from motor.encargos.registro import Registro
from motor.github import GhGitHub


class OrbitEntrega(unittest.TestCase):
    def test_checkout_local_publica_en_su_remoto_sin_alterar_el_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(path, *args):
                return subprocess.run(
                    ["git", "-C", str(path), *args],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()

            bare = root / "remoto.git"
            subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
            source = root / "checkout"
            source.mkdir()
            git(source, "init", "--quiet", "-b", "master")
            (source / "a.py").write_text("valor = 1\n")
            git(source, "add", ".")
            git(
                source,
                "-c",
                "user.name=ensayo",
                "-c",
                "user.email=ensayo@localhost",
                "commit",
                "--quiet",
                "-m",
                "base",
            )
            git(source, "remote", "add", "origin", str(bare))
            git(source, "push", "--quiet", "origin", "master")
            mirror = root / "espejo"
            subprocess.run(
                ["git", "clone", "--quiet", str(source), str(mirror)], check=True
            )
            remote = "git@github.com:owner/orbit.git"
            git(mirror, "remote", "set-url", "origin", remote)
            git(source, "remote", "set-url", "origin", "../espejo")
            reg = Registro(root / "base")
            self.addCleanup(reg.close)
            enc_id = recibir(
                reg,
                "implementa",
                "orbit-local",
                ahora_iso(),
                entrega_nivel="pr",
                repo=str(source),
                rama="encargo/inspeccion",
                base_rama="master",
            )["encargo"]
            enc = reg.fold(enc_id)
            space = root / "base/espacio" / enc_id
            space.mkdir(parents=True)
            self.assertEqual(_asegurar_repo(enc, space), "")
            self.assertEqual(git(space, "config", "--get", "remote.origin.url"), remote)
            head = git(space, "rev-parse", "HEAD")
            git(space, "remote", "set-url", "origin", str(source))
            self.assertEqual(_asegurar_repo(enc, space), "")
            self.assertEqual(git(space, "rev-parse", "HEAD"), head)
            self.assertEqual(git(space, "config", "--get", "remote.origin.url"), remote)
            self.assertEqual(
                git(source, "config", "--get", "remote.origin.url"), "../espejo"
            )
            self.assertEqual(
                git(mirror, "config", "--get", "remote.origin.url"), remote
            )
            git(space, "config", f"url.{bare.as_uri()}.pushInsteadOf", remote)
            (space / "a.py").write_text("valor = 2\n")
            sha, causa = _commit_push(enc, space)
            self.assertEqual(causa, "")
            self.assertEqual(
                git(bare, "rev-parse", "refs/heads/encargo/inspeccion"), sha
            )
            self.assertEqual(git(source, "branch", "--list", "encargo/inspeccion"), "")
            llamadas = []
            gh = GhGitHub()

            def responder(*args):
                llamadas.append(args)
                if args[:2] == ("pr", "list"):
                    return '[{"number":7}]'
                return json.dumps(
                    {
                        "number": 7,
                        "headRefName": enc.entrega.rama,
                        "baseRefName": "master",
                        "isDraft": True,
                        "headRefOid": sha,
                        "state": "OPEN",
                    }
                )

            gh._gh = responder
            self.assertEqual(
                gh.abrir_pr(
                    str(source), enc.entrega.rama, "master", "inspeccion", ""
                ).head,
                sha,
            )
            self.assertTrue(
                all(args[args.index("-R") + 1] == "owner/orbit" for args in llamadas)
            )

    def test_revision_incierta_no_funda_una_aprobacion_de_correccion(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp))
            self.addCleanup(reg.close)
            hora = ahora_iso()
            enc = recibir(
                reg, "inspeccion real antes de implementar", "orbit-scope", hora
            )["encargo"]

            def anotar(kind, **data):
                count = len(reg.eventos(enc))
                self.assertEqual(
                    reg.anotar(enc, {"kind": kind, "data": data}, f"prueba:{count}"),
                    "nuevo",
                )
                return reg.fold(enc)

            anotar(
                "producto_listo",
                version="v1",
                manifiesto=[["a.py", "a1"], ["b.py", "b1"]],
            )
            e = anotar(
                "revision",
                version="v1",
                alcance="completa",
                veredicto="no_concluyente",
                aprobadas=[],
            )
            self.assertIsNone(e.revisado)
            self.assertEqual(alcance_de(e).clase, "completa")
            anotar(
                "producto_listo",
                version="v2",
                manifiesto=[["a.py", "a2"], ["b.py", "b1"]],
            )
            e = anotar(
                "revision",
                version="v2",
                base_version="v1",
                alcance="correccion",
                veredicto="aprobado",
                aprobadas=[["a.py", "a2"], ["b.py", "b1"]],
            )
            self.assertFalse(revision_independiente_cubre_producto(e))
            self.assertEqual(alcance_de(e).clase, "completa")
            hallazgo = {
                "id": "h_accounting",
                "gravedad": "bloqueante",
                "criterio": "comportamiento",
                "titulo": "falta la inspeccion real",
                "reproduccion": "inspeccionar la fuente",
                "origen": "independiente",
            }
            e = anotar(
                "revision",
                version="v2",
                alcance="completa",
                veredicto="cambios",
                hallazgos=[hallazgo],
                aprobadas=[["b.py", "b1"]],
            )
            anotar(
                "producto_listo",
                version="v3",
                manifiesto=[["a.py", "a3"], ["b.py", "b1"]],
            )
            e = anotar(
                "revision",
                version="v3",
                base_version="v2",
                alcance="correccion",
                veredicto="no_concluyente",
                aprobadas=[],
            )
            self.assertEqual(e.revision_vigente.veredicto, "no_concluyente")
            self.assertEqual(e.revisado.firma, "v2")
            self.assertEqual(e.aprobadas, (("b.py", "b1"),))
            alcance = alcance_de(e)
            self.assertEqual(
                (
                    alcance.clase,
                    alcance.base_version,
                    alcance.tocadas,
                    alcance.conservadas,
                ),
                ("correccion", "v2", ("a.py",), ("b.py",)),
            )


if __name__ == "__main__":
    unittest.main()
