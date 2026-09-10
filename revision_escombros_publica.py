"""Diagnóstico público de alcance: observaciones y efectos, sin inferencia."""
import copy
import re


ENUMS = {
    'ubicacion': {'publica', 'privada', 'indeterminada'},
    'presentacion': {'bolsas_chicas_o_suelto', 'solo_bolson', 'sin_pila', 'indeterminada'},
    'material': {'escombros_visible', 'oculto_o_ambiguo', 'incompatible_visible', 'indeterminado'},
    'hay_bolsas_opacas_o_parciales': {'si', 'no', 'indeterminado'},
    'afirmacion_vecinal': {'afirma', 'duda', 'niega', 'no_menciona'},
    'residuos_comunes_independientes': {'si', 'no', 'indeterminado'},
}
EVIDENCIAS = ('evidencia_ubicacion', 'evidencia_presentacion', 'evidencia_material')
COLECCIONES = ('problemas', 'posibles', 'categorias_contexto', 'descartados_por_foto',
               'elementos_detectados', 'en_duda')
REGLAS = {
    'escombros_visibles_sin_corroborar': 'Una observación de restos de obra conserva esa alternativa pendiente; falta corroboración del material.',
    'recoleccion_pendiente_por_material': 'La recolección de esas bolsas queda pendiente al no haber residuos comunes independientes corroborados.',
    'conservar_basura_publica': 'Se conserva la recolección de residuos comunes corroborados.',
    'material_contradictorio': 'Las observaciones del material se contradicen; no se confirma escombros.',
    'alcance_excluido': 'La ubicación o presentación queda fuera del alcance del retiro.',
    'alcance_indeterminado': 'No se pudo confirmar la ubicación y presentación necesarias para el retiro.',
    'contexto_sin_respaldo': 'El contexto no tiene el respaldo necesario para confirmar escombros.',
    'escombros_por_contexto': 'El contexto validado sostiene el retiro; no demuestra por sí solo el material visible.',
    'retirar_servicios_sin_residuos_independientes': 'Se retiran servicios sin residuos independientes corroborados.',
    'material_publico_disputado': 'La ubicación y presentación son aptas, pero el material está disputado; la elección del servicio queda pendiente.',
    'sin_cambios': 'La revisión no cambió las categorías.',
}
AJUSTES = {
    'presentacion_incompatible_con_bolsas_opacas': ('presentacion', 'sin_pila', {'indeterminada'}),
    'material_incompatible_sin_descartar_bolsas_opacas': (
        'material', 'incompatible_visible', {'oculto_o_ambiguo', 'indeterminado'}),
}
_RESERVADO = re.compile(
    r'https?://|file://|www\.|\bsk[-_]|bearer\b|authorization|api[_ -]?key|'
    r'password|contraseñ|credential|credencial|prompt|system|assistant|developer|'
    r'\btoken\b|traceback|\b(?:modelo|verificador|árbitro|arbitro|score)s?\b|'
    r'ignor[aeá].{0,25}instruccion|/Users/|/home/|/private/|'
    r'\b[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b\d[\d .()+-]{6,}\d\b|'
    r'\b\S+\.(?:jpe?g|png|webp|env|py|json)\b|[{}<>]', re.I)


def evidencia_publica(texto, contexto='', limite=160, sanear=None):
    """Omite prosa reservada, sin sustituirla por otra observación."""
    if not isinstance(texto, str) or _RESERVADO.search(texto):
        return None
    limpio = re.sub(r'\s+', ' ', ''.join(c for c in texto if c >= ' ')).strip()
    if not limpio or (sanear is not None and sanear(limpio) != limpio):
        return None
    # Una comparación de palabras no permite separar con certeza una
    # observación visual de una cita parcial o paráfrasis del vecino.
    # Con contexto libre solo se publican los valores estructurados.
    if contexto and contexto.strip():
        return None
    return limpio[:limite]


def capturar_respuesta(respuesta, contexto='', limite=160):
    """Copia acotada: el voto de negocio no se modifica."""
    salida = {k: respuesta[k] for k in ENUMS}
    salida['afirma_validada'] = respuesta['afirma_validada']
    salida['otros_retiros_independientes'] = list(respuesta['otros_retiros_independientes'])
    salida.update({k: evidencia_publica(respuesta.get(k), contexto, limite) for k in EVIDENCIAS})
    return salida


def _visible(entrada):
    fuentes = entrada.get('fuentes') or []
    return isinstance(fuentes, list) and bool(set(fuentes) - {'modelo_local'})


def instantanea(salida):
    """Proyección de ubicaciones de categorías, con visibilidad pública."""
    resultado = {}
    for campo in COLECCIONES[:-1]:
        for c in salida.get(campo) or []:
            if not isinstance(c, dict):
                continue
            # categorias_contexto conserva el contrato público del servidor.
            if campo != 'categorias_contexto' and not _visible(c):
                continue
            key = c.get('key')
            if key:
                resultado.setdefault(key, []).append(campo)
    fuentes = {c.get('key'): c.get('fuentes') or []
               for c in (salida.get('posibles') or []) + (salida.get('problemas') or [])}
    fuentes.update((salida.get('detalle', {}).get('verificacion') or {}).get('fuentes_en_duda') or {})
    for key in salida.get('en_duda') or []:
        if set(fuentes.get(key) or []) - {'modelo_local'}:
            resultado.setdefault(key, []).append('en_duda')
    return {k: list(dict.fromkeys(v)) for k, v in resultado.items()}


class Decision:
    """Registra únicamente ramas ejecutadas dentro de esta etapa."""

    def __init__(self, salida):
        self.antes = instantanea(salida)
        self.reglas = []
        self.por_categoria = {}

    def anotar(self, regla, categorias):
        if regla not in self.reglas:
            self.reglas.append(regla)
        for key in categorias:
            reglas = self.por_categoria.setdefault(key, [])
            if regla not in reglas:
                reglas.append(regla)

    def terminar(self, salida):
        despues = instantanea(salida)
        reglas = self.reglas or ['sin_cambios']
        efectos = [{'key': key, 'antes': self.antes.get(key, []),
                    'despues': despues.get(key, []), 'reglas': self.por_categoria.get(key, [])}
                   for key in sorted(self.antes.keys() | despues.keys())
                   if self.antes.get(key, []) != despues.get(key, [])]
        return {'etapa': 'alcance_escombros', 'reglas': reglas,
                'motivo': ' '.join(REGLAS[r] for r in reglas), 'efectos': efectos}


def _respuesta_publica(r, textos, sanear, limite):
    if not isinstance(r, dict) or any(not isinstance(r.get(k), str) or r[k] not in v
                                      for k, v in ENUMS.items()):
        return None
    otros = r.get('otros_retiros_independientes')
    if (not isinstance(otros, list) or any(k not in ('retiro_muebles', 'retiro_poda') for k in otros)
            or not isinstance(r.get('afirma_validada'), bool)):
        return None
    salida = {k: r[k] for k in ENUMS}
    salida.update(afirma_validada=r['afirma_validada'], otros_retiros_independientes=list(dict.fromkeys(otros)))
    salida.update({k: evidencia_publica(r.get(k), limite=limite, sanear=sanear)
                   if textos else None for k in EVIDENCIAS})
    return salida


def _ajustes_publicos(ajustes):
    if not isinstance(ajustes, list):
        return None
    salida = []
    for a in ajustes:
        if not isinstance(a, dict) or a.get('motivo') not in AJUSTES:
            return None
        campo, original, aplicados = AJUSTES[a['motivo']]
        if (a.get('campo') != campo or a.get('valor_original') != original
                or a.get('valor_aplicado') not in aplicados):
            return None
        salida.append({k: a[k] for k in ('campo', 'valor_original', 'valor_aplicado', 'motivo')})
    return salida


def _decision_publica(decision):
    if not isinstance(decision, dict) or decision.get('etapa') != 'alcance_escombros':
        return None
    reglas = decision.get('reglas')
    if not isinstance(reglas, list) or not reglas or any(r not in REGLAS for r in reglas):
        return None
    efectos = []
    for e in decision.get('efectos') or []:
        if (not isinstance(e, dict) or not isinstance(e.get('key'), str)
                or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', e['key'])):
            return None
        if any(not isinstance(e.get(k), list) or any(v not in COLECCIONES for v in e[k])
               for k in ('antes', 'despues')):
            return None
        if not isinstance(e.get('reglas'), list) or any(r not in reglas for r in e['reglas']):
            return None
        efectos.append({k: copy.deepcopy(e[k]) for k in ('key', 'antes', 'despues', 'reglas')})
    return {'etapa': 'alcance_escombros', 'reglas': list(reglas),
            'motivo': ' '.join(REGLAS[r] for r in reglas), 'efectos': efectos}


def _publicar(revision=None, decision=None, sanear=None, limite=160):
    revision = revision or {}
    registro = revision.get('registro_publico') or {}
    completo = registro.get('version') == 1 and isinstance(registro.get('participantes'), list)
    filas = registro['participantes'] if completo else [
        {'modelo': r.get('modelo'), 'estado': 'ok', 'respuesta': r, 'ajustes': None}
        for r in revision.get('revisiones') or [] if isinstance(r, dict)]
    salida, vistos = [], set()
    for fila in filas:
        modelo = fila.get('modelo') if isinstance(fila, dict) else None
        if not isinstance(modelo, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./:-]{0,159}', modelo):
            completo = False
            continue
        if '://' in modelo or modelo.lower().startswith('sk-'):
            completo = False
            continue
        if modelo in vistos:
            completo = False
            continue
        vistos.add(modelo)
        ok = fila.get('estado') == 'ok'
        respuesta = _respuesta_publica(fila.get('respuesta'), registro.get('version') == 1, sanear, limite) if ok else None
        ajustes = _ajustes_publicos(fila.get('ajustes')) if completo else None
        if ok and ajustes is None:
            completo = False
        if ok and respuesta is None:
            completo = False
            continue
        salida.append({'modelo': modelo, 'estado': 'ok' if ok else 'sin_respuesta_valida',
                       'respuesta': respuesta, 'ajustes': ajustes if ok else []})
    validas = sum(r['estado'] == 'ok' for r in salida)
    estado = ('no_disponible' if not completo or not salida else
              'completo' if validas == len(salida) else 'parcial' if validas else 'sin_respuestas')
    return {'detalle_version': 1, 'detalle_estado': estado, 'revisiones': salida,
            'decision': _decision_publica(decision)}


def publicar(revision=None, decision=None, sanear=None, limite=160):
    """Un diagnóstico antiguo o incompleto no debe romper la clasificación."""
    try:
        return _publicar(revision, decision, sanear, limite)
    except (TypeError, ValueError, KeyError, AttributeError):
        return {'detalle_version': 1, 'detalle_estado': 'no_disponible',
                'revisiones': [], 'decision': None}


def completar_historico(resultado):
    """Un trabajo antiguo puede conservar solo el JSON público."""
    resumen = resultado.get('verificacion_escombros')
    if isinstance(resumen, dict) and 'detalle_version' not in resumen:
        return dict(resultado, verificacion_escombros=dict(resumen, **publicar()))
    return resultado
