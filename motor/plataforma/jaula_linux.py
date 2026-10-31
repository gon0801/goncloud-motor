"""Jaula de Linux con bubblewrap, la misma que concede la de Windows.

Solo stdlib y sin importar nada del motor: en la PC este archivo viaja como fuente (`-c`) a
la distro de WSL, igual que `GUARDIAN_WSL`, y allí no hay ni el motor ni su entorno.

La raíz del agente es un tmpfs de solo lectura. Entra lo que hace falta para correr
(`/usr`, `/etc`, el ejecutable del agente) y lo que el encargo concede:

- escritura: el espacio del encargo y su runtime. El revisor monta el espacio de solo lectura;
- lectura: las fuentes autorizadas;
- nada del home real ni de `/mnt/c`: no se monta, no existe dentro.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

BWRAP = "/usr/bin/bwrap"
RED_ABIERTA = "abierta"
RED_NINGUNA = "ninguna"
RED_PROVEEDOR = "proveedor"
PATH_JAULA = "/usr/local/bin:/usr/bin:/bin"
DIRECTORIOS_DEL_SISTEMA = ("bin", "sbin", "lib", "lib32", "lib64", "libx32")
EGRESO_EN_LA_JAULA = "/run/egreso"
PUERTO_EGRESO = 3128


@dataclass(frozen=True)
class Jaula:
    """`red` es `abierta`, `ninguna` o `proveedor`: sin red propia, solo con una salida a los
    `dominios` del proveedor por el proxy del guardián, cuyo socket está en el directorio
    `egreso` del anfitrión (lo crea el guardián)."""

    espacio: str
    runtime: str
    lectura: bool = False
    lecturas: tuple = ()
    ejecutables: tuple = ()
    red: str = RED_ABIERTA
    dominios: tuple = ()
    egreso: str = ""


def _absoluta(ruta: str) -> str:
    if not ruta.startswith("/"):
        raise ValueError(f"la jaula necesita rutas absolutas: {ruta!r}")
    return os.path.normpath(ruta)


def _sistema() -> list:
    """Lo que el agente necesita para correr, de solo lectura. Sigue al anfitrión: en un
    Ubuntu con `/usr` unificado `/bin` y `/lib` son enlaces; en otro, directorios."""
    montajes = ["--ro-bind", "/usr", "/usr"]
    for nombre in DIRECTORIOS_DEL_SISTEMA:
        ruta = "/" + nombre
        if os.path.islink(ruta):
            montajes += ["--symlink", os.readlink(ruta), ruta]
        elif os.path.isdir(ruta):
            montajes += ["--ro-bind", ruta, ruta]
    return montajes + _etc()


def _etc(origen: str = "/etc") -> list:
    """`/etc` de solo lectura, entrada por entrada. En WSL `resolv.conf` es un enlace a
    `/mnt/wsl/resolv.conf`; montar `/etc` entero lo deja colgando, y montar su destino donde
    está haría existir `/mnt` en la jaula. Se monta el archivo real sobre `/etc/resolv.conf`."""
    montajes = ["--dir", "/etc"]
    for nombre in sorted(os.listdir(origen)):
        ruta, destino = os.path.join(origen, nombre), "/etc/" + nombre
        if nombre == "resolv.conf" and os.path.islink(ruta):
            real = os.path.realpath(ruta)
            if os.path.isfile(real):
                montajes += ["--ro-bind", real, destino]
        elif os.path.islink(ruta):
            montajes += ["--symlink", os.readlink(ruta), destino]
        elif os.path.isdir(ruta) or os.path.isfile(ruta):
            montajes += ["--ro-bind", ruta, destino]
    return montajes


def _ejecutable(ruta: str) -> list:
    """Un enlace se monta como enlace y su destino de solo lectura: el agente ve la ruta
    que le dieron sin que entre el directorio que la contiene."""
    real = os.path.realpath(ruta)
    if real.startswith("/usr/") and ruta.startswith("/usr/"):
        return []
    montajes = ["--ro-bind", real, real]
    if real != ruta:
        montajes += ["--symlink", real, ruta]
    return montajes


def argv_jaula(
    jaula: Jaula, cmd: list, *, cwd: str | None = None, bwrap: str = BWRAP
) -> list:
    """El argv que corre `cmd` dentro de la jaula. Lo arma y no lo ejecuta.

    `--remount-ro /` no es recursivo: un tmpfs montado debajo (`/tmp`) seguiría con
    escritura y bwrap crea ahí los padres de cada bind. Por eso no hay `--tmpfs`: el
    temporal del agente es `TMPDIR`, dentro del runtime."""
    espacio, runtime = _absoluta(jaula.espacio), _absoluta(jaula.runtime)
    if jaula.red not in (RED_ABIERTA, RED_NINGUNA, RED_PROVEEDOR):
        raise ValueError(f"red desconocida: {jaula.red!r}")
    if jaula.red == RED_PROVEEDOR and not (jaula.dominios and jaula.egreso):
        raise ValueError("la red del proveedor necesita sus dominios y el socket de salida")
    if runtime == espacio or runtime.startswith(espacio + "/"):
        raise ValueError("el runtime no puede estar dentro del espacio")
    argv = [bwrap, "--die-with-parent", "--new-session", "--unshare-all"]
    if jaula.red == RED_ABIERTA:
        argv.append("--share-net")
    argv += _sistema()
    argv += ["--proc", "/proc", "--dev", "/dev"]
    for ruta in jaula.ejecutables:
        argv += _ejecutable(_absoluta(ruta))
    for ruta in jaula.lecturas:
        ruta = _absoluta(ruta)
        argv += ["--ro-bind", ruta, ruta]
    argv += ["--bind", runtime, runtime]
    argv += ["--ro-bind" if jaula.lectura else "--bind", espacio, espacio]
    cmd = list(cmd)
    if jaula.red == RED_PROVEEDOR:
        argv += ["--bind", _absoluta(jaula.egreso), EGRESO_EN_LA_JAULA]
        cmd = [
            "/usr/bin/python3", "-I", "-S", "-c", _PUENTE,
            EGRESO_EN_LA_JAULA + "/s", str(PUERTO_EGRESO), "--", *cmd,
        ]
    argv += ["--remount-ro", "/", "--chdir", _absoluta(cwd or espacio), "--"]
    return argv + cmd


def entorno_jaula(jaula: Jaula, extra: dict | None = None) -> dict:
    """Entorno mínimo: nada heredado del anfitrión. El home del agente es su runtime."""
    runtime = _absoluta(jaula.runtime)
    temporal = runtime + "/tmp"
    entorno = {
        "PATH": PATH_JAULA,
        "HOME": runtime,
        "TMPDIR": temporal,
        "TEMP": temporal,
        "TMP": temporal,
        "XDG_CONFIG_HOME": runtime + "/.config",
        "XDG_DATA_HOME": runtime + "/.local/share",
        "LANG": "C.UTF-8",
        "TERM": "dumb",
    }
    if jaula.red == RED_PROVEEDOR:
        proxy = f"http://127.0.0.1:{PUERTO_EGRESO}"
        for nombre in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            entorno[nombre] = proxy
        entorno["NO_PROXY"] = entorno["no_proxy"] = "127.0.0.1,localhost"
    entorno.update(extra or {})
    return entorno


# Dentro de la jaula (sin red propia): escucha en 127.0.0.1 y pasa cada conexión por el socket
# unix del guardián, que decide a dónde se sale. Se lanza antes del agente y no lo sobrevive.
_PUENTE = r"""
import os, select, socket, sys, threading
ruta, puerto, cmd = sys.argv[1], int(sys.argv[2]), sys.argv[4:]
escucha = socket.socket()
escucha.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
escucha.bind(("127.0.0.1", puerto))
escucha.listen(64)
def pasar(a, b):
    try:
        while True:
            for s in select.select([a, b], [], [], 3600)[0]:
                d = s.recv(65536)
                if not d:
                    return
                (b if s is a else a).sendall(d)
    except (OSError, ValueError):
        pass
    finally:
        a.close(); b.close()
def servir(c):
    u = socket.socket(socket.AF_UNIX)
    try:
        u.connect(ruta)
    except OSError:
        c.close(); return
    pasar(c, u)
if os.fork() == 0:
    while True:
        threading.Thread(target=servir, args=(escucha.accept()[0],), daemon=True).start()
escucha.close()
os.execvp(cmd[0], cmd)
"""


def _retransmitir(a, b) -> None:
    import select

    try:
        while True:
            for s in select.select([a, b], [], [], 3600)[0]:
                datos = s.recv(65536)
                if not datos:
                    return
                (b if s is a else a).sendall(datos)
    except (OSError, ValueError):
        pass
    finally:
        a.close()
        b.close()


def _es_global(ip: str) -> bool:
    import ipaddress

    return ipaddress.ip_address(ip).is_global


def _atender_egreso(cliente, dominios, resolver) -> None:
    """Un `CONNECT dominio:443` a un dominio del proveedor, a una dirección pública que se
    resuelve aquí y a la que se conecta tal cual (sin segunda resolución). Todo lo demás se
    rechaza: la jaula no tiene otra salida."""
    import socket

    try:
        cliente.settimeout(30)
        cabecera = b""
        while b"\r\n\r\n" not in cabecera and len(cabecera) < 8192:
            trozo = cliente.recv(4096)
            if not trozo:
                return cliente.close()
            cabecera += trozo
        linea = cabecera.split(b"\r\n", 1)[0].decode("latin-1").split()
        if len(linea) < 2 or linea[0] != "CONNECT":
            return _responder(cliente, b"405 Method Not Allowed")
        host, _, puerto = linea[1].rpartition(":")
        if puerto != "443" or host.lower().rstrip(".") not in dominios:
            return _responder(cliente, b"403 Forbidden")
        destino = None
        for *_resto, direccion in resolver(host, 443, type=socket.SOCK_STREAM):
            if _es_global(direccion[0]):
                try:
                    destino = socket.create_connection((direccion[0], 443), 15)
                    break
                except OSError:
                    continue
        if destino is None:
            return _responder(cliente, b"502 Bad Gateway")
        try:
            cliente.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            resto = cabecera.split(b"\r\n\r\n", 1)[1]
            if resto:
                destino.sendall(resto)
        except OSError:
            destino.close()
            raise
        cliente.settimeout(None)
        _retransmitir(cliente, destino)
    except (OSError, ValueError):
        cliente.close()


def _responder(cliente, estado: bytes) -> None:
    try:
        cliente.sendall(b"HTTP/1.1 " + estado + b"\r\n\r\n")
    except OSError:
        pass
    cliente.close()


def servir_egreso(ruta: str, dominios, resolver=None):
    """El proxy de salida de la jaula, en un socket unix en `ruta`. Devuelve el servidor:
    `shutdown()` y `server_close()` lo paran."""
    import socket
    import socketserver
    import threading

    permitidos = frozenset(d.lower() for d in dominios)
    resolver = resolver or socket.getaddrinfo

    class Manejador(socketserver.BaseRequestHandler):
        def handle(self):
            _atender_egreso(self.request, permitidos, resolver)

    class Servidor(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    servidor = Servidor(ruta, Manejador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor


def _descendientes(raiz: int) -> list:
    """Pids bajo `raiz`, vistos desde el anfitrión, por `/proc/<pid>/stat`."""
    padres = {}
    for nombre in os.listdir("/proc"):
        if not nombre.isdigit():
            continue
        try:
            with open(f"/proc/{nombre}/stat", "rb") as f:
                resto = f.read().rsplit(b")", 1)[1].split()
            padres[int(nombre)] = int(resto[1])
        except (OSError, IndexError, ValueError):
            continue
    vistos, frontera = [], [raiz]
    while frontera:
        hijos = [p for p, padre in padres.items() if padre in frontera and p not in vistos]
        vistos += hijos
        frontera = hijos
    return vistos


def _vivo(pid: int) -> bool:
    """Un zombi ya murió: solo espera que su padre lo recoja."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rsplit(b")", 1)[1].split()[0] != b"Z"
    except (OSError, IndexError):
        return False


def _ns_pid(pid: int) -> str:
    """Identidad del espacio de nombres de pids de `pid` (`pid:[4026532...]`), o vacío si no
    existe o no se deja leer. Un zombi ya no tiene espacio de nombres."""
    try:
        return os.readlink(f"/proc/{pid}/ns/pid")
    except OSError:
        return ""


def _en_ns(ns: str) -> list:
    """Pids del anfitrión que viven en el espacio de nombres `ns`, cuelguen de quien cuelguen.
    Un zombi no cuenta: sigue en el espacio de nombres (su `struct pid` lo conserva) hasta que
    su padre lo recoja, y el guardián, que es subreaper, solo recoge al bwrap. Contarlo
    dejaría la jaula siempre sin cerrar."""
    if not ns:
        return []
    return [
        int(n) for n in os.listdir("/proc")
        if n.isdigit() and _ns_pid(int(n)) == ns and _vivo(int(n))
    ]


def init_de_la_jaula(bwrap: int) -> int:
    """El primer descendiente de `bwrap` que no comparte nuestro espacio de nombres de pids: el
    init de la jaula. 0 mientras no haya nacido o si no se puede leer."""
    propio = _ns_pid(os.getpid())
    for p in _descendientes(bwrap):
        ns = _ns_pid(p)
        if ns and ns != propio:
            return p
    return 0


def ns_de_la_jaula(bwrap: int) -> str:
    """El espacio de nombres de pids de la jaula que lanzó `bwrap`, o vacío."""
    init = init_de_la_jaula(bwrap)
    return _ns_pid(init) if init else ""


def _ns_vigente(identidad: dict) -> str:
    """El `ns_pid` anotado, solo mientras siga vivo el init de esa jaula con su hora de
    arranque. Los números de espacio de nombres se reusan: muerto el init, el kernel ya mató
    a todo el espacio y el número puede ser de otra jaula, que no se toca."""
    ns, init = identidad.get("ns_pid") or "", int(identidad.get("ns_init") or 0)
    if ns and init and _inicio(init) == identidad.get("ns_init_inicio") and _vivo(init):
        return ns
    return ""


def _congelar(pid: int, vistos: dict, ns: str = "") -> None:
    """SIGSTOP a `pid` (si es de verdad el bwrap), a todo lo que cuelga de él y a todo lo que
    vive en el espacio de nombres `ns`, hasta que un recorrido no encuentre nada nuevo. Un
    proceso detenido no puede hacer fork: sin esto, un hijo nacido entre el recorrido y el
    SIGKILL quedaría huérfano, fuera del árbol, y nadie lo vería."""
    import signal

    for _ in range(16):
        candidatos = [*((pid, *_descendientes(pid)) if pid else ()), *_en_ns(ns)]
        nuevos = [p for p in dict.fromkeys(candidatos) if p not in vistos]
        if not nuevos:
            return
        for p in nuevos:
            vistos[p] = _inicio(p)
            try:
                os.kill(p, signal.SIGSTOP)
            except OSError:
                pass


def cerrar(pid: int, plazo: float = 5.0, inicio: str | None = None, ns: str = "") -> bool:
    """Mata la jaula que lanzó el bwrap `pid` y espera a que no quede nada. True si no queda.

    No basta con matar al bwrap exterior: con el bubblewrap 0.9 de Ubuntu 24.04 la jaula
    sigue viva tras su SIGKILL (`--die-with-parent` no es una garantía; CI lo midió). Se
    congela el árbol entero, se anota la identidad (pid y hora de arranque) de cada proceso
    y se mata a todos. Que no queden descendientes de `pid` no prueba que esté cerrada: un
    proceso reparentado a otro padre ya no cuelga de él. Por eso lo que cuenta es que ninguno
    de los anotados siga vivo.

    `ns` es el espacio de nombres de pids de la jaula (`ns_de_la_jaula`, anotado mientras el
    bwrap vivía): identifica a sus procesos aunque el bwrap haya muerto antes y los haya
    dejado reparentados. `pid` solo se toca si sigue siendo el bwrap que se lanzó, con `inicio`
    (su hora de arranque, o la que tiene al entrar): si ya salió, su pid pudo reusarse y ni
    él ni su árbol se señalan. Sin `ns` ni bwrap vivo no hay con qué identificar la jaula y
    no hay nada que cerrar."""
    import signal
    import time

    inicio = inicio or _inicio(pid)
    bwrap = pid if inicio and _inicio(pid) == inicio else 0
    if not bwrap and not ns:
        return True
    fin = time.monotonic() + plazo
    vistos: dict = {}
    while True:
        _congelar(bwrap, vistos, ns)
        for p, arranque in vistos.items():
            if _inicio(p) != arranque:
                continue
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        if bwrap:
            try:
                os.waitpid(bwrap, os.WNOHANG)
            except ChildProcessError:
                pass
        vivos = [p for p, arranque in vistos.items() if _inicio(p) == arranque and _vivo(p)]
        if not vivos and not (bwrap and _descendientes(bwrap)) and not _en_ns(ns):
            return True
        if time.monotonic() >= fin:
            return False
        time.sleep(0.02)


def disponible(bwrap: str = BWRAP) -> bool:
    """True si bubblewrap puede crear una jaula aquí; igual que `disponible` de las jaulas de
    Windows. `causa_no_disponible` dice por qué no."""
    return not causa_no_disponible(bwrap)


def causa_no_disponible(bwrap: str = BWRAP) -> str:
    """Vacío si bubblewrap puede crear una jaula aquí; si no, la causa. Un kernel sin
    espacios de nombres de usuario, o una política que los veda, falla en esta prueba y
    no en el primer encargo."""
    if not os.access(bwrap, os.X_OK):
        return f"no está {bwrap}: instalar bubblewrap"
    try:
        hecho = subprocess.run(
            [bwrap, "--unshare-all", *_sistema(), "--proc", "/proc", "--dev", "/dev",
             "--", "/usr/bin/true"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{bwrap} no corre: {type(exc).__name__}: {exc}"
    if hecho.returncode:
        detalle = " ".join((hecho.stderr or "").split())[:300]
        return f"{bwrap} no puede crear una jaula (codigo {hecho.returncode}): {detalle}"
    return ""


# --- Guardián dentro de la distro -------------------------------------------------------------
# El supervisor corre en Windows y lanza `wsl.exe --exec python3 -I -S -c <este archivo> guardian
# <spec>`. El guardián corre FUERA de la jaula, con el usuario real de la distro: lee la credencial
# de Claude de su `~/.claude`, arma la jaula, la lanza y la cierra entera si el padre desaparece,
# si le llega una señal de parada o si vence el plazo. Un Job de Windows no ve nada de esto.
# Salidas: 124 = parada, plazo o padre muerto; 125 = no pudo cerrar la jaula; 126 = no pudo
# prepararla (credencial); 127 = falta el ejecutable.

SALIDA_PARADA = 124
SALIDA_SIN_CERRAR = 125
SALIDA_SIN_PREPARAR = 126
SALIDA_SIN_EJECUTABLE = 127
ARCHIVO_CONTROL = "jaula-wsl.json"
CLAUDE_EN_EL_HOME = ".local/bin/claude"
PLAZO_NS_S = 5.0


def _inicio(pid: int) -> str:
    """Hora de arranque del proceso en ticks (campo 22 de `/proc/<pid>/stat`), o vacío.
    Distingue un pid reusado."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rsplit(b")", 1)[1].split()[19].decode()
    except (OSError, IndexError):
        return ""


def _publica(ruta: str, documento: dict) -> None:
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(documento, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)


def ejecutable_de_claude(entorno: dict | None = None) -> str:
    """Donde está Claude Code en la distro: `MOTOR_WSL_CLAUDE`, `~/.local/bin/claude` o el PATH."""
    import shutil

    entorno = os.environ if entorno is None else entorno
    candidatos = [
        entorno.get("MOTOR_WSL_CLAUDE", ""),
        os.path.join(os.path.expanduser("~"), CLAUDE_EN_EL_HOME),
    ]
    for ruta in candidatos:
        if ruta and os.path.isfile(ruta) and os.access(ruta, os.X_OK):
            return ruta
    return shutil.which("claude") or ""


def _cuenta_de_claude(home: str) -> dict:
    """La suscripción de Claude de la distro. Levanta ValueError con la causa si no sirve."""
    ruta = os.path.join(home, ".claude", ".credentials.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            cuenta = json.load(f).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        raise ValueError(f"no se puede leer la suscripcion de Claude en {ruta}") from None
    if not cuenta.get("accessToken") or "user:inference" not in (cuenta.get("scopes") or []):
        raise ValueError("la suscripcion de Claude de la distro no da acceso de inferencia")
    return cuenta


def credencial_de_claude(ejecutable: str, home: str | None = None) -> dict:
    """Entorno que lleva la suscripción a la jaula: el token de acceso, sin el de renovación.
    Si el acceso venció se renueva con el cliente oficial, fuera de la jaula, como en Windows."""
    import time

    home = home or os.path.expanduser("~")
    cuenta = _cuenta_de_claude(home)
    expira = float(cuenta.get("expiresAt") or 0) / 1000
    if expira and expira <= time.time():
        try:
            subprocess.run(
                [ejecutable, "--print", "--safe-mode"], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise ValueError("no se pudo renovar la suscripcion de Claude en la distro") from None
        cuenta = _cuenta_de_claude(home)
        expira = float(cuenta.get("expiresAt") or 0) / 1000
        if expira and expira <= time.time():
            raise ValueError("Claude no pudo renovar el acceso de suscripcion en la distro")
    entorno = {
        "CLAUDE_CODE_OAUTH_TOKEN": cuenta["accessToken"],
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
    }
    if expira:
        entorno["MOTOR_CLAUDE_ACCESS_EXPIRES_AT"] = str(expira)
    return entorno


def _preparar_runtime(runtime: str, credencial: str, ejecutable: str) -> dict:
    for sub in ("tmp", ".config", ".local/share", ".claude"):
        os.makedirs(os.path.join(runtime, sub), exist_ok=True)
    entorno = {
        "CLAUDE_CODE_TMPDIR": runtime + "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if credencial == "claude":
        entorno.update(credencial_de_claude(ejecutable))
        entorno["CLAUDE_CONFIG_DIR"] = runtime + "/.claude"
        _publica(
            runtime + "/.claude/settings.json", {"skipDangerousModePermissionPrompt": True}
        )
    return entorno


def _guardian(spec: dict) -> int:
    import ctypes
    import signal
    import time

    try:
        ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    except Exception:  # noqa: BLE001
        pass
    parado = []
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, lambda *_: parado.append(sig))
    padre = os.getppid()
    plazo = spec.get("plazo")
    fin = None if plazo is None else time.monotonic() + float(plazo)

    cmd = list(spec["cmd"])
    jaula_spec = spec["jaula"]
    ejecutables = list(jaula_spec.get("ejecutables") or ())
    ejecutable = ""
    if cmd[0] == "claude":
        ejecutable = ejecutable_de_claude()
        if not ejecutable:
            print("falta Claude Code en la distro (MOTOR_WSL_CLAUDE o ~/.local/bin/claude)",
                  file=sys.stderr)
            return SALIDA_SIN_EJECUTABLE
        cmd[0] = ejecutable
        ejecutables.append(ejecutable)
    try:
        extra = _preparar_runtime(jaula_spec["runtime"], spec.get("credencial", ""), ejecutable)
    except (OSError, ValueError) as exc:
        print(f"Cuenta de claude: {exc}", file=sys.stderr)
        return SALIDA_SIN_PREPARAR
    extra.update(spec.get("env") or {})
    egreso = ""
    if jaula_spec.get("red") == RED_PROVEEDOR:
        import atexit
        import shutil
        import tempfile

        try:
            egreso = tempfile.mkdtemp(prefix="motor-egreso-", dir="/tmp")
            servidor = servir_egreso(egreso + "/s", jaula_spec.get("dominios") or ())
        except OSError as exc:
            print(f"no se pudo abrir la salida del proveedor: {exc}", file=sys.stderr)
            return SALIDA_SIN_PREPARAR
        atexit.register(shutil.rmtree, egreso, ignore_errors=True)
        atexit.register(servidor.server_close)
    jaula = Jaula(
        espacio=jaula_spec["espacio"], runtime=jaula_spec["runtime"],
        lectura=bool(jaula_spec.get("lectura")),
        lecturas=tuple(jaula_spec.get("lecturas") or ()),
        ejecutables=tuple(ejecutables), red=jaula_spec.get("red", RED_ABIERTA),
        dominios=tuple(jaula_spec.get("dominios") or ()), egreso=egreso,
    )
    control = spec.get("control")
    argv = argv_jaula(jaula, cmd, cwd=spec.get("cwd"))
    try:
        proceso = subprocess.Popen(
            argv, env=entorno_jaula(jaula, extra), stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        print(f"no se pudo lanzar la jaula ({BWRAP}): {exc}", file=sys.stderr)
        return SALIDA_SIN_PREPARAR
    identidad = {
        "guardian": os.getpid(), "guardian_inicio": _inicio(os.getpid()),
        "bwrap": proceso.pid, "bwrap_inicio": _inicio(proceso.pid), "estado": "activo",
    }
    if control:
        _publica(os.path.join(control, ARCHIVO_CONTROL), identidad)
    codigo = None
    ns = ""
    busca_hasta = time.monotonic() + PLAZO_NS_S
    while codigo is None:
        if not ns and time.monotonic() < busca_hasta:
            init = init_de_la_jaula(proceso.pid)
            ns = _ns_pid(init) if init else ""
            if ns:
                identidad.update(
                    ns_pid=ns, ns_init=init, ns_init_inicio=_inicio(init)
                )
                if control:
                    _publica(os.path.join(control, ARCHIVO_CONTROL), identidad)
        codigo = proceso.poll()
        if codigo is not None:
            break
        if parado or (fin is not None and time.monotonic() >= fin) or (
            os.getppid() != padre or not os.path.exists(f"/proc/{padre}")
        ):
            codigo = SALIDA_PARADA
            break
        time.sleep(0.02)
    cerrada = cerrar_jaula(proceso.pid, identidad)
    if control:
        _publica(
            os.path.join(control, ARCHIVO_CONTROL),
            {**identidad, "estado": "cerrado" if cerrada else "incierto"},
        )
    return codigo if cerrada else SALIDA_SIN_CERRAR


def cerrar_jaula(bwrap: int, identidad: dict, plazo: float = 5.0) -> bool:
    """`cerrar` con lo anotado en `identidad`. El espacio de nombres solo se usa si el init de
    esa jaula sigue vivo (`_ns_vigente`): con el init muerto el kernel ya cerró el espacio y su
    número puede ser de otra jaula, que el guardián no debe tocar."""
    return cerrar(
        bwrap, plazo, inicio=identidad.get("bwrap_inicio"), ns=_ns_vigente(identidad)
    )


def cerrar_identidad(identidad: dict, plazo: float = 5.0) -> bool:
    """Mata la jaula y al guardián que describe `identidad` (el archivo de control), si siguen
    siendo esos procesos. True si no queda nada vivo."""
    import signal
    import time

    pid = int(identidad.get("bwrap") or 0) if identidad.get("bwrap_inicio") else 0
    ns = _ns_vigente(identidad)
    if pid or ns:
        cerrar(pid, inicio=identidad.get("bwrap_inicio"), ns=ns)
    guardian = int(identidad.get("guardian") or 0)
    if guardian and _inicio(guardian) == identidad.get("guardian_inicio"):
        try:
            os.kill(guardian, signal.SIGKILL)
        except OSError:
            pass
    fin = time.monotonic() + plazo
    while vivo_identidad(identidad):
        if time.monotonic() >= fin:
            return False
        time.sleep(0.02)
    return True


def vivo_identidad(identidad: dict) -> bool:
    """Vivo es el guardián, el bwrap o cualquier proceso que siga en el espacio de nombres de
    la jaula: matar a los dos primeros no cierra lo que ya quedó reparentado."""
    if any(_vivo(p) for p in _en_ns(_ns_vigente(identidad))):
        return True
    for clave in ("guardian", "bwrap"):
        pid = int(identidad.get(clave) or 0)
        if pid and _inicio(pid) == identidad.get(clave + "_inicio") and _vivo(pid):
            return True
    return False


def _causa_de_claude() -> str:
    ejecutable = ejecutable_de_claude()
    if not ejecutable:
        return "falta Claude Code en la distro (MOTOR_WSL_CLAUDE o ~/.local/bin/claude)"
    try:
        _cuenta_de_claude(os.path.expanduser("~"))
    except ValueError as exc:
        return str(exc)
    return ""


def _principal(argv: list) -> int:
    orden = argv[0] if argv else ""
    if orden == "guardian":
        return _guardian(json.loads(argv[1]))
    if orden in ("cerrar", "estado"):
        identidad = json.loads(argv[1])
        if orden == "cerrar":
            cerrar_identidad(identidad)
        print(json.dumps({"vivo": vivo_identidad(identidad)}))
        return 0
    if orden == "disponible":
        causa = causa_no_disponible()
        if not causa and argv[1:2] == ["claude"]:
            causa = _causa_de_claude()
        print(json.dumps({"causa": causa}))
        return 0
    print(f"orden desconocida: {orden!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_principal(sys.argv[1:]))
