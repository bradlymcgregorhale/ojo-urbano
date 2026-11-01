"""Lectura de chapas por recorte (#215): voto, resumen y análisis sin ONNX ni red."""
import unittest
from unittest import mock

from PIL import Image

import patentes as P


class VotoTests(unittest.TestCase):
    def test_dos_iguales_publican(self):
        self.assertEqual(P.votar({"ocr_local": "A123BCD", "grok": "A123BCD", "gemini": None}),
                         ("A123BCD", 2, None))

    def test_una_sola_no_alcanza(self):
        patente, fuentes, motivo = P.votar({"ocr_local": None, "grok": "AB123CD", "gemini": None})
        self.assertIsNone(patente)
        self.assertEqual(fuentes, 1)
        self.assertIn("una sola lectura", motivo)

    def test_valida_distinta_bloquea_aunque_haya_dos_iguales(self):
        patente, fuentes, motivo = P.votar({"ocr_local": "ABC123", "grok": "ABC123", "gemini": "ABC128"})
        self.assertIsNone(patente)
        self.assertEqual(fuentes, 0)
        self.assertIn("lecturas distintas", motivo)
        self.assertIn("ABC128", motivo)

    def test_sin_lecturas(self):
        self.assertEqual(P.votar({"a": None, "b": None})[2], "ningún lector pudo leer la chapa completa")


class RecorteTests(unittest.TestCase):
    def test_recorte_amplia_y_respeta_bordes(self):
        img = Image.new("RGB", (1600, 900), "gray")
        rec = P.recortar(img, [1500, 800, 1600, 900])
        # Caja pegada a la esquina: el margen no puede salirse de la foto
        # (125 px de recorte, ampliado por 5 para acercarse a 640).
        self.assertEqual((rec.width, rec.height), (625, 625))

    def test_recorte_grande_no_se_achica(self):
        img = Image.new("RGB", (2000, 2000), "gray")
        rec = P.recortar(img, [0, 0, 1000, 500])
        self.assertEqual((rec.width, rec.height), (1250, 625))


def _foto(ancho=1600, alto=900):
    return Image.new("RGB", (ancho, alto), "white")


class LeerChapasTests(unittest.TestCase):
    """detectar/leer_local/leer_vlm se reemplazan; el resto es el código real."""

    def _correr(self, cajas, local, vlm, lectores=("x/grok", "y/gemini")):
        lecturas_vlm = iter(vlm)
        with mock.patch.object(P, "detectar", return_value=cajas), \
             mock.patch.object(P, "leer_local", side_effect=lambda rec: {"patente": local, "lectura": local, "confianza": 0.9}), \
             mock.patch.object(P, "leer_vlm", side_effect=lambda m, rec: next(lecturas_vlm)):
            return P.leer_chapas(_foto(), list(lectores))

    def test_chapa_chica_no_se_lee_y_explica_el_ancho(self):
        chapas = self._correr([{"caja": [10, 10, 108, 60], "confianza": 0.8}], "A123BCD", ["A123BCD", "A123BCD"])
        self.assertEqual(len(chapas), 1)
        c = chapas[0]
        self.assertFalse(c["legible"])
        self.assertEqual(c["lecturas"], {})
        self.assertIn("98 px", c["motivo"])
        self.assertIn("más cerca", c["motivo"])

    def test_chapa_grande_vota_y_nombra_a_los_lectores(self):
        chapas = self._correr([{"caja": [100, 100, 300, 220], "confianza": 0.9}], "A123BCD", ["A123BCD", None])
        c = chapas[0]
        self.assertTrue(c["legible"])
        self.assertEqual(c["patente"], "A123BCD")
        self.assertEqual(c["fuentes"], 2)
        self.assertEqual(set(c["lecturas"]), {"ocr_local", "grok", "gemini"})
        self.assertEqual(c["ancho_px"], 200)
        self.assertIsNone(c["motivo"])

    def test_lecturas_distintas_no_publican(self):
        chapas = self._correr([{"caja": [100, 100, 300, 220], "confianza": 0.9}], "A123BCD", ["A123BCD", "A128BCD"])
        self.assertFalse(chapas[0]["legible"])
        self.assertIn("lecturas distintas", chapas[0]["motivo"])


class AnalizarTests(unittest.TestCase):
    def _analizar(self, por_foto):
        lotes = iter(por_foto)
        with mock.patch.object(P, "leer_chapas", side_effect=lambda img, lectores: next(lotes)):
            return P.analizar([_foto() for _ in por_foto], ["x/grok"])

    @staticmethod
    def _chapa(patente, ancho=200):
        return {"caja": [0, 0, ancho, 100], "confianza_deteccion": 0.9, "ancho_px": ancho, "lecturas": {},
                "patente": patente, "fuentes": 2 if patente else 0, "legible": bool(patente),
                "motivo": None if patente else "ningún lector pudo leer la chapa completa"}

    def test_dos_fotos_del_mismo_vehiculo(self):
        r = self._analizar([[self._chapa("A123BCD")], [self._chapa("A123BCD")]])
        self.assertEqual(r["patente"], "A123BCD")
        self.assertIsNone(r["motivo"])
        self.assertEqual([f["patente"] for f in r["fotos"]], ["A123BCD", "A123BCD"])

    def test_una_foto_legible_y_otra_no_publica_la_legible(self):
        r = self._analizar([[self._chapa("A123BCD")], [self._chapa(None)]])
        self.assertEqual(r["patente"], "A123BCD")

    def test_dos_chapas_distintas_son_dos_vehiculos(self):
        r = self._analizar([[self._chapa("A123BCD")], [self._chapa("ABC123")]])
        self.assertIsNone(r["patente"])
        self.assertIn("chapas distintas", r["motivo"])

    def test_dos_chapas_legibles_en_una_foto(self):
        r = self._analizar([[self._chapa("A123BCD"), self._chapa("ABC123")]])
        self.assertIsNone(r["fotos"][0]["patente"])
        self.assertIn("más de una chapa legible", r["fotos"][0]["motivo"])
        self.assertIsNone(r["patente"])

    def test_sin_chapas(self):
        r = self._analizar([[]])
        self.assertEqual(r["fotos"][0]["motivo"], "sin chapa visible")
        self.assertEqual(r["motivo"], "sin chapa visible")
        self.assertEqual(r["lectores"], ["grok"])


class LeerPatenteVerificadorTests(unittest.TestCase):
    """_leer_patente de /clasificar usa el recorte cuando el detector está."""

    def _leer(self, chapas, disponible=True):
        import verificador as V
        llamadas = []
        with mock.patch.object(P, "disponible", return_value=disponible), \
             mock.patch.object(P, "leer_chapas", return_value=chapas), \
             mock.patch.object(V, "_llamar", side_effect=lambda *a, **k: llamadas.append(a) or '{"patente": null}'), \
             mock.patch.object(V, "modelos_activos", return_value=["a/1", "b/2", "c/3"]):
            return V._leer_patente(_foto()), llamadas

    def test_una_chapa_legible_se_publica_sin_llamar_a_la_foto_entera(self):
        patente, llamadas = self._leer([{"patente": "A123BCD", "legible": True}])
        self.assertEqual(patente, "A123BCD")
        self.assertEqual(llamadas, [])

    def test_dos_legibles_o_ninguna_no_publican(self):
        self.assertIsNone(self._leer([{"patente": "A123BCD", "legible": True}, {"patente": "ABC123", "legible": True}])[0])
        self.assertIsNone(self._leer([{"patente": None, "legible": False}])[0])

    def test_sin_detector_sigue_la_lectura_de_la_foto_entera(self):
        patente, llamadas = self._leer([], disponible=False)
        self.assertIsNone(patente)
        self.assertEqual(len(llamadas), 3)


class DisponibleTests(unittest.TestCase):
    def test_sin_onnx_no_esta_disponible(self):
        import builtins
        real = builtins.__import__

        def sin_onnx(nombre, *a, **k):
            if nombre == "onnxruntime":
                raise ImportError(nombre)
            return real(nombre, *a, **k)
        with mock.patch.object(builtins, "__import__", side_effect=sin_onnx):
            self.assertFalse(P.disponible())


if __name__ == "__main__":
    unittest.main()
