"""Import reviewed cases into a private, portable photo corpus."""
import hashlib
import copy
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

ROUNDS = {
    'sample-a': 'revision_muestra20', 'sample-b': 'revision_muestra20b',
    'sample-c': 'revision_muestra20c', 'r1': 'revision_recoleccion_200',
    'r2': 'revision_recoleccion2_200', 'r4': 'revision_recoleccion3_200',
    'r4-small': 'revision_recoleccion4_20', 'r5': 'revision_ronda5_50',
    'r5-large': 'revision_recoleccion5_200', 'r6': 'revision_recoleccion6_100',
}
TAGS = {
    'contenedores': {'contenedor_secos', 'contenedor_humedos_lateral', 'contenedor_humedos_bilateral'},
    'danos': {'reparacion_contenedor', 'reposicion_contenedor', 'reparacion_cesto'},
    'desborde': {'contenedor_desbordado', 'vaciado_contenedor'},
    'escombros': {'retiro_escombros', 'recoleccion_restos_obra'},
    'voluminosos': {'retiro_muebles'}, 'recoleccion': {'recoleccion'},
    'barrido': {'barrido'}, 'poda': {'retiro_poda'},
    'vehiculos': {'vehiculo_mal_estacionado', 'vehiculo_abandonado'},
    'acopio': {'acopio_recuperadores'},
}
KNOWN = {
    'r4-T005': ('subtipo', 'protected'), 'r4-T044': ('dos_contenedores', 'protected'),
    'r4-T082': ('subtipo', 'protected'), 'r4-T008': ('quemado', 'protected'),
    'r4-T035': ('contenedor_fantasma', 'protected'),
    'r4-T109': ('contenedor_fantasma,rotura_fantasma', 'known_limit'),
    'r4-T130': ('contenedor_fantasma', 'protected'),
    'r4-T038': ('manta_colchon', 'protected'),
    'r4-T050': ('tapa_trabada', 'protected'), 'r4-T064': ('tapa_trabada', 'protected'),
    'r4-T051': ('gravedad', 'protected'), 'r4-T083': ('gravedad', 'known_limit'),
    'r4-T055': ('mobiliario_urbano', 'protected'), 'r4-T068': ('mobiliario_urbano', 'protected'),
    'r4-T175': ('mobiliario_urbano,rotura_fantasma', 'protected'),
    'r4-T097': ('tolva_cerrada', 'known_limit'), 'r4-T159': ('tolva_cerrada', 'known_limit'),
    'r4-T183': ('rotura_fantasma', 'known_limit'),
    'r4-T132': ('escombros_omitidos', 'known_limit'), 'r4-T173': ('escombros_omitidos', 'known_limit'),
    'r4-T141': ('tacho_particular', 'protected'), 'r4-T152': ('objetos_secundarios', 'known_limit'),
    'r4-T160': ('dano_estetico', 'protected'), 'r4-T017': ('objetos_secundarios', 'protected'),
    'r4-T053': ('carton_madera', 'label_review'),
    'r5-U003': ('carton_madera', 'protected'), 'r5-U013': ('eje_tapa', 'known_limit'),
    'r5-U014': ('poda_bolsas', 'protected'), 'r5-U022': ('subtipo_nocturno', 'protected'),
    'r5-U030': ('bolsas_fantasma', 'protected'), 'r5-U032': ('prosa_objetos', 'known_limit'),
    'r5-U035': ('escombros_inciertos', 'known_limit'),
    'r5-U042': ('carton_madera,ruedas_asfalto', 'known_limit'),
    'r5-U049': ('prosa_objetos,carton_madera', 'known_limit'),
    'r4-small-R005': ('predio_privado', 'protected'),
    'r6-V049': ('escombros_omitidos', 'known_limit'),
    'r6-V060': ('escombros_falsos', 'known_limit'),
}
SMOKE = ['green-TC03772', 'r4-T044', 'r4-T008', 'r4-T109', 'r5-U003',
         'r4-T017', 'r4-T050', 'r5-U022', 'r6-V049', 'r6-V060',
         'sep-T011', 'sep-T016']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def apply_reviews(manifest, reviews):
    """Apply photo-bound human corrections without changing inference context."""
    result = copy.deepcopy(manifest)
    for review in reviews:
        matches = [c for c in result['cases'] if review['case'] in c['aliases']]
        if len(matches) != 1:
            raise ValueError('Review must identify exactly one case: ' + review['case'])
        case = matches[0]
        if review['photo_sha256'] != case['sha256'] or review['context'] != case.get('context', ''):
            raise ValueError('Review photo/context changed: ' + review['case'])
        labels = review['labels']
        if (not isinstance(labels, dict) or not labels
                or any(v is not None and not isinstance(v, bool) for v in labels.values())
                or not all(review.get(k) for k in ('reviewed_at', 'source', 'note'))):
            raise ValueError('Review needs boolean/null labels, date, source and note.')
        sha = digest(json.dumps(review, sort_keys=True, ensure_ascii=False).encode())
        history = case.setdefault('review_updates', [])
        if any(r['sha256'] == sha for r in history):
            continue
        history.append({'sha256': sha, 'previous_expected': copy.deepcopy(case['expected']),
                        'review': copy.deepcopy(review)})
        uncertain = set(case.get('uncertain_labels', []))
        for key, value in labels.items():
            if value is None:
                case['expected'].pop(key, None)
                uncertain.add(key)
            else:
                case['expected'][key] = value
                uncertain.discard(key)
        case['uncertain_labels'] = sorted(uncertain)
        case['label_conflicts'] = [k for k in case.get('label_conflicts', [])
                                   if k not in labels or labels[k] is None]
        case.setdefault('notes', []).append(review['note'])
    result['label_conflicts'] = [{'id': c['id'], 'categories': c['label_conflicts']}
                                 for c in result['cases'] if c.get('label_conflicts')]
    return result


def prepare(reviews_root, september_root, green_photo, out):
    out = Path(out)
    cases, warnings, missing = [], [], []

    def add(case, photo):
        if not photo.is_file():
            missing.append({'id': case['id'], 'reason': 'missing_photo'})
            return
        raw = photo.read_bytes()
        sha = digest(raw)
        target = out / 'photos' / (sha + photo.suffix.lower())
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(photo, target)
        case.update(photo=target.relative_to(out).as_posix(), sha256=sha)
        cases.append(case)

    for alias, name in ROUNDS.items():
        folder = Path(reviews_root) / name
        state, predictions = folder / 'estado_revision.json', folder / 'predicciones.json'
        if not state.is_file() or not predictions.is_file():
            missing.append({'id': alias, 'reason': 'missing_review_round'})
            continue
        photos = {r['id']: r for r in json.loads(predictions.read_text())}
        for ident, review in json.loads(state.read_text()).items():
            if not isinstance(review, dict) or review.get('rev') is not True:
                continue
            record = photos.get(ident)
            if not record:
                missing.append({'id': alias + '-' + ident, 'reason': 'missing_photo_mapping'})
                continue
            labels = {k: v for k, v in (review.get('cats') or {}).items() if isinstance(v, bool)}
            case_id = alias + '-' + ident
            special, status = KNOWN.get(case_id, ('', 'reviewed'))
            tags = sorted({tag for tag, keys in TAGS.items() if keys & set(labels)}
                          | set(filter(None, special.split(','))))
            notes = []
            # The later case log contradicts the saved checkbox. Keep the
            # disputed category unscored until a person reconciles the label.
            if case_id == 'r4-T053':
                labels.pop('retiro_muebles', None)
                notes.append('Conflicting saved review and later case log for retiro_muebles.')
            add({'id': case_id, 'aliases': [case_id], 'expected': labels,
                 'tags': tags, 'status': status, 'context': '', 'notes': notes,
                 'review': {'kind': 'human_checkbox', 'round': alias, 'case': ident,
                            'severity_informational': review.get('grav'),
                            'source_sha256': digest(state.read_bytes())}},
                folder / 'fotos' / record['archivo'])

    september_root = Path(september_root)
    human = september_root / 'human-review-original.json'
    manifest = september_root / 'blind-manifest.json'
    if human.is_file() and manifest.is_file():
        photos = {r['id']: r['photo'] for r in json.loads(manifest.read_text())}
        for review in json.loads(human.read_text())['items']:
            ident = review['id']
            scope, label = review.get('scope'), review.get('label')
            uncertain = ident == 'T009' or label not in {'si', 'no'}
            expected = {} if uncertain else {'retiro_escombros': label == 'si' and scope == 'in_scope'}
            tags = ['escombros', 'alcance', 'bolsas_opacas']
            if scope != 'in_scope':
                tags.append('predio_privado' if scope == 'private_property' else 'bolson_obra')
            base = {'id': 'sep-' + ident, 'aliases': ['sep-' + ident], 'expected': expected,
                    'tags': tags, 'status': 'label_review' if uncertain else 'reviewed',
                    'context': '', 'notes': ['Human visual interpretation; hidden contents were not inspected.'],
                    'review': {'kind': 'human_visual_and_scope', 'case': ident,
                               'scope': scope, 'material_label': label,
                               'evidence': review.get('evidence', ''),
                               'source_sha256': digest(human.read_bytes())}}
            add(base, september_root / photos[ident])
            if ident in {'T001', 'T005', 'T011', 'T016', 'T028', 'T029'}:
                contextual = dict(base, id=base['id'] + '-context',
                                  aliases=[base['id'] + '-context'],
                                  context='Estas bolsas contienen escombros de una refacción.',
                                  tags=tags + ['contexto'])
                add(contextual, september_root / photos[ident])
    else:
        missing.append({'id': 'september', 'reason': 'missing_human_review'})
    add({'id': 'green-TC03772', 'aliases': ['green-TC03772'],
         'expected': {'contenedor_secos': True, 'contenedor_humedos_lateral': False,
                      'recoleccion': True},
         'tags': ['contenedores', 'contenedor_fantasma', 'recoleccion'],
         'status': 'protected', 'context': '', 'notes': [],
         'review': {'kind': 'current_user_report', 'case': 'TC_03772'}}, Path(green_photo))
    additions = out / 'additions.json'
    if additions.is_file():
        for case in json.loads(additions.read_text()):
            photo = out / case['photo']
            if not photo.is_file() or digest(photo.read_bytes()) != case['sha256']:
                missing.append({'id': case['id'], 'reason': 'changed_custom_photo'})
                continue
            add(case, photo)

    # Identical bytes and context are one test, even across review rounds.
    groups = defaultdict(list)
    for case in cases:
        groups[(case['sha256'], case['context'])].append(case)
    unique = []
    for group in groups.values():
        preferred = next((c for c in group if c['id'] in SMOKE or c['id'] in KNOWN), group[0])
        case = dict(preferred)
        case['aliases'] = sorted({a for c in group for a in c['aliases']})
        case['tags'] = sorted({t for c in group for t in c['tags']})
        case['reviews'] = [c['review'] for c in group]
        labels = defaultdict(set)
        for c in group:
            for k, v in c['expected'].items():
                labels[k].add(v)
        conflict = sorted(k for k, vals in labels.items() if len(vals) > 1)
        case['expected'] = {k: next(iter(vals)) for k, vals in sorted(labels.items()) if len(vals) == 1}
        # A conflicted category in any duplicate stays unscored.
        if 'r4-T053' in case['aliases']:
            case['expected'].pop('retiro_muebles', None)
            conflict = sorted(set(conflict) | {'retiro_muebles'})
        case['label_conflicts'] = conflict
        if conflict:
            warnings.append({'id': case['id'], 'categories': conflict})
        case['smoke'] = bool(set(case['aliases']) & set(SMOKE)) or any(c.get('smoke') for c in group)
        case['difficult'] = (any(a in KNOWN or a.startswith(('sep-', 'green-')) for a in case['aliases'])
                             or any(c.get('difficult') for c in group))
        unique.append(case)
    unique.sort(key=lambda c: (not c['smoke'], not c['difficult'], c['id']))
    result = {'version': 1, 'annotation_policy': 'Only explicitly reviewed categories are scored; missing keys are unknown.',
              'cases': unique, 'missing': missing, 'label_conflicts': warnings,
              'summary': {'reviews_imported': len(cases), 'unique_photo_contexts': len(unique),
                          'photos': len({c['sha256'] for c in unique}),
                          'difficult': sum(c['difficult'] for c in unique),
                          'smoke': sum(c['smoke'] for c in unique),
                          'tags': dict(Counter(t for c in unique for t in c['tags']))}}
    corrections = out / 'reviews.json'
    if corrections.is_file():
        result = apply_reviews(result, json.loads(corrections.read_text()))
    write_json(out / 'cases.json', result)
    return result
