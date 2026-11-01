"""Lectura de patentes por recorte: detector, recorte ampliado y voto (#215).

Mandar la foto entera a un VLM no alcanza: una chapa de moto ocupa 100 a
200 px en una foto de 1600 y los lectores inventan una cadena plausible en
vez de decir que no la leen. Medido el 1 de noviembre de 2026 sobre fotos
reales del grupo: el mejor modelo acertó 5 de 10 e inventó 8.

Acá la chapa se ubica con un detector ONNX (`open-image-models`), se recorta
con margen, se amplía, y la leen un OCR local de chapas (`fast-plate-ocr`) y
dos lectores de visión distintos. Se publica una patente solo cuando dos
lectores coinciden en la misma cadena válida y ninguna otra lectura válida la
contradice. Un recorte angosto no se intenta: se informa el ancho para que el
consumidor le pida al vecino una foto más cerca.

Los pesos (unos 25 MB) viven en `MODELOS_CHAPAS_DIR` (por defecto
`.modelos-chapas/` junto a este archivo) porque el home del servidor no es
escribible. Sin `onnxruntime` instalado, `disponible()` es False y el resto
del sistema sigue como antes.
"""
import concurrent.futures
import json
import os
import re
import threading
import urllib.error
from pathlib import Path

from PIL import Image

import verificador as V
from prompts import _PROMPT_LEER_CHAPA

DIR_MODELOS = Path(os.environ.get("MODELOS_CHAPAS_DIR") or
                   Path(__file__).resolve().parent / ".modelos-chapas")
DETECTOR = os.environ.get("DETECTOR_CHAPAS", "yolo-v9-t-384-license-plate-end2end").strip()
OCR_LOCAL = os.environ.get("OCR_CHAPAS", "cct-xs-v2-global-model").strip()
# Lectores de visión para el recorte. Distintos de los verificadores de la
# rúbrica a propósito: medidos sobre recortes, grok y gemini fueron los que
# más coincidieron entre sí y con el OCR local.
LECTORES = [m.strip() for m in os.environ.get(
    "LECTORES_CHAPA", "x-ai/grok-4.3,google/gemini-3.8-flash").split(",") if m.strip()]
MARGEN = 0.25           # margen alrededor de la caja del detector
ANCHO_RECORTE = 640     # el recorte se amplía hasta este ancho antes de leerlo
# Por debajo de este ancho (en la foto original) ningún lector acertó.
ANCHO_MINIMO = int(os.environ.get("CHAPA_ANCHO_MINIMO", "110"))
CONF_DETECTOR = float(os.environ.get("CHAPA_CONF_DETECTOR", "0.3"))
MAX_CHAPAS = 4
# Confianza mínima del OCR local para que su lectura cuente como un voto.
OCR_CONF_MINIMA = float(os.environ.get("OCR_CHAPAS_CONF_MINIMA", "0.6"))

_ERRORES_LECTOR = (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError, OSError)
_lock = threading.Lock()
_modelos = {}


def disponible():
    """True si las dependencias ONNX están instaladas (no carga los pesos)."""
    try:
        import onnxruntime  # noqa: F401
        import open_image_models  # noqa: F401
        import fast_plate_ocr  # noqa: F401
    except ImportError:
        return False
    return True


def _cargar():
    """Detector y OCR, una sola vez por proceso. Lanza ImportError sin ONNX."""
    with _lock:
        if "detector" in _modelos:
            return _modelos
        import open_image_models.detection.core.hub as hub_det
        import fast_plate_ocr.inference.hub as hub_ocr
        from open_image_models import create_detector
        from fast_plate_ocr import LicensePlateRecognizer
        DIR_MODELOS.mkdir(parents=True, exist_ok=True)
        # Las dos bibliotecas descargan a ~/.cache; en el servidor el home es
        # de root. El directorio se lee al momento de descargar, así que
        # alcanza con cambiar la constante antes de crear los modelos.
        hub_det.MODEL_CACHE_DIR = DIR_MODELOS / "detector"
        hub_ocr.MODEL_CACHE_DIR = DIR_MODELOS / "ocr"
        _modelos["detector"] = create_detector(DETECTOR, conf_thresh=CONF_DETECTOR,
                                               providers=["CPUExecutionProvider"])
        _modelos["ocr"] = LicensePlateRecognizer(OCR_LOCAL, device="cpu")
        return _modelos


def detectar(img):
    """Cajas de chapas en la foto: [{caja: [x1, y1, x2, y2], confianza}]."""
    import numpy as np
    det = _cargar()["detector"]
    cajas = []
    for d in det.predict(np.asarray(img.convert("RGB"))):
        bb = d.bounding_box
        caja = [int(bb.x1), int(bb.y1), int(bb.x2), int(bb.y2)]
        if caja[2] - caja[0] < 8 or caja[3] - caja[1] < 4:
            continue
        cajas.append({"caja": caja, "confianza": round(float(d.confidence), 3)})
    cajas.sort(key=lambda c: -c["confianza"])
    return cajas[:MAX_CHAPAS]


def recortar(img, caja, margen=MARGEN, ancho=ANCHO_RECORTE):
    """Recorte con margen, ampliado por un entero hasta `ancho` (sin achicar)."""
    x1, y1, x2, y2 = caja
    mx, my = int((x2 - x1) * margen), int((y2 - y1) * margen)
    rec = img.crop((max(0, x1 - mx), max(0, y1 - my),
                    min(img.width, x2 + mx), min(img.height, y2 + my)))
    esc = max(1, round(ancho / max(rec.width, 1)))
    if esc > 1:
        rec = rec.resize((rec.width * esc, rec.height * esc), Image.LANCZOS)
    return rec


def leer_local(rec):
    """Lectura del OCR local: {patente (validada o None), confianza}."""
    import numpy as np
    ocr = _cargar()["ocr"]
    pred = ocr.run(np.asarray(rec.convert("RGB")), return_confidence=True)[0]
    texto = str(getattr(pred, "plate", "") or "").replace("_", "")
    probs = getattr(pred, "char_probs", None)
    conf = float(min(probs)) if probs is not None and len(probs) else 0.0
    patente = V._patente_normalizada(texto)
    return {"patente": patente if conf >= OCR_CONF_MINIMA else None,
            "lectura": texto or None, "confianza": round(conf, 3)}


def leer_vlm(modelo, rec):
    """Una lectura de visión sobre el recorte; None si no la leyó o falló."""
    try:
        contenido = V._llamar(modelo, [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": V._imagen_data_url(rec, lado=ANCHO_RECORTE)}},
                {"type": "text", "text": _PROMPT_LEER_CHAPA},
            ]},
        ], max_tokens=400, etapa="leer_chapa")
        return V._patente_normalizada(V._extraer_json(contenido).get("patente"))
    except _ERRORES_LECTOR:
        return None


def votar(lecturas):
    """lecturas: {fuente: patente válida o None} -> (patente, fuentes, motivo).

    Publica solo con dos lecturas iguales y ninguna válida distinta: la nula
    no es discrepancia (lector conservador), la válida distinta sí."""
    validas = [p for p in lecturas.values() if p]
    if not validas:
        return None, 0, "ningún lector pudo leer la chapa completa"
    distintas = set(validas)
    if len(distintas) > 1:
        return None, 0, "lecturas distintas entre lectores: " + " / ".join(sorted(distintas))
    if len(validas) < 2:
        return None, 1, "una sola lectura, sin confirmación"
    return validas[0], len(validas), None


def _nombre(modelo):
    return re.sub(r"[^a-z0-9]+", "_", modelo.split("/")[-1].lower()).strip("_")


def leer_chapas(img, lectores=None):
    """Todas las chapas detectadas en la foto, cada una con sus lecturas."""
    lectores = list(dict.fromkeys(LECTORES if lectores is None else lectores))
    salida = []
    for det in detectar(img):
        x1, y1, x2, y2 = det["caja"]
        chapa = {"caja": det["caja"], "confianza_deteccion": det["confianza"],
                 "ancho_px": x2 - x1, "lecturas": {}, "patente": None,
                 "fuentes": 0, "legible": False, "motivo": None}
        if chapa["ancho_px"] < ANCHO_MINIMO:
            chapa["motivo"] = (f"chapa muy chica ({chapa['ancho_px']} px de ancho): "
                               "sacá la foto más cerca y de atrás")
            salida.append(chapa)
            continue
        rec = recortar(img, det["caja"])
        local = leer_local(rec)
        chapa["lecturas"]["ocr_local"] = local["patente"]
        chapa["ocr_local"] = {"lectura": local["lectura"], "confianza": local["confianza"]}
        if lectores:
            with concurrent.futures.ThreadPoolExecutor(len(lectores)) as pool:
                vistas = V._map_con_contexto(pool, lambda m: leer_vlm(m, rec), lectores)
            for modelo, p in zip(lectores, vistas):
                chapa["lecturas"][_nombre(modelo)] = p
        chapa["patente"], chapa["fuentes"], chapa["motivo"] = votar(chapa["lecturas"])
        chapa["legible"] = chapa["patente"] is not None
        salida.append(chapa)
    return salida


def _resumen_foto(chapas):
    legibles = [c["patente"] for c in chapas if c["legible"]]
    if not chapas:
        return None, "sin chapa visible"
    if len(legibles) == 1:
        return legibles[0], None
    if len(legibles) > 1:
        return None, "más de una chapa legible: " + " / ".join(legibles)
    return None, chapas[0]["motivo"]


def analizar(imgs, lectores=None):
    """Una o dos fotos -> {fotos: [{chapas, patente, motivo}], patente, motivo}.

    La patente del conjunto sale cuando las fotos que leyeron algo coinciden
    en una sola cadena; dos distintas significan dos vehículos y no se publica."""
    fotos = []
    for img in imgs:
        chapas = leer_chapas(img, lectores)
        patente, motivo = _resumen_foto(chapas)
        fotos.append({"chapas": chapas, "patente": patente, "motivo": motivo})
    leidas = {f["patente"] for f in fotos if f["patente"]}
    if len(leidas) == 1:
        patente, motivo = next(iter(leidas)), None
    elif len(leidas) > 1:
        patente, motivo = None, "las fotos muestran chapas distintas: " + " / ".join(sorted(leidas))
    else:
        patente, motivo = None, next((f["motivo"] for f in fotos if f["motivo"]), "sin chapa visible")
    return {"fotos": fotos, "patente": patente, "motivo": motivo,
            "lectores": [_nombre(m) for m in (LECTORES if lectores is None else lectores)],
            "detector": DETECTOR, "ocr": OCR_LOCAL}
