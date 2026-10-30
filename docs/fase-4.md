# Fase 4: entrega local para revisión

No hay push, merge, despliegue ni fase 5. Las instalaciones de `/Users/dn/motor-config` no se editaron. El revisor central no cambió.

## Ramas

| Repo | Worktree | Rama | Base | SHA |
| --- | --- | --- | --- | --- |
| goncloud-motor | `/Users/dn/dev/goncloud-motor-fase4` | `feat/fases-encargo-fase4` | `3014039d4aa38118982bebf98a9c35f6ff34a1ab` | código `eeb5e0eefde41f32942aa788369b6bfd22761d3f`; este documento va en el commit siguiente |
| goncloud-hermes | `/Users/dn/dev/goncloud-hermes-fase4` | `feat/fase4-directores` | `9cecce5fa0f5627ec87df3ef758c109273758ef7` | `67e2372ca8331a1538ed01f676e9932f09852380` |
| goncloud-openclaw | `/Users/dn/dev/goncloud-openclaw-fase4` | `feat/fase4-directores` | `1daa415466cf161905e348085458aaf4547dddf0` | `3648376a44813077decd138e2179015254e1fb66` |
| goncloud-motor-conf | `/Users/dn/dev/goncloud-motor-conf-fase4` | `feat/fase4-directores` | `5f535031d28458fd83bb421cf013a32986f7df63` | `d74363c9b84bec1c7f2e56f77849b97ef9d6b7e6` |

El checkout de fase 3 sigue en `3014039`. El revisor sigue en `99596a31f2a4caf5cc7bf771d3df8373f17e514f`.

## Qué cambia para David

Puede entregar un objetivo o un plan por `motor director encargar`, consultar fase, necesidad y decisiones con `ver`, y responder una pregunta del mismo encargo con `motor director responder`. Cerrar la conversación no borra el registro. Un barrido sin novedad no llama al transporte ni a un modelo.

Un pedido con repositorio y sin nivel explícito queda en `pr`, no en el cierre local. Un `--entrega terminado` expreso sigue siendo local. Merge y despliegue no se infieren.

## Archivos

Motor: `motor/integraciones/directores.py`, `motor/integraciones/cli.py`, `motor/encargos/cli.py`, `motor/encargos/ciclo.py`, `motor/encargos/dominio.py`, `motor/encargos/necesidades.py`, `paquetes/poteto/SKILL.md`, `paquetes/poteto/runtimes/openbot.json`, `ops/ensayo_fase4.py`.

Hermes: `skills/motor/SKILL.md`, `shared/sistema-agentes/poteto/adaptacion-hermes.md`.

Claw: las tres copias de `agents/*/agent/workshop-skills/motor/SKILL.md` y `scripts/agent-work/contexto/adaptacion-openclaw.md`.

Conf: `pstack-motor/motor.md`. No se tocó la tabla de modelos.

## Consumidores y fase 5

| Cambio | Quién lo recibe | Pendiente de activar |
| --- | --- | --- |
| Motor | El supervisor y `motor director` cuando se instale este SHA | Instalación en PC, Mac y Mini |
| Hermes | El director Hermes, al instalar `67e2372` en el perfil | El perfil vivo sigue en `/Users/dn/motor-config/hermes` |
| Claw | `main`, `implementer` e `ingenieria` | El perfil vivo sigue en `/Users/dn/motor-config/claw` |
| Conf | Intentos que copian `pstack-motor/` | La conf viva sigue en `/Users/dn/motor-config/conf` |
| OpenBot | No tiene repo de guía propio. La limitación está en las skills y en `paquetes/poteto/runtimes/openbot.json` | Sigue sin recepción bidireccional |

La receta de la conf es el actualizador de flota (`motor/flota/actualizaciones.py`) y `ops/lib.sh`, que clonan `goncloud-motor-conf`. Hermes y Claw se activan al mover el checkout del perfil. Eso es fase 5.

## Comprobación

Base aislada, no `~/.motor` ni Orbit. Transporte de ensayo que cuenta llamadas y devuelve recibo. No se envió nada a una conversación real.

```sh
python3 -B ops/ensayo_fase4.py /private/var/folders/hp/sc_3c6qs7nvdswl_x3w5sdbr0000gn/T/opencode/fase4-ensayo-sha
```

Salió 0 sobre `eeb5e0e`. Se observó:

- Objetivo sin repo: entrega `terminado`. Plan con repo y sin `--entrega`: entrega `pr`, texto íntegro, restricción `no despliegues`, base `master`, modelos Muse Wonder y Kimi 3.1.
- Pedido con repo y `--entrega terminado`: sigue local.
- `ver` mostró fase `datos`, resultado, decisión y bitácora. Revisión y PR vacíos: este ensayo no publica.
- Primer `avisar`: 3 llamadas al transporte (fase, pregunta `preg1`, decisión), las tres `entregado` verdadero.
- Segundo `avisar` y el de después de cerrar la sesión: 0 llamadas nuevas. `ver` conservó la fase.
- Canal ajeno: rc 2, no aplicada.
- Respuesta `sí` con la sesión del canal: aplicada, continuación `continuadas: 1`, la pregunta quedó `resuelta` en la fase `datos`.
- La misma clave: `retransmision` verdadera, sin otra continuación.
- Respuesta tardía a `preg1` ya resuelta, otra clave: `tardia` verdadera, `aplicada` falsa, el texto guardado sigue siendo `sí`.
- Repetir el encargo con la misma clave no creó otro.

OpenBot real, solo `--help` y `cancel`, sin `run`:

```json
{"entrega": true, "consulta": true, "parada": false, "timeout_cancela": false, "skills": false, "publicacion_de_sesion": false, "recepcion": false}
```

Hermes y Claw no se hablaron por sus chats. Su transporte en el ensayo fue el script local. Un archivo en `canal/` sin recibo externo queda `entregado` falso.

## Límites

No hay aceptación nativa en PC, Mac y Mini ni revisión real de DeepSeek. No se reanudaron encargos detenidos. Los subagentes de Z.AI no arrancaron: la cuota del plan está agotada hasta 2026-11-02. La exploración y el código los hice en este turno.

## Corrección de la ronda 1

Una respuesta de otro intento queda en la historia y no se aplica. La continuación usa el puente existente: si los datos no llegan al espacio, la solicitud no queda resuelta y el resultado del host se reutiliza al publicar. Hermes y Claw firman la adaptación nueva; Claw sube a la generación 3.

```sh
python3 -B sonda-respuestas.py intento_tardio
python3 -B sonda-respuestas.py publicacion
python3 -B sonda-paquetes.py hermes
python3 -B sonda-paquetes.py claw
```

Las cuatro salieron 0 contra estos worktrees. El ensayo de fase 4 también salió 0.
