# El supervisor de la PC se levanta solo

En la Mac launchd relanza el supervisor (`KeepAlive`). En la PC nadie lo hacía:
`~/.motor-encargos/supervisor.ps1` corría una sola vez y, si el proceso terminaba,
el motor de la máquina quedaba apagado hasta que alguien lo arrancaba a mano (1-nov,
17:23Z, "Motor de la PC: No heartbeat").

## Dónde vive el relanzamiento

En `ops/iniciar_encargos_pc.py`, el comando que ya arranca el supervisor. Es el único
componente que sabe dejar un proceso fuera del Job de SSH (Explorer COM, puente con
`CREATE_BREAKAWAY_FROM_JOB`, hijo libre). Un `supervisor.ps1` con bucle correría dentro
del Job de quien lo lance, y sin ese Job el motor no puede separar al trabajador ("el
entorno no deja separar el trabajador del Job"). `supervisor.ps1` sigue siendo el que
está instalado en `~/.motor-encargos`; no viene del repo.

La cadena es la de antes con un eslabón más:

    SSH (en Job) → Explorer COM → puente (en Job) → libre (fuera del Job)
        → vigilante (fuera del Job) → supervisor.ps1 (fuera del Job)

El proceso libre sale en segundos, como antes (el puente la espera hasta 60 s). El vigilante
queda vivo, lanza `supervisor.ps1` y, cuando el proceso termina, lo vuelve a lanzar. Como
el vigilante nace fuera del Job y sus hijos lo heredan, cada supervisor relanzado queda
también fuera. El vigilante comprueba la identidad de cada supervisor (mismo SID, misma
sesión, sin Job) y cierra el que no la cumpla.

## Espera entre intentos

5, 10, 20, 40, 80, 160 y 300 s (tope). Un supervisor que vivió 10 minutos o más reinicia
la cuenta en 5 s. Un lanzamiento que falla (por ejemplo, identidad rechazada) también
cuenta y se reintenta con la misma espera; el vigilante no se cae por eso.

## Instalación y uso

1. Copiar `ops/iniciar_encargos_pc.py` donde el operador lo ejecuta hoy (la copia de la PC
   vive en `C:\Users\ehven\.motor-resguardo\...`; el repo es la fuente).
2. Arrancar, igual que antes y desde SSH:

       python.exe -B <ruta>\iniciar_encargos_pc.py

   Imprime el informe del arranque con `vigilante` y `supervisor`. Es idempotente: si ya
   hay un vigilante, no abre otro y el informe trae `"ya_activo": true`.
3. Estado: `python.exe -B <ruta>\iniciar_encargos_pc.py --estado` muestra
   `~/.motor-encargos/vigilante.json` (pid del vigilante, pid e identidad del supervisor,
   lanzamientos).
4. Parar a propósito (por ejemplo, para desplegar): `... --parar`. El vigilante cierra el
   árbol del supervisor, no lo relanza y sale. El siguiente arranque borra esa petición.

Archivos en `~/.motor-encargos/`: `vigilante.log` (cada lanzamiento, código de salida y
espera; rota a 1 MB), `vigilante.json`, `vigilante.lock` (un solo vigilante por usuario) y
`vigilante.parar`.

Matar solo el supervisor lo hace volver en 5 s. Matar el vigilante deja al supervisor
vivo y sin vigilancia; el siguiente arranque cierra ese PowerShell huérfano antes de lanzar
el suyo, pero solo si es el mismo proceso: el PID sigue siendo `powershell.exe` y nació en el
instante que el vigilante guardó (`supervisor_inicio` en `vigilante.json`). Un PID
reutilizado, aunque sea de otro PowerShell, no coincide y no se toca; si no se puede
comprobar, tampoco. Si `taskkill` no consigue cerrar un supervisor, el vigilante lo intenta
con el propio proceso y, si sigue vivo, lo deja anotado en `vigilante.log` y en
`vigilante.json` para que el siguiente arranque lo cierre.

El arranque puede tardar hasta unos 45 s cuando hay un huérfano que comprobar y cerrar
(`tasklist` 10 s y `taskkill` 20 s como máximo): el informe espera 45 s al vigilante, el
puente 60 s y el comando completo 75 s.

## Lo que no hace

No sobrevive a un reinicio de la PC ni al cierre de la sesión de Windows: sigue haciendo
falta ejecutar el comando de arranque después, como hasta ahora. Instalarlo como tarea al
inicio de sesión es otro cambio.

## Comprobación real en la PC (después del despliegue)

1. Arrancar con el comando y ver `vigilante` y `supervisor` en el informe, con
   `in_job: false` y la sesión de Explorer. Antes de seguir, comprobar que `supervisor.ps1`
   se queda en primer plano: el vigilante lo relanza cada vez que su proceso termina, y el
   supervisor no tiene candado de instancia única. Si `supervisor.ps1` lanzara el motor en
   segundo plano y saliera, habría varios supervisores barriendo el mismo registro. Con el
   supervisor sano y sin tocar nada, esperar 1 o 2 minutos: `--estado` debe seguir con
   `"lanzamientos": 1` y `vigilante.log` no debe mostrar "el supervisor terminó". Si aparece,
   no dar por buena la comprobación: `--parar` y revisar `supervisor.ps1`.
2. Cerrar el proceso del supervisor (`taskkill /PID <pid del supervisor> /T /F`). En
   `vigilante.log` debe aparecer "el supervisor terminó" y "se vuelve a levantar en 5 s", y
   `--estado` debe mostrar un pid nuevo con `in_job: false`.
3. Repetir 2 o 3 veces seguidas: las esperas deben ser 5, 10, 20 s.
4. Con el supervisor relanzado, un encargo que lance un agente debe poder hacerlo (que no
   salga "el entorno no deja separar el trabajador del Job").
5. `--parar` termina el vigilante y el supervisor y no vuelven.

## Qué está probado y qué no

`tests/test_relanzamiento_pc.py` prueba con dobles la espera creciente, el reinicio de la
cuenta, el reintento tras un lanzamiento que falla, la parada, la identidad exigida, que
el supervisor relanzado se crea sin `BREAKAWAY` desde un vigilante fuera del Job, el
candado de un solo vigilante y la limpieza del huérfano. No se probó en Windows real:
Explorer COM, que el vigilante realmente nace fuera del Job, `msvcrt.locking`, `tasklist`
y `taskkill`, ni el efecto sobre el lanzamiento de agentes. Eso es la comprobación de
arriba.
