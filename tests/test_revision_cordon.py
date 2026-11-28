import copy
import unittest
from unittest.mock import patch

import modos_analisis as M
import revision_cordon as R


def revision(estados, **extra):
    return dict(lecturas=[dict(modelo=m, estado=e, pieza='cordon', evidencia='Cara del borde visible')
                         for m, e in zip(R.MODELOS, estados)], fallo=False, **extra)


class GeometriaCordon(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(R, 'MODELOS', ('prueba/a', 'prueba/b', 'prueba/c')))

    def test_rotura_requiere_tres_lectores_y_pieza_correcta(self):
        r = revision(['rotura_estructural'] * 3)
        self.assertEqual(R.decidir(r), 'confirmado')
        for campo, valor in [('pieza', 'vereda'), ('evidencia', ''), ('modelo', R.MODELOS[0]),
                             ('estado', 'indeterminado')]:
            copia = copy.deepcopy(r)
            copia['lecturas'][2][campo] = valor
            self.assertEqual(R.decidir(copia), 'revision')
        r['fallo'] = True
        self.assertEqual(R.decidir(r), 'revision')

    def test_diseno_y_desgaste_no_confirman_reparacion(self):
        self.assertEqual(R.decidir(revision(['sano', 'desgaste_superficial', 'diseno_funcional'])), 'sin_rotura')
        self.assertEqual(R.decidir(revision(['rotura_estructural', 'rotura_estructural', 'sano'])), 'revision')

    def test_desacuerdo_preserva_otras_categorias_y_traza_original(self):
        cordon = dict(key=R.KEY, nombre='Cordón', gravedad=4, fuentes=['lector1', 'lector2'])
        barrido = dict(key='barrido', nombre='Barrido', gravedad=2, fuentes=['lector1'])
        original = dict(problemas=[cordon, barrido], posibles=[], en_duda=[], descripcion='Cordón roto y basura',
                        detalle={'verificacion': {'verificadores': [{'modelo': 'lector1'}]}})
        anterior = copy.deepcopy(original)
        r = R.aplicar(original, revision(['rotura_estructural', 'rotura_estructural', 'indeterminado']))
        self.assertEqual(original, anterior)
        self.assertEqual(r['problemas'], [barrido])
        self.assertEqual(r['en_duda'], [R.KEY])
        self.assertIsNone(r['posibles'][0]['arbitro'])
        self.assertEqual(r['detalle']['verificacion']['revision_cordon']['confirmadas_previas'], [cordon])
        self.assertNotIn('Cordón roto', r['descripcion'])
        negativo = R.aplicar(original, revision(['sano'] * 3))
        self.assertEqual(negativo['problemas'], [barrido])
        self.assertEqual(negativo['posibles'][0]['arbitro'], 'rechazar')
        self.assertEqual(negativo['en_duda'], [])

    def test_modos_reducidos_foto_invalida_y_contexto_no_consultan(self):
        base = {'problemas': [{'key': R.KEY}]}
        for modo in ['bajo', 'medio']:
            with M.usar(M.Perfil(modo, ('m1',), '', False, False, False, 'prueba')), patch.object(R, 'consultar') as red:
                self.assertEqual(R.revisar(None, base), base)
                red.assert_not_called()
        with M.usar(M.Perfil('alto', ('m1', 'm2', 'm3'), '', False, False, False, 'prueba')), patch.object(R, 'consultar') as red:
            for r in [dict(base, foto_valida=False), {'problemas': [{'key': R.KEY, 'origen': 'contexto_vecinal'}]}, {'problemas': []}]:
                self.assertEqual(R.revisar(None, r), r)
            red.assert_not_called()

    def test_desempate_exige_una_pieza_con_respaldo_de_tres_modelos_distintos(self):
        base = revision(['rotura_estructural', 'rotura_estructural', 'desgaste_superficial'])
        cuarto = {'modelo': R.MODELO_DESEMPATE, 'decision': 'pieza_desplazada',
                  'prueba_fisica': 'Un bloque entero quedó corrido hacia la cuneta.'}
        for positivo in ('pieza_desplazada', 'seccion_perdida'):
            self.assertEqual(R.decidir(dict(base, desempate=dict(cuarto, decision=positivo))), 'confirmado')
        for cambio in [{'modelo': R.MODELOS[0]}, {'decision': 'sin_obra'}, {'fallo': True},
                       {'decision': 'indeterminado'}, {'prueba_fisica': ' '}]:
            self.assertEqual(R.decidir(dict(base, desempate=cuarto | cambio)), 'revision')
        self.assertEqual(R.decidir(dict(base, fallo=True, desempate=cuarto)), 'revision')
        base['lecturas'][0]['pieza'] = 'vereda'
        self.assertEqual(R.decidir(dict(base, desempate=cuarto)), 'revision')
