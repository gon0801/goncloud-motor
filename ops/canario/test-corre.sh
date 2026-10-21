#!/usr/bin/env bash
# ops/canario/corre.sh con un ssh falso: entrega por el canal de cada marca, reinicia solo cuando
# hay un bloque a medias y el servicio está arriba, una sola vez por máquina, y nunca toca la Mac.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../tests/lib-prueba.sh"

C="$RAIZ/ops/canario/corre.sh"
export MOTOR_SSH_MINI=gon@mini-fake MOTOR_SSH_PC=gwpc
export MOTOR_CANARIO_SONDEO_S=0 MOTOR_CANARIO_TOPE_ENTREGA_S=2 MOTOR_CANARIO_TOPE_BLOQUE_S=2 MOTOR_CANARIO_TOPE_SERVICIO_S=2
E="$S/estado-motor"   # lo que contesta `motor estado` en cada máquina falsa
export E

cat >"$S/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
while [ "$1" = -o ]; do shift 2; done
host=$1; shift
args="${*//\\ / }"   # la Mini recibe el comando escapado con printf %q
echo "$host :: $args" >>"$FAKE_LOG"
f="$E/$host"
case "$args" in
  "tar -x"*|*"tar -x -C"*) cat >/dev/null ;;
  *"sudo -n -l"*) exit "${FAKE_SUDO_RC:-0}" ;;
  *' canario '*) cat >/dev/null; echo "{\"cortes\": ${FAKE_CORTES:-0}}" ;;
  *" entrega "*) [ -n "${FAKE_ENTREGA_MUDA:-}" ] || echo "${FAKE_FASE:-a_medias}" >"$f"; echo "tomado" ;;
  *oc-forja.sh*|*"hermes chat"*) [ -n "${FAKE_AGENTE_MUDO:-}" ] || echo "${FAKE_FASE:-a_medias}" >"$f"; echo "tomado" ;;
  *" estado "*) case "$(cat "$f" 2>/dev/null)" in
      a_medias) echo '[{"plan":"canario","bloques":[{"bloque":"A","estado":"implementando"}],"hecho":false}]' ;;
      corriendo) echo '[{"plan":"canario","bloques":[{"bloque":"A","estado":"esperando pruebas automáticas"}],"hecho":false}]' ;;
      hecho) echo '[{"plan":"canario","bloques":[{"bloque":"A","estado":"hecho"}],"hecho":true}]' ;;
      *) echo '[]' ;;
    esac ;;
  *launchctl*|*"schtasks /query"*) exit "${FAKE_SERVICIO_RC:-0}" ;;
  *shutdown*) exit 255 ;;
esac
exit 0
FAKE
chmod +x "$S/bin/ssh"

nuevo() { # estado limpio: nada entregado, sin marcas de reinicio
  rm -rf "$E" "$S/marcas"; mkdir -p "$E"; : >"$FAKE_LOG"
  export MOTOR_CANARIO_ESTADO="$S/marcas"
  unset FAKE_CORTES FAKE_SERVICIO_RC FAKE_AGENTE_MUDO FAKE_ENTREGA_MUDA FAKE_SUDO_RC FAKE_FASE
}
cuenta() { grep -cF -- "$2" "$1" || true; }
cuenta_reinicios() { grep -cE 'shutdown -r now|shutdown /r' "$FAKE_LOG" || true; } # los dos reinicios; no cuenta el `sudo -n -l shutdown`

nuevo
espera "entrega y reinicia las dos" 0 bash "$C"
contiene "Mini reiniciada" "$FAKE_LOG" "gon@mini-fake :: sudo -n shutdown -r now"
contiene "PC reiniciada" "$FAKE_LOG" "gwpc :: shutdown /r /t 0"
contiene "canal de claw" "$FAKE_LOG" "oc-forja.sh"
contiene "canal de Hermi" "$FAKE_LOG" "hermes chat"
[ "$(grep -n 'launchctl' "$FAKE_LOG" | head -1 | cut -d: -f1)" -lt "$(grep -n 'shutdown -r now' "$FAKE_LOG" | head -1 | cut -d: -f1)" ] ||
  fallo "la Mini se reinició antes de ver su servicio"
grep -qi 'mac' "$FAKE_LOG" && fallo "se habló con la Mac: $(grep -i mac "$FAKE_LOG")"

espera "repetir no reinicia ni entrega otra vez" 0 bash "$C"
[ "$(cuenta "$FAKE_LOG" 'shutdown -r now')" = 1 ] && [ "$(cuenta "$FAKE_LOG" 'shutdown /r')" = 1 ] || fallo "se reinició más de una vez cada una"
[ "$(cuenta "$FAKE_LOG" 'oc-forja.sh')" = 1 ] || fallo "se volvió a entregar a claw"

nuevo; export FAKE_SERVICIO_RC=1
espera "servicio caído: no reinicia" '!0' bash "$C"
[ "$(cuenta_reinicios)" = 0 ] || fallo "reinició con el servicio caído"
contiene "lo dice" "$S/salida" "servicio no está arriba"

nuevo; export FAKE_AGENTE_MUDO=1
espera "agente mudo: entrega directa y reinicia las dos" 0 bash "$C"
contiene "entregó sin agente" "$FAKE_LOG" " entrega "
[ "$(cuenta "$FAKE_LOG" 'shutdown -r now')" = 1 ] && [ "$(cuenta "$FAKE_LOG" 'shutdown /r')" = 1 ] || fallo "no reinició la Mini y la PC tras la entrega directa"

nuevo; export FAKE_SUDO_RC=1
espera "Mini sin sudo sin contraseña: no reinicia y falla" '!0' bash "$C"
[ "$(cuenta "$FAKE_LOG" 'shutdown -r now')" = 0 ] || fallo "reinició la Mini sin poder"
contiene "lo dice" "$S/salida" "sudoers"
[ ! -e "$MOTOR_CANARIO_ESTADO/mini.reiniciada" ] || fallo "marcó reiniciada una Mini que no se reinició"

nuevo; export FAKE_AGENTE_MUDO=1 FAKE_ENTREGA_MUDA=1
espera "el motor no toma el plan: no reinicia" '!0' bash "$C"
[ "$(cuenta_reinicios)" = 0 ] || fallo "reinició sin plan"
contiene "lo dice" "$S/salida" "no tomó el plan"

nuevo; export FAKE_FASE=hecho
espera "el plan termina sin pasar por a medias: no reinicia" '!0' bash "$C"
[ "$(cuenta_reinicios)" = 0 ] || fallo "reinició un plan ya terminado"

nuevo; export FAKE_CORTES=1; echo hecho >"$E/gon@mini-fake"; echo hecho >"$E/gwpc"
espera "ya hecho con corte anotado: cuenta como reiniciada sin tocar nada" 0 bash "$C"
[ "$(cuenta_reinicios)" = 0 ] && [ "$(cuenta "$FAKE_LOG" 'oc-forja.sh')" = 0 ] || fallo "tocó una máquina ya terminada"
[ -e "$MOTOR_CANARIO_ESTADO/mini.reiniciada" ] && [ -e "$MOTOR_CANARIO_ESTADO/pc.reiniciada" ] || fallo "no contó las dos como reiniciadas"

nuevo; export FAKE_FASE=corriendo
espera "corriendo sin bloque a medias: no reinicia" '!0' bash "$C"
[ "$(cuenta_reinicios)" = 0 ] || fallo "reinició sin bloque a medias"

nuevo; echo a_medias >"$E/gon@mini-fake"; echo a_medias >"$E/gwpc"
espera "el plan ya está en el motor: no se entrega de nuevo" 0 bash "$C"
[ "$(cuenta "$FAKE_LOG" 'oc-forja.sh')" = 0 ] || fallo "re-entregó un plan que el motor ya tenía"

nuevo
espera "si el host de la Mini es esta máquina, se niega" '!0' env MOTOR_SSH_MINI=localhost bash "$C"
[ "$(cuenta_reinicios)" = 0 ] || fallo "reinició con un host que es esta máquina"

nuevo
espera "plan inexistente" '!0' env MOTOR_CANARIO_PLAN="$S/no-existe.md" bash "$C"

nuevo; mkdir -p "$S/marcas"; : >"$S/marcas/mini.reiniciada"; : >"$S/marcas/pc.reiniciada"
espera "limpia.sh (el rollback del despliegue) existe y borra las marcas" 0 bash "$RAIZ/ops/canario/limpia.sh"
[ ! -e "$S/marcas/mini.reiniciada" ] && [ ! -e "$S/marcas/pc.reiniciada" ] || fallo "limpia.sh dejó marcas"

termina
