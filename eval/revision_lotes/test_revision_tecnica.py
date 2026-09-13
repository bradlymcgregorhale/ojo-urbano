"""Revisiones técnicas sintéticas, sin fotos privadas ni inferencia (#46, #52)."""
import copy
import json
import unittest

import regresiones as R
import test_regresiones as fixtures


class TecnicaTest(unittest.TestCase):
    write = fixtures.RegistroTest.write

    def setUp(self):
        fixtures.RegistroTest.setUp(self)
        imagenes = self.base / 'entrega/entradas-api'
        imagenes.mkdir()
        self.imagen = imagenes / 'H0001.jpg'
        self.imagen.write_bytes(b'imagen sintetica para validar identidad')
        digest = R.huella(self.imagen.read_bytes())
        self.foto['sha256_api'] = digest
        self.write(self.base / 'entrega/manifest.json', {'fotos': [self.foto]})
        self.write(self.base / 'manifest-privado.json',
                   {'fotos': [dict(self.foto, particion='desarrollo')]})
        self.original['entrada_api']['sha256'] = digest
        self.original['resultado'].update(
            evaluacion_foto={'calidad_suficiente': True, 'estado_calidad': 'evaluado'},
            contexto_visual={'suficiente': True, 'estado': 'evaluado'})
        self.write(self.a / 'H0001-alto.json', self.original)
        self.revision['original_huella'] = R.huella((self.a / 'H0001-alto.json').read_bytes())
        self.write(self.chat / 'H0001.json', self.revision)
        casos = [{'foto': 'H0001', 'sha256_foto': digest}]
        dataset = R.huella(json.dumps(casos, sort_keys=True).encode())
        self.manifest = {'casos': casos, 'dataset_id': dataset, 'particion': 'desarrollo'}
        self.mp = self.root / 'manifest-tecnico.json'
        self.write(self.mp, self.manifest)
        self.tecnica = {'tipo': 'ojo-urbano-calidad-contexto-v1', 'version': 1,
            'dataset_id': dataset, 'exportada_en': '2026-09-13T10:00:00Z',
            'alcance': 'Solo calidad y contexto.', 'borradores_pendientes': [],
            'revisiones': {'H0001': {'foto': 'H0001', 'sha256_foto': digest,
                'calidad': 'suficiente', 'contexto': 'sin_revisar', 'notas': '',
                'fecha': '2026-09-13T09:00:00Z',
                'accion': 'Confirmación humana explícita de calidad y contexto'}}}
        self.tp = self.root / 'tecnica.json'
        self.write(self.tp, self.tecnica)

    def crear(self, paths=None):
        return R.crear(self.base, self.reg, revisiones_tecnicas=paths or [self.tp],
                       manifest_tecnico=self.mp)

    def test_parcial_conserva_original_exportacion_y_categorias(self):
        registro = self.crear()
        banco = R.cargar(registro)
        caso = banco['casos'][0]
        etiquetas, conflictos = R.etiquetas(caso)
        self.assertEqual(etiquetas, {'categorias.retiro_poda': 'confirmado',
                                    'evaluacion_foto.calidad_suficiente': True})
        self.assertFalse(conflictos)
        self.assertEqual(R.obtener(self.reg, caso['original']), self.original)
        self.assertIn(R.huella(self.tp.read_bytes()), banco['fuentes'])
        self.assertIn(R.huella(self.mp.read_bytes()), banco['fuentes'])
        self.assertEqual(self.crear(), registro)

    def test_sin_revisar_e_indeterminado_no_son_negativos(self):
        self.tecnica['revisiones']['H0001'].update(calidad='indeterminado')
        self.write(self.tp, self.tecnica)
        etiquetas, _ = R.etiquetas(R.cargar(self.crear())['casos'][0])
        self.assertEqual(etiquetas, {'categorias.retiro_poda': 'confirmado'})

    def test_sin_revisar_no_anula_confirmacion_tecnica_anterior(self):
        self.revision['calidad_humana'] = 'suficiente'
        self.write(self.chat / 'H0001.json', self.revision)
        self.tecnica['revisiones']['H0001']['calidad'] = 'sin_revisar'
        self.write(self.tp, self.tecnica)
        etiquetas, conflictos = R.etiquetas(R.cargar(self.crear())['casos'][0])
        self.assertIs(etiquetas['evaluacion_foto.calidad_suficiente'], True)
        self.assertFalse(conflictos)

    def test_repetir_exportacion_no_duplica_anotaciones(self):
        registro = self.crear()
        self.assertEqual(self.crear([self.tp, self.tp]), registro)

    def test_sin_opcion_tecnica_preserva_alcance_del_importador_anterior(self):
        self.revision.update(contexto_visual_suficiente=False, calidad_humana='insuficiente')
        self.write(self.chat / 'H0001.json', self.revision)
        registro = R.crear(self.base, self.reg)
        etiquetas, conflictos = R.etiquetas(R.cargar(registro)['casos'][0])
        self.assertEqual(etiquetas, {'categorias.retiro_poda': 'confirmado'})
        self.assertFalse(conflictos)

    def test_borrador_no_importa_etiquetas_y_conserva_guardado_anterior(self):
        self.tecnica['borradores_pendientes'] = ['H0001']
        self.write(self.tp, self.tecnica)
        etiquetas, _ = R.etiquetas(R.cargar(self.crear())['casos'][0])
        self.assertIs(etiquetas['evaluacion_foto.calidad_suficiente'], True)
        self.tecnica['revisiones'] = {}
        self.write(self.tp, self.tecnica)
        etiquetas, _ = R.etiquetas(R.cargar(self.crear())['casos'][0])
        self.assertNotIn('evaluacion_foto.calidad_suficiente', etiquetas)

    def test_exportaciones_contradictorias_no_eligen_la_mas_reciente(self):
        anterior = self.crear()
        bytes_antes = anterior.read_bytes()
        nueva = copy.deepcopy(self.tecnica)
        nueva['revisiones']['H0001']['calidad'] = 'insuficiente'
        otro = self.root / 'otra.json'
        self.write(otro, nueva)
        registro = self.crear([self.tp, otro])
        self.assertEqual(anterior.read_bytes(), bytes_antes)
        self.assertNotEqual(anterior, registro)
        self.assertEqual(R.comparar(registro)['conflictos'][0]['campos'],
                         ['evaluacion_foto.calidad_suficiente'])

    def test_contradiccion_con_marca_tecnica_anterior_se_conserva(self):
        self.revision.update(contexto_visual_suficiente=False, calidad_humana='insuficiente')
        self.write(self.chat / 'H0001.json', self.revision)
        self.tecnica['revisiones']['H0001']['contexto'] = 'suficiente'
        self.write(self.tp, self.tecnica)
        r = R.comparar(self.crear())
        self.assertEqual(set(r['conflictos'][0]['campos']),
                         {'contexto_visual.suficiente', 'evaluacion_foto.calidad_suficiente'})

    def test_prosa_sobre_calidad_no_se_convierte_en_etiqueta(self):
        self.revision['calidad_humana'] = 'mala_por_sombra_y_contraluz'
        self.write(self.chat / 'H0001.json', self.revision)
        self.tecnica['revisiones']['H0001']['calidad'] = 'sin_revisar'
        self.write(self.tp, self.tecnica)
        etiquetas, conflictos = R.etiquetas(R.cargar(self.crear())['casos'][0])
        self.assertNotIn('evaluacion_foto.calidad_suficiente', etiquetas)
        self.assertFalse(conflictos)

    def test_rechaza_identidades_y_esquemas_incompatibles(self):
        cambios = [lambda e: e.update(version=True),
                   lambda e: e.update(revisiones=[]),
                   lambda e: e.update(exportada_en='2026-09-13'),
                   lambda e: e.update(dataset_id='otro'),
                   lambda e: e.update(categorias={'retiro_poda': 'confirmado'}),
                   lambda e: e['revisiones']['H0001'].update(sha256_foto='c' * 64),
                   lambda e: e['revisiones']['H0001'].update(foto='H0002'),
                   lambda e: e['revisiones']['H0001'].update(calidad=False),
                   lambda e: e['revisiones']['H0001'].update(contexto={}),
                   lambda e: e['revisiones']['H0001'].update(accion='borrador'),
                   lambda e: e['revisiones']['H0001'].update(notas=None),
                   lambda e: e['revisiones']['H0001'].update(fecha='ayer'),
                   lambda e: e['revisiones']['H0001'].update(fecha='2026-09-13'),
                   lambda e: e['revisiones']['H0001'].update(ambito='interior'),
                   lambda e: e.update(borradores_pendientes=['H0002'])]
        for numero, cambio in enumerate(cambios):
            with self.subTest(caso=numero):
                export = copy.deepcopy(self.tecnica)
                cambio(export)
                self.write(self.tp, export)
                with self.assertRaises(ValueError):
                    self.crear()

    def test_rechaza_claves_duplicadas(self):
        raw = self.tp.read_text().replace('"version": 1', '"version": 2, "version": 1')
        self.tp.write_text(raw)
        with self.assertRaises(ValueError):
            self.crear()

    def test_rechaza_foto_alterada_y_particion_reservada(self):
        original = self.imagen.read_bytes()
        self.imagen.write_bytes(b'otra imagen')
        with self.assertRaises(ValueError):
            self.crear()
        self.imagen.write_bytes(original)
        self.write(self.base / 'manifest-privado.json',
                   {'fotos': [dict(self.foto, particion='evaluacion_reservada')]})
        with self.assertRaises(ValueError):
            self.crear()

    def test_rechaza_manifest_alterado_duplicado_o_ausente(self):
        with self.assertRaises(ValueError):
            R.crear(self.base, self.reg, revisiones_tecnicas=[self.tp])
        with self.assertRaises(ValueError):
            R.crear(self.base, self.reg, manifest_tecnico=self.mp)
        for cambio in ('hash', 'duplicado'):
            manifest = copy.deepcopy(self.manifest)
            if cambio == 'hash':
                manifest['dataset_id'] = 'otro'
            else:
                manifest['casos'] *= 2
            self.write(self.mp, manifest)
            with self.assertRaises(ValueError):
                self.crear()

    def test_compara_rechazo_nuevo_y_contexto_sin_confundir_categorias(self):
        self.tecnica['revisiones']['H0001']['contexto'] = 'suficiente'
        self.write(self.tp, self.tecnica)
        registro = self.crear()
        nueva = copy.deepcopy(self.original)
        nueva['resultado']['modo_version'] = 'candidata'
        nueva['resultado']['evaluacion_foto']['calidad_suficiente'] = False
        self.write(self.a / 'H0001-alto.json', nueva)
        r = R.comparar(registro, self.a)
        self.assertEqual(r['conteos']['regresion'], 1)
        self.assertEqual(r['conteos']['acierto_conservado'], 2)
        self.assertFalse(r['proteccion_de_aciertos_comprobada'])

    def test_estado_ausente_indeterminado_y_booleano_falso(self):
        for campo, grupo, clave, estado in [
                ('evaluacion_foto.calidad_suficiente', 'evaluacion_foto',
                 'calidad_suficiente', 'estado_calidad'),
                ('contexto_visual.suficiente', 'contexto_visual', 'suficiente', 'estado')]:
            for value in (None, 0, 1, 'false', {}, []):
                r = copy.deepcopy(self.original['resultado'])
                r[grupo][clave] = value
                self.assertIsNone(R.observado(r, campo))
            r[grupo] = {clave: False, estado: 'evaluado'}
            self.assertIs(R.observado(r, campo), False)
            r[grupo][estado] = 'indeterminado'
            self.assertIsNone(R.observado(r, campo))

    def test_abstencion_nueva_impide_aprobar_preservacion(self):
        registro = self.crear()
        nueva = copy.deepcopy(self.original)
        nueva['resultado']['modo_version'] = 'candidata'
        nueva['resultado']['evaluacion_foto'].update(
            calidad_suficiente=None, estado_calidad='indeterminado')
        self.write(self.a / 'H0001-alto.json', nueva)
        r = R.comparar(registro, self.a)
        self.assertFalse(r['proteccion_de_aciertos_comprobada'])
        self.assertEqual(r['faltantes'][0]['campo'], 'evaluacion_foto.calidad_suficiente')


if __name__ == '__main__':
    unittest.main()
