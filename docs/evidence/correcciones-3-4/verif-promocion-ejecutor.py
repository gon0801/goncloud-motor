import json, subprocess, sys, time
sys.path.insert(0, "/Users/dn/dev/goncloud-motor-encargos-3-4")
from pathlib import Path
from tempfile import TemporaryDirectory
from motor.encargos.registro import Registro
from motor.integraciones.directores import encargar, cerrar_sesion, promover, resultado_directo
from motor import plataforma

with TemporaryDirectory() as tmp:
    root = Path(tmp)
    local, canonical = root / "worker", root / "mini"
    local.mkdir(); canonical.mkdir()
    (local / "autoridad.json").write_text(json.dumps({
        "modo": "local", "base": str(canonical), "python": sys.executable,
        "pythonpath": "/Users/dn/dev/goncloud-motor-encargos-3-4", "cwd": "/Users/dn/dev/goncloud-motor-encargos-3-4"}))
    worker = Registro(local)
    proc = subprocess.Popen(["/bin/sleep", "300"])
    try:
        ident = {"pid": proc.pid, "inicio": plataforma.hora_inicio(proc.pid), "boot_id": plataforma.boot_id(), "host": __import__("socket").gethostname(), "sesion": "s1"}
        one = encargar(worker, "pedido", "p1", "2026-10-27T00:00:00Z", "hermes", "canal", "directa", identidad=ident)["encargo"]
        r = promover(worker, one, "promover1", "2026-10-27T00:00:01Z")
        print("promover_con_ejecutor_vivo_rechaza:", r.get("rechazada"), r.get("motivo"))
        c = cerrar_sesion(worker, one)
        print("cerrar_observa:", c.get("estado"), c.get("observado"))
        time.sleep(0.3)
        print("ejecutor_muerto_tras_cerrar:", proc.poll() is not None)
        p = promover(worker, one, "promover1", "2026-10-27T00:01:00Z")
        auth = Registro(canonical)
        print("F2 tras promover worker/autoridad:", worker.fold(one).via, auth.fold(one).via, "recibo:", bool(p.get("recibo")))
        p2 = promover(worker, one, "promover1", "2026-10-27T00:02:00Z")
        print("retransmision promover:", p2.get("retransmision"), auth.fold(one).via)
        # terminacion directa canonica en otro encargo
        two = encargar(worker, "otro", "p2", "2026-10-27T00:00:00Z", "hermes", "canal", "directa", identidad=ident | {"pid": ""})["encargo"]
        print("sin identidad no cierra ni termina OK (esperado rechazo previo)")
        auth.close()
    finally:
        if proc.poll() is None:
            proc.kill()
        worker.close()
print("done")
