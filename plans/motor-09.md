# MOTOR 09: arreglar el motor y devolverle control al operador

**Seguimiento manual por fases. A completada y revisada por el lead. B aprobada para continuar a C; ver `revisiones/motor-09-fase-B-cierre.md`. C+D aprobadas para continuar E; ver `revisiones/motor-09-fase-CD-cierre.md`. E corregida y comprobada para preparar PR; ver `revisiones/motor-09-fase-E-cierre.md`. No entregar este archivo al motor.**

El pedido de David del 25 de octubre de 2026 autoriza diseñar los arreglos y el plan, sin implementar. Lee primero el [diseño](revisiones/motor-09-diseno.md). Este plan conserva el historial y repara el motor existente hacia delante. No modifica los planes de Orbit ni la elección de modelos.

Actualización de autorización: David autorizó después implementar con Grok y revisión del lead, y luego cambió el flujo a manual. Posteriormente autorizó al lead corregir directamente el último hallazgo de B; ese ajuste quedó comprobado y documentado en el cierre de B. El lead ya detuvo su ejecución automática de Grok. David envía cada brief; el implementador entrega esa fase y se detiene. El lead revisa y prepara correcciones o el brief siguiente. David autorizó el 26 de octubre terminar, integrar, desplegar y limpiar; F y G están autorizadas y en ejecución.

## Cómo ejecutar después de recibir autorización

Trabaja en una copia aislada de `origin/main` del repo `gon0801/goncloud-motor`, en una rama nueva `recuperacion/motor-09`. Usa `pstack:poteto-mode` con un único implementador para los archivos compartidos. Una revisión agrupada comprueba seguridad, datos y comportamiento pedido. No se requieren diez carriles, otro kit ni revisión por cada función.

La base investigada es `1b37d63`, v1.0.34, en `/Users/dn/motor`. `/Users/dn/dev/goncloud-motor` contiene estos documentos pero su código está atrasado. Al comenzar observa `origin/main`; si cambió, revisa solo los cambios que afecten este contrato y actualiza su base antes de editar. No ejecutes ni instales desde el checkout productivo.

Estos dos documentos todavía son archivos locales. Antes de trabajar en el checkout aislado, copia allí `plans/motor-09.md` y `plans/revisiones/motor-09-diseno.md` desde esta carpeta. No supongas que ya existen en `origin/main` ni copies el código atrasado junto con ellos.

Cada casilla se marca solo con evidencia de la comprobación indicada. Guarda evidencia saneada por fase y SHA fuera del repo, en una carpeta privada `motor09-evidencia-<UTC>`. El valor UTC se obtiene con `date -u +%Y%m%dT%H%M%SZ`. Conserva un registro de decisiones y resultados. Los logs crudos, bases y secretos nunca van al PR.

| Operación | Estado en este encargo |
| --- | --- |
| Escribir diseño y plan, leer código y estado | Autorizado. |
| Implementar B a E | B–E completadas; el lead está autorizado a cerrar correcciones necesarias. |
| Crear PR, merge o modificar planes productivos activos | Autorizado por David el 26 de octubre para cerrar MOTOR 09. |
| Instalar, revertir, reanudar o habilitar servicios | Autorizado el 26 de octubre, preservando control y resguardos. |
| Borrar trabajo, bajar formato o restaurar una base vieja | Fuera del diseño. |

## Resultado observable y reparto

David puede entregar un plan y distinguir recepción, aceptación y ejecución. Puede detenerlo sin que se relance, consultar fallas aunque Telegram no responda y recuperar el trabajo tras reinicio sin repetir efectos externos. La instalación repetida conserva una reversa real y compatible.

El lead conserva el contrato y la evidencia. El implementador realiza las fases A a E serialmente porque comparten `daemon.py`, `effects.py`, `machine.py` y formato. La fase E puede prepararse aparte, pero no se instala por separado. Un revisor independiente concentra sus hallazgos en una ronda. La operación del sistema en F necesita administrador real en Mac y Mini. Esa capacidad no está disponible en la sesión investigada y no se sustituye por matar procesos repetidamente.

| Tarea | Contenido | DoD | Depende | Estado |
| --- | --- | --- | --- | --- |
| A | `[lane:gate]` Preservar evidencia y fijar base. | Copia consistente abre, metadatos y árboles inventariados; sin cambio productivo. | - | cc:完了, [revisión A](revisiones/motor-09-fase-A.md) |
| B | `[lane:gate]` Control durable y formato 3. | Pausa/cancelación sobreviven reinicio y ningún escritor antiguo ignora la orden. | A | cc:TODO |
| C | `[lane:gate]` Lanzamiento Windows y recibo de inicio. | Job restrictivo no produce falso inicio; árbol y supervivencia requeridos comprobados. | B | cc:TODO |
| D | `[lane:gate]` Aceptación y avisos recuperables. | Rechazo sin plan activo, mismo recibo al repetir y aviso recuperado o fallo visible. | C | cc:TODO |
| E | `[lane:gate]` Instalación y reversa repetibles. | Reinstalación preserva anterior, fallo parcial conserva origen y reversa verifica pareja/formato. | D | cc:TODO |
| F | `[lane:release]` Sustituir instalaciones contenidas. | Servicios viejos descargados, árboles ausentes, nuevo motor retenido en las tres. | E | cc:TODO |
| G | `[lane:release]` Recorrido controlado y reapertura. | Recepción, ejecución, aviso, pausa, reinicio y cancelación observados por máquina. | F | cc:TODO |

## A. Preservar y fijar la base

- [ ] Tras la autorización de ejecución, observa SHA de código y configuración, servicios, identidades de procesos y archivos pendientes en cada máquina. No reutilices PID sin comprobar su inicio.
- [ ] Conserva SQLite mediante `sqlite3.Connection.backup` sobre conexión de solo lectura, incorporando correctamente WAL. Copia inbox, recibos, intentos, resultados, worktrees, `formato`, `conf-buena`, `etiqueta-anterior` y configuración local privada. No copies credenciales al repositorio.
- [ ] Comprueba `PRAGMA integrity_check` de las copias y reconstrucción de eventos. Guarda la base del análisis y el estado de contención. No cambies el historial original.

## B. Poder parar antes de volver a trabajar

Archivos permitidos: `motor/control.py` nuevo, `motor/store.py`, `motor/aparte.py`, `motor/__main__.py`, `motor/plan.py`, `motor/servicio.py`, `motor/daemon.py`, `motor/machine.py`, `motor/effects.py`, `motor/runwrap.py`, `motor/formato.py` y las comprobaciones pertinentes existentes. El `Spec delta` está en el diseño; incorpóralo en `docs/diseno.md` junto con este cambio futuro.

- [ ] Implementa exactamente los comandos, estados, plazo de 10 segundos y recibos definidos en el diseño. El CLI no escribe SQLite. La retención local no depende de configuración válida ni del candado vitalicio del daemon.
- [ ] Registra pausa/cancelación antes de detener. No confirmes detención con árboles vivos. Un efecto remoto por conciliar sigue visible y no se repite. Una nota o reentrega no revive una cancelación.
- [ ] Eleva el marcador 2 a 3 antes de la semántica nueva y protege todos los escritores. Conserva lectura de eventos antiguos y rechaza reversas incompatibles.
- [ ] Define `Store.append_batch` para las transiciones de control y sus recibos. La fase D reutiliza esa interfaz. Protege también el camino escritor de `aparte` antes de habilitar formato 3.
- [ ] En un home desechable ejecuta activar retención, pausar, reiniciar, intentar liberar con generación vieja y cancelar. Comprueba cero nuevos agentes, trabajo conservado y rechazo de escritor v1.0.34 ante formato 3. Se permite comprobación de detención del sistema; no una prueba nueva por cada estado.

## C. Corregir el arranque sin perder el control del árbol

Archivos permitidos: `motor/plataforma/__init__.py`, `motor/plataforma/windows.py`, `motor/runwrap.py`, `motor/effects.py`, `motor/machine.py` y `tests/test_windows.py`.

- [ ] Migra los tres lanzamientos a la política común con propiedad explícita. Un único fallback por `winerror=5` quita solo `BREAKAWAY`; otros errores y fallas posteriores no repiten creación.
- [ ] Distingue reserva, creación fallida, proceso observado y arranque por conciliar. Un error de creación conserva su causa, no registra `AttemptStarted` vacío ni gasta intentos del modelo. Concilia los marcadores antiguos sin clasificarlos retrospectivamente.
- [ ] Añade una regresión del incidente Windows en `tests.test_windows.WindowsTest.test_job_restringido_permite_lanzar_y_cerrar`, nombre previsto. Exige creación y cierre del árbol en el Job restrictivo, sin falso inicio ni relevo por error de permisos.
- [ ] Ejecuta esa regresión en Windows, con el Python real del motor. Reutiliza `test_proceso_separado_sobrevive_al_cierre_del_daemon`, `test_matar_grupo_termina_el_arbol` y `test_job_mata_al_arbol_al_cerrar_el_handle` de la misma clase. Un skip en Mac no acredita Windows.
- [ ] Comprueba el contexto real del servicio en un home de diagnóstico. Observa identidad del wrapper al reiniciar solo daemon y ausencia de padre, hijo y nieto tras parar. Si no conserva su propiedad, informa entorno incompatible y no instales un broker ni declares aprobado un simple `echo`.

## D. Aceptar y avisar sin contradicciones

Archivos permitidos: `motor/store.py`, `motor/daemon.py`, `motor/effects.py`, `motor/notify.py`, `motor/conf.py`, `motor/__main__.py` y `motor/doctor.py`.

- [ ] Implementa `prepara_repos` sin eventos y `append_batch` atómico. Aceptación, pines, recibo y solicitud de aviso quedan juntos. Repetir `cmd_id` devuelve la decisión anterior; un fallo del Store deja el inbox pendiente.
- [ ] Materializa avisos con ID estable desde `NoticeRequested`. Confirma entrega solo con `ok=true` válido. Conserva fallos definitivos visibles, reintentos explícitos y recarga coherente de notifier. No reenvíes masivamente el historial.
- [ ] Ejecuta las reproducciones aisladas de repo inaccesible, fallo al encolar, reinicio entre commit y movimiento de inbox, y respuesta de transporte inválida. Usa dobles locales de red y home desechable. Exige rechazo sin activación, mismo recibo al repetir y aviso pendiente, entregado o fallido con evidencia. No agregues pruebas nuevas para incidentes de producción que no ocurrieron.
- [ ] Comprueba que `estado`, `doctor` y `recibo` no muestran entrega por cola vacía ni un ID desconocido como encolado. Un fallo global tiene causa visible aunque Telegram esté caído.

## E. Conservar una reversa que realmente sirva

Archivos permitidos: `ops/instala-todo.sh`, `ops/vuelve-a-la-anterior.sh`, `ops/lib.sh`, `ops/huellas.sh`, `ops/tests/test-instala.sh` y `motor/formato.py`.

- [ ] Añade `--maquina` y `--conf-sha`, opciones previstas. Resuelve código y configuración exactos y valida antes de tocar instalaciones o marcas. Mantén las instalaciones retenidas.
- [ ] Persiste recibo por máquina con origen, destino y etapa. Una reinstalación no cambia el anterior. Un intento parcial reutiliza su origen. La reversa valida pareja y formato antes de mutar, sin rebajar marcador.
- [ ] Extiende la prueba de instalación existente con una reproducción del incidente de reinstalación. Recorre misma versión, rechazo previo y recuperación parcial en repos y homes desechables. Ejecuta `bash ops/tests/test-instala.sh` desde el checkout del cambio.
- [ ] Reutiliza `python3 -B -m unittest -v tests.test_motor08_efectos.RecuperaEfectosTest.test_caida_tras_lanzar_recupera_resultado_sin_repetir_instalacion`. La parte PC simulada del script no sustituye comprobar instalación y reversa reales en Windows aislado.

## F. Cambiar instalaciones manteniendo el motor retenido

Esta fase es operación futura. Requiere autorización de despliegue y administrador en ambas Mac. No se ejecuta en la revisión del plan.

- [ ] Publica un único PR coherente con las fases B a E, revisión agrupada y comprobaciones pertinentes sobre su SHA final. Respeta los hooks vigentes, sin reinstalar Ruff ni el kit retirado. Si CI ya validó un comando en ese SHA, reutiliza esa evidencia.
- [ ] En Mac y Mini ejecuta con administrador `sudo launchctl disable system/com.goncloud.motor` y `sudo launchctl bootout system/com.goncloud.motor`. Comprueba descarga y deshabilitación. En PC confirma `GoncloudMotor` deshabilitada. Nunca envíes `SIGCONT` a los procesos viejos.
- [ ] Con el relanzamiento impedido, comprueba identidades y termina árboles suspendidos, preservando sus archivos. Confirma ausencia de descendientes y liberación de candados antes de intercambiar código. Sin administrador o identidad fiable conserva contención y no avances.
- [ ] Fija la etiqueta nueva al SHA mergeado y captura `CONF_SHA` exacto. Desde ese checkout usa el comando futuro `bash ops/instala-todo.sh --maquina pc --conf-sha "$CONF_SHA"`; después usa `mini` y `mac`. No pases a la siguiente máquina sin observar pareja instalada y retención conservada en la anterior.
- [ ] Arranca solo el binario nuevo en mantenimiento. Recoge resultados y concilia operaciones antes de cancelar o pausar `motor-win-spawn`. No ejecutes su bloque C como prueba de este arreglo. Habilita el servicio nuevo todavía retenido solo después de las comprobaciones de esa máquina.

## G. Comprobar un recorrido y reabrir de forma gradual

- [ ] Usa un único plan de diagnóstico y un home aislado. Comprueba recepción durable, agente real elegido por configuración, resultado, aviso en destino de diagnóstico, pausa durante trabajo, reinicio sin segundo agente, reanudación y cancelación terminal. Conserva recibos, identidades, SHA y resultados saneados.
- [ ] Comprueba que un efecto sintético incierto no se repite al reanudar. No escribas producción para demostrarlo. Registra límites de la prueba y no uses un `echo` de SSH como evidencia de ejecución.
- [ ] Tras ese recorrido en cada máquina, observa el estado de planes reales, confirma la conciliación necesaria y libera la generación de mantenimiento correspondiente. No reanudes planes ajenos por defecto. Informa qué quedó retenido, cancelado o habilitado.

## Comprobaciones y límites

Los comandos nuevos de B, la prueba nueva de C y las opciones de E están marcados como previstos. No se pueden ejecutar hoy. Usa `python3 -B -m unittest -v <clase.metodo>` con el intérprete que se haya observado para esa máquina; `<clase.metodo>` se sustituye únicamente por los nombres concretos de C y E. No se propone pytest.

No se escribe una prueba por casilla. Las pruebas nuevas se limitan a dinero, datos de producción, instalar/borrar/deshacer y una falla que ya pasó en producción. Las observaciones aisladas restantes comprueban comportamiento sin agregar otra batería. CI actual compila, revisa secretos y corre los hooks existentes; su verde no demuestra el contexto de Windows ni un despliegue correcto.

| Situación | Acción definida |
| --- | --- |
| No hay administrador para descargar launchd | Mantén la suspensión. B a E pueden construirse aisladas; F no empieza. |
| Configuración inválida | Retención y consulta local disponibles; no prometas daemon completo ni Telegram. |
| No se confirma identidad de proceso | No mates por PID solamente. Conserva retención y recoge evidencia. |
| Lanzamiento o efecto por conciliar | No gires a otro modelo ni repitas la acción. Resuelve con el recibo concreto o conserva incierto visible. |
| Fallo de supervivencia o contención Windows | No declares apta esa máquina. Corrige la frontera dentro de C; no introduzcas otro servicio sin revisar el diseño. |
| Fallo parcial de instalación | Conserva el origen y etapa del mismo intento, retén la máquina y no avances a la siguiente. |
| Telegram falla | Conserva estado y aviso pendiente o fallido; reporta por el canal local disponible, sin filtrar credenciales. |
| Un revisor encuentra falla de seguridad, datos o comportamiento pedido | Corrige con su comando de reproducción en la misma ronda. Lo demás no abre un ciclo general. |

## Revisión del plan y entrega

El diseño combina dos candidatos y un juez independiente. Un lector nuevo y un revisor de contradicciones revisaron ambos documentos, sin bloqueantes abiertos; sus notas de alcance y deduplicación se incorporaron. La [revisión cruzada de Astra](revisiones/motor-09-astra.md), pedida por David, concluyó `APPROVE` sin hallazgos. Solo un bloqueante reproducible reabre una ronda. Todas las tareas permanecen `cc:TODO`. La aprobación cubre documentos, no código ni despliegue.

No se usa `check-plan.mjs` de pstack para imponer diez carriles, pruebas de rendimiento, capturas o reapertura por procedimiento. Ese formato contradice las reglas vigentes de David. La comprobación documental es leer contratos, referencias y comandos, y `git diff --check`.

La memoria externa de harness no fue consultable mediante un conector en esta sesión. Se reutilizaron el historial, documentos de diseño, investigación how/why y estado real del proyecto. No se asume ausencia de decisiones fuera de esas fuentes.

Para una sesión futura, abre el agente desde el checkout de recuperación y dale esta instrucción solo después de autorizar ejecución: "Implementa MOTOR 09 conforme a plans/motor-09.md y su diseño. Mantén los servicios retenidos. No despliegues ni reanudes sin autorización para F y G." No uses una orden de bucle ni entregues el plan al motor averiado.
