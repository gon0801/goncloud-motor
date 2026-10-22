#!/usr/bin/env bash
# ops/instala-todo.sh y ops/desinstala-todo.sh de verdad (git, bash y el motor reales) contra repos
# locales, un HOME de mentira por máquina y un ssh falso: la Mini corre su script en su HOME falso;
# la PC solo registra lo que se le pide. Exige ≠0 cuando el remoto falla y 0 cuando todo está bien.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

# --- repos locales: origen (el motor mergeado en main) y conf
git -C "$RAIZ" rev-parse HEAD >/dev/null || exit 2
git init -q --bare -b main "$S/origen.git"
git -C "$RAIZ" push -q "$S/origen.git" HEAD:refs/heads/main || exit 2
SHA="$(git -C "$S/origen.git" rev-parse main)"
git clone -q "$S/origen.git" "$S/trabajo"
git init -q -b main "$S/conf-src"
cp -R "$RAIZ/conf-ejemplo/." "$S/conf-src/"
mkdir -p "$S/conf-src/maquinas"
for m in mac mini pc; do printf 'nombre = "%s"\nconf = "~/motor-conf"\n' "$m" >"$S/conf-src/maquinas/$m.toml"; done
git -C "$S/conf-src" add -A
git -C "$S/conf-src" -c user.name=t -c user.email=t@t commit -q -m conf
git clone -q --bare "$S/conf-src" "$S/conf.git"

export MOTOR_REPO_URL="$S/origen.git" MOTOR_CONF_URL="$S/conf.git" MOTOR_INSTALA_RAIZ="$S/trabajo"
export MOTOR_SSH_MINI=gon@mini-fake MOTOR_SSH_PC=gwpc MOTOR_LAUNCHD_DIR="$S/launchd"
# La Mini clona por sus propias URLs (en la realidad, su alias de ssh github-claw; acá, otro
# nombre del mismo repo para ver en el log cuál usó).
ln -s origen.git "$S/origen-mini.git"
ln -s conf.git "$S/conf-mini.git"
export MOTOR_REPO_URL_MINI="$S/origen-mini.git" MOTOR_CONF_URL_MINI="$S/conf-mini.git"
mkdir -p "$S/launchd"

# --- ssh falso: la Mini corre de verdad en su HOME falso; la PC solo registra.
cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
echo "$host :: $*" >>"$FAKE_LOG"
case " ${FAKE_CAE:-} " in *" $host "*) echo "ssh: connect to host $host port 22: Connection refused" >&2; exit 255 ;; esac
if [ "$host" = gwpc ]; then
  case "$*" in
    "tar -x"*) cat >"$S/pc-recibido.tar" ;;
    "schtasks /query"*) exit "${FAKE_PC_TAREA_RC:-0}" ;;
    "echo ok") echo ok ;;
    # Estado de mentira de la PC: la etiqueta que tiene ~/motor y la marca de la marcha atrás.
    *"describe --tags --exact-match"*) [ -f "$S/pc-tag" ] && { cat "$S/pc-tag"; exit 0; } || exit 1 ;;
    *"checkout -q --detach refs/tags/"*) echo "${*##*refs/tags/}" >"$S/pc-tag" ;;
    "type "*"etiqueta-anterior"*) [ -f "$S/pc-etiqueta-anterior" ] && { cat "$S/pc-etiqueta-anterior"; exit 0; } || exit 1 ;;
    "echo "*"etiqueta-anterior"*) v="${*#echo }"; echo "${v%%>*}" >"$S/pc-etiqueta-anterior" ;;
    *) [ -z "${FAKE_PC_FALLA:-}" ] || { echo "git: no pude" >&2; exit 1; } ;;
  esac
  exit 0
fi
HOME="$S/mini" bash -c "$*"
FAKE
# sudo y launchctl falsos: instalar no debe llamar a sudo jamás; desinstalar lo usa con -n.
cat >"$S/bin/sudo" <<'FAKE'
#!/usr/bin/env bash
echo "sudo $*" >>"$S/sudo.log"
[ -z "${FAKE_SUDO_FALLA:-}" ] || exit 1
[ "$1" = -n ] && shift
case "$1" in
  launchctl) rm -f "$HOME/.cargado" ;;
  rm) shift; rm "$@" ;;
esac
FAKE
cat >"$S/bin/launchctl" <<'FAKE'
#!/usr/bin/env bash
[ "$1" = print ] && [ -e "$HOME/.cargado" ]
FAKE
chmod +x "$S/bin/"*

# git de verdad, pero con fallas de red a pedido: el primer fetch falla si FAKE_GIT_FETCH_FALLA=1
# (transitorio: un reintento lo absorbe) y todo ls-remote falla si FAKE_GIT_LSREMOTO_FALLA=1.
GIT_REAL="$(command -v git)"
cat >"$S/bin/git" <<FAKE
#!/usr/bin/env bash
if [ -n "\${FAKE_GIT_FETCH_FALLA:-}" ] && [ "\$1" = -C ] && [ "\$3" = fetch ]; then
  if [ ! -e "$S/fetch-fallo" ]; then touch "$S/fetch-fallo"; echo "fatal: cannot lock ref" >&2; exit 128; fi
fi
if [ -n "\${FAKE_GIT_LSREMOTO_FALLA:-}" ] && [ "\$1" = -C ] && [ "\$3" = ls-remote ]; then
  echo "fatal: no pude conectar" >&2; exit 128
fi
exec $GIT_REAL "\$@"
FAKE
chmod +x "$S/bin/git"

reset() { rm -rf "$S/mac" "$S/mini" "$S/sudo.log" "$S/pc-recibido.tar" "$S/pc-tag" "$S/pc-etiqueta-anterior" "$S/launchd"/*; mkdir -p "$S/mac" "$S/mini"; : >"$FAKE_LOG"; unset FAKE_CAE FAKE_PC_FALLA FAKE_SUDO_FALLA FAKE_PC_TAREA_RC MOTOR_ETIQUETA; }
I="$RAIZ/ops/instala-todo.sh"
D="$RAIZ/ops/desinstala-todo.sh"
en_mac() { HOME="$S/mac" "$@"; }

echo "# instalar"
reset
# El motor exporta MOTOR_RAIZ (su propia carpeta, sin git) a quien despliega: no debe romper la instalación.
export MOTOR_RAIZ="$S/no-es-repo"
en_mac espera "instala en las tres" 0 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.24^{commit}")" = "$SHA" ] || fallo "la etiqueta v1.0.24 no quedó en el SHA mergeado"
for m in mac mini; do
  [ "$(git -C "$S/$m/motor" rev-parse HEAD)" = "$SHA" ] || fallo "$m: ~/motor no está en la etiqueta"
  [ "$(git -C "$S/$m/motor" describe --tags --exact-match)" = v1.0.24 ] || fallo "$m: ~/motor sin v1.0.24"
  [ -d "$S/$m/motor-conf/.git" ] || fallo "$m: falta ~/motor-conf"
  cmp -s "$S/conf-src/maquinas/$m.toml" "$S/$m/.motor/maquina.toml" || fallo "$m: maquina.toml no es el de la conf"
  [ "$(readlink "$S/$m/.motor/conf")" = "$S/$m/motor-conf" ] || fallo "$m: ~/.motor/conf no enlaza a ~/motor-conf"
  contiene "$m txt" "$S/$m/.motor/instalar-servicio.txt" "motor instala --maquina $m"
  contiene "$m txt" "$S/$m/.motor/instalar-servicio.txt" "--imprime"
done
contiene "sudoers mac" "$S/mac/.motor/instalar-servicio.txt" "pmset"
contiene "sudoers mini" "$S/mini/.motor/instalar-servicio.txt" "/sbin/shutdown"
[ -e "$S/sudo.log" ] && fallo "instalar llamó a sudo"
tar -xf "$S/pc-recibido.tar" -O instalar-servicio.txt >"$S/pc.txt" 2>/dev/null
contiene "txt de la PC" "$S/pc.txt" "motor instala --maquina pc"
contiene "txt de la PC" "$S/pc.txt" "schtasks /create"
contiene "la PC usa la etiqueta" "$FAKE_LOG" "refs/tags/v1.0.24"
contiene "la PC clona la conf" "$FAKE_LOG" "motor-conf"
contiene "la Mini clona por su URL" "$FAKE_LOG" "mini v1.0.24 $S/origen-mini.git $S/conf-mini.git"
contiene "la PC enlaza la conf" "$FAKE_LOG" "mklink /j"
grep -qE 'dev/goncloud-motor|\.motor-arranque' "$FAKE_LOG" && fallo "tocó ~/dev/goncloud-motor o ~/.motor-arranque"
[ -e "$S/mac/dev" ] || [ -e "$S/mac/.motor-arranque" ] && fallo "creó carpetas ajenas en la Mac"
for m in mac mini; do [ ! -e "$S/$m/.motor/etiqueta-anterior" ] || fallo "$m: anotó etiqueta-anterior en una instalación nueva"; done
[ ! -e "$S/pc-etiqueta-anterior" ] || fallo "pc: anotó etiqueta-anterior en una instalación nueva"

en_mac espera "segunda corrida (actualiza, no reclona)" 0 bash "$I"
# La etiqueta no cambió: la marca de la marcha atrás no se toca.
for m in mac mini; do [ ! -e "$S/$m/.motor/etiqueta-anterior" ] || fallo "$m: anotó etiqueta-anterior sin cambio de etiqueta"; done
[ ! -e "$S/pc-etiqueta-anterior" ] || fallo "pc: anotó etiqueta-anterior sin cambio de etiqueta"

# una etiqueta avanzada por otro commit: la segunda corrida respeta la v1.0.24 existente
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m otro && git -C "$S/trabajo" push -q origin HEAD:main
en_mac espera "la etiqueta existente se respeta" 0 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.24^{commit}")" = "$SHA" ] || fallo "la etiqueta existente se movió"
for m in mac mini; do [ "$(git -C "$S/$m/motor" rev-parse HEAD)" = "$SHA" ] || fallo "$m: ~/motor dejó la etiqueta cuando main avanzó"; done

echo "# instalar con fallas"
reset; git -C "$S/origen.git" tag -d v1.0.24 >/dev/null
FAKE_CAE="gon@mini-fake" en_mac espera "ssh a la Mini falla" '!0' bash "$I"
contiene "dice cuál" "$S/salida" "mini"
[ -d "$S/mac/motor/.git" ] || fallo "la Mac debía instalarse aunque la Mini fallara"
reset
FAKE_PC_FALLA=1 en_mac espera "el remoto de la PC falla" '!0' bash "$I"
contiene "dice cuál" "$S/salida" "pc"

reset; git -C "$S/origen.git" tag -d v1.0.24 >/dev/null
git -C "$S/trabajo" checkout -q --detach HEAD
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m sin-mergear
en_mac espera "SHA sin mergear no se etiqueta" '!0' bash "$I"
git -C "$S/origen.git" rev-parse -q --verify refs/tags/v1.0.24 >/dev/null && fallo "etiquetó un SHA sin mergear"
git -C "$S/trabajo" reset -q --hard origin/main

# una v1.0.24 local (no está en origin) sobre un commit sin mergear no debe publicarse
reset; git -C "$S/origen.git" tag -d v1.0.24 >/dev/null 2>&1
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m viejo-sin-mergear
git -C "$S/trabajo" tag -f v1.0.24 >/dev/null
git -C "$S/trabajo" reset -q --hard origin/main
SHA="$(git -C "$S/trabajo" rev-parse HEAD)"
en_mac espera "etiqueta local vieja no se publica" 0 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.24^{commit}")" = "$SHA" ] || fallo "origin quedó con v1.0.24 en otro commit que el SHA mergeado"

echo "# red transitoria"
# El repo del despliegue lo comparte el daemon (fetcha seguido): un fetch puede chocar con un lock.
reset; git -C "$S/origen.git" tag -d v1.0.24 >/dev/null 2>&1; rm -f "$S/fetch-fallo"
FAKE_GIT_FETCH_FALLA=1 en_mac espera "un fetch que falla una vez se reintenta" 0 bash "$I"
[ "$(git -C "$S/origen.git" rev-parse "refs/tags/v1.0.24^{commit}")" = "$SHA" ] || fallo "no quedó la etiqueta tras el reintento"

# Sin respuesta de origin no se sabe si la etiqueta existe: peor que fallar es pisarla o duplicarla.
reset; git -C "$S/origen.git" tag -d v1.0.24 >/dev/null 2>&1
FAKE_GIT_LSREMOTO_FALLA=1 en_mac espera "ls-remote caído no es etiqueta ausente" '!0' bash "$I"
git -C "$S/origen.git" rev-parse -q --verify refs/tags/v1.0.24 >/dev/null && fallo "etiquetó sin poder consultar origin"

echo "# desinstalar"
reset; en_mac bash "$I" >/dev/null 2>&1
touch "$S/mac/.cargado" "$S/mini/.cargado" "$S/launchd/com.goncloud.motor.plist"
echo registro >"$S/mac/.motor/registro.log"
en_mac espera "descarga en las tres" 0 bash "$D"
[ -e "$S/mac/.cargado" ] && fallo "la Mac sigue cargada"
[ -e "$S/mini/.cargado" ] && fallo "la Mini sigue cargada"
contiene "la PC borra la tarea" "$FAKE_LOG" "schtasks /delete /tn GoncloudMotor"
[ -d "$S/mac/motor" ] && [ -d "$S/mac/motor-conf" ] && [ "$(cat "$S/mac/.motor/registro.log")" = registro ] || fallo "desinstalar borró carpetas o registros"
en_mac espera "ya descargado también es 0" 0 bash "$D"

touch "$S/mac/.cargado"
FAKE_SUDO_FALLA=1 en_mac espera "sin contraseña no puede descargar" '!0' bash "$D"
[ -e "$S/mac/.motor/desinstalar-servicio.txt" ] || fallo "no dejó los comandos para la persona"
FAKE_CAE="gon@mini-fake" en_mac espera "ssh a la Mini falla al desinstalar" '!0' bash "$D"
FAKE_PC_FALLA=1 FAKE_PC_TAREA_RC=0 en_mac espera "la PC no puede borrar la tarea" '!0' bash "$D"
FAKE_CAE=gwpc FAKE_PC_TAREA_RC=1 en_mac espera "sin ssh a la PC" '!0' bash "$D"

echo "# marcha atrás"
V="$RAIZ/ops/vuelve-a-la-anterior.sh"
reset
# v1.0.24 ya existe de las secciones de arriba sobre el mismo SHA: se borra para que la
# entrega nueva la cree sobre un commit distinto (si no, describe no distingue las etiquetas).
git -C "$S/origen.git" tag -d v1.0.24 >/dev/null
MOTOR_ETIQUETA=v1.0.23 en_mac espera "entrega anterior en v1.0.23" 0 bash "$I"
unset MOTOR_ETIQUETA
git -C "$S/trabajo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m avance && git -C "$S/trabajo" push -q origin HEAD:main
touch "$S/mac/.cargado" "$S/mini/.cargado"
en_mac espera "entrega nueva en v1.0.24" 0 bash "$I"
for m in mac mini; do
  contiene "marca $m" "$S/$m/.motor/etiqueta-anterior" "v1.0.23"
  [ "$(git -C "$S/$m/motor" describe --tags --exact-match)" = v1.0.24 ] || fallo "$m: no quedó en v1.0.24"
done
contiene "marca pc" "$S/pc-etiqueta-anterior" "v1.0.23"
[ "$(cat "$S/pc-tag")" = v1.0.24 ] || fallo "pc: no quedó en v1.0.24"

# Sin marca la máquina no se mueve y no es error.
rm "$S/mac/.motor/etiqueta-anterior"
en_mac espera "marcha atrás en las tres" 0 bash "$V"
[ "$(git -C "$S/mac/motor" describe --tags --exact-match)" = v1.0.24 ] || fallo "mac: se movió sin marca"
[ "$(git -C "$S/mini/motor" describe --tags --exact-match)" = v1.0.23 ] || fallo "mini: no volvió a v1.0.23"
[ "$(cat "$S/pc-tag")" = v1.0.23 ] || fallo "pc: no volvió a v1.0.23"
[ -e "$S/mac/.cargado" ] && [ -e "$S/mini/.cargado" ] || fallo "la marcha atrás descargó el servicio"
grep -q "schtasks /delete" "$FAKE_LOG" && fallo "la marcha atrás borró la tarea de la PC"
[ ! -e "$S/sudo.log" ] || fallo "la marcha atrás llamó a sudo"

# Una entrega que falla en una máquina, seguida de su marcha atrás: las otras dos vuelven a
# su etiqueta de antes y el servicio sigue cargado en las tres.
reset
MOTOR_ETIQUETA=v1.0.23 en_mac espera "base v1.0.23" 0 bash "$I"
unset MOTOR_ETIQUETA
touch "$S/mac/.cargado" "$S/mini/.cargado"
FAKE_CAE="gon@mini-fake" en_mac espera "entrega que falla en la Mini" '!0' bash "$I"
[ "$(git -C "$S/mac/motor" describe --tags --exact-match)" = v1.0.24 ] || fallo "mac: no quedó en v1.0.24"
[ "$(git -C "$S/mini/motor" describe --tags --exact-match)" = v1.0.23 ] || fallo "mini: se movió pese al ssh caído"
FAKE_CAE="gon@mini-fake" en_mac espera "marcha atrás con la Mini caída" '!0' bash "$V"
contiene "dice cuál" "$S/salida" "mini"
[ "$(git -C "$S/mac/motor" describe --tags --exact-match)" = v1.0.23 ] || fallo "mac: no volvió a v1.0.23"
[ "$(cat "$S/pc-tag")" = v1.0.23 ] || fallo "pc: no volvió a v1.0.23"
[ -e "$S/mac/.cargado" ] && [ -e "$S/mini/.cargado" ] || fallo "la marcha atrás descargó el servicio"
grep -q "schtasks /delete" "$FAKE_LOG" && fallo "la marcha atrás borró la tarea de la PC"
[ ! -e "$S/sudo.log" ] || fallo "la marcha atrás llamó a sudo"

termina
