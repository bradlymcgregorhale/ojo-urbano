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
            'VERIFICADORES_ALTO': 't/a,t/b,t/c',
            'VERIFICADORES_PRUEBA': 't/decision,t/dirigidos,t/alcance'})


class Configuracion(unittest.TestCase):
    def test_heredado_ausente_y_vacio_conservan_interpretacion(self):
        for legado in ([], ['t/a'], ['t/a', 't/a'], ['t/a', 't/b', 't/c', 't/d']):
            with patch.object(V, 'VERIFICADORES', legado):
                p = M.cargar(V, {}, {})
            self.assertEqual(p['alto'].verificadores, tuple(legado))
            self.assertIsNone(p['alto'].motivo)
            self.assertEqual(p['bajo'].motivo, 'no_configurado')

    def test_listas_dirigidas_y_huella_usan_la_configuracion(self):
        concepto = 'VERIFICADORES_RESIDUOS'
        self.assertIsNone(M.leer_modelos(concepto, ausente=None, entorno={}))
        self.assertEqual(M.leer_modelos(concepto, entorno={concepto: ''}), ())
        self.assertEqual(M.leer_modelos(concepto, entorno={concepto: 'prueba/a, prueba/b, prueba/a'}),
                         ('prueba/a', 'prueba/b'))
        with self.assertRaises(ValueError):
            M.leer_modelos(concepto, entorno={concepto: 'sin-proveedor'})
        antes = M.cargar(V, {}, {concepto: 'prueba/a'})
        despues = M.cargar(V, {}, {concepto: 'prueba/b'})
        self.assertNotEqual(antes['alto'].version, despues['alto'].version)
        self.assertEqual(antes['medio'].version, despues['medio'].version)

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
            if datos.get('response_format'):
                contenido = {'confirmadas': [{'key': 'retiro_muebles', 'evidencia': 'Tubos largos descartados.'}],
                             'dudosas': []}
            elif datos['model'] == __import__('especialista_contenedores').MODELO:
                contenido = {'types': [], 'uncertain': False, 'evidence': ''}
            elif datos['messages'][0].get('content') == V._PROMPT_ENCUADRE_HIGIENE:
                contenido = {'contexto_suficiente': True, 'motivo': None, 'evidencia': 'vereda'}
            elif datos['messages'][0].get('content') == V._PROMPT_APTITUD_HIGIENE:
                contenido = {'calidad_suficiente': True, 'motivo_calidad': None,
                    'evidencia_calidad': 'Objetos nítidos.', 'ambito': 'publica',
                    'evidencia_ambito': 'Vereda y cordón.'}
            elif datos['messages'][0].get('content') == __import__('revision_omisiones').OBJETOS:
                contenido = {'hallazgos': []}
            return {'model': datos['model'], 'usage': {'total_tokens': 10, 'cost': .001},
                    'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(contenido)}}]}
        self.pila.enter_context(patch.object(V, '_pedir_http', side_effect=http))
        # El especialista conserva su contrato, sin abrir el banco privado de ejemplos.
        import especialista_contenedores as E
        self.pila.enter_context(patch.object(E, 'MODELO', 'prueba/inventario'))
        self.pila.enter_context(patch.object(E, 'solicitud', return_value={'model': E.MODELO, 'messages': []}))
        from fastapi.testclient import TestClient
        self.cliente = self.pila.enter_context(TestClient(S.app))
        b = io.BytesIO();Image.new('RGB', (24, 24)).save(b, format='JPEG');self.foto = b.getvalue()

    def post(self, modo=None, ruta='/clasificar', **kw):
        return self.cliente.post(ruta, files={'file': ('foto.jpg', self.foto, 'image/jpeg')},
                                 data={} if modo is None else {'modo': modo}, **kw)

    def test_salud_usa_la_lista_de_completo(self):
        r = self.cliente.get('/salud')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['verificacion'])
        self.assertEqual(r.json()['verificadores'], list(self.ps['alto'].verificadores))

    def test_perfiles_reales_llamadas_y_cache_por_modo(self):
        # Económico: un lector. Medio y Completo agregan encuadre y aptitud
        # por lector; Completo también ejecuta el especialista de contenedores.
        for modo, cantidad in [('bajo', 1), ('medio', 6), ('alto', 13)]:
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
                self.assertAlmostEqual(d['costo_api'], .001 * cantidad, places=6)
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

    def test_prueba_por_ruta_normal_con_costo_y_cache(self):
        r = self.post('prueba')
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(d['modo'], 'prueba')
        self.assertEqual(d['modo_version'], self.ps['prueba'].version)
        self.assertIn('clasificacion_experimental', d['analisis_limitaciones'])
        self.assertEqual([x['key'] for x in d['problemas']], ['retiro_muebles'])
        self.assertEqual([x['model'] for x in self.llamadas].count('t/dirigidos'), 2)
        self.assertEqual(self.llamadas[-1]['model'], 't/decision')
        self.assertEqual([x['max_tokens'] for x in self.llamadas], [1400, 1400, 550])
        self.assertTrue(all(x['provider']['require_parameters'] for x in self.llamadas))
        self.assertTrue(all(not {'temperature', 'top_p', 'seed'} & x.keys() for x in self.llamadas))
        self.assertEqual(d['costo_api'], .003)
        self.assertTrue(d['costo_api_completo'])
        self.assertEqual(self.post('prueba', '/trabajos').json()['resultado'], d)
        self.assertEqual(len(self.llamadas), 3)

    def test_prueba_incluye_en_el_costo_el_apoyo_posterior_a_la_decision(self):
        def http(req, *_):
            cuerpo = json.loads(req.data)
            propiedades = cuerpo['response_format']['json_schema']['schema']['properties']
            if 'rama_desprendida' in propiedades:
                contenido = {'rama_desprendida': 'si', 'problemas_arbolado': 'no',
                             'obstruccion': 'no', 'evidencia': 'Rama suelta sobre la vereda.'}
            else:
                contenido = {'confirmadas': [{'key': 'retiro_poda', 'evidencia': 'Rama cortada.'}],
                             'dudosas': []}
                if 'objetos' in propiedades:
                    contenido['dudosas'].append({'key': 'problemas_arbolado', 'evidencia': 'Tronco al fondo.'})
            self.llamadas.append(cuerpo)
            return {'usage': {'cost': .001, 'total_tokens': 15}, 'choices': [
                {'finish_reason': 'stop', 'message': {'content': json.dumps(contenido)}}]}

        with patch.object(V, '_pedir_http', side_effect=http):
            r = self.post('prueba')
        self.assertEqual(r.status_code, 200, r.text)
        salida = r.json()
        self.assertEqual(len(self.llamadas), 4)
        self.assertEqual(salida['revision_ramas']['descartadas'], ['problemas_arbolado', 'obstruccion'])
        self.assertEqual(salida['costo_api'], .004)
        self.assertTrue(salida['costo_api_completo'])

    def test_prueba_informa_si_falta_el_costo_de_una_consulta(self):
        original = V._pedir_http

        def http(req, *args):
            respuesta = original(req, *args)
            if json.loads(req.data)['model'] == 't/decision':
                del respuesta['usage']['cost']
            return respuesta

        with patch.object(V, '_pedir_http', side_effect=http):
            r = self.post('prueba')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['costo_api'], .002)
        self.assertFalse(r.json()['costo_api_completo'])

    def test_prueba_pide_evidencia_independiente_de_obstruccion_en_un_puesto(self):
        from prueba_completo import _decision
        claves = ('manteros', 'obstruccion')
        self.assertNotIn('barrera independiente', _decision([], {'obstruccion': .9}, claves))
        self.assertIn('barrera independiente', _decision([], {'manteros': .9}, claves))
        self.assertIn('barrera independiente', _decision([{'confirmadas': {
            'manteros': 'Puesto con mercadería exhibida.'}, 'dudosas': {}}], {}, claves))

    def test_prueba_publica_objetos_visibles_en_la_misma_lectura(self):
        casos = (
            ('retiro_muebles', 'Junto al cordón hay un lavarropas y una silla.',
             ['lavarropas', 'silla plástica']),
            ('contenedor_humedos_bilateral', 'Hay un contenedor gris junto al cordón.', []),
        )
        for clave, evidencia, esperados in casos:
            with self.subTest(clave=clave):
                llamadas = []

                def responder(modelo, mensajes, **opciones):
                    etapa = opciones['etapa']
                    llamadas.append(etapa)
                    propiedades = opciones['esquema']['properties']
                    self.assertEqual('objetos' in propiedades, etapa == 'prueba_decision')
                    valor = {'confirmadas': [{'key': clave, 'evidencia': evidencia}], 'dudosas': []}
                    if etapa == 'prueba_decision':
                        self.assertIn('voluminosos descartados', mensajes[0]['content'])
                        valor['objetos'] = ['lavarropas', 'Lavarropas', 'silla plástica', 'contenedor', 'vereda']
                    return json.dumps(valor)

                with patch.object(self.S, '_cache', __import__('collections').OrderedDict()), \
                        patch.object(V, '_llamar', side_effect=responder):
                    r = self.post('prueba')
                self.assertEqual(r.status_code, 200, r.text)
                self.assertCountEqual(llamadas, ['prueba_inicio', 'prueba_retiros', 'prueba_decision'])
                salida = r.json()
                if esperados:
                    self.assertEqual(salida['problemas'][0]['objetos'], esperados)
                else:
                    self.assertEqual(salida['problemas'], [])
                    self.assertEqual(salida['elementos_detectados'][0]['key'], clave)
                    self.assertNotIn('objetos', salida['elementos_detectados'][0])

    def test_prueba_publica_recuento_sin_agregar_llamadas(self):
        for dirigida, final, tapadas, minimo, exacto in (
            (5, 5, False, 5, True), (6, 7, False, 6, False),
            (5, 5, True, 5, False), (5, None, False, 5, False),
            (5, True, False, 5, False), (0, 0, False, 0, True),
            (None, 6, False, 6, False), (None, None, False, None, False),
        ):
            with self.subTest(dirigida=dirigida, final=final, tapadas=tapadas):
                llamadas = []

                def responder(modelo, mensajes, **opciones):
                    esquema = opciones['esquema']['properties']
                    etapa = opciones['etapa']
                    llamadas.append(etapa)
                    valor = {'confirmadas': [{'key': 'retiro_escombros',
                                              'evidencia': 'Sacos de rafia densos en la vereda.'}],
                             'dudosas': []}
                    if etapa != 'prueba_inicio':
                        self.assertIn('bolsas_visibles', esquema)
                        valor.update(bolsas_visibles=dirigida if etapa == 'prueba_retiros' else final,
                                     bolsas_adicionales_posibles=tapadas)
                    if etapa == 'prueba_retiros':
                        valor['dudosas'] = valor['confirmadas']
                        valor['confirmadas'] = []
                    return json.dumps(valor)

                with patch.object(self.S, '_cache', __import__('collections').OrderedDict()):
                    with patch.object(V, '_llamar', side_effect=responder):
                        r = self.post('prueba')
                self.assertEqual(r.status_code, 200)
                self.assertCountEqual(llamadas, ['prueba_inicio', 'prueba_retiros', 'prueba_decision'])
                salida = r.json()
                self.assertEqual([(p['key'], p['bolsas']) for p in salida['problemas']],
                                 [('retiro_escombros', minimo if exacto and minimo else None)])
                if minimo is None:
                    self.assertNotIn('verificacion_escombros', salida)
                else:
                    self.assertEqual(salida['verificacion_escombros']['bolsas_minimo'], minimo)
                    self.assertIs(salida['verificacion_escombros']['bolsas_recuento_completo'], exacto)

    def test_prueba_distingue_evidencia_de_la_escena(self):
        import prueba_completo as P
        for poda, piso, comunes, esperadas in (
            ('Bolsas negras junto al árbol.', 'Rajaduras en el piso del balcón.',
             'Una bolsa negra atada sobre la vereda.', {'retiro_escombros'}),
            ('Ramas cortadas en un montón junto al árbol.', 'Baldosas flojas en la vereda pública.',
             'Cajas de cartón descartadas aparte.', {'retiro_escombros', 'retiro_poda',
                                                      'reparacion_vereda', 'recoleccion'}),
        ):
            confirmadas = {'retiro_escombros': 'Sacos de corralón densos junto al cordón.',
                           'retiro_poda': poda, 'reparacion_vereda': piso,
                           'recoleccion': comunes}
            lectura = {'confirmadas': confirmadas, 'dudosas': {}}
            with self.subTest(lectura=lectura), patch.object(P, '_vistas', return_value=[]), \
                    patch.object(P, '_leer', return_value=lectura) as leer:
                fuentes, _, _ = P.clasificar(Image.new('RGB', (24, 24)), {}, tuple(confirmadas),
                                             ('decision', 'dirigidos', 'alcance'))
            self.assertEqual(set(fuentes), esperadas)
            self.assertEqual(leer.call_count, 3)

    def test_prueba_conserva_obstruccion_de_bolsones_de_izado(self):
        import prueba_completo as P
        casos = [
            ({'obstruccion': 'Dos bolsones con asas de izado reducen el paso peatonal.'},
             {'obstruccion'}),
            ({'obstruccion': 'Dos bolsones con asas reducen el paso por la vereda.'},
             {'obstruccion'}),
            ({'obstruccion': 'Los bolsones de obra estrechan el paso peatonal.'}, {'obstruccion'}),
            ({'obstruccion': 'Bolsas chicas con asas reducen el paso.',
              'retiro_escombros': 'Sacos de obra cargables a mano.'}, {'retiro_escombros'}),
            ({'obstruccion': 'Un bolsón con asas de izado y cascotes ocupa la vereda.'}, set()),
            ({'obstruccion': 'Bolsones sin asas de izado reducen el paso.'}, set()),
            ({}, set()),
        ]
        foto = Image.new('RGB', (24, 24))
        local = {'probabilidades': [{'key': 'retiro_escombros', 'score': .85}],
                 'escombros_mixtos': .98}
        with patch.object(P, '_vistas', return_value=[]):
            for confirmadas, esperadas in casos:
                with self.subTest(confirmadas=confirmadas):
                    lectura = {'confirmadas': confirmadas, 'dudosas': {}}
                    with patch.object(P, '_leer', return_value=lectura) as leer:
                        fuentes, _, _ = P.clasificar(foto, local, ('obstruccion', 'retiro_escombros'),
                                                     ('decision', 'dirigidos', 'alcance'))
                    self.assertEqual(set(fuentes), esperadas)
                    self.assertEqual(leer.call_count, 3)

    def test_prueba_pide_describir_el_paso_si_hay_indicios_de_obra(self):
        from prueba_completo import _decision
        claves = ('retiro_escombros', 'obstruccion')
        self.assertIn('nombrá las asas', _decision([], {'retiro_escombros': .85}, claves))
        self.assertNotIn('nombrá las asas', _decision([], {'retiro_escombros': .05}, claves))

    def test_prueba_rechaza_contexto_y_otros_usos_antes_de_llamar(self):
        f = ('foto.jpg', self.foto, 'image/jpeg')
        self.assertEqual(self.cliente.post('/trabajos', files={'file': f},
            data={'modo': 'prueba', 'contexto': 'Comentario'}).status_code, 422)
        self.assertEqual(self.cliente.post('/comparar', files={'file_a': f, 'file_b': f},
            data={'modo': 'prueba'}).status_code, 422)
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

    def test_respuesta_interrumpida_no_vota_ni_reenvia_en_ningun_modo(self):
        def http(req, *_):
            datos = json.loads(req.data); self.llamadas.append(datos)
            contenido = json.dumps({'categorias': [{'key': 'retiro_escombros',
                'gravedad': 3, 'evidencia': 'material visible'}], 'sin_problema': False})
            return {'model': datos['model'], 'usage': {'total_tokens': 10, 'cost': .001},
                    'choices': [{'finish_reason': 'length',
                                 'message': {'content': None, 'reasoning': contenido}}]}
        with patch.object(V, '_pedir_http', side_effect=http):
            # Sin ninguna primera pasada válida tampoco se paga la pregunta de encuadre (#114).
            for modo, cantidad in [('bajo', 1), ('medio', 2), ('alto', 4)]:
                with self.subTest(modo=modo):
                    self.llamadas.clear()
                    r = self.post(modo)
                    self.assertEqual(r.status_code, 200, r.text)
                    d = r.json()
                    self.assertEqual(d['analisis_estado'], 'parcial', d)
                    self.assertIn('etapa_fallida', d['analisis_limitaciones'])
                    self.assertEqual(d['problemas'], [])
                    self.assertEqual(len(self.llamadas), cantidad)
                    self.assertEqual(d['tokens_api'], cantidad * 10)
                    self.assertTrue(d['tokens_api_completos'])
                    self.assertEqual(d['costo_api'], cantidad * .001)
                    self.assertFalse(self.S._cache)

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

    def test_economico_publica_conclusion_preliminar_sin_confirmar(self):
        # #122: el voto único de Económico se presenta como lectura preliminar.
        with M.usar(self.ps['bajo']), patch.object(self.S, 'PERFILES', self.ps):
            r = M.completar({'hay_problema': False, 'hay_reclamo': False, 'gravedad_maxima': None,
                'predominante': None, 'problemas': [], 'elementos_detectados': [], 'categorias_contexto': [],
                'posibles': [{'key': 'recoleccion', 'nombre': 'Recolección de residuos', 'gravedad': 3,
                              'origen': 'foto', 'arbitro': None, 'motivo': None, 'fuentes': ['t/a']},
                             {'key': 'retiro_escombros', 'nombre': 'Retiro de escombros', 'gravedad': 3,
                              'origen': 'foto', 'arbitro': None, 'motivo': None, 'fuentes': ['t/a']}],
                'en_duda': ['recoleccion', 'retiro_escombros'], 'detalle': {'verificacion': {'activa': True}}})
            d = self.S._publica(r)
        self.assertFalse(d['hay_problema'])
        self.assertFalse(d['hay_reclamo'])
        self.assertEqual(d['problemas'], [])
        self.assertEqual(d['conclusion']['estado'], 'preliminar')
        self.assertTrue(d['conclusion']['requiere_revision'])
        self.assertEqual([c['key'] for c in d['conclusion']['categorias']], ['recoleccion', 'retiro_escombros'])
        self.assertEqual(d['conclusion']['texto'],
                         'Lectura preliminar: se detectaron indicios de recolección de residuos y retiro de escombros. Requieren corroboración.')
        self.assertEqual(d['descripcion'], d['conclusion']['texto'])

    def test_economico_sin_votos_queda_sin_indicios(self):
        with M.usar(self.ps['bajo']), patch.object(self.S, 'PERFILES', self.ps):
            r = M.completar({'hay_problema': False, 'hay_reclamo': False, 'gravedad_maxima': None,
                'predominante': None, 'problemas': [], 'elementos_detectados': [], 'categorias_contexto': [],
                'posibles': [], 'en_duda': [], 'detalle': {'verificacion': {'activa': True}}})
            d = self.S._publica(r)
        self.assertEqual(d['conclusion'], {'estado': 'sin_indicios', 'categorias': [], 'requiere_revision': False,
                                           'texto': 'No se confirmaron problemas con la evidencia disponible.'})
        self.assertNotIn('Lectura preliminar', d['descripcion'])

    def test_modos_con_arbitro_no_publican_preliminar(self):
        # En Equilibrado y Completo los posibles pasaron por el árbitro: sin_indicios o confirmada.
        for modo in ('medio', 'alto'):
            with self.subTest(modo=modo), M.usar(self.ps[modo]), patch.object(self.S, 'PERFILES', self.ps):
                r = M.completar({'hay_problema': False, 'hay_reclamo': False, 'gravedad_maxima': None,
                    'predominante': None, 'problemas': [], 'elementos_detectados': [], 'categorias_contexto': [],
                    'posibles': [{'key': 'retiro_escombros', 'nombre': 'Retiro de escombros', 'gravedad': 3,
                                  'origen': 'foto', 'arbitro': 'rechazar', 'motivo': 'x', 'fuentes': ['t/a']},
                                 {'key': 'recoleccion', 'nombre': 'Recolección', 'gravedad': 3,
                                  'origen': 'foto', 'arbitro': None, 'motivo': None, 'fuentes': ['t/a']}],
                    'en_duda': [], 'detalle': {'verificacion': {'activa': True}}})
                d = self.S._publica(r)
            self.assertEqual(d['conclusion']['estado'], 'sin_indicios')
            self.assertFalse(d['conclusion']['requiere_revision'])
        # Un Completo o Equilibrado sin árbitro configurado tampoco publica preliminar.
        with patch.multiple(V, ARBITRO='', ARBITRO_VE_FOTO=False, ARBITRO_CONFIRMA=False):
            sin_arbitro = M.cargar(V, {'CONTENEDORES_ESPECIALISTA': True}, {
                'VERIFICADORES_BAJO': 't/a', 'VERIFICADORES_MEDIO': 't/a,t/b', 'VERIFICADORES_ALTO': 't/a,t/b,t/c'})
        for modo in ('medio', 'alto'):
            self.assertFalse(sin_arbitro[modo].arbitro)
            with self.subTest(modo=modo, arbitro=''), M.usar(sin_arbitro[modo]), patch.object(self.S, 'PERFILES', sin_arbitro):
                r = M.completar({'hay_problema': False, 'hay_reclamo': False, 'gravedad_maxima': None,
                    'predominante': None, 'problemas': [], 'elementos_detectados': [], 'categorias_contexto': [],
                    'posibles': [{'key': 'recoleccion', 'nombre': 'Recolección', 'gravedad': 3,
                                  'origen': 'foto', 'arbitro': None, 'motivo': None, 'fuentes': ['t/a']}],
                    'en_duda': [], 'detalle': {'verificacion': {'activa': True}}})
                d = self.S._publica(r)
            self.assertEqual(d['conclusion']['estado'], 'sin_indicios')
        with M.usar(self.ps['alto']), patch.object(self.S, 'PERFILES', self.ps):
            r = M.completar({'hay_problema': True, 'hay_reclamo': True, 'gravedad_maxima': 3,
                'predominante': 'recoleccion', 'categorias_contexto': [],
                'problemas': [{'key': 'recoleccion', 'nombre': 'Recolección', 'gravedad': 3, 'fuentes': ['t/a', 't/b']}],
                'elementos_detectados': [], 'posibles': [], 'en_duda': [], 'detalle': {'verificacion': {'activa': True}}})
            d = self.S._publica(r)
        self.assertEqual(d['conclusion']['estado'], 'confirmada')
        self.assertEqual(d['conclusion']['categorias'], [{'key': 'recoleccion', 'nombre': 'Recolección'}])

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
