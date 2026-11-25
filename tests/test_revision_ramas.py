import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import revision_ramas as ramas


def resultado(*claves):
    return {'problemas': [{'key': k, 'fuentes': ['lector'], 'gravedad': 3} for k in claves],
            'posibles': [{'key': 'obstruccion'}, {'key': 'recoleccion'}],
            'en_duda': ['obstruccion', 'recoleccion'],
            'categorias_contexto': [{'codigo': '176011'}, {'codigo': '118020'}],
            'foto_valida': True, 'descripcion': 'Descripción inicial.'}


class TestRevisionRamas(unittest.TestCase):
    lectura = {'rama_desprendida': 'si', 'problemas_arbolado': 'no', 'obstruccion': 'no',
               'evidencia': 'Rama desprendida sobre la vereda.'}

    def test_misma_rama_en_completo_y_en_prueba(self):
        for modo, duplicada in (('alto', 'problemas_arbolado'), ('prueba', 'obstruccion')):
            with self.subTest(modo=modo):
                entrada = resultado(duplicada, 'retiro_poda')
                perfil = SimpleNamespace(modo=modo, verificadores=('uno', 'dos', 'tres'))
                with patch('verificador._imagen_data_url', return_value='imagen'), \
                        patch('verificador._llamar', return_value=json.dumps(self.lectura)) as llamada:
                    corregida = ramas.revisar(object(), entrada, perfil, 'árbol caído' if modo == 'alto' else '')
                self.assertEqual([p['key'] for p in corregida['problemas']], ['retiro_poda'])
                self.assertEqual(corregida['en_duda'], ['recoleccion'])
                self.assertEqual(corregida['categorias_contexto'], [] if modo == 'alto' else [{'codigo': '176011'}])
                self.assertEqual(corregida['descripcion'], self.lectura['evidencia'])
                self.assertEqual(corregida['revision_ramas']['descartadas'], list(ramas.ALTERNATIVAS))
                self.assertNotIn('árbol caído', str(llamada.call_args.args))
                self.assertEqual(len(entrada['problemas']), 2)

    def test_conserva_arbol_y_barrera_separados(self):
        entrada = resultado('problemas_arbolado', 'retiro_poda', 'obstruccion', 'recoleccion')
        for estado in ('si', 'indeterminado'):
            lectura = dict(self.lectura, problemas_arbolado=estado, obstruccion=estado)
            corregida = ramas.aplicar(entrada, lectura)
            self.assertEqual(corregida['problemas'], entrada['problemas'])
            self.assertEqual(corregida['descripcion'], entrada['descripcion'])

    def test_incertidumbre_no_borra_dano_y_barrera_puede_faltar(self):
        entrada = resultado('problemas_arbolado', 'retiro_poda', 'obstruccion')
        corregida = ramas.aplicar(entrada, dict(self.lectura, problemas_arbolado='indeterminado'))
        self.assertEqual([p['key'] for p in corregida['problemas']], ['problemas_arbolado', 'retiro_poda'])

    def test_tronco_tapado_conserva_dano_posible(self):
        entrada = resultado('retiro_poda')
        entrada['posibles'].append({'key': 'problemas_arbolado'})
        entrada['en_duda'].append('problemas_arbolado')
        lectura = dict(self.lectura, problemas_arbolado='indeterminado',
                       evidencia='Una banda de texto tapa parte del tronco.')
        corregida = ramas.aplicar(entrada, lectura)
        self.assertIn({'key': 'problemas_arbolado'}, corregida['posibles'])
        self.assertIn('problemas_arbolado', corregida['en_duda'])
        self.assertNotIn('problemas_arbolado', corregida['revision_ramas']['descartadas'])

    def test_pedido_de_extraccion_separado_se_conserva(self):
        entrada = resultado('problemas_arbolado', 'retiro_poda')
        corregida = ramas.aplicar(entrada, self.lectura, 'Rama caída. También pido extraer el árbol seco.')
        self.assertEqual(corregida['categorias_contexto'], [{'codigo': '176011'}])

    def test_fallo_de_revision_se_informa_para_no_cachear(self):
        entrada = resultado('problemas_arbolado', 'retiro_poda')
        entrada['detalle'] = {'verificacion': {}}
        perfil = SimpleNamespace(modo='alto', verificadores=('uno', 'dos', 'tres'))
        with patch('verificador._imagen_data_url', return_value='imagen'), \
                patch('verificador._llamar', return_value=''):
            salida = ramas.revisar(object(), entrada, perfil)
        self.assertEqual(salida['problemas'], entrada['problemas'])
        self.assertTrue(salida['detalle']['verificacion']['revision_ramas_fallo'])

    def test_arbol_entero_o_imagen_dudosa_no_modifican_el_resultado(self):
        entrada = resultado('problemas_arbolado', 'retiro_poda')
        for lectura in (None, {}, dict(self.lectura, rama_desprendida='no'),
                        dict(self.lectura, rama_desprendida='indeterminado')):
            self.assertIs(ramas.aplicar(entrada, lectura), entrada)

    def test_solo_consulta_con_evidencia_visual_de_retiro_y_conflicto(self):
        entrada = resultado('retiro_poda')
        entrada.update(posibles=[], en_duda=[])
        self.assertFalse(ramas.requiere_revision(entrada, ''))
        self.assertTrue(ramas.requiere_revision(entrada, 'árbol caído'))
        self.assertFalse(ramas.requiere_revision(dict(entrada, foto_valida=False), 'árbol caído'))
        self.assertFalse(ramas.requiere_revision(resultado('problemas_arbolado'), 'árbol caído'))
        entrada['problemas'][0]['fuentes'] = ['modelo_local']
        self.assertFalse(ramas.requiere_revision(entrada, 'árbol caído'))
