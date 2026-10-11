"""Comprueba la geometría del cordón antes de publicar una reparación."""
import copy
import hashlib
import json
from pathlib import Path

from prompts import cargar_prompt

KEY = 'reparacion_cordon'
PROMPT = cargar_prompt('dirigidos/cordon_geometria')
REFERENCIAS = Path(__file__).parent / 'eval/vision/private/serving-cordon-103-v2.json'
REFERENCIAS_SHA256 = '0e39b40952efb6b90d28cc4891e10fe87b9c57b83b5a92542001ca7af4f20f8c'
MODELOS = ('google/gemini-3.8-flash', 'google/gemini-3.5-flash', 'openai/gpt-5.6-luna')
ESTADOS = {'rotura_estructural', 'diseno_funcional', 'desgaste_superficial', 'sano', 'indeterminado'}
PIEZAS = {'cordon', 'cuneta_calzada', 'vereda', 'no_identificable'}


def interpretar(valor):
    if (not isinstance(valor, dict) or not isinstance(valor.get('estado'), str)
            or valor['estado'] not in ESTADOS or not isinstance(valor.get('pieza'), str)
            or valor['pieza'] not in PIEZAS or not isinstance(valor.get('evidencia'), str)
            or not valor['evidencia'].strip()):
        raise ValueError('Geometría del cordón sin decisión o evidencia válida')
    return {k: valor[k].strip()[:500] for k in ('estado', 'pieza', 'evidencia')}


def consultar(img):
    import verificador as V
    try:
        raw = REFERENCIAS.read_bytes()
        if hashlib.sha256(raw).hexdigest() != REFERENCIAS_SHA256:
            raise ValueError('Referencias del cordón modificadas')
        contenido = json.loads(raw)
    except (OSError, ValueError):
        return {'lecturas': [], 'fallo': True}
    contenido.extend([{'type': 'text', 'text': 'Evaluá únicamente esta foto nueva, sin trasladar las etiquetas de los ejemplos:'},
        {'type': 'image_url', 'image_url': {'url': V._imagen_data_url(img, lado=1600)}}])
    ancho, alto = img.size
    for nombre, caja in (
        ('banda inferior', (0, int(alto * .45), ancho, alto)),
        ('banda inferior izquierda', (0, int(alto * .45), max(1, int(ancho * .65)), alto)),
        ('banda inferior derecha', (int(ancho * .35), int(alto * .45), ancho, alto)),
    ):
        contenido.extend([{'type': 'text', 'text': 'Detalle ' + nombre + ' de la MISMA foto nueva:'},
            {'type': 'image_url', 'image_url': {'url': V._imagen_data_url(img.crop(caja), lado=1600)}}])
    def uno(modelo):
        return interpretar(V._extraer_json(V._llamar(modelo,
            [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': contenido}],
            max_tokens=1000, etapa='cordon_geometria')))
    resultados = V._map_modelos(MODELOS, uno)
    lecturas = [dict(modelo=m, **v) for m, v in zip(MODELOS, resultados) if v is not V._FALLO_MODELO]
    return {'lecturas': lecturas, 'fallo': len(lecturas) != 3}


def decidir(revision):
    lecturas = revision.get('lecturas') or []
    if (revision.get('fallo') or len(lecturas) != 3
            or {v.get('modelo') for v in lecturas} != set(MODELOS)):
        return 'revision'
    try:
        lecturas = [interpretar(v) for v in lecturas]
    except ValueError:
        return 'revision'
    if all(v['estado'] == 'rotura_estructural' and v['pieza'] == 'cordon' for v in lecturas):
        return 'confirmado'
    if all(v['estado'] in {'diseno_funcional', 'desgaste_superficial', 'sano'}
           and v['pieza'] == 'cordon' for v in lecturas):
        return 'sin_rotura'
    return 'revision'


def aplicar(salida, revision):
    r = copy.deepcopy(salida)
    estado = decidir(revision)
    veri = r.setdefault('detalle', {}).setdefault('verificacion', {})
    veri['revision_cordon'] = dict(copy.deepcopy(revision), estado=estado)
    if estado == 'confirmado':
        return r
    anteriores = [c for c in r.get('problemas') or [] if c.get('key') == KEY]
    if not anteriores:
        return r
    motivo = ('El cordón conserva su estructura; no se confirmó una rotura que requiera reparación.'
              if estado == 'sin_rotura' else
              'Las lecturas del cordón no coinciden en una rotura estructural. Requiere revisión.')
    veri['revision_cordon']['confirmadas_previas'] = copy.deepcopy(anteriores)
    r['problemas'] = [c for c in r['problemas'] if c.get('key') != KEY]
    r['posibles'] = [c for c in r.get('posibles') or [] if c.get('key') != KEY]
    r['en_duda'] = [k for k in r.get('en_duda') or [] if k != KEY]
    entrada = dict(anteriores[0], gravedad=None, arbitro='rechazar' if estado == 'sin_rotura' else None,
                   motivo=motivo, revision_geometria=estado)
    r['posibles'].append(entrada)
    if estado == 'revision':
        r['en_duda'].append(KEY)
    r['hay_problema'] = bool(r['problemas'])
    r['hay_reclamo'] = bool(r['problemas'] or r.get('categorias_contexto'))
    r['gravedad_maxima'] = max((c.get('gravedad') or 0 for c in r['problemas']), default=0) or None
    otros = '; '.join(c.get('nombre') or c['key'] for c in r['problemas'])
    r['descripcion'] = motivo + (' Otros hallazgos: ' + otros + '.' if otros else '')
    return r


def revisar(img, salida):
    import modos_analisis as modos
    perfil = modos.perfil_actual()
    if (not perfil or perfil.modo != 'alto' or salida.get('foto_valida') is False
            or not any(c.get('key') == KEY and c.get('origen') != 'contexto_vecinal'
                       for c in salida.get('problemas') or [])):
        return salida
    revision = consultar(img)
    if revision.get('fallo'):
        modos.fallo('cordon_geometria')
    return aplicar(salida, revision)
