"""Perfiles inmutables y diagnóstico de cada análisis, sin llamadas externas."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from functools import wraps
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from types import MappingProxyType

NOMBRES = {'bajo': 'Económico', 'medio': 'Equilibrado', 'alto': 'Completo',
           'prueba': 'Completo (prueba)'}
IDENTIFICADOR = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+$')
_TOPE_PRUEBA = Decimal('0.01')
_DECISION_PRUEBA = 'prueba_decision'
ETAPAS_BASE_PRUEBA = (_DECISION_PRUEBA, 'prueba_inicio', 'prueba_retiros')
_ETAPAS_PRUEBA = {*ETAPAS_BASE_PRUEBA, 'prueba_contenedores',
                 'prueba_tipos', 'prueba_alcance', 'rama_desprendida', 'leer_chapa'}


class ConsultaSinSaldo(ValueError):
    """La consulta no se envió, por saldo insuficiente o pendiente."""


CONFIG_MODELOS_ALTO = (
    'VERIFICADORES_RESIDUOS', 'VERIFICADORES_CUBIERTA', 'VERIFICADORES_CORDON',
    'VERIFICADORES_VEREDA', 'VERIFICADORES_FRAGMENTOS', 'VERIFICADORES_PRESENCIA_CONTENEDOR',
    'MODELO_CONTENEDORES', 'PRIORIDAD_COMPARACION_MODELO',
)


def leer_modelos(variable, *, ausente=(), entorno=None):
    env = os.environ if entorno is None else entorno
    if variable not in env:
        return ausente
    modelos = tuple(m.strip() for m in env[variable].split(',') if m.strip())
    if any(not IDENTIFICADOR.fullmatch(m) for m in modelos):
        raise ValueError(f'Identificador de modelo inválido en {variable}')
    return tuple(dict.fromkeys(modelos))


def leer_modelo(variable):
    modelos = leer_modelos(variable)
    if len(modelos) > 1:
        raise ValueError(f'{variable} admite un solo modelo')
    return modelos[0] if modelos else ''


@dataclass(frozen=True)
class Perfil:
    modo: str
    verificadores: tuple
    arbitro: str
    arbitro_ve_foto: bool
    arbitro_confirma: bool
    especialista: bool
    version: str
    motivo: str | None = None
    reservas_prueba: MappingProxyType | None = None
    saldo_prueba: Decimal = _TOPE_PRUEBA

    def metadatos(self):
        return {'modo': self.modo, 'modo_version': self.version}

    def publico(self):
        return {'modo': self.modo, 'modo_version': self.version, 'nombre': NOMBRES[self.modo],
                'disponible': self.motivo is None, 'motivo': self.motivo}


def _leer_reservas_prueba(texto):
    valores = json.loads(texto, parse_float=Decimal)
    if not isinstance(valores, dict) or valores.keys() != _ETAPAS_PRUEBA:
        raise ValueError('Faltan reservas para el modo de prueba')
    chapas = valores['leer_chapa']
    if isinstance(chapas, dict):
        if not chapas or any(not IDENTIFICADOR.fullmatch(modelo) for modelo in chapas):
            raise ValueError('Lectores inválidos en las reservas de chapa')
        del valores['leer_chapa']
        valores.update({f'leer_chapa:{modelo}': importe for modelo, importe in chapas.items()})
    reservas = {etapa: Decimal(str(valor)) for etapa, valor in valores.items()}
    if any(not v.is_finite() or not 0 < v <= _TOPE_PRUEBA for v in reservas.values()):
        raise ValueError('Reserva fuera del rango de costo por foto')
    if sum(reservas[k] for k in ETAPAS_BASE_PRUEBA) > _TOPE_PRUEBA:
        raise ValueError('Las lecturas principales no caben en el presupuesto')
    return MappingProxyType(reservas)


def cargar(verificador, opciones_servidor, entorno=None):
    """Se ejecuta al iniciar. Las listas nuevas nunca heredan por error."""
    env = os.environ if entorno is None else entorno
    raiz = Path(__file__).parent
    archivos = sorted((raiz / 'prompts').rglob('*.txt')) + [
        raiz / n for n in ('verificador.py', 'politica_escombros.py',
                          'revision_escombros_publica.py', 'revision_omisiones.py', 'revision_cordon.py', 'revision_fragmentos.py', 'revision_vereda.py', 'modos_analisis.py',
                          'servidor.py', 'especialista_contenedores.py', 'patentes.py', 'categorias.json', 'evaluacion_foto.py', 'prioridad.py', 'observaciones_higiene.py',
                          'contexto_ocupacion.py', 'revision_ramas.py')]
    base = hashlib.sha256()
    for archivo in archivos:
        base.update(archivo.relative_to(raiz).as_posix().encode())
        base.update(archivo.read_bytes())
    # Solo opciones de ejecución, nunca credenciales ni datos de un pedido.
    opciones = {k: v for k, v in vars(verificador).items()
                if k.isupper() and type(v) in (bool, int, float, str, type(None))
                and not any(x in k for x in ('KEY', 'URL', 'ARBITRO'))
                and not k.startswith('_')}
    opciones.update({k: v for k, v in opciones_servidor.items()
                     if k.startswith(('FUSION_', 'CONTENEDORES_'))
                     and type(v) in (bool, int, float, str, type(None))})
    perfiles = {}
    for modo, cantidad in (('bajo', 1), ('medio', 2), ('alto', 3), ('prueba', 3)):
        nombre = 'VERIFICADORES_' + modo.upper()
        motivo = None
        reservas = None
        if nombre in env:
            modelos = tuple(m.strip() for m in env[nombre].split(','))
            if (len(modelos) != cantidad or len(set(modelos)) != cantidad
                    or any(not IDENTIFICADOR.fullmatch(m) for m in modelos)):
                motivo = 'configuracion_invalida'
        elif modo == 'alto':
            modelos = tuple(verificador.VERIFICADORES)
        else:
            modelos, motivo = (), 'no_configurado'
        if modo == 'prueba' and 'PRUEBA_RESERVAS_USD' in env:
            try:
                reservas = _leer_reservas_prueba(env['PRUEBA_RESERVAS_USD'])
            except (ArithmeticError, ValueError, TypeError):
                motivo = 'configuracion_invalida'
        arbitro = '' if modo in {'bajo', 'prueba'} else verificador.ARBITRO
        foto = bool(modo == 'alto' and verificador.ARBITRO_VE_FOTO)
        confirma = bool(modo == 'alto' and verificador.ARBITRO_CONFIRMA)
        especialista = bool(modo == 'alto' and opciones_servidor.get('CONTENEDORES_ESPECIALISTA'))
        efectivas = dict(opciones)
        if modo != 'alto':
            efectivas = {k: v for k, v in efectivas.items() if not k.startswith('CONTENEDORES_')}
        efectivas['OPENROUTER_MODELOS_MIN_1000'] = leer_modelos(
            'OPENROUTER_MODELOS_MIN_1000', entorno=env)
        efectivas['LECTORES_CHAPA'] = leer_modelos('LECTORES_CHAPA', entorno=env)
        if modo == 'alto':
            efectivas.update({k: leer_modelos(k, ausente=None, entorno=env)
                              for k in CONFIG_MODELOS_ALTO})
        if modo != 'bajo':
            efectivas['ARBITRO_VOTOS'] = verificador.ARBITRO_VOTOS
            efectivas['ARBITRO_SIN_RAZONAMIENTO'] = verificador.ARBITRO_SIN_RAZONAMIENTO
        datos = [base.hexdigest(), modo, modelos, arbitro, foto, confirma,
                 especialista, efectivas, motivo]
        if especialista:
            import especialista_contenedores as e
            datos.append(hashlib.sha256(Path(e.__file__).read_bytes()).hexdigest())
        if modo == 'prueba':
            datos.append(hashlib.sha256((raiz / 'prueba_completo.py').read_bytes()).hexdigest())
            if reservas is not None:
                datos.append({k: str(v.normalize()) for k, v in reservas.items()})
        version = hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        perfiles[modo] = Perfil(modo, modelos, arbitro, foto, confirma, especialista, version, motivo, reservas)
    return MappingProxyType(perfiles)


@dataclass
class Registro:
    perfil: Perfil
    pedido_consumo: str | None = None
    fallos: set = field(default_factory=set)
    omitidas: set = field(default_factory=set)
    pendientes: set = field(default_factory=set)
    llamadas: int = 0
    lock: object = field(default_factory=threading.Lock)
    costo: Decimal = Decimal(0)
    reservado: Decimal = Decimal(0)
    decision_pendiente: bool = True
    limites_costo: set = field(default_factory=set)


_actual = ContextVar('modo_analisis', default=None)


def perfil_actual():
    r = _actual.get()
    return r.perfil if r else None


def pedido_consumo_actual():
    r = _actual.get()
    return r.pedido_consumo if r else None


@contextmanager
def usar(perfil, *, pedido_consumo=None):
    token = _actual.set(Registro(perfil, pedido_consumo) if perfil else None)
    try:
        yield
    finally:
        _actual.reset(token)


def llamada():
    r = _actual.get()
    if r:
        with r.lock:
            r.llamadas += 1


def con_reservas():
    p = perfil_actual()
    return p is not None and p.reservas_prueba is not None


def _caben(r, etapas):
    if r.limites_costo - {'presupuesto_api_insuficiente'}:
        return False
    reservas = r.perfil.reservas_prueba
    decision = reservas[_DECISION_PRUEBA] if r.decision_pendiente and _DECISION_PRUEBA not in etapas else 0
    if all(e in reservas for e in etapas) and (
            r.costo + r.reservado + decision + sum(reservas[e] for e in etapas) <= r.perfil.saldo_prueba):
        return True
    r.limites_costo.add('presupuesto_api_insuficiente')
    return False


def _clave_reserva(reservas, etapa, modelo):
    if etapa == 'leer_chapa' and etapa not in reservas:
        return f'leer_chapa:{modelo}'
    return etapa


def caben_apoyos(*etapas, lectores_chapa=()):
    if not con_reservas():
        return True
    r = _actual.get()
    with r.lock:
        reservas = r.perfil.reservas_prueba
        chapas = tuple(_clave_reserva(reservas, 'leer_chapa', modelo) for modelo in lectores_chapa)
        return _caben(r, etapas + chapas)


def reservar_consulta(etapa, *, modelo=None):
    if not con_reservas():
        return None
    r = _actual.get()
    with r.lock:
        reservas = r.perfil.reservas_prueba
        clave = _clave_reserva(reservas, etapa, modelo)
        if not _caben(r, (clave,)):
            raise ConsultaSinSaldo('Saldo insuficiente o pendiente para la consulta')
        reserva = reservas[clave]
        r.reservado += reserva
        if etapa == _DECISION_PRUEBA:
            r.decision_pendiente = False
        return reserva


def liquidar_consulta(reserva, uso):
    if reserva is None:
        return
    r = _actual.get()
    importe = uso.get('cost') if isinstance(uso, dict) else None
    with r.lock:
        r.reservado -= reserva
        if type(importe) not in (int, float):
            r.limites_costo.add('costo_api_sin_conciliar')
            return
        monto = Decimal(str(importe))
        if not monto.is_finite() or monto < 0:
            r.limites_costo.add('costo_api_sin_conciliar')
            return
        r.costo += monto
        if monto > reserva:
            r.limites_costo.add('reserva_api_excedida')
        if r.costo > r.perfil.saldo_prueba:
            r.limites_costo.add('tope_api_excedido')


def fallo(etapa):
    r = _actual.get()
    if r:
        with r.lock:
            r.fallos.add(etapa)


def omitir(etapa, categorias=()):
    r = _actual.get()
    if r:
        with r.lock:
            r.omitidas.add(etapa)
            r.pendientes.update(categorias)


def minimo_fuentes(minimo, salida, categorias=()):
    """Omite comprobaciones imposibles; conserva la falta de corroboración."""
    def decorar(fn):
        @wraps(fn)
        def ejecutar(*args, **kwargs):
            r = _actual.get()
            if r and r.perfil.modo != 'alto' and len(set(r.perfil.verificadores)) < minimo:
                with r.lock:
                    r.omitidas.add(fn.__name__.removeprefix("validar_").lstrip("_"))
                    r.pendientes.update(categorias)
                return copy.deepcopy(salida)
            return fn(*args, **kwargs)
        return ejecutar
    return decorar


def identidad(datos, contexto, verificar, perfil):
    h = hashlib.sha256(datos)
    h.update(json.dumps([contexto, verificar, perfil.modo, perfil.version],
                        ensure_ascii=False, separators=(',', ':')).encode())
    return h.hexdigest()


def _fallos_en(valor):
    if isinstance(valor, dict):
        if valor.get('fallo') or valor.get('degradado') or valor.get('ok') is False:
            return True
        return any(_fallos_en(v) for v in valor.values())
    if isinstance(valor, (list, tuple)):
        return any(_fallos_en(v) for v in valor)
    return False


def completar(salida):
    r = _actual.get()
    if r is None:
        return salida
    p = r.perfil
    salida.update(p.metadatos())
    veri = (salida.get('detalle') or {}).get('verificacion') or {}
    limites = sorted(r.limites_costo)
    if p.modo != 'alto':
        limites.append('modo_reducido')
    if p.modo == 'prueba':
        limites.append('clasificacion_experimental')
    if not p.especialista:
        limites.append('especialista_desactivado')
    if p.modo == 'medio' and not p.arbitro:
        limites.append('arbitro_no_configurado')
    if r.omitidas:
        limites.append('corroboracion_insuficiente')
    if p.modo == 'bajo':
        # Una etapa omitida no puede ratificar su categoría por ausencia de votos.
        pendientes = r.pendientes
        for campo in ('problemas', 'elementos_detectados'):
            conservar = []
            for c in salida.get(campo) or []:
                if c.get('key') in pendientes:
                    if not any(x.get('key') == c['key'] for x in salida['posibles']):
                        salida['posibles'].append(dict(c, origen='foto',
                            motivo='Falta corroboración independiente para confirmar este hallazgo.'))
                else:
                    conservar.append(c)
            salida[campo] = conservar
        # La descripción usa únicamente el veredicto; no copia prosa sin corroborar.
        nombres = [c['nombre'] for c in salida.get('problemas') or []
                   if set(c.get('fuentes') or []) - {'modelo_local'}]
        salida['descripcion'] = ('Incidencias confirmadas: ' + ', '.join(nombres) + '.'
                                 if nombres else 'No se confirmaron problemas con la evidencia disponible.')
    # Las omisiones previstas no son fallas de proveedor.
    datos_fallos = {k: v for k, v in veri.items() if k not in r.omitidas}
    import verificador
    arbitraje_pendiente = p.arbitro and any(k not in verificador.PRESENCIA for k in salida.get('en_duda') or [])
    fallo_tecnico = bool(r.fallos or _fallos_en(datos_fallos))
    incompleto = bool(r.limites_costo or fallo_tecnico or veri.get('ruteo_contexto_fallo') or arbitraje_pendiente)
    if not veri.get('activa') and not r.llamadas:
        estado = 'sin_verificacion'
        limites.append('sin_verificacion')
    else:
        estado = 'parcial' if incompleto else 'completo'
    if fallo_tecnico:
        limites.append('etapa_fallida')
        if (p.modo == 'prueba' and veri.get('revision_ramas_fallo')
                and r.fallos <= {'rama_desprendida', 'respuesta_dirigida'}
                and not r.limites_costo and not _fallos_en(datos_fallos)
                and not veri.get('ruteo_contexto_fallo') and not arbitraje_pendiente):
            limites.append('solo_revision_ramas_pendiente')
    if arbitraje_pendiente:
        limites.append('revision_pendiente')
    if veri.get('ruteo_contexto_fallo'):
        limites.append('contexto_sin_encaminar')
    if (salida.get('posibles') or salida.get('en_duda')) and 'corroboracion_insuficiente' not in limites:
        limites.append('corroboracion_insuficiente')
    salida.update(analisis_estado=estado, analisis_limitaciones=limites)
    return salida
