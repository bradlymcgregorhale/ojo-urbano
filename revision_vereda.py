"""Distingue rotura propia del plano peatonal de otros objetos dañados."""
import copy

from prompts import cargar_prompt

KEY = 'reparacion_vereda'
PROMPT = cargar_prompt('dirigidos/vereda_geometria')
MODELOS = ('anthropic/claude-sonnet-4.6', 'google/gemini-3.8-flash', 'openai/gpt-5.6-luna')
NEGATIVOS = {'fisura_menor', 'hueco_servicio', 'dano_cordon',
             'afectacion_puntual_elemento', 'sin_rotura'}
ESTADOS = NEGATIVOS | {'rotura_estructural', 'indeterminado'}


def interpretar(valor):
    if (not isinstance(valor, dict) or valor.get('estado') not in ESTADOS
            or any(not isinstance(valor.get(k), str) or not valor[k].strip()
                   for k in ('sector', 'evidencia'))):
        raise ValueError('Plano peatonal sin geometría válida')
    return {k: valor[k].strip()[:500] for k in ('estado', 'sector', 'evidencia')}


def consultar(img):
    import verificador as V
    ancho, alto = img.size
    contenido = [{'type': 'text', 'text': 'Foto completa, sin comentario del vecino:'},
        {'type': 'image_url', 'image_url': {'url': V._imagen_data_url(img, lado=1600)}}]
    for nombre, caja in [('izquierda', (0, 0, max(1, int(ancho * .6)), alto)),
                         ('derecha', (int(ancho * .4), 0, ancho, alto))]:
        contenido.extend([{'type': 'text', 'text': 'Detalle ' + nombre + ' de la MISMA foto'},
            {'type': 'image_url', 'image_url': {'url': V._imagen_data_url(img.crop(caja), lado=1600)}}])
    def uno(modelo):
        return interpretar(V._extraer_json(V._llamar(modelo,
            [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': contenido}],
            max_tokens=1000, etapa='vereda_geometria')))
    resultados = V._map_modelos(MODELOS, uno)
    lecturas = [dict(modelo=m, **v) for m, v in zip(MODELOS, resultados)
                if v is not V._FALLO_MODELO]
    return {'lecturas': lecturas, 'fallo': len(lecturas) != len(MODELOS)}


def decidir(revision):
    lecturas = revision.get('lecturas') or []
    if (revision.get('fallo') or len(lecturas) != len(MODELOS)
            or {v.get('modelo') for v in lecturas} != set(MODELOS)):
        return 'revision'
    try:
        lecturas = [interpretar(v) for v in lecturas]
    except (TypeError, ValueError):
        return 'revision'
    estructurales = sum(v['estado'] == 'rotura_estructural' for v in lecturas)
    if estructurales >= 2:
        return 'confirmado'
    if not estructurales and sum(v['estado'] in NEGATIVOS for v in lecturas) >= 2:
        return 'sin_rotura'
    return 'revision'


def aplicar(salida, revision):
    r = copy.deepcopy(salida)
    estado = decidir(revision)
    consenso = estado
    conflicto_registro = (estado == 'confirmado'
        and any(c.get('key') == 'tapa_vereda' for c in r.get('problemas') or [])
        and any(v.get('estado') == 'hueco_servicio'
                for v in revision.get('lecturas') or []))
    if conflicto_registro:
        estado = 'revision'
    veri = r.setdefault('detalle', {}).setdefault('verificacion', {})
    veri['revision_vereda'] = dict(copy.deepcopy(revision), estado=estado)
    if conflicto_registro:
        veri['revision_vereda'].update(estado_consenso=consenso,
            conflicto_servicio='tapa_vereda',
            motivo_conflicto='Un lector atribuye el hueco al registro ya corroborado. '
            'Falta acuerdo sobre una rotura peatonal independiente de ese objeto.')
    if estado == 'confirmado':
        if any(c.get('key') == KEY for c in r.get('problemas') or []):
            return r
        candidatas = [c for c in r.get('posibles') or [] if c.get('key') == KEY]
        if not candidatas or r.get('foto_valida') is False:
            return r
        previa = candidatas[0]
        veri['revision_vereda']['candidata_previa'] = copy.deepcopy(previa)
        fuentes = [v['modelo'] for v in revision['lecturas']
                   if v['estado'] == 'rotura_estructural']
        agregado = {k: copy.deepcopy(v) for k, v in previa.items()
                    if k not in {'arbitro', 'motivo', 'revision_geometria', 'fuentes'}}
        agregado.update(fuentes=fuentes, origen='foto',
                        gravedad=previa.get('gravedad') or 2,
                        confirmado_por='revision_vereda')
        r.setdefault('problemas', []).append(agregado)
        r['posibles'] = [c for c in r['posibles'] if c.get('key') != KEY]
        r['en_duda'] = [k for k in r.get('en_duda') or [] if k != KEY]
        r['hay_problema'] = r['hay_reclamo'] = True
        r['gravedad_maxima'] = max(c.get('gravedad') or 0 for c in r['problemas'])
        veri['revision_vereda']['descripcion_anterior'] = r.get('descripcion')
        r['descripcion'] = 'Hallazgos confirmados: ' + ', '.join(
            c.get('nombre') or c['key'] for c in r['problemas']) + '.'
        return r
    anteriores = [c for c in r.get('problemas') or []
                  if c.get('key') == KEY and c.get('origen') != 'contexto_vecinal']
    if not anteriores:
        return r
    veri['revision_vereda']['confirmadas_previas'] = copy.deepcopy(anteriores)
    motivo = ('No se corroboró una rotura propia del plano peatonal; las lecturas distinguen '
              'fisuras menores, una tapa o el cordón.' if estado == 'sin_rotura' else
              'La geometría del plano peatonal no reúne dos lecturas estructurales. Requiere revisión.')
    if conflicto_registro:
        motivo = ('El registro de servicio está corroborado, pero las lecturas discrepan '
                  'sobre una rotura independiente de vereda. Requiere revisión.')
    r['problemas'] = [c for c in r['problemas'] if c not in anteriores]
    r['posibles'] = [c for c in r.get('posibles') or [] if c.get('key') != KEY]
    r['en_duda'] = [k for k in r.get('en_duda') or [] if k != KEY]
    r['posibles'].append(dict(anteriores[0], gravedad=None,
        arbitro='rechazar' if estado == 'sin_rotura' else None,
        motivo=motivo, revision_geometria=estado))
    if estado == 'revision':
        r['en_duda'].append(KEY)
    r['hay_problema'] = bool(r['problemas'])
    r['hay_reclamo'] = bool(r['problemas'] or r.get('categorias_contexto'))
    r['gravedad_maxima'] = max((c.get('gravedad') or 0 for c in r['problemas']), default=0) or None
    veri['revision_vereda']['descripcion_anterior'] = r.get('descripcion')
    otros = ', '.join(c.get('nombre') or c['key'] for c in r['problemas'])
    r['descripcion'] = motivo + (' Otros hallazgos: ' + otros + '.' if otros else '')
    return r


def revisar(img, salida):
    import modos_analisis as modos
    perfil = modos.perfil_actual()
    if (not perfil or perfil.modo != 'alto' or salida.get('foto_valida') is False
            or not any(c.get('key') == KEY and c.get('origen') != 'contexto_vecinal'
                       for c in (salida.get('problemas') or [])
                       + (salida.get('posibles') or []))):
        return salida
    revision = consultar(img)
    if revision.get('fallo'):
        modos.fallo('vereda_geometria')
    return aplicar(salida, revision)
