# Cierre de las fases 3–4

Fases 3–4 completas en ramas de trabajo. Astra emitió **APPROVE** sobre el código hasta `fad4de2`. No se hizo push, merge, despliegue ni activación de fase 5. Los servicios operativos conservan su configuración.

## Resultado

Los directores pueden recibir un pedido normal, resolverlo directamente o entregarlo al motor sin convertir el plan del usuario en un programa de comprobaciones. La promoción conserva el encargo y cierra primero al ejecutor anterior. El procedimiento interno y sus instrumentos pueden corregirse sin cambiar el pedido ni repetir un efecto ya aplicado.

Hermes y Claw comparten la política de modelos y esfuerzos y el paquete completo de poteto. Los avisos pendientes se concilian con el chat de origen antes de repetir su envío. La distribución conserva las credenciales en cada máquina.

El controlador descubre herramientas oficiales, prepara instalaciones aisladas y conserva la generación del trabajo activo. Una candidata inválida no reemplaza la válida. Recuperación, aplicación y reversa usan el mismo candado y observan el árbol completo. La reversa independiente funciona aunque el motor nuevo no arranque.

Los plugins resuelven el CLI de su generación. Una herramienta no preparada se informa como no disponible; no se sustituye silenciosamente por un ejecutable global. En Windows, la entrada del motor usa UTF8 sin modificar el entorno global. Hermes usa su entrada oficial Python PM cuando Windows rechaza el lanzador generado; no se cambió AppControl.

## Evidencia comprobada

| Recorrido | Resultado y evidencia |
| --- | --- |
| Directores reales | Hermes PC y Claw Mini terminaron los encargos, produjeron la entrega y publicaron el resultado en sus chats de ensayo. Se comprobó reinicio del conector y conciliación de respuesta perdida. Drenar otra vez conservó 29 intentos, sin ejecutar de nuevo el trabajo. [Informe](evidence/correcciones-3-4/final-directores.md), [datos](evidence/correcciones-3-4/final-directores.json). |
| Actualización Mini | Claw 10.7→10.8 y Codex 0.163→0.164; generación ocupada conservada, integridad inválida rechazada, recuperación y reversa con motor inarrancable. [Datos](evidence/correcciones-3-4/componentes-final-mini.json). |
| Actualización PC | Once pasos completados. Hermes PM y Codex preparados; el plugin ejecutó realmente Codex A 0.163 y B 0.164, independientemente del global 0.163. [Datos](evidence/correcciones-3-4/componentes-final-pc.json). |
| Candidatos autenticados | Los SDK preparados de Claw y Hermes respondieron `CANDIDATO_OK` con cuenta de suscripción y dos lecturas correctas por turno. Configuraciones fuente intactas; perfiles y autenticación de ensayo eliminados. [Alcance y limpieza](evidence/correcciones-3-4/candidatos-modelo-real.md). |
| Política compartida | Mini y PC aceptaron el mismo snapshot; una importación inválida conservó la fuente anterior y una réplica no pudo promover modelos. [Datos PC](evidence/correcciones-3-4/politica-compartida-pc.json). |
| Fuentes oficiales | Ocho herramientas descubiertas; no se afirma haber instalado las ocho en cada máquina. Para modelos: cinco fuentes observadas y Kimi desconocido; política deseada v7 conservada. [Herramientas](evidence/correcciones-3-4/componentes-oficiales.json), [modelos](evidence/correcciones-3-4/catalogos-oficiales-final.json). |

Las preparaciones PC proceden de la base `3781fed` y del cambio de receta posteriormente confirmado en `c5f1153`. El ensayo de operaciones terminó con `7301e2b`; su última lectura de estado expuso el problema de codificación. Se comprobó esa lectura nativa con `fad4de2`, incluso forzando un entorno CP1252, sin reinstalar ni repetir las operaciones completadas. El informe conserva esta procedencia.

Los encargos de fase 3 usaron Grok con la suscripción existente. Se corrigieron el permiso de terminal sin interacción, el modelo seleccionado y la observación de eventos reales de herramientas. Los intentos anteriores permanecen en la evidencia. Las sondas de los SDK candidatos usaron Grok 4.7 como respaldo temporal declarado; no reescribieron las cadenas deseadas. La sonda Hermes aisló plugins/MCP y acredita SDK, cuenta y lecturas, no cadenas adicionales de ingeniería.

OpenBot mantiene el alcance ya comprobado de entrega y consulta; no se le atribuye una parada propia no demostrada. El nombre `openbot` de una clave de ensayo no convierte los avisos nativos de Hermes/Claw en evidencia de otro transporte. Muse mediante WSL no se ofrece dentro de una generación que no lo haya preparado; no se modificó su autenticación ni Keychain.

## Verificación final

- Motor: `python3 -m unittest tests.test_reversa_flota tests.test_encargo_motor06 tests.test_actualizaciones_flota -q`: **12 pruebas, OK**. Los avisos ResourceWarning de objetos Popen no correspondían a procesos vivos al comprobar sus PID.
- Hermes: `python3 -m unittest discover -s tests -p test_subscription_process.py -q`: **38 pruebas, OK, 10 omitidas por plataforma**. El contraste nativo de PC cubre la resolución real del plugin Windows.
- Ruff pasó para los módulos comprobados de flota, observación y entradas de ensayo. No se repitió una batería heredada ajena al cambio.
- Contención del candado comprobada en Windows durante más de diez segundos y en Mini. No quedaron trabajadores registrados ni perfiles OAuth de ensayo abiertos.

Los ensayos se pueden revisar en `ops/ensayo_directores_final.py` y `ops/ensayo_componentes_final.py`. Repetirlos implica volver a invocar agentes o instaladores; no es necesario para aceptar esta evidencia sin cambios de código.

## Ramas preparadas

| Repositorio | Rama | Código revisado |
| --- | --- | --- |
| Motor | `feat/encargos-3-4` | `fad4de2`; este cierre agrega evidencia y completa los campos del informe del ensayo |
| Hermes | `feat/encargos-3-4` | `30146347434758b19e828be10bcdc88adb0876cc` |
| Configuración | `feat/encargos-3-4` | `f02bd82a416aec562b313c1dcf4858d72349327a` |
| OpenClaw | `feat/encargos-3-4` | `4fcc61626139e5de74d880fc1e50c45a3c8a0321` |

## Activación pendiente: fase 5

La entrada preparada es `ops/flota_ciclo.py`, con `--home`, `--motor-origen`, `--repo-hermes`, `--repo-claw` y `--repo-conf`. El publicador usa `--salida` para el snapshot. Las réplicas reciben ese snapshot por transporte autenticado y usan `--replica`; no promueven modelos por separado. Las rutas concretas de instalación se fijan al desplegar, sin reutilizar directorios temporales de ensayo.

La receta operativa para la fase 5 es:

1. Guardar la configuración y el estado actuales. Instalar las ramas revisadas y conservar el runtime anterior para reversa.
2. En PC, localizar con `hermes -p claw cron list` el trabajo existente `vigia-modelos`, guardar su definición y pausar su ID real con `cron pause`. Reemplazar su invocación por la entrada única del controlador, con rutas estables. Ese trabajo es un cron de Hermes, no una tarea programada nueva de Windows.
3. Mantener un solo publicador de política en PC y distribuir su snapshot a las réplicas. La autoridad de encargos de Mini es independiente de esta elección.
4. Retirar los escritores competidores al activar el reemplazo. En Mini, `ai.goncloud.claw-autoupdate`; en Mac, revisar también `ai.goncloud.vigila-modelos`, que es observador. Para desactivar un launch agent de forma persistente, guardar su definición y usar `launchctl disable` en el dominio correcto además de `bootout`; no basta detenerlo hasta el siguiente inicio de sesión. No tocar `GoncloudVigia`, que vigila tareas y es ajeno a esta sustitución.
5. Comprobar una sola vez la entrada desplegada, la misma política en réplicas y un encargo real. Migrar pendientes con sus recibos; no repetir efectos para reconstruir historial. Retirar el flujo antiguo sólo después de esa comprobación.
6. Si falla la activación, usar la reversa independiente `ops/reversa_ensayo.py` con los recibos y runtime anterior preservados; restaurar la definición del cron y habilitar de nuevo los launch agents retirados. Mantener cuentas y credenciales locales fuera del snapshot publicado.

Estos pasos están documentados y **no fueron ejecutados** en este cierre. La fase 5 permanece pendiente.
