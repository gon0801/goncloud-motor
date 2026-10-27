import json, socket, subprocess, sys
sys.path.insert(0, "/Users/dn/dev/goncloud-motor-encargos-3-4")
from pathlib import Path
from tempfile import TemporaryDirectory
from motor import plataforma
from motor.encargos.registro import Registro
from motor.integraciones.directores import encargar, cerrar_sesion, resultado_directo, drenar, registrar_identidad, promover
H = '2026-10-26T12:00:00Z'
def identidad(p):
    return {'pid': p.pid, 'inicio': plataforma.hora_inicio(p.pid), 'boot_id': plataforma.boot_id(), 'host': socket.gethostname()}

# R2-1 duplicado
with TemporaryDirectory() as tmp:
    reg = Registro(Path(tmp) / 'worker'); log = Path(tmp) / 'entregas'
    p = subprocess.Popen(['/bin/sleep', '300'], start_new_session=True)
    try:
        e = encargar(reg, 'pedido', 'p', H, 'hermes', 'canal', 'directa', identidad=identidad(p))['encargo']
        cerrar_sesion(reg, e); p.wait(); resultado_directo(reg, e, 'producto', '2026-10-26T12:00:01Z')
        send = "import sys; from pathlib import Path; p=Path(sys.argv[1]); p.write_text((p.read_text() if p.exists() else '')+'entregado\\n'); print('{\"recibo\":\"externo\"}')"
        (reg.base / 'canal.json').write_text(json.dumps({'canal': {'comando': [sys.executable, '-c', send, str(log)], 'conciliacion': [sys.executable, '-c', 'import sys; sys.exit(7)']}}))
        print('R2-1 corte', drenar(reg, cortar='despues')['avisos'][0]['estado'])
        print('R2-1 reintento', drenar(reg)['avisos'][0]['estado'])
        print('R2-1 entregas', len(log.read_text().splitlines()))
    finally:
        if p.poll() is None: p.kill()
        p.wait(); reg.close()

# R2-2 identidad nueva
with TemporaryDirectory() as tmp:
    reg = Registro(Path(tmp) / 'worker'); ps = []
    try:
        p = subprocess.Popen(['/bin/sleep', '300'], start_new_session=True); ps.append(p)
        e = encargar(reg, 'producto', 'p', H, 'hermes', 'canal', 'directa', identidad=identidad(p))['encargo']
        print('R2-2 cierre_original', cerrar_sesion(reg, e)['estado']); p.wait()
        q = subprocess.Popen(['/bin/sleep', '300'], start_new_session=True); ps.append(q)
        nueva = registrar_identidad(reg, e, identidad(q))
        print('R2-2 sesion_nueva', {k: nueva[k] for k in ('pid', 'estado', 'cierre_observado')})
        r = promover(reg, e, 'promover', H)
        print('R2-2 promocion rechazada', r.get('rechazada', False), 'ejecutor_vivo', q.poll() is None)
    finally:
        for p in ps:
            if p.poll() is None: p.kill()
            p.wait()
        reg.close()

# R2-4 y R2-6 paquete
from motor.flota.control import publicar_proyeccion
from motor.flota.manifiesto import cargar, desde_doc
paquete = Path('/Users/dn/dev/goncloud-hermes-encargos-3-4/shared/sistema-agentes/poteto')
p = cargar(Path('/Users/dn/dev/goncloud-motor-conf-encargos-3-4/flota/manifesto.toml'))
with TemporaryDirectory() as t:
    try:
        out = publicar_proyeccion(Path(t), p, paquete)
        print('R2-4 paquete_completo publicado', bool(out), 'digest', out.get('poteto', '')[:12])
    except ValueError as e:
        print('R2-4 ValueError:', e)

with TemporaryDirectory() as t:
    r = Path(t); paq = r / 'paquete'; (paq / 'runtimes').mkdir(parents=True)
    (paq / 'SKILL.md').write_text('Instrucciones')
    runtime = paq / 'runtimes/hermes.json'; runtime.write_text('{}')
    def politica(m):
        return desde_doc({'generacion': m, 'puesto': [{'funcion': 'dirigir', 'modelo': ['proveedor/' + m], 'esfuerzo': ['medium']}]})
    home = r / 'home'; publicar_proyeccion(home, politica('A'), paq)
    antes = (home / 'proyeccion/model-routing.source.json').read_text()
    runtime.write_text('{ invalido')
    try:
        publicar_proyeccion(home, politica('B'), paq)
        print('R2-6 publicada indebidamente')
    except ValueError as exc:
        print('R2-6 rechazada:', exc)
    print('R2-6 conservada_A', antes == (home / 'proyeccion/model-routing.source.json').read_text())
print('done')
