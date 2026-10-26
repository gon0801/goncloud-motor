"""Demostración de las fases 1 y 2. Escribe un informe y sale distinto de cero si algo falla."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(RAIZ)]
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(encoding="utf-8", errors="replace")

from motor.encargos.ciclo import (  # noqa: E402
    ahora_iso,
    afirmar,
    barrer,
    consultar,
    corregir,
    crecer_log_intento,
    marcar_espera,
    marcar_pregunta,
    ordenar,
    parada_local,
    prompt_de,
    recibir,
)
from motor.encargos.frontera import DestinoEnsayo, despachar  # noqa: E402
from motor.encargos.registro import Registro  # noqa: E402
from motor.encargos.sesion import observar  # noqa: E402

INFORME: list[dict] = []


def paso(nombre: str, ok: bool, detalle: str) -> None:
    INFORME.append({"paso": nombre, "ok": ok, "detalle": detalle})
    print(f"{'ok' if ok else 'FALLO'}  {nombre}: {detalle}", flush=True)
    if not ok:
        raise SystemExit(_cerrar(1))


def _cerrar(codigo: int) -> int:
    destino = Path(os.environ["MOTOR_ENSAYO_INFORME"])
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(INFORME, ensure_ascii=False, indent=2), encoding="utf-8")
    print(destino)
    return codigo


def _motor(*args: str, base: Path, stdin: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    medio = os.environ.copy()
    if env:
        medio.update(env)
    medio["PYTHONPATH"] = str(RAIZ)
    return subprocess.run(
        [sys.executable, "-m", "motor", *args, "--base", str(base)],
        input=stdin,
        text=True,
        capture_output=True,
        env=medio,
        check=False,
    )


def _pid_vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> int:
    ensayo = Path(tempfile.mkdtemp(prefix="encargos-ensayo-"))
    os.environ["MOTOR_ENSAYO_INFORME"] = str(ensayo / "informe.json")
    if "--solo-remotos" in sys.argv:
        _remotos(ensayo)
        return _cerrar(0)
    if "--solo-agente" in sys.argv:
        _agente(ensayo)
        return _cerrar(0)
    if "--solo-autoridad" in sys.argv:
        _autoridad(ensayo)
        return _cerrar(0)
    base = ensayo / "base"
    hora = ahora_iso()
    reg = Registro(base)

    uno = recibir(
        reg,
        "arregla la búsqueda de pedidos",
        "msg-1",
        hora,
        restricciones=("no cambies el formato del archivo",),
        director="ensayo",
    )
    dos = recibir(
        reg,
        "arregla la búsqueda de pedidos",
        "msg-1",
        hora,
        restricciones=("no cambies el formato del archivo",),
        director="ensayo",
    )
    paso(
        "entrega repetida",
        uno["encargo"] == dos["encargo"] and uno["recibo"] == dos["recibo"] and "conflicto" not in dos,
        uno["encargo"],
    )
    conflicto = recibir(reg, "otro pedido", "msg-1", hora, director="ensayo")
    enc = uno["encargo"]
    paso(
        "misma clave con otro texto",
        conflicto.get("conflicto") is True and conflicto["encargo"] == enc,
        "se conserva el primero",
    )
    reg.close()

    archivo = ensayo / "plan-normal.md"
    archivo.write_text("Implementa este texto.\n\nNo lo ejecutes al recibirlo.\n", encoding="utf-8")
    cli = _motor(
        "encargar",
        "--archivo",
        str(archivo),
        "--clave",
        "archivo-1",
        "--restriccion",
        "conserva el objetivo",
        base=base,
    )
    datos = json.loads(cli.stdout)
    paso("archivo normal", cli.returncode == 0 and datos["estado"] == "recibido", datos["encargo"])

    reg = Registro(base)
    enc = datos["encargo"]
    afirmado = afirmar(reg, enc, "ya terminé", hora)
    paso("una afirmación no termina", afirmado["estado"] == "recibido", afirmado["estado"])
    preguntado = marcar_pregunta(reg, enc, "¿cuál es el umbral?", False, hora)
    reg.anotar(
        enc,
        {
            "kind": "actividad",
            "data": {"observada": True, "hecho": "leyó el espacio aislado", "hora": hora},
        },
        "actividad:lectura",
    )
    visto = consultar(reg, enc)
    paso(
        "pregunta aparte de la actividad",
        visto["estado"] == "ejecutando" and visto["preguntas"] == ["¿cuál es el umbral?"],
        visto["ultimo_hecho"],
    )
    futuro = "2099-01-01T00:00:00Z"
    espera = marcar_espera(reg, enc, "sin cupo del proveedor", futuro, hora)
    barrer(reg, hora, time.time(), lambda *a: ["/bin/bash", "-c", "echo no"], umbral_s=10**9)
    espera2 = consultar(reg, enc)
    paso(
        "la espera no se presenta como trabajo",
        espera["estado"] == "esperando_reintento"
        and espera2["estado"] == "esperando_reintento"
        and "sin cupo" in espera2["motivo"],
        espera2["siguiente"],
    )
    reg.anotar(
        enc,
        {"kind": "plazo", "data": {"hora": "2099-01-01T00:00:01Z"}},
        "plazo:ensayo",
    )
    reg.close()

    _procesos(ensayo)
    _efectos(ensayo)
    _parada_local(ensayo)
    _stdin(ensayo)
    _jaula(ensayo)
    _version()
    _recuperacion(ensayo)
    _remotos(ensayo)
    _autoridad(ensayo)
    _agente(ensayo)
    return _cerrar(0)


def _procesos(ensayo: Path) -> None:
    base = ensayo / "procesos"
    reg = Registro(base)
    hora = ahora_iso()
    enc = recibir(reg, "proceso de ensayo", "msg-proc", hora)["encargo"]
    espacio = base / "espacio" / enc
    espacio.mkdir(parents=True, exist_ok=True)
    script = "sleep 300 & echo $! > hijo.pid; sleep 300"
    barrer(
        reg,
        hora,
        time.time(),
        lambda *a: ["/bin/bash", "-c", script],
        umbral_s=10**9,
    )
    proc = reg.fold(enc).proc
    paso("arrancó un trabajador", proc is not None and _pid_vivo(proc.pid), str(proc.pid if proc else ""))
    marca_hijo = espacio / "hijo.pid"
    fin_hijo = time.time() + 5
    while time.time() < fin_hijo and not marca_hijo.exists():
        time.sleep(0.05)
    hijo = int(marca_hijo.read_text().strip())
    time.sleep(0.2)
    paso("el hijo sigue vivo antes de parar", _pid_vivo(hijo), str(hijo))
    crecer_log_intento(base, enc, "esto no es avance\n" * 50)
    parado = ordenar(reg, enc, "parar", "stop-1", ahora_iso())
    rechazado = despachar(
        reg, enc, DestinoEnsayo(base / "destino"), "ext-1", "escribir", "destino:ext-1", "x", ahora_iso()
    )
    paso(
        "parar cierra escrituras antes de exigir el cierre",
        parado["estado"] == "deteniendo" and rechazado.get("rechazado") is True,
        rechazado.get("motivo", ""),
    )
    fin = time.time() + 8
    while time.time() < fin and reg.fold(enc).estado != "detenido":
        barrer(reg, ahora_iso(), time.time(), None, umbral_s=10**9)
        time.sleep(0.1)
    estado = reg.fold(enc)
    paso(
        "parar cierra al padre y al hijo",
        estado.estado == "detenido" and not _pid_vivo(proc.pid) and not _pid_vivo(hijo),
        estado.estado,
    )
    seguido = ordenar(reg, enc, "continuar", "go-1", ahora_iso())
    otra = ordenar(reg, enc, "parar", "stop-1", ahora_iso())
    paso(
        "retransmitir parar no revierte continuar",
        seguido["estado"] == "recibido"
        and otra.get("retransmision") is True
        and otra["recibo"] == parado["recibo"]
        and reg.fold(enc).estado == "recibido",
        otra["recibo"],
    )
    reg.close()
    _supervisor(ensayo)
    _colgada(ensayo)


def _supervisor(ensayo: Path) -> None:
    base = ensayo / "supervisor"
    reg = Registro(base)
    recibir(reg, "trabajador que sobrevive al supervisor", "msg-sup", ahora_iso())
    reg.db.execute("PRAGMA wal_checkpoint(FULL)")
    reg.close()
    enc_dir = base / "ejecucion"
    antes = {p.name for p in enc_dir.iterdir()} if enc_dir.exists() else set()
    proc = subprocess.Popen(
        [sys.executable, "-m", "motor", "supervisor", "--base", str(base), "--agente", "sleep", "--umbral", "100000"],
        env={**os.environ, "PYTHONPATH": str(RAIZ)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    pid = ""
    fin = time.time() + 8
    while time.time() < fin and not pid:
        for carpeta in (base / "ejecucion").glob("*"):
            marca = carpeta / "proc.json"
            if carpeta.name in antes or not marca.exists():
                continue
            datos = json.loads(marca.read_text(encoding="utf-8"))
            if _pid_vivo(int(datos["pid"])):
                pid = str(datos["pid"])
                break
        time.sleep(0.1)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    paso("el supervisor se cerró y el trabajador sigue", bool(pid) and _pid_vivo(int(pid)), pid)
    otra = subprocess.run(
        [
            sys.executable,
            "-m",
            "motor",
            "supervisor",
            "--base",
            str(base),
            "--agente",
            "sleep",
            "--umbral",
            "100000",
            "--una-vez",
        ],
        env={**os.environ, "PYTHONPATH": str(RAIZ)},
        capture_output=True,
        text=True,
        check=False,
    )
    vivos = []
    for marca in (base / "ejecucion").glob("*/proc.json"):
        datos = json.loads(marca.read_text(encoding="utf-8"))
        if _pid_vivo(int(datos["pid"])):
            vivos.append(str(datos["pid"]))
    paso(
        "reiniciar el supervisor no abre otro escritor",
        otra.returncode == 0 and vivos == [pid],
        ",".join(vivos),
    )
    from motor import plataforma

    for marca in (base / "ejecucion").glob("*/proc.json"):
        datos = json.loads(marca.read_text(encoding="utf-8"))
        plataforma.matar_proc(int(datos["pid"]), str(datos.get("inicio") or ""))


def _colgada(ensayo: Path) -> None:
    base = ensayo / "colgada"
    reg = Registro(base)
    hora = ahora_iso()
    enc = recibir(reg, "sesión colgada", "msg-hang", hora)["encargo"]
    barrer(
        reg,
        hora,
        time.time(),
        lambda *a: ["/bin/bash", "-c", "sleep 300"],
        umbral_s=10**9,
    )
    primero = reg.fold(enc).proc
    crecer_log_intento(base, enc, "log mas largo\n" * 100)
    time.sleep(0.4)
    barrer(
        reg,
        ahora_iso(),
        time.time() + 30,
        lambda *a: ["/bin/bash", "-c", "sleep 300"],
        umbral_s=1,
    )
    segundo = reg.fold(enc).proc
    paso(
        "una sesión colgada se cierra sin otro escritor encima",
        primero is not None and not _pid_vivo(primero.pid) and (segundo is None or segundo.pid == primero.pid),
        f"{primero.pid if primero else 0}",
    )
    reg.close()


def _efectos(ensayo: Path) -> None:
    base = ensayo / "efectos"
    reg = Registro(base)
    hora = ahora_iso()
    enc = recibir(reg, "efecto de ensayo", "msg-fx", hora)["encargo"]
    destino = DestinoEnsayo(base / "destino-fx")
    destino.perder = True
    perdido = despachar(reg, enc, destino, "op-1", "escribir", "destino:op-1", "hola", hora)
    vista = consultar(reg, enc)
    n1 = destino.consultar("destino:op-1").get("n")
    destino.perder = False
    conciliado = despachar(reg, enc, destino, "op-1", "escribir", "destino:op-1", "hola", hora)
    n2 = destino.consultar("destino:op-1").get("n")
    paso(
        "respuesta perdida se concilia sin repetir",
        perdido.get("incierto") is True
        and vista["estado"] == "conciliando"
        and conciliado.get("conciliado") is True
        and n1 == 1
        and n2 == 1,
        str(conciliado),
    )
    marca = base / "autoridad.no"
    marca.write_text("1", encoding="utf-8")
    espacio = base / "espacio" / enc
    espacio.mkdir(parents=True, exist_ok=True)
    (espacio / "nota-aislada.txt").write_text("sigue el trabajo aislado", encoding="utf-8")
    negado = despachar(reg, enc, destino, "op-2", "escribir", "destino:op-2", "no", hora)
    paso(
        "sin autoridad no hay escritura nueva",
        negado.get("rechazado") is True
        and destino.consultar("destino:op-2")["estado"] == "ausente"
        and (base / "buzon-local.jsonl").exists()
        and (espacio / "nota-aislada.txt").exists(),
        negado.get("motivo", ""),
    )
    marca.unlink()
    reg.close()


def _parada_local(ensayo: Path) -> None:
    base = ensayo / "local"
    reg = Registro(base)
    hora = ahora_iso()
    enc = recibir(reg, "parada local", "msg-local", hora)["encargo"]
    primero = parada_local(base, "latch-1", hora)
    segundo = parada_local(base, "latch-2", hora)
    barrer(reg, hora, time.time(), lambda *a: ["/bin/bash", "-c", "sleep 300"], umbral_s=1)
    estado = consultar(reg, enc)
    proc = reg.fold(enc).proc
    kinds = [ev["kind"] for ev in reg.eventos(enc)]
    paso(
        "la parada local no crea otra autoridad",
        primero["recibo"] == segundo["recibo"]
        and proc is None
        and "orden" not in kinds
        and estado["estado"] == "recibido",
        primero["recibo"],
    )
    reg.close()


def _stdin(ensayo: Path) -> None:
    base = ensayo / "stdin"
    pedido = "pedido que viaja por stdin y no por el argv"
    argv = [sys.executable, "-m", "motor", "encargos-aplicar"]
    corrida = subprocess.run(
        argv,
        input=json.dumps(
            {"op": "encargar", "clave": "stdin-1", "texto": pedido, "director": "tubo"}
        ),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(RAIZ), "MOTOR_ENCARGO_BASE": str(base)},
        check=False,
    )
    datos = json.loads(corrida.stdout)
    paso(
        "stdin con comando fijo",
        corrida.returncode == 0 and datos["estado"] == "recibido" and pedido not in " ".join(argv),
        " ".join(argv),
    )


def _jaula(ensayo: Path) -> None:
    """La jaula sale de sesion.lanzar, que es el que usa el supervisor."""
    if not Path("/usr/bin/sandbox-exec").exists():
        INFORME.append(
            {
                "paso": "el lanzamiento real no escribe el destino",
                "ok": False,
                "pendiente": True,
                "detalle": "no hay sandbox-exec; este lanzamiento no queda enjaulado",
            }
        )
        print("PENDIENTE  el lanzamiento real no escribe el destino", flush=True)
        return
    base = ensayo / "jaula"
    cli = _motor("encargar", "--texto", "trabajo enjaulado", "--clave", "msg-jaula", base=base)
    if cli.returncode != 0:
        paso("el lanzamiento real no escribe el destino", False, (cli.stderr or cli.stdout)[-300:])
        return
    enc = json.loads(cli.stdout)["encargo"]
    espacio = base / "espacio" / enc
    espacio.mkdir(parents=True, exist_ok=True)
    destino = base / "destino"
    destino.mkdir(parents=True, exist_ok=True)
    trabajo = espacio / "trabajo.sh"
    trabajo.write_text(
        "#!/bin/bash\n"
        "echo si > ok.txt\n"
        f"echo no > {destino / 'x'}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    trabajo.chmod(0o755)
    corrida = subprocess.run(
        [
            sys.executable,
            "-m",
            "motor",
            "supervisor",
            "--base",
            str(base),
            "--agente",
            "ensayo",
            "--una-vez",
            "--umbral",
            "100000",
        ],
        env={**os.environ, "PYTHONPATH": str(RAIZ)},
        capture_output=True,
        text=True,
        check=False,
    )
    fin = time.time() + 5
    while time.time() < fin and not (espacio / "ok.txt").exists():
        time.sleep(0.05)
    proc_path = base / "ejecucion" / enc / "proc.json"
    aislamiento = ""
    if proc_path.exists():
        datos = json.loads(proc_path.read_text(encoding="utf-8"))
        aislamiento = str(datos.get("aislamiento") or "")
        from motor import plataforma

        plataforma.matar_proc(int(datos["pid"]), str(datos.get("inicio") or ""))
    paso(
        "el lanzamiento real no escribe el destino",
        corrida.returncode == 0
        and (espacio / "ok.txt").exists()
        and not (destino / "x").exists()
        and aislamiento == "jaula",
        f"aislamiento={aislamiento} destino={(destino / 'x').exists()} {(corrida.stderr or '')[-160:]}",
    )


def _recuperacion(ensayo: Path) -> None:
    """El supervisor hace las comprobaciones y lanza al revisor. El script no inserta esos eventos."""
    base = ensayo / "recuperacion"
    pedido = "comprobar: /no/existe/herramienta-ensayo\n"
    cli = _motor("encargar", "--texto", pedido, "--clave", "msg-rec", base=base)
    if cli.returncode != 0:
        paso("la revision tecnica arranca", False, (cli.stderr or cli.stdout)[-300:])
        return
    enc = json.loads(cli.stdout)["encargo"]
    espacio = base / "espacio" / enc
    espacio.mkdir(parents=True, exist_ok=True)
    trabajo = espacio / "trabajo.sh"
    trabajo.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    trabajo.chmod(0o755)
    for _ in range(8):
        corrida = subprocess.run(
            [
                sys.executable,
                "-m",
                "motor",
                "supervisor",
                "--base",
                str(base),
                "--agente",
                "ensayo",
                "--una-vez",
                "--umbral",
                "100000",
            ],
            env={**os.environ, "PYTHONPATH": str(RAIZ)},
            capture_output=True,
            text=True,
            check=False,
        )
        if corrida.returncode != 0:
            paso("la revision tecnica arranca", False, (corrida.stderr or corrida.stdout)[-400:])
            return
    reg = Registro(base)
    estado = reg.fold(enc)
    eventos = reg.eventos(enc)
    reg.close()
    revisores = [ev for ev in eventos if ev["kind"] == "reserva" and ev["data"].get("rol") == "revisor"]
    instrumentos = [
        ev
        for ev in eventos
        if ev["kind"] == "comprobacion" and ev["data"].get("clase") == "instrumento"
    ]
    paso(
        "la revision tecnica arranca",
        estado is not None
        and estado.estrategia == "revision_tecnica"
        and len(revisores) >= 1
        and len(instrumentos) >= 2,
        f"estrategia={estado.estrategia if estado else ''} revisores={len(revisores)} instrumentos={len(instrumentos)}",
    )


def _version() -> None:
    corrida = subprocess.run(
        [sys.executable, "-m", "motor", "version"],
        cwd=RAIZ,
        env={**os.environ, "PYTHONPATH": str(RAIZ)},
        capture_output=True,
        text=True,
        check=False,
    )
    paso("el flujo anterior sigue arrancando", corrida.returncode == 0 and "1.0.35" in corrida.stdout, corrida.stdout.strip())


def _remotos(ensayo: Path) -> None:
    for host in ("mini", "pc"):
        prueba = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, "echo", "listo"],
            capture_output=True,
            text=True,
            check=False,
        )
        if prueba.returncode != 0 or "listo" not in prueba.stdout:
            INFORME.append(
                {
                    "paso": f"transporte en {host}",
                    "ok": False,
                    "detalle": "pendiente: el host no respondió",
                    "pendiente": True,
                }
            )
            print(f"PENDIENTE  transporte en {host}", flush=True)
            continue
        _remoto(host, ensayo)


def _remoto(host: str, ensayo: Path) -> None:
    remoto = "/tmp/encargos-1-2-src"
    base = "/tmp/encargos-1-2-base"
    if host == "pc":
        remoto = "$env:TEMP\\encargos-1-2-src"
        base = "$env:TEMP\\encargos-1-2-base"
    # La copia y la corrida se resuelven en el host. El pedido no va en el argv.
    sincroniza = subprocess.run(
        ["rsync", "-a", "--delete", "--exclude", ".git", f"{RAIZ}/", f"{host}:{remoto}/"],
        capture_output=True,
        text=True,
        check=False,
    )
    if host == "pc" or sincroniza.returncode != 0:
        _remoto_tar(host, ensayo)
        return
    py = _interprete(host)
    if not py:
        INFORME.append(
            {
                "paso": f"proceso y stdin en {host}",
                "ok": False,
                "detalle": "pendiente: no hay Python con tomllib",
                "pendiente": True,
            }
        )
        print(f"PENDIENTE  {host}", flush=True)
        return
    corrida = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            host,
            "--",
            py,
            f"{remoto}/ops/ensayo_encargos.py",
            "--remoto-local",
            base,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=90,
    )
    completo = (corrida.stdout or "") + (corrida.stderr or "")
    paso(
        f"proceso y stdin en {host}",
        corrida.returncode == 0 and '"hijo_muerto": true' in completo and '"os": "posix"' in completo,
        completo[-500:],
    )


def _remoto_tar(host: str, ensayo: Path) -> None:
    paquete = ensayo / "src.tgz"
    subprocess.run(
        ["tar", "-czf", str(paquete), "--exclude", ".git", "-C", str(RAIZ.parent), RAIZ.name],
        check=True,
    )
    if host == "pc":
        _remoto_windows(paquete)
        return
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, "rm", "-rf", "/tmp/encargos-1-2-src", "/tmp/encargos-1-2-base"],
        check=False,
    )
    subida = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, "tar", "-xzf", "-", "-C", "/tmp"],
        input=paquete.read_bytes(),
        capture_output=True,
        check=False,
    )
    if subida.returncode != 0:
        INFORME.append({"paso": f"proceso y stdin en {host}", "ok": False, "detalle": subida.stderr[-200:], "pendiente": True})
        print(f"PENDIENTE  {host}: {subida.stderr[-120:]}", flush=True)
        return
    py = _interprete(host)
    if not py:
        INFORME.append(
            {
                "paso": f"proceso y stdin en {host}",
                "ok": False,
                "detalle": "pendiente: no hay Python con tomllib",
                "pendiente": True,
            }
        )
        print(f"PENDIENTE  {host}", flush=True)
        return
    corrida = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            host,
            "--",
            py,
            f"/tmp/{RAIZ.name}/ops/ensayo_encargos.py",
            "--remoto-local",
            "/tmp/encargos-1-2-base",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=90,
    )
    completo = (corrida.stdout or "") + (corrida.stderr or "")
    paso(
        f"proceso y stdin en {host}",
        corrida.returncode == 0 and '"hijo_muerto": true' in completo and '"os": "posix"' in completo,
        completo[-500:],
    )


def _interprete(host: str) -> str:
    if host == "pc":
        return "py -3"
    for candidato in ("/Users/gon/.local/bin/python3", "python3"):
        prueba = _ssh(host, f"{candidato} -c 'import tomllib'")
        if prueba.returncode == 0:
            return candidato
    return ""


def _ssh(host: str, *args: str, entrada: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, *args],
        input=entrada,
        capture_output=True,
        text=entrada is None,
        encoding=None if entrada is not None else "utf-8",
        errors=None if entrada is not None else "replace",
        check=False,
    )


def _remoto_windows(paquete: Path) -> None:
    nombre = RAIZ.name
    # cmd.exe trata & y && dentro de las comillas del servidor ssh como texto.
    # Cada paso es un solo comando, y el directorio lo crea Python.
    prep_code = (
        "import os,pathlib,shutil; "
        "t=pathlib.Path(os.environ['TEMP']); "
        "src=t/'encargos-1-2-src'; base=t/'encargos-1-2-base'; "
        "shutil.rmtree(src, ignore_errors=True); shutil.rmtree(base, ignore_errors=True); "
        "src.mkdir(); print(src)"
    )
    prep = _ssh("pc", f'py -3 -c "{prep_code}"')
    if prep.returncode != 0:
        paso("proceso y stdin en pc", False, (prep.stdout or "") + (prep.stderr or ""))
        return
    subida = _ssh("pc", r"tar -x -C %TEMP%\encargos-1-2-src", entrada=paquete.read_bytes())
    if subida.returncode != 0:
        detalle = subida.stderr
        if isinstance(detalle, bytes):
            detalle = detalle.decode("utf-8", "replace")
        paso("proceso y stdin en pc", False, f"copia {detalle[-240:]}")
        return
    corrida = _ssh(
        "pc",
        "py -3 -X utf8 "
        r'"%TEMP%\encargos-1-2-src'
        "\\"
        + nombre
        + r'\ops\ensayo_encargos.py" --remoto-local %TEMP%\encargos-1-2-base',
    )
    completo = (corrida.stdout or "") + (corrida.stderr or "")
    paso(
        "proceso y stdin en pc",
        corrida.returncode == 0 and '"hijo_muerto": true' in completo and '"os": "nt"' in completo,
        completo[-800:],
    )


def _agente(ensayo: Path) -> None:
    if os.environ.get("MOTOR_ENSAYO_SIN_AGENTE"):
        INFORME.append({"paso": "agente real por el supervisor", "ok": False, "detalle": "omitido", "pendiente": True})
        return
    base = ensayo / "agente"
    pedido = (
        "Corrige suma.py para que sumar devuelva la suma de los dos números.\n"
        'comprobar: python3 -c "import suma; assert suma.sumar(2, 3) == 5"\n'
        "No declares el encargo terminado y no abras otras carpetas."
    )
    cli = _motor(
        "encargar",
        "--texto",
        pedido,
        "--clave",
        "msg-agente",
        "--restriccion",
        "el cambio tiene que ser reversible",
        base=base,
    )
    if cli.returncode != 0:
        paso("agente real por el supervisor", False, (cli.stderr or cli.stdout)[-300:])
        return
    enc = json.loads(cli.stdout)["encargo"]
    espacio = base / "espacio" / enc
    espacio.mkdir(parents=True, exist_ok=True)
    (espacio / "suma.py").write_text("def sumar(a, b):\n    return a - b\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=espacio, check=True)
    subprocess.run(["git", "add", "suma.py"], cwd=espacio, check=True)
    subprocess.run(
        ["git", "-c", "user.email=ensayo@local", "-c", "user.name=ensayo", "commit", "-qm", "base"],
        cwd=espacio,
        check=True,
    )
    fin = time.time() + 600
    vista: dict = {}
    while time.time() < fin:
        corrida = subprocess.run(
            [
                sys.executable,
                "-m",
                "motor",
                "supervisor",
                "--base",
                str(base),
                "--agente",
                "grok",
                "--una-vez",
                "--umbral",
                "600",
            ],
            env={**os.environ, "PYTHONPATH": str(RAIZ)},
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        (ensayo / "agente-grok.txt").write_text(
            (corrida.stdout or "")[-2000:] + "\n" + (corrida.stderr or "")[-1000:],
            encoding="utf-8",
        )
        ver = _motor("ver", enc, base=base)
        if ver.returncode != 0:
            vista = {"estado": "error", "detalle": ver.stderr[-200:]}
            break
        vista = json.loads(ver.stdout)
        if vista.get("estado") == "terminado":
            break
        time.sleep(1)
    fuente = (espacio / "suma.py").read_text(encoding="utf-8") if (espacio / "suma.py").exists() else ""
    reg = Registro(base)
    eventos = reg.eventos(enc)
    reg.close()
    from motor import plataforma

    for marca in (base / "ejecucion").glob("*/proc.json"):
        datos = json.loads(marca.read_text(encoding="utf-8"))
        plataforma.matar_proc(int(datos["pid"]), str(datos.get("inicio") or ""))
        plataforma.cerrar_huerfanos(
            int(datos["pid"]), str(datos.get("inicio") or ""), str(datos.get("job") or "")
        )
    confirmado = any(
        ev["kind"] == "comprobacion" and ev["data"].get("clase") == "confirmado" for ev in eventos
    )
    paso(
        "agente real por el supervisor",
        vista.get("estado") == "terminado" and "a + b" in fuente and confirmado,
        f"estado={vista.get('estado')} linea={fuente.strip().splitlines()[-1:] } confirmado={confirmado}",
    )


def _en_mini(py: str, args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "mini",
            "--",
            "env",
            "PYTHONPATH=/tmp/encargos-aut-src",
            py,
            "-m",
            "motor",
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )


def _linea_autoridad(texto: str) -> dict | None:
    for linea in reversed(texto.splitlines()):
        if linea.startswith("AUTORIDAD "):
            return json.loads(linea[len("AUTORIDAD ") :])
    return None


def _pc_autoridad(nombre: str, modo: str, *args: str) -> subprocess.CompletedProcess:
    script = r'"%TEMP%\encargos-aut-src' + "\\" + nombre + r'\ops\ensayo_encargos.py"'
    return _ssh(
        "pc",
        "py -3 -X utf8 " + script + " --desde-pc " + modo + " " + " ".join(args),
    )


def _autoridad(ensayo: Path) -> None:
    """La Mini guarda el registro. La PC pide el efecto y no se queda con el destino."""
    mini = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "mini", "echo", "listo"],
        capture_output=True,
        text=True,
        check=False,
    )
    pc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "pc", "echo", "listo"],
        capture_output=True,
        text=True,
        check=False,
    )
    if mini.returncode != 0 or "listo" not in mini.stdout:
        INFORME.append(
            {
                "paso": "autoridad en la mini",
                "ok": False,
                "pendiente": True,
                "detalle": "pendiente: la mini no respondió",
            }
        )
        print("PENDIENTE  autoridad en la mini", flush=True)
        return
    if pc.returncode != 0 or "listo" not in pc.stdout:
        INFORME.append(
            {
                "paso": "autoridad en la mini",
                "ok": False,
                "pendiente": True,
                "detalle": "pendiente: la pc no respondió",
            }
        )
        print("PENDIENTE  autoridad en la mini", flush=True)
        return
    py = _interprete("mini")
    if not py:
        INFORME.append(
            {
                "paso": "autoridad en la mini",
                "ok": False,
                "pendiente": True,
                "detalle": "pendiente: la mini no tiene Python con tomllib",
            }
        )
        print("PENDIENTE  autoridad en la mini", flush=True)
        return
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "mini", "rm", "-rf", "/tmp/encargos-aut-src", "/tmp/encargos-aut-base"],
        check=False,
    )
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "mini", "mkdir", "-p", "/tmp/encargos-aut-src", "/tmp/encargos-aut-base"],
        check=False,
    )
    sincroniza = subprocess.run(
        ["rsync", "-a", "--delete", "--exclude", ".git", f"{RAIZ}/", "mini:/tmp/encargos-aut-src/"],
        capture_output=True,
        text=True,
        check=False,
    )
    if sincroniza.returncode != 0:
        paso("autoridad en la mini", False, (sincroniza.stderr or "")[-240:])
        return
    base = "/tmp/encargos-aut-base"
    alta_a = _en_mini(py, ["encargar", "--base", base, "--clave", "msg-a", "--texto", "recurso-comun"])
    if alta_a.returncode != 0:
        paso("autoridad en la mini", False, alta_a.stderr[-300:])
        return
    enc_a = json.loads(alta_a.stdout)["encargo"]
    supervisor = _en_mini(
        py,
        ["supervisor", "--base", base, "--agente", "sleep", "--una-vez", "--umbral", "100000"],
        timeout=40,
    )
    if supervisor.returncode != 0:
        paso("autoridad en la mini", False, supervisor.stderr[-300:])
        return
    efecto_a = _en_mini(
        py,
        [
            "efecto",
            "--base",
            base,
            "--encargo",
            enc_a,
            "--id",
            "op-a",
            "--operacion",
            "escribir",
            "--clave-destino",
            "comun",
            "--cuerpo",
            "desde-mini",
        ],
    )
    alta_b = _en_mini(py, ["encargar", "--base", base, "--clave", "msg-b", "--texto", "otro-encargo"])
    if efecto_a.returncode != 0 or alta_b.returncode != 0:
        paso("autoridad en la mini", False, (efecto_a.stderr + alta_b.stderr)[-300:])
        return
    enc_b = json.loads(alta_b.stdout)["encargo"]
    paquete = ensayo / "aut-src.tgz"
    subprocess.run(
        ["tar", "-czf", str(paquete), "--exclude", ".git", "-C", str(RAIZ.parent), RAIZ.name],
        check=True,
    )
    prep_code = (
        "import os,pathlib,shutil; "
        "t=pathlib.Path(os.environ['TEMP']); "
        "src=t/'encargos-aut-src'; buz=t/'encargos-aut-buzon'; "
        "shutil.rmtree(src, ignore_errors=True); shutil.rmtree(buz, ignore_errors=True); "
        "src.mkdir(); buz.mkdir(); print('prep-ok')"
    )
    prep = _ssh("pc", f'py -3 -c "{prep_code}"')
    prep_txt = (prep.stdout or "") + (prep.stderr or "")
    if isinstance(prep_txt, bytes):
        prep_txt = prep_txt.decode("utf-8", "replace")
    if prep.returncode != 0 or "prep-ok" not in prep_txt:
        paso("autoridad en la mini", False, prep_txt[-300:])
        return
    subida = _ssh("pc", r"tar -x -C %TEMP%\encargos-aut-src", entrada=paquete.read_bytes())
    if subida.returncode != 0:
        detalle = subida.stderr
        if isinstance(detalle, bytes):
            detalle = detalle.decode("utf-8", "replace")
        paso("autoridad en la mini", False, detalle[-240:])
        return
    nombre = RAIZ.name
    host = "gon@100.73.187.5"
    ocupado = _pc_autoridad(nombre, "aplicar", host, py, "/tmp/encargos-aut-src", base, enc_b, "op-b", "comun")
    ocupado_txt = (ocupado.stdout or "") + (ocupado.stderr or "")
    if isinstance(ocupado_txt, bytes):
        ocupado_txt = ocupado_txt.decode("utf-8", "replace")
    dato_ocupado = _linea_autoridad(ocupado_txt) or {}
    parado = _en_mini(py, ["parar", enc_a, "--base", base, "--clave", "stop-a"])
    libre = _pc_autoridad(nombre, "aplicar", host, py, "/tmp/encargos-aut-src", base, enc_b, "op-b", "comun")
    libre_txt = (libre.stdout or "") + (libre.stderr or "")
    if isinstance(libre_txt, bytes):
        libre_txt = libre_txt.decode("utf-8", "replace")
    dato_libre = _linea_autoridad(libre_txt) or {}
    caida = _pc_autoridad(nombre, "caer", host, "no-salio")
    caida_txt = (caida.stdout or "") + (caida.stderr or "")
    if isinstance(caida_txt, bytes):
        caida_txt = caida_txt.decode("utf-8", "replace")
    dato_caida = _linea_autoridad(caida_txt) or {}
    nuevo = _pc_autoridad(
        nombre, "aplicar", host, py, "/tmp/encargos-aut-src", base, enc_b, "op-nuevo", "recurso-nuevo"
    )
    nuevo_txt = (nuevo.stdout or "") + (nuevo.stderr or "")
    if isinstance(nuevo_txt, bytes):
        nuevo_txt = nuevo_txt.decode("utf-8", "replace")
    dato_nuevo = _linea_autoridad(nuevo_txt) or {}
    repetido = _pc_autoridad(
        nombre, "aplicar", host, py, "/tmp/encargos-aut-src", base, enc_b, "op-nuevo", "recurso-nuevo"
    )
    repetido_txt = (repetido.stdout or "") + (repetido.stderr or "")
    if isinstance(repetido_txt, bytes):
        repetido_txt = repetido_txt.decode("utf-8", "replace")
    dato_repetido = _linea_autoridad(repetido_txt) or {}
    import hashlib

    def _remoto_json(clave: str) -> str:
        nombre_archivo = hashlib.sha256(clave.encode()).hexdigest()[:24]
        visto = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "mini", "cat", f"{base}/destino/{nombre_archivo}.json"],
            capture_output=True,
            text=True,
            check=False,
        )
        return (visto.stdout or "") + (visto.stderr or "")

    comun = _remoto_json("comun")
    no_salio = _remoto_json("no-salio")
    recurso = _remoto_json("recurso-nuevo")
    salida_ocupada = dato_ocupado.get("salida") or {}
    salida_libre = dato_libre.get("salida") or {}
    salida_nueva = dato_nuevo.get("salida") or {}
    salida_repetida = dato_repetido.get("salida") or {}
    ok = (
        dato_ocupado.get("alcanzada") is True
        and salida_ocupada.get("rechazado") is True
        and parado.returncode == 0
        and "detenido" in parado.stdout
        and dato_libre.get("alcanzada") is True
        and (salida_libre.get("conciliado") is True or salida_libre.get("ya_aplicado") is True)
        and '"n": 1' in comun
        and dato_caida.get("alcanzada") is False
        and dato_caida.get("buzon") is True
        and "No such file" in no_salio
        and dato_nuevo.get("alcanzada") is True
        and salida_nueva.get("n") == 1
        and (salida_repetida.get("ya_aplicado") is True or salida_repetida.get("conciliado") is True)
        and '"n": 1' in recurso
        and "nota-local.txt" in (dato_nuevo.get("archivos") or [])
        and not any(str(nombre).endswith(".json") for nombre in (dato_nuevo.get("archivos") or []))
    )
    paso(
        "autoridad en la mini",
        ok,
        json.dumps(
            {
                "ocupado": dato_ocupado,
                "libre": dato_libre,
                "caida": dato_caida,
                "nuevo": dato_nuevo,
                "repetido": dato_repetido,
                "parada": parado.stdout[-180:],
            },
            ensure_ascii=False,
        )[-800:],
    )
    INFORME.append(
        {
            "paso": "aislamiento en windows",
            "ok": False,
            "pendiente": True,
            "detalle": (
                "la PC no tiene una jaula equivalente a sandbox-exec; "
                "el trabajador puede escribir su propio disco; "
                "el destino de este ensayo quedó en la Mini"
            ),
        }
    )
    print("PENDIENTE  aislamiento en windows: sin jaula, el destino externo quedó en la Mini", flush=True)


def desde_pc(argv: list[str]) -> int:
    """Corre en la PC. Habla con la Mini y no abre un registro canónico."""
    from motor.encargos.transporte import enviar

    modo = argv[0]
    buzon = Path(os.environ.get("TEMP") or ".") / "encargos-aut-buzon"
    buzon.mkdir(parents=True, exist_ok=True)
    if modo == "caer":
        host, clave = argv[1], argv[2]
        resultado = enviar(
            host,
            ["--", "echo", "no-debe-correr"],
            {"op": "efecto", "clave_destino": clave, "cuerpo": "no"},
            buzon,
            puerto=1,
            timeout=12,
        )
        print(
            "AUTORIDAD "
            + json.dumps(
                {
                    "alcanzada": resultado.get("alcanzada"),
                    "buzon": (buzon / "buzon-local.jsonl").exists(),
                    "archivos": sorted(p.name for p in buzon.iterdir()),
                }
            )
        )
        return 0
    host, py, src, base, encargo, efecto, clave = argv[1:8]
    nota = buzon / "nota-local.txt"
    nota.write_text("trabajo en la pc", encoding="utf-8")
    resultado = enviar(
        host,
        [
            "--",
            "env",
            f"PYTHONPATH={src}",
            f"MOTOR_ENCARGO_BASE={base}",
            py,
            "-m",
            "motor",
            "encargos-aplicar",
        ],
        {
            "op": "efecto",
            "base": base,
            "encargo": encargo,
            "id": efecto,
            "operacion": "escribir",
            "clave_destino": clave,
            "cuerpo": "desde-pc",
        },
        buzon,
        timeout=40,
    )
    print(
        "AUTORIDAD "
        + json.dumps(
            {
                "alcanzada": resultado.get("alcanzada"),
                "salida": resultado.get("salida"),
                "motivo": resultado.get("motivo"),
                "archivos": sorted(p.name for p in buzon.iterdir()),
            },
            ensure_ascii=False,
        )
    )
    return 0 if resultado.get("alcanzada") else 1


def remoto_local(base: Path) -> int:
    """Corre en el host remoto: árbol de procesos y una entrega por stdin."""
    os.environ["MOTOR_ENSAYO_INFORME"] = str(base.parent / "informe-remoto.json")
    base.mkdir(parents=True, exist_ok=True)
    from motor import plataforma

    log = base / "log.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        codigo = (
            "import pathlib,subprocess,sys,time\n"
            "hijo=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)'])\n"
            "pathlib.Path('hijo.pid').write_text(str(hijo.pid),encoding='ascii')\n"
            "time.sleep(300)\n"
        )
        comando = [sys.executable, "-c", codigo]
    else:
        comando = ["/bin/bash", "-c", "sleep 300 & echo $! > hijo.pid; sleep 300"]
    proc = plataforma.lanzar_separado(comando, base, os.environ.copy(), log)
    hijo_path = base / "hijo.pid"
    hijo = 0
    for _ in range(40):
        if hijo_path.exists() and hijo_path.read_text(encoding="utf-8").strip():
            hijo = int(hijo_path.read_text(encoding="utf-8").strip())
            break
        time.sleep(0.1)
    vivo = plataforma.estado_proc(proc) == "vivo"
    plataforma.matar_proc(int(proc["pid"]), str(proc.get("inicio") or ""))
    time.sleep(0.4)
    hijo_muerto = hijo > 0 and not plataforma.pid_vivo(hijo)
    ausente = observar(base, proc) in ("ausente", "muerto") or not plataforma.pid_vivo(int(proc["pid"]))
    pedido = "pedido remoto de ensayo"
    argv = [sys.executable, "-m", "motor", "encargos-aplicar"]
    corrida = subprocess.run(
        argv,
        input=json.dumps({"op": "encargar", "clave": "remoto-1", "texto": pedido}),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(RAIZ), "MOTOR_ENCARGO_BASE": str(base / "reg")},
        check=False,
    )
    ok = vivo and hijo_muerto and corrida.returncode == 0 and pedido not in " ".join(argv)
    paso(
        "árbol y stdin locales del host",
        ok,
        json.dumps(
            {
                "vivo": vivo,
                "hijo": hijo,
                "hijo_muerto": hijo_muerto,
                "ausente": ausente,
                "os": os.name,
                "cli": corrida.returncode,
                "out": corrida.stdout[-180:],
            }
        ),
    )
    return _cerrar(0 if ok else 1)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--remoto-local":
        raise SystemExit(remoto_local(Path(sys.argv[2])))
    if len(sys.argv) >= 3 and sys.argv[1] == "--desde-pc":
        raise SystemExit(desde_pc(sys.argv[2:]))
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        INFORME.append({"paso": "excepcion", "ok": False, "detalle": f"{type(exc).__name__}: {exc}"})
        if "MOTOR_ENSAYO_INFORME" in os.environ:
            raise SystemExit(_cerrar(1)) from exc
        raise
