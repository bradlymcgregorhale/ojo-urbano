"""Contrato y controles contra rechazos falsos, sin red ni pesos."""
import copy
import unittest

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
                self.assertEqual(r['problemas'], salida()['problemas'])

    def test_mixto_y_toma_desde_ventana_no_se_convierten_en_interior(self):
        for ambito in ['mixto', 'publica', 'indeterminado']:
            r = E.aplicar(salida(), [voto(m, ambito=ambito) for m in ['a', 'b']])
            self.assertFalse(r['evaluacion_foto']['rechazada'])

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


if __name__ == '__main__':
    unittest.main()
