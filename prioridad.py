"""Intervención principal sin cambiar clasificación, gravedad ni predominante (#48)."""

CATEGORIAS_OBJETO = {'retiro_muebles', 'retiro_poda', 'retiro_escombros', 'recoleccion'}


def _fundamento_compatible(fundamento, key):
    return fundamento != 'objeto_principal' or key in CATEGORIAS_OBJETO


FUNDAMENTOS = {
    'acumulacion_principal': 'La mayor acumulación de residuos corresponde a esta intervención; los demás residuos son secundarios.',
    'objeto_principal': 'El objeto descartado que motiva el retiro es el hallazgo principal de la escena.',
    'paso_obstruido': 'Esta intervención atiende la obstrucción visible del paso.',
}


def normalizar(valor, categorias, vistas, contexto, categorias_contexto):
    if not isinstance(valor, dict):
        return None
    key, criterio = valor.get('key'), valor.get('criterio')
    if (not isinstance(key, str) or key not in categorias
            or key not in {c.get('key') for c in vistas}
            or criterio not in ('escena', 'pedido_explicito')):
        return None
    if criterio == 'pedido_explicito':
        cita = valor.get('cita_vecinal')
        if (not isinstance(cita, str) or not cita.strip() or not contexto.strip()
                or cita.strip().casefold() not in contexto.casefold()
                or not any(c.get('key') == key and c.get('respaldo') == 'compatible'
                           for c in categorias_contexto)):
            return None
    # No conservar citas ni prosa libre del comentario vecinal en el diagnóstico.
    r = {'key': key, 'criterio': criterio}
    fundamento = valor.get('fundamento')
    if isinstance(fundamento, str) and fundamento in FUNDAMENTOS:
        r['fundamento'] = fundamento
    return r


def _confirmados(publica):
    vistos = []
    for c in publica.get('problemas') or []:
        key = c.get('key') if isinstance(c, dict) else None
        if isinstance(key, str) and key not in vistos:
            vistos.append(key)
    return vistos


def _propuestas_completas(verificadores):
    ok = [v for v in verificadores or [] if isinstance(v, dict) and v.get('ok') is True]
    if not ok:
        return None
    fuentes = {v.get('modelo') for v in ok if v.get('modelo')}
    if (len(fuentes) < 2 or len(fuentes) != len(ok)
            or len(ok) != len(verificadores or [])):
        return None
    propuestas = []
    for v in ok:
        p = v.get('prioridad_propuesta')
        if not isinstance(p, dict) or not isinstance(p.get('key'), str) or not p.get('key'):
            return None
        propuestas.append(p)
    return propuestas


def armar_candidatos(publica, verificadores, catalogo=None):
    catalogo = catalogo or {}
    candidatos = []
    for key in _confirmados(publica):
        observaciones = []
        vistos = set()
        for v in verificadores or []:
            for c in (v.get('categorias') or []) if isinstance(v, dict) else []:
                if not isinstance(c, dict) or c.get('key') != key:
                    continue
                evidencia = c.get('evidencia')
                if not isinstance(evidencia, str):
                    continue
                texto = evidencia.strip()
                marca = texto.casefold()
                if texto and marca not in vistos:
                    vistos.add(marca)
                    observaciones.append(texto)
        observaciones.sort(key=str.casefold)
        nombre = (catalogo.get(key) or {}).get('nombre') if isinstance(catalogo.get(key), dict) else None
        candidatos.append({
            'key': key,
            'nombre': nombre if isinstance(nombre, str) and nombre.strip() else key,
            'observaciones': observaciones,
        })
    return candidatos


def requiere_comparacion(publica, verificadores):
    if (publica.get('evaluacion_foto') or {}).get('rechazada'):
        return False
    if (publica.get('contexto_visual') or {}).get('suficiente') is False:
        return False
    confirmados = set(_confirmados(publica))
    if len(confirmados) < 2:
        return False
    propuestas = _propuestas_completas(verificadores)
    if not propuestas:
        return False
    valores = {(p.get('key'), p.get('criterio')) for p in propuestas}
    if len(valores) == 1:
        return False
    return any(p.get('key') in confirmados for p in propuestas)


def interpretar_comparacion(valor, confirmados):
    if not isinstance(valor, dict):
        return None
    key = valor.get('key')
    if not isinstance(key, str) or key not in confirmados:
        return None
    r = {'key': key, 'criterio': 'escena'}
    fundamento = valor.get('fundamento')
    if isinstance(fundamento, str) and fundamento in FUNDAMENTOS:
        r['fundamento'] = fundamento
    return r


def desde_guardada(verificacion):
    if not isinstance(verificacion, dict):
        return None
    if verificacion.get('prioridad_comparacion_error'):
        def falla(_candidatos):
            raise RuntimeError('prioridad_comparacion')
        return falla
    if 'prioridad_comparacion' not in verificacion:
        return None
    valor = verificacion.get('prioridad_comparacion')
    return lambda _candidatos: valor


def seleccionar(publica, verificadores, comparacion=None):
    sin = {'key': None, 'estado': 'sin_problemas_confirmados', 'criterio': None, 'motivo': None}
    if (publica.get('evaluacion_foto') or {}).get('rechazada'):
        return dict(sin, estado='no_aplica')
    confirmados = {c['key']: c for c in publica.get('problemas') or [] if c.get('key')}
    if not confirmados:
        return sin
    if len(confirmados) == 1:
        key = next(iter(confirmados))
        return {'key': key, 'estado': 'seleccionado', 'criterio': 'unico_confirmado',
                'motivo': 'Es el único problema confirmado en esta respuesta.'}
    propuestas = [v.get('prioridad_propuesta') for v in verificadores if v.get('ok') is True]
    propuestas = [p for p in propuestas if isinstance(p, dict)]
    if not propuestas:
        return dict(sin, estado='no_evaluado')
    pendiente = dict(sin, estado='indeterminado')
    if (publica.get('contexto_visual') or {}).get('suficiente') is False:
        return pendiente
    fuentes = {v.get('modelo') for v in verificadores if v.get('modelo')}
    if (len(fuentes) < 2 or len(fuentes) != len(verificadores)
            or len(propuestas) != len(verificadores)):
        return pendiente
    valores = {(p['key'], p['criterio']) for p in propuestas}
    if len(valores) == 1:
        key, criterio = next(iter(valores))
        if key not in confirmados:
            return pendiente
        motivo = ('Es el problema confirmado que corresponde al pedido explícito del vecino.'
                  if criterio == 'pedido_explicito' else
                  'Es la intervención principal de la escena; los demás hallazgos siguen registrados.')
        if criterio == 'escena':
            for fundamento, texto in FUNDAMENTOS.items():
                if (_fundamento_compatible(fundamento, key)
                        and sum(p.get('fundamento') == fundamento for p in propuestas) >= 2):
                    motivo = texto
                    break
        return {'key': key, 'estado': 'seleccionado', 'criterio': criterio, 'motivo': motivo}
    if comparacion is None or not requiere_comparacion(publica, verificadores):
        return pendiente
    try:
        valor = comparacion(armar_candidatos(publica, verificadores))
    except Exception:
        return dict(sin, estado='no_evaluado')
    elegido = interpretar_comparacion(valor, confirmados)
    if elegido is None:
        return pendiente
    fundamento = elegido.get('fundamento')
    if not _fundamento_compatible(fundamento, elegido['key']):
        fundamento = None
    motivo = FUNDAMENTOS.get(fundamento,
                             'Es la intervención principal de la escena; los demás hallazgos siguen registrados.')
    return {'key': elegido['key'], 'estado': 'seleccionado', 'criterio': 'escena', 'motivo': motivo}


def ajustar_descripcion(publica, verificadores, auditorias=()):
    """Evita que una lectura secundaria sustituya el principal confirmado (#142).

    No vuelve a adjudicar la foto ni copia descripciones completas de lectores:
    conserva la explicación anterior y usa solo evidencia del principal.
    """
    principal = publica.get('problema_principal') or {}
    key = principal.get('key')
    confirmados = {c.get('key'): c for c in publica.get('problemas') or []}
    descarte_sin_confirmar = any(q.get('descarte_independiente') is True
        and not q.get('confirmo') and q.get('key') not in confirmados
        for q in auditorias or [])
    principal_seleccionado = principal.get('estado') == 'seleccionado' and key in confirmados
    if not principal_seleccionado and descarte_sin_confirmar and 'situacion_calle' in confirmados:
        key = 'situacion_calle'
    if ((not principal_seleccionado and not (descarte_sin_confirmar and key in confirmados))
            or (publica.get('evaluacion_foto') or {}).get('rechazada')
            or (publica.get('contexto_visual') or {}).get('suficiente') is False):
        return
    descripcion = publica.get('descripcion')
    if not isinstance(descripcion, str) or not descripcion.strip():
        if not descarte_sin_confirmar:
            return
        descripcion = ''
    revision_escombros = publica.get('verificacion_escombros') or {}
    motivo_escombros = revision_escombros.get('motivo')
    duda_encabeza = (principal_seleccionado and key != 'retiro_escombros'
        and 'retiro_escombros' not in confirmados
        and any(c.get('key') == 'retiro_escombros' for c in publica.get('posibles') or [])
        and isinstance(motivo_escombros, str) and bool(motivo_escombros.strip())
        and (descripcion == motivo_escombros
             or descripcion.startswith(motivo_escombros + ' Otros hallazgos: ')))
    lectores = [v for v in verificadores or [] if v.get('ok') is True]
    def corresponde(v):
        texto = v.get('descripcion')
        return isinstance(texto, str) and (texto == descripcion or (
            len(descripcion) > 40 and descripcion.endswith(('.', '!', '?'))
            and texto.startswith(descripcion + ' ')))
    origenes = [v for v in lectores if corresponde(v)]
    # Conserva prosa sin procedencia reconocible salvo que una auditoría de
    # descarte posterior impida afirmar el objeto como residuo.
    if not descarte_sin_confirmar and not duda_encabeza and (not origenes or any(
            any(c.get('key') == key for c in v.get('categorias') or [])
            for v in origenes)):
        return
    evidencias = []
    fuentes = set()
    for v in lectores:
        for c in v.get('categorias') or []:
            evidencia = c.get('evidencia')
            if c.get('key') == key and isinstance(evidencia, str) and evidencia.strip():
                evidencias.append(' '.join(evidencia.split())[:500])
                if v.get('modelo'):
                    fuentes.add(v['modelo'])
    if not evidencias:
        return
    evidencia = min(set(evidencias), key=lambda t: (len(t), t.casefold(), t))
    nombre = confirmados[key].get('nombre') or key
    etiqueta = 'Problema principal: ' if principal_seleccionado else 'Hallazgo confirmado: '
    texto = etiqueta + nombre + '. Evidencia: ' + evidencia.rstrip('.') + '.'
    secundarios = [c.get('nombre') or k for k, c in confirmados.items() if k != key]
    if secundarios:
        texto += ' Otros hallazgos confirmados: ' + ', '.join(secundarios) + '.'
    if duda_encabeza:
        texto += ' Sin confirmar: ' + motivo_escombros
    if texto == descripcion:
        return
    publica['descripcion'] = texto
    publica['detalle_descripcion'] = {'estado': ('alineada_a_descarte' if descarte_sin_confirmar
                                               else 'confirmado_antes_de_duda' if duda_encabeza else 'alineada_al_principal'),
        'descripcion_anterior': descripcion, 'fuentes_evidencia': sorted(fuentes)}
