"""Ámbito, evaluabilidad y encuadre, separados de la clasificación (#43, #46, #52)."""
import copy

MOTIVOS_CALIDAD = {
    'desenfoque', 'movimiento', 'oscuridad', 'sobreexposicion', 'detalle_insuficiente',
}
MOTIVOS_CONTEXTO = {'encuadre_demasiado_cerrado', 'entorno_no_visible', 'situacion_cortada'}
INDICACION_INTERIOR = ('Necesitamos una foto del objeto o problema en la vía pública. '
                      'Una foto dentro de una vivienda o espacio privado no sirve para este reclamo.')
INDICACION_CALIDAD = ('Sacá otra foto con el objeto o problema enfocado, buena iluminación '
                     'y suficiente detalle para evaluarlo.')
INDICACION_CONTEXTO = ('Sacá una foto complementaria desde más lejos, mostrando el objeto '
                      'o problema y su relación con la vereda o la calle.')
INDICACION_REVISION = ('Hay una observación sobre la ubicación, calidad o encuadre que requiere '
                      'revisión. La foto no se rechazó automáticamente.')
INDICACION_AMBITO = ('La ubicación del objeto o problema requiere revisión. Agregá contexto '
                    'o una foto que muestre si está en la vía pública.')
RETIROS_HIGIENE = {
    'recoleccion', 'barrido', 'retiro_escombros', 'retiro_muebles', 'retiro_poda',
    'acopio_recuperadores', 'residuos_establecimiento',
}


def normalizar(valor):
    """Descarta valores malformados sin convertirlos en un rechazo."""
    if not isinstance(valor, dict):
        return None
    r = {}
    ambito = valor.get('ambito')
    r['ambito'] = ambito if ambito in ('publica', 'interior', 'mixto', 'indeterminado') else None
    evidencia = valor.get('evidencia_ambito')
    if not isinstance(evidencia, str) or not evidencia.strip():
        r['ambito'] = None
    for campo, permitidos in [('calidad', MOTIVOS_CALIDAD), ('contexto', MOTIVOS_CONTEXTO)]:
        v = valor.get(campo + '_suficiente')
        motivos = valor.get('motivos_' + campo)
        motivos = sorted({m for m in motivos if isinstance(m, str) and m in permitidos}) if isinstance(motivos, list) else []
        r[campo + '_suficiente'] = v if type(v) is bool else None
        r['motivos_' + campo] = motivos if v is False else []
        if v is False and not motivos:
            r[campo + '_suficiente'] = None
    return r


def resumir(verificadores):
    """Dos fuentes como mínimo; ninguna respuesta ausente se interpreta como acuerdo."""
    vistos = [v for v in verificadores if isinstance(v, dict)]
    lecturas = [v.get('evaluacion_foto') for v in vistos]
    cantidad = len({v.get('modelo') for v in vistos if v.get('modelo')})
    completas = (cantidad >= 2 and cantidad == len(vistos)
                 and all(v.get('ok') is True for v in vistos)
                 and all(isinstance(x, dict) for x in lecturas))

    def consenso(campo):
        presentes = [x.get(campo) for x in lecturas if isinstance(x, dict) and x.get(campo) is not None]
        if not presentes:
            return None, 'no_evaluado'
        if completas and len(presentes) == len(vistos) and all(x == presentes[0] for x in presentes):
            if presentes[0] in ('indeterminado', 'mixto'):
                return presentes[0], 'indeterminado'
            return presentes[0], 'evaluado'
        return None, 'indeterminado'

    ambito, estado_ambito = consenso('ambito')
    calidad, estado_calidad = consenso('calidad_suficiente')
    contexto, estado_contexto = consenso('contexto_suficiente')
    motivos_calidad = sorted({m for x in lecturas if isinstance(x, dict)
                              for m in x.get('motivos_calidad', [])}) if calidad is False else []
    motivos_contexto = sorted({m for x in lecturas if isinstance(x, dict)
                               for m in x.get('motivos_contexto', [])}) if contexto is False else []
    interior = ambito == 'interior' and estado_ambito == 'evaluado'
    rechazada = interior or calidad is False
    alguna = any(isinstance(x, dict) for x in lecturas)
    negativa = any(isinstance(x, dict) and (x.get('ambito') == 'interior'
                   or x.get('calidad_suficiente') is False or x.get('contexto_suficiente') is False)
                   for x in lecturas)
    sin_objecion = bool(vistos) and all(v.get('ok') is True for v in vistos) and all(
        isinstance(x, dict) and x.get('ambito') == 'publica'
        and x.get('calidad_suficiente') is True and x.get('contexto_suficiente') is True for x in lecturas)
    estado = ('rechazada_interior' if interior else 'rechazada_calidad' if calidad is False
              else 'contexto_insuficiente' if contexto is False
              else 'valida_corroborada' if sin_objecion and completas
              else 'senal_negativa_no_corroborada' if negativa
              else 'sin_objecion' if sin_objecion
              else 'indeterminada' if alguna else 'no_evaluada')
    revision_ambito = estado == 'indeterminada' and any(
        v.get('ok') is True and v.get('modelo') and isinstance(v.get('evaluacion_foto'), dict)
        and v['evaluacion_foto'].get('ambito') in ('mixto', 'indeterminado') for v in vistos)
    evaluacion = {'ambito': ambito, 'estado_ambito': estado_ambito,
                  'estado': estado,
                  'calidad_suficiente': calidad, 'estado_calidad': estado_calidad,
                  'motivos': ['interior'] if interior else motivos_calidad,
                  'rechazada': rechazada,
                  'requiere_revision': estado == 'senal_negativa_no_corroborada' or revision_ambito,
                  'requiere_nueva_foto': rechazada,
                  'requiere_foto_complementaria': contexto is False,
                  'indicacion': INDICACION_INTERIOR if interior else INDICACION_CALIDAD if calidad is False
                  else INDICACION_CONTEXTO if contexto is False
                  else INDICACION_REVISION if estado == 'senal_negativa_no_corroborada'
                  else INDICACION_AMBITO if revision_ambito else None}
    encuadre = {'suficiente': contexto, 'estado': estado_contexto, 'motivos': motivos_contexto,
                'indicacion': INDICACION_CONTEXTO if contexto is False else None}
    return evaluacion, encuadre


def _lecturas_ambito(verificadores):
    """Lecturas usables por modelo. Un duplicado no suma. Un fallo no es un voto."""
    por_modelo = {}
    permitidos = ('publica', 'interior', 'mixto', 'indeterminado')
    for v in verificadores:
        if not isinstance(v, dict) or not v.get('modelo'):
            continue
        if v.get('ok') is not True:
            por_modelo.setdefault(v['modelo'], None)
            continue
        lectura = v.get('evaluacion_foto')
        if isinstance(lectura, dict) and lectura.get('ambito') in permitidos:
            por_modelo[v['modelo']] = lectura['ambito']
        else:
            por_modelo.setdefault(v['modelo'], None)
    validos = [ambito for ambito in por_modelo.values() if ambito]
    return len(por_modelo), validos


def _demorar_higiene_sin_via_publica(r, evaluacion, verificadores):
    """No confirma un retiro de higiene si el ámbito público no está corroborado.

    Hace falta al menos dos lecturas validas y que todas sean publica. Un lector
    que falló no cuenta ni a favor ni en contra. Con una sola fuente el modo
    económico no pierde el hallazgo. Tampoco se rechaza la foto: los retiros
    pasan a posibles y se pide revisión. El encuadre insuficiente conserva el
    detalle visible.
    """
    if evaluacion.get('estado') == 'contexto_insuficiente':
        return r
    intentados, validos = _lecturas_ambito(verificadores)
    if intentados < 2 or not validos:
        return r
    if len(validos) >= 2 and all(ambito == 'publica' for ambito in validos):
        return r
    problemas = list(r.get('problemas') or [])
    higiene = [p for p in problemas if isinstance(p, dict) and p.get('key') in RETIROS_HIGIENE]
    if not higiene:
        return r
    resto = [p for p in problemas if not (isinstance(p, dict) and p.get('key') in RETIROS_HIGIENE)]
    r['problemas'] = resto
    posibles = list(r.get('posibles') or [])
    ya = {p.get('key') for p in posibles if isinstance(p, dict)}
    for p in higiene:
        if p.get('key') in ya:
            continue
        item = dict(p)
        posibles.append(item)
        ya.add(p.get('key'))
    r['posibles'] = posibles
    r['hay_problema'] = bool(resto)
    r['hay_reclamo'] = bool(resto) or bool(r.get('categorias_contexto'))
    if r.get('predominante') in RETIROS_HIGIENE:
        r['predominante'] = resto[0]['key'] if resto and isinstance(resto[0], dict) else None
    principal = r.get('problema_principal')
    if isinstance(principal, dict) and principal.get('key') in RETIROS_HIGIENE:
        r['problema_principal'] = None
    evaluacion['requiere_revision'] = True
    if not evaluacion.get('indicacion'):
        evaluacion['indicacion'] = INDICACION_AMBITO
    if not resto and not r.get('categorias_contexto'):
        r['descripcion'] = evaluacion['indicacion']
    return r


def aplicar(publica, verificadores):
    """Se ejecuta al final para que ningún especialista reponga servicios rechazados."""
    r = copy.deepcopy(publica)
    evaluacion, contexto = resumir(verificadores)
    # No borrar evidencia legible por una evaluación internamente contradictoria.
    if evaluacion['estado'] == 'rechazada_calidad':
        confirmadas = {c.get('key') for c in r.get('problemas') or []}
        respaldo = {}
        for v in verificadores:
            if not isinstance(v, dict) or v.get('ok') is not True or not v.get('modelo'):
                continue
            for c in v.get('categorias') or []:
                if (c.get('key') in confirmadas and not c.get('anulada_por')
                        and str(c.get('evidencia') or '').strip()):
                    respaldo.setdefault(c['key'], set()).add(v['modelo'])
        if any(len(fuentes) >= 2 for fuentes in respaldo.values()):
            evaluacion.update(estado='calidad_contradictoria', rechazada=False,
                              calidad_suficiente=None, estado_calidad='contradictorio',
                              requiere_nueva_foto=False, requiere_revision=True,
                              indicacion='Hay un problema reconocible y una observación contradictoria de calidad. Revisá la foto.')
    r['evaluacion_foto'], r['contexto_visual'] = evaluacion, contexto
    r['estado_evaluacion'] = evaluacion['estado']
    if not evaluacion['rechazada']:
        r = _demorar_higiene_sin_via_publica(r, evaluacion, verificadores)
        r['evaluacion_foto'], r['estado_evaluacion'] = evaluacion, evaluacion['estado']
    if evaluacion['rechazada']:
        for campo in ('problemas', 'posibles', 'categorias_contexto', 'elementos_detectados',
                      'en_duda', 'descartados_por_foto'):
            r[campo] = []
        r['hay_problema'] = r['hay_reclamo'] = False
        r['gravedad_maxima'] = r['predominante'] = None
        r['descripcion'] = evaluacion['indicacion']
        r.pop('patente', None)
        # No publicar un inventario ni una recomendación de retiro utilizable
        # para aceptar la foto que se acaba de rechazar. Los votos crudos quedan en modelos.
        r.pop('contenedores', None)
        r.pop('verificacion_escombros', None)
    return r
