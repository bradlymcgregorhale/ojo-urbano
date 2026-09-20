"""Conteo por foto, reintentos, llamadas paralelas y respuesta pública."""
import concurrent.futures
import copy
import io
import json
import sys
import threading
import unittest
import urllib.error
from contextlib import ExitStack
from unittest.mock import patch

from PIL import Image
import especialista_contenedores as E
import verificador as V

_LLAMAR_REAL = V._llamar


def respuesta(uso, contenido='{"ok":true}'):
    return {"model": E.MODELO, "usage": uso, "choices": [
        {"finish_reason": "stop", "message": {"content": contenido}}]}


class TokensApi(unittest.TestCase):
    def setUp(self):
        V.tokens_reset()
        V.costo_reset()
        self.pila = ExitStack()
        self.pila.enter_context(patch.object(V, 'OPENROUTER_LOG_USO', False))
        self.pila.enter_context(patch.object(V, 'api_key', return_value='prueba'))
        self.pila.enter_context(patch.object(V, '_llamar', _LLAMAR_REAL))

    def tearDown(self):
        V.tokens_total()
        self.pila.close()

    def test_total_y_detalles_sin_duplicar(self):
        V._tokens_sumar(respuesta({'total_tokens': 120, 'cost': None,
                                  'prompt_tokens_details': {'cached_tokens': 90},
                                  'completion_tokens_details': {'reasoning_tokens': 10}}))
        V._tokens_sumar(respuesta({'prompt_tokens': 50, 'completion_tokens': 7}))
        self.assertEqual(V.tokens_total(), {'tokens_api': 177, 'tokens_api_completos': True})
        V.tokens_reset()
        self.assertEqual(V.tokens_total(), {'tokens_api': 0, 'tokens_api_completos': True})

    def test_desglose_entrada_y_salida_por_foto(self):
        V._tokens_sumar(respuesta({'total_tokens': 120, 'prompt_tokens': 100, 'completion_tokens': 20,
                                  'completion_tokens_details': {'reasoning_tokens': 10}}))
        V._tokens_sumar(respuesta({'prompt_tokens': 50, 'completion_tokens': 7}))
        self.assertEqual(V.tokens_total(desglose=True), {
            'tokens_api': 177, 'tokens_api_completos': True,
            'tokens_entrada': 150, 'tokens_salida': 27, 'tokens_desglose_completo': True})
        V.tokens_reset()
        self.assertEqual(V.tokens_total(desglose=True), {
            'tokens_api': 0, 'tokens_api_completos': True,
            'tokens_entrada': 0, 'tokens_salida': 0, 'tokens_desglose_completo': True})
        self.assertEqual(V.tokens_total(desglose=True), {
            'tokens_api': 0, 'tokens_api_completos': True,
            'tokens_entrada': 0, 'tokens_salida': 0, 'tokens_desglose_completo': True})

    def test_total_sin_desglose_no_inventa_entrada_ni_salida(self):
        for uso in ({'total_tokens': 13}, {'total_tokens': 13, 'prompt_tokens': 10},
                    {'total_tokens': 13, 'prompt_tokens': 10, 'completion_tokens': -1},
                    {'total_tokens': 13, 'prompt_tokens': 10.0, 'completion_tokens': 3}):
            with self.subTest(uso=uso):
                V.tokens_reset()
                V._tokens_sumar(respuesta({'prompt_tokens': 4, 'completion_tokens': 1}))
                V._tokens_sumar(respuesta(uso))
                self.assertEqual(V.tokens_total(desglose=True), {
                    'tokens_api': 18, 'tokens_api_completos': True,
                    'tokens_entrada': 4, 'tokens_salida': 1, 'tokens_desglose_completo': False})
        # Sin desglose pedido, la salida conserva las dos claves de siempre.
        V.tokens_reset()
        V._tokens_sumar(respuesta({'prompt_tokens': 4, 'completion_tokens': 1}))
        self.assertEqual(V.tokens_total(), {'tokens_api': 5, 'tokens_api_completos': True})

    def test_faltantes_y_valores_invalidos_no_inventan_consumo(self):
        for uso in (None, {}, {'total_tokens': True}, {'total_tokens': -1},
                    {'total_tokens': '12'}, {'total_tokens': 1.5},
                    {'prompt_tokens': 10}, {'prompt_tokens': 10, 'completion_tokens': False}):
            with self.subTest(uso=uso):
                V.tokens_reset()
                V._tokens_sumar(respuesta({'total_tokens': 13}))
                V._tokens_sumar(respuesta(uso))
                self.assertEqual(V.tokens_total(), {'tokens_api': 13, 'tokens_api_completos': False})

    def test_cuenta_cada_reintento_de_transporte(self):
        with patch.object(V, '_pedir_http', side_effect=[
                urllib.error.URLError('primer fallo simulado'),
                urllib.error.URLError('fallo simulado'),
                respuesta({'total_tokens': 23, 'cost': .002})]) as http:
            V.costo_reset()
            self.assertEqual(V._llamar('m', []), '{"ok":true}')
            self.assertEqual(http.call_count, 3)
            self.assertEqual(V.tokens_total(), {'tokens_api': 23, 'tokens_api_completos': False})
            self.assertEqual(V.costo_total(), .002)

    def test_respuesta_inutilizable_no_reenvia_y_conserva_consumo(self):
        for finish, content, reasoning in [
                ('length', '{"ok":true}', None),
                ('length', None, '{"ok":true}'),
                ('stop', None, '{"ok":true}'),
                ('stop', '   ', '{"ok":true}'),
                ('stop', 'sin JSON', None),
                (None, '{"ok":true}', None),
                ('desconocido', '{"ok":true}', None)]:
            with self.subTest(finish=finish, content=content):
                V.tokens_reset(); V.costo_reset()
                data = respuesta({'total_tokens': 11, 'cost': .001}, content)
                data['choices'][0]['finish_reason'] = finish
                data['choices'][0]['message']['reasoning'] = reasoning
                with patch.object(V, '_pedir_http', return_value=data) as http, patch.object(V, '_registrar_uso') as log:
                    with self.assertRaises(V.RespuestaNoUtilizableError):
                        V._llamar('m', [], etapa='repregunta_objeto')
                self.assertEqual(http.call_count, 1)
                self.assertEqual(log.call_count, 1)
                self.assertIsInstance(log.call_args.args[-1], V.RespuestaNoUtilizableError)
                self.assertEqual(V.tokens_total(), {'tokens_api': 11, 'tokens_api_completos': True})
                self.assertEqual(V.costo_total(), .001)

    def test_cuerpo_http_ilegible_no_reenvia_ni_inventa_consumo(self):
        for body, error in [(b'{"choices":', json.JSONDecodeError),
                            (b'\xff', UnicodeDecodeError)]:
            with self.subTest(body=body):
                V.tokens_reset(); V.costo_reset()
                with patch.object(V, '_opener_con_caja') as opener, patch.object(V, '_registrar_uso') as log:
                    opener.return_value.open.return_value = io.BytesIO(body)
                    with self.assertRaises(error):
                        V._llamar('m', [], etapa='verificar_uno')
                self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertEqual(log.call_count, 1)
                self.assertIsNone(log.call_args.args[-2])
                self.assertIsInstance(log.call_args.args[-1], error)
                self.assertEqual(V.tokens_total(), {'tokens_api': 0, 'tokens_api_completos': False})
                self.assertEqual(V.costo_total(), 0)

    def test_formas_invalidas_y_terminacion_nativa(self):
        base = respuesta({'total_tokens': 0, 'cost': 0})
        invalid = [None, [], {}, {'choices': []}, {'choices': [None]},
                   {'choices': [{}, {}]}, dict(base, error={'message': 'error'})]
        for native in ['max_tokens', 'MAX_OUTPUT_TOKENS', 'tool_use', 'pause_turn',
                       'refusal', 'model_context_window_exceeded', {}, False]:
            data = copy.deepcopy(base); data['choices'][0]['native_finish_reason'] = native
            invalid.append(data)
        for message in [None, [], {'content': 4}, {'content': {}},
                        {'content': '{"ok":true}', 'tool_calls': [{}]},
                        {'content': '{"ok":true}', 'refusal': 'rechazado'}]:
            data = copy.deepcopy(base); data['choices'][0]['message'] = message
            invalid.append(data)
        data = copy.deepcopy(base); data['choices'][0]['error'] = {'code': 503}
        invalid.append(data)
        data = copy.deepcopy(base); data['choices'][0]['finish_reason'] = 'error'
        invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), patch.object(V, '_pedir_http', return_value=data) as http:
                with self.assertRaises(V.RespuestaNoUtilizableError):
                    V._llamar('m', [])
                self.assertEqual(http.call_count, 1)
        for native in [None, '', 'stop', 'STOP', 'completed', 'end_turn', 'terminacion_proveedor']:
            data = copy.deepcopy(base); data['choices'][0]['native_finish_reason'] = native
            with self.subTest(native=native), patch.object(V, '_pedir_http', return_value=data):
                self.assertEqual(V._llamar('m', []), '{"ok":true}')

    def test_razonamiento_sin_final_no_aporta_voto_dirigido(self):
        data = respuesta({'total_tokens': 12, 'cost': .002}, None)
        data['choices'][0].update(finish_reason='length')
        data['choices'][0]['message']['reasoning'] = json.dumps(
            {'veredicto': 'presente', 'ubicacion': 'centro', 'evidencia': 'objeto visible'})
        with patch.object(V, '_pedir_http', return_value=data) as http, patch.object(
                V, '_imagen_data_url', return_value='sin_imagen'):
            readings, failed = V._repregunta_objeto(None, 'objeto de prueba', ['m'], False)
        self.assertEqual(http.call_count, 1)
        self.assertEqual(readings, [])
        self.assertTrue(failed)

    def test_especialista_cuenta_uso_y_distingue_error_previo_al_envio(self):
        with patch.object(E, 'solicitud', return_value={'messages': []}), patch.object(
                V, '_pedir_http', return_value=respuesta({'total_tokens': 51})):
            V.verificar_contenedores(b'foto')
        self.assertEqual(V.tokens_total(), {'tokens_api': 51, 'tokens_api_completos': True})
        V.tokens_reset()
        with patch.object(E, 'solicitud', side_effect=FileNotFoundError), patch.object(V, '_pedir_http') as http:
            V.verificar_contenedores(b'foto')
            http.assert_not_called()
        self.assertEqual(V.tokens_total(), {'tokens_api': 0, 'tokens_api_completos': True})
        V.tokens_reset()
        with patch.object(E, 'solicitud', return_value={}), patch.object(V, '_pedir_http', side_effect=TimeoutError):
            V.verificar_contenedores(b'foto')
        self.assertEqual(V.tokens_total(), {'tokens_api': 0, 'tokens_api_completos': False})

    def test_fotos_simultaneas_con_llamadas_paralelas_no_mezclan_tokens(self):
        barrera = threading.Barrier(2)

        def foto(base):
            V.tokens_reset()
            barrera.wait(timeout=5)
            V._map_modelos([str(base), str(base + 1)], lambda m: V._llamar(m, []))
            return V.tokens_total()

        def http(req, *args):
            return respuesta({'total_tokens': int(json.loads(req.data)['model'])})

        with patch.object(V, '_pedir_http', side_effect=http), concurrent.futures.ThreadPoolExecutor(2) as pool:
            resultados = list(pool.map(foto, [10, 100]))
        self.assertEqual(resultados, [{'tokens_api': 21, 'tokens_api_completos': True},
                                      {'tokens_api': 201, 'tokens_api_completos': True}])
        self.assertEqual(V.tokens_total(), {'tokens_api': 0, 'tokens_api_completos': True})

    def test_fotos_simultaneas_no_mezclan_el_desglose(self):
        barrera = threading.Barrier(2)

        def foto(base):
            V.tokens_reset()
            barrera.wait(timeout=5)
            V._map_modelos([str(base), str(base + 1)], lambda m: V._llamar(m, []))
            return V.tokens_total(desglose=True)

        def http(req, *args):
            n = int(json.loads(req.data)['model'])
            # La foto 100 tiene un intento con total pero sin desglose.
            if n == 101:
                return respuesta({'total_tokens': n})
            return respuesta({'prompt_tokens': n, 'completion_tokens': 1})

        with patch.object(V, '_pedir_http', side_effect=http), concurrent.futures.ThreadPoolExecutor(2) as pool:
            resultados = list(pool.map(foto, [10, 100]))
        self.assertEqual(resultados, [
            {'tokens_api': 23, 'tokens_api_completos': True,
             'tokens_entrada': 21, 'tokens_salida': 2, 'tokens_desglose_completo': True},
            {'tokens_api': 202, 'tokens_api_completos': True,
             'tokens_entrada': 100, 'tokens_salida': 1, 'tokens_desglose_completo': False}])

    def test_costos_superpuestos_y_aporte_tardio(self):
        import contextvars
        a, b = contextvars.Context(), contextvars.Context()
        a.run(V.costo_reset)
        a.run(V._costo_sumar, {'cost': .001})
        b.run(V.costo_reset)
        b.run(V._costo_sumar, {'cost': .002})
        a.run(V._costo_sumar, {'cost': .003})
        self.assertEqual(b.run(V.costo_total), .002)
        self.assertEqual(a.run(V.costo_total), .004)
        original = a.run(V._costo_foto.get)['id']
        salida = io.StringIO()
        with patch.object(sys, 'stderr', salida):
            a.run(V._costo_sumar, {'cost': .005})
        self.assertEqual(a.run(V.costo_total), .004)
        self.assertEqual(b.run(V.costo_total), .002)
        registro = json.loads(salida.getvalue())
        self.assertEqual(registro, {'evento': 'costo_tardio', 'pedido': original, 'cost': .005})

    def test_costos_en_pools_de_fotos_simultaneas(self):
        barrera = threading.Barrier(2)
        def foto(base):
            V.costo_reset()
            barrera.wait(timeout=5)
            V._map_modelos([base, base * 2], lambda costo: V._costo_sumar({'cost': costo}))
            return V.costo_total()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(list(pool.map(foto, [.001, .01])), [.003, .03])

    def test_costo_faltante_no_inventa_valores(self):
        V.costo_reset()
        for uso in (None, {}, {'cost': None}, {'cost': True}, {'cost': '2'},
                    {'cost': float('nan')}, {'cost': float('inf')}, {'cost': -.1}):
            V._costo_sumar(uso)
        self.assertEqual(V.costo_total(), 0)

    @unittest.skipUnless('servidor' in sys.modules, 'Ejecutar con pruebas.py, sin cargar los pesos')
    def test_techo_libera_cupo_sin_mezclar_costos(self):
        import asyncio
        import servidor as S
        inicio, terminar = threading.Event(), threading.Event()
        def proceso(datos, *_):
            V.costo_reset()
            V._costo_sumar({'cost': .001 if datos == b'a' else .002})
            if datos == b'a':
                inicio.set()
                if not terminar.wait(4):
                    raise AssertionError('La prueba no liberó el hilo')
                V._costo_sumar({'cost': .003})
            return {'costo_api': V.costo_total()}
        async def ejecutar():
            S._cupos.acquire()
            a = asyncio.create_task(S._correr_con_cupo(b'a', '', 'auto', 'a'))
            try:
                for _ in range(100):
                    if inicio.is_set() and S._cupos.acquire(blocking=False):
                        break
                    await asyncio.sleep(.01)
                else:
                    self.fail('El techo no devolvió el cupo')
                self.assertEqual(S._perdidos['vivos'], 1)
                b = await S._correr_con_cupo(b'b', '', 'auto', 'b')
                self.assertEqual(b['costo_api'], .002)
            finally:
                terminar.set()
                resultado = await a
            self.assertEqual(resultado['costo_api'], .004)
            self.assertEqual(S._perdidos['vivos'], 0)
        with concurrent.futures.ThreadPoolExecutor(2) as pool, patch.multiple(
                S, procesar=proceso, TECHO_TRABAJO=.04, _pool=pool,
                _cupos=threading.BoundedSemaphore(1),
                _perdidos={'lock': threading.Lock(), 'vivos': 0, 'total': 0}), patch.object(
                S, '_cache_guardar'):
            asyncio.run(ejecutar())

    @unittest.skipUnless('servidor' in sys.modules, 'Ejecutar con pruebas.py, sin cargar los pesos')
    def test_respuesta_publica_incluye_todas_las_etapas_y_cache(self):
        import servidor as S
        from fastapi.testclient import TestClient
        b = io.BytesIO()
        Image.new('RGB', (20, 20)).save(b, format='JPEG')
        local = {'predichas': [], 'probabilidades': [], 'gravedad': {}}
        veri = {'activa': True, 'confirmadas': [], 'en_duda': [],
                'categorias_contexto': [], 'descripcion': '', 'verificadores': []}
        enviados = []

        def llamar(etapa):
            V._llamar(etapa, [], etapa=etapa)

        def verificar(*args):
            V._map_modelos(['primero', 'segundo', 'tercero'], llamar)
            llamar('arbitro')
            return copy.deepcopy(veri)

        def alcance(*args):
            V._map_modelos(['alcance1', 'alcance2', 'alcance3'], llamar)
            return {}

        def obra(*args):
            llamar('obra')
            return {}

        def http(req, *args):
            enviados.append(json.loads(req.data)['model'])
            return respuesta({'total_tokens': 10, 'cost': .001}, json.dumps(
                {'types': [], 'uncertain': False, 'evidence': ''}))

        ajustes = [(V, 'disponible', lambda: True), (V, '_pedir_http', http),
                   (V, 'verificar', verificar), (V, 'validar_alcance_escombros', alcance),
                   (V, 'validar_contexto_obra_servicios', obra), (S, '_hay_cuota', lambda: True),
                   (S, 'clasificar_local', lambda _: local), (S, '_calidad_segura', lambda _: None),
                   (S, 'CONTENEDORES_ESPECIALISTA', True), (S, 'API_TOKEN', ''),
                   (S, '_permitir', lambda _: (True, 0)),
                   (S.politica_escombros, 'requiere_revision', lambda *a: True),
                   (S.politica_escombros, 'requiere_obra_servicios', lambda *a: True),
                   (S.politica_escombros, 'aplicar', lambda s, *a: s),
                   (S.politica_escombros, 'aplicar_obra_servicios', lambda s, *a: s),
                   (E, 'solicitud', lambda _: {'model': E.MODELO})]
        with ExitStack() as pila:
            for obj, nombre, valor in ajustes:
                pila.enter_context(patch.object(obj, nombre, valor))
            pila.enter_context(patch.object(S, '_cache', __import__('collections').OrderedDict()))
            pila.enter_context(patch.object(S, 'CACHE_MAX', 10))
            pila.enter_context(patch.object(S, "PERFILES", S.modos.cargar(V, vars(S))))
            with TestClient(S.app) as cliente:
                r = cliente.post('/clasificar', files={'file': ('prueba.jpg', b.getvalue(), 'image/jpeg')})
                self.assertEqual(r.status_code, 200, r.text)
                publica = r.json()
                self.assertEqual(publica['tokens_api'], 90)
                self.assertTrue(publica['tokens_api_completos'])
                # Las respuestas simuladas solo traen el total: el desglose queda en cero e incompleto.
                self.assertEqual((publica['tokens_entrada'], publica['tokens_salida'],
                                  publica['tokens_desglose_completo']), (0, 0, False))
                self.assertEqual(publica['costo_api'], .009)
                self.assertEqual(len(enviados), 9)
                self.assertEqual(publica['contenedores']['tipos'], [])
                repetida = cliente.post('/clasificar', files={'file': ('otra.jpg', b.getvalue(), 'image/jpeg')})
                self.assertEqual(repetida.status_code, 200, repetida.text)
                self.assertEqual(repetida.json()['tokens_api'], 90)
                self.assertEqual((repetida.json()['tokens_entrada'], repetida.json()['tokens_salida'],
                                  repetida.json()['tokens_desglose_completo']), (0, 0, False))
                self.assertEqual(repetida.json()['costo_api'], .009)
                self.assertEqual(len(enviados), 9)
            # Las revisiones no se activan en esta variante sin llamadas.
            with patch.object(S.politica_escombros, 'requiere_revision', return_value=False), patch.object(
                    S.politica_escombros, 'requiere_obra_servicios', return_value=False):
                sin_verificar = S._publica(S.procesar(b.getvalue(), '', '0'))
                self.assertEqual(sin_verificar['tokens_api'], 0)
                self.assertTrue(sin_verificar['tokens_api_completos'])
                self.assertEqual((sin_verificar['tokens_entrada'], sin_verificar['tokens_salida'],
                                  sin_verificar['tokens_desglose_completo']), (0, 0, True))
                self.assertEqual(len(enviados), 9)
