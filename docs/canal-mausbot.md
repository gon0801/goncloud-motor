# Canal MausBot

El canal MausBot existe para que el motor entregue los avisos de un encargo por
OpenMausBot, igual que ya hace por Telegram y Hermes. El adaptador vive en
`motor/integraciones/transporte_mausbot.py` y se ejecuta como script suelto en la
PC, con la Python del runtime; no importa el paquete `motor`.

Este documento es sólo la guía de instalación. El encargo no instala nada en la
PC: deja el instalador y el adaptador listos para correr allá.

## Instalar

En la PC, con la Python del runtime:

```bat
C:\Users\ehven\motor-runtime\cpython-3.11.16-windows-x86_64-none\python.exe ^
  C:\Users\ehven\motor-sucesor\ops\instala-canal-mausbot.py ^
  --base C:\Users\ehven\.motor-encargos ^
  --python C:\Users\ehven\motor-runtime\cpython-3.11.16-windows-x86_64-none\python.exe ^
  --transporte C:\Users\ehven\motor-sucesor\motor\integraciones\transporte_mausbot.py
```

El instalador lee `<base>\canal.json` y agrega **sólo** la clave `mausbot`:

```json
{
  "comando": ["...\\python.exe", "...\\transporte_mausbot.py", "send"],
  "conciliacion": ["...\\python.exe", "...\\transporte_mausbot.py", "check"],
  "idempotente": true
}
```

`telegram`, `hermes` y cualquier otra clave quedan intactas. Correrlo otra vez
produce el mismo archivo.

## Sondar capacidades

```bat
C:\Users\ehven\motor-runtime\cpython-3.11.16-windows-x86_64-none\python.exe ^
  -B -m motor director capacidades-mausbot ^
  --base C:\Users\ehven\.motor-encargos
```

Sin `--cli`, la sonda usa el ejecutable y el JS por defecto de OpenMausBot. El
resultado se guarda en `politica\mausbot.json` y dice si el canal ofrece
entrega, consulta, parada y recepción. Mientras `parada` no sea `true`, el motor
rechaza `parar` igual que con OpenBot.

## Variables del transporte

- `MOTOR_MAUSBOT_EXE`: ruta al ejecutable de OpenMausBot.
- `MOTOR_MAUSBOT_JS`: ruta al `openmausbot.js`; si se define vacía, se omite.
- `MOTOR_MAUSBOT_TIMEOUT`: segundos de espera, 30 por defecto.
- `MOTOR_AVISO_BASE`, `MOTOR_AVISO_ENCARGO`, `MOTOR_AVISO_ID` y
  `MOTOR_AVISO_FORMATO`: las pone el motor para identificar el aviso y el canal.
