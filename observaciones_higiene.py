"""Materiales, presentación y orientación informativa, sin vetar servicios (#55-57)."""
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
    for foco in focos:
        if not isinstance(foco, dict):
            return None
        if (foco.get('material') not in MATERIALES or foco.get('ubicacion') not in UBICACIONES
                or foco.get('presentacion') not in PRESENTACIONES
                or foco.get('cantidad_relativa') not in {'aislado', 'significativa', 'indeterminada'}
                or not isinstance(foco.get('evidencia'), str) or not foco['evidencia'].strip()):
            return None
        r = {k: foco[k] for k in ('material', 'ubicacion', 'presentacion', 'cantidad_relativa')}
        if r not in resultado:
            resultado.append(r)
    bolson = valor.get('hay_bolson')
    ordinaria = valor.get('solo_limpieza_cotidiana_frente')
    return {'materiales': resultado, 'hay_bolson': bolson if type(bolson) is bool else None,
            'solo_limpieza_cotidiana_frente': ordinaria if type(ordinaria) is bool else None}


def publicar(verificadores, alcance=None, rechazada=False, problemas=()):
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
    ordinarias = {'hojas', 'tierra_polvo', 'papel_carton', 'plastico'}
    cotidianos = bool(materiales) and all(m['estado'] == 'corroborado'
        and m['ubicacion'] == 'vereda_frente_inmueble'
        and ((m['material'] == 'hojas' and m['presentacion'] in {'disperso', 'acumulado'})
             or (m['presentacion'] == 'disperso' and m['cantidad_relativa'] == 'aislado'
                 and m['material'] in ordinarias)) for m in materiales)
    if any(p.get('key') in {'recoleccion', 'retiro_poda', 'retiro_muebles', 'retiro_escombros'} for p in problemas):
        cotidianos = False
    if completos and cotidianos and all(o.get('solo_limpieza_cotidiana_frente') is True for o in observaciones):
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
