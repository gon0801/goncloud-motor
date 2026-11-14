#!/usr/bin/env bash
# ops/canario-encargos.sh con un ssh falso, un HOME temporal por máquina, árboles git
# temporales y un lanzador falso que contesta lo que pida cada caso. La PC de mentira
# contesta CRLF. Nada de esto toca las máquinas reales ni el repo del canario.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

export MOTOR_SSH_MAC=mac MOTOR_SSH_MINI=mini MOTOR_SSH_PC=gwpc
export MOTOR_PC_PYTHON='C:\runtime\python.exe'
unset MOTOR_CANARIO_REPO
LANZADOS="$S/lanzados.log"
export LANZADOS

git init -q -b main "$S/fuente"
printf 'uno\n' >"$S/fuente/README.md"
git -C "$S/fuente" add -A
git -C "$S/fuente" -c user.name=t -c user.email=t@t commit -q -m uno
OTRO="$(git -C "$S/fuente" rev-parse HEAD)"
printf 'dos\n' >>"$S/fuente/README.md"
git -C "$S/fuente" -c user.name=t -c user.email=t@t commit -q -am dos
SHA="$(git -C "$S/fuente" rev-parse HEAD)"
export SHA

# El informe que daría el lanzador según el caso:
# <máquina> <coincide|no_coincide|sucio|sin_filas|no_pudo|muda>. Trae la versión de
# FAKE_VERSION_<máquina>, o la de todas.
mkdir -p "$S/bin-servicio" "$S/path-servicio"
cat >"$S/bin/informe-falso" <<'FALSO'
#!/usr/bin/env bash
m=$1
echo "canario [preparar] base lista; $m" >&2
v="FAKE_VERSION_$m"
version="${!v:-$SHA}"
case "$2" in
  coincide|no_coincide|sucio|sin_filas)
    codigo=0 coincide=true resultado=pasa limpieza=borrado
    tabla='| termina | 1 | pasa | pasa |\n\nCoincide con lo esperado.'
    case "$2" in
      no_coincide)
        codigo=1 coincide=false resultado=falla
        tabla='| termina | 1 | falla | pasa |\n\nNo coincide con lo esperado:\n- termina: regresión' ;;
      sucio) codigo=1 limpieza=falló ;;
    esac
    filas="[{\"nombre\": \"termina\", \"resultado\": \"$resultado\", \"esperado\": \"pasa\", \"discrepancia\": \"\", \"observado\": \"\"}]"
    if [ "$2" = sin_filas ]; then filas='[]' coincide=false tabla=''; fi
    printf '{"maquina": "%s", "version": "%s", "encargo": "enc_%s", "fin": "terminado", "segundos": 2121, "cierre": {"estado": "terminado", "parada": "", "supervisor": "ausente", "vivos": [], "fallas": []}, "detencion": {"clave": "c", "encargo": "enc_%s_falla", "fin": "terminado", "estado": "terminado", "cierre": {"estado": "terminado", "parada": "", "supervisor": "ausente", "vivos": [], "fallas": []}}, "apagon": {"clave": "c", "encargo": "enc_%s_corte", "fin": "terminado", "estado": "terminado", "cierre": {"estado": "terminado", "parada": "", "supervisor": "ausente", "vivos": [], "fallas": []}}, "filas": %s, "limpieza": [{"resto": "espacio/enc_%s", "resultado": "%s", "causa": ""}], "recogida": [], "paso": "", "error": "", "coincide": %s, "tabla": "%s", "codigo": %s}\n' \
      "$m" "$version" "$m" "$m" "$m" "$filas" "$m" "$limpieza" "$coincide" "$tabla" "$codigo"
    exit "$codigo" ;;
  no_pudo)
    printf '{"paso": "encargar", "error": "motor encargar salió 1", "codigo": 2}\n'
    exit 2 ;;
  muda) exit 255 ;;
esac
FALSO
chmod +x "$S/bin/informe-falso"

# El intérprete del servicio de la Mac y la Mini: anota cómo lo llamaron y contesta.
cat >"$S/bin-servicio/python-servicio" <<FALSO
#!/bin/bash
m="\$(basename "\$HOME")"
printf '%s|%s|%s|%s\n' "\$m" "\$PATH" "\${GIT_CONFIG_VALUE_1:-}" "\$*" >>"\$LANZADOS"
v="FAKE_CANARIO_\$m"
exec "$S/bin/informe-falso" "\$m" "\${!v:-coincide}"
FALSO
chmod +x "$S/bin-servicio/python-servicio"

crear_maquina() { # <home> <sha>
  local home=$1
  rm -rf "$home"
  mkdir -p "$home/Library/LaunchAgents" "$home/.motor-encargos"
  git clone -q "$S/fuente" "$home/motor-sucesor"
  git -C "$home/motor-sucesor" checkout -q --detach "$2"
  cat >"$home/Library/LaunchAgents/ai.goncloud.motor-encargos.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>ai.goncloud.motor-encargos</string>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>$S/path-servicio:/usr/bin:/bin</string></dict>
<key>ProgramArguments</key><array><string>$S/bin-servicio/python-servicio</string><string>-B</string><string>-m</string><string>motor</string><string>supervisor</string><string>--base</string><string>$home/.motor-encargos</string></array>
<key>WorkingDirectory</key><string>$home/motor-sucesor</string>
</dict></plist>
PLIST
}

reset_maquinas() {
  crear_maquina "$S/mac" "$SHA"
  crear_maquina "$S/mini" "$SHA"
  crear_maquina "$S/pc" "$SHA"
  : >"$FAKE_LOG"
  : >"$LANZADOS"
  unset FAKE_CAE FAKE_CANARIO_mac FAKE_CANARIO_mini FAKE_CANARIO_pc \
    FAKE_VERSION_mac FAKE_VERSION_mini FAKE_VERSION_pc
}

cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >>"$FAKE_LOG"
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
case " ${FAKE_CAE:-} " in *" $host "*) echo "ssh: connect to host $host port 22: Connection refused" >&2; exit 255 ;; esac
crlf() { sed 's/\r$//' | sed 's/$/\r/'; }
case "$host" in
  gwpc)
    cmd="$*"
    case "$cmd" in
      *canario_lanzador.py*)
        "$S/bin/informe-falso" pc "${FAKE_CANARIO_pc:-coincide}" 2> >(crlf >&2) | crlf
        exit "${PIPESTATUS[0]}" ;;
      *' -c "'*)
        tmp="${cmd#* -c \"}"
        code="${tmp%%\"*}"
        rest="${tmp#*\"}"
        salida="$(HOME="$S/pc" USERPROFILE="$S/pc" python3 -c "$code" $rest)"; rc=$?
        [ -n "$salida" ] && printf '%s\n' "$salida" | crlf
        exit $rc ;;
      *'git -C '*)
        salida="$(bash -c "$cmd")"; rc=$?
        printf '%s\n' "$salida" | crlf
        exit $rc ;;
      *) echo "comando no reconocido por la PC: $*" >&2; exit 1 ;;
    esac ;;
  mac|mini) HOME="$S/$host" bash -c "$*" ;;
  *) exit 1 ;;
esac
FAKE
chmod +x "$S/bin/ssh"

SCRIPT="$RAIZ/ops/canario-encargos.sh"
REPO="dueno/canario"

echo "# las tres bien"
reset_maquinas
espera "sale 0 si coincide en las tres" 0 bash "$SCRIPT" --repo "$REPO"
contiene "dice la versión común" "$S/salida" "versión: mac mini pc en ${SHA:0:12}"
contiene "lo dice al final" "$S/salida" "el canario con ${SHA:0:12} coincide en: mac mini pc"
for m in mac mini pc; do
  contiene "$m: su tabla" "$S/salida" "## $m: coincide"
  contiene "$m: su encargo" "$S/salida" "encargo enc_$m: terminado en 35 min 21 s"
  contiene "$m: el que falla a propósito" "$S/salida" "encargo que falla a propósito enc_${m}_falla: terminado"
  contiene "$m: el del apagón" "$S/salida" "encargo con cortes enc_${m}_corte: terminado"
  contiene "$m: sus avances, con su nombre" "$S/salida" "  $m | canario [preparar] base lista; $m"
done
corrida="$(sed -n 's/.*corrida \(flota-[0-9TZ]*\).*/\1/p' "$S/salida" | head -n 1)"
[ -n "$corrida" ] || fallo "no dijo el nombre de la corrida"
for m in mac mini; do
  linea="$(grep "^$m|" "$LANZADOS")"
  [ "$(printf '%s' "$linea" | cut -d'|' -f2)" = "$S/path-servicio:/usr/bin:/bin" ] \
    || fallo "$m: el lanzador no corrió con el PATH del servicio: $linea"
  [ "$(printf '%s' "$linea" | cut -d'|' -f3)" = '!gh auth git-credential' ] \
    || fallo "$m: git no toma la credencial de gh: $linea"
  [ "$(printf '%s' "$linea" | cut -d'|' -f4)" = "-B $S/$m/motor-sucesor/ops/canario_lanzador.py --repo $REPO --corrida $corrida" ] \
    || fallo "$m: el lanzador no corrió desde su árbol con el repo y la corrida común: $linea"
done
grep -F 'canario_lanzador.py' "$FAKE_LOG" | grep -F gwpc >"$S/pc.cmd"
contiene "la PC usa el intérprete de su servicio" "$S/pc.cmd" '"C:\runtime\python.exe" -B'
contiene "la PC corre el lanzador de su árbol" "$S/pc.cmd" "$S/pc/motor-sucesor/ops/canario_lanzador.py"
contiene "la PC recibe el repo y la corrida común" "$S/pc.cmd" "--repo $REPO --corrida $corrida"
grep -q 'uv' "$FAKE_LOG" && fallo "la PC usó uv"
no_contiene "la salida de la PC llega sin CR" "$S/salida" $'\r'

echo "# una que no coincide"
reset_maquinas
export FAKE_CANARIO_pc=no_coincide
espera "sale 1" 1 bash "$SCRIPT" --repo "$REPO"
contiene "la PC no coincide" "$S/salida" "## pc: no coincide"
contiene "con su tabla" "$S/salida" "- termina: regresión"
contiene "las otras sí" "$S/salida" "## mini: coincide"
contiene "la nombra al final" "$S/salida" "el canario con ${SHA:0:12} falla en: pc"

echo "# una que coincide y no quedó limpia"
reset_maquinas
export FAKE_CANARIO_mac=sucio
espera "sale 1" 1 bash "$SCRIPT" --repo "$REPO"
contiene "dice que coincide y por qué falla" "$S/salida" "## mac: coincide, pero no quedó limpio"
no_contiene "no dice que no coincide" "$S/salida" "## mac: no coincide"
contiene "con lo que no borró" "$S/salida" "limpieza: espacio/enc_mac: falló"
contiene "la nombra al final" "$S/salida" "el canario con ${SHA:0:12} falla en: mac"

echo "# una que sale 0 sin tabla"
reset_maquinas
export FAKE_CANARIO_mini=sin_filas
espera "sale 1" 1 bash "$SCRIPT" --repo "$REPO"
contiene "no la cuenta como buena" "$S/salida" "## mini: sin tabla (salió 0)"
contiene "la nombra al final" "$S/salida" "falla en: mini"

echo "# una cuyo lanzador corrió en otra versión"
reset_maquinas
export FAKE_VERSION_pc="$OTRO"
espera "sale 1" 1 bash "$SCRIPT" --repo "$REPO"
contiene "dice en cuál" "$S/salida" "## pc: corrió en ${OTRO:0:12}, no en ${SHA:0:12}"
contiene "la nombra al final" "$S/salida" "falla en: pc"

echo "# una que no pudo correr"
reset_maquinas
export FAKE_CANARIO_mini=no_pudo
espera "sale 1" 1 bash "$SCRIPT" --repo "$REPO"
contiene "dice en qué paso" "$S/salida" "## mini: no pudo correr"
contiene "y por qué" "$S/salida" "no pudo encargar: motor encargar salió 1"

echo "# una que no contesta"
reset_maquinas
export FAKE_CAE=gwpc
espera "sale 1" 1 bash "$SCRIPT" --repo "$REPO"
contiene "lo dice antes de correr" "$S/salida" "FALLA pc: no contestó"
contiene "la versión es la de las que contestan" "$S/salida" "versión: mac mini en ${SHA:0:12}"
contiene "la nombra al final" "$S/salida" "falla en: pc"
for m in mac mini; do
  grep -q "^$m|" "$LANZADOS" || fallo "$m no corrió porque la PC no contestó"
done

echo "# una que se queda sin informe"
reset_maquinas
export FAKE_CANARIO_mac=muda
espera "sale 1" 1 bash "$SCRIPT" --repo "$REPO"
contiene "no inventa un resultado" "$S/salida" "## mac: sin informe (salió 255)"
contiene "dice dónde queda el informe" "$S/salida" "~/.motor-canario/canario-informe.json"

echo "# una en otra versión"
reset_maquinas
git -C "$S/mini/motor-sucesor" checkout -q --detach "$OTRO"
espera "sale 1" 1 bash "$SCRIPT" --repo "$REPO"
contiene "dice la versión de cada una" "$S/salida" "mini en ${OTRO:0:12}"
contiene "y la de las demás" "$S/salida" "mac en ${SHA:0:12}"
contiene "dice por qué no corre" "$S/salida" "flota mezclada no prueba nada"
[ ! -s "$LANZADOS" ] || fallo "corrió el canario en la Mac o la Mini con la flota mezclada"
grep -qF 'canario_lanzador.py' "$FAKE_LOG" && fallo "corrió el canario en la PC con la flota mezclada"

echo "# la orden mal dada"
reset_maquinas
espera "sin repo no corre" 2 bash "$SCRIPT"
espera "un repo que no es dueño/nombre no corre" 2 bash "$SCRIPT" --repo 'dueno/canario & calc'
espera "una máquina que no existe no corre" 2 bash "$SCRIPT" --repo "$REPO" --maquina 'mac nas'
espera "una máquina repetida no corre" 2 bash "$SCRIPT" --repo "$REPO" --maquina 'mac mini mac'
contiene "dice cuál se repite" "$S/salida" "repite 'mac'"
[ ! -s "$FAKE_LOG" ] || fallo "tocó las máquinas con la orden mal dada"
export MOTOR_CANARIO_REPO="$REPO"
espera "toma el repo de MOTOR_CANARIO_REPO" 0 bash "$SCRIPT" --maquina mini
unset MOTOR_CANARIO_REPO
contiene "solo en la pedida" "$S/salida" "el canario con ${SHA:0:12} coincide en: mini"
grep -q "^mac|" "$LANZADOS" && fallo "corrió en la Mac sin pedirla"

termina
