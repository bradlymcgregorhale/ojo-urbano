"""Corroboración, vetos y publicación de los objetos omitidos."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import revision_omisiones as R

CATS = json.loads((Path(__file__).parent / 'categorias.json').read_text())


def objeto(modelo, tipo='envase_pintura', uso='descartado'):
    return {'modelo': modelo, 'hallazgos': [{'tipo': tipo, 'uso': uso,
        'objeto': 'balde de pintura', 'evidencia': 'Etiqueta y restos de pintura.',
        'ubicacion': 'junto a las bolsas'}]}


class OmisionesTest(unittest.TestCase):
    def salida(self):
        return {'problemas': [{'key': 'recoleccion', 'nombre': 'Recolección',
                    'gravedad': 2, 'fuentes': ['a', 'b']}],
                'posibles': [{'key': 'retiro_muebles', 'fuentes': ['a']}],
                'en_duda': ['retiro_muebles'], 'descripcion': 'No hay voluminosos.',
                'detalle': {'verificacion': {'activa': True, 'verificadores': []}}}

    def test_modelo_duplicado_y_tipos_distintos_no_alcanzan(self):
        self.assertFalse(R.decidir_objetos({'lecturas': [objeto('a'), objeto('a')]}))
        self.assertFalse(R.decidir_objetos({'lecturas': [objeto('a'), objeto('b', 'tanque_descartado')]}))

    def test_uso_explicito_veta_retiro_y_comercio_no_lo_activa(self):
        self.assertFalse(R.decidir_objetos({'lecturas': [objeto('a'), objeto('b'), objeto('c', uso='en_uso')]}))
        comercio = R.decidir_objetos({'lecturas': [objeto('a', 'venta_ambulante', 'venta'),
                                                 objeto('b', 'venta_ambulante', 'venta')]})
        self.assertEqual([c['key'] for c in comercio], ['manteros'])

    def test_dos_ausencias_sin_usable_y_un_usable_veta(self):
        lecturas = [{'modelo': 'a', 'montaje': 'ausente'}, {'modelo': 'b', 'montaje': 'suelto_adentro'},
                    {'modelo': 'c', 'montaje': 'indeterminado'}]
        self.assertEqual(len(R.decidir_cubierta({'lecturas': lecturas})), 1)
        lecturas[-1]['montaje'] = 'articulado_abierto'
        self.assertFalse(R.decidir_cubierta({'lecturas': lecturas}))

    def test_evidencia_invalida_no_se_convierte_en_un_hallazgo(self):
        for valor in ({}, {'hallazgos': None}):
            with self.assertRaises(ValueError):
                R.interpretar_objetos(valor)
        valor = objeto('a');valor['hallazgos'][0]['tipo'] = 'envase_pintura|tanque_descartado'
        r = R.interpretar_objetos(valor)
        self.assertEqual(r['hallazgos'], [])
        self.assertTrue(r['fallo'])
        self.assertEqual(r['descartados_por_formato'], 1)
        with self.assertRaises(ValueError):
            R.interpretar_cubierta({'montaje': 'ausente', 'evidencia': ''})

    def test_recupera_sin_borrar_basura_ni_reescribir_lecturas(self):
        original = self.salida();antes = copy.deepcopy(original)
        r = R.aplicar(original, {'objetos': {'lecturas': [objeto('a'), objeto('b')]}}, CATS)
        self.assertEqual(original, antes)
        self.assertEqual([p['key'] for p in r['problemas']], ['recoleccion', 'retiro_muebles'])
        self.assertEqual(r['posibles'], [])
        self.assertEqual(r['en_duda'], [])
        self.assertNotIn('No hay', r['descripcion'])
        self.assertEqual(r['detalle']['verificacion']['verificadores'], [])

    def test_no_revoca_veto_previo_del_dano(self):
        original = self.salida()
        original['detalle']['verificacion']['segunda_mirada_dano'] = {'sin_dano': [{'modelo': 'c'}]}
        r = R.aplicar(original, {'cubierta': {'lecturas': [
            {'modelo': 'a', 'montaje': 'ausente'}, {'modelo': 'b', 'montaje': 'ausente'}]}}, CATS)
        self.assertEqual(r['problemas'], original['problemas'])
        self.assertTrue(r['detalle']['verificacion']['revision_omisiones']['conflicto_cubierta'])

    def test_segunda_mirada_de_montaje_conserva_votos_y_veto(self):
        import verificador as V
        lecturas = [{'modelo': 'a', 'montaje': 'ausente', 'evidencia': 'Boca sin panel.'},
                    {'modelo': 'b', 'montaje': 'suelto_adentro', 'evidencia': 'Panel caído.'},
                    {'modelo': 'c', 'montaje': 'articulado_abierto', 'evidencia': 'Bisagra unida.'}]
        with patch.object(R, 'consultar_cubierta', return_value={'lecturas': lecturas, 'fallo': False}):
            dano, usable, fallo = V._segunda_mirada_dano(Image.new('RGB', (20, 20)), montaje_cubierta=True)
        self.assertEqual([m for m, _ in dano], ['a', 'b'])
        self.assertEqual([m for m, _ in usable], ['c'])
        self.assertFalse(fallo)

    def test_foto_no_corresponde_no_recupera_servicios(self):
        original = self.salida();original['foto_valida'] = False
        r = R.aplicar(original, {'objetos': {'lecturas': [objeto('a'), objeto('b')]}}, CATS)
        self.assertEqual(r['problemas'], original['problemas'])

    def test_referencias_ausentes_fallan_sin_inventar_un_voto(self):
        with patch.object(Path, 'read_bytes', side_effect=FileNotFoundError):
            resultado = R.consultar_cubierta(Image.new('RGB', (20, 20)))
        self.assertTrue(resultado['fallo'])
        self.assertEqual(resultado['lecturas'], [])


if __name__ == '__main__':
    unittest.main()
