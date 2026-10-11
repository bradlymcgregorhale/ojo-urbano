import copy
import unittest
from unittest.mock import patch

import revision_vereda as R


def revision(estados, **extra):
    return dict(lecturas=[dict(modelo=m, estado=e, sector='plano junto al poste',
        evidencia='Geometría del piso visible.') for m, e in zip(R.MODELOS, estados)],
        fallo=False, **extra)


class PlanoPeatonal(unittest.TestCase):
    def salida(self):
        return dict(problemas=[dict(key=R.KEY, nombre='Vereda', gravedad=3, fuentes=['a', 'b']),
            dict(key='reparacion_cordon', nombre='Cordón', gravedad=3, fuentes=['a', 'b'])],
            posibles=[], en_duda=[], descripcion='Vereda y cordón rotos.',
            detalle={'verificacion': {'verificadores': [{'modelo': 'a'}]}})

    def test_dos_roturas_propias_conservan_la_reparacion(self):
        r = revision(['rotura_estructural', 'rotura_estructural', 'indeterminado'])
        self.assertEqual(R.decidir(r), 'confirmado')
        original = self.salida()
        self.assertEqual(R.aplicar(original, r)['problemas'], original['problemas'])

    def test_fisuras_y_otros_objetos_no_borran_cordon_ni_lecturas(self):
        original = self.salida(); antes = copy.deepcopy(original)
        publicado = R.aplicar(original, revision(['fisura_menor', 'dano_cordon', 'indeterminado']))
        self.assertEqual(original, antes)
        self.assertEqual([c['key'] for c in publicado['problemas']], ['reparacion_cordon'])
        self.assertEqual(publicado['posibles'][0]['arbitro'], 'rechazar')
        self.assertEqual(publicado['detalle']['verificacion']['verificadores'],
                         original['detalle']['verificacion']['verificadores'])
        self.assertEqual(publicado['detalle']['verificacion']['revision_vereda']
                         ['confirmadas_previas'], original['problemas'][:1])

    def test_tapa_conserva_servicio_especifico_y_no_crea_otro(self):
        original = self.salida()
        original['problemas'].append(dict(key='tapa_vereda', nombre='Tapa', gravedad=4))
        r = R.aplicar(original, revision(['hueco_servicio', 'hueco_servicio', 'sin_rotura']))
        self.assertEqual({c['key'] for c in r['problemas']}, {'reparacion_cordon', 'tapa_vereda'})
        self.assertEqual(r['gravedad_maxima'], 4)

    def test_una_rotura_estructural_impide_rechazar_por_mayoria_negativa(self):
        r = revision(['rotura_estructural', 'fisura_menor', 'sin_rotura'])
        self.assertEqual(R.decidir(r), 'revision')
        publicado = R.aplicar(self.salida(), r)
        self.assertIn(R.KEY, publicado['en_duda'])
        self.assertIsNone(publicado['posibles'][0]['arbitro'])

    def test_fallos_duplicados_y_evidencia_vacia_no_son_negativas(self):
        for cambio in ('fallo', 'duplicado', 'evidencia', 'ausente'):
            r = revision(['sin_rotura'] * 3)
            if cambio == 'fallo': r['fallo'] = True
            elif cambio == 'duplicado': r['lecturas'][2]['modelo'] = R.MODELOS[0]
            elif cambio == 'evidencia': r['lecturas'][2]['evidencia'] = ''
            else: r['lecturas'].pop()
            self.assertEqual(R.decidir(r), 'revision')

    def test_declaracion_vecinal_no_se_borra_por_una_negativa_visual(self):
        original = self.salida(); original['problemas'][0]['origen'] = 'contexto_vecinal'
        publicado = R.aplicar(original, revision(['sin_rotura'] * 3))
        self.assertEqual(publicado['problemas'], original['problemas'])

    def test_foto_rechazada_y_modos_reducidos_no_pagan_consulta(self):
        import modos_analisis as M
        from PIL import Image
        original = self.salida(); original['foto_valida'] = False
        with patch.object(R, 'consultar') as consultar, \
                patch.object(M, 'perfil_actual', return_value=type('Perfil', (), {'modo': 'alto'})()):
            self.assertEqual(R.revisar(Image.new('RGB', (20, 20)), original), original)
        consultar.assert_not_called()
        original.pop('foto_valida')
        with patch.object(R, 'consultar') as consultar, \
                patch.object(M, 'perfil_actual', return_value=type('Perfil', (), {'modo': 'medio'})()):
            self.assertEqual(R.revisar(Image.new('RGB', (20, 20)), original), original)
        consultar.assert_not_called()


if __name__ == '__main__':
    unittest.main()
