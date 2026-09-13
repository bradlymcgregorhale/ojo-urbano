"""Comparación privada de preparación visual, sin modificar la API (#44)."""
import argparse
import base64
import copy
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
import especialista_contenedores as especialista

from PIL import Image, ImageOps


def sha(datos):
    return hashlib.sha256(datos).hexdigest()


def imagen_url(imagen, lado):
    copia = imagen.copy()
    copia.thumbnail((lado, lado), Image.Resampling.LANCZOS)
    salida = io.BytesIO()
    copia.save(salida, format='JPEG', quality=92)
    return {'type': 'image_url', 'image_url': {
        'url': 'data:image/jpeg;base64,' + base64.b64encode(salida.getvalue()).decode()}}


def preparar(datos, variante):
    cuerpo = especialista.solicitud(datos, vistas=False)
    if variante == 'actual':
        return cuerpo
    with Image.open(io.BytesIO(datos)) as fuente:
        imagen = ImageOps.exif_transpose(fuente).convert('RGB')
    contenido = cuerpo['messages'][1]['content']
    if variante == '3200':
        contenido[-1] = imagen_url(imagen, 3200)
    elif variante == 'vistas':
        # Recortes mecánicos de la misma escena, sin selección por tipo esperado.
        contenido[-2] = {'type': 'text', 'text': (
            'FOTO A EVALUAR: la vista completa y los cuatro recortes que siguen '
            'pertenecen a UNA MISMA FOTO. Inventariá sus tipos una sola vez. '
            'Los ejemplos anteriores siguen siendo otras escenas.')}
        ancho, alto = imagen.size
        for nombre, caja in (
            ('superior izquierda', (0, 0, math.ceil(ancho*.6), math.ceil(alto*.6))),
            ('superior derecha', (int(ancho*.4), 0, ancho, math.ceil(alto*.6))),
            ('inferior izquierda', (0, int(alto*.4), math.ceil(ancho*.6), alto)),
            ('inferior derecha', (int(ancho*.4), int(alto*.4), ancho, alto)),
        ):
            contenido.append({'type': 'text', 'text': 'Recorte de la misma foto: ' + nombre})
            contenido.append(imagen_url(imagen.crop(caja), 1600))
    else:
        raise ValueError('Variante desconocida')
    return cuerpo


def guardar(ruta, valor):
    temporal = ruta.with_suffix(ruta.suffix + '.tmp')
    temporal.write_text(json.dumps(valor, ensure_ascii=False, indent=2))
    temporal.replace(ruta)


def guardar_bytes(ruta, valor):
    temporal = ruta.with_suffix(ruta.suffix + '.tmp')
    temporal.write_bytes(valor)
    temporal.replace(ruta)


def recibir(solicitud, carpeta, evidencia):
    """Conserva el cuerpo recibido antes de interpretar su contenido (#84)."""
    def conservar(raw):
        evidencia['respuesta_sha256'] = sha(raw)
        evidencia['respuesta_bytes'] = len(raw)
        try:
            guardar_bytes(carpeta/'respuesta.bin', raw)
        except OSError as exc:
            evidencia.update(etapa='error_escritura', respuesta_conservada=False,
                             error_escritura=type(exc).__name__)
            guardar(carpeta/'registro.json', evidencia)
            raise
        evidencia['respuesta_conservada'] = True

    evidencia['etapa'] = 'solicitud_preparada'
    guardar(carpeta/'registro.json', evidencia)
    try:
        with urllib.request.urlopen(solicitud, timeout=60) as respuesta:
            evidencia['estado_http'] = getattr(respuesta, 'status', None)
            raw = respuesta.read()
    except urllib.error.HTTPError as exc:
        evidencia.update(etapa='error_http', estado_http=exc.code, error=type(exc).__name__)
        try:
            raw = exc.read()
        except Exception as lectura:
            evidencia['error_lectura'] = type(lectura).__name__
            parcial = getattr(lectura, 'partial', None)
            if isinstance(parcial, bytes):
                evidencia['respuesta_parcial'] = True
                conservar(parcial)
        else:
            conservar(raw)
        finally:
            try:
                exc.close()
            except Exception as cierre:
                evidencia['error_cierre'] = type(cierre).__name__
        guardar(carpeta/'registro.json', evidencia)
        raise
    except (Exception, KeyboardInterrupt) as exc:
        etapa = 'interrumpida' if isinstance(exc, KeyboardInterrupt) else 'error_transporte'
        evidencia.update(etapa=etapa, error=type(exc).__name__)
        parcial = getattr(exc, 'partial', None)
        if isinstance(parcial, bytes):
            evidencia['respuesta_parcial'] = True
            conservar(parcial)
        guardar(carpeta/'registro.json', evidencia)
        raise
    conservar(raw)
    evidencia['etapa'] = 'recibida'
    guardar(carpeta/'registro.json', evidencia)
    return raw


def ejecutar(base, enviar=False):
    plan_raw = (base/'plan.json').read_bytes()
    plan = json.loads(plan_raw)
    tareas_raw = (base/'tareas.json').read_bytes()
    if sha(tareas_raw) != plan['tareas_sha256']:
        raise ValueError('Cambió el plan de pruebas')
    tareas = json.loads(tareas_raw)
    estado_path = (base/plan.get('ledger', 'estado.json')).resolve()
    ids = [tarea.get('id') for tarea in tareas]
    if (any(not isinstance(ident, str) or not 1 <= len(ident) <= 120
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', ident)
            or ident.casefold() in {'plan', 'tareas', 'estado'}
            or str((base/(ident + '.json')).resolve()).casefold() == str(estado_path).casefold() for ident in ids)
            or len({ident.casefold() for ident in ids}) != len(ids)):
        raise ValueError('Los IDs de tareas deben ser únicos y seguros para guardar evidencia')
    preparadas = []
    for tarea in tareas:
        datos = Path(tarea['archivo']).read_bytes()
        if sha(datos) != tarea['sha256']:
            raise ValueError('Cambió una foto')
        cuerpo = preparar(datos, tarea['variante'])
        preparadas.append((tarea, cuerpo))
    if not enviar:
        return {'solicitudes_preparadas': len(preparadas), 'llamadas_pagas': 0}
    if plan.get('autorizado') is not True or not 0 < plan['tope_usd'] <= 1:
        raise ValueError('Falta autorización específica de hasta USD1 para #44')
    clave = os.environ.get('OPENROUTER_API_KEY')
    if not clave:
        raise ValueError('Falta OPENROUTER_API_KEY en el entorno del proceso')
    # Piloto y regresión pueden compartir un solo gasto acumulado.
    # El bloqueo no se limpia automáticamente tras una interrupción abrupta.
    bloqueo = estado_path.with_suffix('.lock')
    with bloqueo.open('x') as f:
        json.dump({'pid': os.getpid()}, f)
    try:
        estado = json.loads(estado_path.read_text()) if estado_path.exists() else {
            'gasto_usd': 0, 'reserva_incierta_usd': 0, 'activo': None, 'resultados': []}
        if estado['activo'] or estado['reserva_incierta_usd']:
            raise ValueError('Hay un envío pendiente de conciliación; no se repite')
        for tarea, cuerpo in preparadas:
            ident = tarea['id']
            if any(r['id'] == ident for r in estado['resultados']):
                continue
            destino = base/(ident + '.json')
            if destino.exists():
                raise ValueError('Respuesta sin asiento; conciliar antes de seguir')
            carpeta = base/'evidencia'/ident
            if carpeta.exists():
                raise ValueError('Evidencia anterior sin asiento; conciliar antes de seguir')
            if estado['gasto_usd'] + .05 > plan['tope_usd']:
                estado['pausa'] = 'presupuesto'
                guardar(estado_path, estado)
                break
            if (base/'plan.json').read_bytes() != plan_raw:
                raise ValueError('Cambió el presupuesto o plan durante la ejecución')
            datos_solicitud = json.dumps(cuerpo).encode()
            evidencia = {'version': 1, 'tarea_id': ident, 'foto_sha256': tarea['sha256'],
                         'plan_sha256': sha(plan_raw), 'tareas_sha256': sha(tareas_raw),
                         'solicitud_sha256': sha(datos_solicitud)}
            referencia = str(carpeta.relative_to(base))
            estado['activo'] = {'id': ident, 'inicio': time.time(), 'reserva_usd': .05,
                                'evidencia': referencia, **evidencia}
            guardar(estado_path, estado)
            carpeta.mkdir(parents=True)
            solicitud = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',
                data=datos_solicitud, headers={
                    'Authorization': 'Bearer ' + clave, 'Content-Type': 'application/json'})
            # Una sola llamada; no hay reintentos automáticos, ni siquiera tras timeout.
            raw = recibir(solicitud, carpeta, evidencia)
            try:
                resultado = json.loads(raw)
                if not isinstance(resultado, dict):
                    raise ValueError('La respuesta debe ser un objeto JSON')
            except Exception as exc:
                evidencia.update(etapa='error_json', error=type(exc).__name__)
                guardar(carpeta/'registro.json', evidencia)
                raise
            registro = {'tarea': tarea, 'respuesta': resultado, 'fecha': time.time(),
                        'interpretado': {'estado': 'pendiente'}, 'evidencia': referencia}
            guardar(destino, registro)
            error_interpretacion = None
            try:
                registro['interpretado'] = especialista.interpretar(copy.deepcopy(resultado))
                json.dumps(registro['interpretado'])
            except Exception as exc:
                error_interpretacion = exc
                registro['interpretado'] = {'estado': 'error_interpretacion', 'error': type(exc).__name__}
                evidencia.update(etapa='error_interpretacion', error=type(exc).__name__)
            else:
                evidencia['etapa'] = 'interpretada'
            guardar(destino, registro)
            guardar(carpeta/'registro.json', evidencia)
            uso = resultado.get('usage')
            uso = uso if isinstance(uso, dict) else {}
            costo = uso.get('cost')
            conocido = type(costo) in (int, float) and math.isfinite(costo) and costo >= 0
            if conocido:
                estado['gasto_usd'] += costo + .000001
            if (not conocido or type(uso.get('total_tokens')) is not int
                    or uso['total_tokens'] < 0):
                estado['reserva_incierta_usd'] += .05
            estado['resultados'].append({'id': ident, 'costo': costo if conocido else None,
                                        'evidencia': referencia, 'solicitud_sha256': evidencia['solicitud_sha256'],
                                        'plan_sha256': evidencia['plan_sha256']})
            estado['activo'] = None
            guardar(estado_path, estado)
            print(ident, 'guardado', round(estado['gasto_usd'], 6), flush=True)
            if error_interpretacion is not None:
                raise error_interpretacion
            if estado['reserva_incierta_usd']:
                raise ValueError('Consumo incompleto; conciliar antes de continuar')
            time.sleep(1)
        return estado
    finally:
        bloqueo.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('carpeta', type=Path)
    parser.add_argument('--enviar', action='store_true')
    args = parser.parse_args()
    print(json.dumps(ejecutar(args.carpeta, args.enviar), ensure_ascii=False))
