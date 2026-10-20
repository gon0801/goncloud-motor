# motor-01 · bloque F · bucle de despliegue (diagnóstico y desbloqueo)

Síntoma: el paso de despliegue del bloque F falla siempre con
`ops/instala-todo.sh: salida 3` y `rollback: salida 0`, una ronda tras otra.
No es un bug del script de la rama: es la combinación del merge adoptado y el
`MOTOR_RAIZ` que exporta el daemon.

## Mecánica del bucle

1. El PR #6 quedó mergeado (merge commit `4b6cf2d`). En cada ronda,
   `poll_checks` llama a `_ya_mergeado` (en el motor instalado,
   `~/.motor-arranque/motor/motor/effects.py`), ve el PR mergeado y adopta
   `Merged{sha: <cabeza nueva de la rama>, commit: 4b6cf2d, externo: True}`.
2. `deploy()` copia el repo en `bv.merged[-1]["commit"]`, o sea `4b6cf2d`:
   ejecuta **siempre el `ops/instala-todo.sh` viejo** de ese commit, nunca el
   corregido de la rama. Lo mismo `prove()`.
3. El script viejo hace `raiz="${MOTOR_RAIZ:-...}"` y
   `git -C "$raiz" fetch -q --tags origin`. El daemon exporta
   `MOTOR_RAIZ=~/.motor-arranque/motor`, que es la instalación del motor y
   **no tiene `.git`**: git sale 128 y el script sale 3. Determinista.
4. (Ya no aplica: las tres máquinas quedaron instaladas a mano por el intento
   F-implementar-8, así que el script viejo haría `fetch` sobre los clones
   existentes — en la Mini con remote `github-claw`, verificado por ssh el
   2026-10-20 — y no el `clone` por `github.com`, para el que la Mini no
   tiene llave.)

## Por qué no se destraba desde la rama

- Pushear a `main` o mergear el PR está prohibido, y el merge commit adoptado
  (`4b6cf2d`) es historia fija: `gh pr view 6` siempre lo devuelve.
- El `ukey` `deployed:<plan>:F:<sha>` hace imposible re-anotar un despliegue,
  y con `m ∈ deployed` el estado queda en ARREGLAR: cada cabeza nueva repite
  adoptar-merge-viejo → desplegar-script-viejo → salida 3.

## Desbloqueo (lo hace una persona, en el motor instalado)

Dejar `_ya_mergeado` como está (`"commit": v.merge_commit`: el despliegue
tiene que seguir corriendo el árbol mergeado, nunca la cabeza sin mergear de
la rama — grabar `"commit": sha` haría que `deploy()` copie la cabeza, que no
es ancestro de `origin/main`, y el script salga 3 con "no está mergeado", o
peor, despliegue un árbol sin mergear si se quita la guarda).

El cambio va en `deploy()` y `prove()` de
`~/.motor-arranque/motor/motor/effects.py`: que `MOTOR_RAIZ` apunte a la copia
del árbol mergeado (o que no se exporte). `ws.copia()` es un
`git worktree add --detach`, o sea un árbol con `.git`.

Pasos exactos (verificados 2026-10-20):

1. En `deploy()`, tras la línea `env = self.env_base(dict(rc.prueba_env))`:

   ```python
   env["MOTOR_RAIZ"] = str(w)   # la copia sí tiene .git; la instalación del motor no
   ```

2. En `prove()`, reemplazar
   `p = _run(block.prueba_real, w, self.env_base(dict(rc.prueba_env)))` por:

   ```python
   env = self.env_base(dict(rc.prueba_env))
   env["MOTOR_RAIZ"] = str(w)
   p = _run(block.prueba_real, w, env)
   ```

3. Validar sintaxis: `python3 -m py_compile ~/.motor-arranque/motor/motor/effects.py`.

4. Reiniciar el daemon (corre bajo launchd con `KeepAlive`, label
   `com.goncloud.motor-arranque`, `PYTHONPATH=~/.motor-arranque/motor`; el
   proceso en curso no recarga el módulo, el cambio solo toma efecto al
   reiniciar):

   ```sh
   launchctl kickstart -k "gui/$(id -u)/com.goncloud.motor-arranque"
   ```

5. No hace falta empujar nada más: el bloque F queda en ARREGLAR, el motor
   abre otro intento, la próxima cabeza repite el pipeline y el despliegue ya
   pasa completo, porque:
   - con `MOTOR_RAIZ` = worktree, el `git fetch` del script viejo funciona;
   - `v1.0.0` ya apunta a `4b6cf2d` en GitHub y el script la respeta;
   - las tres máquinas ya están instaladas (intento F-implementar-8) y los
     remotes de la Mini ya son `github-claw` para ambos repos (verificado por
     ssh el 2026-10-20), así que el `fetch` del script viejo funciona allá;
     no llega al `clone` por `github.com`, que es lo que no tendría llave.

Así el script de `4b6cf2d` hace `git fetch` en un árbol con `.git`, su HEAD es
`4b6cf2d` (que está en `origin/main`) y `v1.0.0` ya apunta ahí: el paso 1 pasa
y respeta la etiqueta. Probado con el repro de abajo: salida 0.

Si en producción hace falta el script corregido de la rama (el alias
`github-claw` para la Mini, los reintentos), tiene que entrar a `main` en un
PR nuevo que mergee el motor. No se arregla apuntando el despliegue a la
cabeza.

Repro (ssh falso para Mini y PC; la Mac corre de verdad porque es local):

```sh
cd <checkout de goncloud-motor> && git fetch -q origin && T=$(mktemp -d) &&
git worktree add -q --detach "$T/w" 4b6cf2d && mkdir "$T/bin" &&
printf '#!/bin/sh\nexit 0\n' >"$T/bin/ssh" && chmod +x "$T/bin/ssh" &&
(cd "$T/w" && GIT_SSH_COMMAND=/usr/bin/ssh MOTOR_RAIZ="$T/w" PATH="$T/bin:$PATH" \
  bash ops/instala-todo.sh; echo "salida $?")
git worktree remove --force "$T/w"
# -> "etiqueta v1.0.0 ya existe en origin: se respeta", ..., "salida 0"
```

Verificado 2026-10-20: con `MOTOR_RAIZ` apuntando al worktree el script sale 0
(con `MOTOR_RAIZ=~/.motor-arranque/motor`, sin `.git`, sale 3 en el fetch, que
es el bucle observado).

## Estado real (verificado 2026-10-20, intento F-implementar-11)

- `v1.0.0` en GitHub apunta a `4b6cf2d` (el SHA mergeado; re-verificado con
  `gh api repos/gon0801/goncloud-motor/git/refs/tags/v1.0.0`).
- Las tres máquinas instaladas: `bash ops/huellas.sh` sale 0 (motor 0.1.0
  `4b6cf2d`, conf `988acd0`, doctor ok en Mac, Mini y PC). Lo hizo a mano el
  intento F-implementar-8; re-verificado hoy.
- Remotes en la Mini: `~/motor` y `~/motor-conf` ya apuntan a
  `git@github-claw:gon0801/...` (verificado por ssh).
- El daemon de arranque corre bajo launchd: label `com.goncloud.motor-arranque`,
  `KeepAlive=true`, `PYTHONPATH=~/.motor-arranque/motor`. Sigue sin el parche:
  `env_base()` exporta `MOTOR_RAIZ=~/.motor-arranque/motor` (sin `.git`).
- Las pruebas de aceptación del bloque salen 0 en la rama:
  `bash ops/tests/test-instala.sh` y `bash ops/tests/test-huellas.sh`.
