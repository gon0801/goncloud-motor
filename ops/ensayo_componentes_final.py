"""Ensayo aislado del controlador en PC/Mini desde fuentes Git comprometidas.

Sin --ejecutar sólo transfiere bundles y verifica sus revisiones. Nunca copia
credenciales ni detiene servicios. La salida contiene resúmenes, no instaladores.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
REPOS = {
    "motor": RAIZ,
    "hermes": RAIZ.parent / "goncloud-hermes-encargos-3-4",
    "claw": RAIZ.parent / "goncloud-openclaw-encargos-3-4",
    "conf": RAIZ.parent / "goncloud-motor-conf-encargos-3-4",
}
BASES = {
    "mini": "/Users/gon/ensayo-final34-componentes",
    "pc": "C:/Users/ehven/ensayo-final34-componentes",
}
PYTHON = {"mini": "/Users/gon/.local/bin/python3", "pc": "py -3"}


def ejecutar(argv, *, cwd=None, entrada=None, timeout=3600):
    r = subprocess.run(
        argv, cwd=cwd, input=entrada, capture_output=True, timeout=timeout, check=False
    )
    if r.returncode:
        detalle = r.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(
            f"{Path(argv[0]).name} terminó con salida {r.returncode}: {detalle}"
        )
    return r.stdout.decode("utf-8", errors="replace").strip()


def comando_python(host, codigo):
    expresion = "exec(bytes.fromhex('" + codigo.encode().hex() + "'))"
    argumento = '"' + expresion + '"' if host == "pc" else shlex.quote(expresion)
    return PYTHON[host] + " -c " + argumento


def transferir(host, sha):
    actual = ejecutar(["git", "-C", str(RAIZ), "rev-parse", "HEAD"])
    if actual != sha:
        raise ValueError("--sha debe ser el HEAD comprometido del motor")
    with tempfile.TemporaryDirectory(prefix="flota-transferencia-") as tmp:
        tmp = Path(tmp)
        fuentes = {}
        for nombre, repo in REPOS.items():
            revision = ejecutar(["git", "-C", str(repo), "rev-parse", "HEAD"])
            destino = tmp / (nombre + ".bundle")
            ejecutar(["git", "-C", str(repo), "bundle", "create", str(destino), "HEAD"])
            fuentes[nombre] = {
                "revision": revision,
                "sha256": hashlib.sha256(destino.read_bytes()).hexdigest(),
            }
        manifiesto = {"fuentes": fuentes, "motor": sha, "host": host}
        salida = io.BytesIO()
        with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
            for nombre in fuentes:
                z.write(tmp / (nombre + ".bundle"), nombre + ".bundle")
            z.writestr("fuentes.json", json.dumps(manifiesto))
            # Este archivo debe estar incluido también en el commit suministrado.
            script = ejecutar(
                [
                    "git",
                    "-C",
                    str(RAIZ),
                    "show",
                    sha + ":ops/ensayo_componentes_final.py",
                ]
            )
            z.writestr("ensayo.py", script)
        codigo = """import io,json,sys,zipfile\nfrom pathlib import Path\nb=Path(BASE); b.mkdir(parents=True,exist_ok=True)\nwith zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as z:\n for n in z.namelist():\n  assert '/' not in n and '\\\\' not in n\n  (b/n).write_bytes(z.read(n))\nprint(json.dumps({'transferido':True}))\n""".replace(
            "BASE", repr(BASES[host])
        )
        respuesta = ejecutar(
            ["ssh", "-o", "BatchMode=yes", host, comando_python(host, codigo)],
            entrada=salida.getvalue(),
        )
        return manifiesto, respuesta


def json_url(url):
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "goncloud-flota-ensayo"}),
        timeout=60,
    ) as r:
        return json.load(r)


def npm_version(nombre, paquete, version):
    fuente = (
        "https://registry.npmjs.org/"
        + urllib.parse.quote(paquete, safe="")
        + "/"
        + version
    )
    d = json_url(fuente)
    return {
        "nombre": nombre,
        "tipo": "npm",
        "paquete": paquete,
        "version": d["version"],
        "fuente": fuente,
        "tarball": d["dist"]["tarball"],
        "integridad": d["dist"]["integrity"],
    }


def remoto(base, sha, instalar, preparadas=None):
    base = Path(base).resolve()
    esperado = Path(BASES["pc" if os.name == "nt" else "mini"]).resolve()
    if base != esperado:
        raise ValueError("el ensayo sólo acepta su directorio reservado")
    procedencia = json.loads((base / "fuentes.json").read_text())
    if procedencia["motor"] != sha:
        raise ValueError("la transferencia no corresponde al SHA solicitado")
    if os.name != "nt":
        os.environ["PATH"] = (
            "/Users/gon/.openclaw/tools/node/bin:/opt/homebrew/bin:/usr/bin:/bin:"
            + os.environ.get("PATH", "")
        )
    else:
        os.environ["PATH"] = (
            "C:\\Program Files\\nodejs;C:\\Program Files\\Git\\cmd;"
            + os.environ.get("PATH", "")
        )
    repos = {}
    for nombre, info in procedencia["fuentes"].items():
        bundle = base / (nombre + ".bundle")
        if hashlib.sha256(bundle.read_bytes()).hexdigest() != info["sha256"]:
            raise ValueError("bundle alterado")
        repo = base / "fuentes" / nombre
        if not repo.exists():
            repo.parent.mkdir(exist_ok=True)
            ejecutar(["git", "clone", str(bundle), str(repo)])
        ejecutar(["git", "-C", str(repo), "fetch", str(bundle), "HEAD"])
        ejecutar(["git", "-C", str(repo), "checkout", "--detach", info["revision"]])
        if ejecutar(["git", "-C", str(repo), "status", "--porcelain"]):
            raise ValueError("fuente remota modificada")
        repos[nombre] = repo
    informe = {
        "host": procedencia["host"],
        "fuentes": procedencia["fuentes"],
        "pasos": [],
    }
    if not instalar:
        print(json.dumps({"preparado": True, "sha": sha, "host": informe["host"]}))
        return
    motor = repos["motor"]
    sys.path.insert(0, str(motor))
    from motor import plataforma
    from motor.flota.control import anotar_trabajo, asignar

    home = base / "instalacion"
    home.mkdir(exist_ok=True)

    def paso(nombre, datos):
        informe["pasos"].append({"nombre": nombre, "resultado": datos})
        (base / "informe.json").write_text(
            json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"paso": nombre, "ok": True}), flush=True)

    def cli(*args, fallo=False):
        r = subprocess.run(
            [sys.executable, "-B", "-m", "motor", "flota", *map(str, args)],
            cwd=motor,
            capture_output=True,
            timeout=3600,
            check=False,
        )
        if fallo:
            if not r.returncode:
                raise AssertionError("se aceptó una candidata de integridad inválida")
            return {"rc": r.returncode}
        if r.returncode:
            detalle = r.stderr.decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError("CLI flota falló: " + str(args[0]) + ": " + detalle)
        return json.loads(r.stdout)

    componentes = ["codex", "hermes"] if os.name == "nt" else ["codex", "openclaw"]
    descubierto = cli("descubrir", "--componentes", *componentes)
    if descubierto["no_disponibles"]:
        raise RuntimeError("un canal oficial requerido no respondió")
    paso("descubrimiento_oficial", descubierto)
    codex_b = next(d for d in descubierto["componentes"] if d["nombre"] == "codex")
    versiones = json_url("https://registry.npmjs.org/%40openai%2Fcodex")
    estables = [
        v for v in versiones["versions"] if all(p.isdigit() for p in v.split("."))
    ]
    estables.sort(key=lambda v: tuple(map(int, v.split("."))))
    indice = estables.index(codex_b["version"])
    if indice < 1:
        raise RuntimeError("no hay versión anterior oficial de Codex")
    codex_a = npm_version("codex", "@openai/codex", estables[indice - 1])
    if os.name == "nt":
        hermes = next(d for d in descubierto["componentes"] if d["nombre"] == "hermes")
        a, b = [codex_a, hermes], [codex_b, hermes]
    else:
        a = [codex_a, npm_version("openclaw", "openclaw", "2026.10.7")]
        b = [codex_b, npm_version("openclaw", "openclaw", "2026.10.8")]
    catalogos = {}
    for nombre, componentes_catalogo in (("A", a), ("B", b)):
        p = base / ("catalogo-" + nombre + ".json")
        p.write_text(
            json.dumps({"componentes": componentes_catalogo, "no_disponibles": []}),
            encoding="utf-8",
        )
        catalogos[nombre] = p
    opciones = ["--home", home, "--motor-origen", motor]
    for nombre in ("hermes", "claw", "conf"):
        opciones += ["--repo-" + nombre, repos[nombre]]
    if preparadas:
        from motor.flota.actualizaciones import comprobar_runtime

        for identificador in preparadas:
            if len(identificador) != 40 or any(
                c not in "0123456789abcdef" for c in identificador
            ):
                raise ValueError("identidad de preparacion invalida")
            comprobar_runtime(home / "generaciones" / identificador)
        paso(
            "preparaciones_previas_comprobadas",
            {"generaciones": preparadas, "controlador": sha},
        )
        primera = cli("aplicar", "--home", home, "--destino", preparadas[0])
    else:
        primera = cli("actualizar", *opciones, "--catalogo", catalogos["A"])
    gen_a = asignar(home)
    paso("aplicar_A", primera)
    trabajador = base / "trabajador.py"
    trabajador.write_text(
        "import json,subprocess,sys,time\nfrom pathlib import Path\ng=Path(sys.argv[1]);r=json.loads((g/'runtime.json').read_text()); a=r['lanzadores']['codex'];a=[str(g/x) if (g/x).exists() else x for x in a]\ndef foto():\n p=subprocess.run(a+['--version'],capture_output=True,text=True);assert p.returncode==0;return {'version':p.stdout.strip(),'argv':a,'poteto':r.get('poteto'),'generacion':g.name}\nPath(sys.argv[2]).write_text(json.dumps(foto()))\nwhile not Path(sys.argv[3]).exists():time.sleep(.05)\nPath(sys.argv[4]).write_text(json.dumps(foto()))\n",
        encoding="utf-8",
    )
    antes, senal, despues = [base / n for n in ("antes.json", "seguir", "despues.json")]
    for p in (antes, senal, despues):
        p.unlink(missing_ok=True)
    proceso = subprocess.Popen(
        [
            sys.executable,
            str(trabajador),
            str(home / "generaciones" / gen_a),
            str(antes),
            str(senal),
            str(despues),
        ]
    )
    identidad = {
        "pid": proceso.pid,
        "inicio": plataforma.hora_inicio(proceso.pid),
        "boot_id": plataforma.boot_id(),
        "host": socket.gethostname(),
    }
    anotar_trabajo(home, "trabajador-ensayo", gen_a, "codex", identidad)
    try:
        for _ in range(200):
            if antes.exists():
                break
            if proceso.poll() is not None:
                raise RuntimeError("el trabajador no arrancó")
            time.sleep(0.05)
        assert antes.exists()
        segunda = (
            cli("aplicar", "--home", home, "--destino", preparadas[1])
            if preparadas
            else cli("actualizar", *opciones, "--catalogo", catalogos["B"])
        )
        assert segunda["accion"] == "ocupada" and asignar(home) == gen_a
        senal.touch()
        proceso.wait(timeout=30)
        assert json.loads(antes.read_text()) == json.loads(despues.read_text())
        paso(
            "B_ocupada_conserva_A",
            {"accion": segunda["accion"], "trabajador": json.loads(antes.read_text())},
        )
    finally:
        if proceso.poll() is None:
            plataforma.matar_proc(identidad)
        proceso.wait(timeout=15)
    segunda = (
        cli("aplicar", "--home", home, "--destino", preparadas[1])
        if preparadas
        else cli("actualizar", *opciones, "--catalogo", catalogos["B"])
    )
    gen_b = asignar(home)
    assert segunda["accion"] in ("aplicada", "igual") and gen_a != gen_b
    paso("aplicar_B", segunda)
    if os.name == "nt":
        plugin = home / "generaciones" / gen_a / "config/hermes/plugins/goncloud-team"
        sys.path.insert(0, str(plugin))
        from subscriptions import process as proceso_cli
        from subscriptions.process import _resolve_windows_shim, run_cli

        espacio = base / "prueba-plugin"
        espacio.mkdir(exist_ok=True)
        global_cli = subprocess.run(
            _resolve_windows_shim(["codex", "--version"]),
            capture_output=True,
            text=True,
            check=True,
        )
        ejecutados = []
        ejecutar_arbol = proceso_cli._run_with_process_tree

        def registrar(argv, **kwargs):
            ejecutados.append(argv[0])
            return ejecutar_arbol(argv, **kwargs)

        proceso_cli._run_with_process_tree = registrar
        fijado = run_cli(
            ["codex", "--version"],
            "",
            espacio,
            30,
            env_overlay={"MOTOR_GENERACION_RUTA": str(home / "generaciones" / gen_a)},
        )
        assert global_cli.returncode == fijado.returncode == 0
        assert codex_a["version"] in fijado.stdout
        assert (
            Path(ejecutados[-1])
            .resolve()
            .is_relative_to((home / "generaciones" / gen_a).resolve())
        )
        otro = run_cli(
            ["codex", "--version"],
            "",
            espacio,
            30,
            env_overlay={"MOTOR_GENERACION_RUTA": str(home / "generaciones" / gen_b)},
        )
        assert otro.returncode == 0 and codex_b["version"] in otro.stdout
        proceso_cli._run_with_process_tree = ejecutar_arbol
        paso(
            "plugin_version_fijada",
            {"global": global_cli.stdout.strip(), "generacion": fijado.stdout.strip()},
        )
    mala = copy.deepcopy({"componentes": b, "no_disponibles": []})
    mala["componentes"][0]["integridad"] = (
        "sha512-" + base64.b64encode(bytes(64)).decode()
    )
    mala_path = base / "catalogo-invalido.json"
    mala_path.write_text(json.dumps(mala))
    paso(
        "integridad_rechazada",
        cli("preparar", *opciones, "--catalogo", mala_path, fallo=True),
    )
    assert asignar(home) == gen_b
    paso(
        "corte_aplicacion",
        cli("aplicar", "--home", home, "--destino", gen_a, "--corte", "worktree"),
    )
    assert asignar(home) == gen_b
    recuperada = cli("recuperar", "--home", home)
    assert asignar(home) == gen_a
    paso("recuperacion", recuperada)
    cli("aplicar", "--home", home, "--destino", gen_b)
    # Falla de arranque en una candidata del motor real; no se fabrican recibos.
    roto = base / "motor-fallo"
    if not roto.exists():
        ejecutar(["git", "clone", str(motor), str(roto)])
    entrada = roto / "motor/__main__.py"
    original = entrada.read_text(encoding="utf-8")
    bandera = base / "fallar-arranque"
    condicion = (
        "\nfrom pathlib import Path as _EnsayoPath\nif _EnsayoPath("
        + repr(str(bandera))
        + ").exists(): raise RuntimeError('fallo de arranque de ensayo')\n"
    )
    if "from __future__ import annotations" in original:
        nuevo = original.replace(
            "from __future__ import annotations",
            "from __future__ import annotations" + condicion,
            1,
        )
    else:
        nuevo = condicion + original
    entrada.write_text(nuevo, encoding="utf-8")
    ejecutar(["git", "-C", str(roto), "add", "motor/__main__.py"])
    ejecutar(
        [
            "git",
            "-C",
            str(roto),
            "-c",
            "user.name=Ensayo motor",
            "-c",
            "user.email=ensayo@localhost",
            "commit",
            "-m",
            "Inyecta fallo de arranque aislado",
        ]
    )
    rev_rota = ejecutar(["git", "-C", str(roto), "rev-parse", "HEAD"])
    ejecutar(["git", "-C", str(home / "motor"), "fetch", str(roto), rev_rota])
    cli("aplicar", "--home", home, "--destino", rev_rota)
    bandera.touch()
    prueba = subprocess.run(
        [sys.executable, "-m", "motor", "--help"],
        cwd=home / "generaciones" / rev_rota,
        capture_output=True,
        check=False,
    )
    assert prueba.returncode
    reversa = ejecutar(
        [sys.executable, str(motor / "ops/reversa_ensayo.py"), "--home", str(home)]
    )
    assert asignar(home) == gen_b
    bandera.unlink()
    paso(
        "motor_inarrancable_revertido",
        {"rc_arranque": prueba.returncode, "reversa": reversa, "generacion": gen_b},
    )
    paso("estado_final", cli("estado", "--home", home))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", choices=BASES)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--ejecutar", action="store_true")
    ap.add_argument("--remoto", action="store_true")
    ap.add_argument("--base")
    ap.add_argument("--preparadas", nargs=2, metavar=("GENERACION_A", "GENERACION_B"))
    args = ap.parse_args()
    if args.remoto:
        remoto(args.base, args.sha, args.ejecutar, args.preparadas)
        return
    if not args.host:
        ap.error("falta --host")
    manifiesto, respuesta = transferir(args.host, args.sha)
    print(respuesta)
    ruta = BASES[args.host] + "/ensayo.py"
    codigo = (
        "import runpy,sys;sys.argv="
        + repr(
            [ruta, "--remoto", "--base", BASES[args.host], "--sha", args.sha]
            + (["--ejecutar"] if args.ejecutar else [])
            + (["--preparadas", *args.preparadas] if args.preparadas else [])
        )
        + ";runpy.run_path("
        + repr(ruta)
        + ",run_name='__main__')"
    )
    salida = ejecutar(
        ["ssh", "-o", "BatchMode=yes", args.host, comando_python(args.host, codigo)],
        timeout=10800,
    )
    print(salida)
    destino = (
        RAIZ
        / "docs/evidence/correcciones-3-4"
        / ("componentes-final-" + args.host + ".json")
    )
    if args.ejecutar:
        codigo = (
            "from pathlib import Path;print(Path("
            + repr(BASES[args.host] + "/informe.json")
            + ").read_text(encoding='utf-8'))"
        )
        informe = ejecutar(
            ["ssh", "-o", "BatchMode=yes", args.host, comando_python(args.host, codigo)]
        )
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(informe + "\n", encoding="utf-8")
        print(json.dumps({"evidencia": str(destino), "fuentes": manifiesto["fuentes"]}))


if __name__ == "__main__":
    main()
