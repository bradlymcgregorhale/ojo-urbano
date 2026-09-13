import copy
import unittest
import observaciones_higiene as H


def voto(m, **cambios):
    base = {'hay_bolson': False, 'solo_limpieza_cotidiana_frente': True, 'materiales': [
        {'material': 'hojas', 'ubicacion': 'vereda_frente_inmueble', 'presentacion': 'disperso',
         'cantidad_relativa': 'aislado', 'evidencia': 'Hojas caídas separadas en la vereda'}]}
    base.update(cambios)
    return {'modelo': m, 'ok': True, 'observaciones_higiene': H.normalizar(base)}


class ObservacionesHigieneTest(unittest.TestCase):
    def test_hojas_con_acuerdo_orienta_sin_inventar_responsable_personal(self):
        votos = [voto('a'), voto('b')]; copia = copy.deepcopy(votos)
        r = H.publicar(votos)
        self.assertEqual(r['orientacion_limpieza']['responsable_orientativo'], 'frentista')
        self.assertIn('No los barras a la calzada', r['orientacion_limpieza']['indicacion'])
        self.assertEqual(votos, copia)

    def test_una_fuente_duplicados_ausencia_y_error_no_producen_consenso(self):
        for votos in [[voto('a')], [voto('a')]*2, [voto('a'), {'modelo':'b','ok':False}], []]:
            r = H.publicar(votos)
            self.assertIsNone(r['bolsones']['presente'])
            self.assertEqual(r['orientacion_limpieza']['estado'], 'indeterminada')

    def test_bolsas_cantidad_y_ubicacion_no_reciben_exclusion_de_barrido(self):
        for campo, valor in [('presentacion','bolsa'), ('cantidad_relativa','significativa'),
                             ('ubicacion','calzada'), ('material','piedras')]:
            votos = [voto('a'), voto('b')]
            for v in votos:
                v['observaciones_higiene']['materiales'][0]['material'] = 'papel_carton'
                v['observaciones_higiene']['materiales'][0][campo] = valor
            r = H.publicar(votos)
            self.assertEqual(r['orientacion_limpieza']['estado'], 'indeterminada')

    def test_hojas_abundantes_no_son_basura_domiciliaria_pero_poda_confirmada_se_preserva(self):
        votos = [voto('a'), voto('b')]
        for v in votos:
            v['observaciones_higiene']['materiales'][0].update(presentacion='acumulado', cantidad_relativa='significativa')
        self.assertEqual(H.publicar(votos)['orientacion_limpieza']['responsable_orientativo'], 'frentista')
        self.assertEqual(H.publicar(votos, problemas=[{'key':'retiro_poda'}])['orientacion_limpieza']['estado'], 'indeterminada')

    def test_excrementos_no_se_atribuyen_al_frentista(self):
        votos = [voto('a'), voto('b')]
        for v in votos: v['observaciones_higiene']['materiales'][0]['material'] = 'excrementos'
        self.assertEqual(H.publicar(votos)['orientacion_limpieza']['responsable_orientativo'], 'responsable_del_animal')

    def test_bolson_no_excluye_por_presencia_sin_alcance(self):
        r = H.publicar([voto('a', hay_bolson=True), voto('b', hay_bolson=True)])
        self.assertTrue(r['bolsones']['presente'])
        self.assertEqual(r['bolsones']['retiro_caba'], 'no_evaluado')
        alcance = {'estado':'excluido','revisiones':[{'modelo':m,'ubicacion':'publica','presentacion':'solo_bolson'} for m in ['a','b']]}
        self.assertEqual(H.publicar([], alcance)['bolsones']['retiro_caba'], 'excluido_por_presentacion')
        self.assertEqual(H.publicar([], alcance, rechazada=True)['materiales'], [])
        conflicto = H.publicar([voto('a'), voto('b')], alcance)['bolsones']
        self.assertIsNone(conflicto['presente'])
        self.assertEqual(conflicto['estado'], 'contradictorio')
        self.assertEqual(conflicto['retiro_caba'], 'no_evaluado')

    def test_valores_invalidos_no_crean_observaciones(self):
        self.assertIsNone(H.normalizar({'materiales':['piedras']}))
        self.assertIsNone(H.normalizar({'materiales':[{}]}))
        self.assertIsNone(H.normalizar({'materiales':[]} )['hay_bolson'])
