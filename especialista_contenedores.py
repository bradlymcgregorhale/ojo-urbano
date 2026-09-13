"""Inventario municipal con una solicitud visual fija y revision explicita."""
import base64
import copy
import hashlib
import io
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageOps

MODELO = 'google/gemini-3.8-flash'
TIPOS = ('contenedor_secos', 'contenedor_humedos_lateral', 'contenedor_humedos_bilateral')
PLANTILLA = Path(__file__).resolve().parent / 'eval/vision/private/serving-contenedores-051.json'
PLANTILLA_SHA256 = '4d420e509f0ae3569cb83b6b88884722a761004e77eb940f6faeec34db2e8f44'


def _imagen_url(imagen):
    copia = imagen.copy()
    copia.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    copia.save(buffer, format='JPEG', quality=92)
    return {'type': 'image_url', 'image_url': {'url':
        'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()}}


def solicitud(datos, *, vistas=True):
    raw = PLANTILLA.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PLANTILLA_SHA256:
        raise ValueError('Plantilla de contenedores modificada')
    cuerpo = json.loads(raw)
    with Image.open(io.BytesIO(datos)) as fuente:
        imagen = ImageOps.exif_transpose(fuente).convert('RGB')
    contenido = cuerpo['messages'][1]['content']
    contenido[-1] = _imagen_url(imagen)
    if vistas:
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
            contenido.append(_imagen_url(imagen.crop(caja)))
    return cuerpo


def revision(*, fallo=False):
    return dict(estado='revision', tipos=None, fallo=fallo,
                motivo=('No se pudo completar la identificacion de contenedores.' if fallo else
                        'No se distinguen todos los tipos de contenedor con seguridad.'))


def requiere_presencia(inventario):
    return (isinstance(inventario, dict) and inventario.get('estado') == 'revision'
            and inventario.get('fallo') is False and inventario.get('tipos') is None)


def solicitud_presencia(datos, modelo):
    """Pregunta independiente de presencia, sin el inventario ni votos anteriores."""
    prompt = (Path(__file__).parent / 'prompts/inventario/presencia.txt').read_text(encoding='utf-8')
    with Image.open(io.BytesIO(datos)) as fuente:
        imagen = ImageOps.exif_transpose(fuente).convert('RGB')
    return {'model': modelo, 'temperature': 0, 'max_tokens': 700,
            'messages': [{'role': 'system', 'content': prompt},
                         {'role': 'user', 'content': [
                             {'type': 'text', 'text': 'Evaluá la foto.'}, _imagen_url(imagen)]}]}


def interpretar_presencia(data, modelo):
    """Una duda, respuesta truncada o formato inválido nunca acredita ausencia."""
    try:
        choices = data['choices']
        if data.get('model') != modelo or len(choices) != 1 or choices[0]['finish_reason'] != 'stop':
            raise ValueError('Respuesta de presencia incompleta')
        mensaje = choices[0]['message']
        if mensaje.get('refusal'):
            raise ValueError('Respuesta de presencia rechazada')
        contenido = mensaje['content'].strip()
        bloque = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', contenido, re.DOTALL)
        r = json.loads(bloque.group(1) if bloque else contenido, object_pairs_hook=_unico)
        if (not isinstance(r, dict) or set(r) != {'presente', 'evidencia'}
                or (r['presente'] is not None and type(r['presente']) is not bool)
                or not isinstance(r['evidencia'], str) or not r['evidencia'].strip()):
            raise ValueError('Presencia sin decisión o evidencia válida')
        return dict(modelo=modelo, presente=r['presente'], evidencia=r['evidencia'])
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError('Respuesta de presencia inválida') from exc


def resolver_ausencia(inventario, lecturas):
    """Solo resuelve ausencia unánime; nunca propone tipos ni reemplaza un acierto."""
    if (requiere_presencia(inventario) and len(lecturas) == 3
            and all(isinstance(v, dict) and isinstance(v.get('modelo'), str) and v['modelo']
                    and v.get('presente') is False and isinstance(v.get('evidencia'), str)
                    and v['evidencia'].strip() for v in lecturas)
            and len({v['modelo'] for v in lecturas}) == 3):
        return dict(estado='confirmado', tipos=[], fallo=False,
                    motivo='Tres verificadores coinciden en que no se ven contenedores municipales.',
                    revision_presencia={
                        'motivo_previo': str(inventario.get('motivo') or '')[:280],
                        'lecturas': [dict(modelo=v['modelo'], presente=False,
                                         evidencia=v['evidencia'].strip()[:280]) for v in lecturas]})
    return copy.deepcopy(inventario)


def _unico(pares):
    resultado = {}
    for clave, valor in pares:
        if clave in resultado:
            raise ValueError('Clave repetida')
        resultado[clave] = valor
    return resultado


def interpretar(data):
    try:
        if data.get('model') != MODELO:
            return revision(fallo=True)
        choices = data['choices']
        if len(choices) != 1 or choices[0]['finish_reason'] != 'stop':
            return revision(fallo=True)
        message = choices[0]['message']
        if message.get('refusal'):
            return revision(fallo=True)
        answer = json.loads(message['content'], object_pairs_hook=_unico)
        if (type(answer) is not dict or set(answer) != {'types', 'uncertain', 'evidence'}
                or type(answer['types']) is not list
                or any(type(k) is not str or k not in TIPOS for k in answer['types'])
                or len(answer['types']) != len(set(answer['types']))
                or type(answer['uncertain']) is not bool or type(answer['evidence']) is not str):
            return revision(fallo=True)
        if answer['uncertain']:
            return revision()
        return dict(estado='confirmado', tipos=[k for k in TIPOS if k in answer['types']],
                    fallo=False, motivo=None)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return revision(fallo=True)


def aplicar(publica, resultado, categorias):
    """Sustituye solo presencia/tipo; los reclamos mantienen su politica propia."""
    salida = copy.deepcopy(publica)
    confirmado = (isinstance(resultado, dict) and resultado.get('estado') == 'confirmado'
                  and isinstance(resultado.get('tipos'), list)
                  and all(k in TIPOS for k in resultado['tipos']))
    estado = 'confirmado' if confirmado else 'revision'
    tipos = list(dict.fromkeys(resultado['tipos'])) if confirmado else None
    salida['contenedores'] = dict(estado=estado, tipos=tipos,
                                 motivo=resultado.get('motivo') if isinstance(resultado, dict) else revision(fallo=True)['motivo'])
    for campo in ('elementos_detectados', 'posibles'):
        salida[campo] = [c for c in salida.get(campo, []) if c.get('key') not in TIPOS]
    salida['en_duda'] = [k for k in salida.get('en_duda', []) if k not in TIPOS]
    if confirmado:
        salida['elementos_detectados'].extend({'key': k, 'nombre': categorias[k]['nombre']} for k in tipos)
    # Un reclamo de dano/vaciado no se elimina por el inventario. Si contradice
    # una lectura vacia, se publica la discrepancia como revision.
    problemas_contenedor = {'vaciado_contenedor', 'contenedor_desbordado',
                           'reparacion_contenedor', 'reposicion_contenedor', 'lavado_contenedor'}
    if confirmado and not tipos and any(c.get('key') in problemas_contenedor for c in salida.get('problemas', [])):
        salida['contenedores'] = dict(estado='revision', tipos=None,
            motivo='La presencia del contenedor requiere revision por resultados contradictorios.')
    if salida['contenedores']['estado'] == 'revision':
        observaciones = {}
        for v in publica.get('modelos') or []:
            if v.get('ok') is not True or not v.get('modelo'):
                continue
            for c in v.get('categorias') or []:
                if c.get('key') in TIPOS and not c.get('anulada_por'):
                    observaciones.setdefault(c['key'], set()).add(v['modelo'])
        salida['contenedores']['observaciones_tipos'] = [
            dict(key=k, fuentes=len(observaciones[k]), estado='pendiente_de_inventario')
            for k in TIPOS if k in observaciones]
    if isinstance(resultado, dict) and isinstance(resultado.get('revision_presencia'), dict):
        salida['contenedores']['revision_presencia'] = copy.deepcopy(resultado['revision_presencia'])
    # La prosa del consenso anterior no puede contradecir el inventario. Una
    # frase mixta se reconstruye desde los reclamos que siguen confirmados.
    descripcion = salida.get('descripcion')
    if descripcion:
        frases = re.split(r'(?<=[.!?])\s+', descripcion)
        conservar = [f for f in frases if not re.search(r'contenedor|carga\s+(?:bi)?lateral', f, re.I)]
        if len(conservar) != len(frases):
            nombres = [c.get('nombre') or categorias.get(c.get('key'), {}).get('nombre', c.get('key'))
                       for c in salida.get('problemas', [])]
            if nombres:
                prefijo = 'Reclamo por texto: ' if salida.get('foto_valida') is False else 'Incidencias confirmadas: '
                conservar.append(prefijo + '; '.join(nombres) + '.')
            inventario = salida['contenedores']
            if inventario['estado'] == 'revision':
                conservar.append(inventario['motivo'] or 'El inventario de contenedores requiere revision.')
            elif tipos:
                conservar.append('Tipos de contenedor detectados: ' + '; '.join(categorias[k]['nombre'] for k in tipos) + '.')
            else:
                conservar.append('No se detectaron contenedores municipales.')
            salida['descripcion'] = ' '.join(conservar)
    return salida
