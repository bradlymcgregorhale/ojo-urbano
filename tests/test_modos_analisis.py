"""Contrato de perfiles, aislamiento y límites de llamadas sin red ni pesos."""
import concurrent.futures
import asyncio
from contextlib import ExitStack
from dataclasses import replace
from decimal import Decimal
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
_RESERVAS = {
    'prueba_decision': '.0084', 'prueba_retiros': '.00061', 'prueba_inicio': '.000405',
    'prueba_contenedores': '.00105', 'prueba_tipos': '.00066', 'prueba_alcance': '.0008',
    'rama_desprendida': '.0005', 'leer_chapa': '.001',
}


def perfil_con_reservas():
    return M.cargar(V, {}, {
        'VERIFICADORES_PRUEBA': 't/decision,t/dirigidos,t/alcance',
        'PRUEBA_RESERVAS_USD': json.dumps(_RESERVAS),
    })['prueba']


def perfiles():
    with patch.multiple(V, ARBITRO='t/arbitro', ARBITRO_VE_FOTO=True, ARBITRO_CONFIRMA=True):
        return M.cargar(V, {'CONTENEDORES_ESPECIALISTA': True}, {
            'VERIFICADORES_BAJO': 't/a', 'VERIFICADORES_MEDIO': 't/a,t/b',
            'VERIFICADORES_ALTO': 't/a,t/b,t/c',
            'VERIFICADORES_PRUEBA': 't/decision,t/dirigidos,t/alcance'})


class Configuracion(unittest.TestCase):
    def test_senal_de_opacidad_no_requiere_una_frase_del_lector(self):
        from prueba_completo import _excluir_indicios_insuficientes
        for evidencia, pendiente in (('Bolsas negras junto al contenedor.', True),
                                    ('Bolsas, cajas y envases fuera del contenedor.', False)):
            with self.subTest(evidencia=evidencia):
                fuentes, dudas = {'recoleccion': ['externo']}, {}
                _excluir_indicios_insuficientes(fuentes, {'recoleccion': evidencia}, dudas, {}, {}, True)
                self.assertEqual('recoleccion' in dudas, pendiente)
                self.assertEqual('recoleccion' in fuentes, not pendiente)

    def test_bolsas_opacas_sin_respaldo_de_recoleccion_quedan_en_duda(self):
        from prueba_completo import _excluir_indicios_insuficientes

        duda = 'Bolsas cerradas con el contenido oculto. No se distingue qué residuos contienen.'
        casos = (
            (duda, 'Bolsas negras de residuos fuera del contenedor.', True),
            (duda, 'Cartón y envases a un lado del contenedor.', False),
            (duda, 'Bolsas cerradas en un grupo aparte.', False),
            ('No se distingue de qué color es el contenedor.', 'Bolsas negras juntadas.', False),
            (None, 'Bolsas negras de residuos fuera del contenedor.', False),
        )
        for anterior, final, pendiente in casos:
            with self.subTest(anterior=anterior, final=final):
                fuentes = {'recoleccion': ['sim/final'], 'retiro_muebles': ['sim/final']}
                confirmadas = {'recoleccion': final, 'retiro_muebles': 'Tapa descartada.'}
                dudas = {}
                pedidos = {'recoleccion': 'compatible'}
                retiros = {'dudosas': {'recoleccion': anterior}} if anterior else {}

                _excluir_indicios_insuficientes(fuentes, confirmadas, dudas, pedidos, retiros)

                self.assertIn('retiro_muebles', fuentes)
                self.assertEqual('recoleccion' not in fuentes, pendiente)
                self.assertEqual(pedidos['recoleccion'], 'neutral' if pendiente else 'compatible')
                if pendiente:
                    self.assertIn('¿Qué hay en las bolsas?', dudas['recoleccion'])
                else:
                    self.assertEqual(dudas, {})

    def test_se_identifica_si_la_unica_falla_es_la_revision_de_ramas(self):
        perfil = perfiles()['prueba']
        for extra in (None, 'prueba_inicio'):
            with self.subTest(extra=extra), M.usar(perfil):
                M.fallo('rama_desprendida')
                M.fallo('respuesta_dirigida')
                if extra:
                    M.fallo(extra)
                salida = M.completar({'detalle': {'verificacion': {'activa': True, 'revision_ramas_fallo': True}}})
                self.assertEqual(salida['analisis_estado'], 'parcial')
                self.assertEqual('solo_revision_ramas_pendiente' in salida['analisis_limitaciones'], extra is None)

    def test_respaldo_de_retiro_conserva_la_clave_canonica(self):
        import prueba_completo as P

        categorias = {'retiro_muebles': {'nombre': 'Retiro de residuos voluminosos'}}
        contexto = 'Hay un caño grueso tirado en la vereda.'
        legado = {'key': 'retiro_objetos', 'respaldo': 'compatible', 'motivo': 'Hay un voluminoso.'}
        actual = {'key': 'retiro_muebles', 'respaldo': 'contradice', 'motivo': 'Se ve un ropero.'}
        esperado = {'key': 'retiro_muebles', 'respaldo': 'contradice'}

        for pedidos in ([legado, actual], [actual, legado]):
            with self.subTest(pedidos=pedidos):
                datos = {'categorias_contexto': pedidos, 'categorias': []}
                with patch.object(V, '_llamar', return_value=json.dumps(datos)):
                    veredicto = V._verificar_uno('simulado', '', categorias, contexto)
                self.assertEqual(veredicto['categorias_contexto'], [esperado])

                datos = {'confirmadas': [], 'dudosas': [], 'pedidos': pedidos}
                with patch.object(V, '_llamar', return_value=json.dumps(datos)):
                    lectura = P._leer('simulado', 'texto', [], 'decision', categorias, contexto=contexto)
                self.assertEqual(lectura['pedidos'], {'retiro_muebles': 'contradice'})

                with patch.object(V, 'ARBITRO', 'simulado'), \
                        patch.object(V, '_llamar', return_value=json.dumps({'categorias': pedidos})):
                    ruteo = V._clasificar_contexto(contexto, categorias)
                self.assertEqual([(c['key'], c['motivo']) for c in ruteo],
                                 [('retiro_muebles', 'Se ve un ropero.')])

    def test_saldo_restante_de_la_foto_limita_la_reserva_del_mismo_perfil(self):
        perfil = perfil_con_reservas()
        for saldo, espera_apoyo in ((Decimal('.01'), True), (Decimal('.0095'), False)):
            with self.subTest(saldo=saldo), M.usar(replace(perfil, saldo_prueba=saldo)):
                for etapa in M.ETAPAS_BASE_PRUEBA:
                    reserva = M.reservar_consulta(etapa)
                    M.liquidar_consulta(reserva, {'cost': float(reserva)})
                self.assertEqual(M.caben_apoyos('rama_desprendida'), espera_apoyo)

    def test_reservas_incompletas_o_inviables_deshabilitan_solo_prueba(self):
        base = {'VERIFICADORES_PRUEBA': 't/decision,t/dirigidos,t/alcance'}
        sin_reservas = M.cargar(V, {}, base)
        valido = perfil_con_reservas()
        self.assertIsNone(valido.motivo)
        self.assertNotEqual(valido.version, sin_reservas['prueba'].version)
        for reservas in ({}, _RESERVAS | {'prueba_tipos': 'NaN'},
                         _RESERVAS | {'prueba_decision': '0.01'}):
            p = M.cargar(V, {}, base | {'PRUEBA_RESERVAS_USD': json.dumps(reservas)})
            self.assertEqual(p['prueba'].motivo, 'configuracion_invalida')
            self.assertEqual(p['alto'].version, sin_reservas['alto'].version)

    def test_reserva_por_lector_admite_el_par_y_conserva_los_dos_votos(self):
        import patentes

        lectores = ('t/chapa-a', 't/chapa-b')
        importes = {'t/chapa-a': '.0045856', 't/chapa-b': '.00216975'}
        env = {'VERIFICADORES_PRUEBA': 't/decision,t/dirigidos,t/alcance'}
        img = Image.new('RGB', (180, 60))
        detecciones = [{'caja': [0, 0, 158, 40], 'confianza': .9}]
        ocr = {'patente': 'A123BCD', 'lectura': 'A123BCD', 'confianza': .9}

        def responder(req, *_):
            modelo = json.loads(req.data)['model']
            return {'usage': {'cost': float(importes[modelo])}, 'choices': [
                {'finish_reason': 'stop', 'message': {'content': '{"patente":"A123BCD"}'}}]}

        for lectura, esperadas in (('.0045856', 0), (importes, 2), ({lectores[0]: importes[lectores[0]]}, 0)):
            with self.subTest(reserva_de_chapa=lectura):
                perfil = M.cargar(V, {}, env | {'PRUEBA_RESERVAS_USD':
                                               json.dumps(_RESERVAS | {'leer_chapa': lectura})})['prueba']
                self.assertIsNone(perfil.motivo)
                with (M.usar(perfil),
                      patch.object(V, 'api_key', return_value='simulada'),
                      patch.object(V, '_llamar', _LLAMAR),
                      patch.object(V, '_pedir_http', side_effect=responder) as transporte,
                      patch.object(patentes, 'LECTORES', lectores),
                      patch.object(patentes, 'detectar', return_value=detecciones),
                      patch.object(patentes, 'leer_local', return_value=ocr)):
                    reserva = M.reservar_consulta('prueba_decision')
                    M.liquidar_consulta(reserva, {'cost': .003})
                    [chapa] = patentes.leer_chapas(img)
                    salida = M.completar({'detalle': {'verificacion': {'activa': True}}})
                    self.assertEqual(transporte.call_count, esperadas)
                    self.assertEqual(chapa['patente'], 'A123BCD' if esperadas else None)
                    if esperadas:
                        self.assertEqual(chapa['fuentes'], 3)
                        self.assertEqual({json.loads(args.args[0].data)['model']
                                          for args in transporte.call_args_list}, set(lectores))
                        self.assertNotIn('reserva_api_excedida', salida['analisis_limitaciones'])
                    else:
                        self.assertIn('presupuesto_api_insuficiente', salida['analisis_limitaciones'])

    def test_reserva_por_lector_exige_importes_validos_y_cambia_solo_la_huella_de_prueba(self):
        env = {'VERIFICADORES_PRUEBA': 't/decision,t/dirigidos,t/alcance'}
        base = M.cargar(V, {}, env | {'PRUEBA_RESERVAS_USD': json.dumps(_RESERVAS)})
        nueva = M.cargar(V, {}, env | {'PRUEBA_RESERVAS_USD': json.dumps(
            _RESERVAS | {'leer_chapa': {'t/chapa-a': '.0045', 't/chapa-b': '.0021'}})})
        self.assertEqual(nueva['alto'].version, base['alto'].version)
        self.assertNotEqual(nueva['prueba'].version, base['prueba'].version)
        for valor in ({}, {'chapa-a': '.0045'}, {'t/chapa-a': 'NaN'}, {'t/chapa-a': '.02'}):
            with self.subTest(valor=valor):
                p = M.cargar(V, {}, env | {'PRUEBA_RESERVAS_USD': json.dumps(_RESERVAS | {'leer_chapa': valor})})
                self.assertEqual(p['prueba'].motivo, 'configuracion_invalida')
                self.assertEqual(p['alto'].version, base['alto'].version)

    def test_no_gasta_en_una_lectura_suelta_si_las_dos_lecturas_no_caben(self):
        import patentes

        img = Image.new('RGB', (180, 60))
        ocr = {'patente': 'A123BCD', 'lectura': 'A123BCD', 'confianza': .9}
        detecciones = [{'caja': [0, 0, 158, 40], 'confianza': .9}]
        with (M.usar(perfil_con_reservas()),
              patch.object(patentes, 'detectar', return_value=detecciones),
              patch.object(patentes, 'leer_local', return_value=ocr),
              patch.object(patentes, 'leer_vlm') as lector):
            reserva = M.reservar_consulta('prueba_decision')
            M.liquidar_consulta(reserva, {'cost': .0084})
            [chapa] = patentes.leer_chapas(img, ['t/chapa-a', 't/chapa-b'])
            lector.assert_not_called()
            self.assertFalse(chapa['legible'])
            salida = M.completar({'detalle': {'verificacion': {'activa': True}}})
            self.assertIn('presupuesto_api_insuficiente', salida['analisis_limitaciones'])

    def test_no_empieza_si_el_saldo_alcanza_para_una_sola_chapa(self):
        import patentes

        img = Image.new('RGB', (360, 60))
        detecciones = [{'caja': [0, 0, 158, 40], 'confianza': .9},
                       {'caja': [170, 0, 326, 40], 'confianza': .8}]
        with (M.usar(perfil_con_reservas()),
              patch.object(patentes, 'detectar', return_value=detecciones),
              patch.object(patentes, 'leer_local') as ocr,
              patch.object(patentes, 'leer_vlm') as lector):
            reserva = M.reservar_consulta('prueba_decision')
            M.liquidar_consulta(reserva, {'cost': .007})
            self.assertEqual(patentes.leer_chapas(img, ['t/chapa-a', 't/chapa-b'],
                                                lectura_completa=True), [])
            ocr.assert_not_called()
            lector.assert_not_called()
            salida = M.completar({'detalle': {'verificacion': {'activa': True}}})
            self.assertIn('presupuesto_api_insuficiente', salida['analisis_limitaciones'])

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
        self.assertEqual(d['consumo_api']['intentos'], 3)
        self.assertRegex(d['consumo_api']['pedido'], r'^[0-9a-f]{24}$')
        ids_cache = set()
        for ruta in ('/clasificar', '/trabajos'):
            cache = self.post('prueba', ruta).json()
            cache = cache.get('resultado', cache)
            traza = cache.pop('consumo_api')
            self.assertEqual(traza['origen_cache'], d['consumo_api']['pedido'])
            self.assertEqual(traza['intentos'], 0)
            ids_cache.add(traza['pedido'])
            self.assertEqual(cache, {k: v for k, v in d.items() if k != 'consumo_api'})
        self.assertEqual(len(ids_cache | {d['consumo_api']['pedido']}), 3)
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

    def test_la_revision_truncada_de_una_rama_no_descarta_el_pedido_de_vereda(self):
        muebles, vereda = 'retiro_muebles', 'reparacion_vereda'
        pedido = 'Retiren el colchón y reparen las baldosas rotas.'
        hallazgos = [(muebles, 'Colchón descartado.'), (vereda, 'Baldosas partidas y sueltas.'),
                     ('retiro_poda', 'Ramas cortadas.'), ('obstruccion', 'Una rama obstruye el paso.')]

        def http(req, *_):
            esquema = json.loads(req.data)['response_format']['json_schema']['schema']['properties']
            final = 'length' if 'rama_desprendida' in esquema else 'stop'
            items = hallazgos if 'objetos' in esquema or 'bolsas_visibles' not in esquema else hallazgos[:1]
            datos = {'confirmadas': [{'key': k, 'evidencia': e} for k, e in items], 'dudosas': [],
                     'pedidos': [{'key': k, 'respaldo': 'compatible'} for k in (muebles, vereda)],
                     'objetos': ['colchón']}
            return {'usage': {'cost': .001, 'total_tokens': 10}, 'choices': [
                {'finish_reason': final, 'message': {'content': json.dumps(datos)}}]}

        with patch.object(V, '_pedir_http', side_effect=http):
            salida = self.cliente.post('/clasificar', files={'file': ('foto.jpg', self.foto, 'image/jpeg')},
                                       data={'modo': 'prueba', 'contexto': pedido}).json()
        self.assertEqual(salida['analisis_estado'], 'parcial')
        self.assertIn('solo_revision_ramas_pendiente', salida['analisis_limitaciones'])
        self.assertEqual(salida['solicitudes_sugeridas']['servicios'], [muebles, vereda])

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
        self.assertEqual(r.json()['consumo_api']['intentos'], 3)

    def test_prueba_con_reservas_conserva_el_limite_de_salida_de_la_etapa(self):
        responder = V._pedir_http.side_effect

        def http(req, *args):
            respuesta = responder(req, *args)
            respuesta['usage']['cost'] = .0002
            return respuesta

        for perfil, limite in ((perfil_con_reservas(), 550), (self.ps['prueba'], 1000)):
            with self.subTest(limite=limite):
                self.llamadas.clear()
                with (patch.object(self.S, 'PERFILES', self.ps | {'prueba': perfil}),
                      patch.object(V, 'MODELOS_MIN_1000', ('t/decision',)),
                      patch.object(V, '_pedir_http', side_effect=http)):
                    respuesta = self.post('prueba')
                self.assertEqual(respuesta.status_code, 200, respuesta.text)
                self.assertEqual([c['max_tokens'] for c in self.llamadas], [1400, 1400, limite])
                self.assertEqual(respuesta.json()['analisis_estado'], 'completo')
                self.assertEqual(respuesta.json()['consumo_api']['intentos'], 3)

    def test_prueba_reserva_la_decision_antes_del_apoyo_de_contenedores(self):
        perfil = perfil_con_reservas()
        clave = 'contenedor_humedos_lateral'
        local = {'predichas': [], 'probabilidades': [{'key': clave, 'score': .8}], 'gravedad': {}}
        costos = {'prueba_inicio': .000393925, 'prueba_retiros': .000588925,
                  'prueba_contenedores': .00101255, 'prueba_decision': .007686, 'prueba_tipos': .00063155}

        def http(req, *_):
            cuerpo = json.loads(req.data)
            propiedades = cuerpo['response_format']['json_schema']['schema']['properties']
            texto = cuerpo['messages'][0]['content']
            if 'objetos' in propiedades:
                etapa = 'prueba_decision'
            elif 'bolsas_visibles' in propiedades:
                etapa = 'prueba_retiros'
            elif not texto.startswith('Evaluá sólo'):
                etapa = 'prueba_inicio'
            else:
                etapa = 'prueba_contenedores' if 'reparacion_contenedor' in texto else 'prueba_tipos'
            self.llamadas.append(etapa)
            hallazgos = [{'key': clave, 'evidencia': 'Contenedor junto a la calle.'}]
            return {'usage': {'cost': costos[etapa], 'total_tokens': 15}, 'choices': [{
                'finish_reason': 'length' if etapa == 'prueba_contenedores' else 'stop',
                'message': {'content': json.dumps({'confirmadas': hallazgos if etapa in (
                    'prueba_decision', 'prueba_tipos') else [], 'dudosas': []})}}]}

        for activo in (False, True):
            with self.subTest(activo=activo):
                self.llamadas.clear()
                modos = self.ps | {'prueba': perfil} if activo else self.ps
                with (patch.object(self.S, 'PERFILES', modos),
                      patch.object(self.S, 'clasificar_local', return_value=local),
                      patch.object(V, '_pedir_http', side_effect=http)):
                    respuesta = self.post('prueba')
                self.assertEqual(respuesta.status_code, 200, respuesta.text)
                salida = respuesta.json()
                omitidas = {'prueba_contenedores'} if activo else set()
                self.assertCountEqual(self.llamadas, costos.keys() - omitidas)
                self.assertEqual(salida['costo_api'], .0093 if activo else .010313)
                self.assertTrue(salida['costo_api_completo'])
                self.assertEqual(salida['analisis_estado'], 'parcial')
                self.assertEqual('presupuesto_api_insuficiente' in salida['analisis_limitaciones'], activo)
                self.assertEqual('etapa_fallida' in salida['analisis_limitaciones'], not activo)
                self.assertEqual([x['key'] for x in salida['elementos_detectados']], [clave])

    def test_prueba_corta_despues_de_un_cargo_desconocido_o_fuera_de_reserva(self):
        perfil = perfil_con_reservas()
        for cargo, completo, limite in (
            (None, False, 'costo_api_sin_conciliar'),
            (.010001, True, 'tope_api_excedido'),
        ):
            with self.subTest(cargo=cargo):
                def http(req, *_):
                    return {'usage': {'cost': cargo, 'total_tokens': 15}, 'choices': [
                        {'finish_reason': 'stop', 'message': {'content': '{"confirmadas":[],"dudosas":[]}'}}]}
                with (patch.object(self.S, 'PERFILES', self.ps | {'prueba': perfil}),
                      patch.object(V, '_pedir_http', side_effect=http) as respuesta_externa):
                    salida = self.post('prueba').json()
                respuesta_externa.assert_called_once()
                self.assertEqual(salida['costo_api'], cargo or 0)
                self.assertEqual(salida['analisis_estado'], 'parcial')
                self.assertIs(salida['costo_api_completo'], completo)
                self.assertIn(limite, salida['analisis_limitaciones'])

    def test_prueba_solo_comparte_una_chapa_corroborada(self):
        import patentes

        claves = ('vehiculo_abandonado', 'vehiculo_mal_estacionado')
        una = [{'caja': [2, 4, 20, 14], 'confianza': .9}]
        chica = [{'caja': [4, 16, 18, 23], 'confianza': .95}]
        otra = [{'caja': [4, 16, 19, 23], 'confianza': .8}]
        lector_chapa = 't/chapa'
        coincide = ('A123BCD', 'A123BCD')
        for cajas, lecturas, esperado in (
                (una, (coincide,), 'A123BCD'),
                (una, (('A123BCD', 'A128BCD'),), None),
                (chica, (), None),
                (chica + una, (coincide,), 'A123BCD'),
                (una + otra, (coincide, (None, None)), 'A123BCD'),
                (una + otra, (coincide, ('AB123CD', 'AB123CD')), None),
                (una + otra, (coincide, (None, 'respuesta_inutilizable')), None),
                (una + otra, (coincide, (None, 'sin_campo_patente')), None)):
            with self.subTest(cajas=len(cajas), lecturas=lecturas):
                self.llamadas.clear()
                votos_ocr = [{'patente': p, 'lectura': p, 'confianza': .9}
                             for p, _ in lecturas]
                votos_externos = iter(v for _, v in lecturas)

                def http(req, *_):
                    cuerpo = json.loads(req.data)
                    self.llamadas.append(cuerpo['model'])
                    fin = 'stop'
                    if cuerpo['model'] == lector_chapa:
                        lectura = next(votos_externos)
                        if lectura == 'respuesta_inutilizable':
                            fin = 'length'
                        contenido = {} if lectura == 'sin_campo_patente' else {'patente': lectura, 'mismo_vehiculo': True}
                    else:
                        hallazgos = [{'key': k, 'evidencia': 'Una moto junto al poste.'} for k in claves]
                        contenido = {'confirmadas': hallazgos if cuerpo['model'] == 't/decision' else [],
                                     'dudosas': []}
                    return {'usage': {'cost': .001, 'total_tokens': 10}, 'choices': [
                        {'finish_reason': fin, 'message': {'content': json.dumps(contenido)}}]}

                with (patch.multiple(patentes, LECTORES=(lector_chapa,), ANCHO_MINIMO=15),
                      patch.object(patentes, 'disponible', return_value=True),
                      patch.object(patentes, 'detectar', return_value=cajas),
                      patch.object(patentes, 'leer_local', side_effect=votos_ocr),
                      patch.object(V, '_pedir_http', side_effect=http),
                      patch.object(self.S, '_cache', __import__('collections').OrderedDict())):
                    r = self.post('prueba')
                self.assertEqual(r.status_code, 200, r.text)
                salida = r.json()
                self.assertEqual({p['key']: p.get('patente') for p in salida['problemas']},
                                 dict.fromkeys(claves, esperado))
                self.assertEqual(self.llamadas.count(lector_chapa), len(lecturas))
                self.assertAlmostEqual(salida['costo_api'], .003 + .001 * len(lecturas))
                fallo_recorte = any(v in ('respuesta_inutilizable', 'sin_campo_patente') for _, v in lecturas)
                self.assertEqual('etapa_fallida' in salida['analisis_limitaciones'], fallo_recorte)

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

    def test_voluminosos_tienen_una_sola_clave_por_ruta_y_modo(self):
        from time import monotonic, sleep

        anterior = 'retiro_objetos'
        actual = 'retiro_muebles'
        municipal = '1645729658238'
        rubrica = V._prompt_sistema(self.S.CATEGORIAS)
        transporte = V._pedir_http.side_effect
        contexto = 'Hay un caño grueso tirado en la vereda.'
        poda = {'key': 'retiro_poda', 'evidencia': 'Ramas cortadas apiladas sobre la vereda.'}
        legado = {'key': anterior, 'evidencia': 'Caño grueso, suelto y tirado.', 'elemento': 'canos'}
        canonico = {'key': actual, 'evidencia': 'Caño grueso descartado en la vereda.',
                    'objetos': ['caño de hierro'], 'objeto_cant': 1}
        casos = (([legado], ['Caño grueso']), ([legado, canonico], ['caño de hierro']),
                 ([{'key': anterior, 'evidencia': 'Poste anclado todavía en uso.'}], []))

        for modo, perfil in self.ps.items():
            vieja = replace(perfil, version='version-anterior')
            self.S._cache[M.identidad(self.foto, '', True, vieja)] = {'version': '11', 'key': anterior}
            for lectura, nombres in casos:
                for texto in ('', contexto):
                    with self.subTest(modo=modo, lectura=lectura, con_texto=bool(texto)):
                        def responder(req, *_):
                            datos = json.loads(req.data)
                            sistema = datos['messages'][0]['content']
                            if datos.get('response_format'):
                                self.assertNotIn(anterior, sistema)
                                hallazgos = [{k: c[k] for k in ('key', 'evidencia')} for c in [*lectura, poda]]
                                valor = {'confirmadas': hallazgos, 'dudosas': [],
                                         'objetos': canonico['objetos'] if canonico in lectura else [],
                                         'objeto_cant': 1 if canonico in lectura else None,
                                         'pedidos': [{'key': anterior, 'respaldo': 'neutral'}]}
                            elif sistema == rubrica:
                                valor = {'categorias': [*lectura, poda], 'foto_corresponde': True,
                                         'categorias_contexto': [{'codigo': municipal}, {'key': anterior}]}
                            else:
                                return transporte(req)
                            return {'usage': {'cost': .0001}, 'choices': [{'finish_reason': 'stop',
                                'message': {'content': json.dumps(valor)}}]}

                        def enviar(ruta):
                            return self.cliente.post(ruta, files={'file': ('foto.jpg', self.foto, 'image/jpeg')},
                                                     data={'modo': modo, 'contexto': texto})

                        probabilidades = {poda['key']: .9}
                        if nombres:
                            probabilidades[actual] = .9
                        predichas = [{'key': k, 'score': s} for k, s in probabilidades.items()]
                        with (patch.object(self.S, 'CACHE_MAX', 0),
                              patch.object(self.S, 'clasificar_local', return_value={
                                  'predichas': predichas, 'probabilidades': predichas, 'gravedad': {}}),
                              patch.object(V, 'SEGUNDA_MIRADA_VOLUMINOSO', False),
                              patch.object(V, '_pedir_http', side_effect=responder)):
                            directo = enviar('/clasificar/')
                            self.assertEqual(directo.status_code, 200, directo.text)
                            inicio = enviar('/trabajos/')
                            self.assertEqual(inicio.status_code, 202, inicio.text)
                            ruta = '/trabajos/' + inicio.json()['trabajo']
                            vence = monotonic() + 3
                            trabajo = self.cliente.get(ruta).json()
                            while trabajo['estado'] in ('en_cola', 'procesando') and monotonic() < vence:
                                sleep(.01)
                                trabajo = self.cliente.get(ruta).json()
                            self.assertEqual(trabajo['estado'], 'listo', trabajo)

                        for resultado in (directo.json(), trabajo['resultado']):
                            self.assertEqual(resultado['version'], '12')
                            self.assertEqual(resultado['modo_version'], perfil.version)
                            serializado = json.dumps(resultado)
                            self.assertNotIn(anterior, serializado)
                            self.assertNotIn(municipal, serializado)
                            hallados = [c for c in resultado['problemas'] if c['key'] == actual]
                            self.assertEqual(len(hallados), bool(nombres), resultado['problemas'])
                            if nombres:
                                self.assertEqual(hallados[0]['objetos'], nombres)
                                self.assertNotIn('elemento', hallados[0])
                                if canonico in lectura:
                                    self.assertEqual(hallados[0]['objeto_cant'], 1)
                            self.assertIn(poda['key'], {c['key'] for c in resultado['problemas']})
        self.assertNotIn(anterior, self.cliente.get('/salud').text)
        self.assertNotIn(anterior, self.cliente.get('/').text)

    def test_prueba_publica_recuento_sin_agregar_llamadas(self):
        for dirigida, final, tapadas, minimo, exacto, bolsas in (
            (5, 5, False, 5, True, 5), (6, 7, False, 6, False, None),
            (5, 5, True, 5, False, None), (5, None, False, 5, False, None),
            (5, True, False, 5, False, None), (0, 0, False, 0, True, 0),
            (0, None, False, None, False, None), (None, 6, False, 6, False, None),
            (None, None, False, None, False, None), (15, 15, False, 15, True, 15),
            (18, 18, False, 18, True, None),
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
                                 [('retiro_escombros', bolsas)])
                if minimo is None:
                    self.assertNotIn('verificacion_escombros', salida)
                else:
                    self.assertEqual(salida['verificacion_escombros']['bolsas_minimo'], minimo)
                    self.assertIs(salida['verificacion_escombros']['bolsas_recuento_completo'], exacto)
                    self.assertEqual(salida['verificacion_escombros']['bolsas_fuentes'],
                                     sum(type(cuenta) is int for cuenta in (dirigida, final)))

    def test_prueba_separa_recuento_de_alcance_y_servicio(self):
        clave = 'retiro_escombros'
        local = {'predichas': [], 'gravedad': {}, 'escombros_mixtos': .99,
                 'probabilidades': [{'key': clave, 'score': .99}]}
        foto = {'file': ('foto.jpg', self.foto, 'image/jpeg')}
        for primer, segundo, medida, residuos_aparte, publica in (
                ((5, False), (6, True), (5, False), True, True),
                ((0, False), (0, False), (0, True), True, True),
                ((0, False), (5, False), None, True, True),
                ((5, False), (None, None), None, True, True),
                ((5, True), (7, True), (5, False), False, True),
                ((5, True), (7, True), None, False, False)):
            etapas = []

            def responder(modelo, mensajes, **opciones):
                etapa = opciones['etapa']
                etapas.append(etapa)
                if etapa != 'prueba_alcance':
                    return '{"confirmadas":[],"dudosas":[]}'
                self.assertIn('bolsas_visibles', opciones['esquema']['properties'])
                self.assertIn('Excluí bolsones', mensajes[0]['content'])
                numero, tapadas = primer if modelo == 't/dirigidos' else segundo
                return json.dumps({'ubicacion': 'publica' if publica else 'privada', 'presentacion': 'sacos_portables_o_suelto',
                                   'material': 'obra_visible', 'residuos_comunes_aparte': 'si' if residuos_aparte else 'no',
                                   'evidencia': 'Pila de obra junto al cordón.',
                                   'bolsas_visibles': numero, 'bolsas_adicionales_posibles': tapadas})

            with (self.subTest(primer=primer, segundo=segundo), patch.object(self.S, 'CACHE_MAX', 0),
                  patch.object(self.S, 'clasificar_local', return_value=local),
                  patch.object(V, '_llamar', side_effect=responder)):
                r = self.cliente.post('/clasificar', files=foto, data={'modo': 'prueba'})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertCountEqual(etapas, ['prueba_inicio', 'prueba_retiros', 'prueba_decision'] + ['prueba_alcance'] * 2)
            salida = r.json()
            problemas = [p for p in salida['problemas'] if p['key'] == clave]
            self.assertEqual(len(problemas), int(residuos_aparte and publica))
            if medida is None:
                self.assertNotIn('verificacion_escombros', salida)
                self.assertTrue(all(p['bolsas'] is None for p in problemas))
            else:
                minimo, completo = medida
                recuento = salida['verificacion_escombros']
                self.assertEqual((recuento['bolsas_minimo'], recuento['bolsas_recuento_completo'],
                                  recuento['bolsas_fuentes']), (minimo, completo, 2))
                for problema in problemas:
                    self.assertEqual(problema['bolsas'], minimo if completo else None)

    def test_prueba_el_alcance_deja_en_duda_el_material_no_corrobado(self):
        clave = 'retiro_escombros'
        local = {'predichas': [], 'gravedad': {}, 'escombros_mixtos': .99,
                 'probabilidades': [{'key': clave, 'score': .99}]}
        datos = {'modo': 'prueba', 'contexto': 'Pido retiro de escombros'}
        archivos = {'file': ('foto.jpg', self.foto, 'image/jpeg')}
        for materiales, confirmado in (
                (('tapado_o_ambiguo', 'tapado_o_ambiguo'), False),
                (('obra_visible', 'tapado_o_ambiguo'), False),
                (('obra_visible', 'obra_por_presentacion'), True),
                (('obra_por_presentacion', 'obra_por_presentacion'), True)):
            with self.subTest(materiales=materiales):
                etapas = []

                def responder(modelo, mensajes, **opciones):
                    etapa = opciones['etapa']
                    etapas.append(etapa)
                    valor = {'confirmadas': [], 'dudosas': []}
                    if etapa == 'prueba_alcance':
                        self.assertIn('dos señales externas distintas', mensajes[0]['content'])
                        self.assertIn('obra_por_presentacion', opciones['esquema']['properties']['material']['enum'])
                        valor = {'ubicacion': 'publica', 'presentacion': 'sacos_portables_o_suelto',
                                 'material': materiales[0 if modelo == 't/dirigidos' else 1],
                                 'residuos_comunes_aparte': 'si', 'evidencia': 'Un grupo de sacos junto a la vereda.'}
                    elif etapa == 'prueba_decision':
                        valor['confirmadas'] = [{'key': 'recoleccion', 'evidencia': 'Envases aparte de los sacos.'}]
                        valor['pedidos'] = [{'key': clave, 'respaldo': 'compatible'}]
                    return json.dumps(valor)

                with (patch.object(self.S, 'CACHE_MAX', 0),
                      patch.object(self.S, 'clasificar_local', return_value=local),
                      patch.object(V, '_llamar', side_effect=responder)):
                    r = self.cliente.post('/clasificar', files=archivos, data=datos)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertCountEqual(etapas, ['prueba_inicio', 'prueba_retiros', 'prueba_decision'] + ['prueba_alcance'] * 2)
                salida = r.json()
                hallazgos = {p['key'] for p in salida['problemas']}
                self.assertEqual(hallazgos, {'recoleccion', clave} if confirmado else {'recoleccion'})
                self.assertEqual(salida['en_duda'], [] if confirmado else [clave])
                self.assertEqual(salida['categorias_contexto'][0]['respaldo_visual'], 'compatible' if confirmado else 'neutral')

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

    def test_prueba_no_confirma_la_vidriera_como_puesto(self):
        import prueba_completo as P

        for evidencia, esperado in (
            ('Muestras alineadas en la vidriera del local.', set()),
            ('Puesto de peras en la vereda frente a la vidriera.', {'manteros'}),
        ):
            lectura = {'confirmadas': {'manteros': evidencia}, 'dudosas': {}}
            with self.subTest(evidencia=evidencia), patch.object(P, '_vistas', return_value=[]), \
                    patch.object(P, '_leer', return_value=lectura) as leer:
                fuentes, _, _ = P.clasificar(Image.new('RGB', (24, 24)), {}, ('manteros',),
                                             ('decision', 'dirigidos', 'alcance'))
                self.assertEqual(set(fuentes), esperado)
                self.assertEqual(leer.call_count, 3)

    def test_prueba_la_vidriera_no_respalda_el_pedido_de_venta_ambulante(self):
        interior = 'Muestras alineadas en la vidriera del local.'
        exterior = 'Puesto de peras en la vereda frente a la vidriera.'
        vereda = {'key': 'reparacion_vereda', 'evidencia': 'Varias baldosas rotas.'}
        casos = [(interior, [], 'contradice', []),
                 (interior, [vereda], 'contradice', ['reparacion_vereda']),
                 (exterior, [], 'compatible', ['manteros']),
                 ('Puesto de peras en la vereda, no en la vidriera.', [], 'compatible', ['manteros'])]
        for evidencia, adicionales, respaldo, servicios in casos:
            def responder(req, *_):
                lectura = {'confirmadas': [], 'dudosas': []}
                if json.loads(req.data)['model'] == 't/decision':
                    lectura['confirmadas'] = [{'key': 'manteros', 'evidencia': evidencia}, *adicionales]
                    lectura['pedidos'] = [
                        {'key': hallazgo['key'], 'respaldo': 'compatible'}
                        for hallazgo in lectura['confirmadas']]
                return {'usage': {'cost': .001}, 'choices': [
                    {'finish_reason': 'stop', 'message': {'content': json.dumps(lectura)}}]}

            contexto = 'Vendedor ambulante y vereda rota' if adicionales else 'Vendedor ambulante'
            with (self.subTest(evidencia=evidencia, adicionales=bool(adicionales)),
                  patch.object(self.S, 'CACHE_MAX', 0), patch.object(V, '_pedir_http', side_effect=responder)):
                r = self.cliente.post('/clasificar', files={'file': ('foto.jpg', self.foto, 'image/jpeg')},
                                      data={'modo': 'prueba', 'contexto': contexto})
                self.assertEqual(r.status_code, 200, r.text)
                salida = r.json()
                estados = {c['key']: c['respaldo_visual'] for c in salida['categorias_contexto']}
                self.assertEqual(estados['manteros'], respaldo)
                self.assertEqual(salida['foto_valida'], bool(servicios))
                self.assertEqual([p['key'] for p in salida['problemas']], servicios)
                self.assertEqual(salida['solicitudes_sugeridas']['servicios'], servicios)
                self.assertEqual(salida['consumo_api']['intentos'], 3)

    def test_prueba_la_vidriera_en_duda_no_respalda_el_pedido_de_venta(self):
        foto = ('foto.jpg', self.foto, 'image/jpeg')
        venta, vereda = 'manteros', 'reparacion_vereda'
        for mixto in (False, True):
            def responder(req, *_):
                lectura = {'confirmadas': [], 'dudosas': []}
                if json.loads(req.data)['model'] == 't/decision':
                    lectura['dudosas'] = [{'key': venta, 'evidencia': 'Muestras en la vidriera.'}]
                    lectura['pedidos'] = [{'key': venta, 'respaldo': 'compatible'}]
                    if mixto:
                        lectura['confirmadas'] = [{'key': vereda, 'evidencia': 'Faltan baldosas.'}]
                        lectura['pedidos'].append({'key': vereda, 'respaldo': 'compatible'})
                return {'usage': {'cost': .001}, 'choices': [
                    {'finish_reason': 'stop', 'message': {'content': json.dumps(lectura)}}]}

            contexto = 'Vendedor ambulante' + (' y vereda rota' if mixto else '')
            with (self.subTest(mixto=mixto), patch.object(self.S, 'CACHE_MAX', 0),
                  patch.object(V, '_pedir_http', side_effect=responder)):
                r = self.cliente.post('/clasificar', files={'file': foto},
                                      data={'modo': 'prueba', 'contexto': contexto})
                self.assertEqual(r.status_code, 200, r.text)
                salida = r.json()
                respaldos = {p['key']: p['respaldo_visual'] for p in salida['categorias_contexto']}
                self.assertEqual(respaldos[venta], 'neutral')
                self.assertIn(venta, salida['en_duda'])
                self.assertEqual(salida['foto_valida_estado'], 'corresponde' if mixto else 'sin_opinion')
                propuesta = salida['solicitudes_sugeridas']
                self.assertEqual(propuesta['criterio'], 'pedidos_explicitos')
                self.assertEqual(propuesta['servicios'], [vereda] if mixto else [])

    def test_prueba_un_hallazgo_de_la_misma_categoria_no_cambia_el_respaldo_del_pedido(self):
        foto = ('foto.jpg', self.foto, 'image/jpeg')
        retiro, vereda = 'retiro_muebles', 'reparacion_vereda'
        casos = (
            ('contradice', True, True, [retiro, vereda], [], [vereda]),
            ('neutral', True, True, [retiro, vereda], [], [vereda]),
            ('compatible', True, True, [retiro, vereda], [], [retiro, vereda]),
            ('contradice', False, False, [], [retiro], []),
            ('neutral', False, None, [retiro], [], []),
        )
        for respaldo, mixto, validez, hallazgos, descartados, servicios in casos:
            with self.subTest(respaldo=respaldo, mixto=mixto):
                objeto = 'ropero' if respaldo == 'compatible' else 'heladera blanca'
                contexto = f'Retiro de {objeto}' + (' y reparación de baldosas' if mixto else '')

                def responder(req, *_):
                    lectura = {'confirmadas': [], 'dudosas': []}
                    if json.loads(req.data)['model'] == 't/decision':
                        lectura['confirmadas'] = [{'key': retiro, 'evidencia': 'Ropero descartado.'}]
                        lectura['pedidos'] = [{'key': retiro, 'respaldo': respaldo}]
                        if mixto:
                            lectura['confirmadas'].append({'key': vereda, 'evidencia': 'Faltan baldosas.'})
                            lectura['pedidos'].append({'key': vereda, 'respaldo': 'compatible'})
                    return {'usage': {'cost': .001}, 'choices': [
                        {'finish_reason': 'stop', 'message': {'content': json.dumps(lectura)}}]}

                with patch.object(self.S, 'CACHE_MAX', 0), patch.object(V, '_pedir_http', side_effect=responder):
                    r = self.cliente.post('/clasificar', files={'file': foto},
                                          data={'modo': 'prueba', 'contexto': contexto})
                self.assertEqual(r.status_code, 200, r.text)
                salida = r.json()
                self.assertEqual(salida['categorias_contexto'][0]['respaldo_visual'], respaldo)
                self.assertIs(salida['foto_valida'], validez)
                self.assertEqual([p['key'] for p in salida['problemas']], hallazgos)
                self.assertEqual([p['key'] for p in salida['descartados_por_foto']], descartados)
                self.assertEqual(salida['solicitudes_sugeridas']['servicios'], servicios)
                if validez is None:
                    self.assertEqual(salida['foto_valida_estado'], 'sin_opinion')
                    self.assertEqual(salida['solicitudes_sugeridas']['estado'], 'pendiente')
                    self.assertEqual(salida['solicitudes_sugeridas']['criterio'], 'pedidos_explicitos')

    def test_prueba_considera_la_lectura_previa_para_el_pedido_de_ocupacion(self):
        ocupacion = {'key': 'ocupacion_comercial', 'evidencia': 'Sillas y ropa exhibidas frente al local.'}
        muebles = {'key': 'retiro_muebles', 'evidencia': 'Sillón junto al cordón.'}
        foto = ('foto.jpg', self.foto, 'image/jpeg')
        casos = (
            (True, True, 'compatible', ['ocupacion_comercial']),
            (False, True, 'compatible', []),
            (True, False, 'compatible', []),
            (True, True, 'neutral', []),
            (True, True, None, ['retiro_muebles']),
        )
        for previa, duda, respaldo, servicios in casos:
            with self.subTest(previa=previa, duda=duda, respaldo=respaldo):
                def responder(req, *_):
                    cuerpo = json.loads(req.data)
                    inicial = 'Aclaraciones para esta escena' in cuerpo['messages'][0]['content']
                    lectura = {'confirmadas': [ocupacion] if previa and inicial else [], 'dudosas': []}
                    if cuerpo['model'] == 't/decision':
                        lectura['confirmadas'] = [muebles]
                        lectura['dudosas'] = [ocupacion] if duda else []
                        if respaldo:
                            lectura['pedidos'] = [{'key': 'ocupacion_comercial', 'respaldo': respaldo}]
                    return {'usage': {'cost': .001}, 'choices': [
                        {'finish_reason': 'stop', 'message': {'content': json.dumps(lectura)}}]}

                texto = 'Uso indebido del espacio público' if respaldo else ''
                with patch.object(self.S, 'CACHE_MAX', 0), patch.object(V, '_pedir_http', side_effect=responder):
                    r = self.cliente.post('/clasificar', files={'file': foto},
                                          data={'modo': 'prueba', 'contexto': texto})
                self.assertEqual(r.status_code, 200, r.text)
                salida = r.json()
                self.assertEqual(salida['analisis_estado'], 'completo')
                self.assertEqual([p['key'] for p in salida['problemas']], ['retiro_muebles'])
                self.assertEqual('ocupacion_comercial' in salida['en_duda'], duda)
                self.assertEqual(salida['solicitudes_sugeridas']['servicios'], servicios)
                if servicios == ['ocupacion_comercial']:
                    self.assertEqual(salida['reclamo_foto']['codigo'], '118001')
                    self.assertTrue(salida['solicitudes_sugeridas']['requiere_confirmacion'])
                    self.assertTrue(salida['conclusion']['requiere_revision'])

    def test_prueba_pide_evidencia_separada_para_sumar_obstruccion_a_ocupacion(self):
        foto = ('foto.jpg', self.foto, 'image/jpeg')
        datos = {'modo': 'prueba', 'contexto': 'Ocupación de la vereda por local comercial'}
        ocupacion, obstruccion = 'ocupacion_comercial', 'obstruccion'
        equipo = 'La máquina de garra del local ocupa parte del paso.'
        barrera = 'Una pizarra separada impide el paso junto al cordón.'
        casos = (
            (equipo.lower(), None, [ocupacion], [obstruccion], 'neutral'),
            ('Un cartel y varias sillas ocupan parte del paso.', 'dudosas', [ocupacion], [obstruccion], 'neutral'),
            (barrera, None, [ocupacion, obstruccion], [], 'compatible'),
            (barrera, 'confirmadas', [ocupacion, obstruccion], [], 'compatible'),
        )
        for evidencia, inicio, servicios, dudas, respaldo in casos:
            with self.subTest(evidencia=evidencia, inicio=inicio):
                def responder(req, *_):
                    cuerpo = json.loads(req.data)
                    lectura = {'confirmadas': [], 'dudosas': []}
                    if cuerpo['model'] == 't/decision':
                        lectura['confirmadas'] = [{'key': ocupacion, 'evidencia': equipo},
                                                  {'key': obstruccion, 'evidencia': evidencia}]
                        lectura['pedidos'] = [{'key': k, 'respaldo': 'compatible'} for k in (ocupacion, obstruccion)]
                    elif inicio and 'Claves válidas' in cuerpo['messages'][0]['content']:
                        lectura[inicio] = [{'key': obstruccion, 'evidencia': evidencia}]
                    return {'usage': {'cost': .001}, 'choices': [
                        {'finish_reason': 'stop', 'message': {'content': json.dumps(lectura)}}]}

                with patch.object(self.S, 'CACHE_MAX', 0), patch.object(V, '_pedir_http', side_effect=responder):
                    respuesta = self.cliente.post('/clasificar/', files={'file': foto}, data=datos)
                self.assertEqual(respuesta.status_code, 200, respuesta.text)
                salida = respuesta.json()
                self.assertEqual(salida['solicitudes_sugeridas']['servicios'], servicios)
                self.assertEqual(salida['en_duda'], dudas)
                self.assertEqual({c['key']: c['respaldo_visual'] for c in salida['categorias_contexto']},
                                 {ocupacion: 'compatible', obstruccion: respaldo})

    def test_clasificar_acepta_multipart_con_barra_final_sin_redirigir(self):
        archivos = {'file': ('foto.jpg', self.foto, 'image/jpeg')}
        for ruta in ('/clasificar', '/clasificar/'):
            respuesta = self.cliente.post(ruta, params={'verificar': '0'}, data={'modo': 'prueba'},
                                          files=archivos, follow_redirects=False)
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.json()['modo'], 'prueba')
        self.assertEqual(self.llamadas, [])

    def test_prueba_distingue_exclusion_y_duda_de_recoleccion_junto_al_bolson(self):
        datos = {'modo': 'prueba', 'contexto': 'Recolección de basura y barrido de la vereda'}
        archivos = {'file': ('foto.jpg', self.foto, 'image/jpeg')}
        for dirigida, final in ((True, True), (True, False), (False, True), (True, None), (None, True),
                                (False, False), (None, None), (1, True)):
            with self.subTest(dirigida=dirigida, final=final):
                llamadas = []
                votos_dentro = sum(voto is True for voto in (dirigida, final))

                def responder(modelo, mensajes, **opciones):
                    etapa = opciones['etapa']
                    llamadas.append(etapa)
                    esquema = opciones['esquema']
                    usa_bolson = etapa in ('prueba_retiros', 'prueba_decision')
                    self.assertEqual('solo_residuos_del_bolson' in esquema['required'], usa_bolson)
                    valor = {'confirmadas': [], 'dudosas': []}
                    if usa_bolson:
                        self.assertIn('volante aislado', mensajes[0]['content'])
                        valor['solo_residuos_del_bolson'] = dirigida if etapa == 'prueba_retiros' else final
                    if etapa == 'prueba_decision':
                        valor['confirmadas'] = [
                            {'key': 'recoleccion', 'evidencia': 'Restos visibles cerca de un bolsón de izado.'},
                            {'key': 'barrido', 'evidencia': 'Tierra acumulada sobre la vereda.'},
                        ]
                        valor['pedidos'] = [{'key': k, 'respaldo': 'compatible'} for k in ('recoleccion', 'barrido')]
                    return json.dumps(valor)

                with patch.object(self.S, 'CACHE_MAX', 0), patch.object(V, '_llamar', side_effect=responder):
                    r = self.cliente.post('/clasificar', files=archivos, data=datos)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertCountEqual(llamadas, ['prueba_inicio', 'prueba_retiros', 'prueba_decision'])
                salida = r.json()
                esperados = {'barrido'} if votos_dentro else {'recoleccion', 'barrido'}
                self.assertEqual({p['key'] for p in salida['problemas']}, esperados)
                self.assertEqual(salida['en_duda'], ['recoleccion'] if votos_dentro == 1 else [])
                propuesta = salida['solicitudes_sugeridas']
                self.assertEqual(set(propuesta['candidatos']), esperados)
                self.assertEqual(propuesta['servicios'], ['barrido'] if votos_dentro else [])
                self.assertEqual(propuesta.get('criterio'), 'pedidos_explicitos' if votos_dentro == 1 else None)
                respaldo = {p['key']: p['respaldo_visual'] for p in salida['categorias_contexto']}
                self.assertEqual(respaldo['recoleccion'], ('compatible', 'neutral', 'contradice')[votos_dentro])

    def test_prueba_pide_describir_el_paso_si_hay_indicios_de_obra(self):
        from prueba_completo import _decision
        claves = ('retiro_escombros', 'obstruccion')
        self.assertIn('nombrá las asas', _decision([], {'retiro_escombros': .85}, claves))
        self.assertNotIn('nombrá las asas', _decision([], {'retiro_escombros': .05}, claves))

    def test_prueba_incluye_la_cazuela_vacia_en_los_criterios(self):
        from prueba_completo import _decision, _inicio
        claves = ('plantacion_arbol', 'reparacion_vereda', 'reparacion_cordon')
        decision = _decision([], {}, claves)
        self.assertIn('plantera o cazuela vacía y abierta', decision)
        self.assertIn('un árbol enfermo o dañado no es esta categoría', decision)
        self.assertIn('El borde construido de la cazuela no es una rotura de vereda', decision)
        self.assertIn('una separación con el pavimento detrás del cordón', decision)
        inicio = _inicio({}, claves)
        self.assertIn('plantacion_arbol: plantera o cazuela vacía y abierta', inicio)

    def test_prueba_pasa_las_observaciones_previas_como_datos(self):
        decisiones = []
        for color in ('azul', 'verde', None):
            with self.subTest(color=color):
                evidencia = f'Cajón "{color}" junto al cordón.' if color else None

                def responder(req, *_):
                    cuerpo = json.loads(req.data)
                    self.llamadas.append(cuerpo)
                    if cuerpo['model'] == 't/decision':
                        hallazgos = [{'key': 'retiro_muebles', 'evidencia': 'Cajón descartado.'}]
                    else:
                        hallazgos = [{'key': 'retiro_muebles', 'evidencia': f'{evidencia} 1234'}] if evidencia else []
                    valor = {'confirmadas': hallazgos, 'dudosas': []}
                    return {'usage': {'cost': .001}, 'choices': [
                        {'finish_reason': 'stop', 'message': {'content': json.dumps(valor)}}]}

                self.llamadas.clear()
                with (patch.object(self.S, 'CACHE_MAX', 0),
                      patch.object(V, '_pedir_http', side_effect=responder)):
                    r = self.post('prueba')
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual([p['key'] for p in r.json()['problemas']], ['retiro_muebles'])
                self.assertEqual(len(self.llamadas), 3)
                [final] = [c for c in self.llamadas if c['model'] == 't/decision']
                sistema, usuario = final['messages']
                self.assertEqual((sistema['role'], usuario['role']), ('system', 'user'))
                self.assertEqual(sum(p['type'] == 'image_url' for p in usuario['content']), 1)
                datos = [json.loads(p['text']) for p in usuario['content'] if p['type'] == 'text']
                if evidencia:
                    self.assertNotIn(evidencia, sistema['content'])
                    self.assertEqual(datos, [{'observaciones_previas': [
                        {'key': 'retiro_muebles', 'estado': 'confirmadas', 'evidencia': evidencia}]}])
                    decisiones.append(final)
                else:
                    self.assertEqual(datos, [])
        a, b = decisiones
        self.assertEqual(V._clave_cache_prompt(a['model'], a['messages']),
                         V._clave_cache_prompt(b['model'], b['messages']))
        self.assertNotEqual(a, b)

    def test_prueba_separa_el_texto_vecinal_de_los_hallazgos(self):
        foto = ('foto.jpg', self.foto, 'image/jpeg')
        pedido = 'Hay baldosas rotas'

        def responder(req, *_):
            cuerpo = json.loads(req.data)
            self.llamadas.append(cuerpo)
            es_decision = cuerpo['model'] == 't/decision'
            contenido = {'confirmadas': [{'key': 'retiro_muebles', 'evidencia': 'Silla descartada.'}],
                         'dudosas': []}
            propiedades = cuerpo['response_format']['json_schema']['schema']['properties']
            self.assertEqual('pedidos' in propiedades, es_decision)
            self.assertNotIn(pedido, cuerpo['messages'][0]['content'])
            self.assertEqual(pedido in json.dumps(cuerpo['messages'][1]), es_decision)
            if es_decision:
                datos = [json.loads(p['text']) for p in cuerpo['messages'][1]['content'] if p['type'] == 'text']
                self.assertEqual(datos[0], {'contexto_vecinal': pedido})
                self.assertEqual(datos[1], {'observaciones_previas': [
                    {'key': 'retiro_muebles', 'estado': 'confirmadas', 'evidencia': 'Silla descartada.'}]})
                contenido['pedidos'] = [{'key': 'reparacion_vereda', 'respaldo': 'contradice'}]
            return {'usage': {'cost': .001, 'total_tokens': 15}, 'choices': [
                {'finish_reason': 'stop', 'message': {'content': json.dumps(contenido)}}]}

        datos = {'modo': 'prueba', 'contexto': pedido}
        with patch.object(V, '_pedir_http', side_effect=responder):
            r = self.cliente.post('/clasificar', files={'file': foto}, data=datos)
            self.assertEqual(r.status_code, 200, r.text)
            salida = r.json()
            self.assertEqual(salida['foto_valida_estado'], 'no_corresponde')
            self.assertIs(salida['foto_valida'], False)
            self.assertEqual(salida['problemas'], [])
            self.assertEqual([p['key'] for p in salida['descartados_por_foto']], ['retiro_muebles'])
            self.assertEqual([p['key'] for p in salida['categorias_contexto']], ['reparacion_vereda'])
            self.assertEqual(salida['costo_api'], .003)
            self.assertTrue(salida['costo_api_completo'])
            cache = self.cliente.post('/trabajos', files={'file': foto}, data=datos).json()['resultado']
            traza = cache.pop('consumo_api')
            self.assertEqual(traza['intentos'], 0)
            self.assertEqual(traza['origen_cache'], salida.pop('consumo_api')['pedido'])
            self.assertEqual(cache, salida)
        self.assertEqual(len(self.llamadas), 3)

    def test_prueba_rechaza_otros_usos_antes_de_llamar(self):
        f = ('foto.jpg', self.foto, 'image/jpeg')
        self.assertEqual(self.cliente.post('/comparar', files={'file_a': f, 'file_b': f},
            data={'modo': 'prueba'}).status_code, 422)
        self.assertEqual(self.llamadas, [])

    def test_prueba_conserva_el_pedido_de_ocupacion_y_distingue_otra_foto(self):
        foto = ('foto.jpg', self.foto, 'image/jpeg')
        datos = {'modo': 'prueba', 'contexto': 'Uso indebido del espacio público'}
        for clave, evidencia, respaldo, estado, servicios in (
            ('ocupacion_comercial', 'Ropa exhibida por el local en la vereda.',
             'compatible', 'compatible', ['ocupacion_comercial']),
            ('contenedor_desbordado', 'Bolsas que salen del contenedor lleno.',
             'contradice', 'no_corresponde', []),
        ):
            with self.subTest(clave=clave):
                def responder(req, *_):
                    cuerpo = json.loads(req.data)
                    contenido = {'confirmadas': [], 'dudosas': []}
                    if cuerpo['model'] == 't/decision':
                        contenido['confirmadas'] = [{'key': clave, 'evidencia': evidencia}]
                        contenido['pedidos'] = [{'key': 'ocupacion_comercial', 'respaldo': respaldo}]
                    return {'usage': {'cost': .001, 'total_tokens': 15}, 'choices': [
                        {'finish_reason': 'stop', 'message': {'content': json.dumps(contenido)}}]}

                with patch.object(self.S, '_cache', __import__('collections').OrderedDict()), \
                        patch.object(V, '_pedir_http', side_effect=responder):
                    r = self.cliente.post('/clasificar', files={'file': foto}, data=datos)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()['reclamo_foto']['estado'], estado)
                self.assertEqual(r.json()['solicitudes_sugeridas']['servicios'], servicios)

    def test_prueba_conserva_la_vereda_pedida_junto_con_la_revision_de_ocupacion(self):
        from time import monotonic, sleep

        foto = ('foto.jpg', self.foto, 'image/jpeg')
        datos = {'modo': 'prueba', 'contexto': 'Ocupación comercial y baldosas rotas'}
        for respaldo, ocupacion_vista, estado, esperados in (
            ('compatible', True, 'compatible', ['ocupacion_comercial', 'reparacion_vereda']),
            ('contradice', False, 'no_corresponde', ['reparacion_vereda']),
            ('contradice', True, 'no_corresponde', ['reparacion_vereda']),
            ('neutral', True, 'sin_evidencia', ['reparacion_vereda']),
        ):
            with self.subTest(respaldo=respaldo, ocupacion_vista=ocupacion_vista):
                def responder(req, *_):
                    contenido = {'confirmadas': [], 'dudosas': []}
                    if json.loads(req.data)['model'] == 't/decision':
                        contenido['confirmadas'] = [
                            {'key': 'reparacion_vereda', 'evidencia': 'Faltan baldosas.'},
                            {'key': 'retiro_muebles', 'evidencia': 'Silla descartada en otro sector.'}]
                        if ocupacion_vista:
                            ropa = 'Ropa exhibida.' if respaldo == 'compatible' else 'Ropa de otro comercio.'
                            contenido['confirmadas'].append({'key': 'ocupacion_comercial', 'evidencia': ropa})
                        contenido['pedidos'] = [{'key': 'ocupacion_comercial', 'respaldo': respaldo},
                                                {'key': 'reparacion_vereda', 'respaldo': 'compatible'}]
                    return {'usage': {'cost': .001}, 'choices': [
                        {'finish_reason': 'stop', 'message': {'content': json.dumps(contenido)}}]}
                with patch.object(self.S, 'CACHE_MAX', 0), \
                        patch.object(V, '_pedir_http', side_effect=responder):
                    r = self.cliente.post('/clasificar', files={'file': foto}, data=datos)
                    self.assertEqual(r.status_code, 200, r.text)
                    inicio = self.cliente.post('/trabajos', files={'file': foto}, data=datos)
                    self.assertEqual(inicio.status_code, 202, inicio.text)
                    ruta = '/trabajos/' + inicio.json()['trabajo']
                    limite = monotonic() + 3
                    trabajo = self.cliente.get(ruta).json()
                    while trabajo['estado'] in ('en_cola', 'procesando') and monotonic() < limite:
                        sleep(.01)
                        trabajo = self.cliente.get(ruta).json()
                    self.assertEqual(trabajo['estado'], 'listo', trabajo)
                for respuesta in (r.json(), trabajo['resultado']):
                    self.assertEqual(respuesta['reclamo_foto']['estado'], estado)
                    self.assertEqual(respuesta['solicitudes_sugeridas']['servicios'], esperados)
                    hallazgos = {p['key'] for p in respuesta['problemas']}
                    self.assertIn('retiro_muebles', hallazgos)
                    self.assertEqual('ocupacion_comercial' in hallazgos, ocupacion_vista)
                    pedidos = {p['key']: p['respaldo_visual'] for p in respuesta['categorias_contexto']}
                    self.assertEqual(pedidos['ocupacion_comercial'], respaldo)

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

    def test_cola_de_prueba_vincula_el_pedido_con_el_log_y_el_resultado(self):
        import contextlib
        import time

        salida = io.StringIO()
        with patch.object(V, 'OPENROUTER_LOG_USO', True), contextlib.redirect_stderr(salida):
            creacion = self.post('prueba', '/trabajos')
            self.assertEqual(creacion.status_code, 202, creacion.text)
            en_cola = creacion.json()
            consumo = en_cola['consumo_api']
            self.assertIsNone(consumo['intentos'])
            self.assertNotEqual(consumo['pedido'], en_cola['trabajo'])
            for _ in range(100):
                estado = self.cliente.get('/trabajos', params={'id': en_cola['trabajo']}).json()
                if estado['estado'] in {'listo', 'error'}:
                    break
                time.sleep(.01)
        self.assertEqual(estado['estado'], 'listo', estado)
        self.assertEqual(estado['resultado']['consumo_api'], dict(consumo, intentos=3))
        eventos = [json.loads(l) for l in salida.getvalue().splitlines() if l.startswith('{')]
        self.assertEqual(len(eventos), 3)
        self.assertEqual({e['pedido'] for e in eventos}, {consumo['pedido']})

    def test_trabajo_que_espera_la_cache_cierra_su_traza_sin_cargos(self):
        from contextlib import redirect_stderr
        from hashlib import sha256
        from time import monotonic, sleep
        from eval.vision.consumo import conciliar

        iniciado, continuar = threading.Event(), threading.Event()
        http = V._pedir_http.side_effect

        def esperar_segundo(*args):
            iniciado.set()
            if not continuar.wait(timeout=3):
                raise AssertionError('El segundo trabajo no entró en la cola')
            return dict(http(*args), id=sha256(args[0].data).hexdigest())

        salida = io.StringIO()
        with (patch.object(V, '_pedir_http', side_effect=esperar_segundo),
              patch.object(V, 'OPENROUTER_LOG_USO', True),
              patch.object(self.S, '_cupos', threading.Semaphore(1)), redirect_stderr(salida)):
            try:
                primero = self.post('prueba', '/trabajos')
                self.assertEqual(primero.status_code, 202)
                self.assertTrue(iniciado.wait(timeout=3))
                segundo = self.post('prueba', '/trabajos')
                self.assertEqual(segundo.status_code, 202)
            finally:
                continuar.set()
            respuestas = [primero.json(), segundo.json()]
            for inicio in respuestas[:]:
                vence = monotonic() + 3
                while monotonic() < vence:
                    final = self.cliente.get('/trabajos/' + inicio['trabajo']).json()
                    if final['estado'] == 'listo':
                        break
                    self.assertIn(final['estado'], ('en_cola', 'procesando'))
                    sleep(.01)
                self.assertEqual(final['estado'], 'listo')
                respuestas.append(final)

        origen = respuestas[2]['resultado']['consumo_api']['pedido']
        self.assertEqual(respuestas[3]['resultado']['consumo_api'], {
            'pedido': respuestas[1]['consumo_api']['pedido'], 'intentos': 0, 'origen_cache': origen})
        eventos = [json.loads(l) for l in salida.getvalue().splitlines() if l.startswith('{')]
        self.assertEqual(len(eventos), 3)
        resumen, _ = conciliar([{'id': 'foto', 'entrada': 'web', 'modo': 'prueba',
            'modo_version': self.ps['prueba'].version, 'esperado': {'retiro_muebles': True},
            'respuestas': respuestas}], eventos)
        self.assertEqual(resumen['casos_conciliados'], 1)
        self.assertEqual(resumen['casos_con_consumo_pendiente'], 0)
        self.assertEqual(resumen['maximo_conciliado_usd'], '0.003')

    def test_cola_conserva_perfil_capturado_y_error(self):
        inicio = threading.Event();seguir = threading.Event()
        async def espera(*args, **kwargs):
            inicio.set()
            while not seguir.is_set():
                await asyncio.sleep(.005)
            raise self.S.HTTPException(503, 'prueba de error en cola')
        with patch.object(self.S, '_esperar_cupo', side_effect=espera):
            r = self.post('prueba', '/trabajos')
            self.assertEqual(r.status_code, 202)
            d = r.json();tid = d['trabajo']
            self.assertTrue(inicio.wait(timeout=3))
            ps = dict(self.ps);ps['prueba'] = replace(ps['prueba'], version='nueva')
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
                self.assertEqual(final['modo'], 'prueba')
                self.assertEqual(final['modo_version'], d['modo_version'])
                self.assertEqual(final['consumo_api'], d['consumo_api'])
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
