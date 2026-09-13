"""Pruebas sin red de las vistas y de los límites de la evaluación #44."""
import base64
import copy
import importlib.util
import io
import json
import tempfile
import unittest
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

    def test_consumo_incompleto_conserva_respuesta_y_pausa(self):
        respuesta = io.BytesIO(json.dumps({'usage': {'total_tokens': 20}}).encode())
        with patch.dict(E.os.environ, {'OPENROUTER_API_KEY': 'prueba'}), patch.object(E, 'preparar', return_value={}), patch.object(E.urllib.request, 'urlopen', return_value=respuesta):
            with self.assertRaisesRegex(ValueError, 'Consumo incompleto'):
                E.ejecutar(self.base, True)
        self.assertTrue((self.base/'caso-1.json').exists())
        self.assertEqual(self.estado()['reserva_incierta_usd'], .05)


if __name__ == '__main__':
    unittest.main()
