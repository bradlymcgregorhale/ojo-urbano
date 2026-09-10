"""Regresiones de alcance y contexto. Se ejecutan desde pruebas.py sin pesos."""
import copy
import io
import json
import unittest
from unittest.mock import patch

from PIL import Image
import politica_escombros as P
import verificador as V


def categoria(key=P.KEY, fuentes=None):
    return {"key": key, "nombre": key, "gravedad": 2,
            "fuentes": fuentes if fuentes is not None else ["m1", "m2"]}


def salida(problemas=None):
    return {"problemas": problemas if problemas is not None else [categoria()],
            "categorias_contexto": [], "posibles": [], "descartados_por_foto": [],
            "elementos_detectados": [], "en_duda": [], "descripcion": "escena original",
            "foto_valida": None, "foto_valida_estado": "sin_contexto",
            "detalle": {"modelo_local": {}, "verificacion": {"activa": True}}}


def alcance(**cambios):
    return dict({"estado": "apto", "contexto_resuelve": False, "fallo": False,
                 "afirmacion_explicita": False, "basura_independiente": False,
                 "motivo": "motivo visual"}, **cambios)


def respuesta(**cambios):
    return dict({"ubicacion": "publica", "presentacion": "bolsas_chicas_o_suelto",
                 "hay_bolsas_opacas_o_parciales": "si",
                 "material": "oculto_o_ambiguo", "afirmacion_vecinal": "afirma",
                 "cita_vecinal": "son escombros", "residuos_comunes_independientes": "no",
                 "otros_retiros_independientes": [],
                 "evidencia_ubicacion": "Bolsas en la vereda del lado público",
                 "evidencia_presentacion": "Sacos chicos separados del bolsón grande",
                 "evidencia_material": "Contenido de las bolsas opaco"}, **cambios)


class PoliticaTest(unittest.TestCase):
    cats = {P.KEY: {"nombre": "Retiro de escombros"}}

    def aplicar(self, r, **cambios):
        return P.aplicar(r, alcance(**cambios), self.cats)

    def test_publico_conserva_confirmacion_visual_y_no_muta(self):
        r = salida()
        antes = copy.deepcopy(r)
        nuevo = self.aplicar(r)
        self.assertEqual(nuevo["problemas"], r["problemas"])
        self.assertEqual(r, antes)

    def bolsas_con_un_lector(self):
        r = salida([categoria('recoleccion')])
        r['detalle']['modelo_local']['probabilidades'] = [
            {'key': P.KEY, 'score': .9991}, {'key': 'recoleccion', 'score': .4096}]
        revisiones = [dict(respuesta(afirmacion_vecinal='no_menciona',
            cita_vecinal='', material=material, evidencia_material=evidencia), modelo=modelo)
            for modelo, material, evidencia in [
                ('m1', 'escombros_visible', 'Se ve un cascote en la abertura.'),
                ('m2', 'oculto_o_ambiguo', 'No se distingue el contenido.'),
                ('m3', 'oculto_o_ambiguo', 'Las bolsas ocultan el material.')]]
        return r, alcance(revisiones=revisiones)

    def test_un_lector_conserva_escombros_y_recoleccion_pendientes_sin_inventar_votos(self):
        r, revision = self.bolsas_con_un_lector()
        antes, votos = copy.deepcopy(r), copy.deepcopy(revision)
        nuevo = P.aplicar(r, revision, self.cats)
        self.assertFalse(nuevo['hay_problema'])
        self.assertFalse(nuevo['hay_reclamo'])
        self.assertIsNone(nuevo['gravedad_maxima'])
        self.assertTrue(nuevo['verificacion_escombros']['requiere_revision'])
        posibles = {c['key']: c for c in nuevo['posibles']}
        self.assertEqual(set(posibles), {P.KEY, 'recoleccion'})
        self.assertEqual(posibles[P.KEY]['fuentes'], ['m1'])
        self.assertEqual(posibles['recoleccion']['fuentes'], ['m1', 'm2'])
        self.assertTrue(all(c['gravedad'] is None for c in posibles.values()))
        self.assertEqual(set(nuevo['en_duda']), set(posibles))
        decision = nuevo['detalle']['verificacion']['decision_alcance']
        self.assertEqual(decision['reglas'], ['escombros_visibles_sin_corroborar',
                                            'recoleccion_pendiente_por_material'])
        self.assertEqual({e['key'] for e in decision['efectos']}, set(posibles))
        self.assertEqual(nuevo['detalle']['verificacion']['alcance_escombros'], votos)
        self.assertEqual(r, antes)
        self.assertEqual(revision, votos)

    def test_un_lector_conserva_basura_independiente_y_otros_retiros(self):
        for basura in (False, True):
            r, revision = self.bolsas_con_un_lector()
            revision['basura_independiente'] = basura
            r['problemas'] += [categoria('retiro_poda'), categoria('retiro_muebles')]
            nuevo = P.aplicar(r, revision, self.cats)
            esperados = {'retiro_poda', 'retiro_muebles'} | ({'recoleccion'} if basura else set())
            self.assertEqual({c['key'] for c in nuevo['problemas']}, esperados)
            self.assertTrue(nuevo['hay_reclamo'])
            self.assertIn(P.KEY, nuevo['en_duda'])

    def test_un_lector_no_reabre_escombros_ya_confirmados(self):
        r, revision = self.bolsas_con_un_lector()
        r['problemas'].append(categoria())
        nuevo = P.aplicar(r, revision, self.cats)
        self.assertEqual(nuevo['problemas'], r['problemas'])
        self.assertNotIn('escombros_visibles_sin_corroborar',
                         nuevo['detalle']['verificacion']['decision_alcance']['reglas'])

    def test_un_lector_respeta_rechazos_previos_y_exige_un_solo_positivo(self):
        for variante in ('descartado', 'arbitro', 'dos_positivos', 'bolsas_indeterminadas',
                         'material_indeterminado'):
            r, revision = self.bolsas_con_un_lector()
            if variante == 'descartado':
                r['descartados_por_foto'] = [categoria()]
            elif variante == 'arbitro':
                r['posibles'] = [dict(categoria(), arbitro='rechazar')]
            elif variante == 'dos_positivos':
                revision['revisiones'][1]['material'] = 'escombros_visible'
            elif variante == 'bolsas_indeterminadas':
                revision['revisiones'][1]['hay_bolsas_opacas_o_parciales'] = 'indeterminado'
            else:
                revision['revisiones'][1]['material'] = 'indeterminado'
            with self.subTest(variante=variante):
                self.assertIsNone(P._escombros_visibles_sin_corroborar(r, revision))

    def test_un_lector_explica_movimiento_de_alias(self):
        r, revision = self.bolsas_con_un_lector()
        r['posibles'] = [dict(categoria('recoleccion_restos_obra'), codigo=P.CODIGO)]
        nuevo = P.aplicar(r, revision, self.cats)
        efectos = nuevo['detalle']['verificacion']['decision_alcance']['efectos']
        efecto = next(e for e in efectos if e['key'] == 'recoleccion_restos_obra')
        self.assertEqual(efecto['reglas'], ['escombros_visibles_sin_corroborar'])

    def test_revision_posterior_recupera_sospecha_local_rechazada_sin_respaldo_visual(self):
        r, revision = self.bolsas_con_un_lector()
        r['posibles'] = [dict(categoria(fuentes=['modelo_local']), arbitro='rechazar',
                             motivo='La primera pasada no identifica material de obra.')]
        r['detalle']['verificacion']['arbitro'] = {'decisiones': [
            {'key': P.KEY, 'veredicto': 'rechazar'}]}
        anterior = copy.deepcopy(r)
        nuevo = P.aplicar(r, revision, self.cats)
        self.assertFalse(nuevo['problemas'])
        candidato = next(c for c in nuevo['posibles'] if c['key'] == P.KEY)
        self.assertEqual(candidato['fuentes'], ['m1'])
        self.assertIsNone(candidato['arbitro'])
        self.assertTrue(nuevo['verificacion_escombros']['requiere_revision'])
        self.assertEqual(nuevo['detalle']['verificacion']['arbitro'],
                         anterior['detalle']['verificacion']['arbitro'])
        self.assertEqual(r, anterior)

    def test_un_lector_respeta_vetos_y_exige_respaldo_local_y_visual(self):
        variantes = [({'fallo': True}, None), ({'estado': 'excluido'}, None),
            ({'material_contradictorio': True}, None), ({'afirmacion_explicita': True}, None),
            ({'contexto_resuelve': True}, None)]
        for campo, valor in [('ubicacion', 'privada'), ('presentacion', 'solo_bolson'),
                ('hay_bolsas_opacas_o_parciales', 'no'), ('afirmacion_vecinal', 'afirma'),
                ('material', 'incompatible_visible'), ('material', 'oculto_o_ambiguo'),
                ('evidencia_material', '')]:
            variantes.append(({}, (campo, valor)))
        for cambios, campo in variantes:
            r, revision = self.bolsas_con_un_lector()
            revision.update(cambios)
            if campo:
                revision['revisiones'][0][campo[0]] = campo[1]
            with self.subTest(cambios=cambios, campo=campo):
                nuevo = P.aplicar(r, revision, self.cats)
                self.assertNotIn('escombros_visibles_sin_corroborar',
                                 nuevo['detalle']['verificacion']['decision_alcance']['reglas'])
        for variante in ('local_bajo', 'sin_local', 'foto_invalida', 'un_modelo', 'duplicado'):
            r, revision = self.bolsas_con_un_lector()
            if variante == 'local_bajo':
                r['detalle']['modelo_local']['probabilidades'][0]['score'] = .94
            elif variante == 'sin_local':
                r['detalle']['modelo_local'] = {}
            elif variante == 'foto_invalida':
                r['foto_valida'] = False
            elif variante == 'un_modelo':
                revision['revisiones'] = revision['revisiones'][:1]
            else:
                revision['revisiones'][1]['modelo'] = 'm1'
            with self.subTest(variante=variante):
                self.assertIsNone(P._escombros_visibles_sin_corroborar(r, revision))

    def revision_cartones(self):
        return [dict(respuesta(afirmacion_vecinal='no_menciona',
                               material='incompatible_visible', hay_bolsas_opacas_o_parciales='no'), modelo='m1'),
                dict(respuesta(afirmacion_vecinal='no_menciona', presentacion='sin_pila',
                               material='indeterminado', hay_bolsas_opacas_o_parciales='indeterminado'), modelo='m2'),
                dict(respuesta(afirmacion_vecinal='no_menciona', presentacion='sin_pila',
                               material='indeterminado', hay_bolsas_opacas_o_parciales='no'), modelo='m3')]

    def cartones(self, revisiones=None, **cambios):
        r = salida([categoria('recoleccion')])
        r['posibles'] = [categoria(fuentes=['modelo_local'])]
        return self.aplicar(r, estado='indeterminado', material_contradictorio=True,
                            revisiones=revisiones if revisiones is not None else self.revision_cartones(), **cambios)

    def test_cartones_publicos_confirmados_no_desaparecen_al_descartar_escombros(self):
        r = self.cartones()
        self.assertEqual([c['key'] for c in r['problemas']], ['recoleccion'])
        self.assertTrue(r['hay_problema'])
        self.assertIn('residuos comunes', r['descripcion'])
        self.assertFalse(any(c['key'] == 'recoleccion' for c in r['descartados_por_foto']))

    def conflicto_cajas(self, **cambios):
        revisiones = [dict(respuesta(
            afirmacion_vecinal='no_menciona', hay_bolsas_opacas_o_parciales='no',
            material=material), modelo=modelo)
            for modelo, material in [('m1', 'escombros_visible'),
                                      ('m2', 'escombros_visible'),
                                      ('m3', 'incompatible_visible')]]
        revision = alcance(material_contradictorio=True,
                           material_visible_confirmado=True, revisiones=revisiones)
        revision.update(cambios)
        return revision

    def test_cajas_con_material_disputado_requieren_revision_sin_confirmar_servicio(self):
        r = salida([categoria('recoleccion')])
        r['posibles'] = [categoria(fuentes=['modelo_local'])]
        antes = copy.deepcopy(r)
        nuevo = P.aplicar(r, self.conflicto_cajas(), self.cats)
        self.assertFalse(nuevo['hay_problema'])
        self.assertFalse(nuevo['hay_reclamo'])
        self.assertTrue(nuevo['verificacion_escombros'].get('requiere_revision'))
        self.assertEqual({c['key'] for c in nuevo['posibles']}, {'recoleccion', P.KEY})
        rec = next(c for c in nuevo['posibles'] if c['key'] == 'recoleccion')
        self.assertEqual(rec['fuentes'], ['m1', 'm2'])
        self.assertIsNone(rec['gravedad'])
        self.assertNotIn('recoleccion', [c['key'] for c in nuevo['descartados_por_foto']])
        self.assertEqual(nuevo['en_duda'].count('recoleccion'), 1)
        self.assertIn('revisión', nuevo['descripcion'])
        self.assertNotIn('bolsas', nuevo['descripcion'])
        self.assertEqual(r, antes)

    def test_conflicto_no_confirma_ni_revisa_local_solo_y_respeta_vetos(self):
        variantes = [({'fallo': True}, None), ({'afirmacion_explicita': True}, None),
                     ({'estado': 'excluido'}, None), ({'material_visible_confirmado': False}, None)]
        for campo, valor in [('ubicacion', 'privada'), ('presentacion', 'solo_bolson'),
                             ('hay_bolsas_opacas_o_parciales', 'si'),
                             ('hay_bolsas_opacas_o_parciales', 'indeterminado'),
                             ('afirmacion_vecinal', 'afirma')]:
            variantes.append(({}, (campo, valor)))
        for cambios, campo in variantes:
            revision = self.conflicto_cajas(**cambios)
            if campo:
                revision['revisiones'][0][campo[0]] = campo[1]
            r = salida([categoria('recoleccion')])
            r['posibles'] = [categoria(fuentes=['modelo_local'])]
            with self.subTest(cambios=cambios, campo=campo):
                nuevo = P.aplicar(r, revision, self.cats)
                self.assertFalse(nuevo['problemas'])
                self.assertFalse(nuevo['verificacion_escombros'].get('requiere_revision'))
        r = salida([categoria('recoleccion', fuentes=['modelo_local'])])
        r['posibles'] = [categoria(fuentes=['modelo_local'])]
        self.assertFalse(P.aplicar(r, self.conflicto_cajas(), self.cats)
                         ['verificacion_escombros'].get('requiere_revision'))

    def test_conflicto_preserva_otros_problemas_y_no_restaura_foto_invalida(self):
        r = salida([categoria('recoleccion'), categoria('barrido')])
        r['posibles'] = [categoria(fuentes=['modelo_local'])]
        nuevo = P.aplicar(r, self.conflicto_cajas(), self.cats)
        self.assertEqual([c['key'] for c in nuevo['problemas']], ['barrido'])
        self.assertTrue(nuevo['hay_problema'])
        self.assertTrue(nuevo['verificacion_escombros'].get('requiere_revision'))
        r['foto_valida'] = False
        self.assertFalse(P.aplicar(r, self.conflicto_cajas(), self.cats)
                         ['verificacion_escombros'].get('requiere_revision'))

    def test_conflicto_no_demueve_basura_independiente_ya_confirmada(self):
        r = salida([categoria('recoleccion')])
        r['posibles'] = [categoria(fuentes=['modelo_local'])]
        nuevo = P.aplicar(r, self.conflicto_cajas(basura_independiente=True), self.cats)
        self.assertEqual([c['key'] for c in nuevo['problemas']], ['recoleccion'])
        self.assertNotIn('recoleccion', [c['key'] for c in nuevo['posibles']])
        self.assertFalse(nuevo['verificacion_escombros'].get('requiere_revision'))

    def test_no_extiende_excepcion_de_cartones_a_bolsas_privados_o_testimonio(self):
        for field, value in [('ubicacion', 'privada'), ('ubicacion', 'indeterminada'),
                             ('presentacion', 'solo_bolson'), ('material', 'oculto_o_ambiguo'),
                             ('material', 'escombros_visible'), ('afirmacion_vecinal', 'afirma'),
                             ('hay_bolsas_opacas_o_parciales', 'si')]:
            reviews = self.revision_cartones()
            reviews[0][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(self.cartones(reviews)['problemas'])
        for updates in [{'fallo': True}, {'afirmacion_explicita': True}]:
            with self.subTest(updates=updates):
                self.assertFalse(self.cartones(**updates)['problemas'])

    def test_cartones_no_promueve_recoleccion_ausente_o_solo_local(self):
        for problems in [[], [categoria('recoleccion', fuentes=['modelo_local'])]]:
            r = salida(problems)
            r['posibles'] = [categoria(fuentes=['modelo_local'])]
            out = self.aplicar(r, estado='indeterminado', material_contradictorio=True,
                               revisiones=self.revision_cartones())
            self.assertFalse(out['problemas'])

    def fusion_recoleccion(self):
        rec = categoria('recoleccion')
        r = salida([dict(categoria(), reclasificado_por='modelo_local')])
        r['detalle']['verificacion']['confirmadas'] = [copy.deepcopy(rec)]
        r['posibles'] = [dict(rec, origen='foto')]
        return r

    def test_restaura_basura_visible_tras_rechazar_reclasificacion_local(self):
        r = self.fusion_recoleccion()
        antes = copy.deepcopy(r)
        nuevo = self.aplicar(r, material_contradictorio=True,
                            revisiones=self.revision_cartones())
        self.assertEqual(nuevo['problemas'], [categoria('recoleccion')])
        self.assertFalse(any(c['key'] == 'recoleccion' for c in nuevo['posibles']))
        self.assertIn('residuos comunes', nuevo['descripcion'])
        self.assertEqual(r, antes)

    def test_no_restaura_fusion_con_bolsas_ocultas_o_revision_incompleta(self):
        for updates in [{'fallo': True}, {'afirmacion_explicita': True},
                        {'material_contradictorio': False}, {'estado': 'excluido'},
                        {'revisiones': [dict(respuesta(), modelo='m1'),
                                        dict(respuesta(), modelo='m2')]}]:
            review = dict(material_contradictorio=True, revisiones=self.revision_cartones())
            review.update(updates)
            with self.subTest(updates=updates):
                nuevo = self.aplicar(self.fusion_recoleccion(), **review)
                self.assertNotIn('recoleccion', [c['key'] for c in nuevo['problemas']])

    def test_no_resucita_rechazos_ajenos_a_fusion_ni_inventa_fuentes(self):
        for variant in ('sin_fusion', 'sin_confirmacion', 'sin_posible', 'solo_local'):
            r = self.fusion_recoleccion()
            if variant == 'sin_fusion':
                r['problemas'][0].pop('reclasificado_por')
            elif variant == 'sin_confirmacion':
                r['detalle']['verificacion']['confirmadas'] = []
            elif variant == 'sin_posible':
                r['posibles'] = []
            else:
                r['detalle']['verificacion']['confirmadas'][0]['fuentes'] = ['modelo_local']
            with self.subTest(variant=variant):
                nuevo = self.aplicar(r, material_contradictorio=True,
                                    revisiones=self.revision_cartones())
                self.assertFalse(nuevo['problemas'])

    def test_privado_y_bolson_no_se_aceptan(self):
        for razon in ("privada", "solo_bolson"):
            with self.subTest(razon=razon):
                r = self.aplicar(salida(), estado="excluido", motivo=razon)
                self.assertFalse(r["hay_reclamo"])
                self.assertEqual(r["posibles"][0]["arbitro"], "rechazar")
                self.assertTrue(r["verificacion_escombros"]["requiere_nueva_foto"])

    def test_indeterminado_demueve_aceptado(self):
        r = self.aplicar(salida(), estado="indeterminado", fallo=True)
        self.assertFalse(r["hay_problema"])
        self.assertIn(P.KEY, r["en_duda"])

    def test_contexto_resuelve_sin_inventar_votos(self):
        r = self.aplicar(salida([categoria("recoleccion")]), contexto_resuelve=True,
                         afirmacion_explicita=True)
        self.assertEqual([c["key"] for c in r["problemas"]], [P.KEY])
        self.assertEqual(r["problemas"][0]["fuentes"], ["contexto_vecinal"])
        self.assertEqual(r["problemas"][0]["origen"], "contexto_vecinal")
        self.assertIn("El vecino informa", r["descripcion"])

    def test_sin_afirmacion_no_promueve_ambiguas(self):
        r = self.aplicar(salida([categoria("recoleccion")]))
        self.assertEqual([c["key"] for c in r["problemas"]], ["recoleccion"])

    def test_basura_independiente_se_conserva(self):
        for estado in ("apto", "excluido"):
            with self.subTest(estado=estado):
                r = self.aplicar(salida([categoria(), categoria("recoleccion")]),
                                 estado=estado, contexto_resuelve=True,
                                 basura_independiente=True)
                self.assertIn("recoleccion", [c["key"] for c in r["problemas"]])

    def test_privado_no_remapea_mismas_bolsas_a_basura(self):
        r = self.aplicar(salida([categoria(), categoria("recoleccion")]), estado="excluido")
        self.assertEqual(r["problemas"], [])

    def test_otros_reclamos_no_cambian(self):
        r = salida([categoria(), categoria("barrido")])
        r["categorias_contexto"] = [categoria("arbolado")]
        nuevo = self.aplicar(r, estado="excluido")
        self.assertEqual([c["key"] for c in nuevo["problemas"]], ["barrido"])
        self.assertEqual(nuevo["categorias_contexto"], r["categorias_contexto"])

    def test_codigo_y_alias_no_evaden_veto(self):
        for c in ({"codigo": P.CODIGO}, categoria("recoleccion_restos_obra")):
            with self.subTest(c=c):
                r = salida([dict(c, fuentes=["contexto_vecinal"])])
                r["categorias_contexto"] = [c]
                self.assertTrue(P.requiere_revision(r, ""))
                self.assertFalse(self.aplicar(r, estado="excluido")["hay_reclamo"])

    def test_solo_texto_no_evita_contradiccion(self):
        r = self.aplicar(salida([categoria(fuentes=["contexto_vecinal"])]))
        self.assertFalse(r["hay_problema"])
        self.assertIn("no resuelve", r["posibles"][0]["motivo"])

    def test_contradiccion_retira_confirmacion_anterior(self):
        r = self.aplicar(salida(), material_contradictorio=True)
        self.assertFalse(r["hay_problema"])
        self.assertIn("contradice", r["descripcion"])

    def test_incertidumbre_no_deja_camion_comun(self):
        r = self.aplicar(salida([categoria(), categoria("recoleccion")]), estado="indeterminado")
        self.assertEqual(r["problemas"], [])

    def test_no_retiro_alternativo_de_misma_pila_privada(self):
        r = self.aplicar(salida([categoria(), categoria("retiro_muebles"), categoria("retiro_poda")]),
                         estado="excluido")
        self.assertEqual(r["problemas"], [])

    def test_otros_retiros_publicos_independientes_se_conservan(self):
        r = self.aplicar(salida([categoria(), categoria("retiro_muebles")]), estado="excluido",
                         otros_retiros_independientes=["retiro_muebles"])
        self.assertEqual([c["key"] for c in r["problemas"]], ["retiro_muebles"])

    def test_fallback_textual_tambien_tiene_procedencia(self):
        r = self.aplicar(salida([categoria(fuentes=["contexto_vecinal"])]), contexto_resuelve=True)
        self.assertEqual(r["problemas"][0]["origen"], "contexto_vecinal")

    def test_bolsas_ocultas_no_multiplican_testimonio_vecinal(self):
        original = salida([categoria(fuentes=["m1", "m2", "modelo_local"])])
        r = self.aplicar(original, contexto_resuelve=True)
        self.assertEqual(r["problemas"][0]["fuentes"], ["contexto_vecinal"])
        self.assertTrue(r["verificacion_escombros"]["basado_en_contexto"])
        self.assertEqual(original["problemas"][0]["fuentes"], ["m1", "m2", "modelo_local"])

    def test_material_realmente_visible_conserva_fuentes(self):
        r = self.aplicar(salida(), contexto_resuelve=True, material_visible_confirmado=True)
        self.assertEqual(r["problemas"][0]["fuentes"], ["m1", "m2"])
        self.assertFalse(r["verificacion_escombros"]["basado_en_contexto"])

    def test_no_crea_duda_fantasma(self):
        r = salida([categoria("barrido")])
        r["posibles"] = [categoria("arbolado")]
        nuevo = self.aplicar(r, estado="indeterminado")
        self.assertNotIn(P.KEY, nuevo["en_duda"])
        self.assertEqual(nuevo["descripcion"], r["descripcion"])

    def test_no_corrige_foto_invalida_por_otro_reclamo(self):
        r = salida([categoria("recoleccion")])
        r["foto_valida"], r["foto_valida_estado"] = False, "no_corresponde"
        r["categorias_contexto"] = [categoria("barrido")]
        nuevo = self.aplicar(r, contexto_resuelve=True)
        self.assertIs(nuevo["foto_valida"], False)

    def test_disparadores_limitados_y_sin_verificacion(self):
        r = salida([])
        self.assertFalse(P.requiere_revision(r, None))
        self.assertTrue(P.requiere_revision(r, "son cascotes de obra"))
        r["detalle"]["modelo_local"] = {"probabilidades": [{"key": P.KEY, "score": .7}]}
        self.assertTrue(P.requiere_revision(r, ""))
        r["detalle"]["verificacion"]["activa"] = False
        self.assertFalse(P.requiere_revision(r, "escombros"))


class RevisionTest(unittest.TestCase):
    def revisar(self, respuestas, contexto="Estas bolsas son escombros"):
        self.llamadas = []

        def llamar(modelo, mensajes, **opciones):
            self.llamadas.append((modelo, mensajes, opciones))
            r = respuestas[int(modelo[-1]) - 1]
            if isinstance(r, Exception):
                raise r
            return json.dumps(r)

        with patch.object(V, "VERIFICADORES", ["m1", "m2", "m3"]), patch.object(V, "_llamar", llamar):
            return V.validar_alcance_escombros(Image.new("RGB", (64, 64)), contexto)

    def test_contexto_explicito_compatible(self):
        r = self.revisar([respuesta()] * 3)
        self.assertTrue(r["contexto_resuelve"])
        self.assertEqual(r["estado"], "apto")
        self.assertNotIn("cita_vecinal", json.dumps(r))
        for _, mensajes, opciones in self.llamadas:
            self.assertEqual(opciones["etapa"], "alcance_escombros")
            self.assertEqual(opciones["max_tokens"], 1000)
            self.assertEqual(mensajes[0]["role"], "system")
            self.assertNotIn("modelo_local", json.dumps(mensajes))

    def test_privado_bolson_y_sin_pila(self):
        for cambio in ({"ubicacion": "privada"}, {"presentacion": "solo_bolson"},
                       {"presentacion": "sin_pila", "hay_bolsas_opacas_o_parciales": "no"}):
            with self.subTest(cambio=cambio):
                r = self.revisar([respuesta(**cambio)] * 3)
                self.assertEqual(r["estado"], "excluido")
                self.assertFalse(r["contexto_resuelve"])

    def test_ausencia_de_pila_con_bolsas_opacas_no_veta_dos_testigos(self):
        r = self.revisar([respuesta(), respuesta(), respuesta(presentacion='sin_pila')])
        self.assertEqual(r['estado'], 'apto')
        self.assertEqual(r['revisiones'][2]['presentacion'], 'indeterminada')
        self.assertEqual(r['revisiones'][2]['presentacion_original'], 'sin_pila')
        # Una ausencia consistente sigue impidiendo confirmar la pila.
        r = self.revisar([respuesta(), respuesta(), respuesta(presentacion='sin_pila',
                                                             hay_bolsas_opacas_o_parciales='no')])
        self.assertEqual(r['estado'], 'indeterminado')
        # Tres contradicciones no se convierten en tres positivos.
        r = self.revisar([respuesta(presentacion='sin_pila')] * 3)
        self.assertEqual(r['estado'], 'indeterminado')
        r = self.revisar([respuesta(ubicacion='privada', presentacion='sin_pila')] * 3)
        self.assertEqual(r['estado'], 'excluido')

    def test_conflicto_de_ubicacion_abstiene(self):
        r = self.revisar([respuesta(), respuesta(), respuesta(ubicacion="privada")])
        self.assertEqual(r["estado"], "indeterminado")
        self.assertFalse(r["contexto_resuelve"])

    def test_dos_votos_y_un_fallo_no_se_cachean(self):
        r = self.revisar([respuesta(), respuesta(), RuntimeError("sin red")])
        self.assertEqual(r["estado"], "apto")
        self.assertTrue(r["fallo"])

    def test_un_solo_voto_no_alcanza(self):
        r = self.revisar([respuesta(), {}, []])
        self.assertEqual(r["estado"], "indeterminado")
        self.assertTrue(r["fallo"])

    def test_respuesta_incompleta_sin_evidencia_no_cuenta(self):
        for cambio in ({"ubicacion": "pública"}, {"evidencia_material": ""}, {"material": True}):
            with self.subTest(cambio=cambio):
                r = self.revisar([respuesta(**cambio)] * 3)
                self.assertEqual(r["estado"], "indeterminado")

    def test_cita_inventada_no_es_contexto(self):
        r = self.revisar([respuesta()] * 3, "Por favor retiren las bolsas")
        self.assertFalse(r["contexto_resuelve"])
        self.assertFalse(r["afirmacion_explicita"])

    def test_negacion_duda_y_orden_no_promueven(self):
        for tipo in ("niega", "duda", "no_menciona"):
            with self.subTest(tipo=tipo):
                r = self.revisar([respuesta(afirmacion_vecinal=tipo)] * 3)
                self.assertFalse(r["contexto_resuelve"])

    def test_contenido_visible_incompatible_veta_contexto(self):
        r = self.revisar([respuesta(), respuesta(), respuesta(
            material="incompatible_visible", hay_bolsas_opacas_o_parciales="no")])
        self.assertFalse(r["contexto_resuelve"])

    def test_carton_visible_no_desmiente_bolsas_opacas(self):
        r = self.revisar([respuesta(material="incompatible_visible")] * 3)
        self.assertFalse(r["material_contradictorio"])
        self.assertTrue(r["contexto_resuelve"])

    def test_modelos_duplicados_no_suman_votos(self):
        with patch.object(V, "VERIFICADORES", ["m1", "m1", "m1"]), patch.object(
                V, "_llamar", return_value=json.dumps(respuesta())) as llamada:
            r = V.validar_alcance_escombros(Image.new("RGB", (64, 64)), "son escombros")
        self.assertEqual(llamada.call_count, 1)
        self.assertEqual(r["estado"], "indeterminado")


class PipelineTest(unittest.TestCase):
    @patch.object(V, "ARBITRO", "test/arbiter")
    @patch.object(V, "VERIFICADORES", ["test/vision-1", "test/vision-2"])
    def test_descripcion_no_niega_material_confirmado(self):
        import servidor as S
        r = salida()
        r["descripcion"] = ("Hay dos bolsas junto al contenedor. Las bolsas son residuos "
                            "comunes y no muestran señales visibles de escombros.")
        pub = S._publica(r)
        self.assertFalse(V._niega_escombros(pub["descripcion"]))
        self.assertIn("escombros", pub["descripcion"])
        self.assertEqual(pub["problemas"][0]["key"], P.KEY)
        r["problemas"] = [categoria("recoleccion")]
        self.assertEqual(S._publica(r)["descripcion"], r["descripcion"])

    def test_negacion_material_no_confunde_otras_negaciones(self):
        for texto in ["No se ven escombros.", "Sin señales visibles de cascotes.",
                      "Los escombros no se distinguen."]:
            self.assertTrue(V._niega_escombros(texto), texto)
        for texto in ["Hay escombros sin daños en el contenedor.",
                      "No hay daños; hay escombros.", "Son escombros, no basura.",
                      "No es basura sino escombros."]:
            self.assertFalse(V._niega_escombros(texto), texto)

    def procesar(self, revision, foto_valida=None, texto=False, fusion=False,
                 obra=False, obra_aceptada=False, reco_local=0,
                 revision_material=None, poda=False, adjudicado=False, veri_extra=None,
                 score_mixto=.99):
        import servidor as S
        buf = io.BytesIO()
        Image.new("RGB", (64, 64)).save(buf, format="JPEG")
        r = salida([categoria(fusion if isinstance(fusion, str) else "recoleccion") if fusion else categoria()])
        veri = dict(r["detalle"]["verificacion"], confirmadas=r["problemas"], en_duda=[],
                    categorias_contexto=[], descripcion="una escena", foto_valida=foto_valida,
                    por_contexto=[categoria(fuentes=["contexto_vecinal"])] if texto else [])
        if obra:
            entrada = {"codigo": P.CODIGO_OBRA_SERVICIOS, "nombre": "Restos de obra en vereda",
                       "fuentes": 1, "de": 3, "gravedad": 2}
            veri["categorias_contexto"] = [entrada]
            if texto:
                veri["por_contexto"] = [dict(entrada, fuentes=["contexto_vecinal"])]
        if poda:
            veri["confirmadas"].append(categoria("retiro_poda"))
        if adjudicado:
            veri["adjudicadas_dirigidas"] = [P.KEY]
        if veri_extra:
            veri.update(copy.deepcopy(veri_extra))
        local = {"probabilidades": [{"key": P.KEY, "score": 1}, {"key": "recoleccion", "score": reco_local}],
                 "predichas": [], "top5": [], "gravedad": {"value": 2},
                 "revision_material": revision_material, "escombros_mixtos": score_mixto}
        def verificar(*args):
            V._costo_sumar({"cost": .01})
            return veri

        def revisar(*args):
            V._costo_sumar({"cost": .003})
            return revision

        with patch.object(V, "disponible", return_value=True), patch.object(V, "verificar", side_effect=verificar), \
                patch.object(V, "validar_alcance_escombros", side_effect=revisar) as guardia, \
                patch.object(V, "validar_contexto_obra_servicios", return_value={"aceptado": obra_aceptada, "fallo": False}), \
                patch.object(S, "clasificar_local", return_value=local), patch.object(S, "_hay_cuota", return_value=True), \
                patch.object(S, "FUSION_ESCOMBROS", True):
            result = S.procesar(buf.getvalue(), "son escombros", "1")
        guardia.assert_called_once()
        return result, S._publica(result)

    def test_confirmar_saco_de_cemento_no_confirma_basura_independiente(self):
        for objeto, evidencia, conserva in [
                ("un saco de cemento", "Dos bolsas cerradas", False),
                ("una bolsa de basura común", "Dos bolsas cerradas", True),
                ("un saco de cemento", "Dos bolsas y cartón en el piso", True)]:
            with self.subTest(objeto=objeto, evidencia=evidencia):
                veri = {"confirmadas": [categoria("recoleccion", ["m1", "m3"]), categoria()],
                        "verificadores": [
                            {"modelo": "m1", "categorias": [{"key": P.KEY}]},
                            {"modelo": "m2", "categorias": [{"key": P.KEY}]},
                            {"modelo": "m3", "categorias": [
                                {"key": "recoleccion", "evidencia": evidencia}]}],
                        "repreguntas": [{"key": "recoleccion", "respuestas": [
                            {"modelo": "m1", "veredicto": "presente", "que_es": objeto}]}]}
                _, pub = self.procesar(alcance(), reco_local=.99, veri_extra=veri)
                keys = {c["key"] for c in pub["problemas"]}
                self.assertIn(P.KEY, keys)
                self.assertEqual("recoleccion" in keys, conserva)

    def test_carton_mixto_necesita_dos_lecturas_y_descarte_en_piso(self):
        a = {"modelo": "m1", "veredicto": "identificado",
             "que_es": "cajas de cartón y mueble desmontado",
             "ubicacion": "en la vereda", "evidencia": "paneles de cartón junto al mueble"}
        b = dict(a, modelo="m2")
        self.assertTrue(V._carton_mixto_corroborado([a, b], False))
        self.assertFalse(V._carton_mixto_corroborado([a, b], True))
        self.assertFalse(V._carton_mixto_corroborado([a, a], False))
        for cambio in [{"que_es": "un mueble de cartón"},
                       {"que_es": "madera y muebles, no cartón"},
                       {"ubicacion": "dentro del contenedor, frente a la vereda"},
                       {"evidencia": "cartón envolviendo un mueble en uso"},
                       {"veredicto": "no_identificable"}, {"ubicacion": ""}]:
            self.assertFalse(V._carton_mixto_corroborado([a, dict(b, **cambio)], False), cambio)

    def test_pila_mixta_exige_pesos_revisados_y_respeta_vetos(self):
        casos = [
            ({}, True),
            ({"revision_material": None}, False),
            ({"revision_material": "otra-revision"}, False),
            ({"score_mixto": None}, False),
            ({"score_mixto": .94}, False),
            ({"reco_local": .64}, False),
            ({"poda": True}, False),
            ({"adjudicado": True}, False),
        ]
        for cambios, esperado in casos:
            with self.subTest(cambios=cambios):
                opciones = dict(fusion=True, reco_local=.99,
                                revision_material="escombros-preservacion-20260906")
                opciones.update(cambios)
                _, pub = self.procesar(alcance(), **opciones)
                keys = {c["key"] for c in pub["problemas"]}
                self.assertEqual(P.KEY in keys, esperado)
                self.assertIn("recoleccion", keys)

    def test_pila_mixta_no_evade_exclusion_de_espacio_privado(self):
        _, pub = self.procesar(alcance(estado="excluido"), fusion=True,
                               reco_local=.99, revision_material="escombros-preservacion-20260906")
        self.assertNotIn(P.KEY, {c["key"] for c in pub["problemas"]})

    def test_fusion_local_no_restaurara_privados(self):
        _, pub = self.procesar(alcance(estado="excluido"), fusion=True)
        self.assertEqual(pub["problemas"], [])
        self.assertFalse(pub["hay_reclamo"])

    def test_fallback_textual_no_restaurara_privados(self):
        _, pub = self.procesar(alcance(estado="excluido"), foto_valida=False, texto=True)
        self.assertEqual(pub["problemas"], [])

    def test_fusion_desde_muebles_no_evita_exclusion(self):
        _, pub = self.procesar(alcance(estado="excluido"), fusion="retiro_muebles")
        self.assertEqual(pub["problemas"], [])

    def test_costo_incluye_revision_final(self):
        r, pub = self.procesar(alcance())
        self.assertEqual(r["costo_api"], .013)
        self.assertEqual(pub["costo_api"], .013)

    def test_fallback_contextual_apto_tiene_una_fuente(self):
        _, pub = self.procesar(alcance(contexto_resuelve=True, afirmacion_explicita=True),
                               foto_valida=False, texto=True)
        self.assertEqual(pub["problemas"][0]["fuentes"], 1)
        self.assertEqual(pub["problemas"][0]["confianza"], "baja")

    def test_obra_no_declarada_no_restaura_reclamo_tras_exclusion(self):
        _, pub = self.procesar(alcance(estado="excluido"), foto_valida=False, texto=True, obra=True)
        self.assertFalse(pub["hay_reclamo"])
        self.assertEqual(pub["categorias_contexto"], [])

    def test_catalogo_con_conteo_entero_se_serializa_tras_rechazo(self):
        _, pub = self.procesar(alcance(estado="excluido"), obra=True)
        self.assertFalse(pub["hay_reclamo"])
        entrada = next(c for c in pub["posibles"] if c.get("codigo") == P.CODIGO_OBRA_SERVICIOS)
        self.assertEqual(entrada["fuentes"], 1)

    def test_obra_independiente_declarada_sobrevive_exclusion(self):
        _, pub = self.procesar(alcance(estado="excluido"), foto_valida=False, texto=True,
                               obra=True, obra_aceptada=True)
        self.assertTrue(pub["hay_reclamo"])
        self.assertEqual(pub["problemas"][0]["codigo"], P.CODIGO_OBRA_SERVICIOS)

    def test_fallo_final_no_se_cachea(self):
        import servidor as S
        r, pub = self.procesar(alcance(estado="indeterminado", fallo=True))
        self.assertFalse(S._cacheable(r))
        self.assertFalse(pub["hay_problema"])

    def test_contexto_publico_confianza_baja_y_sin_doble_camion(self):
        import servidor as S
        r = P.aplicar(salida([categoria("recoleccion")]),
                      alcance(contexto_resuelve=True, afirmacion_explicita=True), PoliticaTest.cats)
        pub = S._publica(r)
        self.assertEqual(pub["predominante"], P.KEY)
        self.assertEqual(len(pub["problemas"]), 1)
        self.assertEqual(pub["problemas"][0]["confianza"], "baja")
        self.assertEqual(pub["problemas"][0]["fuentes"], 1)
        self.assertNotIn("detalle", pub)


class ObraServiciosTest(unittest.TestCase):
    def test_no_repite_categoria_descartada_en_descripcion_mixta(self):
        r = salida([dict(categoria(), origen="contexto_vecinal"),
                    {"codigo": P.CODIGO_OBRA_SERVICIOS, "nombre": "Restos de obra en vereda que impiden el paso"}])
        r["descripcion"] = P.DESCRIPCION_CONTEXTUAL + " Otros hallazgos: Restos de obra en vereda que impiden el paso."
        nuevo = P.aplicar_obra_servicios(r, {"aceptado": False, "estado": "rechazado"})
        self.assertIn(P.DESCRIPCION_CONTEXTUAL, nuevo["descripcion"])
        self.assertNotIn("Otros hallazgos: Restos de obra", nuevo["descripcion"])

    def test_duda_no_es_rechazo_definitivo(self):
        with patch.object(V, "VERIFICADORES", ["m1", "m2", "m3"]), patch.object(
                V, "_llamar", return_value='{"declarada":"duda","cita":""}'):
            revision = V.validar_contexto_obra_servicios("Quizás sea una obra de servicios")
        self.assertEqual(revision["estado"], "indeterminado")
        r = salida([])
        r["categorias_contexto"] = [{"codigo": P.CODIGO_OBRA_SERVICIOS, "fuentes": 1, "de": 3}]
        nuevo = P.aplicar_obra_servicios(r, revision)
        self.assertIsNone(nuevo["posibles"][0]["arbitro"])

    def test_no_es_alias_del_retiro(self):
        self.assertFalse(P.es_escombros({"codigo": P.CODIGO_OBRA_SERVICIOS}))
        self.assertFalse(P.requiere_obra_servicios(salida()))

    def test_se_dispara_en_cualquiera_de_los_campos(self):
        for campo in ("problemas", "categorias_contexto", "posibles"):
            r = salida([])
            r[campo] = [{"codigo": P.CODIGO_OBRA_SERVICIOS}]
            self.assertTrue(P.requiere_obra_servicios(r))

    def test_fallo_no_cachea_y_no_acepta_catalogo(self):
        import servidor as S
        r = salida([])
        r["categorias_contexto"] = [{"codigo": P.CODIGO_OBRA_SERVICIOS, "fuentes": ["contexto_vecinal"]}]
        r = P.aplicar_obra_servicios(r, {"aceptado": False, "fallo": True})
        self.assertFalse(S._cacheable(r))
        self.assertFalse(r["hay_reclamo"])

    def test_no_toca_otros_reclamos(self):
        r = salida([categoria("barrido")])
        r["categorias_contexto"] = [{"codigo": P.CODIGO_OBRA_SERVICIOS}, {"codigo": "otro"}]
        nuevo = P.aplicar_obra_servicios(r, {"aceptado": False})
        self.assertEqual(nuevo["problemas"], r["problemas"])
        self.assertEqual(nuevo["categorias_contexto"], [{"codigo": "otro"}])

    def test_exige_consenso_y_cita_del_comentario(self):
        texto = "La empresa de agua dejó escombros de su obra que impiden pasar por la vereda."
        for declarada, cita, esperado in (("si", texto, True), ("si", "frase inventada", False),
                                         ("no", "", False), ("duda", "", False)):
            with self.subTest(declarada=declarada, cita=cita), patch.object(
                    V, "VERIFICADORES", ["m1", "m2", "m3"]), patch.object(
                    V, "_llamar", return_value=json.dumps({"declarada": declarada, "cita": cita})) as llamada:
                r = V.validar_contexto_obra_servicios(texto)
                self.assertEqual(r["aceptado"], esperado)
                self.assertNotIn("cita", json.dumps(r))
                self.assertEqual(llamada.call_args.kwargs["etapa"], "obra_servicios_contexto")

    def test_respuesta_malformada_y_modelos_duplicados_no_aceptan(self):
        with patch.object(V, "VERIFICADORES", ["m1", "m1"]), patch.object(
                V, "_llamar", return_value='{"declarada":"si","cita":"obra"}'):
            self.assertFalse(V.validar_contexto_obra_servicios("obra")["aceptado"])
        with patch.object(V, "_llamar", return_value='{}'):
            r = V.validar_contexto_obra_servicios("obra")
            self.assertFalse(r["aceptado"])
            self.assertTrue(r["fallo"])
