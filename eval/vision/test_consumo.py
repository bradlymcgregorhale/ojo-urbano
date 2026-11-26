import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from eval.vision.consumo import conciliar


def caso(id_foto, pedidos, **datos):
    return {
        "id": id_foto, "modo": "prueba", "modo_version": "version-de-prueba",
        "entrada": "ojo_urbano", "pedidos": pedidos,
        "esperado": {"recoleccion": True, "retiro_poda": None},
        "etiquetas": ["recoleccion"], **datos,
    }


def uso(pedido, generacion, costo, **datos):
    return {
        "evento": "openrouter_uso", "pedido": pedido, "id": generacion, "cost": costo,
        "etapa": "prueba_decision", "intento": 1, **datos,
    }


def recibo(generacion, costo):
    return {"data": {"id": generacion, "total_cost": costo}}


def caso_con_recuento(esperado, problema, revision=None, **datos):
    respuesta = {"modo": "prueba", "modo_version": "version-de-prueba",
                 "problemas": [problema], "verificacion_escombros": revision,
                 "consumo_api": {"pedido": "web", "intentos": 1}, **datos}
    return {"id": "a", "entrada": "ojo_urbano", "modo": respuesta["modo"],
            "modo_version": respuesta["modo_version"], "esperado": {"retiro_escombros": True},
            "recuento_esperado": esperado, "respuestas": [respuesta]}


class PruebaConsumo(unittest.TestCase):
    def test_extrae_los_pedidos_y_las_etiquetas_de_los_dos_recorridos(self):
        base = {"id": "a", "modo": "prueba", "modo_version": "v", "esperado": {"recoleccion": True}}
        metadatos = {"modo": "prueba", "modo_version": "v"}
        cola = metadatos | {"estado": "en_cola", "consumo_api": {"pedido": "web", "intentos": None}}
        clasificacion = metadatos | {"problemas": [{"key": "recoleccion"}]}
        web = clasificacion | {"consumo_api": {"pedido": "web", "intentos": 2}}
        bot = clasificacion | {"consumo_api": {"pedido": "bot", "intentos": 1}}
        eventos = [uso("web", "1", .007), uso("web", "2", .002), uso("bot", "3", .0091)]
        casos = [
            base | {"entrada": "ojo_urbano", "respuestas": [cola, {"estado": "listo", "resultado": web}]},
            base | {"entrada": "bacollab", "respuestas": [None, {"detail": "respuesta interrumpida"}, bot]},
        ]
        resumen, (primero, segundo) = conciliar(casos, eventos)
        self.assertEqual(resumen["casos_conciliados"], 1)
        self.assertEqual(resumen["casos_con_consumo_pendiente"], 1)
        self.assertEqual(resumen["casos_con_diferencias"], 0)
        self.assertEqual(primero["costo_conocido_usd"], "0.009")
        self.assertEqual(segundo["costo_conocido_usd"], "0.0091")
        self.assertEqual(segundo["respuestas_sin_traza"], 2)
        self.assertFalse(segundo["conciliado"])

    def test_un_caso_sin_verificacion_o_con_otro_modo_no_se_puntua(self):
        metadatos = {"modo": "prueba", "modo_version": "v"}
        referencia = metadatos | {"id": "a", "entrada": "ojo_urbano", "esperado": {"recoleccion": True}}
        salida = metadatos | {"problemas": [], "verificacion_activa": False,
                             "consumo_api": {"pedido": "web", "intentos": 0}}
        [detalle] = conciliar([referencia | {"respuestas": [salida]}], [])[1]
        self.assertTrue(detalle["conciliado"])
        self.assertIsNone(detalle["diferencias"])
        with self.assertRaisesRegex(ValueError, "otro modo o versión"):
            conciliar([referencia | {"respuestas": [salida | {"modo_version": "otra"}]}], [])

    def test_presupuesto_y_etiquetas_se_puntuan_por_separado(self):
        casos = [
            caso("correcta", {"p1": 2, "p2": 1}),
            caso("diferente", {"p3": 1}, entrada="bacollab", etiquetas=["retiro_poda"]),
            caso("pendiente", {"p4": 2, "p5": None}, etiquetas=None),
        ]
        apoyo = uso("p1", "g2", .0012, etapa="prueba_contenedores", finish_reason="length")
        eventos = [
            uso("p1", "g1", .007), apoyo, apoyo,
            uso("p2", "g3", .0019, error="RespuestaNoUtilizableError"),
            uso("p3", "g4", .01), uso("p4", "g5", .006),
            uso("p4", None, None, intento=2, error="TimeoutError"),
            uso("p5", "g6", 0), uso("fuera-del-banco", "g7", .7),
        ]
        resumen, detalles = conciliar(casos, eventos)
        self.assertEqual(resumen, {
            "casos": 3, "casos_conciliados": 2, "casos_con_consumo_pendiente": 1,
            "casos_sobre_el_tope": 1, "casos_sin_puntuar": 1, "casos_con_diferencias": 1,
            "diferencias_de_etiqueta": 1, "maximo_conciliado_usd": "0.0101",
        })
        self.assertEqual(detalles[0]["costo_conocido_usd"], "0.0101")
        self.assertEqual(detalles[0]["diferencias"], [])
        self.assertEqual(detalles[1]["diferencias"], ["recoleccion"])
        self.assertFalse(detalles[1]["supera_tope"])
        self.assertFalse(detalles[2]["conciliado"])
        self.assertIsNone(detalles[2]["diferencias"])

    def test_recuento_con_diferencias_aunque_acierten_la_categoria_y_el_costo(self):
        escombros = {"key": "retiro_escombros"}
        for esperado, publicado in (({"bolsas": 0}, escombros | {"bolsas": None}),
                                    ({"bolsas": None}, escombros),
                                    ({"bolsas": 0}, escombros | {"bolsas": False})):
            with self.subTest(esperado=esperado, publicado=publicado):
                resumen, [foto] = conciliar([caso_con_recuento(esperado, publicado)], [uso("web", "g", .009)])
                self.assertTrue(foto["conciliado"])
                self.assertEqual(foto["diferencias"], [])
                self.assertEqual(foto["diferencias_recuento"], ["bolsas"])
                self.assertEqual(resumen["casos_con_diferencias_recuento"], 1)

    def test_recuento_acepta_rangos_y_cantidad_nula_con_minimo_corrobado(self):
        problema = {"key": "retiro_escombros", "bolsas": None}
        revision = {"bolsas_minimo": 13, "bolsas_recuento_completo": False}
        for intervalo, diferencias in (([5, None], []), ([12, 15], []), ([14, None], ["bolsas_minimo"])):
            with self.subTest(intervalo=intervalo):
                esperado = {"bolsas": None, "bolsas_minimo": intervalo, "bolsas_recuento_completo": False}
                [foto] = conciliar([caso_con_recuento(esperado, problema, revision)], [uso("web", "g", .009)])[1]
                self.assertEqual(foto["diferencias_recuento"], diferencias)
                self.assertEqual(foto["recuento_observado"], revision | {"bolsas": None})

    def test_recuento_exige_dos_fuentes_si_la_referencia_lo_pide(self):
        problema = {"key": "retiro_escombros", "bolsas": None}
        esperado = {"bolsas_minimo": 5, "bolsas_fuentes": 2}
        for campos, diferencias in (({}, ["bolsas_fuentes"]), ({"bolsas_fuentes": 1}, ["bolsas_fuentes"]),
                                    ({"bolsas_fuentes": 2}, [])):
            with self.subTest(campos=campos):
                revision = {"bolsas_minimo": 5, "bolsas_recuento_completo": False, **campos}
                [foto] = conciliar([caso_con_recuento(esperado, problema, revision)], [uso("web", "g", .009)])[1]
                self.assertEqual(foto["diferencias_recuento"], diferencias)
                self.assertEqual(foto["diferencias"], [])

    def test_recuento_sin_analisis_verificado_no_se_puntua(self):
        problema = {"key": "retiro_escombros", "bolsas": None}
        datos = caso_con_recuento({"bolsas": None}, problema, verificacion_activa=False)
        resumen, [foto] = conciliar([datos], [uso("web", "g", .009)])
        self.assertIsNone(foto["diferencias_recuento"])
        self.assertIsNone(foto["recuento_observado"])
        self.assertEqual(resumen["casos_sin_puntuar_recuento"], 1)

    def test_respaldos_y_servicios_se_puntuan_aparte_del_costo_y_las_etiquetas(self):
        esperado = {"categorias_contexto": {"retiro_muebles": "neutral", "ocupacion_comercial": None},
                    "solicitudes_sugeridas": ["reparacion_vereda", "barrido"], "reclamo_foto_estado": None}
        categorias = [{"key": "retiro_muebles", "respaldo_visual": "neutral"},
                      {"key": "reparacion_vereda", "respaldo_visual": "compatible"}]
        respuesta = {
            "modo": "prueba", "modo_version": "v", "analisis_estado": "completo",
            "problemas": [{"key": "retiro_muebles"}, {"key": "reparacion_vereda"}],
            "categorias_contexto": categorias,
            "solicitudes_sugeridas": {"servicios": ["barrido", "reparacion_vereda"]},
            "consumo_api": {"pedido": "web", "intentos": 1},
        }
        base = {"id": "a", "entrada": "trabajos", "modo": "prueba", "modo_version": "v",
                "esperado": {"retiro_muebles": True, "reparacion_vereda": True}}
        alternativa = respuesta | {
            "categorias_contexto": [categorias[0] | {"respaldo_visual": "compatible"}, categorias[1]],
            "solicitudes_sugeridas": {"servicios": ["retiro_muebles"]},
            "reclamo_foto": {"estado": "no_corresponde"},
        }
        for resultado, diferencias in (
            (respuesta, []),
            (alternativa, ["categorias_contexto.retiro_muebles", "solicitudes_sugeridas", "reclamo_foto_estado"]),
            (respuesta | {"analisis_estado": "parcial"}, None),
            (respuesta | {"categorias_contexto": [categorias[0] | {"respaldo_visual": None}]}, None),
        ):
            with self.subTest(diferencias=diferencias):
                caso = base | {"contexto_esperado": esperado, "respuestas": [{"resultado": resultado}]}
                resumen, [foto] = conciliar([caso], [uso("web", "g", .009)])
                self.assertEqual(foto["diferencias"], [])
                self.assertEqual(foto["costo_conocido_usd"], "0.009")
                self.assertEqual(foto["diferencias_contexto"], diferencias)
                self.assertEqual(resumen["casos_sin_puntuar_contexto"], int(diferencias is None))
                self.assertEqual(resumen["casos_con_diferencias_contexto"], int(bool(diferencias)))
        parcial = {"categorias_contexto": {"ocupacion_comercial": None}}
        _, [foto] = conciliar([base | {"contexto_esperado": parcial, "respuestas": [alternativa]}],
                              [uso("web", "g", .009)])
        self.assertEqual(foto["contexto_observado"], parcial)
        self.assertEqual(foto["diferencias_contexto"], [])
        with self.assertRaisesRegex(ValueError, "respaldos válidos"):
            conciliar([base | {"contexto_esperado": {"categorias_contexto": {"barrido": True}},
                              "respuestas": [respuesta]}], [uso("web", "g", .009)])

    def test_un_importe_conocido_puede_superar_el_tope_aunque_falten_cargos(self):
        [foto] = conciliar([caso("a", {"p": 2})], [uso("p", "g", .010000001)])[1]
        self.assertFalse(foto["conciliado"])
        self.assertTrue(foto["supera_tope"])

    def test_conserva_el_minimo_sin_id_y_no_duplica_una_copia_identificada(self):
        casos = [caso("a", {"p": 2})]
        decision = uso("p", "g", .009, modelo="laboratorio/decision")
        apoyo = uso("p", None, .0012, modelo="laboratorio/retiros", etapa="prueba_retiros")
        resumen, [foto] = conciliar(casos, [apoyo, decision, apoyo])
        self.assertEqual(foto["costo_conocido_usd"], "0.0102")
        self.assertEqual(foto["pedidos"][0]["costo_sin_generacion_usd"], "0.0012")
        self.assertEqual(foto["pedidos"][0]["generaciones_con_importe"], 1)
        self.assertFalse(foto["conciliado"])
        self.assertTrue(foto["supera_tope"])
        self.assertEqual(resumen["casos_sobre_el_tope"], 1)

        copia = apoyo | {"id": "g2", "cost": None}
        for eventos in ([apoyo, decision, copia], [copia, apoyo, decision]):
            [foto] = conciliar(casos, eventos, [recibo("g2", .0012)])[1]
            self.assertEqual(foto["costo_conocido_usd"], "0.0102")
            self.assertNotIn("costo_sin_generacion_usd", foto["pedidos"][0])
            self.assertEqual(foto["pedidos"][0]["generaciones_con_recibo"], 1)
            self.assertFalse(foto["conciliado"])

    def test_no_suma_dos_cargos_sin_id_que_pueden_ser_el_mismo_intento(self):
        casos = [caso("a", {"p": 2})]
        apoyo = uso("p", None, .0012, modelo="laboratorio/retiros", etapa="prueba_retiros")
        [foto] = conciliar(casos, [apoyo, apoyo | {"cost": .0013}])[1]
        self.assertEqual(foto["costo_conocido_usd"], "0.0013")
        [foto] = conciliar(casos, [apoyo, uso("p", "legado", .0012)])[1]
        self.assertEqual(foto["costo_conocido_usd"], "0.0012")
        self.assertFalse(foto["conciliado"])

    def test_recibo_completa_el_cargo_de_una_respuesta_fallida(self):
        casos = [caso("a", {"p": 2})]
        eventos = [uso("p", "decision", .009),
                   uso("p", "apoyo", None, etapa="prueba_retiros", finish_reason="length")]
        facturas = [recibo("decision", .009), recibo("apoyo", .00101), recibo("apoyo", .00101),
                    recibo("fuera-del-banco", .008)]
        [antes] = conciliar(casos, eventos)[1]
        self.assertFalse(antes["conciliado"])
        resumen, [foto] = conciliar(casos, eventos, facturas)
        self.assertTrue(foto["conciliado"])
        self.assertTrue(foto["supera_tope"])
        self.assertEqual(resumen["maximo_conciliado_usd"], "0.01001")
        self.assertEqual(foto["pedidos"][0]["generaciones_con_recibo"], 2)

    def test_recibo_sin_vinculo_no_cubre_un_intento_sin_identificador(self):
        casos = [caso("a", {"p": 1})]
        eventos = [uso("p", None, None, error="TimeoutError")]
        [foto] = conciliar(casos, eventos, [recibo("sin-vinculo", .002)])[1]
        self.assertFalse(foto["conciliado"])
        self.assertEqual(foto["costo_conocido_usd"], "0")
        self.assertEqual(foto["pedidos"][0]["generaciones_con_recibo"], 0)

    def test_rechaza_importes_de_recibo_incompatibles(self):
        casos = [caso("a", {"p": 1})]
        eventos = [uso("p", "g", .008)]
        with self.assertRaisesRegex(ValueError, "difiere del registro"):
            conciliar(casos, eventos, [recibo("g", .009)])
        with self.assertRaisesRegex(ValueError, "recibos con importes distintos"):
            conciliar(casos, eventos, [recibo("g", .008), recibo("g", .009)])

    def test_descarta_importes_invalidos_y_detecta_dos_cargos_para_la_misma_generacion(self):
        casos = [caso("a", {"p": 1, "q": 1})]
        for costo in (None, -1, True, "0.01", float("inf"), float("nan")):
            with self.subTest(costo=costo):
                [foto] = conciliar(casos, [uso("p", "g", costo)])[1]
                self.assertEqual(foto["costo_conocido_usd"], "0")
                self.assertFalse(foto["conciliado"])
        for incompatible in (uso("p", "g", .004), uso("q", "g", .005)):
            with self.assertRaises(ValueError):
                conciliar(casos, [uso("p", "g", .005), incompatible])

    def test_cli_conserva_la_precision_y_no_sobrescribe_el_detalle(self):
        with tempfile.TemporaryDirectory() as raiz:
            base = Path(raiz)
            manifiesto, log, detalle = (base / n for n in ("casos.json", "uso.log", "detalle.json"))
            manifiesto.write_text(json.dumps({"casos": [caso("foto-privada", {"p": 1})]}))
            linea = json.dumps(uso("p", "g", None)).replace('"cost": null', '"cost": 0.010000000000000001')
            log.write_text("INFO: listo\n" + linea + "\n")
            cmd = [sys.executable, "-m", "eval.vision.consumo",
                   "--casos", str(manifiesto), "--uso", str(log), "--detalle", str(detalle)]
            salida = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(salida.returncode, 1, salida.stderr)
            self.assertNotIn("foto-privada", salida.stdout)
            self.assertEqual(json.loads(salida.stdout)["casos_sobre_el_tope"], 1)
            self.assertEqual(stat.S_IMODE(detalle.stat().st_mode), 0o600)
            original = detalle.read_text()
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 2)
            self.assertEqual(detalle.read_text(), original)
