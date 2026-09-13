import copy
import unittest
import sys
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

    def test_bolson_distingue_no_evaluado_duda_desacuerdo_y_ausencia(self):
        self.assertEqual(H.publicar([])['bolsones']['estado'], 'no_evaluado')
        self.assertEqual(H.publicar([voto('a')])['bolsones']['estado'], 'indeterminado')
        r = H.publicar([voto('a'), voto('b', hay_bolson=True)])['bolsones']
        self.assertEqual(r['estado'], 'contradictorio')
        self.assertIsNone(r['presente'])
        self.assertIsNone(r['servicio'])
        r = H.publicar([voto('a'), voto('b')])['bolsones']
        self.assertIs(r['presente'], False)
        self.assertEqual(r['estado'], 'corroborado')

    def test_exclusion_de_bolson_identifica_politica_y_accion_sin_inferir_material(self):
        alcance = {'estado': 'excluido', 'revisiones': [
            {'modelo': m, 'ubicacion': 'publica', 'presentacion': 'solo_bolson'} for m in ['a', 'b']]}
        r = H.publicar([], alcance)
        self.assertEqual(r['materiales'], [])
        b = r['bolsones']
        self.assertIs(b['presente'], True)
        self.assertEqual(b['jurisdiccion'], 'CABA')
        self.assertEqual(b['politica'], 'bolsones_obra_caba')
        self.assertEqual(b['servicio'], 'retiro_escombros')
        self.assertEqual(b['motivo'], 'presentacion_no_admitida')
        self.assertEqual(b['accion'], 'consultar_servicio')
        self.assertIn('147', b['indicacion'])
        self.assertNotIn('500', b['indicacion'])
        for variante in ['fallo', 'mixto', 'duplicado']:
            a = copy.deepcopy(alcance)
            if variante == 'fallo': a['fallo'] = True
            if variante == 'mixto': a['revisiones'][1]['presentacion'] = 'bolsas_chicas_o_suelto'
            if variante == 'duplicado': a['revisiones'][1]['modelo'] = 'a'
            b = H.publicar([], a)['bolsones']
            self.assertEqual(b['retiro_caba'], 'no_evaluado')
            self.assertIsNone(b['motivo'])
            self.assertIsNone(b['accion'])

    def test_otra_revision_de_foto_se_conserva_ante_presentacion_excluida(self):
        base = {'problemas': [{'key': 'retiro_poda'}], 'predominante': 'retiro_poda',
                'observaciones_higiene': {'bolsones': {'retiro_caba': 'excluido_por_presentacion'}},
                'verificacion_escombros': {'requiere_nueva_foto': True},
                'evaluacion_foto': {}, 'contexto_visual': {}}
        for campo in ['ninguno', 'calidad', 'complementaria', 'contexto']:
            p = copy.deepcopy(base)
            if campo == 'calidad': p['evaluacion_foto']['requiere_nueva_foto'] = True
            if campo == 'complementaria': p['evaluacion_foto']['requiere_foto_complementaria'] = True
            if campo == 'contexto': p['contexto_visual']['suficiente'] = False
            antes = copy.deepcopy(p)
            H.ajustar_presentacion(p)
            self.assertIs(p['verificacion_escombros']['requiere_nueva_foto'], campo != 'ninguno')
            self.assertIs(p['verificacion_escombros']['requiere_cambio_presentacion'], True)
            p['verificacion_escombros'] = antes['verificacion_escombros']
            self.assertEqual(p, antes)
        for retiro in ['no_evaluado', None]:
            p = copy.deepcopy(base)
            p['observaciones_higiene']['bolsones']['retiro_caba'] = retiro
            antes = copy.deepcopy(p)
            H.ajustar_presentacion(p)
            self.assertEqual(p, antes)
        for revision in [None, [], 'sin revisión']:
            p = copy.deepcopy(base)
            p['verificacion_escombros'] = revision
            antes = copy.deepcopy(p)
            H.ajustar_presentacion(p)
            self.assertEqual(p, antes)

    @unittest.skipUnless('servidor' in sys.modules, 'Ejecutar mediante pruebas.py sin cargar pesos')
    def test_publicacion_evalua_contexto_antes_de_ajustar_presentacion(self):
        import servidor as S
        import evaluacion_foto as E
        for suficiente in [True, False]:
            votos = [voto(m, hay_bolson=True) for m in ['a', 'b']]
            for v in votos:
                v['evaluacion_foto'] = E.normalizar({
                    'ambito': 'publica', 'evidencia_ambito': 'Bolsón sobre la vereda',
                    'calidad_suficiente': True, 'motivos_calidad': [],
                    'contexto_suficiente': suficiente,
                    'motivos_contexto': [] if suficiente else ['encuadre_demasiado_cerrado']})
            alcance = {'estado': 'excluido', 'revisiones': [
                {'modelo': m, 'ubicacion': 'publica', 'presentacion': 'solo_bolson'} for m in ['a', 'b']]}
            r = {'problemas': [{'key': 'retiro_poda', 'nombre': 'Poda', 'gravedad': 3, 'fuentes': ['a', 'b']}],
                 'verificacion_escombros': {'estado': 'excluido', 'requiere_nueva_foto': True},
                 'detalle': {'verificacion': {'verificadores': votos, 'alcance_escombros': alcance}}}
            p = S._publica(r)
            self.assertIs(p['contexto_visual']['suficiente'], suficiente)
            self.assertIs(p['verificacion_escombros']['requiere_nueva_foto'], not suficiente)
            self.assertTrue(p['verificacion_escombros']['requiere_cambio_presentacion'])
            self.assertEqual([c['key'] for c in p['problemas']], ['retiro_poda'])
