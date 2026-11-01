# Un canal de avisos caído se ve

Decisiones de la fase `canal-visible` (`motor/integraciones/canales.py`).

- **Qué cuenta como canal caído.** El transporte falla (no corre, no responde o sale con error). Una salida ilegible o
  sin recibo es un fallo de ese mensaje y no marca el canal. Un canal cuenta como **caído** cuando lleva al menos
  `UMBRAL_CAIDO_S` (10 minutos) fallando desde el primer fallo.
- **Dónde se guarda.** `base/canales/<canal>.json`, escrito al final de cada pasada de `drenar`:
  `fallando_desde`, `ultimo_intento`, `fallos` y `ultimo_error`. Cuando el canal vuelve a responder, el archivo queda con
  `ultimo_exito` y sin `fallando_desde`.
- **Dónde se ve.** `motor director ver <encargo>` agrega `canales_caidos` (solo si hay alguno). La señal a Kuma sale con
  `status=down` y un mensaje `N planes; canal <canal> caído desde <hora>: <último error>`; sin canales caídos sale
  `status=up` como siempre. Kuma avisa por sus propias notificaciones, no por el canal que está caído.
- **No depende del canal caído.** Los dos leen el archivo local y no usan el transporte.
- **El error se limpia antes de guardarse y de mostrarse.** Se quitan direcciones (`http://…`), valores de
  `Bearer`/`Basic`, de `token`, `api_key`, `password`, `clave`, `authorization` o `cookie` tras `=` o `:`, tokens con
  forma `id:secreto` y cualquier cadena opaca de 32 caracteres o más. Queda en una línea y acotado a 200 caracteres,
  porque un error de transporte puede traer la dirección o la clave del bot.
- **Kuma necesita su dirección.** La señal solo sale si el `secretos.env` de la base del supervisor tiene
  `MOTOR_KUMA_URL` (permisos 600). Sin ella, un canal caído se escribe en el log del proceso (stderr), una vez por hora,
  y `motor director ver` sigue mostrándolo.
- **Un estado dañado no rompe nada.** Un archivo de estado ilegible se ignora y un `fallos` que no es número cuenta como 0.
- **Límite.** El estado se actualiza cuando hay un aviso que enviar. Si un canal falla y no quedan avisos pendientes,
  sigue figurando caído hasta el siguiente envío; `ultimo_intento` dice cuándo fue la última prueba.
