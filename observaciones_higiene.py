"""Materiales, presentación y orientación informativa, sin vetar servicios (#55-57)."""
import re

EVIDENCIA_MAX = 500


def _limpiar_evidencia(texto):
    texto = ''.join(c for c in texto if c == '\n' or c >= ' ')
    return re.sub(r'\s+', ' ', texto).strip()


MATERIALES = {'hojas', 'ramas', 'tierra_polvo', 'papel_carton', 'plastico',
              'piedras', 'hormigon_cascotes', 'madera', 'metal', 'vidrio',
              'excrementos', 'residuos_mezclados', 'no_identificable'}
UBICACIONES = {'vereda_frente_inmueble', 'otra_vereda', 'calzada', 'cordon',
               'interior', 'espacio_publico_especial', 'indeterminada'}
PRESENTACIONES = {'disperso', 'acumulado', 'bolsa', 'bolson', 'objeto', 'indeterminada'}
FUENTE_LIMPIEZA = 'https://buenosaires.gob.ar/gcaba_historico/noticias/buenos-aires-limpia-todo-lo-que-necesitas-saber-para-cuidar-tu-barrio'
FUENTE_ANIMALES = 'https://boletinoficialpdf.buenosaires.gob.ar/util/imagen.php?idf=1&idn=30564'
VERSION_POLITICA_LIMPIEZA = '2026-09-13'
FECHA_CONSULTA_LIMPIEZA = '2026-09-13'


def _orientacion(estado):
    return {'estado': estado, 'es_informativa': True, 'jurisdiccion': 'CABA',
            'politica': 'limpieza_veredas_y_deyecciones_caba',
            'version_politica': VERSION_POLITICA_LIMPIEZA,
            'fuentes_consultadas_el': FECHA_CONSULTA_LIMPIEZA}


_ORDINARIAS = {'hojas', 'tierra_polvo', 'papel_carton', 'plastico'}


def _foco_limpieza_cotidiana(foco):
    if not isinstance(foco, dict) or foco.get('ubicacion') != 'vereda_frente_inmueble':
        return False
    if foco.get('material') == 'hojas' and foco.get('presentacion') in {'disperso', 'acumulado'}:
        return True
    return (foco.get('presentacion') == 'disperso' and foco.get('cantidad_relativa') == 'aislado'
            and foco.get('material') in _ORDINARIAS)


def _liviano_en_vereda(foco):
    if not isinstance(foco, dict) or foco.get('ubicacion') != 'vereda_frente_inmueble':
        return False
    if foco.get('material') == 'hojas' and foco.get('presentacion') in {'disperso', 'acumulado'}:
        return True
    return (foco.get('presentacion') == 'disperso'
            and foco.get('cantidad_relativa') in {'aislado', 'indeterminada'}
            and foco.get('material') in {'tierra_polvo', 'papel_carton', 'plastico'})


def _lecturas_limpieza_cotidiana(observaciones):
    """Hojas en la vereda frente al inmueble no exigen acuerdo de presentación
    ni que papel, plástico o polvo dispersos estén aislados. Una cantidad
    significativa de esos livianos deja la orientación indeterminada."""
    if not observaciones:
        return False, False, False
    cotidianos = True
    hojas_y_livianos = True
    extra = False
    for o in observaciones:
        materiales = o.get('materiales') or []
        if not materiales:
            return False, False, False
        cotidianos = cotidianos and all(_foco_limpieza_cotidiana(m) for m in materiales)
        tiene_hojas = any(m.get('material') == 'hojas' and _liviano_en_vereda(m) for m in materiales)
        hojas_y_livianos = hojas_y_livianos and tiene_hojas and all(_liviano_en_vereda(m) for m in materiales)
        extra = extra or any(m.get('material') in {'papel_carton', 'plastico', 'tierra_polvo'}
                             and _liviano_en_vereda(m) for m in materiales)
    return cotidianos, hojas_y_livianos, extra


def _bolsones(presente, estado, retiro='no_evaluado'):
    excluido = retiro == 'excluido_por_presentacion'
    return {'presente': presente, 'estado': estado, 'retiro_caba': retiro,
            'jurisdiccion': 'CABA', 'politica': 'bolsones_obra_caba',
            'servicio': 'retiro_escombros' if excluido else None,
            'motivo': 'presentacion_no_admitida' if excluido else None,
            'accion': 'consultar_servicio' if excluido else None,
            'indicacion': 'Consultá al 147 cómo gestionar el retiro del bolsón.' if excluido else None}


def ajustar_presentacion(publica):
    """Ajusta la acción informativa en la respuesta propia, sin alterar servicios."""
    bolsones = (publica.get('observaciones_higiene') or {}).get('bolsones') or {}
    revision = publica.get('verificacion_escombros')
    if bolsones.get('retiro_caba') != 'excluido_por_presentacion' or not isinstance(revision, dict):
        return
    foto = publica.get('evaluacion_foto') or {}
    contexto = publica.get('contexto_visual') or {}
    revision['requiere_nueva_foto'] = bool(
        foto.get('requiere_nueva_foto') is True
        or foto.get('requiere_foto_complementaria') is True
        or contexto.get('suficiente') is False)
    revision['requiere_cambio_presentacion'] = True


def normalizar(valor):
    if not isinstance(valor, dict):
        return None
    focos = valor.get('materiales')
    if not isinstance(focos, list) or len(focos) > 12:
        return None
    resultado = []
    lecturas = []
    parcial = False
    for indice, foco in enumerate(focos, 1):
        if not isinstance(foco, dict):
            return None
        if foco.get('ubicacion') == 'vereda':
            foco = dict(foco, ubicacion='indeterminada')
            parcial = True
        if (any(not isinstance(foco.get(k), str) for k in
                ('material', 'ubicacion', 'presentacion', 'cantidad_relativa'))
                or foco['material'] not in MATERIALES or foco['ubicacion'] not in UBICACIONES
                or foco['presentacion'] not in PRESENTACIONES
                or foco['cantidad_relativa'] not in {'aislado', 'significativa', 'indeterminada'}
                or not isinstance(foco.get('evidencia'), str) or not foco['evidencia'].strip()):
            return None
        r = {k: foco[k] for k in ('material', 'ubicacion', 'presentacion', 'cantidad_relativa')}
        evidencia = _limpiar_evidencia(foco['evidencia'])
        lecturas.append(dict(r, indice=indice, evidencia=evidencia[:EVIDENCIA_MAX],
                             evidencia_truncada=len(evidencia) > EVIDENCIA_MAX))
        if r not in resultado:
            resultado.append(r)
    bolson = valor.get('hay_bolson')
    ordinaria = valor.get('solo_limpieza_cotidiana_frente')
    if parcial:
        # Recuperar materiales no habilita decisiones que antes no se podían usar.
        return {'materiales': resultado, 'lecturas': lecturas, 'hay_bolson': None,
                'solo_limpieza_cotidiana_frente': None, 'normalizacion_parcial': True}
    return {'materiales': resultado, 'lecturas': lecturas, 'hay_bolson': bolson if type(bolson) is bool else None,
            'solo_limpieza_cotidiana_frente': ordinaria if type(ordinaria) is bool else None}


def _publicar_completas(verificadores, alcance=None, rechazada=False, problemas=()):
    if rechazada:
        return {'estado': 'no_aplica', 'materiales': [],
                'bolsones': _bolsones(None, 'no_aplica'),
                'orientacion_limpieza': _orientacion('no_aplica')}
    lectores = [v for v in verificadores if isinstance(v, dict)]
    completos = (len(lectores) >= 2 and len({v.get('modelo') for v in lectores if v.get('modelo')}) == len(lectores)
                 and all(v.get('ok') is True and isinstance(v.get('observaciones_higiene'), dict) for v in lectores))
    observaciones = [v['observaciones_higiene'] for v in lectores
                     if v.get('ok') is True and isinstance(v.get('observaciones_higiene'), dict)]
    agrupadas = {}
    for v in lectores:
        if v.get('ok') is not True or not v.get('modelo'):
            continue
        for foco in (v.get('observaciones_higiene') or {}).get('materiales', []):
            clave = tuple(foco[k] for k in ('material', 'ubicacion', 'presentacion'))
            grupo = agrupadas.setdefault(clave, {'fuentes': set(), 'cantidades': set()})
            grupo['fuentes'].add(v['modelo'])
            grupo['cantidades'].add(foco['cantidad_relativa'])
    materiales = []
    for (material, ubicacion, presentacion), g in sorted(agrupadas.items()):
        acuerdo = completos and len(g['fuentes']) == len(lectores)
        materiales.append({'material': material, 'ubicacion': ubicacion, 'presentacion': presentacion,
                           'cantidad_relativa': next(iter(g['cantidades'])) if acuerdo and len(g['cantidades']) == 1 else 'indeterminada',
                           'estado': 'corroborado' if acuerdo else 'pendiente', 'fuentes': len(g['fuentes'])})
    valores = [o.get('hay_bolson') for o in observaciones]
    bolson = valores[0] if completos and valores and type(valores[0]) is bool and all(x is valores[0] for x in valores) else None
    # El alcance ya ejecutado puede corroborar presentación aunque la pasada general no la evaluó.
    revisiones = (alcance or {}).get('revisiones') or []
    solo_bolson = (not (alcance or {}).get('fallo') and len(revisiones) >= 2
                  and len({v.get('modelo') for v in revisiones if v.get('modelo')}) == len(revisiones)
                  and all(v.get('presentacion') == 'solo_bolson' and v.get('ubicacion') == 'publica' for v in revisiones))
    conflicto_bolson = solo_bolson and bolson is False
    if solo_bolson:
        bolson = None if conflicto_bolson else True
    elegibilidad = 'excluido_por_presentacion' if solo_bolson and bolson is True and (alcance or {}).get('estado') == 'excluido' else 'no_evaluado'
    desacuerdo = completos and any(v is True for v in valores) and any(v is False for v in valores)
    estado_bolson = ('contradictorio' if conflicto_bolson or (bolson is None and desacuerdo) else
                     'corroborado' if bolson is not None else
                     'no_evaluado' if not observaciones and not revisiones else 'indeterminado')
    orientacion = _orientacion('indeterminada' if observaciones else 'no_evaluado')
    cotidianos, hojas_y_livianos, extra = _lecturas_limpieza_cotidiana(observaciones)
    if any(p.get('key') in {'recoleccion', 'retiro_poda', 'retiro_muebles', 'retiro_escombros'} for p in problemas):
        cotidianos = hojas_y_livianos = False
    # Un false explícito veta la escena de solo suciedad cotidiana. Hojas
    # corroboradas con livianos dispersos en la misma vereda no se apagan,
    # salvo que esos livianos vengan en cantidad significativa.
    solo_ok = all(o.get('solo_limpieza_cotidiana_frente') is not False for o in observaciones)
    if completos and ((hojas_y_livianos and extra) or (cotidianos and solo_ok)):
        orientacion.update(estado='orientacion_disponible', tarea='limpieza_cotidiana_vereda',
                          responsable_orientativo='frentista', fuente=FUENTE_LIMPIEZA,
                          indicacion='La limpieza cotidiana de la vereda corresponde al frentista. Barré desde el cordón hacia el frente, juntá los residuos y embolsalos. No los barras a la calzada.')
    excrementos = any(m['material'] == 'excrementos' and m['estado'] == 'corroborado'
                     and m['ubicacion'] not in {'interior', 'indeterminada'} for m in materiales)
    if excrementos:
        orientacion.update(estado='orientacion_disponible', tarea='recoger_deyecciones',
                          responsable_orientativo='responsable_del_animal', fuente=FUENTE_ANIMALES,
                          indicacion='Quien lleva al animal debe recoger sus deyecciones. La foto no identifica a esa persona ni determina otras responsabilidades.')
    return {'estado': 'evaluado' if completos else 'parcial' if observaciones else 'no_evaluado',
            'materiales': materiales,
            'bolsones': _bolsones(bolson, estado_bolson, elegibilidad),
            'orientacion_limpieza': orientacion}


def _detalle_lecturas(verificadores, rechazada):
    """Conserva descripciones individuales; la referencia no identifica un objeto (#56)."""
    if rechazada:
        return {'estado': 'no_aplica', 'lecturas': []}
    lecturas = []
    campos = ('material', 'ubicacion', 'presentacion', 'cantidad_relativa')
    for numero, v in enumerate(verificadores, 1):
        modelo = v.get('modelo')
        obs = v.get('observaciones_higiene')
        if v.get('ok') is not True or not isinstance(modelo, str) or not modelo.strip() or not isinstance(obs, dict):
            continue
        filas = obs.get('lecturas')
        if not isinstance(filas, list) or len(filas) > 12:
            continue
        materiales = obs.get('materiales')
        if not isinstance(materiales, list):
            continue
        for indice, fila in enumerate(filas, 1):
            if (not isinstance(fila, dict) or type(fila.get('indice')) is not int
                    or fila['indice'] != indice or not isinstance(fila.get('evidencia'), str)
                    or not fila['evidencia'].strip()
                    or any(not isinstance(fila.get(k), str) for k in campos)
                    or not any(isinstance(m, dict) and all(m.get(k) == fila.get(k) for k in campos)
                               for m in materiales)):
                continue
            evidencia = _limpiar_evidencia(fila['evidencia'])
            if not evidencia:
                continue
            lecturas.append({**{k: fila[k] for k in campos},
                             'referencia': f'v{numero}:m{indice}', 'modelo': modelo,
                             'estado': 'lectura_individual', 'origen': 'no_evaluado',
                             'evidencia': evidencia[:EVIDENCIA_MAX],
                             'evidencia_truncada': fila.get('evidencia_truncada') is True or len(evidencia) > EVIDENCIA_MAX,
                             'normalizacion_parcial': obs.get('normalizacion_parcial') is True})
    return {'estado': 'disponible' if lecturas else 'no_evaluado', 'lecturas': lecturas}


def publicar(verificadores, alcance=None, rechazada=False, problemas=()):
    """Recupera detalles parciales sin incorporarlos a las decisiones (#56)."""
    lectores = [v for v in verificadores if isinstance(v, dict)]
    es_parcial = lambda v: (isinstance(v.get('observaciones_higiene'), dict)
                           and v['observaciones_higiene'].get('normalizacion_parcial') is True)
    anteriores = [dict(v, observaciones_higiene=None) if es_parcial(v) else v for v in lectores]
    resultado = _publicar_completas(anteriores, alcance, rechazada, problemas)
    resultado['detalle_materiales'] = _detalle_lecturas(lectores, rechazada)
    parciales = [v for v in lectores if es_parcial(v) and v.get('ok') is True and v.get('modelo')]
    if rechazada or not parciales:
        return resultado
    # Una lectura parcial mantiene incompleto el consenso. Solo se amplía la
    # presentación de materiales, con fuentes únicas y cantidades indeterminadas.
    grupos = {}
    for v in lectores:
        if v.get('ok') is not True or not v.get('modelo'):
            continue
        for foco in (v.get('observaciones_higiene') or {}).get('materiales', []):
            clave = tuple(foco[k] for k in ('material', 'ubicacion', 'presentacion'))
            grupos.setdefault(clave, set()).add(v['modelo'])
    resultado.update(estado='parcial', normalizacion_parcial=True, materiales=[
        {'material': m, 'ubicacion': u, 'presentacion': p,
         'cantidad_relativa': 'indeterminada', 'estado': 'pendiente', 'fuentes': len(fuentes)}
        for (m, u, p), fuentes in sorted(grupos.items())])
    return resultado
