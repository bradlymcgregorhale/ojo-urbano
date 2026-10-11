"""Respuestas JSON con texto literal: no cortar cadenas ni corregir entradas inválidas."""
import json
import unittest

import verificador as V


class ExtractorJsonTests(unittest.TestCase):
    def test_texto_literal_y_estructuras_se_conservan(self):
        casos = [
            {'evidencia': 'bloque } junto { al cordón'},
            {'evidencia': 'texto ```json y ``` dentro del cartel'},
            {'evidencia': 'comillas " y barra \\ y salto\n, café 🏠'},
            {'objetos': [{'texto': 'cierre }'}, {'anidado': {'texto': '{ apertura'}}]},
        ]
        for original in casos:
            for ascii_only in [True, False]:
                with self.subTest(original=original, ascii_only=ascii_only):
                    texto = json.dumps(original, ensure_ascii=ascii_only)
                    self.assertEqual(V._extraer_json(texto), original)

    def test_cerco_prosa_y_segundo_objeto_no_alteran_el_primero(self):
        original = {'evidencia': 'objeto con } literal', 'ok': True}
        texto = json.dumps(original)
        for envuelto in ['```json\n' + texto + '\n```', 'Respuesta: ' + texto + ' Fin.',
                         texto + '\n{"segunda": false}', '```\n' + texto + '\n```']:
            with self.subTest(envuelto=envuelto):
                self.assertEqual(V._extraer_json(envuelto), original)

    def test_json_invalido_no_se_repara_ni_se_ejecuta(self):
        for texto in ['sin objeto', '{"evidencia":"truncada}', '{"a":1,}',
                      '{"a":', '{"a": True}', '{"a":__import__("os")}']:
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                V._extraer_json(texto)


if __name__ == '__main__':
    unittest.main()
