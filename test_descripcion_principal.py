"""Coherencia de descripción sin nuevas inferencias ni reclasificación."""
import copy
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
