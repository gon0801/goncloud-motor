#!/usr/bin/env bash
# Comprueba, por máquina, que el despliegue del motor de encargos quedó en el SHA nuevo.
# Lee solo el recibo del motor de encargos (~/.motor-encargos/instalacion.json); no cae
# al recibo del motor viejo de planes (~/.motor). Tampoco despliega ni revierte nada.
# Para instalar el motor viejo de planes usa ops/instala-todo.sh; este comprobador no lo mira.
# Es idempotente: solo lee y reintenta la lectura. Si el agente arrancaría sin el token de
# GitHub de los agentes, avisa por máquina sin fallar. Imprime la huella de la flota de
# cada máquina y avisa, sin fallar, si no corren la misma tabla de modelos o manifiesto, o
# si recuperar dejó una generación sin medir. Por cada director que usa el motor de la
# máquina imprime una línea MODELOS: cuántos agentes tienen los modelos de esa tabla y
# cuáles no. Tampoco hace fallar el despliegue.
#
# --sha SHA     SHA que debe estar instalado (por omisión, HEAD del repo).
# --maquina M   Una sola máquina: mac, mini o pc.
# --espera S    Segundos acotados a esperar una pasada nueva con ese SHA.
# --reinicio    El despliegue ya ordenó el reinicio: sin pasada nueva se dice
#               "el reinicio falló" por máquina.
# --ensayo      No toca ssh: imprime lo que comprobaría y sale 0.
# --recibo-viejo  Lee primero ~/.motor/instalacion.json. Solo lo usa instala-todo.sh
#               para el motor viejo de planes; el motor de encargos no lo toca.
set -uo pipefail
aqui="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$aqui/lib.sh"

sha=""
espera=0
ensayo=0
reinicio=0
recibo_viejo=0
maquinas="$MAQUINAS"
while [ $# -gt 0 ]; do
  case "$1" in
    --sha) sha="${2:-}"; shift 2 ;;
    --maquina) maquinas="${2:-}"; shift 2 ;;
    --espera) espera="${2:-}"; shift 2 ;;
    --reinicio) reinicio=1; shift ;;
    --ensayo) ensayo=1; shift ;;
    --recibo-viejo) recibo_viejo=1; shift ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
case "$espera" in
  "" | *[!0-9]*) echo "espera inválida: $espera" >&2; exit 2 ;;
esac
if [ -z "$sha" ]; then
  sha="$(git -C "$aqui/.." rev-parse HEAD)" || exit 2
fi
case "$sha" in
  "" | *[!0-9a-fA-F]*) echo "SHA inválido: $sha" >&2; exit 2 ;;
esac

# El registro se pliega con el mismo código y el mismo intérprete que el supervisor,
# no con una copia de la regla. El intérprete sale del LaunchAgent (Mac y Mini) o es
# el de motor-runtime (PC).
PROGRAMA='import base64,hashlib,json,os,pathlib,re,subprocess,sys
home = pathlib.Path.home()
recibo_viejo = sys.argv[-1] == "1"

def leer(ruta):
    try:
        return json.loads(pathlib.Path(ruta).read_text(encoding="utf-8"))
    except Exception:
        return {}

# El motor de encargos usa su propio recibo. Solo instala-todo.sh pide el viejo.
recibo = "-"
rutas = [home / ".motor" / "instalacion.json"] if recibo_viejo else []
rutas.append(home / ".motor-encargos" / "instalacion.json")
for ruta in rutas:
    doc = leer(ruta)
    if isinstance(doc, dict):
        valor = str((doc.get("destino") or {}).get("codigo") or "")
        if valor:
            recibo = valor
            break

pasada = leer(home / ".motor-encargos" / "supervisor-pasada.json")
if not isinstance(pasada, dict):
    pasada = {}
arbol_ruta = str(pasada.get("ruta") or "")
arbol = "-"
if arbol_ruta:
    try:
        r = subprocess.run(
            ["git", "-C", arbol_ruta, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            arbol = r.stdout.strip()
    except Exception:
        pass
vivo = "no"
pid = pasada.get("pid")
if isinstance(pid, int) and pid > 0:
    if os.name == "nt":
        try:
            r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True, text=True, timeout=10)
            if str(pid) in (r.stdout or ""):
                vivo = "si"
        except Exception:
            pass
    else:
        try:
            os.kill(pid, 0)
            vivo = "si"
        except OSError:
            pass
if vivo == "no" and os.name != "nt":
    try:
        r = subprocess.run(["pgrep", "-f", "motor.*supervisor"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and (r.stdout or "").strip():
            vivo = "si"
    except Exception:
        pass

def interprete_supervisor():
    if os.name == "nt":
        return sys.executable
    try:
        import plistlib
        plist = home / "Library" / "LaunchAgents" / "ai.goncloud.motor-encargos.plist"
        datos = plistlib.loads(plist.read_bytes())
        argumentos = datos.get("ProgramArguments") if isinstance(datos, dict) else None
        if argumentos and isinstance(argumentos[0], str) and pathlib.Path(argumentos[0]).exists():
            return argumentos[0]
    except Exception:
        pass
    return sys.executable

PLIEGA = """import pathlib,sys
sys.path.insert(0, sys.argv[1])
from motor.encargos.registro import Registro
from motor.encargos.ciclo import plegar_aislado
registro = Registro(pathlib.Path.home() / ".motor-encargos")
ids = registro.ids()
fallan = sum(1 for ident in ids if plegar_aislado(registro, ident) is None)
print(len(ids), fallan)
"""
registro = ""
if arbol_ruta and (home / ".motor-encargos" / "registro.sqlite").is_file():
    try:
        r = subprocess.run(
            [interprete_supervisor(), "-B", "-c", PLIEGA, arbol_ruta],
            capture_output=True, text=True, timeout=120,
        )
        partes = (r.stdout or "").split()
        if r.returncode == 0 and len(partes) == 2 and partes[0].isdigit() and partes[1].isdigit():
            registro = partes[0] + " " + partes[1]
        else:
            causa = ((r.stderr or "") or "no se pudo leer el registro").strip().splitlines()[-1:]
            registro = "error " + (causa[0] if causa else "no se pudo leer el registro")
    except Exception as exc:
        registro = "error " + type(exc).__name__ + ": " + str(exc)
else:
    registro = "0 0"

# El token de GitHub de los agentes, juzgado con el código del supervisor: la causa si no
# sirve, nunca el valor.
TOKEN = """import pathlib,sys
sys.path.insert(0, sys.argv[1])
from motor.encargos.aislamiento import token_de_agentes
print(token_de_agentes(pathlib.Path.home() / ".motor-encargos")[1] or "ok")
"""
token = "no se pudo comprobar: la pasada no dice qué árbol ejecuta el supervisor"
if arbol_ruta:
    try:
        r = subprocess.run(
            [interprete_supervisor(), "-B", "-c", TOKEN, arbol_ruta],
            capture_output=True, text=True, timeout=60,
        )
        lineas = (r.stdout or "").strip().splitlines()
        if r.returncode == 0 and lineas:
            token = lineas[-1]
        else:
            causa = (r.stderr or "").strip().splitlines()[-1:]
            token = "no se pudo comprobar: " + (causa[0] if causa else "sin salida")
    except Exception as exc:
        token = "no se pudo comprobar: " + type(exc).__name__

def corto(datos):
    return hashlib.sha256(datos).hexdigest()[:12]

# Mac y Mini corren el python3 del sistema, que puede no traer tomllib: la versión de la
# tabla es la línea que escribe motor.flota.manifiesto.emitir, un texto JSON.
def tabla_de(texto):
    hallada = re.search("^generacion = (\".*\")\\s*$", texto, re.M)
    try:
        nombre = json.loads(hallada.group(1)).strip() if hallada else ""
    except ValueError:
        nombre = ""
    if not nombre:
        return "sin-tabla"
    # El motor acepta cualquier nombre: fuera de este juego, y en los sin-* que el
    # comprobador usa para lo que falta, se imprime su huella.
    if re.fullmatch("[A-Za-z0-9][A-Za-z0-9._-]{0,63}", nombre) and not nombre.startswith("sin-"):
        return nombre
    return "#" + corto(nombre.encode("utf-8"))

def home_flota():
    base = home / ".motor-encargos"
    vinculo = leer(base / "flota-home.json")
    destino = vinculo.get("home") if isinstance(vinculo, dict) else None
    return base / str(destino) if destino else home / "flota"

# Se compara lo que corre, la generación activa. El manifiesto raíz es el deseado: una
# actualización pendiente lo escribe antes de activar su generación.
def huella_flota():
    flota = home_flota()
    if not flota.is_dir():
        return "sin-flota"
    try:
        activo = (flota / "actual" / "manifesto.toml").read_bytes().replace(b"\r\n", b"\n")
        manifiesto = corto(activo)
        tabla = tabla_de(activo.decode("utf-8"))
    except OSError:
        manifiesto, tabla = "sin-manifiesto", "sin-tabla"
    except UnicodeDecodeError:
        tabla = "sin-tabla"
    try:
        deseado = corto((flota / "manifiesto.toml").read_bytes().replace(b"\r\n", b"\n"))
    except OSError:
        deseado = "sin-manifiesto"
    fuente = leer(flota / "fuente-compartida.json")
    snapshot = str(fuente.get("snapshot_sha256") or "") if isinstance(fuente, dict) else ""
    snapshot = snapshot[:12] if re.fullmatch("[0-9a-f]{64}", snapshot) else "sin-fuente"
    return " ".join((tabla, manifiesto, snapshot, deseado))

# motor flota aplicar o recuperar deja sin-medir.json cuando una lectura de la generación no
# terminó; la orden trae rutas de la máquina y no se imprime.
def sin_medir():
    nota = leer(home_flota() / "sin-medir.json")
    if not isinstance(nota, dict) or not nota:
        return "-"
    gen = str(nota.get("generacion") or "")
    gen = gen[:12] if re.fullmatch("[0-9a-f]{7,64}", gen) else "#" + corto(gen.encode("utf-8"))
    tope = nota.get("tope")
    operacion = nota.get("operacion")
    return " ".join((
        gen,
        str(tope) if isinstance(tope, (int, float)) else "?",
        operacion if operacion in ("aplicar", "recuperar") else "?",
    ))

print("recibo", recibo)
print("vivo", vivo)
print(
    "pasada",
    str(pasada.get("sha") or "-"),
    str(pasada.get("hora") or "-"),
    "completa" if pasada.get("completa") else "parcial",
)
print("ruta", arbol_ruta or "-")
print("arbol", arbol)
print("registro", registro)
print("token", token)
print("flota", huella_flota())
print("sinmedir", sin_medir())

# Los modelos se comparan con la tabla que la línea flota nombra, la de la generación activa.
# Es un aviso: un error aquí no puede cambiar el resultado del despliegue.
try:
    modelos = {"__name__": "modelos_por_agente"}
    exec(compile(base64.b64decode(MODELOS_POR_AGENTE), "modelos_por_agente.py", "exec"), modelos)
    lineas = modelos["informe"](home, home_flota() / "actual" / "manifesto.toml")
except Exception as exc:
    lineas = ["directores: no pude leer (" + type(exc).__name__ + ")"]
modelos["imprimir"](lineas, sys.stdout)
'
PROGRAMA="MODELOS_POR_AGENTE = \"$(_pc_b64 <"$aqui/modelos_por_agente.py")\"
$PROGRAMA"

PROGRAMA_PC_B64="$(printf '%s' "$PROGRAMA" | _pc_b64)"

lee_estado() {
  local m=$1
  if [ "$m" = pc ]; then
    # La PC contesta cada línea con \r\n: el \r no puede formar parte de ningún
    # campo, o el mismo SHA deja de ser igual a sí mismo.
    py_pc_runtime_prog "$PROGRAMA_PC_B64" "$recibo_viejo" | tr -d '\r'
  else
    en_posix "$m" "$m" "$sha" <<REMOTO | tr -d '\r'
set -euo pipefail
python3 - "$recibo_viejo" <<'PY'
$PROGRAMA
PY
REMOTO
  fi
}

causa_de() { # causa_de <estado> -> texto vacío si el despliegue está comprobado
  local estado=$1 recibo vivo pasada_sha pasada_completa ruta arbol registro registro_total registro_fallan causa=""
  recibo="$(printf '%s\n' "$estado" | awk '/^recibo /{print $2; exit}')"
  vivo="$(printf '%s\n' "$estado" | awk '/^vivo /{print $2; exit}')"
  pasada_sha="$(printf '%s\n' "$estado" | awk '/^pasada /{print $2; exit}')"
  pasada_completa="$(printf '%s\n' "$estado" | awk '/^pasada /{print $4; exit}')"
  ruta="$(printf '%s\n' "$estado" | awk '/^ruta /{print $2; exit}')"
  arbol="$(printf '%s\n' "$estado" | awk '/^arbol /{print $2; exit}')"
  registro="$(printf '%s\n' "$estado" | awk '/^registro /{ $1=""; sub(/^ /,""); print; exit}')"
  registro_total="$(printf '%s\n' "$registro" | awk '{print $1}')"
  registro_fallan="$(printf '%s\n' "$registro" | awk '{print $2}')"
  [ "$recibo" = "$sha" ] || causa="$causa recibo en ${recibo:0:12} (se esperaba ${sha:0:12});"
  [ "$vivo" = "si" ] || causa="$causa supervisor no vivo;"
  [ "$pasada_sha" = "$sha" ] || causa="$causa sin pasada con ${sha:0:12};"
  [ "$pasada_completa" = "completa" ] || causa="$causa la última pasada no fue completa;"
  if [ "$reinicio" = 1 ] && { [ "$vivo" != "si" ] || [ "$pasada_sha" != "$sha" ]; }; then
    causa="$causa el reinicio falló;"
  fi
  [ -n "$ruta" ] && [ "$ruta" != "-" ] || causa="$causa la pasada no dice qué árbol ejecuta el supervisor;"
  [ "$arbol" = "$sha" ] || causa="$causa el árbol del supervisor está en ${arbol:0:12} (se esperaba ${sha:0:12});"
  case "$registro" in
    error*) causa="$causa no se pudo plegar el registro: ${registro#error };" ;;
    "")
      causa="$causa la comprobación no dice si el registro pliega;"
      ;;
    *)
      [ "$registro_fallan" = "0" ] \
        || causa="$causa el registro no pliega entero ($registro_total encargos, $registro_fallan fallan);"
      ;;
  esac
  printf '%s' "$causa"
}

campo() { # campo <nombre> <valor>: "tabla v9" o "sin tabla"
  case "$2" in
    sin-*) printf 'sin %s' "$1" ;;
    *) printf '%s %s' "$1" "$2" ;;
  esac
}

# difiere <qué> <columna>: agrupa las máquinas de $huellas por el valor de esa columna
# y, si hay más de un valor, avisa con cada valor y las máquinas que lo tienen.
difiere() {
  printf '%s' "$huellas" | awk -v que="$1" -v col="$2" '
    !($col in maquinas) { orden[++n] = $col; maquinas[$col] = $1; next }
    { maquinas[$col] = maquinas[$col] ", " $1 }
    END {
      if (n < 2) exit
      linea = ""
      for (i = 1; i <= n; i++) {
        valor = orden[i]
        sub(/^sin-/, "sin ", valor)
        linea = linea (i > 1 ? "; " : "") valor " en " maquinas[orden[i]]
      }
      print "AVISO flota: las máquinas no corren " que ": " linea
    }'
}

fallos=0
huellas="" # una línea por máquina: <máquina> <tabla> <manifiesto>
for m in $maquinas; do
  if [ "$ensayo" = 1 ]; then
    echo "ensayo $m: recibo con ${sha:0:12}, supervisor vivo, pasada completa y registro entero"
    continue
  fi
  fin=$(( $(date +%s) + espera ))
  causa=" no responde;"
  while :; do
    if estado="$(lee_estado "$m")"; then
      causa="$(causa_de "$estado")"
      [ -z "$causa" ] && break
    fi
    [ "$(date +%s)" -ge "$fin" ] && break
    sleep 2
  done
  if [ -n "$causa" ]; then
    echo "FALLA $m:$causa" >&2
    fallos=$((fallos + 1))
  else
    echo "OK $m: recibo ${sha:0:12}, supervisor vivo, pasada completa y registro entero"
  fi
  # Sin el token de los agentes el motor funciona: el aviso no hace fallar el despliegue.
  token="$(printf '%s\n' "$estado" | awk '/^token /{ $1=""; sub(/^ /,""); print; exit}')"
  if [ -n "$token" ] && [ "$token" != ok ]; then
    echo "AVISO $m: el agente arranca sin credencial de GitHub: $token" >&2
  fi
  read -r sin_gen sin_tope sin_op <<<"$(printf '%s\n' "$estado" | awk '/^sinmedir /{ print $2, $3, $4; exit }')"
  if [ -n "$sin_gen" ] && [ "$sin_gen" != - ]; then
    case "$sin_op" in
      aplicar) repite="vuelve a aplicar la generación $sin_gen con motor flota aplicar" ;;
      recuperar) repite="vuelve a correr motor flota recuperar" ;;
      *) repite="vuelve a aplicarla con motor flota aplicar o a correr motor flota recuperar" ;;
    esac
    echo "AVISO $m: la flota no midió la generación $sin_gen (tope $sin_tope s); $repite" >&2
  fi
  flota="$(printf '%s\n' "$estado" | awk '/^flota /{ $1=""; sub(/^ /,""); print; exit}')"
  case "$flota" in
    "") ;;
    sin-flota)
      echo "FLOTA $m: sin flota"
      huellas="$huellas$m sin-flota sin-flota"$'\n'
      ;;
    *)
      read -r tabla manifiesto fuente deseado <<<"$flota"
      echo "FLOTA $m: $(campo tabla "$tabla"), $(campo manifiesto "$manifiesto"), $(campo fuente "$fuente"), $(campo "manifiesto deseado" "$deseado")"
      huellas="$huellas$m $tabla $manifiesto"$'\n'
      ;;
  esac
  printf '%s\n' "$estado" | awk -v m="$m" 'sub(/^modelos /, "") { print "MODELOS " m " " $0 }'
done

# Una flota distinta no hace fallar el despliegue: el código del servicio sí quedó en el SHA.
difiere "la misma tabla de modelos" 2 >&2
difiere "el mismo manifiesto" 3 >&2

if [ "$fallos" -gt 0 ]; then
  echo "$fallos máquina(s) no comprobaron el despliegue" >&2
  exit 1
fi
echo "despliegue comprobado en:$maquinas"
