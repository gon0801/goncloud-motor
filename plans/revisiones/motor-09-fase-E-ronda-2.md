Estado posterior: [E cerrada tras corrección del lead](motor-09-fase-E-cierre.md).

# MOTOR 09: segunda revisión de E

**E todavía no aprobada. Origen de pareja, comandos de recibos PC y prueba existente corregidos. Queda la reversa de la configuración local efectiva.**

Revisión directa sobre `recuperacion/motor-09`, base `1b37d63`, sin commit. No se modificó implementación ni se operó producción. Las huellas están en `.motor09/revision-E/lead/huellas-ronda-2.json`.

## Comprobaciones acreditadas

- El lead ejecutó la sonda original `python3 -B .motor09/revision-E/lead/pareja.py`. El JSON ahora confirma origen B/F1 tras pasar A/F1 → B/F1 → B/F2, reversa de código a B y conf a F1, TOML igual al del repo anterior y mantenimiento conservado. El script conserva el assert que esperaba las fallas viejas y por eso termina en 1; ese assert no se interpreta como fallo del instalador ni como evidencia de éxito de otra condición.
- `python3 -B -m unittest -v tests.test_motor08_efectos.RecuperaEfectosTest.test_caida_tras_lanzar_recupera_resultado_sin_repetir_instalacion` pasa: 1 test, salida 0. Se conserva la caída del escenario y un solo lanzamiento; el doble aporta la identidad de la API actual.
- `python3 -B .motor09/revision-E/lead/pc-recibos.py` pasa con salida 0. Ejecuta las funciones actuales de `ops/lib.sh` mediante CMD/Python nativos en PC con USERPROFILE y HOME apuntando a una carpeta temporal única. Escribe/lee JSON con +/=, borra y recibe código 2 al leer el archivo ausente. Limpia únicamente ese perfil temporal. No usa el home, código o servicios instalados. Los prefijos inválidos anteriores ya fueron sustituidos.
- `git diff --check` pasa. El staging de validación PC ahora apunta a TEMP. Se reutiliza la ejecución reportada del script de instalación sobre el candidato B–E para los casos que cubre; no se repitió esa batería antes de corregir el caso adicional.

## Bloqueante: la copia privada del TOML local se guarda pero se ignora al revertir

Ubicación: `ops/instala-todo.sh:355–364`, `ops/vuelve-a-la-anterior.sh:165–171` y camino equivalente PC.

El caso anterior quedó corregido cuando el TOML efectivo coincide con el archivo versionado. Pero una máquina puede tener un TOML local distinto del repo: precisamente por eso E debía conservar su copia privada. La instalación guarda esa copia si cambia; la reversa prefiere siempre el archivo del repo y solo usaría la copia si ese archivo no existiera. Además, antes de mutar exige que el blob del repo exista, por lo que esa alternativa no restaura normalmente la configuración local original.

Reproducción del lead con instaladores reales, repos y HOME temporales:

```sh
python3 -B .motor09/revision-E/lead/toml-local.py
```

El caso parte de B/F1 y un `maquina.toml` local con nombre personalizado, instala B/F2 y vuelve a B/F1. Resultado:

```json
{"cambio_solo_conf_origen_deberia_ser_B": true, "reversa_codigo_es_B": true, "reversa_conf_es_F1": true, "copia_privada_guarda_original": true, "maquina_local_restaurada": false, "mantenimiento_conservado": true}
```

La reversa informa éxito pero no devuelve los bytes efectivos que había antes, aunque conserva la copia que permitiría hacerlo. Es el mismo criterio de reversa efectiva de la primera revisión; copiar un archivo del repo no basta para acreditar el estado previo de esa máquina. En PC la instalación tampoco registra esa copia privada previa en su camino actual.

El origen de la operación debe incluir la configuración efectiva anterior, su referencia privada y lo necesario para restaurar su enlace, sin secretos en recibos ni repo. La reversa debe validar ese origen antes de mutar y comprobarlo después. La copia de un intento no debe sustituirse por el TOML de un destino parcial al reintentar. No se pide reintroducir un rollback que rebaje formato 3 ni borrar controles para forzarlo.

## Alcance y siguiente paso

La instalación/reversa Windows completas y el entorno del servicio siguen sin acreditarse. Los recibos nativos no sustituyen esa comprobación. No se habilitaron tareas, no hubo instalación real, PR, commit, push ni despliegue.

La reversa efectiva sigue bloqueante tras dos revisiones; conforme a AGENTS, no se inicia otra ronda automática y la decisión vuelve a David. La recomendación es corregir este caso antes del PR. Queda un [brief acotado](motor-09-brief-E-toml-local.md) para su envío manual.
