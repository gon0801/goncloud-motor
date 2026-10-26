# MOTOR 09: impedir falsa ausencia en Windows antes de C

Usa `pstack:poteto-mode`. Trabaja en `/Users/dn/dev/goncloud-motor-recuperacion`, rama `recuperacion/motor-09`, conservando el diff. Lee `plans/revisiones/motor-09-fase-B-ronda-3.md`.

Los dos casos anteriores de B ya pasaron la revisión del lead. Corrige solo el siguiente criterio: `motor/plataforma/windows.py:presencia_grupo` devuelve siempre `ausente` aunque no observe descendientes. El lead reprodujo en Python nativo de PC un agente muerto con hijo vivo y observación ausente:

```sh
python3 -B .motor09/revision-B/windows-arbol.py
```

Mientras Windows no pueda demostrar ausencia, devuelve/conserva observación incierta. Comprueba con esta sonda que `arbol_ausente` es falso cuando el hijo sigue vivo y que ese estado impide confirmar detención o liberar. No implementes ahora el Job de C; esa fase aportará la comprobación real. No declares ausencia por muerte del líder ni por falta de capacidad de inspección.

Conserva las correcciones ya verificadas de grupos POSIX, recuperación de liberación e I/O Windows. Actualiza `.motor09/resultado-B.json` con el comando y resultado real. No repitas una batería ni otras comprobaciones ya válidas si el cambio no las afecta. Respeta AGENTS.

No avances a C–G ni delegues. No toques `/Users/dn/motor`, homes productivos, servicios, tareas, configuración compartida o secretos. Nunca SIGCONT. No push, PR, merge, instalación ni despliegue. Entrega el ajuste y detente.
