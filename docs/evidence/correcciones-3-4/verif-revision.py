import json, subprocess, sys
sys.path.insert(0, "/Users/dn/dev/goncloud-motor-encargos-3-4")
from pathlib import Path
from tempfile import TemporaryDirectory
from motor.encargos.registro import Registro
from motor.integraciones.directores import encargar, cerrar_sesion, promover, resultado_directo

# F2 reproduccion revision
with TemporaryDirectory() as tmp:
    root = Path(tmp)
    local, canonical = root / "worker", root / "mini"
    local.mkdir(); canonical.mkdir()
    (local / "autoridad.json").write_text(json.dumps({
        "modo": "local", "base": str(canonical), "python": sys.executable,
        "pythonpath": str(Path.cwd()), "cwd": str(Path.cwd())}))
    worker = Registro(local)
    one = encargar(worker, "pedido", "p1", "2026-10-27T00:00:00Z", "hermes", "canal", "directa")["encargo"]
    print("cerrar_sin_identidad:", cerrar_sesion(worker, one))
    print("promover_sin_cierre_observado:", promover(worker, one, "promover1", "2026-10-27T00:00:01Z").get("motivo"))
    two = encargar(worker, "otro", "p2", "2026-10-27T00:00:00Z", "hermes", "canal", "directa")["encargo"]
    print("resultado_sin_cierre:", resultado_directo(worker, two, "resultado real", "2026-10-27T00:00:01Z"))
    authority = Registro(canonical)
    print("F2 worker/autoridad via one:", worker.fold(one).via, authority.fold(one).via)
    print("F2 worker/autoridad estado two:", worker.fold(two).estado, authority.fold(two).estado)
    authority.close(); worker.close()

# F5
from motor.flota.control import publicar_proyeccion
from motor.flota.manifiesto import desde_doc
def politica(rol):
    return desde_doc({"generacion": "prueba", "puesto": [{"funcion": rol, "modelo": ["proveedor/modelo"], "esfuerzo": ["medium"]}]})
with TemporaryDirectory() as tmp:
    home = Path(tmp)
    paquete = Path("/Users/dn/dev/goncloud-motor-encargos-3-4/paquetes/poteto")
    publicar_proyeccion(home, politica("dirigir"), paquete)
    ruta = home / "proyeccion/model-routing.source.json"
    antes = ruta.read_text()
    try:
        publicar_proyeccion(home, politica("codigo"), paquete)
    except ValueError as exc:
        print("F5 rechazada:", exc)
    print("F5 anterior_conservada:", antes == ruta.read_text())
    print("F5 flota_conservada:", (home / "proyeccion/flota.json").exists())

# F7 propio repo sin comprobar.py
import tempfile
repo = Path("/Users/dn/dev/goncloud-motor-encargos-3-4")
sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=repo).strip()
from motor.flota.control import aplicar
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    subprocess.run(["git", "clone", "-q", "--shared", str(repo), str(home / "motor")], check=True)
    try:
        r = aplicar(home, sha)
        print("F7 existe_comprobador:", (home / "motor/comprobar.py").exists(), "resultado:", r)
    finally:
        for gen in (home / "generaciones").glob("*"):
            subprocess.run(["git", "-C", str(home / "motor"), "worktree", "remove", "--force", str(gen)], capture_output=True)
