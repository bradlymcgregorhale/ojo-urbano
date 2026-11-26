"""Distingue una rama para retirar de otros hallazgos en la misma foto."""
import re

from prompts import cargar_prompt

RETIRO = 'retiro_poda'
ALTERNATIVAS = {'problemas_arbolado': '176008', 'obstruccion': '118020'}
CAMPOS = ('rama_desprendida', *ALTERNATIVAS)
ESTADOS = ('si', 'no', 'indeterminado')
CAIDA = re.compile(r'\b(?:[aá]rbol(?:es)?|ramas?)\s+(?:ca[ií]d[oa]s?|quebrad[oa]s?|desprendid[oa]s?)\b', re.I)
EXTRACCION = re.compile(r'\b(?:extracci[oó]n|extra\w+|tal\w+|sac\w+|quit\w+|retir\w+)\b', re.I)
PROMPT = cargar_prompt('dirigidos/rama_desprendida')
SCHEMA = {
    'type': 'object',
    'properties': {**{k: {'type': 'string', 'enum': list(ESTADOS)} for k in CAMPOS},
                   'evidencia': {'type': 'string'}},
    'required': [*CAMPOS, 'evidencia'],
    'additionalProperties': False,
}


def requiere_revision(salida, contexto):
    if salida.get('foto_valida') is False or not any(
            p['key'] == RETIRO and set(p.get('fuentes') or ()) - {'modelo_local', 'contexto_vecinal'}
            for p in salida.get('problemas', ())):
        return False
    alternativas = set(salida.get('en_duda', ()))
    for campo in ('problemas', 'posibles'):
        alternativas.update(p['key'] for p in salida.get(campo, ()))
    return bool(ALTERNATIVAS.keys() & alternativas or CAIDA.search(contexto or ''))


def lectura_valida(lectura):
    return (isinstance(lectura, dict) and all(lectura.get(k) in ESTADOS for k in CAMPOS)
            and isinstance(lectura.get('evidencia'), str) and bool(lectura['evidencia'].strip()))


def aplicar(salida, lectura, contexto=''):
    if not lectura_valida(lectura) or lectura['rama_desprendida'] != 'si':
        return salida
    descartadas = [k for k in ALTERNATIVAS if lectura[k] == 'no']
    codigos = {ALTERNATIVAS[k] for k in descartadas}
    # El texto puede pedir una extracción distinta de la rama vista en la foto.
    if '176008' in codigos and CAIDA.search(contexto) and not EXTRACCION.search(contexto):
        codigos.add('176011')
    evidencia = ' '.join(lectura['evidencia'].split())[:240]
    problemas = [p for p in salida['problemas'] if p['key'] not in descartadas]
    return dict(salida, problemas=problemas,
                posibles=[p for p in salida['posibles'] if p['key'] not in descartadas],
                en_duda=[k for k in salida['en_duda'] if k not in descartadas],
                categorias_contexto=[c for c in salida['categorias_contexto']
                                     if c.get('key') not in descartadas and str(c.get('codigo')) not in codigos],
                revision_ramas={'evidencia': evidencia, 'descartadas': descartadas},
                descripcion=evidencia if {p['key'] for p in problemas} == {RETIRO} else salida['descripcion'])


def revisar(img, salida, perfil, contexto=''):
    if not perfil or perfil.modo not in {'alto', 'prueba'} or not requiere_revision(salida, contexto):
        return salida
    import modos_analisis as modos
    import verificador as V

    if not modos.caben_apoyos('rama_desprendida'):
        return salida
    modelo = perfil.verificadores[1 if perfil.modo == 'prueba' else 0]
    mensajes = [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': V._imagen_data_url(img)}}]}]
    lectura, = V._map_modelos((modelo,), lambda m: V._extraer_json(V._llamar(
        m, mensajes, max_tokens=400, intentos=1, etapa='rama_desprendida', esquema=SCHEMA, esfuerzo='low')))
    if not lectura_valida(lectura):
        modos.fallo('rama_desprendida')
        salida['detalle']['verificacion']['revision_ramas_fallo'] = True
        return salida
    return aplicar(salida, lectura, contexto)
