# Avisos: un canal caído no frena al motor

Decisiones sobre `drenar` y `_publicar_aviso` (`motor/integraciones/directores.py`).

## Qué va al canal

Solo avisos para una persona no técnica:

- el encargo empezó (una vez);
- necesita tu decisión (la pregunta, en palabras simples);
- el PR está listo para que decidas el merge (una vez por número de PR);
- se trabó y qué hace falta (una vez por bloqueo: clase y vez que el encargo se trabó con ella);
- terminó (el aviso de cierre).

Decisiones del agente, esperas de reintento, revisiones, comprobaciones, intentos y fases intermedias quedan en
el registro. Los archivos, comandos, ids y hashes de un texto de agente se cambian por su nombre común ("un archivo")
y el aviso remite al estado del encargo para el detalle.

## Reintentos

- Un aviso `incierto` guarda `fallos` y `proximo_intento`. La espera es 60 s por 2^(fallos-1), con tope de 3600 s.
- Un aviso viejo sin `fallos` hereda sus `intentos`, así que los acumulados pasan al tope de una hora en su primer fallo.
- Si el transporte falla (no corre, no responde o sale con error), el canal queda cortado en esa pasada: no se intenta
  ningún otro aviso suyo. Una salida ilegible o sin recibo es un fallo de ese mensaje y no corta el canal.
- Antes de reenviar un aviso `incierto` se concilia (`conciliacion` del canal), aunque el canal diga `idempotente`.
  Solo si la conciliación dice `ausente` se reenvía. Sin `conciliacion`, un canal idempotente reintenta con el mismo id.
- De un encargo terminado solo queda el aviso de cierre. Los avisos de avance sin entregar se **descartan** (estado
  `descartado`, con su motivo); no se resumen, porque el cierre ya lleva el resultado y al volver el canal no llegan
  decenas de avisos viejos.

## Los avisos no frenan el barrido

Se eligieron las dos cosas, cada una por una razón distinta:

- **Un hilo aparte (`DrenadoAparte`) en el bucle del supervisor.** Es lo que independiza la cadencia de `barrer`
  del canal, también cuando el canal funciona: la vuelta llama a `lanzar()`, que no espera. El hilo abre su propio
  `Registro` y hay una sola pasada viva a la vez. Un proceso aparte no hacía falta: los avisos solo escriben archivos
  por aviso y el registro sqlite (WAL) admite otra conexión.
- **Un tope de tiempo por pasada (`tope_s`, 20 s).** Pasado el tope, la pasada no empieza avisos nuevos; lo que no
  se intentó se retoma en la pasada siguiente. El tope **no mata un envío ya empezado**: conserva su timeout propio
  (30 s). Matarlo podría cortar un mensaje que ya salió, duplicarlo al reintentar y marcar caído un canal sano. En el
  hilo, una pasada larga no frena a `barrer`. Con `--una-vez` el proceso termina tras la pasada, así que no puede dejar
  el trabajo en un hilo: drena en línea con ese mismo tope, y como mucho espera el envío en curso (hasta 30 s más).

`motor director avisar` sigue sin tope: lo pide una persona.
