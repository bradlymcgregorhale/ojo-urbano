#!/usr/bin/env python3
"""Ojo Urbano: API de clasificación de fotos de incidencias urbanas.

Un modelo propio (embeddings CLIP + DINOv2 + SigLIP2 con cabezal de regresión
logística multi-etiqueta, entrenado con miles de fotos reales etiquetadas a
mano) clasifica la foto localmente. Si hay una OPENROUTER_API_KEY configurada,
dos modelos de visión verifican el resultado de forma independiente y un
árbitro de texto resuelve los desacuerdos (ver verificador.py).

Uso:
    pip install -r requirements.txt
    cp .env.example .env   # completar OPENROUTER_API_KEY (opcional)
    python servidor.py

Abrí http://127.0.0.1:8080 y arrastrá una foto, o usá POST /clasificar.
La primera ejecución descarga los modelos de embeddings (varios GB).
"""
import asyncio
import collections
import concurrent.futures
import hashlib
import hmac
import io
import json
import math
import os
import threading
import time
from pathlib import Path

# carga .env si existe (sin dependencias extra)
_env = Path(__file__).resolve().parent / ".env"
if _env.exists():
    for linea in _env.read_text().splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

import joblib
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image
from sentence_transformers import SentenceTransformer

import verificador

AQUI = Path(__file__).resolve().parent
MODELO = AQUI / "model.joblib"
CATEGORIAS = json.loads((AQUI / "categorias.json").read_text())
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
UMBRAL = float(os.environ.get("UMBRAL", "0.5"))
GRAV_MAX = 5

# Límites de abuso. Clasificar una foto cuesta 25-60 s de CPU y 2-3 llamadas
# pagas a OpenRouter, así que el endpoint no puede quedar abierto sin techo.
MAX_BYTES = int(os.environ.get("MAX_BYTES", str(10 * 1024 * 1024)))
MARGEN_MULTIPART = 64 * 1024  # boundaries, headers y contexto encima de la foto
MAX_PIXELES = int(os.environ.get("MAX_PIXELES", str(25_000_000)))
CONCURRENCIA = max(1, int(os.environ.get("CONCURRENCIA", "1")))
# Techo global de fotos verificadas por día (0 = sin techo). El límite por IP
# no alcanza solo: detrás de un proxy todos comparten IP, y un atacante
# distribuido usa muchas. Esto acota el gasto pase lo que pase.
CUOTA_DIARIA = int(os.environ.get("CUOTA_DIARIA", "500"))
MOTIVO_CUOTA = "cuota diaria de verificación agotada"
RATE_LIMITE = int(os.environ.get("RATE_LIMITE", "60"))  # 0 = sin límite
RATE_VENTANA = int(os.environ.get("RATE_VENTANA", "3600"))
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
CACHE_MAX = int(os.environ.get("CACHE_MAX", "128"))
# Solo con esto activo se cree el X-Forwarded-For; si no, cualquiera podría
# falsear su IP y saltarse el límite de tasa poniendo un header.
CONFIAR_PROXY = os.environ.get("CONFIAR_PROXY", "").strip().lower() not in (
    "", "0", "false", "no")

# Pillow, entre MAX_IMAGE_PIXELS y el doble, solo avisa y sigue decodificando:
# una imagen bomba de pocos KB se convierte en cientos de MB de RGB.
Image.MAX_IMAGE_PIXELS = MAX_PIXELES

if not MODELO.exists():
    raise SystemExit("Falta model.joblib junto a servidor.py")

bundle = joblib.load(MODELO)
clf = bundle["clf"]
clases = list(bundle.get("classes") or clf.classes_)
sev_model = bundle.get("sev_model")
combo = bundle.get("embed_model", "clip-ViT-B-32").lower()

print("Cargando CLIP (la primera vez se descarga)...")
clip = SentenceTransformer("clip-ViT-B-32")

_dino, _siglip = {}, {}


def dino_vec(img):
    if not _dino:
        import torch
        from transformers import AutoImageProcessor, AutoModel
        print("Cargando facebook/dinov2-base...")
        _dino["torch"] = torch
        _dino["proc"] = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
        _dino["model"] = AutoModel.from_pretrained("facebook/dinov2-base").eval()
    with _dino["torch"].no_grad():
        out = _dino["model"](**_dino["proc"](images=img, return_tensors="pt"))
    v = out.pooler_output[0].numpy().astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-8)


def siglip_vec(img):
    if not _siglip:
        import torch
        from transformers import AutoModel, AutoProcessor
        nombre = "google/siglip2-so400m-patch14-384"
        print(f"Cargando {nombre}...")
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        _siglip["torch"] = torch
        _siglip["dev"] = dev
        _siglip["proc"] = AutoProcessor.from_pretrained(nombre)
        _siglip["model"] = AutoModel.from_pretrained(nombre).to(dev).eval()
    with _siglip["torch"].no_grad():
        inp = _siglip["proc"](images=img, return_tensors="pt").to(_siglip["dev"])
        v = _siglip["model"].get_image_features(**inp)
        # transformers >= 5 devuelve un ModelOutput; antes, el tensor directo
        if not _siglip["torch"].is_tensor(v):
            v = v.pooler_output
        v = v[0].cpu().numpy().astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-8)


def caracteristicas(img):
    partes = [clip.encode([img], convert_to_numpy=True, normalize_embeddings=True)[0]]
    if "dinov2" in combo:
        partes.append(dino_vec(img))
    if "siglip" in combo:
        partes.append(siglip_vec(img))
    return np.concatenate(partes).reshape(1, -1)


def nombre_de(k):
    return CATEGORIAS.get(k, {}).get("nombre", k)


def clasificar_local(img):
    feats = caracteristicas(img)
    proba = clf.predict_proba(feats)[0]
    # pliega las clases sinónimas del modelo a su categoría canónica (max score)
    agg = {}
    for k, p in zip(clases, proba):
        ck = verificador.FOLD.get(k, k)
        agg[ck] = max(agg.get(ck, 0.0), float(p))
    ranking = sorted(agg.items(), key=lambda x: -x[1])
    fmt = lambda lst: [{"key": k, "nombre": nombre_de(k), "score": round(s, 4)}
                       for k, s in lst]
    predichas = [p for p in ranking if p[1] >= UMBRAL] or ranking[:1]

    gravedad = None
    if sev_model is not None:
        raw = float(sev_model.predict(feats)[0])
        # piso, no redondeo: 2.51 es gravedad 2, no 3
        gravedad = {"value": int(min(GRAV_MAX, max(1, math.floor(raw)))), "raw": round(raw, 2)}

    return {
        "predichas": fmt(predichas),
        "top5": fmt(ranking[:5]),
        "probabilidades": fmt(ranking),
        "gravedad": gravedad,
        "umbral": UMBRAL,
    }


_pedidos = collections.defaultdict(collections.deque)  # ip -> timestamps
_cache = collections.OrderedDict()                    # huella -> respuesta
# El cupo lo suelta el HILO cuando termina de verdad, no la corrutina que lo
# espera: cancelar un await NO corta el thread, así que soltarlo ahí dejaría
# entrar pedidos nuevos mientras el pipeline anterior sigue quemando CPU.
_cupos = threading.BoundedSemaphore(CONCURRENCIA)
# Tantos hilos como cupos: con el semáforo de admisión, un trabajo aceptado
# siempre encuentra un worker libre y no se queda encolado.
_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=CONCURRENCIA, thread_name_prefix="ojo")
_cuota = {"dia": None, "usadas": 0}
_cuota_lock = threading.Lock()


def _hay_cuota():
    """Consume una unidad del techo diario global de verificaciones pagas."""
    if CUOTA_DIARIA <= 0:
        return True
    hoy = time.strftime("%Y-%m-%d", time.gmtime())
    with _cuota_lock:
        if _cuota["dia"] != hoy:
            _cuota["dia"], _cuota["usadas"] = hoy, 0
        if _cuota["usadas"] >= CUOTA_DIARIA:
            return False
        _cuota["usadas"] += 1
        return True


def _ip_cliente(request):
    if CONFIAR_PROXY:
        reenviada = request.headers.get("x-forwarded-for", "")
        if reenviada:
            return reenviada.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _purgar_pedidos(ahora):
    """Saca las IPs que ya salieron de la ventana.

    Sin esto, una IP que pega una sola vez deja su entrada para siempre y el
    diccionario crece sin techo ante un ataque distribuido.
    """
    viejas = [ip for ip, cola in _pedidos.items()
              if not cola or ahora - cola[-1] > RATE_VENTANA]
    for ip in viejas:
        del _pedidos[ip]


def _permitir(ip):
    """Ventana deslizante por IP. Devuelve False si ya agotó su cuota."""
    if RATE_LIMITE <= 0:
        return True
    ahora = time.monotonic()
    if len(_pedidos) > 1024:
        _purgar_pedidos(ahora)
    cola = _pedidos[ip]
    while cola and ahora - cola[0] > RATE_VENTANA:
        cola.popleft()
    if len(cola) >= RATE_LIMITE:
        return False
    cola.append(ahora)
    return True


async def _leer_acotado(archivo):
    """Lee el upload en trozos y aborta apenas pasa el límite de bytes."""
    trozos, total = [], 0
    while True:
        trozo = await archivo.read(65536)
        if not trozo:
            break
        total += len(trozo)
        if total > MAX_BYTES:
            raise HTTPException(
                413, f"la foto supera el límite de {MAX_BYTES // (1024 * 1024)} MB")
        trozos.append(trozo)
    return b"".join(trozos)


def _abrir_imagen(datos):
    """Abre la foto rechazando bombas de descompresión antes de decodificarla.

    Image.open solo lee la cabecera, así que las dimensiones se controlan
    ANTES del convert(), que es el que materializa la imagen entera en RAM.
    El corte es en el mismo umbral en el que Pillow apenas avisaría, así que
    el warning queda cubierto sin tocar el filtro global de warnings (que no
    sería seguro de mutar ahora que esto corre en un hilo aparte).
    """
    demasiado = f"la foto supera los {MAX_PIXELES // 1_000_000} megapíxeles"
    try:
        img = Image.open(io.BytesIO(datos))
        ancho, alto = img.size
        if ancho * alto > MAX_PIXELES:
            raise HTTPException(400, demasiado)
        return img.convert("RGB")
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        # Pillow ya la frena solo por encima del doble de MAX_IMAGE_PIXELS
        raise HTTPException(400, demasiado)
    except Exception:
        raise HTTPException(400, "no pude leer la imagen")


def _cacheable(respuesta):
    """Un resultado degradado por algo transitorio no se guarda.

    Si se cachea una respuesta hecha con la cuota diaria agotada (o con los
    verificadores caídos), esa foto queda devuelta sin verificar para siempre,
    incluso al día siguiente con cuota nueva.
    """
    veri = respuesta.get("detalle", {}).get("verificacion", {})
    if veri.get("activa"):
        if any(not v.get("ok") for v in veri.get("verificadores") or []):
            return False
        # Con árbitro configurado, que queden categorías en duda significa que
        # el arbitraje falló o quedó incompleto (respondió sin decidirlas
        # todas). Cachearlo congela ese resultado a medio hacer para siempre.
        if verificador.ARBITRO and respuesta.get("en_duda"):
            return False
        arbitro = veri.get("arbitro")
        return not (arbitro and not arbitro.get("ok"))
    # Sin clave o apagado por parámetro el resultado es estable; por cuota no.
    return veri.get("motivo") != MOTIVO_CUOTA


def procesar(datos, contexto, verificar):
    """Pipeline completo y sincrónico. Corre fuera del event loop."""
    img = _abrir_imagen(datos)
    local = clasificar_local(img)
    activar = (verificador.disponible() if verificar == "auto"
               else verificar not in ("0", "false", "no"))
    sin_cuota = activar and verificador.disponible() and not _hay_cuota()
    if sin_cuota:
        activar = False

    if activar and verificador.disponible():
        veri = verificador.verificar(img, CATEGORIAS, local, contexto)
        categorias = veri["confirmadas"]
        en_duda = veri["en_duda"]
        ctx_cats = veri["categorias_contexto"]
        descripcion = veri["descripcion"]
    else:
        if sin_cuota:
            motivo = MOTIVO_CUOTA
        elif not verificador.disponible():
            motivo = "falta OPENROUTER_API_KEY"
        else:
            motivo = "desactivada por parámetro"
        veri = {"activa": False, "motivo": motivo}
        categorias = [{"key": p["key"], "nombre": p["nombre"],
                       "gravedad": (local["gravedad"] or {}).get("value"),
                       "fuentes": ["modelo_local"]}
                      for p in local["predichas"] if p["key"] != "sin_problema"]
        en_duda, ctx_cats, descripcion = [], [], None

    # El veredicto primero; todo lo interno queda en "detalle".
    problemas = [c for c in categorias if c["key"] not in verificador.PRESENCIA]
    elementos = [{"key": c["key"], "nombre": c["nombre"]}
                 for c in categorias if c["key"] in verificador.PRESENCIA]
    gravedades = [c["gravedad"] for c in problemas if c.get("gravedad")]
    return {
        "hay_problema": bool(problemas),
        "gravedad_maxima": max(gravedades) if gravedades else None,
        "problemas": problemas,
        "descripcion": descripcion,
        "categorias_contexto": ctx_cats,
        "elementos_detectados": elementos,
        "en_duda": en_duda,
        "detalle": {"modelo_local": local, "verificacion": veri},
    }


app = FastAPI(title="Ojo Urbano")


@app.middleware("http")
async def guardias(request, call_next):
    """Token, tamaño declarado y límite por IP ANTES de parsear el multipart.

    La ruta se saca de scope["path"], que es la MISMA que usa el router. Con
    request.url.path no alcanza: se arma con el header Host, así que un
    "Host: evil?" deja el path en "" y el pedido esquiva todas las guardas
    mientras el router igual despacha /clasificar.
    """
    ruta = request.scope.get("path", "").rstrip("/")
    if request.method == "POST" and ruta.endswith("/clasificar"):
        if API_TOKEN and not hmac.compare_digest(
                request.headers.get("x-api-token", ""), API_TOKEN):
            return JSONResponse({"detail": "token inválido o ausente"}, status_code=401)
        declarado = request.headers.get("content-length", "")
        if not declarado.isdigit():
            # Sin Content-Length (cuerpo chunked) el techo de tamaño no se
            # puede aplicar antes de que el parser multipart lea todo. Los
            # navegadores y curl siempre lo mandan en un multipart.
            return JSONResponse(
                {"detail": "hace falta Content-Length"}, status_code=411)
        # El Content-Length mide el multipart entero (boundaries, headers y el
        # contexto), no solo la foto: sin este margen una foto justo en el
        # límite se rechazaría por el envoltorio. El techo exacto de la foto lo
        # aplica _leer_acotado.
        if int(declarado) > MAX_BYTES + MARGEN_MULTIPART:
            return JSONResponse(
                {"detail": f"la foto supera el límite de {MAX_BYTES // (1024 * 1024)} MB"},
                status_code=413)
        if not _permitir(_ip_cliente(request)):
            return JSONResponse(
                {"detail": "demasiados pedidos; probá de nuevo más tarde"},
                status_code=429)
    return await call_next(request)


@app.get("/salud")
def salud():
    canonicas = sorted({verificador.FOLD.get(k, k) for k in clases})
    return {"ok": True, "clases": canonicas,
            "verificacion": verificador.disponible(),
            "verificadores": verificador.VERIFICADORES,
            "arbitro": verificador.ARBITRO or None}


@app.post("/clasificar")
async def clasificar(request: Request, file: UploadFile = File(...),
                     verificar: str = "auto", contexto: str = Form("")):
    datos = await _leer_acotado(file)
    contexto = (contexto or "").strip()[:500]

    # La misma foto con el mismo contexto no se vuelve a pagar.
    huella = hashlib.sha256(
        datos + b"\x00" + contexto.encode() + b"\x00" + verificar.encode()).hexdigest()
    if huella in _cache:
        _cache.move_to_end(huella)
        return JSONResponse(_cache[huella])

    # Acá hubo una deduplicación de pedidos en vuelo (que el segundo pedido de
    # la misma foto se colgara del primero en vez de pagarla dos veces). Se
    # sacó a propósito: hacía esperar a los waiters reteniendo cada uno su
    # copia de hasta MAX_BYTES, que con los defaults son ~600 MB de una sola
    # IP, mucho peor que el problema que resolvía. Con CONCURRENCIA=1 el cupo
    # ya evita el pipeline duplicado (el segundo se lleva un 503); subirla
    # afloja esa garantía y se puede volver a pagar una foto simultánea.
    respuesta = await _clasificar_una(datos, contexto, verificar)

    if CACHE_MAX > 0 and _cacheable(respuesta):
        _cache[huella] = respuesta
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return JSONResponse(respuesta)


async def _clasificar_una(datos, contexto, verificar):
    """Corre el pipeline con el cupo tomado, fuera del event loop."""
    # Sin techo de concurrencia, cada pedido encolado retiene su imagen en RAM
    # y suma minutos de espera. Mejor rechazar rápido.
    if not _cupos.acquire(blocking=False):
        raise HTTPException(503, "el servidor está ocupado; reintentá en un momento")

    def trabajo():
        # El cupo se suelta acá, en el hilo, para que siga tomado si la
        # corrutina que espera se cancela (cliente que corta la conexión):
        # cancelar el await NO corta el hilo, que sigue quemando CPU.
        try:
            return procesar(datos, contexto, verificar)
        finally:
            _cupos.release()

    # El pipeline es sincrónico y tarda 25-60 s: fuera del event loop, o
    # bloquea /salud, la portada y cualquier otro pedido mientras corre.
    # Se usa el pool propio (y no asyncio.to_thread) para poder preguntarle al
    # future si el trabajo llegó a arrancar: si se cancela mientras todavía
    # estaba encolado, el finally de trabajo() nunca corre y el cupo se
    # perdería para siempre.
    try:
        tarea = _pool.submit(trabajo)
    except RuntimeError:
        _cupos.release()
        raise HTTPException(503, "el servidor se está apagando")
    try:
        return await asyncio.wrap_future(tarea)
    except asyncio.CancelledError:
        # cancel() devuelve True solo si seguía en la cola: ahí es seguro
        # soltar, porque trabajo() no va a correr nunca. Si devuelve False ya
        # arrancó y lo suelta su propio finally.
        if tarea.cancel():
            _cupos.release()
        raise


@app.get("/", response_class=HTMLResponse)
def portada():
    return PAGINA


PAGINA = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ojo Urbano · clasificador de incidencias urbanas</title>
<style>
  :root{--bg:#f4f4f4;--surface:#fff;--soft:#fafafa;--ink:#111;--muted:#666;
        --muted2:#8a8a8a;--line:#dedede;--line2:#bdbdbd;--bar:#e8e8e8}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;
       background:linear-gradient(180deg,rgba(255,255,255,.86),rgba(255,255,255,0) 320px),var(--bg);color:var(--ink)}
  .wrap{width:min(920px,100%);margin:0 auto;padding:28px 20px 44px}
  .masthead{margin-bottom:20px;padding-bottom:22px;border-bottom:1px solid var(--line)}
  h1{font-size:19px;letter-spacing:.14em;margin:0}
  .tagline{margin-top:3px;color:var(--muted);font-size:13px;font-weight:700}
  .sub{color:var(--muted2);font-size:13px;margin-top:5px;max-width:620px}
  .mode{display:inline-block;font-size:12px;padding:3px 10px;border-radius:20px;
        border:1px solid var(--line2);background:var(--surface);color:var(--muted)}
  .ctxlabel{display:block;margin-top:18px;font-size:12px;text-transform:uppercase;
            letter-spacing:.07em;color:var(--muted);font-weight:700}
  #ctx{width:100%;margin-top:6px;padding:9px 11px;border:1px solid var(--line2);
       border-radius:8px;font:inherit;background:var(--surface);color:var(--ink)}
  #ctx::placeholder{color:var(--muted2)}
  .ctxhint{font-size:12px;color:var(--muted2);margin-top:4px}
  #drop{margin-top:12px;border:1px dashed var(--line2);border-radius:8px;background:var(--surface);
        padding:36px 20px;text-align:center;cursor:pointer;transition:.15s}
  #drop:hover,#drop:focus-visible,#drop.over{border-color:var(--ink);background:var(--soft);outline:none}
  #drop p{margin:6px 0;color:var(--muted)}
  #drop strong{color:var(--ink)}
  .grid{display:grid;grid-template-columns:300px minmax(0,1fr);gap:22px;margin-top:22px;align-items:start}
  #preview{width:100%;border-radius:8px;border:1px solid var(--line);background:#111;display:none}
  .espera{display:none;border:1px solid var(--line);border-radius:8px;background:var(--surface);
          padding:16px 18px;gap:14px;align-items:flex-start}
  .espera b{font-size:14px}
  .esptxt{font-size:12.5px;color:var(--muted);margin-top:3px}
  .spin{width:18px;height:18px;border:2px solid var(--line2);border-top-color:var(--ink);
        border-radius:50%;flex:none;margin-top:2px;animation:gira .8s linear infinite}
  @keyframes gira{to{transform:rotate(360deg)}}
  .res{display:none;border:1px solid var(--line);border-radius:8px;background:var(--surface);padding:18px}
  .res h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:0 0 10px}
  .res h3{font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:16px 0 7px}
  .concl{font-size:14.5px;font-weight:600;margin-bottom:12px}
  .cats{display:flex;flex-wrap:wrap;gap:8px}
  .cat{border:1px solid var(--line2);border-radius:8px;background:var(--soft);padding:8px 12px}
  .cat b{display:block;font-size:14px}
  .cat span{font-size:12px;color:var(--muted)}
  .cat.ctx{border-style:dashed;background:var(--surface)}
  .desc{font-size:13.5px;background:var(--soft);border:1px solid var(--line);
        border-radius:8px;padding:10px 12px}
  .mini{font-size:12px;color:var(--muted2);margin-top:6px}
  .estado{font-size:12.5px;color:var(--muted);margin:8px 0}
  .voto{font-size:12.5px;color:var(--muted);margin:3px 0}
  .voto b{color:var(--ink);font-weight:600}
  .row{margin:9px 0}
  .row .name{display:flex;justify-content:space-between;font-size:13px;margin-bottom:3px}
  .row .name .pct{color:var(--muted)}
  .track{height:8px;background:var(--bar);border-radius:999px;overflow:hidden}
  .track>i{display:block;height:100%;background:var(--ink);border-radius:999px}
  .row:not(:first-child) .track>i{background:#747474}
  .err{display:none;font-size:13px;font-weight:600;margin-top:12px;border:1px solid var(--line2);
       border-radius:8px;background:var(--surface);padding:10px 12px}
  .reenviar{width:100%;margin-top:10px;padding:8px 12px;font:13px inherit;font-weight:600;
       border:1px solid var(--line2);border-radius:8px;background:var(--surface);
       color:var(--muted);cursor:pointer}
  .reenviar:hover{color:var(--ink);border-color:var(--ink)}
  .reenviar.primario{background:var(--ink);border-color:var(--ink);color:#fff}
  .reenviar.primario:hover{color:#fff;opacity:.85}
  details.det{margin-top:18px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
  details.det>summary{cursor:pointer;padding:10px 14px;font-size:13px;font-weight:600;color:var(--muted);
       list-style-position:inside}
  details.det[open]>summary{border-bottom:1px solid var(--line);color:var(--ink)}
  details.det>.detbody{padding:14px}
  .res details.det{margin-top:16px}
  .res details.det>.detbody{padding:12px 14px}
  .endpoint{font:13px ui-monospace,Menlo,monospace;background:var(--surface);
            border:1px solid var(--line);border-radius:8px;padding:8px 11px;margin-bottom:10px}
  .apinote{font-size:12.5px;color:var(--muted);margin-bottom:12px}
  .tabs{display:flex;gap:6px;margin-bottom:8px}
  .tab{padding:5px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface);
       cursor:pointer;font:13px inherit;color:var(--muted)}
  .tab.active{color:#fff;border-color:var(--ink);background:var(--ink)}
  pre.code,pre.json{background:#111;color:#f2f2f2;padding:14px;border-radius:8px;overflow:auto;
       font:12.5px/1.55 ui-monospace,Menlo,monospace;margin:0}
  pre.code{display:none}
  pre.code.active{display:block}
  .copybtn{font:12px inherit;border:1px solid var(--line2);border-radius:6px;background:var(--surface);
       color:var(--muted);padding:4px 10px;cursor:pointer;margin-bottom:8px}
  .copybtn:hover{color:var(--ink);border-color:var(--ink)}
  @media(max-width:700px){.grid{grid-template-columns:1fr}}
</style></head>
<body><div class="wrap">
  <header class="masthead">
    <h1>OJO URBANO</h1>
    <div class="tagline">Reconocimiento visual de incidencias urbanas</div>
    <div class="sub">Subí una foto de un problema en la vía pública (basura fuera del contenedor, muebles
      abandonados, veredas rotas, vehículos sobre la ciclovía) y el sistema identifica qué reporte
      corresponde. La foto y el contexto se envían a modelos de IA de terceros vía OpenRouter para la
      verificación cruzada.</div>
  </header>
  <span id="modechip" class="mode">cargando…</span>

  <label class="ctxlabel" for="ctx">Contexto vecinal (opcional)</label>
  <input id="ctx" type="text" maxlength="500"
         placeholder="Contá algo que quizá no se vea en la foto, p. ej. «todo huele mal» o «hay ratas»">
  <div class="ctxhint">Sirve de pista para interpretar la foto y para sugerir reportes. Lo que no se vea
    en la foto vuelve como sugerencia aparte, nunca como confirmación. Podés escribirlo antes o después
    de elegir la foto.</div>

  <div id="drop" role="button" tabindex="0" aria-label="Elegir una foto para analizar">
    <p><strong>Arrastrá una foto acá</strong> o hacé clic para elegir</p>
    <p>JPG / PNG / WEBP · después tocá «Analizar»</p>
    <input id="file" type="file" accept="image/*" hidden>
  </div>
  <div class="err" id="err"></div>

  <div class="grid">
    <div>
      <img id="preview" alt="Foto subida">
      <button id="reenviar" class="reenviar" style="display:none">&#8635; Reanalizar esta foto</button>
    </div>
    <div>
      <div class="espera" id="espera">
        <div class="spin" aria-hidden="true"></div>
        <div>
          <div aria-live="polite">
            <b>Analizando la foto</b>
            <div class="esptxt">Modelo local + verificación cruzada con dos modelos de visión.
              Suele tardar entre 20 y 60 segundos.</div>
          </div>
          <div class="esptxt" id="elapsed" aria-hidden="true">0 s</div>
        </div>
      </div>
      <div class="res" id="res">
        <h2>Resultado</h2>
        <div class="concl" id="concl"></div>
        <div class="cats" id="cats"></div>
        <div id="descwrap" style="display:none">
          <h3>Descripción de la escena</h3>
          <div class="desc" id="desc"></div>
        </div>
        <div id="ctxwrap" style="display:none">
          <h3>Sugerido por el contexto vecinal</h3>
          <div class="cats" id="ctxcats"></div>
          <div class="mini">Surgen del texto que escribiste; la foto no las confirma y no suman a la gravedad.</div>
        </div>
        <div id="preswrap" style="display:none">
          <h3>Elementos detectados</h3>
          <div class="cats" id="prescats"></div>
          <div class="mini">Contenedores visibles en la foto; se informan aunque no tengan problemas.</div>
        </div>
        <div id="dudawrap" style="display:none">
          <h3>Sin consenso</h3>
          <div class="mini" id="dudas"></div>
        </div>
        <details class="det">
          <summary>Cómo se obtuvo este resultado</summary>
          <div class="detbody">
            <div class="estado" id="estado"></div>
            <div id="votos"></div>
            <h3>Predicciones del modelo local (top 5)</h3>
            <div class="mini" style="margin:0 0 8px">Probabilidades del clasificador propio. La confirmación
              final surge del consenso: una categoría queda confirmada con 2 de 3 fuentes (modelo local +
              dos modelos de visión); las de una sola fuente las decide un árbitro de texto.</div>
            <div id="bars"></div>
          </div>
        </details>
      </div>
    </div>
  </div>

  <details class="det" id="jsonwrap" style="display:none">
    <summary>Ver respuesta JSON <span id="jsonsize" style="font-weight:400"></span></summary>
    <div class="detbody">
      <button class="copybtn" id="copyjson">Copiar JSON</button>
      <pre class="json" id="json"></pre>
    </div>
  </details>

  <details class="det">
    <summary>API para desarrolladores</summary>
    <div class="detbody">
      <div class="endpoint"><b>POST</b> <span id="ep"></span> · multipart/form-data, campo <b>file</b> · campo opcional <b>contexto</b></div>
      <div class="apinote">Parámetro opcional <b>?verificar=</b> <b>auto</b> (default: verifica si hay clave
        de OpenRouter) · <b>1</b> (forzar verificación) · <b>0</b> (solo modelo local). Las categorías que el
        contexto describe pero la foto no confirma vuelven en <b>final.categorias_contexto</b>.</div>
      <div class="tabs" id="tabs">
        <button class="tab active" data-l="curl">curl</button>
        <button class="tab" data-l="python">Python</button>
        <button class="tab" data-l="js">JavaScript</button>
      </div>
      <pre class="code active" id="code-curl"></pre>
      <pre class="code" id="code-python"></pre>
      <pre class="code" id="code-js"></pre>
    </div>
  </details>
</div>
<script>
const $=s=>document.querySelector(s);
// Prefijo-agnóstico: funciona en la raíz (http://localhost:8080/) y detrás de
// un proxy con prefijo (https://dominio/ojourbano/). Bajo prefijo, las rutas
// llevan barra final para que el proxy las sirva sin redirecciones (una 301
// convierte el POST en GET y rompe la subida).
const O=location.origin+location.pathname.replace(/\/$/,'');
const SUF=location.pathname.replace(/\/$/,'')?'/':'';
$('#ep').textContent=O+'/clasificar'+SUF;
const GRAV={1:'mínima',2:'leve',3:'alta',4:'grave',5:'muy grave'};
const PRESENCIA=['contenedor_secos','contenedor_humedos_lateral','contenedor_humedos_bilateral'];
const SNIP={
 curl:`curl -s -F "file=@foto.jpg" -F "contexto=vidrios rotos en la vereda" ${O}/clasificar`,
 python:`import requests\n\nwith open("foto.jpg", "rb") as f:\n    r = requests.post("${O}/clasificar", files={"file": f},\n                      data={"contexto": "vidrios rotos en la vereda"})\ndata = r.json()\nif data["hay_problema"]:\n    print(data["descripcion"])\n    for p in data["problemas"]:\n        print(p["key"], p["gravedad"], p["fuentes"])\nelse:\n    print("sin problema:", data["descripcion"])`,
 js:`const fd = new FormData();\nfd.append("file", fileInput.files[0]);\nfd.append("contexto", "vidrios rotos en la vereda"); // opcional\nconst res = await fetch("${O}/clasificar", { method: "POST", body: fd });\nconst d = await res.json();\nif (d.hay_problema) console.log(d.problemas, d.descripcion);`
};
['curl','python','js'].forEach(l=>$('#code-'+l).textContent=SNIP[l]);
$('#tabs').onclick=e=>{const b=e.target.closest('.tab');if(!b)return;
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t===b));
  document.querySelectorAll('.code').forEach(c=>c.classList.remove('active'));
  $('#code-'+b.dataset.l).classList.add('active');};
fetch(O+'/salud'+SUF).then(r=>r.json()).then(h=>{
  const chip=$('#modechip');
  chip.textContent=h.verificacion?'Análisis completo activo':'Modo básico: solo modelo local, sin verificación cruzada';
  chip.title=h.verificacion?('Verificadores: '+h.verificadores.join(' + ')+(h.arbitro?' · árbitro: '+h.arbitro:'')):'Configurá OPENROUTER_API_KEY para activar la verificación';});
const drop=$('#drop'),file=$('#file');
drop.onclick=()=>file.click();
drop.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();file.click();}});
// Toda la página acepta drop: si la segunda foto cae fuera del recuadro (por
// ejemplo sobre el resultado), el navegador ya no navega al archivo.
['dragover','dragenter'].forEach(e=>document.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(e=>document.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('over')}));
document.addEventListener('drop',ev=>{if(ev.dataTransfer.files[0])seleccionar(ev.dataTransfer.files[0])});
file.addEventListener('change',()=>{if(file.files[0]){seleccionar(file.files[0]);file.value='';}});
// Elegir la foto NO arranca el análisis: deja el preview y el botón Analizar,
// así se puede escribir o ajustar el contexto vecinal antes de mandar.
function seleccionar(f){
  ultimoArchivo=f;
  const img=$('#preview');
  if(img.src.startsWith('blob:'))URL.revokeObjectURL(img.src);
  img.src=URL.createObjectURL(f);img.style.display='block';
  const b=$('#reenviar');
  b.textContent='Analizar esta foto';b.classList.add('primario');b.style.display='block';
}
$('#copyjson').onclick=()=>{navigator.clipboard.writeText($('#json').textContent).then(()=>{
  $('#copyjson').textContent='Copiado ✓';setTimeout(()=>$('#copyjson').textContent='Copiar JSON',1500);});};
const chip=(c,extra)=>`<div class="cat${extra?' ctx':''}"><b>${c.nombre}</b>${c.gravedad?`<span title="${(c.fuentes||[]).join(', ')}">${c.gravedad}/5 · ${GRAV[c.gravedad]||''} · ${(c.fuentes||[]).length} fuente${(c.fuentes||[]).length!==1?'s':''}</span>`:''}</div>`;
let ctrl=null,cronoIv=null,ultimoArchivo=null;
$('#reenviar').onclick=()=>{if(ultimoArchivo)enviar(ultimoArchivo);};
function enviar(f){
  if(ctrl)ctrl.abort();
  if(cronoIv)clearInterval(cronoIv);
  ctrl=new AbortController();
  ultimoArchivo=f;
  $('#reenviar').style.display='none';
  $('#err').style.display='none';$('#err').textContent='';
  $('#res').style.display='none';$('#cats').innerHTML='';$('#bars').innerHTML='';
  $('#estado').textContent='';$('#votos').innerHTML='';$('#concl').textContent='';
  ['descwrap','ctxwrap','preswrap','dudawrap'].forEach(id=>$('#'+id).style.display='none');
  const jw=$('#jsonwrap');jw.style.display='none';jw.removeAttribute('open');
  $('#json').textContent='';$('#jsonsize').textContent='';
  $('#espera').style.display='flex';
  const t0=Date.now();
  cronoIv=setInterval(()=>{$('#elapsed').textContent=Math.round((Date.now()-t0)/1000)+' s'},1000);
  $('#elapsed').textContent='0 s';
  const img=$('#preview');
  if(img.src.startsWith('blob:'))URL.revokeObjectURL(img.src);
  img.src=URL.createObjectURL(f);img.style.display='block';
  const fd=new FormData();fd.append('file',f);
  const ctx=$('#ctx').value.trim();if(ctx)fd.append('contexto',ctx);
  fetch(O+'/clasificar'+SUF,{method:'POST',body:fd,signal:ctrl.signal}).then(r=>{if(!r.ok)throw new Error('no pude leer la imagen');return r.json()})
   .then(d=>{
     clearInterval(cronoIv);
     $('#espera').style.display='none';$('#res').style.display='block';
     const probs=d.problemas;
     $('#concl').textContent=!d.hay_problema
       ?'No se identificaron problemas en la foto.'
       :(probs.length===1?'Se identificó 1 incidencia':'Se identificaron '+probs.length+' incidencias')
         +(d.gravedad_maxima?` · gravedad máxima ${d.gravedad_maxima}/5 (${GRAV[d.gravedad_maxima]})`:'')+'.';
     $('#cats').innerHTML=probs.map(c=>chip(c)).join('');
     if(d.descripcion){$('#desc').textContent=d.descripcion;$('#descwrap').style.display='block';}
     const cc=d.categorias_contexto||[];
     const RESPALDO={compatible:'la foto es compatible con el reclamo',neutral:'no visible en la foto',contradice:'la foto lo contradice'};
     if(cc.length){$('#ctxcats').innerHTML=cc.map(c=>`<div class="cat ctx"><b>${c.nombre}</b><span>${RESPALDO[c.respaldo_visual]||'según el contexto'}</span></div>`).join('');$('#ctxwrap').style.display='block';}
     const pres=d.elementos_detectados||[];
     if(pres.length){$('#prescats').innerHTML=pres.map(c=>`<div class="cat"><b>${c.nombre}</b></div>`).join('');$('#preswrap').style.display='block';}
     if(d.en_duda.length){$('#dudas').textContent='Reportadas por una sola fuente y sin decisión del árbitro: '
       +d.en_duda.map(k=>k.replace(/_/g,' ')).join(', ')+'. No se incluyen entre las confirmadas.';
       $('#dudawrap').style.display='block';}
     const v=d.detalle.verificacion;
     $('#estado').textContent=v.activa
       ?'Verificación cruzada completada en '+Math.round((Date.now()-t0)/1000)+' s.'
       :'Sin verificación cruzada ('+v.motivo+'): resultado solo del modelo local.';
     if(v.activa){$('#votos').innerHTML=v.verificadores.map(x=>x.ok
       ?`<div class="voto"><b>${x.modelo}</b>: ${x.categorias.length?x.categorias.map(c=>c.key.replace(/_/g,' ')).join(', '):'sin hallazgos'}</div>`
       :`<div class="voto"><b>${x.modelo}</b>: no respondió</div>`).join('')
       +(v.arbitro&&v.arbitro.ok&&v.arbitro.decisiones.length
         ?`<div class="voto"><b>árbitro</b>: ${v.arbitro.decisiones.map(dd=>dd.key.replace(/_/g,' ')+' '+(dd.veredicto==='confirmar'?'✓':'✗')).join(', ')}</div>`:'');}
     $('#bars').innerHTML=d.detalle.modelo_local.top5.map(t=>`
       <div class="row"><div class="name"><span>${t.nombre}</span><span class="pct">${Math.round(t.score*100)}%</span></div>
       <div class="track"><i style="width:${Math.max(2,Math.round(t.score*100))}%"></i></div></div>`).join('');
     const jtxt=JSON.stringify(d,null,2);
     $('#json').textContent=jtxt;
     $('#jsonsize').textContent='· '+(new Blob([jtxt]).size/1024).toFixed(1)+' KB';
     $('#jsonwrap').style.display='block';
     mostrarReanalizar();
   }).catch(e=>{if(e.name==='AbortError')return;
     clearInterval(cronoIv);
     $('#espera').style.display='none';
     $('#err').textContent=e.message+' · Reintentá con el botón o probá otra foto.';
     $('#err').style.display='block';
     mostrarReanalizar();});
}
function mostrarReanalizar(){
  const b=$('#reenviar');
  b.textContent='↻ Reanalizar esta foto';b.classList.remove('primario');b.style.display='block';
}
</script></body></html>"""


if __name__ == "__main__":
    estado = "activa" if verificador.disponible() else "desactivada (sin OPENROUTER_API_KEY)"
    print(f"Ojo Urbano -> http://{HOST}:{PORT}  ·  verificación {estado}")
    uvicorn.run(app, host=HOST, port=PORT)
