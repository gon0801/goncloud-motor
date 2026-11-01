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
- **El error se limpia antes de mostrarse.** Se quitan direcciones y tokens, se deja en una línea y se acota a 200
  caracteres, porque un error de transporte puede traer la dirección del bot.
- **Límite.** El estado se actualiza cuando hay un aviso que enviar. Si un canal falla y no quedan avisos pendientes,
  sigue figurando caído hasta el siguiente envío; `ultimo_intento` dice cuándo fue la última prueba.
