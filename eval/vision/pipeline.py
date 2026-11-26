#!/usr/bin/env python3
"""Evaluate the final API contract with recorded local and OpenRouter evidence."""
import argparse
import ast
import copy
import datetime as dt
import importlib.metadata
import importlib
import json
import math
import os
import re
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stderr
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eval.vision import run as R
from eval.vision import dataset as D
from eval.vision import consumo as C
from eval.revision_lotes import regresiones
from PIL import Image

TRIAGE = ['green-TC03772', 'r4-T008', 'r4-T017', 'r4-T050', 'r4-T109',
          'r5-U022', 'r6-V049', 'sep-T011']


def local_identity():
    source = (ROOT / 'servidor.py').read_text()
    tree = ast.parse(source)
    names = {'dino_vec', 'siglip_vec', 'caracteristicas', '_liberar_transitorio',
             'clasificar_local', 'nombre_de', '_abrir_imagen'}
    functions = {n.name: ast.get_source_segment(source, n) for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name in names}
    packages = {}
    for name in ['torch', 'transformers', 'sentence-transformers', 'Pillow', 'scikit-learn', 'numpy']:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {'weights': D.digest((ROOT / 'model.joblib').read_bytes()), 'functions': R.fingerprint(functions),
            'packages': packages, 'threshold': os.environ.get('UMBRAL', '0.5'),
            'fold': R.V.FOLD}


def load_server(real_local=False):
    """Load actual server functions; replay mode makes inference impossible."""
    if 'servidor' in sys.modules:
        raise RuntimeError('Run pipeline evaluation in its own process.')
    if real_local:
        os.environ.setdefault('HF_HUB_OFFLINE', '1')
        os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
        os.environ.setdefault('OMP_NUM_THREADS', '2')
        import guard_modelo
        guard_modelo.adquirir_singleton(matar=False)
        import servidor
        return servidor
    class NoInference:
        classes_ = []
        def __init__(self, *args, **kwargs):
            pass
        def encode(self, *args, **kwargs):
            raise RuntimeError('Replay cannot run a placeholder local model.')
        predict_proba = encode
    joblib = types.ModuleType('joblib')
    joblib.load = lambda path: {'clf': NoInference(), 'classes': [], 'sev_model': None}
    encoders = types.ModuleType('sentence_transformers')
    encoders.SentenceTransformer = NoInference
    guard = types.ModuleType('guard_modelo')
    guard.adquirir_singleton = lambda *args, **kwargs: None
    replacements = {'joblib': joblib, 'sentence_transformers': encoders, 'guard_modelo': guard}
    originals = {name: sys.modules.get(name) for name in replacements}
    try:
        sys.modules.update(replacements)
        import servidor
    finally:
        for name, original in originals.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
    return servidor


def local_path(private, case, identity):
    return private / 'local' / (R.fingerprint({'photo': case['sha256'], 'identity': identity}) + '.json')


def read_local(private, case, identity):
    path = local_path(private, case, identity)
    if not path.is_file():
        raise R.NotAvailable('Missing current local prediction. Run pipeline.py local first.')
    record = json.loads(path.read_text())
    if (not isinstance(record, dict) or record.get('identity') != identity
            or record.get('photo_sha256') != case['sha256']):
        raise R.NotAvailable('Local prediction provenance mismatch.')
    if not isinstance(record.get('prediction'), dict):
        raise R.NotAvailable('La predicción local no contiene un resultado válido.')
    return record


def verified_photo(private, case):
    raw = (private / case['photo']).read_bytes()
    if D.digest(raw) != case['sha256']:
        raise ValueError('Photo SHA-256 mismatch.')
    return raw


def freeze_evidence(transport, baseline):
    """A code regression uses the reference's historical evidence, without TTL."""
    if baseline.get('kind') != 'pipeline' or not baseline.get('rows'):
        raise ValueError('Expected a nonempty pipeline baseline.')
    for row in baseline['rows']:
        if row['status'] not in {'pass', 'fail'}:
            raise ValueError('Cannot freeze a baseline with incomplete evidence.')
        for event in row.get('requests', []):
            if event.get('usable') and not event.get('error_type') and event.get('content'):
                transport.seen[event['request_hash']] = copy.deepcopy(event)


def contract_issues(public):
    issues = []
    problems = public.get('problemas') or []
    elements = public.get('elementos_detectados') or []
    if public.get('hay_problema') != bool(problems):
        issues.append('hay_problema disagrees with published problems')
    if public.get('hay_reclamo') != bool(problems or public.get('categorias_contexto')):
        issues.append('hay_reclamo disagrees with published findings')
    for field, rows in [('problemas', problems), ('elementos_detectados', elements)]:
        keys = [c.get('key') for c in rows]
        if len(keys) != len(set(keys)):
            issues.append('duplicate categories in ' + field)
    if set(c.get('key') for c in problems) & R.V.PRESENCIA:
        issues.append('container presence published as an incident')
    text = public.get('descripcion') or ''
    categories = json.loads((ROOT / 'categorias.json').read_text())
    if any(key in text for key in categories if '_' in key):
        issues.append('internal category key in final description')
    keys = {c.get('key') for c in problems}
    if 'retiro_escombros' in keys and R.V._niega_escombros(text):
        issues.append('description denies confirmed retiro_escombros')
    for key in sorted(R.V._trabajos_contenedor_descritos(text) - keys):
        issues.append('description recommends unconfirmed ' + key)
    return issues


def evaluation_row(case):
    return {'case': case['id'], 'aliases': case['aliases'], 'model': 'final-api', 'stage': 'pipeline',
           'tags': case['tags'], 'known_status': case['status'], 'expected': case['expected'],
           'label_hash': R.fingerprint(case['expected']), 'photo_sha256': case['sha256'],
           'context_hash': R.fingerprint(case.get('context', '')), 'photo': case['photo'],
           'notes': case.get('notes', []), 'uncertain_labels': case.get('uncertain_labels', []),
           'review_updates': case.get('review_updates', [])}


def _leer_respuesta_prueba(respuesta):
    datos = respuesta.json()
    if not respuesta.is_success:
        raise ValueError(f'HTTP {respuesta.status_code}: {datos.get("detail", "")}')
    return datos


def _esperar_trabajo_prueba(client, respuesta, *, medicion=None):
    estado = _leer_respuesta_prueba(respuesta)
    trabajo = estado.get('trabajo')
    pedido = (estado.get('consumo_api') or {}).get('pedido')
    if respuesta.status_code != 202 or not trabajo or not pedido:
        raise ValueError('La cola no devolvió un trabajo nuevo con su pedido de consumo.')
    if medicion is not None:
        medicion.guardar_respuesta(estado)
    limite = time.monotonic() + (medicion.espera if medicion is not None else 10)
    while estado['estado'] in ('en_cola', 'procesando') and time.monotonic() < limite:
        estado = _leer_respuesta_prueba(client.get('/trabajos/', params={'id': trabajo}))
        if estado['estado'] in ('en_cola', 'procesando'):
            time.sleep(.5 if medicion is not None else .02)
    if estado['estado'] != 'listo':
        raise ValueError(f"El trabajo no terminó listo ({estado['estado']}): {estado.get('detail', '')}")
    resultado = estado['resultado']
    if (resultado.get('consumo_api') or {}).get('pedido') != pedido:
        raise ValueError('El resultado no conserva el pedido de consumo del trabajo.')
    return resultado


def evaluate_prueba(case, private, identity, server, respuestas, *, ruta='clasificar', medicion=None):
    from fastapi.testclient import TestClient

    row = evaluation_row(case)
    fuente = 'live' if medicion is not None else 'archivo'
    row.update(cost_usd=0, requests=[], response={'source': fuente}, ruta_prueba=ruta)
    if 'recuento_esperado' in case:
        row.update(recuento_esperado=case['recuento_esperado'], recuento_observado=None,
                   diferencias_recuento=None)
    if case.get('principal_esperado') is not None:
        row.update(principal_esperado=case['principal_esperado'], principal_observado=None,
                   diferencia_principal=None)
    if 'contexto_esperado' in case:
        row.update(contexto_esperado=case['contexto_esperado'], contexto_observado=None,
                   diferencias_contexto=None)
    lock = threading.Lock()
    pedir_http = R.V._pedir_http

    def contestar(pedido, *tiempos):
        cuerpo = json.loads(pedido.data)
        huella = R.fingerprint({'endpoint': pedido.full_url, 'body': cuerpo})
        evento = {'request_hash': huella, 'model_requested': cuerpo['model']}
        if medicion is not None:
            costo_foto = R.V._costo_foto.get()
            evento.update(source=fuente, pedido=costo_foto['id'] if costo_foto else None)
            with lock:
                row['requests'].append(evento)
                medicion.anotar_intento(evento, 'iniciado')
            respuesta = pedir_http(pedido, *tiempos)
            with lock:
                evento['raw'] = respuesta
                medicion.anotar_intento(evento, 'recibido')
            return respuesta
        guardada = respuestas.get(huella)
        with lock:
            row['requests'].append(evento | {'source': 'archivo' if guardada is not None else 'missing',
                                             'raw': guardada})
        if guardada is None:
            raise FileNotFoundError('Falta la respuesta archivada de ' + huella)
        return copy.deepcopy(guardada)

    try:
        raw = verified_photo(private, case)
        local = read_local(private, case, identity)
        row['local_hash'] = R.fingerprint(local['prediction'])
        row.update(modo='prueba', modo_version=server.PERFILES['prueba'].version)
        headers = {'x-api-token': server.API_TOKEN} if server.API_TOKEN else {}
        with patch.object(server, 'clasificar_local', side_effect=lambda _: copy.deepcopy(local['prediction'])), \
                patch.multiple(server, CACHE_MAX=0, RATE_LIMITE=0), patch.object(server, '_hay_cuota', return_value=True), \
                patch.object(R.V, 'OPENROUTER_LOG_USO', medicion is not None), \
                patch.object(R.V, '_pedir_http', side_effect=contestar), ExitStack() as configuracion, \
                ThreadPoolExecutor(max_workers=1) as pool, patch.object(server, '_pool', pool), \
                TestClient(server.app, headers=headers) as client:
            if medicion is None:
                configuracion.enter_context(patch.object(R.V, 'api_key', return_value='reproduccion-sin-red'))
            # El pool termina antes de retirar el transporte de archivo, incluso si la consulta vence.
            endpoint = '/trabajos/' if ruta == 'trabajos' else '/clasificar'
            respuesta = client.post(endpoint, params={'verificar': '1'}, data={
                'modo': 'prueba', 'contexto': case.get('context', '')}, files={'file': ('foto.jpg', raw, 'image/jpeg')})
            public = (_esperar_trabajo_prueba(client, respuesta, medicion=medicion) if ruta == 'trabajos'
                      else _leer_respuesta_prueba(respuesta))
        row.update(prediction=public, modo=public.get('modo'), modo_version=public.get('modo_version'))
        if any(r['source'] == 'missing' for r in row['requests']):
            row.update(status='uncached', error='Faltan respuestas para las huellas indicadas.')
        elif (not row['requests'] or public.get('analisis_estado') != 'completo'
              or public.get('verificacion_activa') is False):
            row.update(status='error', error='El análisis publicado quedó incompleto.')
        else:
            row.update(R.assess(case, {'categorias': public['problemas'] + public['elementos_detectados']}))
            row['contract_issues'] = contract_issues(public)
            if 'recuento_esperado' in row:
                medidas = C.medidas_bolsas(public)
                row.update(recuento_observado=medidas,
                           diferencias_recuento=C.diferencias_recuento(row['recuento_esperado'], medidas))
            if 'principal_esperado' in row:
                principal = regresiones.observado(public, 'problema_principal')
                if principal is None:
                    row.update(status='error', error='Falta una decisión verificable del problema principal.')
                    return row
                row.update(principal_observado=principal, diferencia_principal=principal != row['principal_esperado'])
            if 'contexto_esperado' in row:
                observado, diferencias = C.comparar_contexto(row['contexto_esperado'], public)
                if diferencias is None:
                    row.update(status='error', error='Falta una respuesta evaluable del pedido vecinal.')
                    return row
                row.update(contexto_observado=observado, diferencias_contexto=diferencias)
            row['status'] = 'fail' if any(row.get(k) for k in (
                'missing', 'unexpected', 'contract_issues', 'diferencias_recuento', 'diferencia_principal',
                'diferencias_contexto')) else 'pass'
    except R.NotAvailable as error:
        row.update(status='uncached', error=str(error))
    except Exception as error:
        row.update(status='error', error=str(error)[:300])
    return row


class MedicionPrueba:
    def __init__(self, output, presupuesto, version, ruta, espera):
        self.uso = output.with_suffix('.uso.jsonl')
        self.intentos = output.with_suffix('.intentos.jsonl')
        self.manifiesto = output.with_suffix('.consumo.json')
        self.presupuesto = Decimal(str(presupuesto))
        self.version, self.ruta, self.espera = version, ruta, espera
        self.costo = Decimal(0)
        self.incierto, self.detener, self.consultas = False, False, 0
        self.datos = {'presupuesto_usd': str(self.presupuesto), 'casos': []}
        self._manifiesto_lock = threading.Lock()
        if any(p.exists() for p in (output.with_suffix('.html'), output.with_suffix('.comparison.json'),
                                   self.manifiesto, self.uso, self.intentos)):
            raise FileExistsError('La medición requiere archivos de salida nuevos.')
        # El manifiesto se reserva antes de enviar fotos, incluso si dos procesos eligen el mismo nombre.
        with self.manifiesto.open('x', encoding='utf-8') as archivo:
            json.dump(self.datos, archivo)
        self.uso.touch(exist_ok=False)
        self.intentos.touch(exist_ok=False)

    def admite_foto(self):
        return not self.detener and self.costo + C.TOPE <= self.presupuesto

    def guardar_respuesta(self, respuesta, *, provisional=False):
        with self._manifiesto_lock:
            if provisional and self.caso['respuestas'] != [None]:
                return
            self.caso['respuestas'] = [respuesta]
            D.write_json(self.manifiesto, self.datos)

    def anotar_intento(self, evento, estado):
        if estado == 'iniciado' and evento['pedido']:
            # Vinculá el gasto al caso antes de enviarlo. Un 202 de la cola
            # puede llegar en paralelo y conserva prioridad sobre esta traza.
            self.guardar_respuesta({'modo': 'prueba', 'modo_version': self.version,
                                    'consumo_api': {'pedido': evento['pedido'], 'intentos': None}},
                                   provisional=True)
        with self.intentos.open('a', encoding='utf-8') as archivo:
            archivo.write(R.canonical(evento | {'estado': estado, 'caso': self.caso['id']}) + '\n')

    def medir(self, case, private, identity, server):
        self.caso = {'id': case['id'], 'entrada': self.ruta, 'modo': 'prueba',
                     'modo_version': self.version, 'esperado': case['expected']}
        if 'recuento_esperado' in case:
            self.caso['recuento_esperado'] = case['recuento_esperado']
        if 'contexto_esperado' in case:
            self.caso['contexto_esperado'] = case['contexto_esperado']
        self.datos['casos'].append(self.caso)
        self.guardar_respuesta(None)
        with self.uso.open('a', encoding='utf-8') as archivo, redirect_stderr(archivo):
            row = evaluate_prueba(case, private, identity, server, None, ruta=self.ruta, medicion=self)
        public = row.get('prediction')
        if public is not None:
            self.guardar_respuesta(public)
        resumen, detalle = C.conciliar(self.datos['casos'], C.leer_uso([self.uso]))
        actual = detalle[-1]
        row['cost_usd'] = float(Decimal(actual['costo_conocido_usd']))
        row['consumo_facturado'] = actual
        self.costo = sum((Decimal(r['costo_conocido_usd']) for r in detalle), Decimal(0))
        self.consultas += len(row['requests'])
        limites = (public or {}).get('analisis_limitaciones', [])
        self.incierto = bool(resumen['casos_con_consumo_pendiente']) or (public or {}).get('costo_api_completo') is not True
        self.detener = (self.incierto or not row['requests'] or actual['supera_tope']
                        or 'reserva_api_excedida' in limites)
        return row


def load_prueba_responses(path, baseline):
    archivadas = {}

    def guardar(huella, respuesta):
        if not re.fullmatch(r'[0-9a-f]{64}', huella) or not isinstance(respuesta, dict):
            raise ValueError('Se espera el JSON original de cada respuesta con su huella SHA-256.')
        if huella in archivadas and archivadas[huella] != respuesta:
            raise ValueError('Una huella tiene respuestas distintas en los archivos.')
        archivadas[huella] = respuesta

    for row in (baseline or {}).get('rows', []):
        for evento in row['requests']:
            if evento.get('raw') is not None:
                guardar(evento['request_hash'], evento['raw'])
    if path:
        nuevas = json.loads(path.read_text())['respuestas']
        if not isinstance(nuevas, dict):
            raise ValueError('Se espera un objeto respuestas con los JSON originales por huella.')
        for huella, respuesta in nuevas.items():
            guardar(huella, respuesta)
    return archivadas


def evaluate(case, private, identity, server, transport, allow_initial_live=False, initial_fixture=None):
    row = evaluation_row(case)
    start = len(transport.events)
    paid_before = transport.meter.spent
    original_map = R.V._map_modelos

    def checked_map(models, fn):
        def checked_one(model):
            before = len(transport.events)
            try:
                return fn(model)
            except Exception as exc:
                # JSON can be syntactically valid yet fail the production
                # stage's schema. Keep the evidence, but never reuse it.
                with transport.lock:
                    for event in reversed(transport.events[before:]):
                        if event.get('model_requested') == model:
                            event['error_type'] = type(exc).__name__
                            if not isinstance(exc, R.CacheRejected):
                                key = event['request_hash']
                                (transport.cache / (key + '.json')).unlink(missing_ok=True)
                                transport.seen.pop(key, None)
                            break
                raise
        return original_map(models, checked_one)
    try:
        raw = verified_photo(private, case)
        local = read_local(private, case, identity)
        row['local_hash'] = R.fingerprint(local['prediction'])
        # First-pass cache misses stop before the pipeline can spend money on
        # follow-ups, unless a fresh initial reading was explicitly requested.
        if not allow_initial_live and initial_fixture is None:
            offline = R.Transport(transport.cache, max_age_days=transport.max_age / 86400,
                                  journal=transport.journal)
            offline.seen.update(transport.seen)
            for model in R.V.VERIFICADORES:
                initial = R.evaluate_case(case, model, private, offline, server.CATEGORIAS)
                if initial['status'] == 'cache_rejected':
                    transport.events.extend(e for e in offline.events if e.get('source') == 'cache_rejected')
                    raise R.CacheRejected(initial['error'])
                if initial['status'] not in {'pass', 'fail'}:
                    raise R.NotAvailable('Missing initial response for ' + model)
        with ExitStack() as stack, \
                patch.object(server, 'clasificar_local', side_effect=lambda img: copy.deepcopy(local['prediction'])), \
                patch.object(server, '_hay_cuota', return_value=True), \
                patch.object(R.V, 'disponible', return_value=True), patch.object(R.V, '_llamar', transport), \
                patch.object(R.V, '_map_modelos', checked_map):
            if initial_fixture is not None:
                if initial_fixture['case'] != case['id']:
                    raise ValueError('Initial fixture belongs to another photo.')
                votes = {v['modelo']: v for v in initial_fixture['models']}
                if set(votes) != set(R.V.VERIFICADORES) or any(not v.get('ok') for v in votes.values()):
                    raise ValueError('Initial fixture must contain all configured models.')
                row['initial_fixture_hash'] = R.fingerprint(initial_fixture)
                row['initial_source'] = initial_fixture['source']
                stack.enter_context(patch.object(R.V, '_verificar_uno',
                                    side_effect=lambda model, *args: copy.deepcopy(votes[model])))
            internal = server.procesar(raw, case.get('context', ''), '1')
            public = server._publica(internal)
        row.update(prediction=public, internal=internal)
        assessment = R.assess(case, {'categorias': public['problemas'] + public['elementos_detectados']})
        row.update(assessment)
        invariants = contract_issues(public)
        row['contract_issues'] = invariants
        row['status'] = 'fail' if assessment['missing'] or assessment['unexpected'] or invariants else 'pass'
        events = transport.events[start:]
        failures = [e for e in events if e.get('error_type')]
        # Scope review can deliberately leave rubble uncertain. That is a
        # completed verdict, whereas unanswered arbiter disputes are not.
        evidence_view = dict(internal, en_duda=internal['detalle']['verificacion'].get('en_duda', []))
        if failures or not server._cacheable(evidence_view):
            kinds = {e.get('error_type') for e in failures}
            rejected = [e for e in failures if e.get('error_type') == 'CacheRejected']
            if rejected:
                row.update(status='cache_rejected', error='; '.join(
                    f"Caché rechazada en {e['cache_path']}: {e['reason']}" for e in rejected))
            else:
                row.update(status='budget' if 'BudgetStop' in kinds else 'uncached' if 'NotAvailable' in kinds else 'error',
                           error='Incomplete model evidence; final result is not scored.')
    except R.CacheRejected as exc:
        row.update(status='cache_rejected', error=str(exc))
    except R.NotAvailable as exc:
        row.update(status='uncached', error=str(exc))
    except Exception as exc:
        row.update(status='error', error=str(exc)[:300])
    row['requests'] = transport.events[start:]
    row['cost_usd'] = float(transport.meter.spent - paid_before)
    row['response'] = {'source': 'cache_rejected' if row['status'] == 'cache_rejected' else
                       'live' if any(e.get('source') == 'live' for e in row['requests']) else 'cache'}
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['local', 'run'])
    parser.add_argument('--private', type=Path, default=R.DEFAULT_PRIVATE)
    parser.add_argument('--ids', nargs='+')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--allow-initial-live', action='store_true')
    parser.add_argument('--budget', type=float)
    parser.add_argument('--reserve', type=float, default=0.03)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--baseline', type=Path, help='Fail on new discrepancies against this recorded run')
    parser.add_argument('--initial-fixture', type=Path, help='Explicit recorded first-pass votes for one reported case')
    parser.add_argument('--modo', choices=['alto', 'prueba'], default='alto')
    parser.add_argument('--respuestas-prueba', type=Path, help='Respuestas de OpenRouter guardadas por huella exacta')
    parser.add_argument('--ruta-prueba', choices=['clasificar', 'trabajos'],
                        help='Ruta de Completo (prueba) a evaluar. Por defecto, clasificar.')
    args = parser.parse_args(argv)
    prueba = args.modo == 'prueba'
    facturada = prueba and args.live
    if prueba and (args.initial_fixture or args.allow_initial_live):
        parser.error('Completo (prueba) evalúa todas las etapas de la foto.')
    if facturada:
        if args.command != 'run' or not args.ids or args.budget is None or args.respuestas_prueba:
            parser.error('La medición facturada requiere run, --ids y --budget explícitos.')
        if not math.isfinite(args.budget) or Decimal(str(args.budget)) < C.TOPE:
            parser.error('El presupuesto de la medición debe cubrir al menos USD 0,01.')
        if args.out and args.out.suffix != '.json':
            parser.error('La salida de la medición debe terminar en .json.')
        os.umask(0o077)
    presupuesto = args.budget if args.budget is not None else 0.5
    if args.respuestas_prueba and not prueba:
        parser.error('--respuestas-prueba requiere --modo prueba.')
    if args.ruta_prueba and (not prueba or args.command != 'run'):
        parser.error('--ruta-prueba requiere run --modo prueba.')
    R.load_env()
    if 'servidor' not in sys.modules:
        # Configuration constants are evaluated when verificador is imported.
        # Reload after .env so CLI checks use the server's actual settings.
        importlib.reload(R.V)
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    if baseline and baseline.get('modo', 'alto') != args.modo:
        parser.error('La referencia corresponde a otro modo de análisis.')
    try:
        base_facturada = prueba and not facturada and (baseline or {}).get('evidence_policy') == 'lecturas-nuevas'
        respuestas = (load_prueba_responses(args.respuestas_prueba, None if base_facturada else baseline)
                      if prueba and not facturada else None)
        respuestas_por_caso = ({r['case']: load_prueba_responses(None, {'rows': [r]}) for r in baseline['rows']}
                               if base_facturada else {})
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.error(str(error))
    ids = args.ids or ([r['case'] for r in baseline['rows']] if baseline else TRIAGE)
    manifest = json.loads((args.private / 'cases.json').read_text())
    cases = R.select(manifest['cases'], 'all', ids=ids, incluir_campos_de_prueba=prueba)
    unknown = set(ids) - {a for c in cases for a in c['aliases']}
    if unknown:
        parser.error('Unknown or unscorable case IDs: ' + ', '.join(sorted(unknown)))
    if prueba:
        for caso in cases:
            try:
                C.validar_etiquetas_esperadas(caso['expected'])
                if 'contexto_esperado' in caso:
                    C.validar_contexto_esperado(caso['contexto_esperado'])
                if 'recuento_esperado' in caso:
                    C.diferencias_recuento(caso['recuento_esperado'], None)
            except ValueError as error:
                parser.error(f"{caso['id']}: {error}")
            principal = caso.get('principal_esperado')
            if principal is not None and (
                    not isinstance(principal, str) or principal not in regresiones.SERVICIOS | {'indeterminado'}
                    or caso['expected'].get(principal) is False):
                parser.error('principal_esperado debe ser un servicio confirmado o indeterminado.')
    if facturada and len({c['id'] for c in cases}) != len(cases):
        parser.error('El lote contiene IDs de caso repetidos.')
    if args.allow_initial_live and not args.live:
        parser.error('--allow-initial-live requires --live')
    fixture = (json.loads(args.initial_fixture.read_text()) if args.initial_fixture
               else (baseline or {}).get('initial_fixture'))
    if fixture is not None and (len(cases) != 1 or cases[0]['id'] != fixture.get('case')):
        parser.error('--initial-fixture requires exactly its matching --ids case')
    identity = local_identity()
    if args.command == 'local':
        needed = [c for c in cases if not local_path(args.private, c, identity).is_file()]
        if not needed:
            print('All local predictions are current; no model loaded.')
            return 0
        server = load_server(real_local=True)
        for case in needed:
            image = server._abrir_imagen(verified_photo(args.private, case))
            prediction = server.clasificar_local(image)
            D.write_json(local_path(args.private, case, identity), {
                'identity': identity, 'photo_sha256': case['sha256'], 'prediction': prediction})
            print(case['id'] + ': local prediction recorded', flush=True)
        return 0
    if args.live and not R.V.api_key():
        parser.error('Missing OPENROUTER_API_KEY')
    if facturada:
        for caso in cases:
            try:
                verified_photo(args.private, caso)
                read_local(args.private, caso, identity)
            except (R.NotAvailable, ValueError, OSError) as error:
                parser.error(f"{caso['id']}: {error}")
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.out or args.private / 'runs' / ('pipeline-' + stamp + '.json')
    if output.exists():
        parser.error('Output exists; preserve the earlier report.')
    output.parent.mkdir(parents=True, exist_ok=True)
    server = load_server()
    transport = (R.Transport(args.private / 'cache', live=args.live, budget=presupuesto, reserve=args.reserve,
                             journal=output.with_suffix('.attempts.jsonl')) if not facturada else None)
    if baseline and not args.live and not prueba:
        freeze_evidence(transport, baseline)
    code = {p: D.digest((ROOT / p).read_bytes()) for p in ['servidor.py', 'verificador.py', 'politica_escombros.py']}
    code['politica_escombros.py'] = D.digest(Path(server.politica_escombros.__file__).read_bytes())
    report = {'version': 1, 'kind': 'pipeline', 'created_at': stamp, 'suite': 'triage',
              'evaluator_hash': R.fingerprint({p: D.digest((ROOT / p).read_bytes())
                                 for p in ('eval/vision/pipeline.py', 'eval/vision/run.py')}),
              'private_root': str(args.private.resolve()), 'local_identity': identity,
              'code': code, 'models': list(R.V.VERIFICADORES), 'arbiter': R.V.ARBITRO,
              'initial_fixture': fixture, 'evidence_policy': 'baseline-frozen' if baseline and not args.live else 'request-cache',
              'configuration': {name: getattr(server, name) for name in (
                  'FUSION_ESCOMBROS', 'FUSION_ESCOMBROS_UMBRAL', 'FUSION_ESCOMBROS_RECO_BAJA',
                  'FUSION_ESCOMBROS_UMBRAL_RESCATE', 'FUSION_ESCOMBROS_RECO_RESCATE')},
              'rows': [], 'cost_usd': 0, 'budget_usd': presupuesto, 'summary': {}}
    report['modo'] = args.modo
    medicion = None
    if prueba:
        perfil = server.PERFILES['prueba']
        report.update(modo_version=perfil.version, models=list(perfil.verificadores), arbiter=perfil.arbitro,
                      evidence_policy='lecturas-nuevas' if facturada else 'archivo-por-huella',
                      budget_usd=presupuesto if facturada else 0,
                      ruta_prueba=args.ruta_prueba or 'clasificar')
        if facturada:
            if perfil.motivo or not getattr(perfil, 'reservas_prueba', None):
                parser.error('La medición requiere un perfil disponible con PRUEBA_RESERVAS_USD.')
            try:
                medicion = MedicionPrueba(output, presupuesto, perfil.version, report['ruta_prueba'],
                                           server.TRABAJO_ESPERA + server.TECHO_TRABAJO + 30)
            except OSError as error:
                parser.error(str(error))
    for case in cases:
        if medicion is not None:
            if not medicion.admite_foto():
                break
            try:
                row = medicion.medir(case, args.private, identity, server)
            except (ValueError, OSError) as error:
                print('No se pudo conciliar la medición. ' + str(error), file=sys.stderr)
                return 2
        elif prueba:
            evidencia = respuestas | respuestas_por_caso.get(case['id'], {})
            row = evaluate_prueba(case, args.private, identity, server, evidencia, ruta=report['ruta_prueba'])
        else:
            row = evaluate(case, args.private, identity, server, transport, args.allow_initial_live, fixture)
        report['rows'].append(row)
        report['cost_usd'] = float(medicion.costo if medicion is not None else transport.meter.spent)
        report['billing_uncertain'] = medicion.incierto if medicion is not None else transport.meter.unresolved
        report['requests_made'] = medicion.consultas if medicion is not None else transport.meter.calls
        report['summary'] = R.summarize(report['rows'])
        report['summary']['planned_model_cases'] = len(cases)
        report['summary']['complete'] &= len(report['rows']) == len(cases)
        if medicion is not None:
            report['casos_sin_procesar'] = [c['id'] for c in cases[len(report['rows']):]]
            report['summary']['complete'] &= not medicion.detener
        R.write_report(output, report)
        print(f'{case["id"]}: {row["status"]}; spent US${report["cost_usd"]:.6f}', flush=True)
    print('Report: ' + str(output))
    if args.baseline:
        comparison = R.compare(baseline, report)
        report['comparison'] = comparison
        report['baseline'] = str(args.baseline)
        R.write_report(output, report)
        D.write_json(output.with_suffix('.comparison.json'), comparison)
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
        if medicion is not None and (medicion.detener or report['casos_sin_procesar']):
            return 2
        return 2 if comparison['incomparable'] else 1 if comparison['regressions'] else 0
    return 2 if not report['summary']['complete'] else 1 if report['summary']['counts'].get('fail') else 0


if __name__ == '__main__':
    raise SystemExit(main())
