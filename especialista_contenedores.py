"""Inventario municipal con una solicitud visual fija y revision explicita."""
import base64
import copy
import hashlib
import io
import json
import re
from pathlib import Path

from PIL import Image, ImageOps

MODELO = 'google/gemini-3.8-flash'
TIPOS = ('contenedor_secos', 'contenedor_humedos_lateral', 'contenedor_humedos_bilateral')
PLANTILLA = Path(__file__).resolve().parent / 'eval/vision/private/serving-contenedores-051.json'
PLANTILLA_SHA256 = '4d420e509f0ae3569cb83b6b88884722a761004e77eb940f6faeec34db2e8f44'


def solicitud(datos):
    raw = PLANTILLA.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PLANTILLA_SHA256:
        raise ValueError('Plantilla de contenedores modificada')
    cuerpo = json.loads(raw)
    with Image.open(io.BytesIO(datos)) as imagen:
        imagen = ImageOps.exif_transpose(imagen).convert('RGB')
        imagen.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        imagen.save(buffer, format='JPEG', quality=92)
    cuerpo['messages'][1]['content'][-1]['image_url']['url'] = (
        'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode())
    return cuerpo


def revision(*, fallo=False):
    return dict(estado='revision', tipos=None, fallo=fallo,
                motivo=('No se pudo completar la identificacion de contenedores.' if fallo else
                        'No se distinguen todos los tipos de contenedor con seguridad.'))


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
