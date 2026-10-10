"""Recupera objetos omitidos con lecturas independientes y evidencia acotada."""
import copy
import hashlib
import json
from pathlib import Path

from prompts import cargar_prompt

OBJETOS = cargar_prompt('dirigidos/objetos_omitidos')
CUBIERTA = cargar_prompt('dirigidos/cubierta_contenedor')
REFERENCIAS = Path(__file__).parent / 'eval/vision/private/serving-cubierta-111-v2.json'
REFERENCIAS_SHA256 = 'dd236dbe45d0ed7c979bbd78977ca75c9d359c21b29a1f75196a61a3f87ecda1'
TIPOS = {
    'envase_pintura': ('retiro_muebles', 'descartado', 'Envases de pintura o químicos descartados.'),
    'tanque_descartado': ('retiro_muebles', 'descartado', 'Tanque o tambor voluminoso descartado.'),
    'venta_ambulante': ('manteros', 'venta', 'Mercadería exhibida para venta ambulante.'),
}
MONTAJES = {'montado_cerrado', 'articulado_abierto', 'suelto_adentro',
            'suelto_afuera', 'ausente', 'indeterminado'}
DANO = {'suelto_adentro', 'suelto_afuera', 'ausente'}
MONTADO = {'montado_cerrado', 'articulado_abierto'}


def interpretar_objetos(valor):
    if not isinstance(valor, dict) or not isinstance(valor.get('hallazgos'), list):
        raise ValueError('inventario de objetos incompleto')
    salida, invalidos = [], 0
    for h in valor['hallazgos'][:10]:
        if (not isinstance(h, dict) or h.get('tipo') not in TIPOS
                or h.get('uso') not in {'descartado', 'en_uso', 'venta', 'indeterminado'}
                or any(not isinstance(h.get(k), str) or not h[k].strip()
                       for k in ('objeto', 'evidencia', 'ubicacion'))):
            invalidos += 1
            continue
        salida.append({k: h[k].strip()[:500] for k in
                       ('tipo', 'uso', 'objeto', 'evidencia', 'ubicacion')})
    return {'hallazgos': salida, 'fallo': bool(invalidos), 'descartados_por_formato': invalidos}


def interpretar_cubierta(valor):
    if (not isinstance(valor, dict) or valor.get('montaje') not in MONTAJES
            or not isinstance(valor.get('evidencia'), str) or not valor['evidencia'].strip()):
        raise ValueError('montaje sin evidencia válida')
    return {'montaje': valor['montaje'], 'evidencia': valor['evidencia'].strip()[:500]}


def _consultar(img, prompt, contenido, etapa, interpretar, modelos=None):
    import verificador as V
    modelos = list(dict.fromkeys(V.modelos_activos() if modelos is None else modelos))
    mensajes = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': contenido}]
    def uno(modelo):
        return interpretar(V._extraer_json(V._llamar(
            modelo, mensajes, max_tokens=1500 if etapa == 'objetos_omitidos' else 1000,
            etapa=etapa)))
    resultados = V._map_modelos(modelos, uno)
    lecturas = [dict(modelo=m, **r) for m, r in zip(modelos, resultados)
                if r is not V._FALLO_MODELO]
    return {'lecturas': lecturas, 'fallo': len(lecturas) != len(modelos)
            or any(r.get('fallo') for r in lecturas)}


def consultar_objetos(img):
    import verificador as V
    ancho, alto = img.size
    contenido = [{'type': 'text', 'text': 'Foto completa:'},
                 {'type': 'image_url', 'image_url': {'url': V._imagen_data_url(img, lado=1600)}}]
    for nombre, caja in [('mitad izquierda', (0, 0, max(1, int(ancho * .6)), alto)),
                         ('mitad derecha', (int(ancho * .4), 0, ancho, alto))]:
        contenido.extend([{'type': 'text', 'text': nombre + ' de la misma foto'},
            {'type': 'image_url', 'image_url': {'url': V._imagen_data_url(img.crop(caja), lado=1600)}}])
    return _consultar(img, OBJETOS, contenido, 'objetos_omitidos', interpretar_objetos)


def cargar_referencias():
    raw = REFERENCIAS.read_bytes()
    if hashlib.sha256(raw).hexdigest() != REFERENCIAS_SHA256:
        raise ValueError('referencias visuales modificadas')
    return json.loads(raw)


def consultar_cubierta(img):
    import verificador as V
    import modos_analisis as modos
    try:
        contenido = cargar_referencias()
    except (OSError, ValueError):
        return {'lecturas': [], 'fallo': True, 'motivo': 'Referencias visuales no disponibles.'}
    contenido.extend([{'type': 'text', 'text': 'Ahora evaluá solamente esta foto:'},
                      {'type': 'image_url', 'image_url': {'url': V._imagen_data_url(img, lado=1400)}}])
    modelos = list(V.modelos_activos())
    perfil = modos.perfil_actual()
    if (perfil is None or perfil.modo == 'alto') and 'google/gemini-3.8-flash' not in modelos:
        # El lector de inventario también distingue el montaje. Los modos
        # reducidos conservan sus lectores y su límite de consultas.
        modelos = ['google/gemini-3.8-flash' if m == 'openai/gpt-5-mini' else m for m in modelos]
    return _consultar(img, CUBIERTA, contenido, 'cubierta_contenedor', interpretar_cubierta, modelos)


def _unicas(lecturas):
    # Una misma fuente nunca cuenta dos veces, incluso en reproducciones guardadas.
    return {v['modelo']: v for v in lecturas if isinstance(v, dict)
            and isinstance(v.get('modelo'), str) and v['modelo']}.values()


def decidir_objetos(revision):
    lecturas = list(_unicas(revision.get('lecturas') or []))
    confirmados = []
    for tipo, (key, uso, descripcion) in TIPOS.items():
        fuentes = [v['modelo'] for v in lecturas if any(
            h.get('tipo') == tipo and h.get('uso') == uso
            for h in v.get('hallazgos') or [])]
        # Una pertenencia o un objeto en uso explícito bloquean el retiro.
        veto = uso == 'descartado' and any(h.get('tipo') == tipo and h.get('uso') == 'en_uso'
            for v in lecturas for h in v.get('hallazgos') or [])
        if len(fuentes) >= 2 and not veto:
            confirmados.append({'key': key, 'fuentes': fuentes, 'tipo': tipo,
                                'evidencia': descripcion})
    return confirmados


def decidir_cubierta(revision):
    lecturas = list(_unicas(revision.get('lecturas') or []))
    fuentes = [v['modelo'] for v in lecturas if v.get('montaje') in DANO]
    # Se conserva el veto estricto: una lectura montada impide confirmar rotura.
    if len(fuentes) >= 2 and not any(v.get('montaje') in MONTADO for v in lecturas):
        return [{'key': 'reparacion_contenedor', 'fuentes': fuentes,
                 'evidencia': 'La cubierta superior del contenedor está ausente o desprendida.'}]
    return []


def aplicar(salida, revision, categorias):
    """Agrega hallazgos corroborados; conserva decisiones y lecturas anteriores."""
    r = copy.deepcopy(salida)
    veri = r.setdefault('detalle', {}).setdefault('verificacion', {})
    veri['revision_omisiones'] = copy.deepcopy(revision)
    if r.get('foto_valida') is False:
        return r
    nuevos = decidir_objetos(revision.get('objetos') or {})
    cubierta = decidir_cubierta(revision.get('cubierta') or {})
    # Una segunda mirada anterior que encontró el contenedor usable no se revoca.
    if (veri.get('segunda_mirada_dano') or {}).get('sin_dano'):
        veri['revision_omisiones']['conflicto_cubierta'] = bool(cubierta)
    else:
        nuevos += cubierta
    existentes = {p['key'] for p in r.get('problemas') or []}
    agregados = {}
    for hallazgo in nuevos:
        key = hallazgo['key']
        if key in existentes or key not in categorias:
            continue
        if key not in agregados:
            agregado = dict(categorias[key], key=key, gravedad=2, fuentes=[],
                            origen='foto', confirmado_por='revision_omisiones')
            agregados[key] = agregado
        agregado = agregados[key]
        agregado['fuentes'] = sorted(set(agregado['fuentes']) | set(hallazgo['fuentes']))
    if not agregados:
        return r
    r.setdefault('problemas', []).extend(agregados.values())
    r['posibles'] = [p for p in r.get('posibles') or [] if p.get('key') not in agregados]
    r['en_duda'] = [k for k in r.get('en_duda') or [] if k not in agregados]
    r['hay_problema'] = r['hay_reclamo'] = True
    r['gravedad_maxima'] = max(p.get('gravedad') or 1 for p in r['problemas'])
    veri['revision_omisiones']['agregados'] = sorted(agregados)
    veri['revision_omisiones']['descripcion_anterior'] = r.get('descripcion')
    # La descripción previa puede negar el objeto que se acaba de reconocer.
    # Se reemplaza por hallazgos confirmados, sin repetir detalles discutidos.
    r['descripcion'] = 'Hallazgos confirmados: ' + ', '.join(
        p.get('nombre') or p['key'] for p in r['problemas']) + '.'
    return r


def revisar(img, salida, categorias):
    import verificador as V
    import evaluacion_foto
    veri = (salida.get('detalle') or {}).get('verificacion') or {}
    lecturas = veri.get('verificadores') or []
    evaluacion, _ = evaluacion_foto.resumir(lecturas)
    if (salida.get('foto_valida') is False or evaluacion.get('rechazada')
            or not veri.get('activa') or not any(v.get('ok') is True for v in lecturas)):
        return salida
    trabajos = [('objetos', consultar_objetos)]
    hay_contenedor = any(c.get('key') in V.CONTENEDOR_KEYS
        for v in lecturas for c in v.get('categorias') or [])
    montaje_ya_revisado = (veri.get('segunda_mirada_dano') or {}).get('tipo_pregunta') == 'montaje_cubierta'
    if hay_contenedor and not montaje_ya_revisado and not any(p.get('key') == 'reparacion_contenedor'
                                  for p in salida.get('problemas') or []):
        trabajos.append(('cubierta', consultar_cubierta))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(len(trabajos)) as pool:
        resultados = V._map_con_contexto(pool, lambda t: t[1](img), trabajos)
    return aplicar(salida, dict(zip((t[0] for t in trabajos), resultados)), categorias)
