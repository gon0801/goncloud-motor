# MOTOR 09: cierre de B

**B aprobada para continuar con C. No autoriza despliegue ni declara recuperado el motor completo.**

David autorizó al lead corregir directamente el último hallazgo. El único cambio de implementación del lead es `motor/plataforma/windows.py:presencia_grupo`: devuelve `incierto` cuando aún no puede observar el Job, en vez de declarar `ausente` sin prueba.

Se amplió la sonda privada existente para recorrer también `_cierra` en pausa y cancelación y `_liberar`, con Store real y home temporal. Se ejecutó con Python nativo de PC sobre una copia temporal del código:

```sh
python3 -B .motor09/revision-B/windows-arbol.py
```

Resultado, salida 0:

```json
{"agente_muerto": true, "hijo_vivo": true, "observacion": "incierto", "arbol_ausente": false, "PausaSolicitada_sin_confirmar": true, "CancelacionSolicitada_sin_confirmar": true, "comando_liberar": null, "retenido": true, "sin_eventos_de_cierre": true}
```

Esto corrige el falso positivo observado en la tercera revisión: con un hijo vivo ya no confirma pausa/cancelación, no termina el intento y no retira mantenimiento. La sonda limpia únicamente los procesos que ella creó. No se modificaron tareas, servicios, homes productivos ni la instalación.

`git diff --check` pasa. Se conserva la evidencia válida anterior de cierre POSIX, recuperación del inbox, retención y sincronización de directorios Windows, porque este ajuste no cambia esos caminos. No se repitieron baterías ni se agregaron pruebas versionadas fuera de los casos de AGENTS. El resultado y las huellas están en `.motor09/resultado-B.json` y `.motor09/revision-B/huellas-cierre-B.json`.

Limitación explícita: en Windows, después de morir una identidad conocida, la falta de observación del Job mantiene incertidumbre incluso si ya no quedaran hijos. C debe aportar propiedad, observación y cierre verificable del árbol para resolverla. B permite continuar el desarrollo retenido; no permite reabrir producción con esa incertidumbre ni afirmar que el arranque Windows está arreglado. C–G siguen pendientes.

No se hizo commit, push, PR, merge, instalación, despliegue ni SIGCONT.
