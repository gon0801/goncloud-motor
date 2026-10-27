---
name: motor
description: Cómo reparte el trabajo el director. Una tarea acotada la hacen sus agentes. El motor continúa el trabajo sin la conversación, coordina máquinas o sostiene una entrega de varias etapas. La vía que David nombra se respeta.
---

# Repartir el pedido

David manda texto o archivos. Eso es el pedido. No lo conviertas en bloques, campos Acepta ni comandos que haya que aprobar antes de empezar.

Si el pedido cabe en tus agentes y David no pidió continuidad, hazlo por la vía directa. Si debe seguir sin esta conversación, cruzar máquinas o entregar en etapas, usa la vía durable. Si David nombró la vía, usa esa.

```sh
python3 -B -m motor director encargar \
  --via directa \
  --director hermes \
  --canal origen \
  --clave mensaje-1 \
  --base /tmp/ensayo \
  --archivo pedido.txt
```

`hermes`, `claw` y `openbot` son los directores. Al pasar de directa a durable, cierra la sesión anterior y conserva el mismo encargo.

```sh
python3 -B -m motor director cerrar-sesion ENCARGO --base /tmp/ensayo
python3 -B -m motor director promover ENCARGO --base /tmp/ensayo --clave promover-1
```

Consultar, parar, continuar y cancelar retransmiten la misma clave.

```sh
python3 -B -m motor director ver ENCARGO --base /tmp/ensayo
python3 -B -m motor director parar ENCARGO --base /tmp/ensayo --clave orden-1
python3 -B -m motor director continuar ENCARGO --base /tmp/ensayo --clave orden-2
```

OpenBot no ofrece parada hasta que su comando la demuestre. Un timeout no cancela el trabajo.

El resultado vuelve al canal de origen aunque este turno cierre. Si el aviso falla, se recupera el aviso. No se repite el encargo.

```sh
python3 -B -m motor director avisar --base /tmp/ensayo
```

La política de modelos y esfuerzos sale de la fuente común. No la edites a mano. Un respaldo por cuota no cambia la cadena deseada.
