"""Las revisiones dirigidas respetan la desactivación y la cuota global."""
import copy
import io
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from PIL import Image

import modos_analisis as M
import revision_cordon as C
import revision_vereda as W


class GuardasDirigidas(unittest.TestCase):
    def test_inactiva_o_ausente_no_consulta_ni_muta(self):
        perfil = M.Perfil('alto', ('m1', 'm2', 'm3'), '', False, False, False, 'prueba')
        for module in (C, W):
            for veri in ({}, {'activa': False, 'motivo': 'desactivada por parámetro'},
                         {'activa': False, 'motivo': 'falta OPENROUTER_API_KEY'},
                         {'activa': False, 'motivo': 'cuota diaria de verificación agotada'}):
                with self.subTest(module=module.KEY, veri=veri), M.usar(perfil), patch.object(module, 'consultar') as red:
                    salida = {'problemas': [{'key': module.KEY, 'fuentes': ['modelo_local']}],
                              'detalle': {'verificacion': veri}}
                    anterior = copy.deepcopy(salida)
                    self.assertEqual(module.revisar(None, salida), anterior)
                    self.assertEqual(salida, anterior)
                    red.assert_not_called()

    def test_activa_conserva_consulta_y_decision(self):
        perfil = M.Perfil('alto', ('m1', 'm2', 'm3'), '', False, False, False, 'prueba')
        for module in (C, W):
            salida = {'problemas': [{'key': module.KEY}], 'detalle': {'verificacion': {'activa': True}}}
            with self.subTest(module=module.KEY), M.usar(perfil), patch.object(module, 'consultar', return_value={'fallo': False}) as red, patch.object(module, 'aplicar', return_value={'resultado': 'conservado'}) as aplicar:
                self.assertEqual(module.revisar(None, salida), {'resultado': 'conservado'})
                red.assert_called_once_with(None)
                aplicar.assert_called_once_with(salida, {'fallo': False})

    @unittest.skipUnless('servidor' in sys.modules, 'Ejecutar con pruebas.py sin pesos')
    def test_recorrido_servidor_sin_verificacion_no_consume(self):
        import servidor as S
        import verificador as V
        import revision_omisiones as O
        datos = io.BytesIO(); Image.new('RGB', (40, 60), 'gray').save(datos, format='JPEG')
        local = {'predichas': [{'key': 'reparacion_cordon', 'nombre': 'Cordón'},
                              {'key': 'reparacion_vereda', 'nombre': 'Vereda'}],
                 'probabilidades': [{'key': 'reparacion_cordon', 'score': .99},
                                   {'key': 'reparacion_vereda', 'score': .99}],
                 'gravedad': {'value': 3}}
        for verificar, disponible, cuota in [('0', True, True), ('auto', False, True), ('auto', True, False)]:
            with self.subTest(verificar=verificar, disponible=disponible, cuota=cuota), ExitStack() as stack:
                stack.enter_context(M.usar(S.PERFILES['alto']))
                stack.enter_context(patch.object(S, 'clasificar_local', return_value=local))
                stack.enter_context(patch.object(S, '_hay_cuota', return_value=cuota))
                stack.enter_context(patch.object(V, 'disponible', return_value=disponible))
                consultas = [stack.enter_context(patch.object(module, name)) for module, name in (
                    (V, 'verificar'), (V, 'verificar_contenedores'), (V, 'validar_alcance_escombros'),
                    (V, 'validar_contexto_obra_servicios'), (V, 'comparar_prioridad'),
                    (C, 'consultar'), (W, 'consultar'), (O, 'consultar_objetos'),
                    (O, 'consultar_residuos'), (O, 'consultar_cubierta'))]
                result = S.procesar(datos.getvalue(), '', verificar)
                self.assertFalse(result['detalle']['verificacion']['activa'])
                self.assertEqual(result['costo_api'], 0)
                for consulta in consultas: consulta.assert_not_called()


if __name__ == '__main__':
    unittest.main()
