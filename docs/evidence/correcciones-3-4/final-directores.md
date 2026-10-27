# Recorrido final de directores

Completado en perfiles propios, sin activar servicios operativos. La evidencia seleccionada está en final-directores.json; no contiene autenticación.

## Resultado observado

| Director real | Encargo conservado | Promoción canónica | Resultado | Aviso al mismo chat |
| --- | --- | --- | --- | --- |
| Hermes, PC | enc_f50772f827d39a15 | rec_297d0b6fe91b | terminado, 16:43:02Z | hermes:local-0d0a8363dff4e3e9ec11201b056ec129116b9c4fd88e7d0cdf8bc76dd24a64e1:6d3a9eef755845f889646d23091d4c50 |
| Claw, Mini | enc_62309c037bc14b39 | rec_e0137743a854 | terminado, 16:44:22Z | claw:ensayo-final34-promocion-2:8de9edaa-4e89-4f27-b3ef-12f0192d6a4a |

Las horas son UTC del 27 de octubre de 2026. Cada director usó sus herramientas reales para cerrar la sesión anterior, observar ausencia y promover el mismo encargo. El escritor directo era un proceso Python de ensayo que escribía periódicamente un archivo; no se presentó como un agente LLM. El cierre se produjo sobre procesos reales, no cambiando solamente un JSON. Los responsables y revisores durables sí fueron Grok reales por suscripción, dentro de la jaula nativa.

En ambos productos, terminado.txt contiene exactamente los 24 bytes de entrega final comprobada, sin salto de línea. Los responsables generaron procedimiento.json y evidencia-comportamiento.txt. El supervisor ejecutó /bin/cat; los revisores inspeccionaron el producto y esas observaciones y emitieron juicios confirmados. No hubo solicitudes de efectos comerciales ni escrituras de producción.

Los turnos originales de Hermes y Claw ya habían terminado. El gateway propio de Hermes se detuvo mediante su API nativa, validando su dueño, y volvió con PID diferente: 10816 a 4608. Claw volvió al mismo chat con otro proceso CLI. Los supervisores también se ejecutaron en procesos nuevos entre pasos. La autoridad de ensayo estuvo en la Mini, separada de la instalada.

## Avisos y conciliación

La entrega usó los chats CLI reales de los perfiles de ensayo. El alias de canal heredado en los datos del instrumento es openbot, pero su configuración propia enruta por director al chat local de Hermes o Claw. Esto no demuestra Telegram ni amplía capacidades de OpenBot; su ensayo DG previo sigue siendo la evidencia de esa aplicación.

El adaptador propio perdió deliberadamente el recibo de respuesta y luego consultó los registros nativos del chat. Se publicaron recibos basados en IDs de mensajes reales, no en archivos locales inventados. Cada chat conserva una entrada y una respuesta correspondientes a su aviso. Drenar después de publicar conservó 29 intentos de trabajo antes y después; no volvió a implementar el encargo.

Claw requirió un intento de aviso. Hermes registró tres: el proceso soltado en background por SSH Windows murió antes de recibir el mensaje; el segundo cliente rechazó un cambio de límite al reanudar una sesión; el tercero conservó la política original y entregó. En los dos fallos previos se consultó el chat y se confirmó ausencia antes de reintentar sólo el aviso. La ejecución del trabajo no se repitió. Estas correcciones del instrumento quedaron en ops/ensayo_directores_final.py.

## Modelos, cuenta y poteto

Hermes usó xai-oauth y Grok4.7 con el login existente del mismo usuario, copiado sólo al home propio de la PC. Su traza contiene skill_view, read_file y terminal. Claw usó el perfil OAuth Xai existente, copiado sólo al almacén nativo propio de la Mini; su recibo de runtime distingue solicitado grok-4.7 y respuesta efectiva grok-4.7-build. Ese sufijo de backend puede ser un alias normal, no se presenta como avería.

La discrepancia real encontrada fue que el lanzador genérico del motor usaba por defecto grok-4.6-build. Después del pin de modelo y esfuerzo, los eventos end de responsables y revisores registraron grok-4.7-build. La evidencia incluye argumentos, cierre del proceso, modelo efectivo y actividad de herramientas; no se infiere el modelo sólo de su configuración.

Los perfiles recibieron el paquete completo de 79 archivos Markdown de skills y referencias. Los directores leyeron poteto-mode y referencias mediante sus herramientas. Los trabajadores durables también leyeron copias completas en su runtime privado. No se sustituyó el paquete por el resumen del motor. El modelo de ensayo no alteró la política deseada v7 de las instalaciones operativas.

El recorrido encontró y verificó correcciones de permisos headless de Grok, modelo por defecto, observación de tool_call/tool_call_update y acceso a observaciones históricas mediante copias privadas. Las fallas anteriores y sus trazas seleccionadas permanecen explícitas en el JSON; no se fabricaron juicios ni se modificó el registro para terminar.

## Limpieza y conservación

Se detuvo sólo el gateway de ensayo de Hermes, se eliminaron ambos perfiles y sus copias de autenticación y se borraron 58 directorios privados de caché de suscripción de los intentos. No quedaron trabajadores registrados abiertos ni coordinadores del ensayo. Los productos, la autoridad y las observaciones se conservaron.

Los historiales nativos se conservaron para lectura en C:\Users\ehven\ensayo-final34\evidencia\hermes-state.sqlite y /Users/gon/ensayo-final34/evidencia/claw-agent.sqlite. Antes de conservar el segundo se vaciaron los almacenes de autenticación propios. Los servicios y credenciales operativos permanecieron sin cambios.

Ruff del instrumento y git diff --check pasan. No se añadieron pruebas universales ni requisitos nuevos al plan del usuario.
