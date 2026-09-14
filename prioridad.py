"""Intervención principal sin cambiar clasificación, gravedad ni predominante (#48)."""

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
                if sum(p.get('fundamento') == fundamento for p in propuestas) >= 2:
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
    motivo = FUNDAMENTOS.get(elegido.get('fundamento'),
                             'Es la intervención principal de la escena; los demás hallazgos siguen registrados.')
    return {'key': elegido['key'], 'estado': 'seleccionado', 'criterio': 'escena', 'motivo': motivo}
