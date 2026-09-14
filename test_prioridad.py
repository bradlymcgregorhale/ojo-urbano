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

    def _disenso(self):
        r = {'problemas': [{'key': 'retiro_escombros'}, {'key': 'recoleccion'}]}
        v = [
            {'modelo': 'a', 'ok': True, 'prioridad_propuesta': {'key': 'retiro_escombros', 'criterio': 'escena'},
             'categorias': [{'key': 'retiro_escombros', 'evidencia': 'sacos de obra'},
                            {'key': 'recoleccion', 'evidencia': 'bolsas negras'}]},
            {'modelo': 'b', 'ok': True, 'prioridad_propuesta': {'key': 'recoleccion', 'criterio': 'escena'},
             'categorias': [{'key': 'retiro_escombros', 'evidencia': 'Sacos de obra'},
                            {'key': 'recoleccion', 'evidencia': 'tres bolsas'}]},
        ]
        return r, v

    def test_comparacion_solo_ante_disenso_completo(self):
        r, v = self._disenso()
        llamadas = []

        def comparacion(candidatos):
            llamadas.append(candidatos)
            return {'key': 'retiro_escombros', 'fundamento': 'acumulacion_principal', 'evidencia': 'sacos'}

        consenso = copy.deepcopy(v)
        consenso[1]['prioridad_propuesta']['key'] = 'retiro_escombros'
        self.assertEqual(P.seleccionar(r, consenso, comparacion=comparacion)['key'], 'retiro_escombros')
        self.assertEqual(llamadas, [])
        self.assertTrue(P.requiere_comparacion(r, v))
        elegido = P.seleccionar(r, v, comparacion=comparacion)
        self.assertEqual(elegido['key'], 'retiro_escombros')
        self.assertEqual(elegido['criterio'], 'escena')
        self.assertEqual(elegido['motivo'], P.FUNDAMENTOS['acumulacion_principal'])
        self.assertEqual(len(llamadas), 1)
        self.assertEqual([c['key'] for c in llamadas[0]], ['retiro_escombros', 'recoleccion'])
        self.assertEqual(llamadas[0][0]['observaciones'], ['sacos de obra'])

    def test_comparacion_fallida_nula_o_fuera_de_confirmadas_no_inventa(self):
        r, v = self._disenso()
        self.assertEqual(P.seleccionar(r, v)['estado'], 'indeterminado')
        def boom(_candidatos):
            raise TimeoutError('x')
        self.assertEqual(P.seleccionar(r, v, comparacion=boom)['estado'], 'no_evaluado')
        self.assertEqual(
            P.seleccionar(r, v, comparacion=lambda _c: {'key': None, 'fundamento': None, 'evidencia': 'empate'})['estado'],
            'indeterminado')
        self.assertEqual(
            P.seleccionar(r, v, comparacion=lambda _c: {'key': 'volquete_mal_dispuesto', 'fundamento': 'objeto_principal', 'evidencia': 'x'})['estado'],
            'indeterminado')
        self.assertFalse(P.requiere_comparacion(dict(r, contexto_visual={'suficiente': False}), v))
        self.assertFalse(P.requiere_comparacion({'problemas': [{'key': 'retiro_escombros'}]}, v))
        self.assertFalse(P.requiere_comparacion(dict(r, evaluacion_foto={'rechazada': True}), v))

    def test_escena_mixta_sin_prioridad_clara_no_inventa_principal(self):
        r = {'problemas': [
            {'key': 'barrido'}, {'key': 'hidrolavado_grafitis'},
            {'key': 'recoleccion'}, {'key': 'retiro_muebles'},
        ]}
        v = [
            {'modelo': 'a', 'ok': True, 'prioridad_propuesta': {'key': 'recoleccion', 'criterio': 'escena'},
             'categorias': [{'key': 'recoleccion', 'evidencia': 'papeles sueltos'}]},
            {'modelo': 'b', 'ok': True, 'prioridad_propuesta': {'key': 'retiro_muebles', 'criterio': 'escena'},
             'categorias': [{'key': 'retiro_muebles', 'evidencia': 'cajón en la vereda'},
                            {'key': 'barrido', 'evidencia': 'restos en baldosas'}]},
            {'modelo': 'c', 'ok': True, 'prioridad_propuesta': {'key': 'barrido', 'criterio': 'escena'},
             'categorias': [{'key': 'barrido', 'evidencia': 'hojas y papeles'},
                            {'key': 'hidrolavado_grafitis', 'evidencia': 'pintada en la pared'}]},
        ]
        llamadas = []

        def empate(candidatos):
            llamadas.append([c['key'] for c in candidatos])
            return {'key': None, 'fundamento': None, 'evidencia': 'varios focos sin uno principal'}

        self.assertTrue(P.requiere_comparacion(r, v))
        self.assertEqual(P.seleccionar(r, v, comparacion=empate)['estado'], 'indeterminado')
        self.assertEqual(llamadas, [['barrido', 'hidrolavado_grafitis', 'recoleccion', 'retiro_muebles']])
        sin_propuestas = [{'modelo': m, 'ok': True} for m in ['a', 'b', 'c']]
        self.assertFalse(P.requiere_comparacion(r, sin_propuestas))
        self.assertEqual(P.seleccionar(r, sin_propuestas, comparacion=empate)['estado'], 'no_evaluado')
        self.assertEqual(llamadas, [['barrido', 'hidrolavado_grafitis', 'recoleccion', 'retiro_muebles']])

    def test_comparacion_usa_problemas_despues_de_evaluar_ambito(self):
        import evaluacion_foto as E
        r, v = self._disenso()
        for lectura in v:
            lectura['evaluacion_foto'] = {
                'ambito': 'interior', 'evidencia_ambito': 'El colchón está dentro de una habitación.',
                'calidad_suficiente': True, 'contexto_suficiente': True,
            }
        previa = E.aplicar(r, v)
        self.assertTrue(previa['evaluacion_foto']['rechazada'])
        self.assertFalse(P.requiere_comparacion(previa, v))
        self.assertEqual(P.seleccionar(previa, v, comparacion=lambda _c: {'key': 'retiro_escombros'})['estado'],
                         'no_aplica')

    def test_comparacion_guardada_no_vuelve_a_consultar(self):
        r, v = self._disenso()
        veri = {'prioridad_comparacion': {'key': 'retiro_escombros', 'fundamento': 'acumulacion_principal'}}
        self.assertEqual(P.seleccionar(r, v, comparacion=P.desde_guardada(veri))['key'], 'retiro_escombros')
        self.assertEqual(
            P.seleccionar(r, v, comparacion=P.desde_guardada({'prioridad_comparacion_error': True}))['estado'],
            'no_evaluado')
        self.assertIsNone(P.desde_guardada({}))

    def test_comparar_prioridad_usa_el_prompt_dirigido(self):
        from unittest.mock import patch
        import verificador as V
        with patch.object(V, '_imagen_data_url', return_value='data:image/jpeg;base64,QQ=='), \
                patch.object(V, '_llamar', return_value='{"key":"retiro_escombros","fundamento":"acumulacion_principal","evidencia":"sacos"}') as llamada:
            r = V.comparar_prioridad(object(), [{'key': 'retiro_escombros', 'nombre': 'Escombros', 'observaciones': ['sacos']}])
        self.assertEqual(r['key'], 'retiro_escombros')
        modelo, mensajes = llamada.call_args.args[:2]
        self.assertEqual(modelo, V.MODELO_PRIORIDAD_COMPARACION)
        self.assertEqual(llamada.call_args.kwargs['etapa'], 'prioridad_comparacion')
        self.assertEqual(mensajes[0]['content'], V._PROMPT_PRIORIDAD_COMPARACION)
        self.assertNotIn(V.PRIORIDAD[:40], mensajes[0]['content'])


if __name__ == '__main__':
    unittest.main()
