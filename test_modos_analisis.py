"""Contrato de perfiles, aislamiento y límites de llamadas sin red ni pesos."""
import concurrent.futures
import asyncio
from contextlib import ExitStack
from dataclasses import replace
import io
import json
import sys
import threading
import unittest
from unittest.mock import patch
from PIL import Image
import modos_analisis as M
import verificador as V

_LLAMAR = V._llamar
_VERIFICAR = V.verificar


def perfiles():
    with patch.multiple(V, ARBITRO='t/arbitro', ARBITRO_VE_FOTO=True, ARBITRO_CONFIRMA=True):
        return M.cargar(V, {'CONTENEDORES_ESPECIALISTA': True}, {
            'VERIFICADORES_BAJO': 't/a', 'VERIFICADORES_MEDIO': 't/a,t/b',
            'VERIFICADORES_ALTO': 't/a,t/b,t/c'})


class Configuracion(unittest.TestCase):
    def test_heredado_ausente_y_vacio_conservan_interpretacion(self):
        for legado in ([], ['t/a'], ['t/a', 't/a'], ['t/a', 't/b', 't/c', 't/d']):
            with patch.object(V, 'VERIFICADORES', legado):
                p = M.cargar(V, {}, {})
            self.assertEqual(p['alto'].verificadores, tuple(legado))
            self.assertIsNone(p['alto'].motivo)
            self.assertEqual(p['bajo'].motivo, 'no_configurado')

    def test_listas_explicitas_invalidas_aisladas(self):
        for valor in ('', 't/a,', 't/a,t/a', 't/a,t/b', 'http://privado', 'sk-secreto'):
            p = M.cargar(V, {}, {'VERIFICADORES_BAJO': valor})
            self.assertEqual(p['bajo'].motivo, 'configuracion_invalida')
            self.assertIsNone(p['alto'].motivo)
        p = M.cargar(V, {}, {'VERIFICADORES_ALTO': ''})
        self.assertEqual(p['alto'].motivo, 'configuracion_invalida')

    def test_version_por_modo_y_perfiles_inmutables(self):
        a = M.cargar(V, {}, {'VERIFICADORES_BAJO': ' t/a '})
        b = M.cargar(V, {}, {'VERIFICADORES_BAJO': 't/b'})
        self.assertEqual(a['alto'].version, b['alto'].version)
        self.assertNotEqual(a['bajo'].version, b['bajo'].version)
        self.assertEqual(a['bajo'].verificadores, ('t/a',))
        with self.assertRaises(Exception):
            a['bajo'].modo = 'alto'
        with self.assertRaises(TypeError):
            a['alto'] = b['alto']

    def test_composicion_y_estado_en_hilos(self):
        ps = perfiles()
        barrera = threading.Barrier(2)
        def pedido(modo):
            with M.usar(ps[modo]):
                barrera.wait(timeout=3)
                return V._map_modelos([0, 1], lambda _: (
                    V.modelos_activos(), V.arbitro_activo(), V.arbitro_ve_foto(), V.arbitro_confirma()))
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            bajo, medio = list(pool.map(pedido, ['bajo', 'medio']))
        self.assertEqual(bajo, [(('t/a',), '', False, False)] * 2)
        self.assertEqual(medio, [(('t/a', 't/b'), 't/arbitro', False, False)] * 2)
        self.assertIsNone(M.perfil_actual())

    def test_identidad_modo_version_y_contexto(self):
        p = perfiles()
        self.assertNotEqual(M.identidad(b'foto', '', 'auto', p['bajo']), M.identidad(b'foto', '', 'auto', p['alto']))
        self.assertNotEqual(M.identidad(b'foto', '', 'auto', p['alto']), M.identidad(b'foto', '', 'auto', replace(p['alto'], version='otra')))

    def test_bajo_no_lee_patente_ni_usa_arbitro_o_especialista(self):
        with M.usar(perfiles()['bajo']), patch.object(V, '_llamar') as llamada:
            self.assertIsNone(V._leer_patente(Image.new('RGB', (20, 20))))
            self.assertIsNone(V._arbitrar(set(), [], [], {}, set()))
            with self.assertRaises(ValueError):
                V.verificar_contenedores(b'foto')
            llamada.assert_not_called()

    def test_frontera_no_acepta_modelo_ajeno(self):
        with M.usar(perfiles()['medio']), patch.object(V, '_pedir_http') as http:
            with self.assertRaises(ValueError):
                _LLAMAR('t/ajeno', [])
            http.assert_not_called()

    def test_contexto_en_bajo_usa_su_generalista_sin_foto(self):
        with M.usar(perfiles()['bajo']), patch.object(V, '_llamar', return_value='{"categorias": []}') as llamada:
            V._clasificar_contexto('hay residuos', {'recoleccion': {'nombre': 'Recolección'}})
        self.assertEqual(llamada.call_args.args[0], 't/a')
        self.assertNotIn('image_url', json.dumps(llamada.call_args.args[1]))

    def test_omision_de_dos_fuentes_deja_hallazgo_pendiente(self):
        salida = {'problemas': [{'key': 'retiro_escombros', 'nombre': 'Retiro de escombros',
                                 'fuentes': ['modelo_local', 't/a']}],
                  'elementos_detectados': [], 'posibles': [], 'en_duda': [],
                  'detalle': {'verificacion': {'activa': True}}}
        with M.usar(perfiles()['bajo']):
            M.omitir('alcance_escombros', ('retiro_escombros',))
            r = M.completar(salida)
        self.assertEqual(r['problemas'], [])
        self.assertEqual(r['posibles'][0]['fuentes'], ['modelo_local', 't/a'])
        self.assertEqual(r['analisis_estado'], 'completo')
        self.assertIn('corroboracion_insuficiente', r['analisis_limitaciones'])

    def test_omision_prevista_no_es_fallo_pero_error_de_etapa_si(self):
        for falla, estado in [(False, 'completo'), (True, 'parcial')]:
            with M.usar(perfiles()['bajo']):
                M.omitir('contexto_obra_servicios')
                if falla:
                    M.fallo('leer_material')
                r = M.completar({'posibles': [], 'detalle': {'verificacion': {
                    'activa': True, 'contexto_obra_servicios': {'ok': False}}}})
            self.assertEqual(r['analisis_estado'], estado)

    def test_revision_pendiente_no_se_presenta_como_falla_tecnica(self):
        with M.usar(perfiles()['medio']):
            r = M.completar({'en_duda': ['retiro_escombros'],
                'detalle': {'verificacion': {'activa': True}}})
        self.assertEqual(r['analisis_estado'], 'parcial')
        self.assertIn('revision_pendiente', r['analisis_limitaciones'])
        self.assertNotIn('etapa_fallida', r['analisis_limitaciones'])


@unittest.skipUnless('servidor' in sys.modules, 'Ejecutar con pruebas.py, sin cargar pesos')
class ApiModos(unittest.TestCase):
    def setUp(self):
        import servidor as S
        self.S = S
        self.pila = ExitStack()
        self.addCleanup(self.pila.close)
        self.ps = perfiles()
        self.pila.enter_context(patch.object(S, 'PERFILES', self.ps))
        self.pila.enter_context(patch.object(S, 'API_TOKEN', ''))
        self.pila.enter_context(patch.object(S, '_permitir', return_value=(True, 0)))
        self.pila.enter_context(patch.object(S, '_cache', __import__('collections').OrderedDict()))
        self.pila.enter_context(patch.object(S, 'CACHE_MAX', 20))
        self.pila.enter_context(patch.object(S, '_hay_cuota', return_value=True))
        self.pila.enter_context(patch.object(S, 'clasificar_local', return_value={
            'predichas': [], 'probabilidades': [], 'gravedad': {}}))
        self.pila.enter_context(patch.object(S, '_calidad_segura', return_value=None))
        self.pila.enter_context(patch.object(V, 'api_key', return_value='prueba'))
        self.pila.enter_context(patch.object(V, '_llamar', _LLAMAR))
        self.pila.enter_context(patch.object(V, 'verificar', _VERIFICAR))
        self.pila.enter_context(patch.object(V, 'OPENROUTER_LOG_USO', False))
        self.llamadas = []
        def http(req, *_):
            datos = json.loads(req.data);self.llamadas.append(datos)
            contenido = {'categorias': [], 'descripcion': '', 'sin_problema': True}
            if datos['model'] == __import__('especialista_contenedores').MODELO:
                contenido = {'types': [], 'uncertain': False, 'evidence': ''}
            return {'model': datos['model'], 'usage': {'total_tokens': 10, 'cost': .001},
                    'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(contenido)}}]}
        self.pila.enter_context(patch.object(V, '_pedir_http', side_effect=http))
        # El especialista conserva su contrato, sin abrir el banco privado de ejemplos.
        import especialista_contenedores as E
        self.pila.enter_context(patch.object(E, 'solicitud', return_value={'model': E.MODELO, 'messages': []}))
        from fastapi.testclient import TestClient
        self.cliente = self.pila.enter_context(TestClient(S.app))
        b = io.BytesIO();Image.new('RGB', (24, 24)).save(b, format='JPEG');self.foto = b.getvalue()

    def post(self, modo=None, ruta='/clasificar', **kw):
        return self.cliente.post(ruta, files={'file': ('foto.jpg', self.foto, 'image/jpeg')},
                                 data={} if modo is None else {'modo': modo}, **kw)

    def test_perfiles_reales_llamadas_y_cache_por_modo(self):
        for modo, cantidad in [('bajo', 1), ('medio', 2), ('alto', 4)]:
            with self.subTest(modo=modo):
                self.llamadas.clear()
                r = self.post(modo)
                self.assertEqual(r.status_code, 200, r.text)
                d = r.json()
                self.assertEqual(d['modo'], modo)
                self.assertEqual(d['modo_version'], self.ps[modo].version)
                self.assertEqual(d['analisis_estado'], 'completo', d)
                self.assertEqual(len(self.llamadas), cantidad, self.llamadas)
                self.assertEqual(d['tokens_api'], 10 * cantidad)
                self.assertEqual(d['costo_api'], .001 * cantidad)
                cache = self.post(modo, '/trabajos').json()
                self.assertIsNone(cache['trabajo'])
                self.assertEqual(cache['resultado'], d)
                self.assertEqual(cache['modo'], modo)
                self.assertEqual(len(self.llamadas), cantidad)
        self.assertEqual(self.post().json(), self.post('alto').json())

    def test_parametros_invalidos_sin_llamadas(self):
        for ruta in ('/clasificar', '/trabajos'):
            for modo in ('', 'otro', 'ALTO', ' alto'):
                self.assertEqual(self.post(modo, ruta).status_code, 422)
            self.assertEqual(self.post(None, ruta+'?modo=bajo').status_code, 422)
            r = self.cliente.post(ruta, files={'file': ('foto.jpg', self.foto)}, data={'modo[]': 'alto'})
            self.assertEqual(r.status_code, 422)
        self.assertEqual(self.llamadas, [])

    def test_ultimo_campo_escalar_y_no_archivo(self):
        r = self.cliente.post('/clasificar', files=[('file', ('foto.jpg', self.foto)),
            ('modo', (None, 'alto')), ('modo', (None, 'bajo'))])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['modo'], 'bajo')
        r = self.cliente.post('/clasificar', files=[('file', ('foto.jpg', self.foto)), ('modo', ('modo.txt', b'alto'))])
        self.assertEqual(r.status_code, 422)

    def test_no_disponible_incluso_sin_verificar(self):
        ps = dict(self.ps);ps['alto'] = replace(ps['alto'], motivo='configuracion_invalida')
        with patch.object(self.S, 'PERFILES', ps):
            self.assertEqual(self.post(None, '/clasificar?verificar=0').status_code, 503)
            self.assertEqual(self.post(None, '/trabajos?verificar=0').status_code, 503)
            salud = self.cliente.get('/salud')
            self.assertEqual(salud.headers['cache-control'], 'no-store')
            self.assertFalse(next(x for x in salud.json()['modos_analisis'] if x['modo'] == 'alto')['disponible'])
        self.assertEqual(self.llamadas, [])

    def test_sin_verificacion_conserva_modo(self):
        for modo in self.ps:
            d = self.post(modo, '/clasificar?verificar=0').json()
            self.assertEqual(d['modo'], modo)
            self.assertEqual(d['analisis_estado'], 'sin_verificacion')
            self.assertEqual(d['tokens_api'], 0)
        self.assertEqual(self.llamadas, [])

    def test_fallo_reducido_no_escala_ni_cachea(self):
        with patch.object(V, '_pedir_http', side_effect=TimeoutError):
            a = self.post('bajo').json()
            self.assertEqual(a['analisis_estado'], 'parcial')
            self.assertFalse(self.S._cache)
            self.assertEqual(a['modo'], 'bajo')

    def test_cola_conserva_perfil_capturado_y_error(self):
        inicio = threading.Event();seguir = threading.Event()
        async def espera(*args, **kwargs):
            inicio.set()
            while not seguir.is_set():
                await asyncio.sleep(.005)
            raise self.S.HTTPException(503, 'prueba de error en cola')
        with patch.object(self.S, '_esperar_cupo', side_effect=espera):
            r = self.post('medio', '/trabajos')
            self.assertEqual(r.status_code, 202)
            d = r.json();tid = d['trabajo']
            self.assertTrue(inicio.wait(timeout=3))
            ps = dict(self.ps);ps['medio'] = replace(ps['medio'], version='nueva')
            with patch.object(self.S, 'PERFILES', ps):
                pendiente = self.cliente.get('/trabajos/' + tid).json()
                self.assertEqual(pendiente['modo_version'], d['modo_version'])
                seguir.set()
                import time
                for _ in range(100):
                    final = self.cliente.get('/trabajos/' + tid).json()
                    if final['estado'] == 'error':
                        break
                    time.sleep(.01)
                self.assertEqual(final['estado'], 'error')
                self.assertEqual(final['modo'], 'medio')
                self.assertEqual(final['modo_version'], d['modo_version'])
        self.assertEqual(self.llamadas, [])

    def test_serializacion_recalcula_agregados_despues_de_dejar_pendiente(self):
        with M.usar(self.ps['bajo']):
            M.omitir('alcance_escombros', ('retiro_escombros',))
            r = M.completar({'hay_problema': True, 'hay_reclamo': True,
                'gravedad_maxima': 4, 'predominante': 'retiro_escombros',
                'problemas': [{'key': 'retiro_escombros', 'nombre': 'Retiro de escombros',
                    'fuentes': ['modelo_local', 't/a'], 'gravedad': 4}],
                'posibles': [], 'elementos_detectados': [], 'categorias_contexto': [],
                'en_duda': [], 'detalle': {'verificacion': {'activa': True}}})
            d = self.S._publica(r)
        self.assertFalse(d['hay_problema'])
        self.assertFalse(d['hay_reclamo'])
        self.assertIsNone(d['gravedad_maxima'])
        self.assertIsNone(d['predominante'])
        self.assertEqual(d['posibles'][0]['key'], 'retiro_escombros')
