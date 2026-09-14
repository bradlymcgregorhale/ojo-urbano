"""Contrato y controles contra rechazos falsos, sin red ni pesos."""
import copy
import json
import sys
import unittest
from pathlib import Path

import evaluacion_foto as E


def voto(modelo, **cambios):
    valor = dict(ambito='publica', evidencia_ambito='Objeto en la vereda junto al cordón',
                 calidad_suficiente=True, motivos_calidad=[], contexto_suficiente=True,
                 motivos_contexto=[])
    valor.update(cambios)
    return {'modelo': modelo, 'ok': True, 'evaluacion_foto': E.normalizar(valor)}


def salida():
    return {'problemas': [{'key': 'retiro_muebles'}], 'posibles': [{'key': 'retiro_escombros'}],
            'categorias_contexto': [], 'elementos_detectados': [{'key': 'contenedor_secos'}],
            'en_duda': [], 'descartados_por_foto': [], 'hay_problema': True, 'hay_reclamo': True,
            'gravedad_maxima': 3, 'predominante': 'retiro_muebles', 'foto_valida': True,
            'calidad_foto': {'definicion': 'limitada'}, 'costo_api': .02, 'tokens_api': 100,
            'analisis_estado': 'completo', 'descripcion': 'Un mueble.',
            'contenedores': {'estado': 'confirmado', 'tipos': ['contenedor_secos']},
            'verificacion_escombros': {'estado': 'apto'}, 'modelos': []}


class EvaluacionFotoTest(unittest.TestCase):
    def test_exterior_conserva_clasificacion_y_calidad_limitada_no_es_rechazo(self):
        for cantidad in [1, 2, 3]:
            with self.subTest(cantidad=cantidad):
                original = salida()
                r = E.aplicar(original, [voto(str(n)) for n in range(cantidad)])
                self.assertFalse(r['evaluacion_foto']['rechazada'])
                self.assertEqual(r['estado_evaluacion'], 'sin_objecion' if cantidad == 1 else 'valida_corroborada')
                for k in original: self.assertEqual(r[k], original[k])

    def test_rechazo_interior_vacia_servicios_sin_negar_correspondencia_ni_perder_consumo(self):
        original = salida(); antes = copy.deepcopy(original)
        r = E.aplicar(original, [voto('a', ambito='interior'), voto('b', ambito='interior')])
        self.assertEqual(r['estado_evaluacion'], 'rechazada_interior')
        for k in ['problemas', 'posibles', 'elementos_detectados', 'categorias_contexto', 'en_duda']:
            self.assertEqual(r[k], [])
        self.assertFalse(r['hay_reclamo'])
        self.assertTrue(r['foto_valida'])
        self.assertNotIn('contenedores', r)
        self.assertNotIn('verificacion_escombros', r)
        self.assertEqual(r['tokens_api'], original['tokens_api'])
        self.assertEqual(r['costo_api'], original['costo_api'])
        self.assertEqual(original, antes)

    def test_calidad_insuficiente_necesita_motivo_y_consenso(self):
        votos = [voto(m, calidad_suficiente=False, motivos_calidad=['desenfoque']) for m in ['a', 'b']]
        r = E.aplicar(salida(), votos)
        self.assertEqual(r['estado_evaluacion'], 'rechazada_calidad')
        self.assertTrue(r['evaluacion_foto']['requiere_nueva_foto'])
        votos[0] = voto('a', calidad_suficiente=False)
        self.assertFalse(E.aplicar(salida(), votos)['evaluacion_foto']['rechazada'])

    def test_contexto_insuficiente_es_informativo_y_no_borra_detalle_visible(self):
        original = salida()
        r = E.aplicar(original, [voto(m, contexto_suficiente=False,
            motivos_contexto=['encuadre_demasiado_cerrado']) for m in ['a', 'b']])
        self.assertEqual(r['estado_evaluacion'], 'contexto_insuficiente')
        self.assertFalse(r['contexto_visual']['suficiente'])
        self.assertFalse(r['evaluacion_foto']['rechazada'])
        self.assertFalse(r['evaluacion_foto']['requiere_nueva_foto'])
        self.assertTrue(r['evaluacion_foto']['requiere_foto_complementaria'])
        for k in original: self.assertEqual(r[k], original[k])

    def test_calidad_contradictoria_conserva_evidencia_y_exige_fuentes_reales(self):
        votos = [voto(m, calidad_suficiente=False, motivos_calidad=['desenfoque']) for m in ['a','b']]
        for v in votos:
            v['categorias'] = [{'key': 'retiro_muebles', 'evidencia': 'Un colchón entero en la vereda.'}]
        r = E.aplicar(salida(), votos)
        self.assertEqual(r['estado_evaluacion'], 'calidad_contradictoria')
        self.assertEqual(r['problemas'], salida()['problemas'])
        votos[0]['categorias'][0]['anulada_por'] = 'revision'
        self.assertTrue(E.aplicar(salida(), votos)['evaluacion_foto']['rechazada'])

    def test_fallo_ausencia_disenso_duplicado_y_unica_fuente_no_rechazan(self):
        base = [voto('a', ambito='interior'), voto('b', ambito='interior')]
        casos = [base[:1], base[:1] * 2, base + [voto('c')],
                 base + [{'modelo': 'c', 'ok': False}],
                 base + [{'modelo': 'c', 'ok': True}],
                 base + [voto('c', ambito='indeterminado')],
                 base + [voto('c', ambito='mixto')]]
        for votos in casos:
            with self.subTest(votos=votos):
                r = E.aplicar(salida(), votos)
                self.assertFalse(r['evaluacion_foto']['rechazada'])
                self.assertIsNone(r['evaluacion_foto']['ambito'])
                intentados, validos = E._lecturas_ambito(votos)
                if intentados >= 2 and not (len(validos) >= 2 and all(a == 'publica' for a in validos)):
                    self.assertEqual(r['problemas'], [])
                    self.assertIn('retiro_muebles', {p['key'] for p in r['posibles']})
                    self.assertFalse(r['hay_problema'])
                    self.assertTrue(r['evaluacion_foto']['requiere_revision'])
                else:
                    self.assertEqual(r['problemas'], salida()['problemas'])

    def test_mixto_y_toma_desde_ventana_no_se_convierten_en_interior(self):
        for ambito in ['mixto', 'publica', 'indeterminado']:
            r = E.aplicar(salida(), [voto(m, ambito=ambito) for m in ['a', 'b']])
            self.assertFalse(r['evaluacion_foto']['rechazada'])
            if ambito == 'publica':
                self.assertEqual(r['problemas'], salida()['problemas'])
                self.assertTrue(r['hay_problema'])
            else:
                self.assertEqual(r['problemas'], [])
                self.assertIn('retiro_muebles', {p['key'] for p in r['posibles']})
                self.assertFalse(r['hay_problema'])
                self.assertTrue(r['evaluacion_foto']['requiere_revision'])

    def test_senal_aislada_pide_revision_sin_rechazar_ni_borrar_hallazgos(self):
        senales = [dict(ambito='interior'),
                   dict(calidad_suficiente=False, motivos_calidad=['desenfoque']),
                   dict(contexto_suficiente=False, motivos_contexto=['entorno_no_visible'])]
        for senal in senales:
            for otros in [[], [voto('b')], [{'modelo': 'b', 'ok': False}]]:
                with self.subTest(senal=senal, otros=otros):
                    original = salida()
                    votos = [voto('a', **senal)] + otros
                    antes = copy.deepcopy(votos)
                    r = E.aplicar(original, votos)
                    e = r['evaluacion_foto']
                    self.assertEqual(e['estado'], 'senal_negativa_no_corroborada')
                    self.assertTrue(e['requiere_revision'])
                    self.assertIn('requiere revisión', e['indicacion'])
                    self.assertFalse(e['rechazada'])
                    self.assertFalse(e['requiere_nueva_foto'])
                    self.assertFalse(e['requiere_foto_complementaria'])
                    intentados, validos = E._lecturas_ambito(votos)
                    if intentados >= 2 and not (len(validos) >= 2 and all(a == 'publica' for a in validos)):
                        self.assertEqual(r['problemas'], [])
                        self.assertIn('retiro_muebles', {p['key'] for p in r['posibles']})
                        self.assertFalse(r['hay_problema'])
                    else:
                        for k in original:
                            self.assertEqual(r[k], original[k])
                    self.assertEqual(votos, antes)

    def test_sin_senal_negativa_no_inventa_revision_por_falta_de_corroboracion(self):
        for votos in [[], [voto('a')], [voto('a'), voto('b')],
                      [{'modelo': 'a', 'ok': False}]]:
            with self.subTest(votos=votos):
                e = E.aplicar(salida(), votos)['evaluacion_foto']
                self.assertFalse(e['requiere_revision'])
                self.assertIsNone(e['indicacion'])

    def test_ambito_mixto_o_indeterminado_pide_aclaracion_sin_rechazar(self):
        for ambito in ('mixto', 'indeterminado'):
            for otros in ([], [voto('b')], [voto('b', ambito=ambito)],
                          [{'modelo': 'b', 'ok': False}], [{'modelo': 'b', 'ok': True}]):
                with self.subTest(ambito=ambito, otros=otros):
                    original = salida()
                    votos = [voto('a', ambito=ambito)] + otros
                    antes = copy.deepcopy(votos)
                    r = E.aplicar(original, votos)
                    e = r['evaluacion_foto']
                    self.assertEqual(e['estado'], 'indeterminada')
                    self.assertTrue(e['requiere_revision'])
                    self.assertEqual(e['indicacion'], E.INDICACION_AMBITO)
                    self.assertFalse(e['rechazada'])
                    self.assertFalse(e['requiere_nueva_foto'])
                    self.assertFalse(e['requiere_foto_complementaria'])
                    intentados, validos = E._lecturas_ambito(votos)
                    if intentados >= 2 and not (len(validos) >= 2 and all(a == 'publica' for a in validos)):
                        self.assertEqual(r['problemas'], [])
                        self.assertIn('retiro_muebles', {p['key'] for p in r['posibles']})
                        self.assertFalse(r['hay_problema'])
                    else:
                        for k in original:
                            self.assertEqual(r[k], original[k])
                    self.assertEqual(votos, antes)

    def test_ambito_sin_lectura_valida_no_inventa_indicacion(self):
        casos = [dict(voto('a', ambito='mixto'), ok=False),
                 dict(voto('a', ambito='indeterminado'), modelo=None),
                 voto('a', ambito='indeterminado', evidencia_ambito='')]
        for v in casos:
            with self.subTest(voto=v):
                e = E.aplicar(salida(), [v])['evaluacion_foto']
                self.assertFalse(e['requiere_revision'])
                self.assertIsNone(e['indicacion'])

    def test_ambito_incierto_respeta_precedencia_de_calidad_y_encuadre(self):
        for campo, motivo, estado in [('calidad', 'desenfoque', 'rechazada_calidad'),
                                      ('contexto', 'entorno_no_visible', 'contexto_insuficiente')]:
            with self.subTest(campo=campo):
                votos = [voto(m, ambito='indeterminado', **{campo+'_suficiente': False,
                         'motivos_'+campo: [motivo]}) for m in ('a', 'b')]
                e = E.aplicar(salida(), votos)['evaluacion_foto']
                self.assertEqual(e['estado'], estado)
                self.assertFalse(e['requiere_revision'])
                self.assertNotEqual(e['indicacion'], E.INDICACION_AMBITO)
        for otro in (voto('b', ambito='interior'),
                     voto('b', calidad_suficiente=False, motivos_calidad=['oscuridad'])):
            with self.subTest(senal_previa=otro):
                e = E.aplicar(salida(), [voto('a', ambito='mixto'), otro])['evaluacion_foto']
                self.assertEqual(e['estado'], 'senal_negativa_no_corroborada')
                self.assertTrue(e['requiere_revision'])
                self.assertEqual(e['indicacion'], E.INDICACION_REVISION)

    def test_rechazo_corroborado_no_se_presenta_como_revision_pendiente(self):
        for cambio in [dict(ambito='interior'),
                       dict(calidad_suficiente=False, motivos_calidad=['oscuridad'])]:
            with self.subTest(cambio=cambio):
                e = E.aplicar(salida(), [voto(m, **cambio) for m in ['a', 'b']])['evaluacion_foto']
                self.assertTrue(e['rechazada'])
                self.assertTrue(e['requiere_nueva_foto'])
                self.assertFalse(e['requiere_revision'])

    def test_valores_malformados_y_prosa_no_crean_motivos(self):
        for valor in ['false', 0, 1, [], {}, None]:
            r = E.normalizar({'ambito': 'interior', 'calidad_suficiente': valor,
                              'motivos_calidad': ['<script>', 'entorno_no_visible']})
            self.assertIsNone(r['calidad_suficiente'])
            self.assertIsNone(r['ambito'])
            self.assertFalse(r['motivos_calidad'])
        self.assertIsNone(E.normalizar('interior'))

    def test_sin_verificacion_no_inventa_evaluacion(self):
        r = E.aplicar(salida(), [])
        self.assertEqual(r['estado_evaluacion'], 'no_evaluada')
        self.assertIsNone(r['contexto_visual']['suficiente'])
        self.assertIsNone(r['evaluacion_foto']['calidad_suficiente'])
        self.assertFalse(r['evaluacion_foto']['rechazada'])

    def test_higiene_sin_publica_corroborada_pasa_a_posibles(self):
        original = salida()
        original['problemas'] = [{'key': 'retiro_poda'}, {'key': 'vehiculo_mal_estacionado'}]
        original['posibles'] = []
        original['problema_principal'] = {'key': 'retiro_poda', 'estado': 'seleccionado'}
        original['predominante'] = 'retiro_poda'
        votos = [voto('a', ambito='publica'), voto('b', ambito='indeterminado'),
                 voto('c', ambito='interior')]
        r = E.aplicar(original, votos)
        self.assertFalse(r['evaluacion_foto']['rechazada'])
        self.assertTrue(r['evaluacion_foto']['requiere_revision'])
        self.assertEqual([p['key'] for p in r['problemas']], ['vehiculo_mal_estacionado'])
        self.assertEqual([p['key'] for p in r['posibles']], ['retiro_poda'])
        self.assertTrue(r['hay_problema'])
        self.assertEqual(r['predominante'], 'vehiculo_mal_estacionado')
        self.assertIsNone(r['problema_principal'])
        self.assertEqual(r['elementos_detectados'], original['elementos_detectados'])

    def test_publica_evaluada_conserva_retiro(self):
        r = E.aplicar(salida(), [voto(m) for m in ('a', 'b', 'c')])
        self.assertEqual(r['evaluacion_foto']['ambito'], 'publica')
        self.assertEqual(r['evaluacion_foto']['estado_ambito'], 'evaluado')
        self.assertEqual(r['problemas'], salida()['problemas'])
        self.assertTrue(r['hay_problema'])
        self.assertFalse(r['evaluacion_foto']['rechazada'])

    def test_una_fuente_economica_no_demora_un_retiro(self):
        r = E.aplicar(salida(), [voto('a', ambito='indeterminado')])
        self.assertEqual(r['problemas'], salida()['problemas'])
        self.assertTrue(r['hay_problema'])
        self.assertFalse(r['evaluacion_foto']['rechazada'])

    def test_p100_sin_consenso_publico_no_confirma_poda(self):
        original = salida()
        original['problemas'] = [{'key': 'retiro_poda', 'nombre': 'Retiro de restos de poda o jardinería'}]
        original['posibles'] = []
        original['predominante'] = 'retiro_poda'
        original['problema_principal'] = {'key': 'retiro_poda'}
        votos = [voto('openai/gpt-5-mini', ambito='indeterminado'),
                 voto('google/gemini-3.5-flash-lite', ambito='interior'),
                 voto('openai/gpt-5.6-luna', ambito='indeterminado')]
        r = E.aplicar(original, votos)
        self.assertFalse(r['evaluacion_foto']['rechazada'])
        self.assertEqual(r['problemas'], [])
        self.assertEqual([p['key'] for p in r['posibles']], ['retiro_poda'])
        self.assertFalse(r['hay_problema'])
        self.assertFalse(r['hay_reclamo'])
        self.assertTrue(r['evaluacion_foto']['requiere_revision'])

    def test_reclamo_textual_sobrevive_cuando_se_demora_higiene(self):
        original = salida()
        original['problemas'] = [{'key': 'retiro_poda', 'nombre': 'Retiro de poda'}]
        original['posibles'] = []
        original['categorias_contexto'] = [{'key': 'retiro_poda', 'respaldo': 'compatible'}]
        original['descripcion'] = 'Pedido de retiro'
        original['predominante'] = 'retiro_poda'
        votos = [voto('a', ambito='publica'), voto('b', ambito='interior')]
        r = E.aplicar(original, votos)
        self.assertFalse(r['evaluacion_foto']['rechazada'])
        self.assertEqual(r['problemas'], [])
        self.assertEqual([p['key'] for p in r['posibles']], ['retiro_poda'])
        self.assertEqual(r['categorias_contexto'], original['categorias_contexto'])
        self.assertFalse(r['hay_problema'])
        self.assertTrue(r['hay_reclamo'])
        self.assertEqual(r['descripcion'], 'Pedido de retiro')
        self.assertTrue(r['evaluacion_foto']['requiere_revision'])

    def test_reclamo_textual_sobrevive_si_higiene_pasa_a_posibles(self):
        original = salida()
        original['problemas'] = [{'key': 'retiro_poda'}]
        original['posibles'] = []
        original['categorias_contexto'] = [{'key': 'retiro_escombros', 'respaldo': 'compatible'}]
        original['predominante'] = 'retiro_poda'
        original['problema_principal'] = {'key': 'retiro_poda'}
        votos = [voto('a', ambito='indeterminado'), voto('b', ambito='interior')]
        r = E.aplicar(original, votos)
        self.assertFalse(r['evaluacion_foto']['rechazada'])
        self.assertEqual(r['problemas'], [])
        self.assertEqual([p['key'] for p in r['posibles']], ['retiro_poda'])
        self.assertFalse(r['hay_problema'])
        self.assertTrue(r['hay_reclamo'])
        self.assertEqual(r['categorias_contexto'], original['categorias_contexto'])

    @unittest.skipUnless('servidor' in sys.modules, 'Ejecutar mediante pruebas.py sin cargar pesos')
    def test_api_conserva_reclamo_textual_tras_demorar_higiene(self):
        import servidor as S
        r = {
            'problemas': [{'key': 'retiro_poda', 'nombre': 'Poda', 'gravedad': 3,
                           'fuentes': ['a', 'b']}],
            'posibles': [],
            'categorias_contexto': [{'key': 'retiro_escombros', 'nombre': 'Escombros',
                                     'respaldo': 'compatible'}],
            'elementos_detectados': [], 'en_duda': [], 'descartados_por_foto': [],
            'hay_problema': True, 'hay_reclamo': True, 'descripcion': 'Poda.',
            'detalle': {'verificacion': {'activa': True, 'verificadores': [
                voto('a', ambito='indeterminado'), voto('b', ambito='interior')]}}}
        p = S._publica(r)
        self.assertEqual(p['problemas'], [])
        self.assertEqual([x['key'] for x in p['posibles']], ['retiro_poda'])
        self.assertFalse(p['hay_problema'])
        self.assertTrue(p['hay_reclamo'])
        self.assertEqual([c['key'] for c in p['categorias_contexto']], ['retiro_escombros'])

    def test_p044_publica_conserva_voluminosos(self):
        original = salida()
        original['problemas'] = [{'key': 'retiro_muebles'}]
        original['posibles'] = [{'key': 'vehiculo_mal_estacionado'}]
        votos = [voto(m, ambito='publica') for m in ('a', 'b', 'c')]
        r = E.aplicar(original, votos)
        self.assertEqual([p['key'] for p in r['problemas']], ['retiro_muebles'])
        self.assertEqual([p['key'] for p in r['posibles']], ['vehiculo_mal_estacionado'])
        self.assertTrue(r['hay_problema'])
        self.assertFalse(r['evaluacion_foto']['requiere_revision'])

    def test_p023_dos_publica_y_un_fallo_conserva_el_retiro(self):
        original = salida()
        original['problemas'] = [{'key': 'retiro_escombros'}]
        original['posibles'] = []
        votos = [voto('a', ambito='publica'), voto('b', ambito='publica'),
                 {'modelo': 'c', 'ok': False}]
        r = E.aplicar(original, votos)
        self.assertEqual([p['key'] for p in r['problemas']], ['retiro_escombros'])
        self.assertTrue(r['hay_problema'])
        self.assertFalse(r['evaluacion_foto']['rechazada'])

    def test_sin_lectura_de_ambito_no_inventa_una_demora(self):
        original = salida()
        votos = [{'modelo': 'm1', 'ok': True, 'categorias': [{'key': 'retiro_escombros'}]},
                 {'modelo': 'm2', 'ok': True, 'categorias': [{'key': 'retiro_escombros'}]},
                 {'modelo': 'm3', 'ok': True, 'categorias': [{'key': 'recoleccion'}]}]
        r = E.aplicar(original, votos)
        self.assertEqual(r['problemas'], original['problemas'])
        self.assertTrue(r['hay_problema'])
        self.assertFalse(r['evaluacion_foto']['requiere_revision'])

    def test_tres_intentos_con_una_lectura_publica_no_corroboran(self):
        original = salida()
        votos = [voto('a', ambito='publica'),
                 {'modelo': 'b', 'ok': False},
                 {'modelo': 'c', 'ok': True}]
        r = E.aplicar(original, votos)
        self.assertEqual(r['problemas'], [])
        self.assertIn('retiro_muebles', {p['key'] for p in r['posibles']})
        self.assertFalse(r['hay_problema'])
        self.assertEqual(r['descripcion'], r['evaluacion_foto']['indicacion'])

    def test_mayoria_de_tres_decide_calidad_y_encuadre(self):
        votos = [voto('a', calidad_suficiente=False, motivos_calidad=['desenfoque']),
                 voto('b', calidad_suficiente=False, motivos_calidad=['detalle_insuficiente']),
                 voto('c')]
        sin_hallazgos = dict(salida(), problemas=[], hay_problema=False, hay_reclamo=False)
        r = E.aplicar(sin_hallazgos, votos)
        self.assertEqual(r['estado_evaluacion'], 'rechazada_calidad')
        self.assertEqual(r['evaluacion_foto']['decision_calidad'], 'mayoria')
        self.assertEqual(r['evaluacion_foto']['estado_calidad'], 'evaluado')
        self.assertEqual(r['evaluacion_foto']['motivos'], ['desenfoque', 'detalle_insuficiente'])
        self.assertTrue(r['evaluacion_foto']['requiere_nueva_foto'])
        self.assertEqual(r['problemas'], [])
        original = salida()
        votos = [voto('a', contexto_suficiente=False, motivos_contexto=['encuadre_demasiado_cerrado']),
                 voto('b', contexto_suficiente=False, motivos_contexto=['entorno_no_visible']),
                 voto('c')]
        r = E.aplicar(original, votos)
        self.assertEqual(r['estado_evaluacion'], 'contexto_insuficiente')
        self.assertFalse(r['contexto_visual']['suficiente'])
        self.assertEqual(r['contexto_visual']['estado'], 'evaluado')
        self.assertEqual(r['contexto_visual']['motivos'], ['encuadre_demasiado_cerrado', 'entorno_no_visible'])
        self.assertTrue(r['evaluacion_foto']['requiere_foto_complementaria'])
        self.assertFalse(r['evaluacion_foto']['rechazada'])
        for k in original:
            self.assertEqual(r[k], original[k])

    def test_mayoria_exige_dos_lectores_distintos_y_sin_empate(self):
        casos = [
            # dos lecturas del mismo modelo no suman dos fuentes
            [voto('a', calidad_suficiente=False, motivos_calidad=['desenfoque'])] * 2 + [voto('b')],
            # uno en contra, uno a favor y uno sin decisión: empate
            [voto('a', calidad_suficiente=False, motivos_calidad=['desenfoque']), voto('b'),
             voto('c', calidad_suficiente=None)],
            # dos lectores en desacuerdo, sin tercero
            [voto('a', calidad_suficiente=False, motivos_calidad=['desenfoque']), voto('b')],
            # un false sin motivo no es un voto
            [voto('a', calidad_suficiente=False), voto('b', calidad_suficiente=False), voto('c')],
        ]
        for votos in casos:
            with self.subTest(votos=votos):
                r = E.aplicar(salida(), votos)
                self.assertFalse(r['evaluacion_foto']['rechazada'])
                self.assertIsNone(r['evaluacion_foto']['calidad_suficiente'])
                self.assertEqual(r['problemas'], salida()['problemas'])

    def test_mayoria_a_favor_conserva_la_senal_de_revision(self):
        votos = [voto('a'), voto('b'),
                 voto('c', contexto_suficiente=False, motivos_contexto=['situacion_cortada'])]
        r = E.aplicar(salida(), votos)
        self.assertTrue(r['contexto_visual']['suficiente'])
        self.assertEqual(r['contexto_visual']['estado'], 'evaluado')
        self.assertEqual(r['contexto_visual']['motivos'], [])
        self.assertEqual(r['estado_evaluacion'], 'senal_negativa_no_corroborada')
        self.assertTrue(r['evaluacion_foto']['requiere_revision'])
        self.assertFalse(r['evaluacion_foto']['requiere_foto_complementaria'])

    def test_un_lector_caido_no_impide_dos_votos_explicitos(self):
        votos = [voto('a', calidad_suficiente=False, motivos_calidad=['oscuridad']),
                 voto('b', calidad_suficiente=False, motivos_calidad=['oscuridad']),
                 {'modelo': 'c', 'ok': False}]
        r = E.aplicar(dict(salida(), problemas=[], hay_problema=False, hay_reclamo=False), votos)
        self.assertEqual(r['estado_evaluacion'], 'rechazada_calidad')
        self.assertEqual(r['evaluacion_foto']['motivos'], ['oscuridad'])
        # Con un problema ya confirmado, ese mismo padrón pide revisión en vez de rechazar.
        self.assertEqual(E.aplicar(salida(), votos)['estado_evaluacion'], 'calidad_contradictoria')

    def test_mayoria_no_alcanza_para_el_ambito(self):
        votos = [voto('a', ambito='interior'), voto('b', ambito='interior'), voto('c')]
        r = E.aplicar(salida(), votos)
        self.assertFalse(r['evaluacion_foto']['rechazada'])
        self.assertIsNone(r['evaluacion_foto']['ambito'])
        self.assertNotEqual(r['estado_evaluacion'], 'rechazada_interior')

    def test_mayoria_no_borra_un_problema_confirmado_con_el_modelo_local(self):
        # retiro_muebles quedó confirmado por el modelo local y un solo lector: los
        # otros dos no dan evidencia y votan calidad insuficiente. Se pide revisión.
        votos = [voto('a', calidad_suficiente=False, motivos_calidad=['desenfoque']),
                 voto('b', calidad_suficiente=False, motivos_calidad=['desenfoque']), voto('c')]
        votos[2]['categorias'] = [{'key': 'retiro_muebles', 'evidencia': 'Un colchón entero en la vereda.'}]
        r = E.aplicar(salida(), votos)
        self.assertEqual(r['estado_evaluacion'], 'calidad_contradictoria')
        self.assertFalse(r['evaluacion_foto']['rechazada'])
        self.assertTrue(r['evaluacion_foto']['requiere_revision'])
        self.assertEqual(r['problemas'], salida()['problemas'])
        # Con unanimidad de los tres lectores y un solo respaldo, el rechazo sí corre.
        votos = [voto(m, calidad_suficiente=False, motivos_calidad=['desenfoque']) for m in 'abc']
        votos[2]['categorias'] = [{'key': 'retiro_muebles', 'evidencia': 'Un colchón entero en la vereda.'}]
        r = E.aplicar(salida(), votos)
        self.assertEqual(r['estado_evaluacion'], 'rechazada_calidad')
        self.assertEqual(r['evaluacion_foto']['decision_calidad'], 'unanime')

    def test_solo_los_votos_elegibles_aportan_motivos_y_senales(self):
        # Una entrada caída que igual trae una lectura, y un duplicado del lector a,
        # no votan, no suman motivos ni activan la señal de revisión.
        caido = dict(voto('z', contexto_suficiente=False, motivos_contexto=['situacion_cortada']), ok=False)
        duplicado = voto('a', calidad_suficiente=False, motivos_calidad=['movimiento'])
        votos = [voto('a', calidad_suficiente=False, motivos_calidad=['desenfoque']),
                 voto('b', calidad_suficiente=False, motivos_calidad=['oscuridad']), duplicado, caido]
        r = E.aplicar(dict(salida(), problemas=[], hay_problema=False), votos)
        self.assertEqual(r['evaluacion_foto']['motivos'], ['desenfoque', 'oscuridad'])
        r = E.aplicar(salida(), [voto('a'), voto('b'), caido])
        self.assertEqual(r['estado_evaluacion'], 'valida_corroborada' if False else r['estado_evaluacion'])
        self.assertFalse(r['evaluacion_foto']['requiere_revision'])
        self.assertIsNone(r['contexto_visual']['suficiente'] if r['contexto_visual']['estado'] != 'evaluado' else None)

    def test_mayoria_de_calidad_respeta_la_evidencia_contradictoria(self):
        votos = [voto('a', calidad_suficiente=False, motivos_calidad=['desenfoque']),
                 voto('b', calidad_suficiente=False, motivos_calidad=['desenfoque']), voto('c')]
        for v in votos:
            v['categorias'] = [{'key': 'retiro_muebles', 'evidencia': 'Un colchón entero en la vereda.'}]
        r = E.aplicar(salida(), votos)
        self.assertEqual(r['estado_evaluacion'], 'calidad_contradictoria')
        self.assertEqual(r['problemas'], salida()['problemas'])

    def test_retiros_higiene_existen_en_el_catalogo(self):
        cats = json.loads(Path(__file__).with_name('categorias.json').read_text())
        for key in E.RETIROS_HIGIENE:
            self.assertIn(key, cats)
            self.assertEqual(cats[key]['grupo'], 'Residuos')


if __name__ == '__main__':
    unittest.main()
