"""Controles sintéticos de la reproducción de retiros, sin red (#45)."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import reproducir_poda as R
from test_politica_escombros import respuesta


class ReproduccionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'respuesta.json'
        self.retiros = ('retiro_poda', 'retiro_muebles')
        self.datos = {'foto': 'S001', 'resultado': {
            'modelos': [{'modelo': m, 'ok': True, 'categorias': [
                {'key': k, 'evidencia': 'Objeto visible independiente'} for k in self.retiros]}
                for m in ['m1', 'm2']],
            'verificacion_escombros': {'revisiones': [
                {'modelo': m, 'estado': 'ok', 'respuesta': respuesta(
                    material='incompatible_visible', hay_bolsas_opacas_o_parciales='no',
                    afirmacion_vecinal='no_menciona', cita_vecinal='')}
                for m in ['m1', 'm2']]}}}

    def ejecutar(self, **cambios):
        datos = copy.deepcopy(self.datos)
        for r in datos['resultado']['verificacion_escombros']['revisiones']:
            r['respuesta'].update(cambios)
        self.path.write_text(json.dumps(datos))
        antes = self.path.read_bytes()
        r = R.reproducir(self.path, self.retiros)
        self.assertEqual(self.path.read_bytes(), antes)
        self.assertEqual(r['llamadas_openrouter'], 0)
        return r

    def test_regla_necesaria_para_ambos_retiros(self):
        r = self.ejecutar()
        self.assertEqual(r['cumple_retiros'], dict.fromkeys(self.retiros, True))
        self.assertEqual(r['regla_preservacion_necesaria'], dict.fromkeys(self.retiros, True))
        self.assertEqual(r['problemas_sin_regla_preservacion'], [])

    def test_alcance_apto_no_acredita_regla_de_preservacion(self):
        r = self.ejecutar(material='escombros_visible')
        self.assertEqual(r['estado_alcance'], 'apto')
        self.assertTrue(all(r['cumple_retiros'].values()))
        self.assertFalse(any(r['regla_preservacion_necesaria'].values()))

    def test_retiros_ya_independientes_no_acreditan_regla_nueva(self):
        r = self.ejecutar(otros_retiros_independientes=list(self.retiros))
        self.assertTrue(all(r['cumple_retiros'].values()))
        self.assertFalse(any(r['regla_preservacion_necesaria'].values()))

    def test_interior_no_conserva_retiros(self):
        r = self.ejecutar(ubicacion='privada')
        self.assertFalse(any(r['cumple_retiros'].values()))
        self.assertFalse(any(r['regla_preservacion_necesaria'].values()))

    def test_votos_duplicados_no_son_dos_lectores(self):
        self.datos['resultado']['modelos'][1]['modelo'] = 'm1'
        with self.assertRaises(ValueError):
            self.ejecutar()
