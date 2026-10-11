"""Distingue el fragmento del cordón de un retiro independiente."""
import copy

from prompts import cargar_prompt

KEY = 'retiro_escombros'
MODELOS = ('anthropic/claude-sonnet-4.6', 'google/gemini-3.8-flash', 'openai/gpt-5.6-luna')
ESTADOS = {'fragmentos_del_dano', 'pila_independiente', 'indeterminado'}
PROMPT = cargar_prompt('dirigidos/fragmentos_cordon')


def interpretar(valor):
    if (not isinstance(valor, dict) or not isinstance(valor.get('estado'), str) or valor['estado'] not in ESTADOS
            or not isinstance(valor.get('evidencia'), str) or not valor['evidencia'].strip()):
        raise ValueError('La relación entre cordón y restos no tiene evidencia válida')
    return {'estado': valor['estado'], 'evidencia': valor['evidencia'].strip()[:500]}


def consultar(img):
    import verificador as V
    contenido = [{'type': 'image_url', 'image_url': {'url': V._imagen_data_url(img, lado=1600)}}]

    def uno(modelo):
        return interpretar(V._extraer_json(V._llamar(modelo,
            [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': contenido}],
            max_tokens=600, etapa='fragmentos_cordon')))

    resultados = V._map_modelos(MODELOS, uno)
    lecturas = [dict(modelo=m, **v) for m, v in zip(MODELOS, resultados) if v is not V._FALLO_MODELO]
    return {'lecturas': lecturas, 'fallo': len(lecturas) != len(MODELOS)}


def decidir(revision):
    lecturas = revision.get('lecturas') or []
    if (revision.get('fallo') or len(lecturas) != len(MODELOS)
            or {v.get('modelo') for v in lecturas} != set(MODELOS)):
        return 'revision'
    try:
        estados = {interpretar(v)['estado'] for v in lecturas}
    except (ValueError, TypeError):
        return 'revision'
    if estados == {'fragmentos_del_dano'}:
        return 'fragmentos_del_dano'
    if estados == {'pila_independiente'}:
        return 'pila_independiente'
    return 'revision'


def aplicar(salida, revision):
    r = copy.deepcopy(salida)
    estado = decidir(revision)
    veri = r.setdefault('detalle', {}).setdefault('verificacion', {})
    veri['revision_fragmentos'] = dict(copy.deepcopy(revision), estado=estado)
    if estado != 'fragmentos_del_dano':
        return r
    previas = [p for p in r.get('problemas') or [] if p.get('key') == KEY]
    veri['revision_fragmentos']['confirmadas_previas'] = copy.deepcopy(previas)
    r['problemas'] = [p for p in r.get('problemas') or [] if p.get('key') != KEY]
    r['posibles'] = [p for p in r.get('posibles') or [] if p.get('key') != KEY]
    r['en_duda'] = [k for k in r.get('en_duda') or [] if k != KEY]
    r['hay_problema'] = bool(r['problemas'])
    r['hay_reclamo'] = bool(r['problemas'] or r.get('categorias_contexto'))
    r['gravedad_maxima'] = max((p.get('gravedad') or 0 for p in r['problemas']), default=0) or None
    r['verificacion_escombros'] = {'estado': 'excluido', 'basado_en_contexto': False,
        'requiere_nueva_foto': False,
        'motivo': 'Los fragmentos corresponden al daño del cordón; no se corroboró una pila independiente para retirar.'}
    nombres = '; '.join(p.get('nombre') or p['key'] for p in r['problemas'])
    r['descripcion'] = 'Hallazgos confirmados: ' + nombres + '.'
    return r


def revisar(img, salida):
    import modos_analisis as M
    perfil = M.perfil_actual()
    veri = (salida.get('detalle') or {}).get('verificacion') or {}
    claves = {p.get('key') for p in salida.get('problemas') or []}
    if (veri.get('activa') is not True or not perfil or perfil.modo != 'alto'
            or salida.get('foto_valida') is False
            or (veri.get('revision_cordon') or {}).get('estado') != 'confirmado'
            or not {'reparacion_cordon', KEY} <= claves
            or any(p.get('key') == KEY and p.get('origen') == 'contexto_vecinal'
                   for p in salida.get('problemas') or [])):
        return salida
    revision = consultar(img)
    if revision.get('fallo'):
        M.fallo('fragmentos_cordon')
    return aplicar(salida, revision)
