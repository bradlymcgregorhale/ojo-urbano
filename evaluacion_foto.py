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
    evaluacion = {'ambito': ambito, 'estado_ambito': estado_ambito,
                  'estado': estado,
                  'calidad_suficiente': calidad, 'estado_calidad': estado_calidad,
                  'motivos': ['interior'] if interior else motivos_calidad,
                  'rechazada': rechazada, 'requiere_revision': False, 'requiere_nueva_foto': rechazada,
                  'requiere_foto_complementaria': contexto is False,
                  'indicacion': INDICACION_INTERIOR if interior else INDICACION_CALIDAD if calidad is False
                  else INDICACION_CONTEXTO if contexto is False else None}
    encuadre = {'suficiente': contexto, 'estado': estado_contexto, 'motivos': motivos_contexto,
                'indicacion': INDICACION_CONTEXTO if contexto is False else None}
    return evaluacion, encuadre


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
