"""Free regression checks for final-result evaluation and incomplete evidence."""
import concurrent.futures
import io
import json
import tempfile
import types
import unittest
from pathlib import Path

from eval.vision import pipeline as P
from eval.vision import run as R


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        photo = self.root / 'photo.jpg'
        P.Image.new('RGB', (40, 40)).save(photo)
        self.case = {'id': 'test', 'aliases': ['test'], 'tags': [], 'status': 'reviewed',
                     'photo': photo.name, 'sha256': R.D.digest(photo.read_bytes()),
                     'expected': {'recoleccion': True}, 'context': ''}
        self.identity = {'weights': 'tested-weights', 'functions': 'tested-functions'}
        R.D.write_json(P.local_path(self.root, self.case, self.identity), {
            'identity': self.identity, 'photo_sha256': self.case['sha256'],
            'prediction': {'predichas': [], 'probabilidades': []}})
        self.public = {'problemas': [], 'elementos_detectados': [], 'categorias_contexto': [],
                       'hay_problema': False, 'hay_reclamo': False, 'descripcion': ''}
        self.internal = {'en_duda': ['retiro_escombros'],
                         'detalle': {'verificacion': {'activa': True, 'en_duda': []}}}
        self.server = types.SimpleNamespace(
            CATEGORIAS={}, clasificar_local=lambda img: None, _hay_cuota=lambda: True,
            procesar=lambda *a: self.internal, _publica=lambda r: self.public,
            _cacheable=lambda r: not r['en_duda'])

    def test_missing_local_snapshot_is_incomplete_not_a_placeholder_prediction(self):
        P.local_path(self.root, self.case, self.identity).unlink()
        transport = R.Transport(self.root / 'cache')
        row = P.evaluate(self.case, self.root, self.identity, self.server, transport, True)
        self.assertEqual(row['status'], 'uncached')
        self.assertEqual(transport.meter.calls, 0)

    def test_changed_local_weights_cannot_reuse_snapshot(self):
        with self.assertRaises(R.NotAvailable):
            P.read_local(self.root, self.case, dict(self.identity, weights='new-weights'))
        path = P.local_path(self.root, self.case, self.identity)
        record = json.loads(path.read_text())
        record['photo_sha256'] = 'another-photo'
        R.D.write_json(path, record)
        with self.assertRaises(R.NotAvailable):
            P.read_local(self.root, self.case, self.identity)

    def test_deliberate_uncertainty_is_scored_as_missing_not_a_transport_error(self):
        row = P.evaluate(self.case, self.root, self.identity, self.server,
                         R.Transport(self.root / 'cache'), True)
        self.assertEqual(row['status'], 'fail')
        self.assertEqual(row['missing'], ['recoleccion'])

    def test_production_schema_failure_invalidates_cached_json_and_blocks_pass(self):
        raw = {'usage': {'cost': .001}, 'choices': [{'finish_reason': 'stop',
               'message': {'content': '{"unrelated": true}'}}]}
        transport = R.Transport(self.root / 'cache', live=True,
                                opener=lambda *a, **k: io.BytesIO(json.dumps(raw).encode()))
        def process(*args):
            def parse(model):
                return json.loads(R.V._llamar(model, [], etapa='scope_test'))['required_field']
            R.V._map_modelos(['model'], parse)
            return self.internal
        self.server.procesar = process
        row = P.evaluate(self.case, self.root, self.identity, self.server, transport, True)
        self.assertEqual(row['status'], 'error')
        self.assertEqual(transport.meter.calls, 1)
        self.assertEqual(float(transport.meter.spent), .001)
        self.assertFalse(list((self.root / 'cache').glob('*.json')))
        self.assertEqual(row['requests'][0]['error_type'], 'KeyError')

    def test_parallel_pipeline_calls_cannot_exceed_sequential_admission(self):
        raw = {'usage': {'cost': .002}, 'choices': [{'finish_reason': 'stop',
               'message': {'content': '{"categorias": []}'}}]}
        transport = R.Transport(self.root / 'cache', live=True, fresh=True, budget=.032, reserve=.03,
                                opener=lambda *a, **k: io.BytesIO(json.dumps(raw).encode()))
        def request(n):
            try:
                transport('model', [{'role': 'user', 'content': str(n)}])
                return 'made'
            except R.BudgetStop:
                return 'budget'
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            results = list(pool.map(request, range(8)))
        self.assertEqual(results.count('made'), 2)
        self.assertEqual(transport.meter.calls, 2)
        self.assertEqual(len(transport.events), 8)

    def test_contract_failures_and_local_evidence_changes_are_comparison_gates(self):
        row = {'case': 'test', 'model': 'final-api', 'stage': 'pipeline', 'status': 'fail',
               'local_hash': 'local', 'label_hash': 'labels', 'photo_sha256': 'photo',
               'context_hash': 'context', 'missing': ['recoleccion'], 'unexpected': [],
               'contract_issues': []}
        after = dict(row, contract_issues=['internal category key in final description'])
        result = R.compare({'rows': [row]}, {'rows': [after]})
        self.assertEqual(result['regressions'][0]['kind'], 'contract')
        after = dict(row, local_hash='different local result')
        self.assertTrue(R.compare({'rows': [row]}, {'rows': [after]})['incomparable'])

    def test_public_contract_checks_do_not_accept_internal_category_names(self):
        public = dict(self.public, descripcion='Se detecta contenedor_secos.')
        self.assertIn('internal category key in final description', P.contract_issues(public))
        public['descripcion'] = 'Se ve un contenedor de reciclables.'
        self.assertEqual(P.contract_issues(public), [])

    def test_description_cannot_deny_confirmed_rubble(self):
        public = dict(self.public, hay_problema=True, hay_reclamo=True,
                      problemas=[{'key': 'retiro_escombros'}],
                      descripcion='Las bolsas son residuos comunes y no muestran señales visibles de escombros.')
        self.assertIn('description denies confirmed retiro_escombros', P.contract_issues(public))
        public['descripcion'] = 'Hay dos sacos de escombros junto al contenedor.'
        self.assertEqual(P.contract_issues(public), [])

    def test_frozen_baseline_replays_old_evidence_without_disk_cache_or_api(self):
        raw = {'usage': {'cost': .001}, 'choices': [{'finish_reason': 'stop',
               'message': {'content': '{"categorias": []}'}}]}
        source = R.Transport(self.root / 'cache', live=True,
                             opener=lambda *a, **k: io.BytesIO(json.dumps(raw).encode()))
        source('model', [])
        event = dict(source.events[0], created_at=0)
        replay = R.Transport(self.root / 'empty-cache')
        baseline = {'kind': 'pipeline', 'rows': [{'status': 'pass', 'requests': [event]}]}
        P.freeze_evidence(replay, baseline)
        self.assertEqual(replay('model', []), '{"categorias": []}')
        self.assertEqual(replay.meter.calls, 0)
        with self.assertRaises(R.NotAvailable):
            replay('model', [{'role': 'user', 'content': 'changed prompt'}])
        baseline['rows'][0]['status'] = 'error'
        with self.assertRaises(ValueError):
            P.freeze_evidence(replay, baseline)

    def test_description_cannot_recommend_unconfirmed_container_work(self):
        for text in ['Se requiere reparación del contenedor.', 'El contenedor requiere reparación.']:
            public = dict(self.public, descripcion=text)
            self.assertIn('description recommends unconfirmed reparacion_contenedor', P.contract_issues(public))
        public = dict(self.public, descripcion='El contenedor no requiere reparación.')
        self.assertEqual(P.contract_issues(public), [])

    def test_description_cannot_add_repositioning_through_relative_clause(self):
        public = dict(self.public, descripcion='El contenedor está desplazado, lo que requiere reposición.')
        self.assertIn('description recommends unconfirmed reposicion_contenedor', P.contract_issues(public))
        public.update(descripcion='Se requiere reparación del contenedor.', hay_problema=True,
                      hay_reclamo=True, problemas=[{'key': 'reparacion_contenedor'}])
        self.assertEqual(P.contract_issues(public), [])


if __name__ == '__main__':
    unittest.main()
