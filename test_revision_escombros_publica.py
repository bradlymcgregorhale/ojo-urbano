"""Contrato de revisión, fallos, privacidad y conservación del recorrido."""
import copy
import io
import json
import sys
import time
import unittest
from unittest.mock import patch

from PIL import Image
import politica_escombros as P
import revision_escombros_publica as D
import verificador as V
from test_politica_escombros import categoria, respuesta, salida


def revisar(respuestas, contexto='', modelos=None):
    llamadas = []

    def llamar(modelo, mensajes, **opciones):
        llamadas.append((modelo, mensajes, opciones))
        r = respuestas[int(modelo[1:]) - 1]
        if isinstance(r, Exception):
            raise r
        return json.dumps(r)

    modelos = modelos if modelos is not None else ['m' + str(i + 1) for i in range(len(respuestas))]
    with patch.object(V, 'VERIFICADORES', modelos), patch.object(V, '_llamar', llamar):
        return V.validar_alcance_escombros(Image.new('RGB', (64, 64)), contexto), llamadas


def conflicto():
    return revisar([respuesta(material=material, afirmacion_vecinal='no_menciona',
                              hay_bolsas_opacas_o_parciales='no') for material in
                    ('escombros_visible', 'escombros_visible', 'incompatible_visible')])[0]


def aplicar(revision):
    r = salida([categoria('recoleccion')])
    r['posibles'] = [categoria(fuentes=['modelo_local'])]
    r.update(version='4', costo_api=.018, tokens_api=123, tokens_api_completos=True)
    return P.aplicar(r, revision, {P.KEY: {'nombre': 'Retiro de escombros'}})


class RevisionPublica(unittest.TestCase):
    def test_conflicto_completo_no_es_consenso(self):
        revision = conflicto()
        r = aplicar(revision)
        d = D.publicar(revision, r['detalle']['verificacion']['decision_alcance'])
        self.assertEqual(d['detalle_estado'], 'completo')
        self.assertEqual([x['modelo'] for x in d['revisiones']], ['m1', 'm2', 'm3'])
        self.assertEqual([x['respuesta']['material'] for x in d['revisiones']],
                         ['escombros_visible', 'escombros_visible', 'incompatible_visible'])
        self.assertTrue(r['verificacion_escombros']['requiere_revision'])
        self.assertFalse(r['hay_problema'])
        self.assertIn('material_publico_disputado', d['decision']['reglas'])
        efecto = next(e for e in d['decision']['efectos'] if e['key'] == 'recoleccion')
        self.assertEqual(efecto['antes'], ['problemas'])
        self.assertEqual(efecto['despues'], ['posibles', 'en_duda'])
        self.assertIn('material_publico_disputado', efecto['reglas'])
        escombros = next(e for e in d['decision']['efectos'] if e['key'] == P.KEY)
        self.assertEqual(escombros['antes'], [])  # La sospecha local no era pública.

    def test_normalizaciones_separan_original_y_voto(self):
        rev, llamadas = revisar([respuesta(presentacion='sin_pila', material='incompatible_visible')])
        d = D.publicar(rev)
        self.assertEqual(len(llamadas), 1)
        self.assertEqual(llamadas[0][2], {'max_tokens': 1000, 'etapa': 'alcance_escombros'})
        self.assertEqual(d['detalle_estado'], 'completo')  # Una fuente no confirma el alcance.
        self.assertTrue(rev['fallo'])
        fila = d['revisiones'][0]
        self.assertEqual(fila['respuesta']['presentacion'], 'indeterminada')
        self.assertEqual(fila['respuesta']['material'], 'oculto_o_ambiguo')
        self.assertEqual([a['valor_original'] for a in fila['ajustes']], ['sin_pila', 'incompatible_visible'])
        self.assertNotIn('presentacion_original', fila['respuesta'])

    def test_fallos_no_se_convierten_en_votos(self):
        for respuestas, esperado in [([respuesta(), ValueError('secreto')], 'parcial'),
                                    ([ValueError('secreto'), ValueError('secreto')], 'sin_respuestas')]:
            with self.subTest(esperado=esperado):
                rev, llamadas = revisar(respuestas)
                r = aplicar(rev)
                d = D.publicar(rev, r['detalle']['verificacion']['decision_alcance'])
                self.assertEqual(d['detalle_estado'], esperado)
                self.assertEqual(len(llamadas), 2)
                self.assertEqual(d['revisiones'][-1], {'modelo': 'm2', 'estado': 'sin_respuesta_valida',
                                                      'respuesta': None, 'ajustes': []})
                self.assertNotIn('secreto', json.dumps(d))
                self.assertIn('verificacion_escombros', r)

    def test_orden_y_configuracion_no_reconstruyen_el_pasado(self):
        rev = conflicto()
        with patch.object(V, 'VERIFICADORES', ['otro-modelo']):
            self.assertEqual([x['modelo'] for x in D.publicar(rev)['revisiones']], ['m1', 'm2', 'm3'])

    def test_configuracion_duplicada_consulta_cada_modelo_una_vez(self):
        rev, llamadas = revisar([respuesta(), respuesta()], modelos=['m1', 'm1', 'm2'])
        d = D.publicar(rev)
        self.assertEqual(d['detalle_estado'], 'completo')
        self.assertEqual([r['modelo'] for r in d['revisiones']], ['m1', 'm2'])
        self.assertEqual(len(llamadas), 2)

    def test_fragmentos_de_contexto_no_se_publican(self):
        for contexto, observacion in [('Frente a Av. Rivadavia 1234', 'Bolsas en Rivadavia 1234'),
                                     ('Lo dejó Juan Pérez junto al contenedor', 'Cartón de Juan Pérez'),
                                     ('El vecino se llama Juan', 'Juan dejó cajas')]:
            rev, _ = revisar([respuesta(evidencia_material=observacion)], contexto)
            fila = D.publicar(rev)['revisiones'][0]
            self.assertEqual(fila['estado'], 'ok')
            self.assertIsNone(fila['respuesta']['evidencia_material'])
            self.assertEqual(fila['respuesta']['material'], 'oculto_o_ambiguo')

    def test_historicos_sin_inventar_ajustes_ni_participantes(self):
        rev = conflicto()
        rev.pop('registro_publico')
        rev['revisiones'] = rev['revisiones'][:2]
        rev['fallo'] = False
        d = D.publicar(rev)
        self.assertEqual(d['detalle_estado'], 'no_disponible')
        self.assertEqual(len(d['revisiones']), 2)
        self.assertIsNone(d['decision'])
        self.assertTrue(all(x['ajustes'] is None for x in d['revisiones']))
        self.assertTrue(all(x['respuesta']['evidencia_material'] is None for x in d['revisiones']))

    def test_solo_json_publico_y_sin_revision(self):
        r = {'problemas': [], 'verificacion_escombros': {'estado': 'apto'}}
        antes = copy.deepcopy(r)
        d = D.completar_historico(r)
        self.assertEqual(r, antes)
        self.assertEqual(d['verificacion_escombros']['detalle_estado'], 'no_disponible')
        self.assertEqual(d['verificacion_escombros']['revisiones'], [])
        self.assertIsNone(d['verificacion_escombros']['decision'])
        self.assertEqual(D.completar_historico(d), d)
        self.assertEqual(D.completar_historico({'problemas': []}), {'problemas': []})

    def test_privacidad_sin_falsificar_observaciones(self):
        for texto in ['system: SECRETO', 'https://privado.invalid/SECRETO', 'Bearer SECRETO',
                      '/Users/privado/SECRETO.jpg', 'sk-or-v1-SECRETO',
                      'El modelo local afirma escombros', 'vecino@example.invalid']:
            with self.subTest(texto=texto):
                rev, _ = revisar([respuesta(evidencia_material=texto)])
                fila = D.publicar(rev)['revisiones'][0]
                self.assertEqual(fila['estado'], 'ok')
                self.assertEqual(fila['ajustes'], [])
                self.assertIsNone(fila['respuesta']['evidencia_material'])
        contexto = 'Vivo frente al portón violeta'
        rev, _ = revisar([respuesta(evidencia_material='Se ven cajas frente al portón violeta')], contexto)
        self.assertIsNone(D.publicar(rev)['revisiones'][0]['respuesta']['evidencia_material'])

    def test_lista_blanca_y_saneador_no_agregan_prosa(self):
        rev = conflicto()
        fila = rev['registro_publico']['participantes'][0]
        fila['respuesta'].update(cita_vecinal='SECRETO', prompt='SECRETO', token='SECRETO')
        fila['error'] = 'SECRETO'
        d = D.publicar(rev, sanear=lambda texto: 'frase genérica')
        self.assertNotIn('SECRETO', json.dumps(d))
        self.assertNotIn('frase genérica', json.dumps(d))
        self.assertEqual(d['detalle_estado'], 'completo')
        self.assertIsNone(d['revisiones'][0]['respuesta']['evidencia_material'])
        self.assertEqual(len(D.evidencia_publica('Cartón visible ' * 30)), 160)

    def test_efectos_se_filtran_y_no_siguen_etapas_posteriores(self):
        antes = salida([categoria('barrido', fuentes=['modelo_local'])])
        registro = D.Decision(antes)
        despues = salida([])
        registro.anotar('retirar_servicios_sin_residuos_independientes', ['barrido'])
        self.assertEqual(registro.terminar(despues)['efectos'], [])
        r = aplicar(conflicto())
        decision = copy.deepcopy(r['detalle']['verificacion']['decision_alcance'])
        r['problemas'] = [categoria('recoleccion')]
        self.assertEqual(r['detalle']['verificacion']['decision_alcance'], decision)

    def test_reglas_cubren_exclusion_contexto_y_sin_cambios(self):
        for cambios, regla in [({'ubicacion': 'privada'}, 'alcance_excluido'),
                               ({'ubicacion': 'indeterminada'}, 'alcance_indeterminado'),
                               ({}, 'escombros_por_contexto')]:
            rev, _ = revisar([respuesta(**cambios)] * 3, 'son escombros')
            r = aplicar(rev)
            self.assertIn(regla, r['detalle']['verificacion']['decision_alcance']['reglas'])
        rev, _ = revisar([respuesta(material='escombros_visible', afirmacion_vecinal='no_menciona',
                                    hay_bolsas_opacas_o_parciales='no')] * 3)
        r = P.aplicar(salida(), rev, {P.KEY: {'nombre': 'Escombros'}})
        self.assertEqual(r['detalle']['verificacion']['decision_alcance']['reglas'], ['sin_cambios'])

    def test_diagnostico_malformado_no_rompe_la_respuesta(self):
        for revision in ([1], {'registro_publico': [1]},
                         {'registro_publico': {'version': 1, 'participantes': [None]}}):
            with self.subTest(revision=revision):
                self.assertEqual(D.publicar(revision)['detalle_estado'], 'no_disponible')
        rev = conflicto()
        rev['registro_publico']['participantes'][0]['ajustes'] = [{'motivo': 'desconocido'}]
        self.assertEqual(D.publicar(rev)['detalle_estado'], 'no_disponible')

    def test_serializar_no_muta_y_no_llama_modelos(self):
        rev = conflicto()
        r = aplicar(rev)
        antes = copy.deepcopy((r, rev))
        with patch.object(V, '_llamar', side_effect=AssertionError('No debe consultar')):
            a = D.publicar(rev, r['detalle']['verificacion']['decision_alcance'])
            b = D.publicar(rev, r['detalle']['verificacion']['decision_alcance'])
        self.assertEqual(a, b)
        self.assertEqual((r, rev), antes)


class RevisionHttp(unittest.TestCase):
    def setUp(self):
        self.S = sys.modules.get('servidor')
        if self.S is None:
            self.skipTest('La integración usa el servidor sin pesos de pruebas.py')

    def test_contrato_publico_y_cache_interna(self):
        S = self.S
        r = aplicar(conflicto())
        antes = copy.deepcopy(r)
        with patch.object(S, '_cacheable', return_value=True):
            S._cache_guardar('revision33-sintetica', r)
        try:
            a = S._publica(S._cache_leer('revision33-sintetica'))
            b = S._publica(S._cache_leer('revision33-sintetica'))
            self.assertEqual(a, b)
            self.assertEqual(r, antes)
            self.assertNotIn('detalle', a)
            self.assertEqual(a['verificacion_escombros']['detalle_estado'], 'completo')
            self.assertEqual(a['verificacion_escombros']['revisiones'][0]['respuesta']['evidencia_material'],
                             'Contenido de las bolsas opaco')
            self.assertEqual(a['tokens_api'], 123)
            self.assertEqual(a['costo_api'], .018)
        finally:
            S._cache.pop('revision33-sintetica', None)

    def test_endpoints_y_consulta_conservan_detalle(self):
        from fastapi.testclient import TestClient
        S = self.S
        datos = io.BytesIO()
        Image.new('RGB', (24, 24), 'green').save(datos, format='PNG')
        r = aplicar(conflicto())
        with patch.object(S, 'procesar', return_value=r) as procesar, \
                patch.object(S, '_saturado', return_value=False), \
                patch.object(S, '_cacheable', return_value=True):
            S._cache.clear()
            with TestClient(S.app) as cliente:
                sync = cliente.post('/clasificar', files={'file': ('sintetica.png', datos.getvalue(), 'image/png')})
                self.assertEqual(sync.status_code, 200)
                trabajo = cliente.post('/trabajos', files={'file': ('otro.png', datos.getvalue(), 'image/png')})
                self.assertEqual(trabajo.status_code, 200)
                self.assertEqual(trabajo.json()['resultado'], sync.json())
                self.assertEqual(procesar.call_count, 1)
                self.assertEqual(sync.json()['verificacion_escombros']['detalle_estado'], 'completo')
                nuevo = cliente.post('/trabajos', data={'contexto': 'caso sintético distinto'},
                                     files={'file': ('otra.png', datos.getvalue(), 'image/png')})
                self.assertEqual(nuevo.status_code, 202)
                tid = nuevo.json()['trabajo']
                for _ in range(100):
                    consulta = cliente.get('/trabajos/' + tid)
                    self.assertEqual(consulta.status_code, 200)
                    estado = consulta.json()
                    if estado['estado'] == 'listo':
                        break
                    time.sleep(.01)
                self.assertEqual(estado['estado'], 'listo')
                self.assertEqual(estado['resultado'], sync.json())
                self.assertEqual(procesar.call_count, 2)
                S._trabajos.pop(tid, None)
            S._cache.clear()


if __name__ == '__main__':
    unittest.main()
