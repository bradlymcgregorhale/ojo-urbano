"""Controles sin red de la confirmación limitada de ausencia (#44)."""
import copy
import io
import json
import sys
import unittest
from unittest.mock import patch

from PIL import Image

import especialista_contenedores as E
import modos_analisis as M
import verificador as V


def respuesta(modelo, contenido, **cambio):
    r = {'model': modelo, 'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps(contenido)}}], 'usage': {'cost': .001, 'total_tokens': 5}}
    r.update(cambio)
    return r


class Presencia(unittest.TestCase):
    def setUp(self):
        b = io.BytesIO()
        Image.new('RGB', (80, 60)).save(b, format='JPEG')
        self.datos = b.getvalue()
        self.modelos = ['m1', 'm2', 'm3']
        self.lecturas = [dict(modelo=m, presente=False, evidencia='Escena sin contenedores')
                         for m in self.modelos]
        V.costo_reset()
        V.tokens_reset()
        self.addCleanup(V.tokens_total)
        for nombre, valor in [('api_key', lambda: 'prueba'), ('OPENROUTER_LOG_USO', False)]:
            parche = patch.object(V, nombre, valor)
            parche.start()
            self.addCleanup(parche.stop)

    def test_ausencia_exige_tres_negativos_distintos_y_con_evidencia(self):
        original = E.revision()
        self.assertEqual(E.resolver_ausencia(original, self.lecturas)['tipos'], [])
        for campo, valor in [('presente', None), ('presente', True), ('presente', 0),
                             ('evidencia', ''), ('evidencia', None), ('modelo', 'm2')]:
            votos = copy.deepcopy(self.lecturas)
            votos[0][campo] = valor
            self.assertEqual(E.resolver_ausencia(original, votos), original)
        for votos in [self.lecturas[:2], self.lecturas + self.lecturas[:1], [None] * 3]:
            self.assertEqual(E.resolver_ausencia(original, votos), original)

    def test_conserva_confirmados_y_fallos_del_especialista(self):
        for original in [dict(estado='confirmado', tipos=[E.TIPOS[0]], fallo=False),
                         dict(estado='confirmado', tipos=[], fallo=False), E.revision(fallo=True),
                         dict(estado='revision', tipos=[E.TIPOS[0]], fallo=False)]:
            antes = copy.deepcopy(original)
            self.assertEqual(E.resolver_ausencia(original, self.lecturas), antes)
            self.assertEqual(original, antes)

    def test_parsea_json_y_bloque_sin_relajar_el_contrato(self):
        r = respuesta('m1', {'presente': False, 'evidencia': 'Sin contenedores'})
        for bloque in [False, True]:
            c = copy.deepcopy(r)
            if bloque:
                c['choices'][0]['message']['content'] = '```json\n' + c['choices'][0]['message']['content'] + '\n```'
            self.assertIs(E.interpretar_presencia(c, 'm1')['presente'], False)
        for contenido in ['{"presente":false,"presente":true,"evidencia":"x"}',
                          '{"presente":0,"evidencia":"x"}', '{"presente":false}',
                          '{"presente":false,"evidencia":" "}',
                          '{"presente":false,"evidencia":"x","otro":1}']:
            c = copy.deepcopy(r)
            c['choices'][0]['message']['content'] = contenido
            with self.assertRaises(ValueError):
                E.interpretar_presencia(c, 'm1')

    def test_rechaza_truncado_rechazo_y_modelo_distinto(self):
        r = respuesta('m1', {'presente': False, 'evidencia': 'Sin contenedores'})
        for campo, valor in [('finish_reason', 'length'), ('refusal', 'rechazo'), ('model', 'm2')]:
            c = copy.deepcopy(r)
            if campo == 'finish_reason': c['choices'][0][campo] = valor
            elif campo == 'refusal': c['choices'][0]['message'][campo] = valor
            else: c[campo] = valor
            with self.assertRaises(ValueError):
                E.interpretar_presencia(c, 'm1')

    def ejecutar(self, duda=True, fallo_modelo=None):
        def http(req, *args):
            modelo = json.loads(req.data)['model']
            if modelo == E.MODELO:
                return respuesta(modelo, {'types': [], 'uncertain': duda, 'evidence': 'Escena'})
            if modelo == fallo_modelo:
                raise TimeoutError('sin respuesta')
            return respuesta(modelo, {'presente': False, 'evidencia': 'Escena sin contenedores'})
        with patch.object(E, 'solicitud', return_value={'model': E.MODELO}), \
                patch.object(V, 'modelos_activos', return_value=self.modelos), \
                patch.object(V, '_pedir_http', side_effect=http) as red:
            r = V.verificar_contenedores(self.datos)
            return r, red.call_count

    def test_ruta_incierta_cuenta_las_cuatro_llamadas(self):
        r, llamadas = self.ejecutar()
        self.assertEqual((r['estado'], r['tipos'], llamadas), ('confirmado', [], 4))
        self.assertEqual(V.tokens_total(), {'tokens_api': 20, 'tokens_api_completos': True})
        self.assertAlmostEqual(V.costo_total(), .004)

    def test_ruta_confirmada_no_hace_lecturas_adicionales(self):
        r, llamadas = self.ejecutar(duda=False)
        self.assertEqual((r['estado'], r['tipos'], llamadas), ('confirmado', [], 1))

    def test_timeout_no_repite_y_conserva_incertidumbre_y_consumo(self):
        with patch.object(M, 'fallo') as fallo:
            r, llamadas = self.ejecutar(fallo_modelo='m2')
            fallo.assert_called_once_with('presencia_contenedores')
        self.assertEqual((r['estado'], r['tipos'], llamadas), ('revision', None, 4))
        self.assertIs(r['fallo'], True)
        self.assertEqual(V.tokens_total(), {'tokens_api': 15, 'tokens_api_completos': False})
        self.assertAlmostEqual(V.costo_total(), .003)

    def test_modos_reducidos_no_hacen_lecturas_de_presencia(self):
        for modo, lectores in [('bajo', ('m1',)), ('medio', ('m1', 'm2'))]:
            perfil = M.Perfil(modo, lectores, '', False, False, False, 'prueba')
            with M.usar(perfil), patch.object(V, '_pedir_http') as red:
                with self.assertRaises(ValueError):
                    V.verificar_contenedores(self.datos)
                red.assert_not_called()

    def test_cantidad_distinta_de_tres_no_activa_lecturas(self):
        self.modelos = ['m1', 'm2']
        r, llamadas = self.ejecutar()
        self.assertEqual((r['estado'], llamadas), ('revision', 1))

    def test_plazo_compartido_y_pedido_sin_inventario_previo(self):
        cuerpos = []
        plazos = []
        with patch.object(V.time, 'monotonic', return_value=100) as reloj:
            def http(req, timeout, vence):
                cuerpo = json.loads(req.data)
                plazos.append((cuerpo['model'], timeout, vence))
                if cuerpo['model'] == E.MODELO:
                    reloj.return_value = 133
                    return respuesta(E.MODELO, {'types': [], 'uncertain': True,
                                                 'evidence': 'MARCA_PRIVADA_NO_REENVIAR'})
                cuerpos.append(cuerpo)
                return respuesta(cuerpo['model'], {'presente': False, 'evidencia': 'Escena sin contenedores'})
            with patch.object(E, 'solicitud', return_value={'model': E.MODELO}), \
                    patch.object(V, 'modelos_activos', return_value=self.modelos), \
                    patch.object(V, '_pedir_http', side_effect=http):
                self.assertEqual(V.verificar_contenedores(self.datos)['tipos'], [])
        self.assertEqual(len(cuerpos), 3)
        self.assertEqual(len(plazos), 4)
        for modelo, timeout, vence in plazos:
            self.assertEqual(vence, 140)
            self.assertLessEqual(timeout, 40 if modelo == E.MODELO else 7)
        for cuerpo in cuerpos:
            self.assertNotIn('MARCA_PRIVADA_NO_REENVIAR', json.dumps(cuerpo))
            self.assertEqual(cuerpo, E.solicitud_presencia(self.datos, cuerpo['model']))

    def test_sin_tiempo_no_envia_mas_ni_marca_un_fallo_inexistente(self):
        with patch.object(V.time, 'monotonic', return_value=100) as reloj:
            def http(req, *args):
                reloj.return_value = 139.5
                return respuesta(E.MODELO, {'types': [], 'uncertain': True, 'evidence': 'Escena'})
            with patch.object(E, 'solicitud', return_value={'model': E.MODELO}), \
                    patch.object(V, 'modelos_activos', return_value=self.modelos), \
                    patch.object(M, 'fallo') as fallo, patch.object(V, '_pedir_http', side_effect=http) as red:
                resultado = V.verificar_contenedores(self.datos)
                self.assertEqual(resultado['estado'], 'revision')
                self.assertIs(resultado['fallo'], True)
                self.assertEqual(red.call_count, 1)
                fallo.assert_called_once_with('presencia_contenedores_sin_tiempo')

    def test_procedencia_no_modifica_lecturas_y_respeta_contradiccion_publica(self):
        antes = copy.deepcopy(self.lecturas)
        inventario = E.resolver_ausencia(E.revision(), self.lecturas)
        inventario['revision_presencia']['lecturas'][0]['evidencia'] = 'Cambio externo'
        self.assertEqual(antes, self.lecturas)
        original = {'problemas': [{'key': 'reparacion_contenedor'}], 'modelos': []}
        publico = E.aplicar(original, inventario, {})
        self.assertEqual(publico['contenedores']['estado'], 'revision')
        self.assertIsNone(publico['contenedores']['tipos'])
        self.assertEqual(len(publico['contenedores']['revision_presencia']['lecturas']), 3)

    @unittest.skipUnless('servidor' in sys.modules, 'Ejecutar con pruebas.py para usar el servidor sin pesos')
    def test_fallo_de_presencia_no_se_guarda_como_duda_valida(self):
        import servidor as S
        inventario, _ = self.ejecutar(fallo_modelo='m2')
        r = {'detalle': {'verificacion': {'activa': True, 'inventario_contenedores': inventario}}}
        self.assertFalse(S._cacheable(r))
        r['detalle']['verificacion']['inventario_contenedores'] = E.revision()
        self.assertTrue(S._cacheable(r))


if __name__ == '__main__':
    unittest.main()
