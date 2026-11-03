#!/usr/bin/env bash
# ops/desbloquea-arranque.sh contra un arranque de mentira y un launchctl de mentira que
# SIGUE ESCRIBIENDO después de la línea que coincide: así cae el `grep -q` bajo pipefail
# (grep corta el pipe, launchctl recibe SIGPIPE y "el daemon no está" con el daemon sano).
# No toca nada real: el arranque y el launchctl viven en un tmp desechable.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib-prueba.sh"

B="$RAIZ/ops/desbloquea-arranque.sh"
export ARRANQUE_HOME="$S/arranque"
export FAKE_LAUNCHD_LOG="$S/launchd.log"
mkdir -p "$ARRANQUE_HOME/motor/motor"

# effects.py de mentira: solo necesita compilar y traer cada ancla del parche una vez.
cat >"$ARRANQUE_HOME/motor/motor/effects.py" <<'PY'
# motor de mentira: compila y trae las dos anclas del parche, nada más
class Efectos:
    def deploy(self):
        w = object()
        rc = {}
        env = self.env_base(dict(rc.prueba_env))
        if not desplegar:
            pass

    def prove(self):
        if True:
            w = object()
            rc = {}
            p = _run(block.prueba_real, w, self.env_base(dict(rc.prueba_env)))
PY

cat >"$S/bin/launchctl" <<'FAKE'
#!/bin/sh
label=com.goncloud.motor-arranque
log="${FAKE_LAUNCHD_LOG:-/dev/null}"
case "$1" in
  list)
    printf 'PID\tStatus\tLabel\n'
    printf '64789\t0\t%s\n' "$label"
    sleep 0.4
    printf 'más salida tras la coincidencia: quien corte el pipe temprano recibe SIGPIPE\n'
    ;;
  kickstart) printf 'kickstart %s\n' "$*" >>"$log" ;;
esac
FAKE
chmod +x "$S/bin/launchctl"

# El launchctl de mentira reproduce la trampa: un consumidor con grep -q bajo pipefail
# no ve el daemon. Si esta prueba falla, el falso dejó de ser hostil y no está vigilando nada.
espera "launchctl+grep -q+pipefail no ve el daemon (la trampa)" '!0' \
  bash -c 'set -o pipefail; launchctl list 2>/dev/null | grep -q com.goncloud.motor-arranque'

# Estado: el dry-run muestra y no toca.
cp "$ARRANQUE_HOME/motor/motor/effects.py" "$S/effects-antes"
espera "estado sin --aplicar" 0 bash "$B"
contiene "dice sin parche" "$S/salida" "parcheado: no"
contiene "ve el daemon corriendo" "$S/salida" "corriendo"
cmp -s "$S/effects-antes" "$ARRANQUE_HOME/motor/motor/effects.py" || fallo "el dry-run tocó effects.py"
[ -e "$FAKE_LAUNCHD_LOG" ] && fallo "el dry-run reinició el daemon"

# Aplicar: parchea las dos anclas, compila, reinicia y lo verifica.
espera "aplicar parchea, compila y reinicia" 0 bash "$B" --aplicar
[ "$(grep -c 'env\["MOTOR_RAIZ"\] = str(w)' "$ARRANQUE_HOME/motor/motor/effects.py")" = 2 ] \
  || fallo "el parche no quedó en deploy() y prove()"
contiene "dice reiniciado" "$S/salida" "reiniciado con el parche"
contiene "reinició el daemon" "$FAKE_LAUNCHD_LOG" "kickstart -k"
ls "$ARRANQUE_HOME"/motor/motor/effects.py.bak-* >/dev/null 2>&1 || fallo "no dejó respaldo"
python3 -m py_compile "$ARRANQUE_HOME/motor/motor/effects.py" || fallo "el effects.py parcheado no compila"

# Idempotente: la segunda corrida ya no toca effects.py, solo reinicia.
cp "$ARRANQUE_HOME/motor/motor/effects.py" "$S/effects-parcheado"
espera "segunda corrida solo reinicia" 0 bash "$B" --aplicar
contiene "dice que ya estaba" "$S/salida" "ya estaba parcheado"
cmp -s "$S/effects-parcheado" "$ARRANQUE_HOME/motor/motor/effects.py" || fallo "la segunda corrida volvió a tocar effects.py"

# El parche que aplica la persona y el arreglo del repo son el mismo cambio.
[ "$(grep -c 'env\["MOTOR_RAIZ"\] = str(w)' "$RAIZ/motor/effects.py")" -ge 2 ] \
  || fallo "motor/effects.py del repo no lleva el arreglo que el parche aplica"

termina
