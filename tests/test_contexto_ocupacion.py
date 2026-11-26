import copy
import json
import unittest
from unittest.mock import patch

import contexto_ocupacion
import prioridad
import revision_omisiones
import verificador


PEDIDO = "uso indebido del espacio público"
MUEBLES = {"problemas": [{"key": "retiro_muebles", "fuentes": 3}],
           "categorias_contexto": [{"codigo": "118001", "respaldo_visual": "compatible", "fuentes": 1}],
           "modelos": [{"categorias": [{"key": "ocupacion_comercial",
                                        "mercaderia_visible": "ropa y sillón en exhibición"}]}],
           "foto_valida": True}


class ContextoOcupacionTest(unittest.TestCase):
    def test_foto_compatible_con_el_pedido_aunque_la_lectura_visual_diga_retiro(self):
        resultado = copy.deepcopy(MUEBLES)
        contexto_ocupacion.aplicar(resultado, PEDIDO)
        self.assertEqual(resultado["reclamo_foto"]["estado"], "compatible")
        self.assertEqual(resultado["solicitudes_sugeridas"]["servicios"], ["ocupacion_comercial"])
        self.assertTrue(resultado["solicitudes_sugeridas"]["requiere_confirmacion"])
        self.assertEqual(resultado["problemas"], MUEBLES["problemas"])

    def test_muebles_sin_indicio_de_venta_no_alcanzan_para_ocupacion_comercial(self):
        resultado = {"problemas": MUEBLES["problemas"]}
        contexto_ocupacion.aplicar(resultado, "ocupacion de espacio publico")
        self.assertEqual(resultado["reclamo_foto"]["estado"], "sin_evidencia")

    def test_pizarra_y_cajones_de_apoyo_son_obstruccion_con_pedido_de_ocupacion(self):
        resultado = {"problemas": [{"key": "obstruccion", "fuentes": 2}],
                     "categorias_contexto": MUEBLES["categorias_contexto"]}
        contexto_ocupacion.aplicar(resultado, "Ocupación local")
        self.assertEqual(resultado["reclamo_foto"]["estado"], "compatible")
        self.assertEqual(resultado["reclamo_foto"]["codigo"], "118020")
        self.assertEqual(resultado["solicitudes_sugeridas"]["servicios"], ["obstruccion"])

    def test_obstruccion_y_mercaderia_confirmadas_tienen_servicios_distintos(self):
        problemas = [{"key": clave, "fuentes": 2} for clave in ("obstruccion", "ocupacion_comercial")]
        resultado = {"problemas": problemas}
        contexto_ocupacion.aplicar(resultado, PEDIDO)
        self.assertEqual(resultado["reclamo_foto"]["servicios"], ["ocupacion_comercial", "obstruccion"])
        self.assertEqual(resultado["problemas"], problemas)

    def test_propuesta_sin_contexto_conserva_los_dos_servicios(self):
        problemas = [{"key": clave, "fuentes": 2} for clave in ("obstruccion", "ocupacion_comercial")]
        resultado = prioridad.proponer_solicitudes({"problemas": problemas})
        self.assertEqual(resultado["servicios"], ["ocupacion_comercial", "obstruccion"])

    def test_prioridad_respeta_un_pedido_especifico_respaldado_por_la_foto(self):
        problemas = [{"key": clave, "fuentes": 2} for clave in ("obstruccion", "ocupacion_comercial")]
        principal = {"key": "obstruccion", "estado": "seleccionado", "criterio": "pedido_explicito"}
        resultado = prioridad.proponer_solicitudes({"problemas": problemas, "problema_principal": principal})
        self.assertEqual(resultado["servicios"], ["obstruccion"])

    def test_obstruccion_corroborada_mantiene_pedido_generico_aunque_la_foto_haya_sido_descartada(self):
        resultado = {"foto_valida": False, "descartados_por_foto": [
            {"key": "obstruccion", "fuentes": 2, "motivo_descarte": "la foto no corresponde a lo que se pidió"},
        ]}
        contexto_ocupacion.aplicar(resultado, PEDIDO)
        self.assertEqual(resultado["reclamo_foto"]["servicios"], ["obstruccion"])

    def test_el_verificador_exige_mercaderia_visible_para_reportar_venta(self):
        categorias = {clave: {"nombre": clave} for clave in ("ocupacion_comercial", "manteros", "obstruccion")}
        veredicto = {"categorias": [
            {"key": "ocupacion_comercial", "mercaderia_visible": None},
            {"key": "manteros", "mercaderia_visible": "No se ve mercadería"},
            {"key": "obstruccion", "evidencia": "Pizarra y cajones de apoyo en el paso"},
        ]}
        with patch.object(verificador, "_llamar", return_value=json.dumps(veredicto)):
            lectura = verificador._verificar_uno("prueba/modelo", "", categorias)
        self.assertEqual([c["key"] for c in lectura["categorias"]], ["obstruccion"])

    def test_el_verificador_conserva_mercaderia_identificada_en_ambos_tipos_de_venta(self):
        for clave in ("ocupacion_comercial", "manteros"):
            with self.subTest(clave=clave):
                categoria = {"key": clave, "mercaderia_visible": "naranjas y peras",
                             "evidencia": "Naranjas y peras en exhibición en la vereda"}
                with patch.object(verificador, "_llamar", return_value=json.dumps({"categorias": [categoria]})):
                    lectura = verificador._verificar_uno("prueba/modelo", "", {clave: {"nombre": clave}})
                self.assertEqual(lectura["categorias"][0]["mercaderia_visible"], "naranjas y peras")

    def test_equipamiento_de_un_local_respalda_la_ocupacion_sin_mercaderia(self):
        categorias = {clave: {"nombre": clave} for clave in ("ocupacion_comercial", "manteros")}
        equipo = "Máquina de garra instalada en la vereda junto al kiosco"
        veredicto = {"categorias": [{"key": clave, "equipamiento_visible": equipo}
                                   for clave in categorias]}
        with patch.object(verificador, "_llamar", return_value=json.dumps(veredicto)):
            lectura = verificador._verificar_uno("prueba/modelo", "", categorias)
        self.assertEqual([c["key"] for c in lectura["categorias"]], ["ocupacion_comercial"])
        self.assertEqual(lectura["categorias"][0]["equipamiento_visible"], equipo)
        resultado = {"modelos": [lectura]}
        contexto_ocupacion.aplicar(resultado, "ocupación por local comercial")
        self.assertEqual(resultado["reclamo_foto"]["codigo"], "118001")
        self.assertEqual(resultado["solicitudes_sugeridas"]["servicios"], ["ocupacion_comercial"])

    def test_cartel_y_cajones_vacios_no_cuentan_como_equipamiento(self):
        for equipo in ("Cartel de precios en la vereda", "Cajones de apoyo vacíos", "Pizarra aislada",
                       "No se ve equipamiento comercial", None):
            with self.subTest(equipo=equipo):
                categoria = {"key": "ocupacion_comercial", "equipamiento_visible": equipo}
                with patch.object(verificador, "_llamar", return_value=json.dumps({"categorias": [categoria]})):
                    lectura = verificador._verificar_uno("prueba/modelo", "", {"ocupacion_comercial": {}})
                self.assertEqual(lectura["categorias"], [])

    def test_revision_de_objetos_no_recupera_venta_ambulante_sin_productos(self):
        hallazgo = {"tipo": "venta_ambulante", "uso": "venta", "objeto": "pizarra",
                    "evidencia": "lista de precios y cajones", "ubicacion": "vereda"}
        self.assertEqual(revision_omisiones.interpretar_objetos({"hallazgos": [hallazgo]})["hallazgos"], [])
        hallazgo["mercaderia_visible"] = "peras sobre una mesa"
        self.assertEqual(revision_omisiones.interpretar_objetos({"hallazgos": [hallazgo]})["hallazgos"][0],
                         hallazgo)

    def test_venta_en_vidriera_no_es_ambulante_y_el_puesto_en_la_vereda_se_conserva(self):
        for ubicacion, evidencia, mercaderia, publicado in (
            ("vidriera", "Muestras alineadas en la vidriera del negocio", "telas de colores", False),
            ("frente al local", "Exhibición del mostrador del local", "telas", False),
            ("frente al local", "Muestras detrás del vidrio", "telas", False),
            ("vereda frente a la vidriera", "Puesto con peras y un vendedor en la vereda", "peras", True),
        ):
            with self.subTest(ubicacion=ubicacion, evidencia=evidencia):
                hallazgo = {"tipo": "venta_ambulante", "uso": "venta", "objeto": mercaderia,
                            "evidencia": evidencia, "ubicacion": ubicacion, "mercaderia_visible": mercaderia}
                omisiones = revision_omisiones.interpretar_objetos({"hallazgos": [hallazgo]})
                self.assertEqual(bool(omisiones["hallazgos"]), publicado)
                categoria = {"key": "manteros", "evidencia": evidencia, "mercaderia_visible": mercaderia}
                with patch.object(verificador, "_llamar", return_value=json.dumps({"categorias": [categoria]})):
                    lectura = verificador._verificar_uno("prueba/modelo", "", {"manteros": {}})
                self.assertEqual(bool(lectura["categorias"]), publicado)

    def test_contenedor_lleno_con_el_mismo_texto_pide_otra_foto(self):
        resultado = {"problemas": [{"key": "contenedor_desbordado"}],
                     "categorias_contexto": MUEBLES["categorias_contexto"]}
        contexto_ocupacion.aplicar(resultado, "ocupacion de espacio pubilco")
        self.assertEqual(resultado["reclamo_foto"]["estado"], "no_corresponde")
        self.assertEqual(resultado["solicitudes_sugeridas"]["servicios"], [])
        self.assertTrue(resultado["conclusion"]["requiere_revision"])

    def test_sin_pedido_y_pedidos_distintos_conservan_su_contrato(self):
        for texto in ("", "retiro de muebles", "no es ocupación del espacio público",
                      "ocupación del espacio público moto sin dominio",
                      "ocupación del espacio público de un local gastronómico"):
            with self.subTest(texto=texto):
                resultado = copy.deepcopy(MUEBLES)
                contexto_ocupacion.aplicar(resultado, texto)
                self.assertEqual(resultado, MUEBLES)

    def test_pedido_explicito_con_retiro_se_resuelve_en_prueba(self):
        texto = "Ocupación comercial y retiro de muebles"
        ocupacion = {"key": "ocupacion_comercial", "nombre": "Ocupación comercial"}
        retiro = {"key": "retiro_muebles", "nombre": "Retiro de muebles"}
        for respaldo in ("compatible", "contradice"):
            with self.subTest(respaldo=respaldo):
                problemas = [ocupacion, retiro] if respaldo == "compatible" else [retiro]
                resultado = {"modo": "prueba", "foto_valida": True, "problemas": problemas,
                             "categorias_contexto": [ocupacion | {"respaldo_visual": respaldo},
                                                     retiro | {"respaldo_visual": "compatible"}]}
                resultado["solicitudes_sugeridas"] = prioridad.proponer_solicitudes(resultado)
                contexto_ocupacion.aplicar(resultado, texto)
                self.assertEqual(resultado["reclamo_foto"]["estado"],
                                 "compatible" if respaldo == "compatible" else "no_corresponde")
                self.assertEqual(resultado["solicitudes_sugeridas"]["servicios"], [p["key"] for p in problemas])

        for modo, pedido, categorias in (
                ("alto", texto, [ocupacion, retiro]),
                ("prueba", "No es ocupación comercial. Pido retiro de muebles.", [ocupacion, retiro]),
                ("prueba", "Ocupación del espacio público y retiro de muebles", [ocupacion, retiro]),
                ("prueba", texto, [retiro])):
            with self.subTest(modo=modo, pedido=pedido, categorias=categorias):
                resultado = {"modo": modo, "categorias_contexto": categorias}
                contexto_ocupacion.aplicar(resultado, pedido)
                self.assertNotIn("reclamo_foto", resultado)

    def test_falta_de_evidencia_se_distingue_de_foto_distinta(self):
        resultado = {}
        contexto_ocupacion.aplicar(resultado, PEDIDO)
        self.assertEqual(resultado["reclamo_foto"]["estado"], "sin_evidencia")
        self.assertEqual(resultado["solicitudes_sugeridas"]["servicios"], [])
        self.assertTrue(resultado["hay_reclamo"])

    def test_la_barrera_solo_se_propone_si_respalda_el_otro_pedido_explicito(self):
        ocupacion = {"key": "ocupacion_comercial", "nombre": "Ocupación comercial"}
        retiro = {"key": "retiro_muebles", "nombre": "Retiro de muebles"}
        obstruccion = {"key": "obstruccion", "nombre": "Obstrucción"}
        for pedida in (False, True):
            with self.subTest(pedida=pedida):
                contexto = [ocupacion | {"respaldo_visual": "contradice"},
                            retiro | {"respaldo_visual": "compatible"}]
                if pedida:
                    contexto.append(obstruccion | {"respaldo_visual": "compatible"})
                salida = {"modo": "prueba", "analisis_estado": "completo", "foto_valida": True,
                          "problemas": [ocupacion, retiro, obstruccion],
                          "categorias_contexto": contexto}
                salida["solicitudes_sugeridas"] = prioridad.proponer_solicitudes(salida)
                texto = "Ocupación comercial y retiro de muebles"
                if pedida:
                    texto += " y obstrucción por una barrera aparte"
                contexto_ocupacion.aplicar(salida, texto)
                self.assertEqual(salida["reclamo_foto"]["estado"], "compatible" if pedida else "no_corresponde")
                servicios = ["obstruccion", "retiro_muebles"] if pedida else ["retiro_muebles"]
                self.assertEqual(salida["solicitudes_sugeridas"]["servicios"], servicios)


if __name__ == "__main__":
    unittest.main()
