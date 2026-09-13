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


def seleccionar(publica, verificadores):
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
    if len(valores) != 1:
        return pendiente
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
