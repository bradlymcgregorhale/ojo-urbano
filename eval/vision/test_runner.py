"""Cost-free checks of billing, cache isolation, labels and regression scoring."""
import copy
from contextlib import closing
import io
import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from eval.vision import run as R


class RunnerTests(unittest.TestCase):
    def test_training_keeps_secondary_tags_and_opens_database_readonly(self):
        from eval.vision.local_training import read_multilabel_tags
        path = self.root / 'labels.db'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE photo_labels (file TEXT, label_type TEXT)')
            db.execute('CREATE TABLE photo_tags (file TEXT, label_type TEXT)')
            db.execute("INSERT INTO photo_labels VALUES ('photo.jpg', 'recoleccion')")
            db.executemany('INSERT INTO photo_tags VALUES (?, ?)', [
                ('photo.jpg', 'recoleccion'), ('photo.jpg', 'retiro_escombros')])
        before = path.read_bytes()
        self.assertEqual(read_multilabel_tags(path)['photo.jpg'], {'recoleccion', 'retiro_escombros'})
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(sqlite3.OperationalError):
            read_multilabel_tags(self.root / 'missing.db')
        self.assertFalse((self.root / 'missing.db').exists())

    def test_retention_update_preserves_reference_and_does_not_mutate_head(self):
        import numpy as np
        from types import SimpleNamespace
        from scipy.special import expit
        from eval.vision.local_training import refine_head
        head = SimpleNamespace(coef_=np.array([[2., -1.]]), intercept_=np.array([.3]))
        x = np.array([[1., 0.], [-1., 0.], [0., 1.]])
        targets = expit(x @ head.coef_[0] + head.intercept_[0])
        same, _ = refine_head(head, x, targets, np.ones(3))
        np.testing.assert_allclose(same.coef_, head.coef_)
        corrected = targets.copy()
        corrected[0] = 0
        updated, _ = refine_head(head, x, corrected, np.array([100., 1., 1.]))
        self.assertLess(expit(updated.coef_[0, 0] + updated.intercept_[0]), .1)
        np.testing.assert_array_equal(head.coef_, [[2., -1.]])
        with self.assertRaises(ValueError):
            refine_head(head, x, np.array([2., 0., 0.]), np.ones(3))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.requests = []
        self.raw = {'id': 'test', 'model': 'test-model', 'provider': 'test-provider',
                    'usage': {'cost': 0.002}, 'choices': [{'finish_reason': 'stop',
                    'message': {'content': '{"categorias": []}'}}]}
        self.messages = [{'role': 'system', 'content': 'rubric'},
                         {'role': 'user', 'content': [{'type': 'text', 'text': 'context'},
                          {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,AAA'}}]}]

    def open(self, req, **kwargs):
        self.requests.append(json.loads(req.data))
        return io.BytesIO(json.dumps(self.raw).encode())

    def transport(self, **kwargs):
        return R.Transport(self.root / 'cache', opener=self.open, **kwargs)

    def call(self, transport, messages=None, model='test-model', **kwargs):
        return transport(model, messages or self.messages, etapa='verificar_uno', **kwargs)

    def test_offline_never_calls_api(self):
        with self.assertRaises(R.NotAvailable):
            self.call(self.transport())
        self.assertEqual(self.requests, [])

    def test_exact_replay_is_free_and_preserves_raw_provenance(self):
        first = self.transport(live=True)
        expected = self.call(first)
        replay = self.transport()
        self.assertEqual(expected, self.call(replay))
        self.assertEqual(replay.meter.spent, 0)
        self.assertEqual(replay.last['source'], 'cache')
        self.assertEqual(replay.last['raw']['provider'], 'test-provider')
        self.assertEqual(len(self.requests), 1)

    def test_transport_body_matches_production_generation_settings(self):
        # The legacy suite replaces module globals without restoring them.
        # Load a clean transport for this contract check, without loading the server.
        spec = importlib.util.spec_from_file_location('vision_transport_contract', R.ROOT / 'verificador.py')
        production = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(production)
        def capture(req, *args):
            self.requests.append(json.loads(req.data))
            return self.raw
        for seed, fixed, cache in [(None, False, False), (1234, True, True)]:
            with patch.object(R, 'V', production), \
                    patch.object(R.V, 'SEMILLA', seed), patch.object(R.V, 'PROVEEDOR_FIJO', fixed), \
                    patch.object(R.V, 'OPENROUTER_CACHE_PROMPTS', cache), \
                    patch.object(R.V, '_pedir_http', capture), patch.object(R.V, '_costo_sumar'), \
                    patch.object(R.V, '_registrar_uso'):
                R.V._llamar('test-model', self.messages, max_tokens=400, intentos=1)
                self.assertEqual(self.requests[-1], R.request_body('test-model', self.messages, 400))

    def test_every_request_input_invalidates_cache(self):
        self.call(self.transport(live=True))
        variants = []
        for index, value in [(0, 'changed rubric'), (1, [{'type': 'text', 'text': 'changed context'}])]:
            messages = copy.deepcopy(self.messages)
            messages[index]['content'] = value
            variants.append(messages)
        image = copy.deepcopy(self.messages)
        image[1]['content'][1]['image_url']['url'] += 'different-photo'
        variants.append(image)
        for messages in variants:
            with self.subTest(messages=messages), self.assertRaises(R.NotAvailable):
                self.call(self.transport(), messages)
        with self.assertRaises(R.NotAvailable):
            self.call(self.transport(), model='other-model')
        with self.assertRaises(R.NotAvailable):
            self.call(self.transport(), max_tokens=400)
        for setting, value in [('TEMPERATURA', 0.77), ('SEMILLA', 998877),
                               ('PROVEEDOR_FIJO', not R.V.PROVEEDOR_FIJO)]:
            with self.subTest(setting=setting), patch.object(R.V, setting, value):
                with self.assertRaises(R.NotAvailable):
                    self.call(self.transport())
        self.assertEqual(len(self.requests), 1)

    def test_fresh_bypasses_disk_and_in_run_cache(self):
        self.call(self.transport(live=True))
        fresh = self.transport(live=True, fresh=True)
        self.call(fresh)
        self.call(fresh)
        self.assertEqual(len(self.requests), 3)

    def test_expired_or_corrupt_cache_does_not_pass(self):
        self.call(self.transport(live=True))
        file = next((self.root / 'cache').glob('*.json'))
        record = json.loads(file.read_text())
        record['created_at'] = time.time() - 8 * 86400
        file.write_text(json.dumps(record))
        with self.assertRaises(R.NotAvailable):
            self.call(self.transport())
        file.write_text('partial file')
        with self.assertRaises(R.NotAvailable):
            self.call(self.transport())

    def test_admission_limit_prevents_another_request(self):
        transport = self.transport(live=True, fresh=True, budget=0.031, reserve=0.03)
        self.call(transport)
        with self.assertRaises(R.BudgetStop):
            self.call(transport)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(float(transport.meter.spent), 0.002)

    def test_unknown_or_large_bill_stops_further_spend(self):
        for cost in [None, 'NaN', -1, 0.04]:
            with self.subTest(cost=cost):
                self.raw['usage']['cost'] = cost
                transport = self.transport(live=True, fresh=True)
                self.call(transport)
                with self.assertRaises(R.BudgetStop):
                    self.call(transport)
                self.assertTrue(transport.meter.unresolved)

    def test_network_failure_has_no_retry_and_stops_spending(self):
        calls = []
        def fail(*args, **kwargs):
            calls.append(1)
            raise OSError('timeout')
        transport = self.transport(live=True)
        transport.opener = fail
        with self.assertRaises(OSError):
            self.call(transport)
        with self.assertRaises(R.BudgetStop):
            self.call(transport)
        self.assertEqual(calls, [1])

    def test_bad_response_is_billed_but_not_cached(self):
        for content, finish in [('not json', 'stop'), ('{"categorias": []}', 'length'),
                                 ('{}', 'stop'), ('[]', 'stop')]:
            with self.subTest(content=content, finish=finish):
                self.raw['choices'][0] = {'finish_reason': finish, 'message': {'content': content}}
                transport = self.transport(live=True)
                with self.assertRaises((ValueError, TypeError)):
                    self.call(transport)
                self.assertEqual(float(transport.meter.spent), 0.002)
                self.assertFalse(list((self.root / 'cache').glob('*.json')))

    def test_unreviewed_categories_are_neither_passes_nor_false_positives(self):
        result = R.assess({'expected': {'yes': True, 'no': False}},
                          {'categorias': [{'key': 'no'}, {'key': 'unknown'}]})
        self.assertEqual(result['missing'], ['yes'])
        self.assertEqual(result['unexpected'], ['no'])
        self.assertEqual(result['unreviewed_predictions'], ['unknown'])

    def test_compare_detects_new_error_even_if_case_already_failed(self):
        row = {'case': 'a', 'model': 'm', 'status': 'fail', 'label_hash': 'l',
               'photo_sha256': 'p', 'context_hash': 'c', 'missing': ['a'], 'unexpected': []}
        after = dict(row, missing=['a', 'b'])
        result = R.compare({'rows': [row]}, {'rows': [after]})
        self.assertEqual([r['category'] for r in result['regressions']], ['b'])
        for field in ['label_hash', 'photo_sha256', 'context_hash', 'stage', 'status']:
            with self.subTest(field=field):
                changed = dict(after, **{field: 'changed'})
                self.assertTrue(R.compare({'rows': [row]}, {'rows': [changed]})['incomparable'])
        self.assertTrue(R.compare({'rows': [row]}, {'rows': []})['incomparable'])

    def test_photo_tampering_prevents_api_call(self):
        photo = self.root / 'test.jpg'
        photo.write_bytes(b'changed')
        case = {'id': 'a', 'aliases': ['a'], 'tags': [], 'status': 'reviewed',
                'expected': {'recoleccion': True}, 'sha256': 'wrong', 'photo': photo.name}
        row = R.evaluate_case(case, 'm', self.root, self.transport(live=True), {})
        self.assertEqual(row['status'], 'missing_photo')
        self.assertEqual(self.requests, [])

    def test_contrast_offline_and_budget_are_not_misreported_as_model_errors(self):
        photo = self.root / 'test.jpg'
        R.Image.new('RGB', (40, 40)).save(photo)
        case = {'id': 'green-TC03772', 'aliases': ['green-TC03772'], 'tags': [],
                'status': 'reviewed', 'expected': {'contenedor_humedos_lateral': False},
                'sha256': R.D.digest(photo.read_bytes()), 'photo': photo.name}
        case = R.contrast_cases([case])[0]
        for transport, status in [(self.transport(), 'uncached'),
                                  (self.transport(live=True, budget=0), 'budget')]:
            row = R.evaluate_case(case, 'm', self.root, transport, {}, 'container-contrast')
            self.assertEqual(row['status'], status)
        self.assertEqual(self.requests, [])

    def test_import_merges_identical_photos_and_quarantines_conflicting_labels(self):
        reviews = self.root / 'reviews'
        folder = reviews / 'round'
        (folder / 'fotos').mkdir(parents=True)
        for name in ['a.jpg', 'b.jpg']:
            (folder / 'fotos' / name).write_bytes(b'same image')
        R.D.write_json(folder / 'predicciones.json', [{'id': 'A', 'archivo': 'a.jpg'},
                                                     {'id': 'B', 'archivo': 'b.jpg'}])
        R.D.write_json(folder / 'estado_revision.json', {
            'A': {'rev': True, 'cats': {'recoleccion': True, 'retiro_muebles': False}},
            'B': {'rev': True, 'cats': {'recoleccion': False, 'contenedor_secos': True}}})
        with patch.object(R.D, 'ROUNDS', {'test': 'round'}):
            result = R.D.prepare(reviews, self.root / 'absent', self.root / 'absent.jpg', self.root / 'out')
        self.assertEqual(len(result['cases']), 1)
        self.assertEqual(result['cases'][0]['aliases'], ['test-A', 'test-B'])
        self.assertEqual(result['cases'][0]['expected'], {'retiro_muebles': False, 'contenedor_secos': True})
        self.assertEqual(result['label_conflicts'][0]['categories'], ['recoleccion'])

    def test_manual_case_is_free_and_survives_reimport(self):
        photo = self.root / 'new.jpg'
        R.Image.new('RGB', (40, 40)).save(photo)
        private = self.root / 'private'
        R.D.write_json(private / 'cases.json', {'cases': [], 'summary': {}})
        case = R.add_case(private, photo, 'new-case', {'recoleccion': False}, ['recoleccion'], smoke=True)
        with self.assertRaises(ValueError):
            R.add_case(private, photo, 'another-id', {'recoleccion': False}, ['recoleccion'])
        with patch.object(R.D, 'ROUNDS', {}):
            result = R.D.prepare(self.root, self.root / 'missing', self.root / 'absent.jpg', private)
        self.assertEqual(result['cases'][0]['sha256'], case['sha256'])
        self.assertEqual(result['cases'][0]['expected'], {'recoleccion': False})
        self.assertTrue(result['cases'][0]['smoke'])
        self.assertEqual(self.requests, [])

    def review_fixture(self):
        case = {'id': 'photo', 'aliases': ['photo'], 'sha256': 'abc', 'context': '',
                'expected': {'recoleccion': True, 'contenedor_secos': False},
                'notes': [], 'label_conflicts': []}
        review = {'case': 'photo', 'photo_sha256': 'abc', 'context': '',
                  'labels': {'contenedor_secos': True, 'recoleccion': None},
                  'reviewed_at': '2026-09-06', 'source': 'human review', 'note': 'A green container.'}
        return {'cases': [case]}, review

    def test_human_correction_preserves_previous_labels_and_inference_context(self):
        manifest, review = self.review_fixture()
        before = copy.deepcopy(manifest)
        result = R.D.apply_reviews(manifest, [review])
        case = result['cases'][0]
        self.assertEqual(case['expected'], {'contenedor_secos': True})
        self.assertEqual(case['uncertain_labels'], ['recoleccion'])
        self.assertEqual(case['review_updates'][0]['previous_expected'], before['cases'][0]['expected'])
        self.assertEqual(case['context'], '')
        self.assertEqual(manifest, before)
        self.assertEqual(R.D.apply_reviews(result, [review]), result)

    def test_correction_rejects_wrong_photo_context_or_non_boolean_labels(self):
        manifest, original = self.review_fixture()
        for field, value in [('photo_sha256', 'changed'), ('context', 'hint for the model'),
                             ('case', 'missing'), ('labels', {'recoleccion': 1}), ('source', '')]:
            review = dict(original, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                R.D.apply_reviews(manifest, [review])

    def test_correction_survives_reimport_without_merging_old_opposite_label(self):
        photo = self.root / 'new.jpg'
        R.Image.new('RGB', (40, 40)).save(photo)
        private = self.root / 'private'
        R.D.write_json(private / 'cases.json', {'cases': [], 'summary': {}})
        case = R.add_case(private, photo, 'new-case', {'contenedor_secos': False}, ['contenedores'])
        _, review = self.review_fixture()
        review.update(case=case['id'], photo_sha256=case['sha256'])
        R.D.write_json(private / 'reviews.json', [review])
        with patch.object(R.D, 'ROUNDS', {}):
            result = R.D.prepare(self.root, self.root / 'missing', self.root / 'absent.jpg', private)
        self.assertEqual(result['cases'][0]['expected'], {'contenedor_secos': True})
        self.assertEqual(result['cases'][0]['review_updates'][0]['review'], review)
        self.assertFalse(result['label_conflicts'])


if __name__ == '__main__':
    unittest.main()
