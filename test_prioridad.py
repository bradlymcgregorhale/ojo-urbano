"""Prioridad separada de detección y del orden de las categorías."""
import copy
import unittest
import prioridad as P


class PrioridadTest(unittest.TestCase):
    def test_cero_uno_y_rechazo(self):
        self.assertEqual(P.seleccionar({'problemas': []}, [])['estado'], 'sin_problemas_confirmados')
        r = {'problemas': [{'key': 'retiro_poda'}]}
        self.assertEqual(P.seleccionar(r, [])['criterio'], 'unico_confirmado')
        r['evaluacion_foto'] = {'rechazada': True}
        self.assertEqual(P.seleccionar(r, [])['estado'], 'no_aplica')

    def test_consenso_no_altera_gravedad_secundarios_o_predominante(self):
        r = {'problemas': [{'key': 'retiro_poda', 'gravedad': 2}, {'key': 'recoleccion', 'gravedad': 3}],
             'predominante': 'recoleccion'}
        antes = copy.deepcopy(r)
        v = [{'modelo': m, 'ok': True, 'prioridad_propuesta': {'key': 'retiro_poda', 'criterio': 'escena'}}
             for m in ['a', 'b']]
        self.assertEqual(P.seleccionar(r, v)['key'], 'retiro_poda')
        self.assertEqual(r, antes)
        self.assertEqual(P.seleccionar(dict(r, problemas=list(reversed(r['problemas']))), v)['key'], 'retiro_poda')

    def test_disenso_fallo_duplicados_y_propuesta_descartada_no_seleccionan(self):
        r = {'problemas': [{'key': 'retiro_poda'}, {'key': 'recoleccion'}]}
        v = [{'modelo': m, 'ok': True, 'prioridad_propuesta': {'key': 'retiro_poda', 'criterio': 'escena'}}
             for m in ['a', 'b']]
        for modo in ['unico', 'duplicado', 'fallo', 'ausente', 'otra_key', 'otro_criterio', 'descartada', 'contexto']:
            with self.subTest(modo=modo):
                vv, rr = copy.deepcopy(v), copy.deepcopy(r)
                if modo == 'unico': vv.pop()
                if modo == 'duplicado': vv[1]['modelo'] = 'a'
                if modo == 'fallo': vv[1]['ok'] = False
                if modo == 'ausente': vv[1].pop('prioridad_propuesta')
                if modo == 'otra_key': vv[1]['prioridad_propuesta']['key'] = 'recoleccion'
                if modo == 'otro_criterio': vv[1]['prioridad_propuesta']['criterio'] = 'pedido_explicito'
                if modo == 'descartada':
                    for x in vv: x['prioridad_propuesta']['key'] = 'retiro_escombros'
                if modo == 'contexto': rr['contexto_visual'] = {'suficiente': False}
                self.assertIsNone(P.seleccionar(rr, vv)['key'])

    def test_pedido_explicito_requiere_cita_real_y_compatibilidad(self):
        cats = {'retiro_poda': {}}
        vistas = [{'key': 'retiro_poda'}]
        ctx = [{'key': 'retiro_poda', 'respaldo': 'compatible'}]
        valor = {'key': 'retiro_poda', 'criterio': 'pedido_explicito', 'cita_vecinal': 'retiren las ramas'}
        self.assertEqual(P.normalizar(valor, cats, vistas, 'Por favor retiren las ramas.', ctx),
                         {'key': 'retiro_poda', 'criterio': 'pedido_explicito'})
        for contexto, contexto_cats in [('', ctx), ('Hay basura', ctx), ('retiren las ramas', [])]:
            self.assertIsNone(P.normalizar(valor, cats, vistas, contexto, contexto_cats))
        self.assertIsNone(P.normalizar(valor, cats, [], 'retiren las ramas', ctx))
        self.assertIsNone(P.normalizar({'key': '<script>', 'criterio': 'escena'}, cats, vistas, '', []))


if __name__ == '__main__':
    unittest.main()
