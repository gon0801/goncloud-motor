import json, socket, subprocess, sys
sys.path.insert(0, "/Users/dn/dev/goncloud-motor-encargos-3-4")
from pathlib import Path
from tempfile import TemporaryDirectory
from motor.encargos.registro import Registro
from motor.integraciones.directores import encargar, cerrar_sesion, resultado_directo, drenar
from motor import plataforma

with TemporaryDirectory() as tmp:
    root = Path(tmp)
    base = root / "base"; base.mkdir()
    chan = root / "chan.py"
    chan.write_text(
        "import json, os, sys\n"
        "modo = sys.argv[1] if len(sys.argv) > 1 else 'send'\n"
        "if modo == 'check':\n"
        "    print(json.dumps({'recibo': 'ext-123'}))\n"
        "else:\n"
        "    cuerpo = sys.stdin.read()\n"
        "    Path = __import__('pathlib').Path\n"
        "    Path(os.environ['CHAN_OUT']).write_text(cuerpo)\n"
        "    print(json.dumps({'recibo': 'ext-123'}))\n"
    )
    out = root / "recibido.txt"
    import os
    os.environ["CHAN_OUT"] = str(out)
    (base / "canal.json").write_text(json.dumps({"canal": {
        "comando": [sys.executable, str(chan), "send"],
        "conciliacion": [sys.executable, str(chan), "check"]}}))
    reg = Registro(base)
    proc = subprocess.Popen(["/bin/sleep", "300"])
    try:
        ident = {"pid": proc.pid, "inicio": plataforma.hora_inicio(proc.pid), "boot_id": plataforma.boot_id(), "host": socket.gethostname(), "sesion": "s1"}
        enc = encargar(reg, "pedido", "p1", "2026-10-27T00:00:00Z", "hermes", "canal", "directa", identidad=ident)["encargo"]
        print("cerrar:", cerrar_sesion(reg, enc).get("estado"))
        print("resultado:", resultado_directo(reg, enc, "resultado real", "2026-10-27T00:00:01Z").get("estado"))
        r1 = drenar(reg, cortar="despues")
        print("tras corte despues:", [(a["estado"], a.get("recibo")) for a in r1["avisos"]])
        r2 = drenar(reg)
        print("tras conciliar:", [(a["estado"], a.get("recibo")) for a in r2["avisos"]])
        print("canal recibio cuerpo una vez:", out.read_text() if out.exists() else None)
        r3 = drenar(reg)
        print("tercer drenar no reenvia:", [(a["estado"], a.get("recibo")) for a in r3["avisos"]])
    finally:
        if proc.poll() is None: proc.kill()
        reg.close()
print("done")
