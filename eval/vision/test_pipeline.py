"""Free regression checks for final-result evaluation and incomplete evidence."""
import concurrent.futures
import io
import json
import tempfile
import threading
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from fastapi import FastAPI, Form, Header, UploadFile

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
            PERFILES={'prueba': types.SimpleNamespace(version='version-prueba')}, _pool=None,
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

    def test_replay_prueba_scores_only_exact_archived_http_requests(self):
        self.server.app = FastAPI()
        self.server.CACHE_MAX = 10
        self.server.RATE_LIMITE = 60
        self.server.API_TOKEN = 'test-only-token'
        url = 'https://openrouter.example/chat/completions'
        cuerpo = {'model': 'lector', 'response_format': {'type': 'json_schema'}}
        resultado = dict(self.public, modo='prueba', modo_version='version-prueba', analisis_estado='completo',
                         problemas=[{'key': 'recoleccion'}], hay_problema=True, hay_reclamo=True)

        @self.server.app.post('/clasificar')
        async def clasificar(file: UploadFile, modo: str = Form(), contexto: str = Form(''), x_api_token: str = Header()):
            self.assertEqual(modo, 'prueba')
            self.assertEqual(x_api_token, self.server.API_TOKEN)
            self.assertEqual(self.server.RATE_LIMITE, 0)
            self.assertEqual((await file.read()), (self.root / self.case['photo']).read_bytes())
            solicitud = Request(url, data=json.dumps(cuerpo | {'contexto': contexto}).encode())
            try:
                R.V._pedir_http(solicitud, 10, 20)
            except FileNotFoundError:
                return dict(resultado, analisis_estado='parcial')
            return resultado

        huella = R.fingerprint({'endpoint': url, 'body': cuerpo | {'contexto': ''}})
        archivadas = {huella: {'usage': {'cost': .003}}}
        row = P.evaluate_prueba(self.case, self.root, self.identity, self.server, archivadas)
        self.assertEqual((row['status'], row['modo_version'], row['cost_usd']), ('pass', 'version-prueba', 0))
        self.assertEqual(self.server.RATE_LIMITE, 60)
        self.assertNotIn(self.server.API_TOKEN, json.dumps(row))
        self.assertEqual(row['requests'][0]['raw'], archivadas[huella])
        distinto = dict(self.case, context='Otro pedido')
        row = P.evaluate_prueba(distinto, self.root, self.identity, self.server, archivadas)
        self.assertEqual((row['status'], row['requests'][0]['source']), ('uncached', 'missing'))
        self.assertNotEqual(row['requests'][0]['request_hash'], huella)
        self.assertNotIn('predicted', row)

    def test_replay_prueba_verifica_el_trabajo_y_su_pedido_de_consumo(self):
        self.server.app = FastAPI()
        self.server.CACHE_MAX = 10
        self.server.RATE_LIMITE = 60
        self.server.API_TOKEN = 'test-only-token'
        solicitud = Request('https://openrouter.example/chat/completions', data=b'{"model":"lector"}')
        huella = R.fingerprint({'endpoint': solicitud.full_url, 'body': {'model': 'lector'}})
        archivadas = {huella: {'usage': {'cost': .003}}}
        pedido_final = 'pedido-original'
        consultas = []

        @self.server.app.post('/trabajos/', status_code=202)
        async def crear(file: UploadFile, modo: str = Form(), x_api_token: str = Header()):
            self.assertEqual(modo, 'prueba')
            self.assertEqual(x_api_token, self.server.API_TOKEN)
            self.assertEqual(await file.read(), (self.root / self.case['photo']).read_bytes())
            return {'trabajo': 'enviado', 'estado': 'en_cola', 'consumo_api': {'pedido': 'pedido-original'}}

        @self.server.app.get('/trabajos/')
        async def consultar(id: str, x_api_token: str = Header()):
            self.assertEqual(x_api_token, self.server.API_TOKEN)
            consultas.append(id)
            if len(consultas) == 1:
                return {'estado': 'procesando'}
            completo = True
            try:
                R.V._pedir_http(solicitud, 10, 20)
            except FileNotFoundError:
                completo = False
            resultado = dict(self.public, modo='prueba', modo_version='version-prueba',
                             analisis_estado='completo' if completo else 'parcial',
                             problemas=[{'key': 'recoleccion'}], hay_problema=True, hay_reclamo=True,
                             consumo_api={'pedido': pedido_final})
            return {'estado': 'listo', 'resultado': resultado}

        row = P.evaluate_prueba(self.case, self.root, self.identity, self.server, archivadas, ruta='trabajos')
        self.assertEqual((row['status'], row['ruta_prueba'], row['cost_usd']), ('pass', 'trabajos', 0))
        self.assertEqual(consultas, ['enviado', 'enviado'])
        self.assertEqual(row['prediction']['consumo_api']['pedido'], pedido_final)

        row = P.evaluate_prueba(self.case, self.root, self.identity, self.server, {}, ruta='trabajos')
        self.assertEqual((row['status'], row['requests'][0]['source']), ('uncached', 'missing'))
        self.assertEqual(row['requests'][0]['request_hash'], huella)

        pedido_final = 'otro-pedido'
        row = P.evaluate_prueba(self.case, self.root, self.identity, self.server, archivadas, ruta='trabajos')
        self.assertEqual(row['status'], 'error')
        self.assertIn('no conserva el pedido de consumo', row['error'])
        self.assertNotIn('predicted', row)

    def test_prueba_compara_el_recuento_sin_confundirlo_con_la_categoria(self):
        self.server.app = FastAPI()
        self.server.CACHE_MAX = self.server.RATE_LIMITE = 0
        self.server.API_TOKEN = ''
        caso = self.case | {'expected': {'retiro_escombros': True}, 'recuento_esperado': {'bolsas': 5}}
        solicitud = Request('https://openrouter.example/chat/completions', data=b'{"model":"lector"}')
        huella = R.fingerprint({'endpoint': solicitud.full_url, 'body': {'model': 'lector'}})

        @self.server.app.post('/clasificar')
        def clasificar():
            lectura = R.V._pedir_http(solicitud, 10, 20)
            return self.public | {
                'modo': 'prueba', 'modo_version': 'version-prueba', 'analisis_estado': lectura['estado'],
                'problemas': [{'key': 'retiro_escombros', 'bolsas': lectura['bolsas']}],
                'hay_problema': True, 'hay_reclamo': True,
            }

        def evaluar(referencia, bolsas, estado='completo'):
            return P.evaluate_prueba(referencia, self.root, self.identity, self.server,
                                     {huella: {'bolsas': bolsas, 'estado': estado}})

        original = evaluar(caso, 5)
        cambiado = evaluar(caso, 4)
        self.assertEqual((original['status'], cambiado['status']), ('pass', 'fail'))
        self.assertEqual(cambiado['recuento_observado'], {'bolsas': 4})
        self.assertEqual(cambiado['diferencias_recuento'], ['bolsas'])
        self.assertEqual((cambiado['missing'], cambiado['unexpected']), ([], []))
        comparacion = R.compare({'rows': [original]}, {'rows': [cambiado]})
        self.assertEqual(comparacion['regressions'], [
            {'case': 'test', 'model': 'final-api', 'stage': 'pipeline', 'kind': 'recuento', 'category': 'bolsas'}])

        incompleto = evaluar(caso, 5, estado='parcial')
        self.assertIsNone(incompleto['diferencias_recuento'])
        referencia_distinta = evaluar(caso | {'recuento_esperado': {'bolsas': [3, 5]}}, 4)
        self.assertEqual(referencia_distinta['status'], 'pass')
        caso.pop('recuento_esperado')
        sin_recuento = evaluar(caso, 4)
        self.assertEqual(sin_recuento['status'], 'pass')
        self.assertNotIn('diferencias_recuento', sin_recuento)
        for row in (incompleto, referencia_distinta, sin_recuento):
            self.assertEqual(R.compare({'rows': [original]}, {'rows': [row]})['comparable'], 0)

    def test_prueba_compara_el_problema_principal_por_separado(self):
        self.server.app = FastAPI()
        self.server.CACHE_MAX = self.server.RATE_LIMITE = 0
        self.server.API_TOKEN = ''
        caso = self.case | {'expected': {'barrido': True, 'reparacion_cordon': True},
                            'principal_esperado': 'reparacion_cordon'}
        solicitud = Request('https://openrouter.example/chat/completions', data=b'{"model":"lector"}')
        huella = R.fingerprint({'endpoint': solicitud.full_url, 'body': {'model': 'lector'}})

        @self.server.app.post('/clasificar')
        def clasificar():
            lectura = R.V._pedir_http(solicitud, 10, 20)
            return self.public | {
                'modo': 'prueba', 'modo_version': 'version-prueba', 'analisis_estado': 'completo',
                'problemas': [{'key': 'barrido'}, {'key': 'reparacion_cordon'}],
                'hay_problema': True, 'hay_reclamo': True, 'problema_principal': lectura['principal'],
            }

        def evaluar(referencia, principal):
            return P.evaluate_prueba(referencia, self.root, self.identity, self.server,
                                     {huella: {'principal': principal}})

        cordon = {'estado': 'seleccionado', 'criterio': 'escena', 'key': 'reparacion_cordon'}
        barrido = cordon | {'key': 'barrido'}
        original, cambiado = evaluar(caso, cordon), evaluar(caso, barrido)
        self.assertEqual((original['status'], cambiado['status']), ('pass', 'fail'))
        self.assertEqual(cambiado['principal_observado'], 'barrido')
        self.assertTrue(cambiado['diferencia_principal'])
        self.assertEqual((cambiado['missing'], cambiado['unexpected']), ([], []))
        comparacion = R.compare({'rows': [original]}, {'rows': [cambiado]})
        self.assertEqual(comparacion['regressions'], [
            {'case': 'test', 'model': 'final-api', 'stage': 'pipeline',
             'kind': 'prioridad', 'category': 'reparacion_cordon'}])

        sin_respuesta = evaluar(caso, None)
        self.assertEqual(sin_respuesta['status'], 'error')
        self.assertIsNone(sin_respuesta['diferencia_principal'])
        sin_referencia = evaluar(caso | {'principal_esperado': None}, barrido)
        self.assertEqual(sin_referencia['status'], 'pass')
        self.assertNotIn('diferencia_principal', sin_referencia)
        indeterminado = evaluar(caso | {'principal_esperado': 'indeterminado'},
                                {'estado': 'indeterminado', 'key': None})
        self.assertEqual(indeterminado['status'], 'pass')
        for row in (sin_respuesta, sin_referencia, indeterminado):
            self.assertEqual(R.compare({'rows': [original]}, {'rows': [row]})['comparable'], 0)

        solo_principal = caso | {'expected': {}}
        self.assertEqual(R.select([solo_principal], 'all'), [])
        self.assertEqual(R.select([solo_principal], 'all', incluir_campos_de_prueba=True), [solo_principal])
        a, b = evaluar(solo_principal, cordon), evaluar(solo_principal, barrido)
        self.assertEqual(R.compare({'rows': [a]}, {'rows': [b]})['regressions'], comparacion['regressions'])

    def test_prueba_selecciona_el_recuento_y_el_respaldo_sin_etiquetas_de_categoria(self):
        sin_categorias = self.case | {'expected': {'recoleccion': None}}
        recuento = sin_categorias | {'recuento_esperado': {'bolsas_fuentes': 2}}
        respaldo = sin_categorias | {'contexto_esperado': {'solicitudes_sugeridas': []}}
        casos = [recuento, respaldo, sin_categorias]
        self.assertEqual(R.select(casos, 'all', incluir_campos_de_prueba=True), [recuento, respaldo])
        self.assertEqual(R.select(casos, 'all'), [])

    def test_prueba_rechaza_un_recuento_invalido_antes_de_cargar_el_servidor(self):
        invalido = self.case | {'recuento_esperado': {'bolsas_fuentes': 3}}
        (self.root / 'cases.json').write_text(json.dumps({'cases': [invalido]}))
        with (patch.object(R, 'load_env'), patch.object(P.importlib, 'reload'),
              patch.object(P, 'load_server') as cargar, redirect_stderr(io.StringIO()) as stderr,
              self.assertRaises(SystemExit) as salida):
            P.main(['run', '--private', str(self.root), '--ids', self.case['id'], '--modo', 'prueba'])
        self.assertEqual(salida.exception.code, 2)
        self.assertIn('recuento', stderr.getvalue())
        cargar.assert_not_called()

    def test_prueba_valida_las_etiquetas_de_todo_el_lote_antes_de_cargar_el_servidor(self):
        segundo = self.case | {'id': 'segundo', 'aliases': ['segundo'],
                               'expected': {'recoleccion': True, 'retiro_muebles': 'duda'}}
        (self.root / 'cases.json').write_text(json.dumps({'cases': [self.case, segundo]}))
        with (patch.object(R, 'load_env'), patch.object(P.importlib, 'reload'),
              patch.object(P, 'load_server') as cargar, redirect_stderr(io.StringIO()) as stderr,
              self.assertRaises(SystemExit) as salida):
            P.main(['run', '--private', str(self.root), '--ids', self.case['id'], 'segundo', '--modo', 'prueba'])
        self.assertEqual(salida.exception.code, 2)
        self.assertIn('segundo: La referencia debe tener etiquetas true, false o null', stderr.getvalue())
        cargar.assert_not_called()

    def test_medicion_verifica_las_fotos_y_predicciones_antes_de_cargar_el_servidor(self):
        foto = self.root / 'segunda.jpg'
        P.Image.new('RGB', (40, 40), 'red').save(foto)
        original = foto.read_bytes()
        segundo = self.case | {'id': 'segundo', 'aliases': ['segundo'], 'photo': foto.name,
                               'sha256': R.D.digest(original)}
        (self.root / 'cases.json').write_text(json.dumps({'cases': [self.case, segundo]}))
        salida = self.root / 'lote.json'
        argumentos = ['run', '--private', str(self.root), '--ids', self.case['id'], 'segundo',
                      '--modo', 'prueba', '--live', '--budget', '0.02', '--out', str(salida)]
        prediccion = P.local_path(self.root, segundo, self.identity)
        casos = [
            (b'foto alterada', None, 'Photo SHA-256 mismatch.'),
            (original, None, 'Missing current local prediction.'),
            (original, [], 'La predicción local no contiene un resultado válido.'),
        ]
        for datos, resultado, mensaje in casos:
            with self.subTest(mensaje=mensaje):
                foto.write_bytes(datos)
                if resultado is not None:
                    R.D.write_json(prediccion, {'identity': self.identity, 'photo_sha256': segundo['sha256'],
                                                 'prediction': resultado})
                with (patch.object(R, 'load_env'), patch.object(P.importlib, 'reload'),
                      patch.object(R.V, 'api_key', return_value='simulada'),
                      patch.object(P, 'local_identity', return_value=self.identity),
                      patch.object(P, 'load_server') as cargar, redirect_stderr(io.StringIO()) as stderr,
                      self.assertRaises(SystemExit) as error):
                    P.main(argumentos)
                self.assertEqual(error.exception.code, 2)
                self.assertIn(f'segundo: {mensaje}', stderr.getvalue())
                cargar.assert_not_called()
                self.assertFalse(salida.with_suffix('.consumo.json').exists())

    def test_prueba_registra_la_regresion_del_respaldo_sin_cambiar_las_categorias(self):
        self.server.app = FastAPI()
        self.server.CACHE_MAX = self.server.RATE_LIMITE = 0
        self.server.API_TOKEN = ''
        caso = self.case | {'contexto_esperado': {'categorias_contexto': {'recoleccion': 'neutral'}}}
        cuerpo = {'model': 'lector'}
        solicitud = Request('https://openrouter.example/chat/completions', data=json.dumps(cuerpo).encode())
        huella = R.fingerprint({'endpoint': solicitud.full_url, 'body': cuerpo})

        @self.server.app.post('/clasificar')
        def clasificar():
            lectura = R.V._pedir_http(solicitud, 10, 20)
            return self.public | {
                'modo': 'prueba', 'modo_version': 'version-prueba', 'analisis_estado': 'completo',
                'problemas': [{'key': 'recoleccion'}], 'hay_problema': True, 'hay_reclamo': True,
                'categorias_contexto': [{'key': 'recoleccion', 'respaldo_visual': lectura['respaldo']}],
            }

        def evaluar(respaldo):
            return P.evaluate_prueba(caso, self.root, self.identity, self.server, {huella: {'respaldo': respaldo}})

        original, cambiado, pendiente = map(evaluar, ('neutral', 'compatible', None))
        self.assertEqual([r['status'] for r in (original, cambiado, pendiente)], ['pass', 'fail', 'error'])
        self.assertEqual((cambiado['missing'], cambiado['unexpected']), ([], []))
        self.assertIsNone(pendiente['diferencias_contexto'])
        comparacion = R.compare({'rows': [original]}, {'rows': [cambiado]})
        self.assertEqual(comparacion['regressions'], [
            {'case': 'test', 'model': 'final-api', 'stage': 'pipeline', 'kind': 'contexto',
             'category': 'categorias_contexto.recoleccion'}])
        self.assertEqual(R.compare({'rows': [original]}, {'rows': [pendiente]})['comparable'], 0)

    def test_replay_prueba_drena_el_trabajo_si_vence_la_espera(self):
        self.server.app = FastAPI()
        self.server.CACHE_MAX = self.server.RATE_LIMITE = 0
        self.server.API_TOKEN = ''
        solicitud = Request('https://openrouter.example/chat/completions', data=b'{"model":"lector"}')
        huella = R.fingerprint({'endpoint': solicitud.full_url, 'body': {'model': 'lector'}})
        archivadas = {huella: {'usage': {'cost': .003}}}
        continuar = threading.Event()

        @self.server.app.post('/trabajos/', status_code=202)
        async def crear():
            def leer():
                if continuar.wait(timeout=2):
                    R.V._pedir_http(solicitud, 10, 20)
            self.server._pool.submit(leer)
            return {'estado': 'en_cola'}

        def vencer(*_, medicion=None):
            self.assertIsNone(medicion)
            continuar.set()
            raise TimeoutError('Venció la espera del control.')

        with patch.object(P, '_esperar_trabajo_prueba', side_effect=vencer), \
                patch.object(R.V, '_pedir_http', side_effect=AssertionError('transporte externo')) as original:
            row = P.evaluate_prueba(self.case, self.root, self.identity, self.server, archivadas, ruta='trabajos')
            original.assert_not_called()
        self.assertEqual(row['status'], 'error')
        self.assertEqual([r['source'] for r in row['requests']], ['archivo'])

    def test_replay_prueba_rejects_conflicting_responses_for_the_same_request(self):
        huella = 'c' * 64
        path = self.root / 'respuestas.json'
        path.write_text(json.dumps({'respuestas': {huella: {'id': 'primera'}}}))
        baseline = {'rows': [{'requests': [{'request_hash': huella, 'raw': {'id': 'segunda'}}]}]}
        with self.assertRaisesRegex(ValueError, 'respuestas distintas'):
            P.load_prueba_responses(path, baseline)

    def test_medicion_conserva_el_pedido_si_se_pierde_la_respuesta_directa(self):
        self.server.app = FastAPI()
        self.server.CACHE_MAX = 10
        self.server.RATE_LIMITE = 60
        self.server.API_TOKEN = 'test-only-token'
        solicitud = Request('https://openrouter.example/chat/completions', data=b'{"model":"lector"}')
        guardado = []

        @self.server.app.post('/clasificar', status_code=502)
        async def clasificar():
            R.V.costo_reset()
            dato = R.V._pedir_http(solicitud, 10, 20)
            R.V._registrar_uso('lector', 'prueba_inicio', None, 1, P.time.monotonic(), dato)
            guardado.append(json.loads(medicion.manifiesto.read_text())['casos'])
            return {'detail': 'Se perdió la respuesta'}

        medicion = P.MedicionPrueba(self.root / 'lote.json', .02, 'version-prueba', 'clasificar', 5)
        self.assertTrue(medicion.admite_foto())

        def proveedor(*_):
            [caso] = json.loads(medicion.manifiesto.read_text())['casos']
            [respuesta] = caso['respuestas']
            esperado = {'pedido': R.V._costo_foto.get()['id'], 'intentos': None}
            self.assertEqual(respuesta['consumo_api'], esperado)
            return {'id': 'gen-sintetica', 'usage': {'cost': .003}}

        with patch.object(R.V, '_pedir_http', side_effect=proveedor):
            row = medicion.medir(self.case, self.root, self.identity, self.server)
        consumo = row['consumo_facturado']
        self.assertEqual(row['status'], 'error')
        self.assertEqual(consumo['costo_conocido_usd'], '0.003')
        self.assertFalse(consumo['conciliado'])
        self.assertFalse(medicion.admite_foto())
        intentos = [json.loads(l) for l in medicion.intentos.read_text().splitlines()]
        self.assertEqual([i['estado'] for i in intentos], ['iniciado', 'recibido'])
        self.assertEqual({i['pedido'] for i in intentos}, {consumo['pedidos'][0]['pedido']})
        self.assertEqual(consumo['pedidos'][0]['intentos_esperados'], None)
        [casos] = guardado
        _, [recuperado] = P.C.conciliar(casos, P.C.leer_uso([medicion.uso]))
        self.assertEqual(recuperado, consumo)
        with self.assertRaises(FileExistsError):
            P.MedicionPrueba(self.root / 'lote.json', .02, 'version-prueba', 'clasificar', 5)

    def test_medicion_guarda_la_aceptacion_antes_de_consultar_la_cola(self):
        self.server.app = FastAPI()
        self.server.CACHE_MAX = self.server.RATE_LIMITE = 0
        self.server.API_TOKEN = ''
        medicion = P.MedicionPrueba(self.root / 'lote.json', .02, 'version-prueba', 'trabajos', 5)
        aceptado = {'trabajo': 'trabajo-enviado', 'estado': 'en_cola', 'modo': 'prueba',
                    'modo_version': 'version-prueba', 'consumo_api': {'pedido': 'pedido-aceptado', 'intentos': None}}
        solicitud = Request('https://openrouter.example/chat/completions', data=b'{"model":"lector"}')

        @self.server.app.post('/trabajos/', status_code=202)
        async def crear():
            with R.V.modos.usar(self.server.PERFILES['prueba'], pedido_consumo='pedido-aceptado'):
                R.V.costo_reset()
                dato = R.V._pedir_http(solicitud, 10, 20)
                R.V._registrar_uso('lector', 'prueba_inicio', None, 1, P.time.monotonic(), dato)
            return aceptado

        @self.server.app.get('/trabajos/', status_code=404)
        async def leer():
            guardada = json.loads(medicion.manifiesto.read_text())['casos'][0]['respuestas']
            self.assertEqual(guardada, [aceptado])
            return {'detail': 'Se perdió el trabajo'}

        with patch.object(R.V, '_pedir_http', return_value={'id': 'gen-sintetica', 'usage': {'cost': .002}}):
            row = medicion.medir(self.case, self.root, self.identity, self.server)
        self.assertEqual(row['status'], 'error')
        self.assertEqual(json.loads(medicion.manifiesto.read_text())['casos'][0]['respuestas'], [aceptado])
        self.assertEqual(row['consumo_facturado']['pedidos'][0]['pedido'], 'pedido-aceptado')
        self.assertEqual(row['consumo_facturado']['costo_conocido_usd'], '0.002')
        self.assertFalse(medicion.admite_foto())

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

    def test_cache_rechazada_en_pasada_dirigida_se_conserva_y_no_se_puntua(self):
        raw = {'usage': {'cost': .001}, 'choices': [{'finish_reason': 'stop',
               'message': {'content': '{"required_field": true}'}}]}
        first = R.Transport(self.root / 'cache', live=True,
                            opener=lambda *a, **k: io.BytesIO(json.dumps(raw).encode()))
        first('model', [], etapa='scope_test')
        path = next((self.root / 'cache').glob('*.json'))
        record = json.loads(path.read_text())
        record['raw']['choices'][0]['finish_reason'] = 'length'
        path.write_text(json.dumps(record))
        before = path.read_bytes()
        def process(*args):
            def parse(model):
                return json.loads(R.V._llamar(model, [], etapa='scope_test'))['required_field']
            R.V._map_modelos(['model'], parse)
            return self.internal
        self.server.procesar = process
        for live in [False, True]:
            def no_enviar(*args, **kwargs):
                self.fail('Una caché rechazada no debe provocar un envío.')
            journal = self.root / f'pipeline-{live}.jsonl'
            transport = R.Transport(self.root / 'cache', live=live, opener=no_enviar, journal=journal)
            row = P.evaluate(self.case, self.root, self.identity, self.server, transport, True)
            self.assertEqual(row['status'], 'cache_rejected')
            self.assertEqual(row['response']['source'], 'cache_rejected')
            self.assertIn(str(path), row['error'])
            self.assertEqual(row['requests'][0]['error_type'], 'CacheRejected')
            self.assertEqual(row['cost_usd'], 0)
            self.assertEqual(transport.meter.calls, 0)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(json.loads(journal.read_text())['state'], 'cache_rejected')

    def test_cache_rechazada_en_prevalidacion_conserva_motivo_y_no_inicia_pipeline(self):
        raw = {'usage': {'cost': .001}, 'choices': [{'finish_reason': 'stop',
               'message': {'content': '{"categorias": []}'}}]}
        first = R.Transport(self.root / 'cache', live=True,
                            opener=lambda *a, **k: io.BytesIO(json.dumps(raw).encode()))
        model = R.V.VERIFICADORES[0]
        R.evaluate_case(self.case, model, self.root, first, {})
        path = next((self.root / 'cache').glob('*.json'))
        record = json.loads(path.read_text())
        record['raw']['choices'][0]['finish_reason'] = 'length'
        path.write_text(json.dumps(record))
        before = path.read_bytes()
        def no_procesar(*args):
            self.fail('La prevalidación rechazada debe impedir procesar la foto.')
        self.server.procesar = no_procesar
        journal = self.root / 'prevalidacion.jsonl'
        transport = R.Transport(self.root / 'cache', live=True, journal=journal)
        row = P.evaluate(self.case, self.root, self.identity, self.server, transport)
        self.assertEqual(row['status'], 'cache_rejected')
        self.assertIn(str(path), row['error'])
        self.assertEqual(row['requests'][0]['source'], 'cache_rejected')
        self.assertEqual(row['cost_usd'], 0)
        self.assertEqual(transport.meter.calls, 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads(journal.read_text())['state'], 'cache_rejected')

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
