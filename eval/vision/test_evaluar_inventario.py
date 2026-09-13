"""Pruebas sin red de las vistas y de los límites de la evaluación #44."""
import base64
import copy
import importlib.util
import io
import http.client
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from PIL import Image

spec = importlib.util.spec_from_file_location('evaluacion_inventario',
    Path(__file__).with_name('evaluar_inventario.py'))
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


class Evaluacion(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.base = Path(self.temporal.name)
        buffer = io.BytesIO()
        Image.new('RGB', (400, 600), 'red').save(buffer, format='JPEG')
        self.datos = buffer.getvalue()
        self.foto = self.base/'foto.jpg'
        self.foto.write_bytes(self.datos)
        tareas = [{'id': 'caso-1', 'variante': 'actual', 'archivo': str(self.foto),
                   'sha256': E.sha(self.datos)}]
        raw = json.dumps(tareas).encode()
        (self.base/'tareas.json').write_bytes(raw)
        self.plan = {'autorizado': True, 'tope_usd': 1, 'tareas_sha256': E.sha(raw)}
        self.guardar_plan()
        self.payload = {'messages': [{'role': 'system', 'content': 'Reglas fijas'},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': 'Ejemplo verificado'},
                {'type': 'image_url', 'image_url': {'url': 'referencia-intacta'}},
                {'type': 'text', 'text': 'Foto a evaluar'},
                {'type': 'image_url', 'image_url': {'url': 'original'}}]}]}

    def guardar_plan(self):
        (self.base/'plan.json').write_text(json.dumps(self.plan))

    def estado(self):
        return json.loads((self.base/'estado.json').read_text())

    def test_vistas_no_modifican_reglas_ni_ejemplos(self):
        def solicitud_base(datos, *, vistas):
            self.assertFalse(vistas)
            self.assertEqual(datos, self.datos)
            return copy.deepcopy(self.payload)
        with patch.object(E.especialista, 'solicitud', side_effect=solicitud_base):
            self.assertEqual(E.preparar(self.datos, 'actual'), self.payload)
            for variante, total in [('3200', 4), ('vistas', 12)]:
                salida = E.preparar(self.datos, variante)
                self.assertEqual(salida['messages'][0], self.payload['messages'][0])
                contenido = salida['messages'][1]['content']
                self.assertEqual(contenido[:2], self.payload['messages'][1]['content'][:2])
                self.assertEqual(len(contenido), total)
                for bloque in contenido[3:]:
                    if bloque['type'] != 'image_url' or bloque['image_url']['url'] == 'original':
                        continue
                    raw = base64.b64decode(bloque['image_url']['url'].split(',')[1])
                    with Image.open(io.BytesIO(raw)) as im:
                        self.assertLessEqual(max(im.size), 3200 if variante == '3200' else 1600)
        self.assertEqual(self.foto.read_bytes(), self.datos)

    def test_sin_autorizacion_no_envia(self):
        self.plan['autorizado'] = False
        self.guardar_plan()
        with patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen') as http:
            with self.assertRaisesRegex(ValueError, 'autorización'):
                E.ejecutar(self.base, True)
            http.assert_not_called()

    def test_tope_no_envia(self):
        self.plan['tope_usd'] = .04
        self.guardar_plan()
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen') as http:
            E.ejecutar(self.base, True)
            http.assert_not_called()
        self.assertEqual(self.estado()['pausa'], 'presupuesto')

    def test_timeout_preserva_intento_y_no_repite(self):
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', side_effect=TimeoutError) as http:
            with self.assertRaises(TimeoutError):
                E.ejecutar(self.base, True)
            with self.assertRaisesRegex(ValueError, 'conciliación'):
                E.ejecutar(self.base, True)
            self.assertEqual(http.call_count, 1)
        self.assertEqual(self.estado()['activo']['id'], 'caso-1')
        meta = json.loads((self.base/'evidencia/caso-1/registro.json').read_text())
        self.assertEqual(meta['etapa'], 'error_transporte')
        self.assertFalse((self.base/'evidencia/caso-1/respuesta.bin').exists())

    def test_consumo_incompleto_conserva_respuesta_y_pausa(self):
        respuesta = io.BytesIO(json.dumps({'usage': {'total_tokens': 20}}).encode())
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', return_value=respuesta) as http, patch.object(E.especialista, 'interpretar', return_value={}):
            with self.assertRaisesRegex(ValueError, 'Consumo incompleto'):
                E.ejecutar(self.base, True)
            with self.assertRaisesRegex(ValueError, 'conciliación'):
                E.ejecutar(self.base, True)
            self.assertEqual(http.call_count, 1)
        self.assertTrue((self.base/'caso-1.json').exists())
        self.assertEqual(self.estado()['reserva_incierta_usd'], .05)

    def evidencia(self):
        return self.base/'evidencia/caso-1'

    def test_error_http_conserva_cuerpo_y_no_repite(self):
        raw = b'{"id":"error-sintetico","error":{"message":"sin servicio"}}'
        error = urllib.error.HTTPError('https://openrouter.ai/api/v1/chat/completions',
                                       503, 'Error sintético', {}, io.BytesIO(raw))
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'secreto-sintetico'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', side_effect=error) as http:
            with self.assertRaises(urllib.error.HTTPError):
                E.ejecutar(self.base, True)
            with self.assertRaisesRegex(ValueError, 'conciliación'):
                E.ejecutar(self.base, True)
            self.assertEqual(http.call_count, 1)
        self.assertEqual((self.evidencia()/'respuesta.bin').read_bytes(), raw)
        meta = json.loads((self.evidencia()/'registro.json').read_text())
        self.assertEqual(meta['estado_http'], 503)
        self.assertEqual(meta['etapa'], 'error_http')
        self.assertEqual(meta['respuesta_sha256'], E.sha(raw))
        for f in self.evidencia().iterdir():
            self.assertNotIn(b'secreto-sintetico', f.read_bytes())
            self.assertNotIn(b'Authorization', f.read_bytes())

    def test_json_invalido_conserva_bytes_y_pendiente(self):
        raw = b'No es JSON: \xff'
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', return_value=io.BytesIO(raw)) as http:
            with self.assertRaises(ValueError):
                E.ejecutar(self.base, True)
            with self.assertRaisesRegex(ValueError, 'conciliación'):
                E.ejecutar(self.base, True)
            self.assertEqual(http.call_count, 1)
        self.assertEqual((self.evidencia()/'respuesta.bin').read_bytes(), raw)
        self.assertEqual(json.loads((self.evidencia()/'registro.json').read_text())['etapa'], 'error_json')
        self.assertEqual(self.estado()['activo']['id'], 'caso-1')

    def test_error_interprete_conserva_respuesta_y_contabiliza_una_vez(self):
        respuesta = {'id': 'generacion-prueba', 'usage': {'cost': .02, 'total_tokens': 40}}
        raw = json.dumps(respuesta).encode()
        def fallar(obj):
            previo = json.loads((self.base/'caso-1.json').read_text())
            self.assertEqual(previo['respuesta'], respuesta)
            self.assertEqual((self.evidencia()/'respuesta.bin').read_bytes(), raw)
            # El intérprete tampoco puede alterar el original que voy a conservar.
            obj['usage']['cost'] = 100
            raise RuntimeError('Intérprete sintético')
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', return_value=io.BytesIO(raw)) as http, patch.object(E.especialista, 'interpretar', side_effect=fallar):
            with self.assertRaisesRegex(RuntimeError, 'Intérprete sintético'):
                E.ejecutar(self.base, True)
            E.ejecutar(self.base, True)
            self.assertEqual(http.call_count, 1)
        registro = json.loads((self.base/'caso-1.json').read_text())
        self.assertEqual(registro['respuesta'], respuesta)
        self.assertEqual(registro['interpretado']['estado'], 'error_interpretacion')
        self.assertEqual((self.evidencia()/'respuesta.bin').read_bytes(), raw)
        estado = self.estado()
        self.assertEqual(len(estado['resultados']), 1)
        self.assertAlmostEqual(estado['gasto_usd'], .020001)
        self.assertEqual(estado['reserva_incierta_usd'], 0)
        self.assertIsNone(estado['activo'])

    def test_exito_conserva_interpretacion_y_huellas_de_solicitud(self):
        respuesta = {'id': 'generacion-prueba', 'usage': {'cost': .01, 'total_tokens': 12}}
        esperado = {'estado': 'valido', 'resultado': ['sin_cambios']}
        raw = json.dumps(respuesta).encode()
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'secreto-sintetico'}), patch.object(E, 'preparar', return_value=self.payload), patch.object(E.urllib.request, 'urlopen', return_value=io.BytesIO(raw)) as http, patch.object(E.especialista, 'interpretar', return_value=esperado), patch.object(E.time, 'sleep'):
            E.ejecutar(self.base, True)
        registro = json.loads((self.base/'caso-1.json').read_text())
        self.assertEqual(registro['respuesta'], respuesta)
        self.assertEqual(registro['interpretado'], esperado)
        meta = json.loads((self.evidencia()/'registro.json').read_text())
        self.assertEqual(meta['solicitud_sha256'], E.sha(http.call_args.args[0].data))
        self.assertEqual(meta['plan_sha256'], E.sha((self.base/'plan.json').read_bytes()))
        self.assertEqual(meta['foto_sha256'], E.sha(self.datos))
        self.assertEqual(meta['tareas_sha256'], E.sha((self.base/'tareas.json').read_bytes()))

    def test_ids_invalidos_o_colision_no_llegan_a_la_red(self):
        for ids in [['../afuera'], ['plan'], ['PLAN'], ['x'*121], [['no-es-texto']], ['igual','igual'], ['igual','IGUAL'], ['caso-1']]:
            with self.subTest(ids=ids):
                tareas = [{'id': ident, 'archivo': str(self.foto), 'sha256': E.sha(self.datos), 'variante': 'actual'} for ident in ids]
                E.guardar(self.base/'tareas.json', tareas)
                self.plan['tareas_sha256'] = E.sha((self.base/'tareas.json').read_bytes())
                self.plan['ledger'] = 'caso-1.json'
                self.guardar_plan()
                with patch.object(E.urllib.request, 'urlopen') as http:
                    with self.assertRaisesRegex(ValueError, 'IDs'):
                        E.ejecutar(self.base, True)
                    http.assert_not_called()

    def test_evidencia_sin_ledger_no_se_sobrescribe(self):
        self.evidencia().mkdir(parents=True)
        raw = b'respuesta anterior'
        (self.evidencia()/'respuesta.bin').write_bytes(raw)
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen') as http:
            with self.assertRaisesRegex(ValueError, 'Evidencia anterior'):
                E.ejecutar(self.base, True)
            http.assert_not_called()
        self.assertEqual((self.evidencia()/'respuesta.bin').read_bytes(), raw)

    def test_interpretacion_no_serializable_conserva_consumo(self):
        raw = json.dumps({'usage': {'cost': .01, 'total_tokens': 12}}).encode()
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', return_value=io.BytesIO(raw)), patch.object(E.especialista, 'interpretar', return_value={'objeto': object()}):
            with self.assertRaises(TypeError):
                E.ejecutar(self.base, True)
        self.assertEqual(json.loads((self.base/'caso-1.json').read_text())['interpretado']['estado'], 'error_interpretacion')
        self.assertAlmostEqual(self.estado()['gasto_usd'], .010001)
        self.assertEqual((self.evidencia()/'respuesta.bin').read_bytes(), raw)

    def test_json_sin_objeto_no_oculta_respuesta(self):
        raw = b'["respuesta fuera del contrato"]'
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', return_value=io.BytesIO(raw)):
            with self.assertRaisesRegex(ValueError, 'objeto JSON'):
                E.ejecutar(self.base, True)
        self.assertEqual((self.evidencia()/'respuesta.bin').read_bytes(), raw)
        self.assertEqual(self.estado()['activo']['id'], 'caso-1')

    def test_ledger_se_lee_despues_de_obtener_el_bloqueo(self):
        abrir_original = Path.open
        previo = {'gasto_usd': .2, 'reserva_incierta_usd': 0, 'activo': None,
                  'resultados': [{'id': 'otra-tarea', 'costo': .2}]}
        intercalado = []
        def abrir(ruta, *args, **kwargs):
            if ruta == (self.base/'estado.lock').resolve() and args == ('x',):
                # Otra ejecución termina antes de que ésta adquiera el bloqueo.
                intercalado.append(True)
                (self.base/'estado.json').write_text(json.dumps(previo))
            return abrir_original(ruta, *args, **kwargs)
        raw = json.dumps({'usage': {'cost': .01, 'total_tokens': 12}}).encode()
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', return_value=io.BytesIO(raw)), patch.object(E.especialista, 'interpretar', return_value={}), patch.object(Path, 'open', abrir), patch.object(E.time, 'sleep'):
            E.ejecutar(self.base, True)
        self.assertEqual(intercalado, [True])
        self.assertAlmostEqual(self.estado()['gasto_usd'], .210001)
        self.assertEqual([r['id'] for r in self.estado()['resultados']], ['otra-tarea', 'caso-1'])

    def test_cierre_http_fallido_no_oculta_error_ni_cuerpo(self):
        raw = b'error recibido'
        error = urllib.error.HTTPError('https://openrouter.ai/api/v1/chat/completions',
                                       401, 'Error sintético', {}, io.BytesIO(raw))
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', side_effect=error), patch.object(error, 'close', side_effect=OSError('cierre sintético')):
            with self.assertRaises(urllib.error.HTTPError):
                E.ejecutar(self.base, True)
        error.close()
        meta = json.loads((self.evidencia()/'registro.json').read_text())
        self.assertEqual(meta['error_cierre'], 'OSError')
        self.assertEqual(meta['etapa'], 'error_http')
        self.assertEqual((self.evidencia()/'respuesta.bin').read_bytes(), raw)

    def test_cuerpo_parcial_conserva_estado_http_y_pausa(self):
        class Cortada(io.BytesIO):
            status = 200
            def read(self):
                raise http.client.IncompleteRead(b'fragmento', 100)
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', return_value=Cortada()):
            with self.assertRaises(http.client.IncompleteRead):
                E.ejecutar(self.base, True)
        meta = json.loads((self.evidencia()/'registro.json').read_text())
        self.assertEqual(meta['estado_http'], 200)
        self.assertTrue(meta['respuesta_parcial'])
        self.assertEqual((self.evidencia()/'respuesta.bin').read_bytes(), b'fragmento')
        self.assertEqual(self.estado()['activo']['id'], 'caso-1')

    def test_interrupcion_conserva_registro_y_pendiente(self):
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                E.ejecutar(self.base, True)
        self.assertEqual(json.loads((self.evidencia()/'registro.json').read_text())['etapa'], 'interrumpida')
        self.assertEqual(self.estado()['activo']['id'], 'caso-1')

    def test_error_al_guardar_cuerpo_no_afirma_haberlo_conservado(self):
        raw = b'respuesta recibida'
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', return_value=io.BytesIO(raw)), patch.object(E, 'guardar_bytes', side_effect=OSError('disco sintético')):
            with self.assertRaises(OSError):
                E.ejecutar(self.base, True)
        meta = json.loads((self.evidencia()/'registro.json').read_text())
        self.assertFalse(meta['respuesta_conservada'])
        self.assertEqual(meta['error_escritura'], 'OSError')
        self.assertEqual(meta['etapa'], 'error_escritura')
        self.assertEqual(self.estado()['activo']['id'], 'caso-1')

    def test_error_http_con_cuerpo_parcial_conserva_el_fragmento(self):
        class Cortada(io.BytesIO):
            def read(self, *args):
                raise http.client.IncompleteRead(b'error parcial', 100)
        error = urllib.error.HTTPError('https://openrouter.ai/api/v1/chat/completions',
                                       503, 'Error sintético', {}, Cortada())
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                E.ejecutar(self.base, True)
        meta = json.loads((self.evidencia()/'registro.json').read_text())
        self.assertEqual(meta['estado_http'], 503)
        self.assertEqual(meta['error_lectura'], 'IncompleteRead')
        self.assertTrue(meta['respuesta_parcial'])
        self.assertEqual((self.evidencia()/'respuesta.bin').read_bytes(), b'error parcial')


if __name__ == '__main__':
    unittest.main()
