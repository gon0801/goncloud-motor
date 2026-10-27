import json, subprocess, sys, time
from pathlib import Path

RAIZ = Path("/Users/dn/dev/goncloud-motor-encargos-3-4")
BASE = Path("/tmp/demo-durable-r3")
CANAL = Path("/tmp/canal_openbot.py")
CANAL.write_text(
    "import json, os, subprocess, sys\n"
    "cuerpo = sys.stdin.read()\n"
    "argv = ['node', '/Applications/OpenBot.app/Contents/Resources/cli/openbot-cli.mjs', 'run', 'DG', cuerpo, '--id', os.environ.get('MOTOR_AVISO_ID','aviso'), '--json']\n"
    "r = subprocess.run(argv, capture_output=True, text=True, timeout=120)\n"
    "sys.stdout.write(r.stdout)\n"
    "sys.exit(r.returncode)\n"
)

def motor(*args, timeout=180):
    r = subprocess.run([sys.executable, "-B", "-m", "motor", *args], cwd=RAIZ, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise SystemExit(f"motor {args} rc={r.returncode}: {r.stdout[-500:]} {r.stderr[-500:]}")
    return json.loads(r.stdout)

if BASE.exists():
    import shutil; shutil.rmtree(BASE)
BASE.mkdir(parents=True)
plan = BASE / "plan-externo.txt"
plan.write_text("Escribe el archivo resultado.txt con la frase exacta: entrega durable comprobada. Solo eso, sin otros archivos.\n", encoding="utf-8")
(BASE / "canal.json").write_text(json.dumps({"openbot": {
    "comando": [sys.executable, str(CANAL), "send"],
    "conciliacion": [sys.executable, str(CANAL), "check"],
}}), encoding="utf-8")

alta = motor("director", "encargar", "--base", str(BASE), "--director", "openbot", "--via", "durable", "--canal", "openbot", "--clave", "durable-r3-1", "--archivo", str(plan))
enc = alta["encargo"]
print("encargo", enc, alta.get("estado"), alta.get("via"))

estado = ""
for i in range(30):
    motor("supervisor", "--base", str(BASE), "--una-vez", "--agente", "grok", "--umbral", "30", timeout=300)
    v = motor("director", "ver", enc, "--base", str(BASE))
    estado = v["estado"]
    print(f"paso {i}: {estado} ultimo={v.get('ultimo_hecho')}")
    if estado in ("terminado", "cancelado"):
        break
    time.sleep(2)
print("estado_final", estado)
prod = BASE / "espacio" / enc / "resultado.txt"
print("producto", prod.read_text(encoding="utf-8") if prod.exists() else "sin producto")
avisos = motor("director", "avisar", "--base", str(BASE))
print("avisos", json.dumps(avisos)[:600])
