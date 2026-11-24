from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from eval.vision.centavo import (
    confirma_pieza_rigida,
    errores,
    monto,
    piso_privado,
    reproducir,
    revisar_poda,
    verificar_recorte,
)


def recorte(caja=(5, 15, 25, 50)):
    return {
        "objetos": [(caja, "wooden cabinet drawer")],
        "detecciones": [{"texto": "municipal dumpster", "confianza": 0.8, "caja": (50, 0, 150, 90)}],
    }


def lectura():
    return {
        "pieza": "otro_rigido",
        "canto": "dos_paredes_rigidas",
        "evidencia_canto": "Dos paredes rígidas se encuentran en la esquina",
        "apoyo": "pavimento",
        "evidencia_apoyo": "El canto inferior apoya sobre las baldosas",
    }


class PruebaCentavo(unittest.TestCase):
    def test_poda_exige_vegetacion(self):
        self.assertFalse(revisar_poda("Bolsas negras junto al tronco"))
        self.assertTrue(revisar_poda("Salían ramas de una bolsa"))

    def test_vereda_visible_conserva_la_revision(self):
        privado = {"ubicacion": "privada", "evidencia_acopio": "Dentro de una habitación"}
        self.assertTrue(piso_privado("Bolsas junto al pretil del balcón", None))
        self.assertTrue(piso_privado("Bolsas junto a la pared", privado))
        self.assertFalse(piso_privado("Bolsas sobre la vereda frente al balcón", privado))
        self.assertFalse(piso_privado("Bolsas junto a una baranda", None))

    def test_evitar_cajon_dentro_del_cuerpo(self):
        self.assertTrue(confirma_pieza_rigida(recorte(), lectura()))
        self.assertFalse(confirma_pieza_rigida(recorte((60, 15, 80, 50)), lectura()))
        self.assertFalse(confirma_pieza_rigida(recorte(), lectura() | {"canto": "carton_plegado"}))

    def test_exporta_solo_metricas_y_mantiene_el_control(self):
        banco = {
            "control": {
                "esperado": {"retiro_escombros": True, "recoleccion": False},
                "lectura": {"evidencia": "Bolsas junto al tronco"},
            },
            "pieza": {
                "esperado": {"recoleccion": True, "retiro_muebles": True},
                "lectura": {"evidencia": "Cartón junto a una pieza apoyada"},
            },
        }
        base = {
            "control": {"etiquetas": ["retiro_escombros"], "errores": [], "presupuesto_usd": "0.009866"},
            "pieza": {"etiquetas": ["recoleccion"], "errores": ["retiro_muebles"], "presupuesto_usd": "0.00997475"},
        }
        apoyo = {"control": {"agrega": [], "quita": [], "reserva_usd": "0.00135"}}
        indice = {"pieza": {"en_ruta": True}}
        lecturas = {"pieza": (lectura(), lectura() | {"evidencia_canto": "El tablero tiene canto grueso"})}
        resultado = reproducir(banco, base, {}, apoyo, indice, lecturas, {"pieza": recorte()}, {"control"})
        self.assertEqual(resultado["fotos_exactas"], 2)
        self.assertEqual(resultado["piezas_rigidas_recuperadas"], 1)
        self.assertEqual(resultado["omitidas_poda"], 1)
        self.assertEqual(resultado["diferencias_nuevas"], 0)
        self.assertEqual(Decimal(resultado["ahorro_simulado_usd"]), Decimal("0.00135"))
        self.assertNotIn("control", resultado)

    def test_presupuesto_y_referencias(self):
        self.assertEqual(monto("0.01"), Decimal("0.01"))
        for importe in ("0.01001", "-0.01", "NaN"):
            with self.assertRaises(ValueError):
                monto(importe)
        self.assertEqual(errores({"recoleccion"}, {"recoleccion": None, "retiro_escombros": True}), {"retiro_escombros"})

    def test_recorte_de_otro_origen_se_rechaza(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta)
            (ruta / "archivo.jpg").write_bytes(b"recorte")
            huella = sha256(b"recorte").hexdigest()
            datos = {"foto": "archivo.jpg", "origen_sha256": "A", "entrada_sha256": huella}
            original = {"origen_sha256": "A", "entrada_sha256": "B"}
            verificar_recorte(ruta, datos, original)
            with self.assertRaises(ValueError):
                verificar_recorte(ruta, datos | {"origen_sha256": "C"}, original)


if __name__ == "__main__":
    unittest.main()
