#!/usr/bin/env python3
"""Budgeted OpenRouter vision evaluation with exact-request response reuse."""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import sys
import time
import threading
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))


def load_env():
    # Match the server's configuration loading, with the project's API key
    # taking precedence over unrelated credentials in the parent process.
    env = ROOT / '.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                key, value = key.strip(), value.strip()
                if key == 'OPENROUTER_API_KEY':
                    os.environ[key] = value
                else:
                    os.environ.setdefault(key, value)


if __name__ == '__main__':
    load_env()

from eval.vision import dataset as D
import verificador as V
from PIL import Image, ImageOps

DEFAULT_MODELS = tuple(V.VERIFICADORES)
DEFAULT_PRIVATE = HERE / 'private'


class NotAvailable(RuntimeError):
    pass


class BudgetStop(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def request_body(model, messages, max_tokens):
    # Match the production transport's semantic generation parameters.
    body = {'model': model, 'messages': messages, 'max_tokens': max_tokens,
            'usage': {'include': True}, 'reasoning': {'effort': 'low'},
            'temperature': V.TEMPERATURA, 'top_p': 1}
    if V.SEMILLA is not None:
        body['seed'] = V.SEMILLA
    if V.PROVEEDOR_FIJO:
        body['provider'] = {'allow_fallbacks': False}
    if V.OPENROUTER_CACHE_PROMPTS:
        key = V._clave_cache_prompt(model, messages)
        if key:
            body['prompt_cache_key'] = key
    return body


class Meter:
    """Sequential admission limit. Unknown billing stops further requests."""
    def __init__(self, budget, reserve):
        self.limit, self.reserve = Decimal(str(budget)), Decimal(str(reserve))
        if not self.limit.is_finite() or not self.reserve.is_finite() or self.limit < 0 or self.reserve <= 0:
            raise ValueError('Budget must be finite and nonnegative; reserve must be positive.')
        self.spent = Decimal('0')
        self.unresolved = False
        self.calls = 0

    def admit(self):
        if self.unresolved or self.spent + self.reserve > self.limit:
            raise BudgetStop('Remaining budget cannot cover the next request reserve.')
        self.calls += 1

    def settle(self, usage):
        try:
            cost = Decimal(str((usage or {}).get('cost')))
            if not cost.is_finite() or cost < 0:
                raise ValueError('invalid cost')
        except (ValueError, ArithmeticError):
            self.spent += self.reserve
            self.unresolved = True
            return
        self.spent += cost
        if cost > self.reserve:
            # An unexpectedly expensive response requires a new explicit run.
            self.unresolved = True


class Transport:
    def __init__(self, cache, live=False, fresh=False, budget=0.5, reserve=0.03,
                 max_age_days=7, timeout=90, journal=None, opener=None):
        self.cache = Path(cache)
        self.live, self.fresh = live, fresh
        self.max_age = max_age_days * 86400
        self.timeout, self.journal = timeout, journal
        self.meter = Meter(budget, reserve)
        self.opener = opener or urllib.request.urlopen
        self.last = None
        self.blocked = None
        self.seen = {}
        self.events = []
        self.lock = threading.RLock()

    def record_event(self, event):
        if self.journal:
            with Path(self.journal).open('a') as stream:
                stream.write(canonical(event) + '\n')

    def __call__(self, model, messages, max_tokens=6000, intentos=3, *, etapa='sin_etapa'):
        # The production pipeline fans out across models. Serialize paid calls
        # so admission, billing and per-request provenance remain atomic.
        with self.lock:
            try:
                return self._call(model, messages, max_tokens, intentos, etapa=etapa)
            except Exception as exc:
                if self.last is not None:
                    self.last['error_type'] = type(exc).__name__
                raise
            finally:
                if self.last is not None:
                    self.events.append(dict(self.last))

    def _call(self, model, messages, max_tokens=6000, intentos=3, *, etapa='sin_etapa'):
        self.blocked = None
        body = request_body(model, messages, max_tokens)
        key = fingerprint({'endpoint': V.OPENROUTER_URL, 'body': body})
        path = self.cache / (key + '.json')
        self.last = {'request_hash': key, 'source': 'missing', 'stage': etapa}
        cached = None if self.fresh else self.seen.get(key)
        if not self.fresh and cached is None and path.is_file():
            try:
                candidate = json.loads(path.read_text())
            except (ValueError, OSError):
                candidate = {}
            if (candidate.get('request_hash') == key and candidate.get('usable')
                    and 0 <= time.time() - candidate.get('created_at', 0) <= self.max_age):
                cached = candidate
        if cached is not None:
            self.last = dict(cached, source='cache', billed_this_run=0)
            return cached['content']
        if not self.live:
            self.blocked = NotAvailable('No current response for this exact request. Use --live to call OpenRouter.')
            raise self.blocked
        try:
            self.meter.admit()
        except BudgetStop as exc:
            self.blocked = exc
            raise
        self.record_event({'state': 'started', 'request_hash': key, 'model': model,
                           'reserve_usd': float(self.meter.reserve), 'time': time.time()})
        started = time.monotonic()
        req = urllib.request.Request(V.OPENROUTER_URL, data=canonical(body).encode(), headers={
            'Authorization': 'Bearer ' + V.api_key(), 'Content-Type': 'application/json'})
        try:
            with self.opener(req, timeout=self.timeout) as response:
                raw = json.loads(response.read())
        except Exception:
            self.meter.settle(None)
            self.last.update(source='live_error', cost_unknown=True)
            self.record_event(dict(self.last, state='billing_unknown'))
            raise
        usage = raw.get('usage') or {}
        self.meter.settle(usage)
        V._costo_sumar(usage)
        record = {'request_hash': key, 'created_at': time.time(), 'stage': etapa,
                  'model_requested': model, 'model_returned': raw.get('model'),
                  'provider': raw.get('provider'), 'usage': usage, 'response_id': raw.get('id'),
                  'latency_s': round(time.monotonic() - started, 3), 'raw': raw, 'usable': False}
        self.last = dict(record, source='live', billed_this_run=usage.get('cost'))
        self.record_event(dict(self.last, state='received'))
        choices = raw.get('choices') or []
        choice = choices[0] if choices else {}
        content = (choice.get('message') or {}).get('content')
        if choice.get('finish_reason') in {'length', 'content_filter', 'error'} or not isinstance(content, str):
            raise ValueError('Incomplete response; billed but not reusable.')
        parsed = V._extraer_json(content)
        if not isinstance(parsed, dict):
            raise ValueError('Expected a JSON object; billed but not reusable.')
        if etapa == 'verificar_uno' and not isinstance(parsed.get('categorias'), list):
            raise ValueError('Missing categorias array; billed but not reusable.')
        if etapa == 'repregunta_objeto' and parsed.get('veredicto') not in {'presente', 'ausente', 'no_se_distingue'}:
            raise ValueError('Invalid presence verdict; billed but not reusable.')
        record.update(content=content, usable=True)
        D.write_json(path, record)
        self.seen[key] = record
        self.last = dict(record, source='live', billed_this_run=usage.get('cost'))
        return content


def select(cases, suite='smoke', tags=(), ids=(), limit=None):
    selected = []
    for c in cases:
        if suite == 'smoke' and not c.get('smoke'):
            continue
        if suite == 'difficult' and not c.get('difficult'):
            continue
        if tags and not set(tags) & set(c['tags']):
            continue
        if ids and not set(ids) & set(c['aliases']):
            continue
        if not c['expected']:
            continue
        selected.append(c)
    return selected[:limit] if limit is not None else selected


def assess(case, prediction):
    keys = {c['key'] for c in prediction.get('categorias', [])}
    expected = case['expected']
    return {'missing': sorted(k for k, v in expected.items() if v and k not in keys),
            'unexpected': sorted(k for k, v in expected.items() if not v and k in keys),
            'unreviewed_predictions': sorted(keys - set(expected)),
            'predicted': sorted(keys)}


def contrast_cases(cases):
    """Two reviewed controls, exercising the actual production follow-up."""
    probes = {'green-TC03772': 'contenedor_humedos_lateral',
              'r4-T044': 'contenedor_humedos_bilateral'}
    result = []
    for case in cases:
        for alias in case['aliases']:
            key = probes.get(alias)
            if key and key in case['expected']:
                result.append(dict(case, expected={key: case['expected'][key]}, target=key))
                break
    return result


def add_case(private, photo, ident, expected, tags, context='', note='', smoke=False):
    """Register explicit human labels; preserve additions across reimports."""
    manifest_path = private / 'cases.json'
    manifest = json.loads(manifest_path.read_text())
    if not expected or not all(isinstance(value, bool) for value in expected.values()):
        raise ValueError('Expected labels must be a nonempty object of category: true/false.')
    categories = json.loads((ROOT / 'categorias.json').read_text())
    if set(expected) - set(categories):
        raise ValueError('Unknown category keys: ' + ', '.join(sorted(set(expected) - set(categories))))
    raw = photo.read_bytes()
    sha = D.digest(raw)
    for c in manifest['cases']:
        if ident in c['aliases']:
            raise ValueError('Case ID already exists.')
        if c['sha256'] == sha and c.get('context', '') == context:
            raise ValueError('Photo/context already exists as ' + c['id'] + '; reconcile its labels instead.')
    with Image.open(photo) as image:
        image.verify()
    target = private / 'photos' / (sha + photo.suffix.lower())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    case = {'id': ident, 'aliases': [ident], 'expected': expected, 'tags': tags,
            'status': 'reviewed', 'context': context, 'notes': [note] if note else [],
            'review': {'kind': 'human_manual', 'case': ident}, 'label_conflicts': [],
            'photo': target.relative_to(private).as_posix(), 'sha256': sha,
            'smoke': smoke, 'difficult': True}
    case['reviews'] = [case['review']]
    additions_path = private / 'additions.json'
    additions = json.loads(additions_path.read_text()) if additions_path.exists() else []
    D.write_json(additions_path, additions + [case])
    manifest['cases'].append(case)
    manifest['summary'].update(unique_photo_contexts=len(manifest['cases']),
                               photos=len({c['sha256'] for c in manifest['cases']}),
                               difficult=sum(c['difficult'] for c in manifest['cases']),
                               smoke=sum(c['smoke'] for c in manifest['cases']))
    manifest['summary']['tags'] = dict(Counter(t for c in manifest['cases'] for t in c['tags']))
    D.write_json(manifest_path, manifest)
    return case


def evaluate_case(case, model, private, transport, cats, stage='initial'):
    row = {'case': case['id'], 'aliases': case['aliases'], 'model': model,
           'stage': stage,
           'tags': case['tags'], 'known_status': case['status'], 'expected': case['expected'],
           'label_hash': fingerprint(case['expected']), 'photo_sha256': case['sha256'],
           'context_hash': fingerprint(case.get('context', '')), 'photo': case['photo']}
    photo = Path(private) / case['photo']
    if not photo.is_file() or D.digest(photo.read_bytes()) != case['sha256']:
        return dict(row, status='missing_photo', error='Photo missing or SHA-256 mismatch.')
    transport.last = None
    try:
        with Image.open(photo) as original:
            image = ImageOps.exif_transpose(original).convert('RGB')
            with patch.object(V, '_llamar', transport):
                if stage == 'container-contrast':
                    target = case['target']
                    obj = ('un contenedor municipal de basura ' + V.DESCRIPTOR_CONTENEDOR[target]
                           + ' (aunque sea recortado por el borde del encuadre)'
                           + V._CONTRASTE_CONTENEDOR_SECOS)
                    answers, failed = V._repregunta_objeto(image, obj, [model], False)
                    if transport.blocked:
                        raise transport.blocked
                    verdict = answers[0]['veredicto'] if answers else None
                    if not failed and verdict == 'no_se_distingue':
                        return dict(row, status='abstain', response=transport.last,
                                    error='Model could not distinguish the requested object.', answers=answers)
                    if failed or verdict not in {'presente', 'ausente'}:
                        result = {'ok': False, 'error': 'Follow-up failed or abstained.', 'answers': answers}
                    else:
                        result = {'ok': True, 'categorias': [{'key': target}] if verdict == 'presente' else [],
                                  'answers': answers}
                else:
                    result = V._verificar_uno(model, V._imagen_data_url(image), cats, case.get('context', ''))
        row['response'] = transport.last
        if not result.get('ok'):
            # Invalid structured responses must not persist as usable cache.
            if transport.last and 'answers' not in result:
                (transport.cache / (transport.last['request_hash'] + '.json')).unlink(missing_ok=True)
                transport.seen.pop(transport.last['request_hash'], None)
            return dict(row, status='error', error=result.get('error', 'Invalid response'))
        assessment = assess(case, result)
        row.update(assessment, prediction=result)
        row['status'] = 'fail' if assessment['missing'] or assessment['unexpected'] else 'pass'
    except (NotAvailable, BudgetStop) as exc:
        row.update(status='uncached' if isinstance(exc, NotAvailable) else 'budget', error=str(exc))
    except Exception as exc:
        row.update(status='error', error=str(exc)[:300], response=transport.last)
    return row


def summarize(rows):
    counts = Counter(r['status'] for r in rows)
    categories = defaultdict(Counter)
    for row in rows:
        if row['status'] not in {'pass', 'fail'}:
            continue
        keys = set(row['predicted'])
        for k, truth in row['expected'].items():
            categories[k]['tp' if truth and k in keys else 'fn' if truth else 'fp' if k in keys else 'tn'] += 1
    metrics = {}
    for k, c in categories.items():
        tp, fp, fn = c['tp'], c['fp'], c['fn']
        metrics[k] = dict(c, precision=tp / (tp + fp) if tp + fp else None,
                         recall=tp / (tp + fn) if tp + fn else None)
    models, tags = defaultdict(Counter), defaultdict(Counter)
    for row in rows:
        models[row['model']][row['status']] += 1
        for tag in row.get('tags', []):
            tags[tag][row['status']] += 1
    return {'counts': dict(counts), 'complete': all(r['status'] in {'pass', 'fail'} for r in rows) and bool(rows),
            'scored_model_cases': counts['pass'] + counts['fail'], 'categories': metrics,
            'models': dict(models), 'tags': dict(tags)}


def compare(before, after):
    old = {(r['case'], r['model'], r.get('stage', 'initial')): r for r in before['rows']}
    new = {(r['case'], r['model'], r.get('stage', 'initial')): r for r in after['rows']}
    regressions, improvements, incomparable = [], [], []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        same = a and b and all(a.get(k) == b.get(k) for k in ['label_hash', 'photo_sha256', 'context_hash', 'local_hash', 'initial_fixture_hash'])
        if not same or a['status'] not in {'pass', 'fail'} or b['status'] not in {'pass', 'fail'}:
            incomparable.append(key)
            continue
        previous = {('missing', k) for k in a['missing']} | {('unexpected', k) for k in a['unexpected']}
        current = {('missing', k) for k in b['missing']} | {('unexpected', k) for k in b['unexpected']}
        previous |= {('contract', k) for k in a.get('contract_issues', [])}
        current |= {('contract', k) for k in b.get('contract_issues', [])}
        regressions.extend({'case': key[0], 'model': key[1], 'stage': key[2], 'kind': kind, 'category': cat}
                           for kind, cat in sorted(current - previous))
        improvements.extend({'case': key[0], 'model': key[1], 'stage': key[2], 'kind': kind, 'category': cat}
                            for kind, cat in sorted(previous - current))
    return {'regressions': regressions, 'improvements': improvements, 'incomparable': incomparable,
            'comparable': len(set(old) | set(new)) - len(incomparable)}


def write_report(path, report):
    D.write_json(path, report)
    esc = lambda value: html.escape(str(value))
    table = []
    categories = json.loads((ROOT / 'categorias.json').read_text())
    names = lambda keys: ', '.join(categories.get(k, {}).get('nombre', k) for k in keys) or 'Ninguno'
    comparison = report.get('comparison')
    regression_keys = {(r['case'], r['model'], r['stage']) for r in
                       (comparison or {}).get('regressions', [])}
    incomparable_keys = {tuple(k) for k in (comparison or {}).get('incomparable', [])}
    for row in report['rows']:
        photo = Path(report['private_root']) / row['photo']
        relative = os.path.relpath(photo, path.parent)
        correct = sorted(k for k in row.get('predicted', []) if row.get('expected', {}).get(k) is True)
        details = (f'<b>Confirmado correctamente:</b> {esc(names(correct))}<br>'
                   f'<b>Falta detectar:</b> {esc(names(row.get("missing", [])))}<br>'
                   f'<b>Detectado incorrectamente:</b> {esc(names(row.get("unexpected", [])))}')
        if row.get('contract_issues'):
            details += '<br>' + esc('; '.join(row['contract_issues']))
        if row['status'] not in {'pass', 'fail'}:
            details = esc(row.get('error', ''))
        if row.get('uncertain_labels'):
            details += '<br><b>Etiqueta pendiente de revisión (sin puntuar):</b> ' + esc(names(row['uncertain_labels']))
        if row.get('notes'):
            details += '<br><b>Notas de revisión:</b> ' + esc(' '.join(row['notes']))
        source = (row.get('response') or {}).get('source', '')
        status = {'pass': 'Coincide con la revisión', 'fail': 'Discrepancia con la revisión'}.get(row['status'], row['status'])
        if row['status'] == 'fail' and comparison is not None:
            key = (row['case'], row['model'], row['stage'])
            if key in regression_keys:
                status = 'Regresión nueva'
            elif key not in incomparable_keys:
                status = 'Discrepancia conocida'
            else:
                status = 'Discrepancia; comparación pendiente por cambio de etiquetas o evidencia'
        answer = esc(json.dumps(row.get('prediction', row.get('answers', {})), ensure_ascii=False, indent=2))
        table.append(f'<tr data-status="{esc(row["status"])}"><td><a href="{esc(relative)}"><img loading="lazy" src="{esc(relative)}" width="130"></a></td>'
                     f'<td>{esc(row["case"])}<br>{esc(row["model"])}<br>{esc(row["known_status"])}</td>'
                     f'<td>{esc(status)} ({esc(source)})</td><td>{details}'
                     f'<details><summary>Respuesta</summary><pre>{answer}</pre></details></td></tr>')
    summary = esc(', '.join(f'{k}: {v}' for k, v in report['summary']['counts'].items()))
    comparison_note = (f'<p>Comparación: {len(comparison["regressions"])} regresiones nuevas, '
                       f'{len(comparison["improvements"])} mejoras, '
                       f'{len(comparison["incomparable"])} casos no comparables. '
                       'Una discrepancia conocida sigue siendo un resultado pendiente de corregir.</p>'
                       if comparison is not None else '')
    page = ('<!doctype html><meta charset="utf-8"><title>Pruebas de visión</title>'
            '<style>body{font:15px system-ui;margin:24px}td{padding:12px;border-bottom:1px solid #ddd;vertical-align:top}'
            'img{max-height:160px;object-fit:contain}pre{white-space:pre-wrap;max-width:650px}tr[data-status="fail"]{background:#fff1f0}</style>'
            '<h1>Pruebas de visión</h1>'
            f'<p>Etapa: {esc(report["kind"])}. '
            + ('Evalúa el resultado final de la API con evidencia registrada.</p>' if report['kind'] in {'pipeline', 'validation-summary'}
               else 'No evalúa el consenso ni la descripción final.</p>')
            +
            f'<p>Resultados: {summary}. Costo registrado: US${report["cost_usd"]:.6f}.</p>'
            + (f'<p>Costo de los ensayos de esta revisión: US${report["development_api_cost_usd"]:.6f}. '
               'Validación local; cambios sin desplegar.</p>'
               if report.get('validation', {}).get('local_only') and 'development_api_cost_usd' in report else '')
            + comparison_note +
            f'<p>Ejecución completa: {"sí" if report["summary"]["complete"] else "no"}. '
            f'Facturación pendiente de verificar: {"sí" if report.get("billing_uncertain") else "no"}.</p>'
            '<p>Solo se puntúan las categorías revisadas. Un resultado cacheado no es una nueva lectura de la foto.</p>'
            '<p>protected y known_limit son etiquetas históricas del conjunto, no resultados de esta ejecución. '
            'Un cambio de etiquetas requiere una nueva referencia y no cuenta como mejora del modelo.</p>'
            '<label><input type="checkbox" onchange="document.querySelectorAll(\'tr[data-status=pass]\').forEach(r=>r.hidden=this.checked)"> Ocultar aciertos</label>'
            '<table><thead><tr><th>Foto</th><th>Caso / modelo</th><th>Estado</th><th>Aciertos y discrepancias</th></tr></thead><tbody>'
            + ''.join(table) + '</tbody></table>')
    path.with_suffix('.html').write_text(page)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--reviews-root', type=Path, required=True)
    prep.add_argument('--september-root', type=Path, required=True)
    prep.add_argument('--green-photo', type=Path, required=True)
    prep.add_argument('--private', type=Path, default=DEFAULT_PRIVATE)
    add = sub.add_parser('add')
    add.add_argument('--private', type=Path, default=DEFAULT_PRIVATE)
    add.add_argument('--photo', type=Path, required=True)
    add.add_argument('--id', required=True)
    add.add_argument('--expected', type=Path, required=True, help='JSON file with human-reviewed category: true/false labels')
    add.add_argument('--tags', nargs='+', required=True)
    add.add_argument('--context', default='')
    add.add_argument('--note', default='')
    add.add_argument('--smoke', action='store_true')
    review = sub.add_parser('review')
    review.add_argument('--private', type=Path, default=DEFAULT_PRIVATE)
    review.add_argument('--file', type=Path, required=True, help='Photo-bound human label corrections')
    for name in ['plan', 'run']:
        p = sub.add_parser(name)
        p.add_argument('--private', type=Path, default=DEFAULT_PRIVATE)
        p.add_argument('--suite', choices=['smoke', 'difficult', 'all'], default='smoke')
        p.add_argument('--tags', nargs='+', default=[])
        p.add_argument('--ids', nargs='+', default=[])
        p.add_argument('--limit', type=int)
        p.add_argument('--models', nargs='+', default=list(DEFAULT_MODELS))
        p.add_argument('--stage', choices=['initial', 'container-contrast'], default='initial')
        if name == 'run':
            p.add_argument('--live', action='store_true')
            p.add_argument('--fresh', action='store_true')
            p.add_argument('--budget', type=float, default=0.5)
            p.add_argument('--reserve', type=float, default=0.03)
            p.add_argument('--max-age-days', type=float, default=7)
            p.add_argument('--out', type=Path)
    cmp = sub.add_parser('compare')
    cmp.add_argument('before', type=Path)
    cmp.add_argument('after', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'review':
        updates = json.loads(args.file.read_text())
        categories = json.loads((ROOT / 'categorias.json').read_text())
        for update in updates:
            if set(update['labels']) - set(categories):
                raise ValueError('Unknown category in review: ' + update['case'])
        path = args.private / 'cases.json'
        revised = D.apply_reviews(json.loads(path.read_text()), updates)
        archive = args.private / 'reviews.json'
        recorded = json.loads(archive.read_text()) if archive.exists() else []
        D.write_json(archive, recorded + [r for r in updates if r not in recorded])
        D.write_json(path, revised)
        print(f'Recorded {len(updates)} human reviews; no API calls made.')
        return 0
    if args.command == 'add':
        case = add_case(args.private, args.photo, args.id, json.loads(args.expected.read_text()),
                        args.tags, args.context, args.note, args.smoke)
        print('Added ' + case['id'] + '; no API calls made.')
        return 0
    if args.command == 'prepare':
        result = D.prepare(args.reviews_root, args.september_root, args.green_photo, args.private)
        print(json.dumps(result['summary'], ensure_ascii=False, indent=2))
        print(f'Missing inputs: {len(result["missing"])}; label conflicts: {len(result["label_conflicts"])}')
        return 0
    if args.command == 'compare':
        result = compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result['incomparable'] else 1 if result['regressions'] else 0
    if args.limit is not None and args.limit <= 0:
        parser.error('--limit must be positive')
    manifest = json.loads((args.private / 'cases.json').read_text())
    cases = select(manifest['cases'], args.suite, args.tags, args.ids, args.limit)
    if args.stage == 'container-contrast':
        cases = contrast_cases(cases)
    models = list(dict.fromkeys(args.models))
    if args.command == 'plan':
        print(json.dumps({'cases': len(cases), 'models': models, 'maximum_requests': len(cases) * len(models),
                          'case_ids': [c['id'] for c in cases], 'missing_inputs': manifest['missing'],
                          'label_conflicts': manifest['label_conflicts']}, ensure_ascii=False, indent=2))
        return 0 if cases else 2
    if args.fresh and not args.live:
        parser.error('--fresh requires --live')
    if args.live:
        load_env()
        if not V.api_key():
            parser.error('Missing OPENROUTER_API_KEY')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.out or args.private / 'runs' / (stamp + '.json')
    if output.exists():
        parser.error('Output exists; choose a new path to preserve the baseline')
    output.parent.mkdir(parents=True, exist_ok=True)
    transport = Transport(args.private / 'cache', args.live, args.fresh, args.budget,
                          args.reserve, args.max_age_days, journal=output.with_suffix('.attempts.jsonl'))
    cats = json.loads((ROOT / 'categorias.json').read_text())
    report = {'version': 1, 'kind': args.stage, 'created_at': stamp,
              'suite': args.suite, 'private_root': str(args.private.resolve()),
              'rubric_hash': fingerprint(V._prompt_sistema(cats)), 'models': models,
              'budget_usd': args.budget, 'reserve_usd': args.reserve, 'rows': [],
              'cost_usd': 0, 'summary': {}, 'fresh': args.fresh}
    for case in cases:
        for model in models:
            row = evaluate_case(case, model, args.private, transport, cats, args.stage)
            report['rows'].append(row)
            report['cost_usd'] = float(transport.meter.spent)
            report['billing_uncertain'] = transport.meter.unresolved
            report['requests_made'] = transport.meter.calls
            report['summary'] = summarize(report['rows'])
            report['summary']['planned_model_cases'] = len(cases) * len(models)
            report['summary']['complete'] &= len(report['rows']) == len(cases) * len(models)
            write_report(output, report)
            print(f'{case["id"]} {model}: {row["status"]}; spent US${report["cost_usd"]:.6f}', flush=True)
    if not cases:
        report['summary'] = summarize([])
        write_report(output, report)
    print(f'Report: {output}\n{canonical(report["summary"]["counts"])}')
    return 2 if not report['summary']['complete'] else 1 if report['summary']['counts'].get('fail') else 0


if __name__ == '__main__':
    raise SystemExit(main())
