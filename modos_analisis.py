"""Perfiles inmutables y diagnóstico de cada análisis, sin llamadas externas."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from types import MappingProxyType

NOMBRES = {'bajo': 'Económico', 'medio': 'Equilibrado', 'alto': 'Completo'}
IDENTIFICADOR = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+$')


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

    def metadatos(self):
        return {'modo': self.modo, 'modo_version': self.version}

    def publico(self):
        return {'modo': self.modo, 'nombre': NOMBRES[self.modo],
                'disponible': self.motivo is None, 'motivo': self.motivo}


def cargar(verificador, opciones_servidor, entorno=None):
    """Se ejecuta al iniciar. Las listas nuevas nunca heredan por error."""
    env = os.environ if entorno is None else entorno
    raiz = Path(__file__).parent
    archivos = sorted((raiz / 'prompts').rglob('*.txt')) + [
        raiz / n for n in ('verificador.py', 'politica_escombros.py',
                          'revision_escombros_publica.py', 'modos_analisis.py',
                          'servidor.py', 'categorias.json')]
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
    for modo, cantidad in (('bajo', 1), ('medio', 2), ('alto', 3)):
        nombre = 'VERIFICADORES_' + modo.upper()
        motivo = None
        if nombre in env:
            modelos = tuple(m.strip() for m in env[nombre].split(','))
            if (len(modelos) != cantidad or len(set(modelos)) != cantidad
                    or any(not IDENTIFICADOR.fullmatch(m) for m in modelos)):
                motivo = 'configuracion_invalida'
        elif modo == 'alto':
            modelos = tuple(verificador.VERIFICADORES)
        else:
            modelos, motivo = (), 'no_configurado'
        arbitro = '' if modo == 'bajo' else verificador.ARBITRO
        foto = bool(modo == 'alto' and verificador.ARBITRO_VE_FOTO)
        confirma = bool(modo == 'alto' and verificador.ARBITRO_CONFIRMA)
        especialista = bool(modo == 'alto' and opciones_servidor.get('CONTENEDORES_ESPECIALISTA'))
        efectivas = dict(opciones)
        if modo != 'alto':
            efectivas = {k: v for k, v in efectivas.items() if not k.startswith('CONTENEDORES_')}
        if modo != 'bajo':
            efectivas['ARBITRO_VOTOS'] = verificador.ARBITRO_VOTOS
        datos = [base.hexdigest(), modo, modelos, arbitro, foto, confirma,
                 especialista, efectivas, motivo]
        if especialista:
            import especialista_contenedores as e
            datos.append(hashlib.sha256(Path(e.__file__).read_bytes()).hexdigest())
        version = hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        perfiles[modo] = Perfil(modo, modelos, arbitro, foto, confirma, especialista, version, motivo)
    return MappingProxyType(perfiles)


@dataclass
class Registro:
    perfil: Perfil
    fallos: set = field(default_factory=set)
    omitidas: set = field(default_factory=set)
    pendientes: set = field(default_factory=set)
    llamadas: int = 0
    lock: object = field(default_factory=threading.Lock)


_actual = ContextVar('modo_analisis', default=None)


def perfil_actual():
    r = _actual.get()
    return r.perfil if r else None


@contextmanager
def usar(perfil):
    token = _actual.set(Registro(perfil) if perfil else None)
    try:
        yield
    finally:
        _actual.reset(token)


def llamada():
    r = _actual.get()
    if r:
        with r.lock:
            r.llamadas += 1


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
    limites = []
    if p.modo != 'alto':
        limites.append('modo_reducido')
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
    incompleto = bool(fallo_tecnico or veri.get('ruteo_contexto_fallo') or arbitraje_pendiente)
    if not veri.get('activa') and not r.llamadas:
        estado = 'sin_verificacion'
        limites.append('sin_verificacion')
    else:
        estado = 'parcial' if incompleto else 'completo'
    if fallo_tecnico:
        limites.append('etapa_fallida')
    if arbitraje_pendiente:
        limites.append('revision_pendiente')
    if veri.get('ruteo_contexto_fallo'):
        limites.append('contexto_sin_encaminar')
    if (salida.get('posibles') or salida.get('en_duda')) and 'corroboracion_insuficiente' not in limites:
        limites.append('corroboracion_insuficiente')
    salida.update(analisis_estado=estado, analisis_limitaciones=limites)
    return salida
