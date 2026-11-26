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


class PruebaConsumo(unittest.TestCase):
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

    def test_un_importe_conocido_puede_superar_el_tope_aunque_falten_cargos(self):
        [foto] = conciliar([caso("a", {"p": 2})], [uso("p", "g", .010000001)])[1]
        self.assertFalse(foto["conciliado"])
        self.assertTrue(foto["supera_tope"])

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
