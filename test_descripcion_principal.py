"""Coherencia de descripción sin nuevas inferencias ni reclasificación."""
import copy
import sys
import unittest
import prioridad as P


class DescripcionPrincipalTest(unittest.TestCase):
    def caso(self):
        pub = {'problemas': [{'key': 'situacion_calle', 'nombre': 'Personas en situación de calle', 'gravedad': 3},
                            {'key': 'recoleccion', 'nombre': 'Recolección', 'gravedad': 2}],
               'descripcion': 'Se observa un bulto textil y cartones.',
               'problema_principal': {'key': 'situacion_calle', 'estado': 'seleccionado'},
               'predominante': 'situacion_calle', 'costo_api': .04}
        lectores = [
            {'modelo': 'a', 'ok': True, 'descripcion': 'Persona acostada sobre cartones.',
             'categorias': [{'key': 'situacion_calle', 'evidencia': 'persona acostada sobre cartones'}]},
            {'modelo': 'b', 'ok': True, 'descripcion': 'Persona con sus pertenencias.',
             'categorias': [{'key': 'situacion_calle', 'evidencia': 'persona descansando con sus pertenencias'}]},
            {'modelo': 'c', 'ok': True, 'descripcion': pub['descripcion'],
             'categorias': [{'key': 'recoleccion', 'evidencia': 'bulto textil'}]}]
        return pub, lectores

    def test_lectura_secundaria_no_oculta_principal(self):
        pub, lectores = self.caso()
        antes = copy.deepcopy(pub)
        P.ajustar_descripcion(pub, lectores)
        self.assertIn('Personas en situación de calle', pub['descripcion'])
        self.assertIn('persona acostada sobre cartones', pub['descripcion'])
        self.assertIn('Recolección', pub['descripcion'])
        self.assertNotIn('bulto textil', pub['descripcion'])
        self.assertEqual(pub['detalle_descripcion']['descripcion_anterior'], antes['descripcion'])
        for k in antes:
            if k != 'descripcion':
                self.assertEqual(pub[k], antes[k])
        repetido = copy.deepcopy(pub)
        P.ajustar_descripcion(pub, list(reversed(lectores)))
        self.assertEqual(pub, repetido)

    def test_descripcion_saneada_conserva_procedencia_por_prefijo(self):
        pub, lectores = self.caso()
        pub['descripcion'] = 'Se observa un bulto textil sobre la vereda junto al árbol.'
        lectores[-1]['descripcion'] = pub['descripcion'] + ' No se distingue el material.'
        P.ajustar_descripcion(pub, lectores)
        self.assertIn('Personas en situación de calle', pub['descripcion'])

    def test_aciertos_ausencias_y_rechazos_se_conservan(self):
        for variante in ('acierto', 'arbitro', 'sin_prioridad', 'indeterminado', 'no_confirmado', 'rechazada', 'contexto', 'sin_evidencia'):
            with self.subTest(variante=variante):
                pub, lectores = self.caso()
                if variante == 'acierto': pub['descripcion'] = lectores[0]['descripcion']
                if variante == 'arbitro': pub['descripcion'] = 'Descripción consolidada por el árbitro.'
                if variante == 'sin_prioridad': pub.pop('problema_principal')
                if variante == 'indeterminado': pub['problema_principal']['estado'] = 'indeterminado'
                if variante == 'no_confirmado': pub['problema_principal']['key'] = 'retiro_poda'
                if variante == 'rechazada': pub['evaluacion_foto'] = {'rechazada': True}
                if variante == 'contexto': pub['contexto_visual'] = {'suficiente': False}
                if variante == 'sin_evidencia':
                    for v in lectores:
                        for c in v['categorias']: c.pop('evidencia')
                antes = copy.deepcopy(pub)
                P.ajustar_descripcion(pub, lectores)
                self.assertEqual(pub, antes)

    def test_auditoria_respeta_descarte_sin_confirmar_en_prosa_del_arbitro(self):
        pub, lectores = self.caso()
        pub['problemas'] = pub['problemas'][:1]
        pub['descripcion'] = 'También hay un colchón enrollado abandonado. Personas en situación de calle.'
        audit = [{'key': 'retiro_muebles', 'descarte_independiente': True, 'confirmo': False}]
        antes = copy.deepcopy(pub)
        P.ajustar_descripcion(pub, lectores, audit)
        self.assertNotIn('colchón', pub['descripcion'])
        self.assertIn('persona acostada sobre cartones', pub['descripcion'])
        self.assertEqual(pub['detalle_descripcion']['descripcion_anterior'], antes['descripcion'])
        self.assertEqual(pub['problemas'], antes['problemas'])
        repetido = copy.deepcopy(pub)
        P.ajustar_descripcion(pub, lectores, audit)
        self.assertEqual(pub, repetido)

    def test_auditoria_no_inventa_prioridad_indeterminada(self):
        pub, lectores = self.caso()
        pub['problema_principal'] = {'key': None, 'estado': 'indeterminado'}
        audit = [{'key': 'retiro_muebles', 'descarte_independiente': True, 'confirmo': False}]
        P.ajustar_descripcion(pub, lectores, audit)
        self.assertTrue(pub['descripcion'].startswith('Hallazgo confirmado:'))
        self.assertEqual(pub['problema_principal']['estado'], 'indeterminado')
        self.assertNotIn('bulto textil', pub['descripcion'])

    def test_auditoria_favorable_no_recorta_descripcion(self):
        pub, lectores = self.caso()
        pub['descripcion'] = 'Persona descansando junto a un sillón descartado aparte.'
        antes = copy.deepcopy(pub)
        P.ajustar_descripcion(pub, lectores, [{'key': 'retiro_muebles',
            'descarte_independiente': True, 'confirmo': True}])
        self.assertEqual(pub, antes)

    @unittest.skipUnless("servidor" in sys.modules, "Publicación se prueba en pruebas.py sin cargar pesos")
    def test_publicacion_explica_retiro_posible_y_conserva_lecturas(self):
        import servidor as S
        pub, lectores = self.caso()
        persona = dict(pub['problemas'][0], fuentes=['a', 'b'])
        audit = [{'key': 'retiro_muebles', 'descarte_independiente': True, 'confirmo': False}]
        r = {'problemas': [persona], 'posibles': [{'key': 'retiro_muebles',
            'nombre': 'Retiro de residuos voluminosos', 'fuentes': ['a', 'c'],
            'motivo': 'Dos lectores ven un colchón abandonado.', 'arbitro': 'confirmar'}],
            'descripcion': 'También hay un colchón enrollado abandonado.',
            'detalle': {'verificacion': {'activa': True, 'verificadores': lectores,
                                        'repreguntas': audit}}}
        antes = copy.deepcopy(r)
        final = S._publica(r)
        self.assertNotIn('colchón', final['descripcion'])
        self.assertIn('persona', final['descripcion'])
        self.assertIn('No se corroboró', final['posibles'][0]['motivo'])
        self.assertEqual(final['posibles'][0]['verificacion_descarte_independiente'], 'sin_corroborar')
        self.assertEqual(r, antes)

    def test_objeto_descartado_no_es_motivo_para_personas(self):
        pub, lectores = self.caso()
        for v in lectores:
            v['prioridad_propuesta'] = {'key': 'situacion_calle', 'criterio': 'escena', 'fundamento': 'objeto_principal'}
        self.assertNotIn('descartado', P.seleccionar(pub, lectores)['motivo'])
        lectores[-1]['prioridad_propuesta']['key'] = 'recoleccion'
        r = P.seleccionar(pub, lectores, comparacion=lambda _: {'key': 'situacion_calle', 'fundamento': 'objeto_principal'})
        self.assertEqual(r['key'], 'situacion_calle')
        self.assertNotIn('descartado', r['motivo'])


if __name__ == '__main__':
    unittest.main()
