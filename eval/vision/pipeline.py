#!/usr/bin/env python3
"""Evaluate the final API contract with recorded local and OpenRouter evidence."""
import argparse
import ast
import copy
import datetime as dt
import importlib.metadata
import importlib
import json
import os
import re
import sys
import types
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eval.vision import run as R
from eval.vision import dataset as D
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
    if record.get('identity') != identity or record.get('photo_sha256') != case['sha256']:
        raise R.NotAvailable('Local prediction provenance mismatch.')
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


def evaluate(case, private, identity, server, transport, allow_initial_live=False, initial_fixture=None):
    row = {'case': case['id'], 'aliases': case['aliases'], 'model': 'final-api', 'stage': 'pipeline',
           'tags': case['tags'], 'known_status': case['status'], 'expected': case['expected'],
           'label_hash': R.fingerprint(case['expected']), 'photo_sha256': case['sha256'],
           'context_hash': R.fingerprint(case.get('context', '')), 'photo': case['photo']}
    row.update(notes=case.get('notes', []), uncertain_labels=case.get('uncertain_labels', []),
               review_updates=case.get('review_updates', []))
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
            offline = R.Transport(private / 'cache', max_age_days=transport.max_age / 86400)
            offline.seen.update(transport.seen)
            for model in R.V.VERIFICADORES:
                initial = R.evaluate_case(case, model, private, offline, server.CATEGORIAS)
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
            row.update(status='budget' if 'BudgetStop' in kinds else 'uncached' if 'NotAvailable' in kinds else 'error',
                       error='Incomplete model evidence; final result is not scored.')
    except R.NotAvailable as exc:
        row.update(status='uncached', error=str(exc))
    except Exception as exc:
        row.update(status='error', error=str(exc)[:300])
    row['requests'] = transport.events[start:]
    row['cost_usd'] = float(transport.meter.spent - paid_before)
    row['response'] = {'source': 'live' if any(e.get('source') == 'live' for e in row['requests']) else 'cache'}
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['local', 'run'])
    parser.add_argument('--private', type=Path, default=R.DEFAULT_PRIVATE)
    parser.add_argument('--ids', nargs='+')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--allow-initial-live', action='store_true')
    parser.add_argument('--budget', type=float, default=0.5)
    parser.add_argument('--reserve', type=float, default=0.03)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--baseline', type=Path, help='Fail on new discrepancies against this recorded run')
    parser.add_argument('--initial-fixture', type=Path, help='Explicit recorded first-pass votes for one reported case')
    args = parser.parse_args(argv)
    R.load_env()
    if 'servidor' not in sys.modules:
        # Configuration constants are evaluated when verificador is imported.
        # Reload after .env so CLI checks use the server's actual settings.
        importlib.reload(R.V)
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    ids = args.ids or ([r['case'] for r in baseline['rows']] if baseline else TRIAGE)
    manifest = json.loads((args.private / 'cases.json').read_text())
    cases = R.select(manifest['cases'], 'all', ids=ids)
    unknown = set(ids) - {a for c in cases for a in c['aliases']}
    if unknown:
        parser.error('Unknown or unscorable case IDs: ' + ', '.join(sorted(unknown)))
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
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.out or args.private / 'runs' / ('pipeline-' + stamp + '.json')
    if output.exists():
        parser.error('Output exists; preserve the earlier report.')
    output.parent.mkdir(parents=True, exist_ok=True)
    server = load_server()
    transport = R.Transport(args.private / 'cache', live=args.live, budget=args.budget, reserve=args.reserve,
                            journal=output.with_suffix('.attempts.jsonl'))
    if baseline and not args.live:
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
              'rows': [], 'cost_usd': 0, 'budget_usd': args.budget, 'summary': {}}
    for case in cases:
        row = evaluate(case, args.private, identity, server, transport, args.allow_initial_live, fixture)
        report['rows'].append(row)
        report['cost_usd'] = float(transport.meter.spent)
        report['billing_uncertain'] = transport.meter.unresolved
        report['requests_made'] = transport.meter.calls
        report['summary'] = R.summarize(report['rows'])
        report['summary']['planned_model_cases'] = len(cases)
        report['summary']['complete'] &= len(report['rows']) == len(cases)
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
        return 2 if comparison['incomparable'] else 1 if comparison['regressions'] else 0
    return 2 if not report['summary']['complete'] else 1 if report['summary']['counts'].get('fail') else 0


if __name__ == '__main__':
    raise SystemExit(main())
