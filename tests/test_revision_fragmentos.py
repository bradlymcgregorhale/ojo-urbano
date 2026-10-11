"""No duplica un retiro por el fragmento del daño; conserva las pilas independientes."""
import copy
import unittest
from unittest.mock import patch

import modos_analisis as M
import revision_fragmentos as R


def revision(*estados):
    return {'lecturas': [dict(modelo=m, estado=e, evidencia='Bloque contiguo al faltante del cordón.')
                        for m, e in zip(R.MODELOS, estados)], 'fallo': False}


def salida():
    return {'problemas': [{'key': 'reparacion_cordon', 'gravedad': 3},
                         {'key': 'retiro_escombros', 'gravedad': 2},
                         {'key': 'barrido', 'gravedad': 1}],
            'posibles': [], 'en_duda': [], 'categorias_contexto': [],
            'hay_problema': True, 'hay_reclamo': True, 'gravedad_maxima': 3,
            'descripcion': 'Cordón y escombros.', 'costo_api': .1,
            'detalle': {'verificacion': {'activa': True, 'revision_cordon': {'estado': 'confirmado'}}}}


class FragmentosTests(unittest.TestCase):
    def test_tres_lecturas_del_mismo_dano_excluyen_solo_el_retiro(self):
        s = salida(); anterior = copy.deepcopy(s)
        r = R.aplicar(s, revision(*['fragmentos_del_dano'] * 3))
        self.assertEqual([p['key'] for p in r['problemas']], ['reparacion_cordon', 'barrido'])
        self.assertEqual(r['verificacion_escombros']['estado'], 'excluido')
        self.assertEqual(r['costo_api'], .1)
        self.assertEqual(r['gravedad_maxima'], 3)
        self.assertNotIn('escombros', r['descripcion'])
        self.assertEqual(s, anterior)
        self.assertEqual(r['detalle']['verificacion']['revision_fragmentos']['confirmadas_previas'], [anterior['problemas'][1]])

    def test_pila_independiente_conserva_reparacion_y_retiro(self):
        s = salida()
        r = R.aplicar(s, revision(*['pila_independiente'] * 3))
        for k, v in s.items():
            if k != 'detalle': self.assertEqual(r[k], v)
        self.assertEqual(r['detalle']['verificacion']['revision_fragmentos']['estado'], 'pila_independiente')

    def test_disenso_fallo_duplicado_y_formato_no_eliminan_evidencia(self):
        base = revision(*['fragmentos_del_dano'] * 3)
        casos = [revision('fragmentos_del_dano', 'fragmentos_del_dano', 'pila_independiente'),
                 revision('fragmentos_del_dano', 'fragmentos_del_dano', 'indeterminado'),
                 revision('fragmentos_del_dano', 'fragmentos_del_dano'), dict(base, fallo=True)]
        for campo, valor in [('modelo', R.MODELOS[0]), ('evidencia', ''), ('estado', ['invalido'])]:
            c = copy.deepcopy(base); c['lecturas'][2][campo] = valor; casos.append(c)
        for c in casos:
            with self.subTest(c=c):
                r = R.aplicar(salida(), c)
                self.assertEqual(r['problemas'], salida()['problemas'])
                self.assertEqual(r['detalle']['verificacion']['revision_fragmentos']['estado'], 'revision')

    def test_guardas_no_consultan_ni_modifican(self):
        alto = M.Perfil('alto', ('a', 'b', 'c'), '', False, False, False, 'prueba')
        medio = M.Perfil('medio', ('a', 'b'), '', False, False, False, 'prueba')
        casos = [(salida(), medio)]
        for tipo in ('sin_verificacion', 'sin_cordon', 'sin_retiro', 'cordon_pendiente', 'contexto', 'foto_rechazada'):
            s = salida()
            if tipo == 'sin_verificacion': s['detalle']['verificacion']['activa'] = False
            if tipo == 'sin_cordon': s['problemas'] = [s['problemas'][1]]
            if tipo == 'sin_retiro': s['problemas'] = [s['problemas'][0]]
            if tipo == 'cordon_pendiente': s['detalle']['verificacion']['revision_cordon']['estado'] = 'revision'
            if tipo == 'contexto': s['problemas'][1]['origen'] = 'contexto_vecinal'
            if tipo == 'foto_rechazada': s['foto_valida'] = False
            casos.append((s, alto))
        for s, perfil in casos:
            with M.usar(perfil), patch.object(R, 'consultar') as red:
                anterior = copy.deepcopy(s)
                self.assertEqual(R.revisar(None, s), anterior)
                self.assertEqual(s, anterior)
                red.assert_not_called()

    def test_caso_activo_consulta_y_preserva_pila_independiente(self):
        alto = M.Perfil('alto', ('a', 'b', 'c'), '', False, False, False, 'prueba')
        with M.usar(alto), patch.object(R, 'consultar', return_value=revision(*['pila_independiente'] * 3)) as red:
            r = R.revisar(None, salida())
            red.assert_called_once_with(None)
            self.assertEqual(r['problemas'], salida()['problemas'])


if __name__ == '__main__':
    unittest.main()
