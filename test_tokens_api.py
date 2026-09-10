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

    def test_faltantes_y_valores_invalidos_no_inventan_consumo(self):
        for uso in (None, {}, {'total_tokens': True}, {'total_tokens': -1},
                    {'total_tokens': '12'}, {'total_tokens': 1.5},
                    {'prompt_tokens': 10}, {'prompt_tokens': 10, 'completion_tokens': False}):
            with self.subTest(uso=uso):
                V.tokens_reset()
                V._tokens_sumar(respuesta({'total_tokens': 13}))
                V._tokens_sumar(respuesta(uso))
                self.assertEqual(V.tokens_total(), {'tokens_api': 13, 'tokens_api_completos': False})

    def test_cuenta_cada_reintento_aunque_no_haya_json(self):
        with patch.object(V, '_pedir_http', side_effect=[
                respuesta({'total_tokens': 11, 'cost': .001}, 'sin JSON'),
                urllib.error.URLError('fallo simulado'),
                respuesta({'total_tokens': 23, 'cost': .002})]) as http:
            V.costo_reset()
            self.assertEqual(V._llamar('m', []), '{"ok":true}')
            self.assertEqual(http.call_count, 3)
            self.assertEqual(V.tokens_total(), {'tokens_api': 34, 'tokens_api_completos': False})
            self.assertEqual(V.costo_total(), .003)

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
            with TestClient(S.app) as cliente:
                r = cliente.post('/clasificar', files={'file': ('prueba.jpg', b.getvalue(), 'image/jpeg')})
                self.assertEqual(r.status_code, 200, r.text)
                publica = r.json()
                self.assertEqual(publica['tokens_api'], 90)
                self.assertTrue(publica['tokens_api_completos'])
                self.assertEqual(publica['costo_api'], .009)
                self.assertEqual(len(enviados), 9)
                self.assertEqual(publica['contenedores']['tipos'], [])
                repetida = cliente.post('/clasificar', files={'file': ('otra.jpg', b.getvalue(), 'image/jpeg')})
                self.assertEqual(repetida.status_code, 200, repetida.text)
                self.assertEqual(repetida.json()['tokens_api'], 90)
                self.assertEqual(len(enviados), 9)
            # Las revisiones no se activan en esta variante sin llamadas.
            with patch.object(S.politica_escombros, 'requiere_revision', return_value=False), patch.object(
                    S.politica_escombros, 'requiere_obra_servicios', return_value=False):
                sin_verificar = S._publica(S.procesar(b.getvalue(), '', '0'))
                self.assertEqual(sin_verificar['tokens_api'], 0)
                self.assertTrue(sin_verificar['tokens_api_completos'])
                self.assertEqual(len(enviados), 9)
