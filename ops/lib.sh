# shellcheck shell=bash disable=SC2034
# Lo común de ops/*.sh: las tres máquinas y cómo hablarles. Se carga con `.`, no se ejecuta.
# Las tres máquinas se tratan igual: todo va por ssh, también hacia la máquina en la que corre
# el script, que entra a sí misma por ssh. Así el mismo script hace lo mismo en las tres.
# Estos scripts viven en el repo del motor y cargan esta base común.

MAQUINAS="mac mini pc"
REPO_URL="${MOTOR_REPO_URL:-git@github.com:gon0801/goncloud-motor.git}"
CONF_URL="${MOTOR_CONF_URL:-git@github.com:gon0801/goncloud-motor-conf.git}"
ETIQUETA="${MOTOR_ETIQUETA:-v1.0.35}"
PC_USUARIO="${MOTOR_PC_USUARIO:-ehven}"
PC_UV="${MOTOR_PC_UV:-C:\\Users\\ehven\\.local\\bin\\uv.exe}"
TAREA_PC="GoncloudMotor"
SERVICIO_MAC="com.goncloud.motor"

host_de() {
  case "$1" in
    mac) echo "${MOTOR_SSH_MAC:-mac}" ;;
    mini) echo "${MOTOR_SSH_MINI:-mini}" ;;
    pc) echo "${MOTOR_SSH_PC:-pc}" ;;
    *) echo "máquina desconocida: $1" >&2; return 2 ;;
  esac
}

# sha_publicado <raiz>: el commit al que apunta $ETIQUETA en origin (vacío si no existe); sale != 0
# si origin no responde. La última línea manda: ls-remote pone el ^{} de una anotada al final.
sha_publicado() {
  local salida
  salida="$(git -C "$1" ls-remote --tags origin "refs/tags/$ETIQUETA" "refs/tags/$ETIQUETA^{}")" || return 1
  printf '%s\n' "$salida" | tail -n 1 | cut -f1
}

# reintenta <veces> <comando...>: lo de red choca con locks o baches; un fallo suelto no debe tumbar nada.
reintenta() {
  local n=$1 i
  shift
  for i in $(seq "$n"); do
    "$@" && return 0
    [ "$i" -lt "$n" ] && sleep 2
  done
  return 1
}

ssh_a() { ssh -o BatchMode=yes -o ConnectTimeout=15 "$@"; }

# py_pc <codigo> [args...]: python en la PC. Los args van en la línea de cmd.exe,
# no como asignación POSIX. El código no puede llevar comillas dobles ni $.
py_pc() {
  local code=$1
  shift
  ssh_a "$(host_de pc)" "$PC_UV run --python 3.12 python -c \"$code\" $*"
}

# Programa real en argv, en base64: el python remoto no puede llevar comillas ni if/else.
PC_EXEC='import base64,sys; exec(base64.b64decode(sys.argv[1]))'

_pc_b64() {
  python3 -c 'import base64,sys; print(base64.b64encode(sys.stdin.buffer.read()).decode())'
}

# argv[1] es este programa; argv[2] es el nombre bajo ~/.motor.
# 0 leído, 2 ausente, 1 error (vacío, ilegible o no es archivo).
LEE_ARCHIVO_PC_B64="$(_pc_b64 <<'PY'
import pathlib, sys
p = pathlib.Path.home() / ".motor" / sys.argv[2]
if not p.exists():
    raise SystemExit(2)
if not p.is_file():
    raise SystemExit(1)
try:
    raw = p.read_bytes()
except OSError:
    raise SystemExit(1)
if raw == b"":
    raise SystemExit(1)
sys.stdout.buffer.write(raw)
PY
)"

# ausente | ilegible | entero. 1 si el archivo existe y no se puede leer.
LEE_FORMATO_PC_B64="$(_pc_b64 <<'PY'
import json, pathlib, sys
p = pathlib.Path.home() / ".motor" / "formato"
if not p.exists():
    sys.stdout.write("ausente")
    raise SystemExit(0)
try:
    raw = p.read_text(encoding="utf-8")
except OSError:
    raise SystemExit(1)
try:
    v = json.loads(raw).get("version")
    sys.stdout.write(str(v) if type(v) is int else "ilegible")
except Exception:
    sys.stdout.write("ilegible")
PY
)"

# tres líneas: sha del toml efectivo, sha del toml en la conf, ok|no.
ESTADO_TOML_PC_B64="$(_pc_b64 <<'PY'
import hashlib, pathlib, sys
home = pathlib.Path.home()

def sha(p):
    if not p.exists():
        return "-"
    if not p.is_file():
        raise SystemExit(1)
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        raise SystemExit(1)

link = home / ".motor" / "conf"
estado = "ok" if (link.is_symlink() or link.is_dir()) else "no"
sys.stdout.write(
    sha(home / ".motor" / "maquina.toml")
    + "\n"
    + sha(home / "motor-conf" / "maquinas" / "pc.toml")
    + "\n"
    + estado
    + "\n"
)
PY
)"

py_pc_prog() {
  local b64=$1
  shift
  py_pc "$PC_EXEC" "$b64" "$@"
}

lee_archivo_pc() {
  py_pc_prog "$LEE_ARCHIVO_PC_B64" "$1"
}

# Imprime none, ilegible o el entero. != 0 si ssh falla o el archivo no se puede leer.
lee_formato_pc() {
  local raw rc
  set +e
  raw="$(py_pc_prog "$LEE_FORMATO_PC_B64")"
  rc=$?
  set -e
  raw="$(printf '%s' "$raw" | tr -d '\r')"
  [ "$rc" -eq 0 ] || return "$rc"
  case "$raw" in
    ausente) echo none ;;
    "") return 1 ;;
    *) echo "$raw" ;;
  esac
}

escribe_archivo_pc() {
  local nombre=$1 b64
  b64="$(python3 -c 'import base64,sys; print(base64.b64encode(sys.stdin.buffer.read()).decode())')"
  py_pc "import base64,os,pathlib,sys; p=pathlib.Path.home()/'.motor'/sys.argv[1]; p.parent.mkdir(parents=True, exist_ok=True); raw=base64.b64decode(sys.argv[2]); t=p.with_suffix(p.suffix+'.tmp'); t.write_bytes(raw); os.chmod(t,0o600); os.replace(t,p)" "$nombre" "$b64"
}

borra_archivo_pc() {
  py_pc "import pathlib,sys; p=pathlib.Path.home()/'.motor'/sys.argv[1]; p.unlink(missing_ok=True)" "$1"
}

# Se ejecuta el helper del controlador, nunca el de una instalación vieja.
origen_maquina_b64() {
  python3 -c 'import base64,sys; print(base64.b64encode(open(sys.argv[1],"rb").read()).decode())' "$(dirname "${BASH_SOURCE[0]}")/maquina_local.py"
}

maquina_local_pc() {
  local programa
  programa="$(origen_maquina_b64)" || return 1
  py_pc 'import base64,sys; programa=base64.b64decode(sys.argv.pop(1)); exec(programa)' "$programa" "$@"
}

# en_posix <mac|mini|pc> args... : el script de stdin corre allá con `bash -s -- args`.
en_posix() {
  local m=$1 cmd
  shift
  cmd="$(printf '%q ' bash -s -- "$@")"
  ssh_a "$(host_de "$m")" "$cmd"
}

# corre <m> <comando posix> <comando cmd.exe de la PC>
corre() {
  local m=$1
  if [ "$m" = pc ]; then
    ssh_a "$(host_de pc)" "$3"
  else
    # bash explícito: el shell de login de la Mini es zsh y no parte `$py` en palabras.
    ssh_a "$(host_de "$m")" "$(printf '%q ' bash -c "$2")"
  fi
}

# maquinas_canario: mini y pc menos la máquina en la que corre el script (nunca la Mac).
# Para saber cuál es, pide por ssh el hostname de cada una y lo compara con el local.
# Si el hostname local o el de alguna máquina falla o sale vacío, sale != 0 y no imprime
# ninguna lista usable: quien llama debe negarse antes de tocar nada.
maquinas_canario() {
  local m remoto_crudo remoto propio_crudo propio
  propio_crudo="$(hostname 2>/dev/null)" || return 1
  propio="$(printf '%s' "$propio_crudo" | tr -d '\r' | tr -d '[:space:]')"
  [ -n "$propio" ] || return 1
  for m in mini pc; do
    remoto_crudo="$(corre "$m" hostname hostname 2>/dev/null)" || return 1
    remoto="$(printf '%s' "$remoto_crudo" | tr -d '\r' | tr -d '[:space:]')"
    [ -n "$remoto" ] || return 1
    if [ "$remoto" = "$propio" ]; then
      continue
    fi
    printf '%s\n' "$m"
  done
}

# info_daemon <m>: "<pid> <epoch de arranque>" del proceso motor daemon (no del supervisor); != 0 si no hay.
info_daemon() {
  local m=$1 out pid inicio
  if [ "$m" = pc ]; then
    out="$(corre pc x "powershell -NoProfile -NonInteractive -Command \"Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*motor daemon*--home*' -and \$_.Name -like 'python*' } | Select-Object -First 1 | ForEach-Object { \$_.ProcessId.ToString() + ' ' + ([DateTimeOffset]\$_.CreationDate).ToUnixTimeSeconds().ToString() }\"" 2>/dev/null)" || return 1
    out="$(printf '%s\n' "$out" | tr -d '\r' | grep -E '^[0-9]+ [0-9]+$' | head -n 1)"
    [ -n "$out" ] || return 1
    printf '%s\n' "$out"
    return 0
  fi
  pid="$(corre "$m" 'pgrep -a -f "[m]otor daemon --home $HOME/.motor( |$)" | head -n 1' x 2>/dev/null | awk '{print $1}' | tr -d '[:space:]')"
  [ -n "$pid" ] || return 1
  inicio="$(corre "$m" "ps -o lstart= -p $pid" x 2>/dev/null)"
  inicio="$(printf '%s' "$inicio" | python3 -c 'import sys,datetime
s=" ".join(sys.stdin.read().split())
try:
    print(int(datetime.datetime.strptime(s, "%a %b %d %H:%M:%S %Y").timestamp()))
except Exception:
    sys.exit(1)' 2>/dev/null)" || return 1
  [ -n "$inicio" ] || return 1
  printf '%s %s\n' "$pid" "$inicio"
}

# codigo_de <m>: "<sha> <epoch>" del commit que tiene ~/motor y de cuándo se puso (mtime de .git/HEAD).
codigo_de() {
  local m=$1 sha epoca
  if [ "$m" = pc ]; then
    sha="$(corre pc x 'git -C %USERPROFILE%\motor rev-parse HEAD' 2>/dev/null | tr -d '[:space:]')" || return 1
    epoca="$(corre pc x "powershell -NoProfile -NonInteractive -Command \"[DateTimeOffset]::new((Get-Item (Join-Path \$env:USERPROFILE 'motor\\.git\\HEAD')).LastWriteTimeUtc).ToUnixTimeSeconds()\"" 2>/dev/null | tr -d '[:space:]')" || return 1
  else
    sha="$(corre "$m" 'git -C "$HOME/motor" rev-parse HEAD' x 2>/dev/null | tr -d '[:space:]')" || return 1
    epoca="$(corre "$m" 'python3 -c "import os,sys;print(int(os.path.getmtime(sys.argv[1])))" "$HOME/motor/.git/HEAD"' x 2>/dev/null | tr -d '[:space:]')" || return 1
  fi
  [ -n "$sha" ] && [ -n "$epoca" ] || return 1
  printf '%s %s\n' "$sha" "$epoca"
}

# toma_opciones_e [--maquina mac|mini|pc] [--conf-sha SHA]
# Deja MAQUINAS (una, si se pidió) y CONF_SHA_PEDIDO (vacío si no vino).
# --conf-sha solo lo usa la instalación; la reversa lo rechaza con admite_conf=0.
toma_opciones_e() {
  local admite_conf=${1:-1}
  shift
  CONF_SHA_PEDIDO=""
  local pedidos=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --maquina)
        [ $# -ge 2 ] || { echo "--maquina necesita mac, mini o pc" >&2; return 2; }
        case "$2" in
          mac|mini|pc) pedidos="$2" ;;
          *) echo "--maquina es mac, mini o pc, no '$2'" >&2; return 2; ;;
        esac
        shift 2
        ;;
      --conf-sha)
        [ "$admite_conf" = 1 ] || { echo "la reversa no recibe --conf-sha; usa el recibo" >&2; return 2; }
        [ $# -ge 2 ] || { echo "--conf-sha necesita un SHA" >&2; return 2; }
        CONF_SHA_PEDIDO="$2"
        shift 2
        ;;
      *) echo "opción desconocida: $1" >&2; return 2 ;;
    esac
  done
  if [ -n "$pedidos" ]; then MAQUINAS="$pedidos"; fi
  if [ -n "$CONF_SHA_PEDIDO" ]; then
    case "$CONF_SHA_PEDIDO" in
      [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
      *) echo "--conf-sha tiene que ser 40 hexadecimales en minúscula" >&2; return 2 ;;
    esac
  fi
}

# regla_formato_b64: las dos funciones puras, para ejecutarlas en la máquina sin importar un árbol viejo.
regla_formato_b64() {
  local fuente=$1
  PYTHONPATH="$fuente" python3 -c 'import base64, inspect, motor.formato as f
src = "from __future__ import annotations\nimport ast\n" + inspect.getsource(f.limite_declarado) + "\n" + inspect.getsource(f.destino_compatible) + "\n"
print(base64.b64encode(src.encode()).decode())'
}

# accion_formato <version-home|-1|vacio> <limite|vacio>  ->  imprime accion y causa
accion_formato() {
  local fuente=$1 home=$2 limite=$3
  PYTHONPATH="$fuente" python3 -c 'import sys
from motor.formato import destino_compatible
home, limite = sys.argv[1], sys.argv[2]
hv = None if home in ("", "none") else int(home)
lv = None if limite in ("", "none") else int(limite)
accion, causa = destino_compatible(hv, lv)
print(accion)
print(causa)' "$home" "$limite"
}

# espera_daemon_en_sha <maquina> <sha> <tope_s>: 0 cuando el motor daemon de esa máquina arrancó
# con el código de <sha> (~/motor está en ese commit y el proceso nació después de ponerlo); 1 al pasar el tope.
espera_daemon_en_sha() {
  local m=$1 sha=$2 tope=$3 hasta info codigo
  hasta=$((SECONDS + tope))
  while :; do
    if info="$(info_daemon "$m")" && codigo="$(codigo_de "$m")" \
      && [ "${codigo%% *}" = "$sha" ] && [ "${info##* }" -ge "${codigo##* }" ] 2>/dev/null; then
      return 0
    fi
    [ "$SECONDS" -lt "$hasta" ] || return 1
    sleep "${MOTOR_ESPERA_PASO:-5}"
  done
}
