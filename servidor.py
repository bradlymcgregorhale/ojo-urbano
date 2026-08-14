#!/usr/bin/env python3
"""Ojo Urbano: API de clasificación de fotos de incidencias urbanas.

Un modelo propio (embeddings CLIP + DINOv2 + SigLIP2 con cabezal de regresión
logística multi-etiqueta, entrenado con miles de fotos reales etiquetadas a
mano) clasifica la foto localmente. Si hay una OPENROUTER_API_KEY configurada,
varios modelos de visión (tres por defecto) verifican el resultado de forma
independiente, y lo que ve una sola fuente queda como "posible" en vez de
confirmarse. El contexto que escribe el vecino puede sostener un reclamo por
sí solo cuando la foto no sirve (ver verificador.py).

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
import re
import secrets
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
from PIL import Image, ImageOps
from sentence_transformers import SentenceTransformer

import verificador

AQUI = Path(__file__).resolve().parent
MODELO = AQUI / "model.joblib"
CATEGORIAS = json.loads((AQUI / "categorias.json").read_text())
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
UMBRAL = float(os.environ.get("UMBRAL", "0.5"))
GRAV_MAX = 5
# Versión del contrato de la respuesta. Se sube cuando cambia el SIGNIFICADO de
# un campo que ya existía, no cuando se agrega uno nuevo. v2: hay_problema pasó
# a incluir lo que sostiene solo el texto del vecino, problemas dejó de ser
# solo visual, apareció "contexto_vecinal" como fuente, y el árbitro dejó de
# confirmar hallazgos de una sola fuente. v3: la respuesta viene resumida (los
# modelos de visión en "modelos", sin el ranking completo del modelo local ni
# los campos repetidos); el volcado entero se pide con ?detalle=1. v4: el
# modelo local desaparece de la respuesta (sigue corriendo y contando como
# fuente del consenso, pero su voto no se publica); no hay más ?detalle=1;
# "fuentes" pasa a ser un conteo; hay_problema vuelve a significar problema
# CONFIRMADO (hay_problema == bool(problemas)) y hay_reclamo expresa "el
# vecino pide algo aunque la foto no lo confirme"; el contexto del vecino no
# se devuelve nunca; ?verificar=0 responde degradado en vez de publicar el
# modelo local. Ver README, "Cambios de contrato".
VERSION_API = "4"

# Límites de abuso. Clasificar una foto cuesta 25-60 s de CPU y varias
# llamadas pagas a OpenRouter (una por verificador, tres por defecto, más el
# árbitro y el encaminamiento por texto cuando hacen falta), así que el
# endpoint no puede quedar abierto sin techo.
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
# Fusión escombros: el modelo local (entrenado con fotos reales etiquetadas a
# mano de esta ciudad) distingue escombros embolsados donde los modelos de
# visión generalistas no llegan (medido: 7 modelos, 2 rúbricas y 2
# resoluciones = 0 detecciones en fotos nocturnas donde el local da >=0.95;
# y el local a ese umbral acertó 43/43 contra la etiqueta humana). Regla
# acotada a ESA única categoría; ver procesar().
FUSION_ESCOMBROS = os.environ.get(
    "FUSION_ESCOMBROS", "1").strip().lower() not in ("0", "false", "no")
FUSION_ESCOMBROS_UMBRAL = float(os.environ.get("FUSION_ESCOMBROS_UMBRAL", "0.95"))
FUSION_ESCOMBROS_RECO_BAJA = float(os.environ.get("FUSION_ESCOMBROS_RECO_BAJA", "0.2"))
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
CACHE_MAX = int(os.environ.get("CACHE_MAX", "128"))
# Cola de espera por el cupo: en vez de rebotar con 503 apenas hay otra foto
# en proceso, un pedido espera su turno un rato acotado. La espera máxima más
# el trabajo (25-60 s) tiene que quedar bajo el techo de ~100 s del proxy
# (Cloudflare corta la conexión del cliente); los que no entran en la cola o
# se cansan de esperar siguen recibiendo 503, ahora con Retry-After.
# COLA_MAX acota la memoria retenida (cada pedido en espera guarda su foto:
# COLA_MAX x MAX_BYTES con los defaults son ~30 MB, no los ~600 MB que hacían
# inviable la deduplicación en vuelo que se sacó en su momento).
COLA_MAX = max(0, int(os.environ.get("COLA_MAX", "3")))
ESPERA_CUPO = max(0, int(os.environ.get("ESPERA_CUPO", "30")))
# Trabajos asíncronos: POST /trabajos devuelve un id al instante y el cliente
# consulta GET /trabajos/{id} (o /trabajos?id=...) hasta que esté listo. Es la
# vía para lotes de varias fotos: la sincrónica obliga a sostener la conexión
# los 25-60 s de trabajo y el proxy la corta a los ~100 s, así que la
# profundidad de cola real queda atada a ese techo; acá la conexión se suelta
# al toque y la espera vive en el servidor. Los pedidos sincrónicos tienen
# PRIORIDAD sobre los trabajos encolados al asignar el cupo: un lote grande no
# puede dejar sin servicio al que espera con la conexión abierta (a lo sumo lo
# demora un trabajo ya en curso).
TRABAJOS_MAX = max(1, int(os.environ.get("TRABAJOS_MAX", "10")))
# Techo por IP de trabajos pendientes: sin esto, un solo cliente llena los
# TRABAJOS_MAX lugares sin sostener ni una conexión. La página pública manda
# de a pocos y reintenta sola, así que un techo chico no la rompe.
TRABAJOS_POR_IP = max(1, int(os.environ.get("TRABAJOS_POR_IP", "4")))
# Cuánto puede esperar un trabajo en cola antes de darse por vencido, y cuánto
# se retiene el resultado terminado para que el cliente lo pase a buscar.
TRABAJO_ESPERA = max(60, int(os.environ.get("TRABAJO_ESPERA", "900")))
TRABAJO_TTL = max(60, int(os.environ.get("TRABAJO_TTL", "1800")))
# Techo de resultados terminados retenidos (además del TTL): sin él, un
# goteo sostenido de trabajos acumula registros más rápido de lo que vencen.
TRABAJOS_LISTOS_MAX = max(1, int(os.environ.get("TRABAJOS_LISTOS_MAX", "200")))
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
# La caché la escribe el HILO del pipeline al terminar (no la corrutina del
# pedido: si el cliente aborta, la corrutina muere y el resultado se
# perdería, que era exactamente lo que pasaba con el patrón abortar+reintentar
# del demo). La leen el event loop y los hilos: siempre bajo lock.
_cache_lock = threading.Lock()
# Colas FIFO de espera por el cupo. Solo las toca el event loop (un único
# hilo), por eso una deque pelada sin lock alcanza y la justicia FIFO no
# tiene carreras. Son dos carriles: _cola para pedidos sincrónicos (que
# sostienen la conexión y no pueden esperar mucho) y _cola_trab para los
# trabajos asíncronos. El carril sincrónico tiene prioridad: mientras haya
# un sincrónico formado, ningún trabajo encolado toma el cupo.
_cola = collections.deque()
_cola_trab = collections.deque()
# El cupo lo suelta el HILO cuando termina de verdad, no la corrutina que lo
# espera: cancelar un await NO corta el thread, así que soltarlo ahí dejaría
# entrar pedidos nuevos mientras el pipeline anterior sigue quemando CPU.
_cupos = threading.BoundedSemaphore(CONCURRENCIA)
# Techo de última instancia. El deadline de verificador.py acota las lecturas
# HTTP, pero no TODO se puede interrumpir desde afuera: la resolución DNS, por
# ejemplo, pasa adentro de la conexión y no hay forma de cortarla. Si alguna
# vez un hilo queda trabado igual, el cupo tomado dejaría el servicio en 503
# permanente, que es exactamente el incidente que ya pasó una vez. Pasado este
# techo el trabajo se da por PERDIDO y se devuelve su cupo: el hilo sigue vivo
# (a un hilo de Python no se lo puede matar), pero el servicio se recupera.
# Piso de 120 s: una clasificación normal tarda 25-60 s, así que un techo más
# bajo declararía perdidos trabajos sanos, pasaría de CONCURRENCIA y llenaría
# la reserva sin que hubiera fallado nada.
TECHO_TRABAJO = max(120, int(os.environ.get("TECHO_TRABAJO", "600")))
# Los hilos perdidos no pueden quedarse con los workers, o el próximo pedido
# esperaría por uno libre en vez de correr. Se deja lugar para unos cuantos.
ABANDONO_MAX = max(1, int(os.environ.get("ABANDONO_MAX", "4")))
_perdidos = {"vivos": 0, "total": 0, "lock": threading.Lock()}
# Tantos hilos como cupos: con el semáforo de admisión, un trabajo aceptado
# siempre encuentra un worker libre y no se queda encolado.
_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=CONCURRENCIA + ABANDONO_MAX, thread_name_prefix="ojo")
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
        # Las fotos de celular suelen venir acostadas, con la rotación solo
        # anotada en el EXIF. convert() entrega los píxeles crudos y pierde
        # esa anotación: sin este paso, los modelos ven la escena (y las
        # patentes) de costado.
        img = ImageOps.exif_transpose(img)
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
        # La segunda mirada de escombros falló en alguna llamada: el "no
        # confirmado" puede ser un corte transitorio, no un veredicto.
        if (veri.get("segunda_mirada") or {}).get("fallo"):
            return False
        # Con árbitro configurado, que queden categorías en duda significa que
        # el arbitraje falló o quedó incompleto (respondió sin decidirlas
        # todas). Cachearlo congela ese resultado a medio hacer para siempre.
        # EXCEPTO las claves de PRESENCIA: esas no se arbitran nunca (un
        # contenedor visto por una sola fuente queda en duda POR DISEÑO, es
        # su estado final) y no deben volver incacheable cada foto donde un
        # solo modelo vio un contenedor.
        if verificador.ARBITRO and [k for k in respuesta.get("en_duda") or []
                                    if k not in verificador.PRESENCIA]:
            return False
        # El encaminamiento del reclamo por texto falló: la respuesta puede
        # ser un falso "no hay problema" por un corte transitorio.
        if veri.get("ruteo_contexto_fallo"):
            return False
        arbitro = veri.get("arbitro")
        return not (arbitro and not arbitro.get("ok"))
    # Sin clave o apagado por parámetro el resultado es estable; por cuota no.
    return veri.get("motivo") != MOTIVO_CUOTA


def _cache_leer(huella):
    with _cache_lock:
        respuesta = _cache.get(huella)
        if respuesta is not None:
            _cache.move_to_end(huella)
        return respuesta


def _cache_guardar(huella, respuesta):
    if CACHE_MAX <= 0 or not _cacheable(respuesta):
        return
    with _cache_lock:
        _cache[huella] = respuesta
        _cache.move_to_end(huella)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


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
        # None = no se pudo juzgar (sin contexto, o los modelos no coincidieron)
        foto_valida = veri.get("foto_valida")
        posibles = veri.get("posibles") or []
        # Si el estado no vino (respuesta vieja o incompleta), se deduce del
        # booleano en vez de asumir "sin_contexto": eso daría un par imposible
        # como foto_valida=True con estado "sin_contexto".
        foto_estado = veri.get("foto_valida_estado") or (
            "corresponde" if foto_valida is True else
            "no_corresponde" if foto_valida is False else "sin_contexto")
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
        en_duda, ctx_cats, descripcion, foto_valida = [], [], None, None
        posibles = []
        # Sin verificación no se evaluó la foto: decirlo, para que nadie
        # interprete el null como "la foto sirve".
        foto_estado = "no_evaluado"

    # El veredicto primero; todo lo interno queda en "detalle".
    problemas = [c for c in categorias if c["key"] not in verificador.PRESENCIA]
    # fuentes se guarda en el objeto interno para que _publica pueda filtrar
    # lo solo-local; el serializador lo saca antes de publicar.
    elementos = [{"key": c["key"], "nombre": c["nombre"],
                  "fuentes": c.get("fuentes") or []}
                 for c in categorias if c["key"] in verificador.PRESENCIA]

    # Si la foto NO corresponde a lo que el vecino contó, no se puede reportar
    # lo que se vea en ella: sería abrir un reclamo por algo que el vecino no
    # pidió, mirando una foto que ya dijimos que no sirve. Los hallazgos no se
    # tiran (quedan en descartados, con el motivo), pero no son el veredicto.
    # POSIBLES: todo lo que PODRÍA ser un reporte pero no está confirmado. Se
    # devuelve siempre, incluso cuando no hay nada definitivo: es justamente
    # ahí donde le sirve a quien consume la API, para repreguntarle al vecino
    # en vez de recibir una respuesta vacía. Cada uno dice de dónde salió.
    for p in posibles:
        p.setdefault("origen", "foto")
    vistos_pos = {p["key"] for p in posibles}
    # lo que el contexto sugiere y la foto no confirmó también es un posible
    for c in ctx_cats:
        if c.get("key") and c["key"] not in vistos_pos:
            vistos_pos.add(c["key"])
            posibles.append({
                "key": c["key"], "nombre": c["nombre"],
                "gravedad": None, "fuentes": ["contexto_vecinal"],
                "origen": "contexto_vecinal",
                "respaldo_visual": c.get("respaldo_visual"),
            })

    # FUSIÓN ESCOMBROS: si el modelo local está prácticamente seguro
    # (>= FUSION_ESCOMBROS_UMBRAL) de que hay escombros y algún verificador
    # vio la misma pila de bolsas (recoleccion confirmada), el material lo
    # reclasifica el especialista: escombros entra a problemas con las
    # fuentes de esa pila más el local, marcado con reclasificado_por. Y si
    # el local además dice que recolección casi no hay (la pila es SOLO
    # escombros), la entrada recoleccion baja a posibles con su motivo.
    # Sin recoleccion confirmada no se dispara: el voto local solo sigue
    # sin publicarse (contrato v4).
    if FUSION_ESCOMBROS and activar:
        prob_local = {p["key"]: p["score"]
                      for p in local.get("probabilidades") or []}
        esc_local = prob_local.get("retiro_escombros", 0.0)
        rec = next((c for c in problemas if c["key"] == "recoleccion"), None)
        ya_esta = any(c["key"] == "retiro_escombros" for c in problemas)
        if esc_local >= FUSION_ESCOMBROS_UMBRAL and rec is not None and not ya_esta:
            fuentes_esc = ["modelo_local"] + [
                f for f in (rec.get("fuentes") or []) if f != "modelo_local"]
            problemas.append({
                "key": "retiro_escombros",
                "nombre": CATEGORIAS.get("retiro_escombros", {}).get(
                    "nombre", "retiro_escombros"),
                "gravedad": rec.get("gravedad"),
                "fuentes": fuentes_esc,
                "reclasificado_por": "modelo_local",
            })
            solo_escombros = (prob_local.get("recoleccion", 1.0)
                              <= FUSION_ESCOMBROS_RECO_BAJA)
            if solo_escombros:
                problemas = [c for c in problemas if c["key"] != "recoleccion"]
                if "recoleccion" not in vistos_pos:
                    vistos_pos.add("recoleccion")
                    posibles.append(dict(
                        rec, origen="foto",
                        motivo="la pila de bolsas fue reclasificada como "
                               "escombros; basura común casi no se detecta"))
            # La descripción la redactó el árbitro con lo que vieron los
            # verificadores ("bolsas de residuos"); sin esta nota quedaría
            # contradiciendo al veredicto reclasificado. Y si el árbitro
            # negó escombros ("no se identifican escombros"), esa frase
            # quedó obsoleta: se quita antes de agregar la nota. En
            # castellano de vecino, sin claves internas.
            if descripcion:
                frases = [f for f in re.split(r"(?<=[.!?])\s+", descripcion)
                          if f and "escombro" not in f.lower()]
                descripcion = " ".join(frases)
            nota = ("El análisis del material indica que las bolsas "
                    "acumuladas contienen escombros de obra, no basura "
                    "domiciliaria común." if solo_escombros else
                    "El análisis del material indica que entre las bolsas "
                    "hay también escombros de obra.")
            descripcion = (descripcion.rstrip() + " " + nota
                           if descripcion else nota)

    descartados = []
    if foto_valida is False:
        # La foto no muestra lo que el vecino reclamó: lo que vieron los
        # modelos es OTRA cosa, no lo que él vino a reportar. Se guarda pero
        # no se reporta, y el reclamo se arma con lo que dijo el vecino.
        if problemas:
            descartados = [dict(c, motivo_descarte="la foto no corresponde a lo "
                                "que el vecino reportó") for c in problemas]
            # No se reportan, pero siguen siendo cosas que podrían ser un
            # reporte: van a posibles marcadas con su origen, para que se
            # entienda que salieron de una foto que no venía al caso.
            for c in descartados:
                if c["key"] not in vistos_pos:
                    vistos_pos.add(c["key"])
                    posibles.append(dict(c, origen="foto_no_relacionada"))
        problemas = list(veri.get("por_contexto") or [])
        if isinstance(veri, dict) and veri.get("confirmadas"):
            veri = dict(veri, confirmadas=[c for c in veri["confirmadas"]
                                           if c["key"] in verificador.PRESENCIA],
                        descartadas_por_foto=[c for c in veri["confirmadas"]
                                              if c["key"] not in verificador.PRESENCIA])

    gravedades = [c["gravedad"] for c in problemas if c.get("gravedad")]
    salida = {
        "version": VERSION_API,
        # Sobre el objeto INTERNO. _publica() recalcula los dos sobre lo que
        # queda visible después de filtrar lo solo-local, y ahí valen las
        # invariantes del contrato: hay_problema == bool(problemas) y
        # hay_reclamo == bool(problemas or categorias_contexto).
        "hay_problema": bool(problemas),
        "hay_reclamo": bool(problemas) or bool(ctx_cats),
        "gravedad_maxima": max(gravedades) if gravedades else None,
        "problemas": problemas,
        "descripcion": descripcion,
        "categorias_contexto": ctx_cats,
        "foto_valida": foto_valida,
        "foto_valida_estado": foto_estado,
        "descartados_por_foto": descartados,
        # Lo que vio UNA sola fuente. No es un problema confirmado: se ofrece
        # para que el consumidor repregunte, no para reportarlo como un hecho.
        "posibles": posibles,
        "elementos_detectados": elementos,
        "en_duda": en_duda,
        "detalle": {"modelo_local": local, "verificacion": veri},
    }
    # Patente leída de la chapa del vehículo reportado (dos lectores
    # coincidentes; ver README). Campo aditivo: ausente cuando no la hay.
    if veri.get("patente"):
        salida["patente"] = veri["patente"]
    return salida


def _terminos_prohibidos():
    """Nombres internos que un texto público jamás debe contener."""
    modelos = [m for m in (list(verificador.VERIFICADORES) + [verificador.ARBITRO]) if m]
    partes = [re.escape(m) for m in modelos]
    # también el nombre pelado, sin el proveedor: "gpt-5-mini" a secas
    partes += [re.escape(m.split("/", 1)[1]) for m in modelos if "/" in m]
    # Solo frases atadas al MECANISMO de clasificación. Genéricos como
    # "sistema interno" o "fuente local" describen cosas reales de la vía
    # pública (el sistema interno de un semáforo, una fuente) y borrarían
    # descripciones válidas.
    partes += [r"modelo[_ ]local", r"\bscore\b", r"probabilidad(?:es)?\s+local(?:es)?",
               r"clasificador\s+(?:local|propio|interno)", r"modelo\s+interno"]
    return re.compile("|".join(partes), re.IGNORECASE)


_MOTIVO_GENERICO = ("Sin evidencia visual suficiente: ningún análisis de la foto "
                    "describe esta categoría con un objeto concreto.")


def _sanear_motivo(texto):
    """Backstop del prompt: si el motivo del árbitro nombra el mecanismo
    interno (modelos, scores, "modelo local"), se reemplaza entero por uno
    genérico en vez de operarlo palabra por palabra."""
    if not texto:
        return texto
    return _MOTIVO_GENERICO if _terminos_prohibidos().search(texto) else texto


def _publica(r):
    """Contrato v4: el veredicto, lo que dijo cada modelo de visión, y lo que
    podría ser un reporte. El modelo local no aparece: sigue corriendo y
    contando como fuente del consenso, pero su voto no se publica (README,
    "Cambios de contrato").
    """
    veri = (r.get("detalle") or {}).get("verificacion") or {}
    pub = {k: v for k, v in r.items() if k != "detalle"}

    def _visible(c):
        # Una entrada sostenida SOLO por el modelo local no se publica.
        fuentes = c.get("fuentes") or []
        return bool(set(fuentes) - {"modelo_local"})

    def _entrada(c, patente_ok=False):
        e = {k: v for k, v in c.items() if k != "fuentes"}
        # "fuentes" público es un CONTEO (cuántas fuentes del consenso la
        # sostienen), nunca la lista de nombres.
        e["fuentes"] = len(c.get("fuentes") or [])
        if "motivo" in e:
            e["motivo"] = _sanear_motivo(e["motivo"])
        # La patente publicada vive SOLO en problemas confirmados: un
        # hallazgo que cayó a posibles o a descartados_por_foto no la
        # arrastra (README, contrato de `patente`). "parte" (cesto/contenedor
        # dañado: cuerpo/tapa/pedal) sigue el mismo contrato.
        if not patente_ok:
            e.pop("patente", None)
            e.pop("parte", None)
        return e

    for campo in ("problemas", "posibles", "descartados_por_foto"):
        pub[campo] = [_entrada(c, patente_ok=(campo == "problemas"))
                      for c in (r.get(campo) or []) if _visible(c)]

    # elementos_detectados y en_duda también pueden nacer solo del modelo
    # local (modo sin verificación; árbitro caído): mismo filtro. en_duda es
    # una lista de claves peladas, así que las fuentes se buscan en el objeto
    # interno; una clave que no se puede rastrear no se publica.
    pub["elementos_detectados"] = [
        {"key": c.get("key"), "nombre": c.get("nombre")}
        for c in (r.get("elementos_detectados") or []) if _visible(c)]
    # Primero el mapa directo del verificador (cubre las claves de PRESENCIA,
    # que no viajan en posibles); posibles/problemas quedan de respaldo.
    _fuentes_de = {p.get("key"): p.get("fuentes") or []
                   for p in (r.get("posibles") or []) + (r.get("problemas") or [])}
    _fuentes_de.update(veri.get("fuentes_en_duda") or {})
    pub["en_duda"] = [k for k in (r.get("en_duda") or [])
                      if set(_fuentes_de.get(k, [])) - {"modelo_local"}]

    # La descripción consolidada la redacta un LLM: mismo backstop que los
    # motivos para que no cuente el mecanismo interno.
    pub["descripcion"] = _sanear_motivo(pub.get("descripcion"))

    # Confianza por problema, derivada del CONTEO de fuentes (determinística,
    # nada de porcentajes auto-reportados por los modelos): 3+ fuentes alta,
    # 2 media. Lo de una sola fuente ya vive en "posibles", que ES el nivel
    # bajo del contrato; ahí no se repite el campo.
    for c in pub["problemas"]:
        c["confianza"] = "alta" if c["fuentes"] >= 3 else "media"

    # Las invariantes del contrato valen sobre lo PUBLICADO: si el filtro
    # sacó el único problema (modo sin verificación), hay_problema es false.
    gravedades = [c["gravedad"] for c in pub["problemas"] if c.get("gravedad")]
    pub["hay_problema"] = bool(pub["problemas"])
    pub["hay_reclamo"] = bool(pub["problemas"]) or bool(pub.get("categorias_contexto"))
    pub["gravedad_maxima"] = max(gravedades) if gravedades else None

    # Predominante: la categoría que domina la escena, para que el cliente
    # pueda reportar "lo principal" sin reglas propias. Mayor gravedad gana;
    # a igual gravedad desempatan las fuentes. Solo entre problemas
    # confirmados; con un solo problema es ese.
    if pub["problemas"]:
        dom = max(pub["problemas"],
                  key=lambda c: (c.get("gravedad") or 0, c.get("fuentes") or 0))
        pub["predominante"] = dom.get("key") or dom.get("codigo")
    else:
        pub["predominante"] = None

    pub["verificacion_activa"] = bool(veri.get("activa"))
    if not veri.get("activa") and veri.get("motivo"):
        pub["verificacion_motivo"] = veri["motivo"]
    pub["modelos"] = [
        {"modelo": v.get("modelo"), "ok": v.get("ok"),
         "sin_problema": v.get("sin_problema"),
         "foto_corresponde": v.get("foto_corresponde"),
         "categorias": [{"key": c.get("key"), "gravedad": c.get("gravedad"),
                         "evidencia": c.get("evidencia")}
                        for c in (v.get("categorias") or [])],
         "descripcion": v.get("descripcion")}
        for v in (veri.get("verificadores") or [])]
    return pub


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
    if request.method == "POST" and (ruta.endswith("/clasificar")
                                     or ruta.endswith("/trabajos")):
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


def _saturado():
    # Si ya hay tantos trabajos perdidos como lugar de reserva, aceptar uno
    # más lo mandaría a la cola del executor, que no tiene techo: el pedido no
    # se respondería nunca y encima seguiría pagando verificaciones. Mejor
    # decir que no.
    with _perdidos["lock"]:
        return _perdidos["vivos"] >= ABANDONO_MAX


def _503(detalle, reintento=5):
    # Retry-After para que cualquier cliente sepa cuándo vale la pena volver.
    return HTTPException(503, detalle, headers={"Retry-After": str(reintento)})


async def _esperar_cupo(huella, tipo="sync"):
    """Espera el turno FIFO por el cupo. Devuelve "cupo" (quedó tomado) o
    "cache" (un pedido idéntico terminó mientras esperábamos y el resultado
    ya está guardado). Levanta 503 si la cola está llena o se agotó la
    espera. Corre entero en el event loop: las colas no tienen lock y la
    justicia FIFO depende de que un solo hilo las toque.

    tipo="sync": carril prioritario, acotado por COLA_MAX y ESPERA_CUPO
    (la conexión abierta no aguanta más que eso). tipo="trabajo": carril de
    los trabajos asíncronos, sin techo propio acá (la admisión ya lo acotó
    con TRABAJOS_MAX) y con paciencia TRABAJO_ESPERA; solo toma el cupo
    cuando no hay ningún sincrónico formado."""
    if tipo == "sync":
        # Sin nadie esperando en el carril propio, el camino rápido de
        # siempre; los trabajos encolados no frenan a un sincrónico (esa es
        # la prioridad). Con cola propia, el nuevo se forma atrás: si pudiera
        # tomar el cupo directo se lo robaría a los que llegaron antes.
        if not _cola and _cupos.acquire(blocking=False):
            return "cupo"
        if len(_cola) >= COLA_MAX or ESPERA_CUPO <= 0:
            raise _503("el servidor está ocupado; reintentá en un momento")
        cola, espera = _cola, ESPERA_CUPO
    else:
        # Un trabajo respeta a los dos carriles: el camino rápido solo si no
        # hay NADIE esperando.
        if not _cola and not _cola_trab and _cupos.acquire(blocking=False):
            return "cupo"
        cola, espera = _cola_trab, TRABAJO_ESPERA
    token = object()
    cola.append(token)
    try:
        vence = time.monotonic() + espera
        while True:
            # Si la misma foto terminó mientras esperábamos, no hace falta
            # cupo ni volver a pagarla: el hilo ya la dejó en la caché.
            if _cache_leer(huella) is not None:
                return "cache"
            # Solo el primero de su cola puede tomar el cupo; y un trabajo,
            # solo si no hay ningún sincrónico formado.
            if cola[0] is token and (tipo == "sync" or not _cola) \
                    and _cupos.acquire(blocking=False):
                return "cupo"
            if time.monotonic() >= vence:
                raise _503("el servidor está ocupado; reintentá en un momento")
            await asyncio.sleep(0.3)
    finally:
        # Sale de la cola pase lo que pase: también si el cliente abortó y
        # la corrutina se canceló en el sleep. Un token filtrado dejaría la
        # cola llena para siempre.
        try:
            cola.remove(token)
        except ValueError:
            pass


@app.post("/clasificar")
async def clasificar(request: Request, file: UploadFile = File(...),
                     verificar: str = "auto", contexto: str = Form("")):
    datos = await _leer_acotado(file)
    contexto = (contexto or "").strip()[:500]

    # La misma foto con el mismo contexto no se vuelve a pagar.
    huella = hashlib.sha256(
        datos + b"\x00" + contexto.encode() + b"\x00" + verificar.encode()).hexdigest()
    # La caché guarda el objeto INTERNO completo; la respuesta SIEMPRE pasa
    # por el serializador v4. No hay escotilla que devuelva los internals.
    respuesta = _cache_leer(huella)
    if respuesta is not None:
        return JSONResponse(_publica(respuesta))

    # Acá hubo una deduplicación de pedidos en vuelo (que el segundo pedido
    # de la misma foto se colgara del primero en vez de pagarla dos veces).
    # Se sacó a propósito: hacía esperar a los waiters SIN TECHO, reteniendo
    # cada uno su copia de hasta MAX_BYTES (~600 MB de una sola IP con los
    # defaults). La cola de ahora recupera el efecto con memoria acotada:
    # a lo sumo COLA_MAX pedidos esperan, y el que espera una foto idéntica
    # la saca de la caché (que escribe el hilo) en vez de volver a pagarla.
    if _saturado():
        raise _503("el servidor está degradado; reintentá más tarde", 30)

    turno = await _esperar_cupo(huella)
    if turno == "cache":
        respuesta = _cache_leer(huella)
        if respuesta is not None:
            return JSONResponse(_publica(respuesta))
        # Entre el aviso y la lectura la caché se vació (rarísimo: CACHE_MAX
        # entradas nuevas en milisegundos). Antes que complicar la cola:
        raise _503("el servidor está ocupado; reintentá en un momento")

    # Cupo tomado. El servicio pudo degradarse mientras esperábamos: el
    # techo cuenta el perdido ANTES de soltar el cupo, así que acá la
    # pérdida ya es visible y devolver el cupo es seguro.
    if _saturado():
        _cupos.release()
        raise _503("el servidor está degradado; reintentá más tarde", 30)

    respuesta = await _correr_con_cupo(datos, contexto, verificar, huella)
    return JSONResponse(_publica(respuesta))


async def _correr_con_cupo(datos, contexto, verificar, huella):
    """Corre el pipeline con el cupo YA tomado, fuera del event loop."""
    # El cupo lo suelta EL PRIMERO que llegue: el hilo al terminar, o el techo
    # si el trabajo se colgó. Nunca los dos (BoundedSemaphore explotaría).
    soltado = threading.Lock()
    estado_cupo = {"suelto": False}

    def soltar_cupo(por_techo=False):
        # Marcar el trabajo como perdido y contarlo tiene que pasar ADENTRO
        # del mismo lock: si se contara después de soltar el lock, un trabajo
        # que termina justo en el medio vería el cupo ya suelto y restaría un
        # perdido que todavía no se sumó. El max(0, ...) lo dejaría en cero y
        # después el techo sumaría uno que ya nadie va a restar: un perdido
        # fantasma para siempre. Con ABANDONO_MAX=1 eso es 503 permanente.
        # Contar ANTES de soltar el cupo además ordena bien la admisión: quien
        # entra ve la pérdida antes de ver el cupo libre.
        with soltado:
            if estado_cupo["suelto"]:
                return False
            estado_cupo["suelto"] = True
            if por_techo:
                with _perdidos["lock"]:
                    _perdidos["vivos"] += 1
                    _perdidos["total"] += 1
        _cupos.release()
        # El log va al final y envuelto: si explotara (stdout cerrado,
        # colector caído) el cupo ya quedó devuelto. Al revés era 503
        # permanente otra vez.
        if por_techo:
            try:
                print(f"[ojo] trabajo abandonado tras {TECHO_TRABAJO}s; "
                      f"se devolvió el cupo (perdidos={_perdidos['total']})",
                      flush=True)
            except Exception:      # noqa: BLE001 - loguear nunca puede romper
                pass
        return True

    techo = threading.Timer(TECHO_TRABAJO, soltar_cupo, kwargs={"por_techo": True})
    techo.daemon = True
    techo.start()

    def trabajo():
        # El cupo se suelta acá, en el hilo, para que siga tomado si la
        # corrutina que espera se cancela (cliente que corta la conexión):
        # cancelar el await NO corta el hilo, que sigue quemando CPU.
        try:
            respuesta = procesar(datos, contexto, verificar)
            # La caché se escribe ACÁ, en el hilo y antes de soltar el cupo:
            # si el cliente abortó, la corrutina ya no está para guardarla, y
            # el reintento de la misma foto tiene que encontrar el resultado
            # (es el caso abortar+reintentar del demo). Nunca puede romper el
            # retorno de un resultado bueno.
            try:
                _cache_guardar(huella, respuesta)
            except Exception:  # noqa: BLE001 - cachear nunca puede romper
                pass
            return respuesta
        finally:
            techo.cancel()
            # Si el techo ya lo había dado por perdido, al terminar devuelve
            # su lugar de reserva: el pool vuelve a tener aire.
            if not soltar_cupo():
                with _perdidos["lock"]:
                    _perdidos["vivos"] = max(0, _perdidos["vivos"] - 1)

    # El pipeline es sincrónico y tarda 25-60 s: fuera del event loop, o
    # bloquea /salud, la portada y cualquier otro pedido mientras corre.
    # Se usa el pool propio (y no asyncio.to_thread) para poder preguntarle al
    # future si el trabajo llegó a arrancar: si se cancela mientras todavía
    # estaba encolado, el finally de trabajo() nunca corre y el cupo se
    # perdería para siempre.
    try:
        tarea = _pool.submit(trabajo)
    except RuntimeError:
        techo.cancel()
        soltar_cupo()
        raise _503("el servidor se está apagando", 15)
    try:
        return await asyncio.wrap_future(tarea)
    except asyncio.CancelledError:
        # cancel() devuelve True solo si seguía en la cola: ahí es seguro
        # soltar, porque trabajo() no va a correr nunca. Si devuelve False ya
        # arrancó y lo suelta su propio finally.
        if tarea.cancel():
            techo.cancel()
            soltar_cupo()
        raise


# ---- Trabajos asíncronos ---------------------------------------------------
# Registro en memoria: id -> trabajo. Lo tocan SOLO corrutinas del event loop
# (los endpoints y la tarea de cada trabajo), así que no lleva lock. Un
# reinicio del servidor lo pierde entero: el cliente trata el 404 como
# "reenviá la foto" (documentado en la portada y el README).
_trabajos = collections.OrderedDict()


def _trabajos_pendientes():
    return [t for t in _trabajos.values()
            if t["estado"] in ("en_cola", "procesando")]


def _podar_trabajos():
    """Vence terminados por TTL y aplica el techo de retenidos. Nunca toca
    pendientes: un trabajo aceptado siempre llega a listo o error."""
    ahora = time.monotonic()
    vencidos = [tid for tid, t in _trabajos.items()
                if t["estado"] in ("listo", "error")
                and ahora - t["fin"] > TRABAJO_TTL]
    for tid in vencidos:
        del _trabajos[tid]
    # El techo desaloja por hora de FINALIZACIÓN, no por orden de creación:
    # un trabajo viejo que recién termina no puede ser el primero en caer
    # antes de que su dueño lo pase a buscar.
    terminados = sorted((tid for tid, t in _trabajos.items()
                         if t["estado"] in ("listo", "error")),
                        key=lambda tid: _trabajos[tid]["fin"])
    for tid in terminados[:max(0, len(terminados) - TRABAJOS_LISTOS_MAX)]:
        del _trabajos[tid]


def _posicion_trabajo(t):
    """1 = el próximo en entrar. Cuenta solo los que siguen en cola delante."""
    pos = 1
    for otro in _trabajos.values():
        if otro is t:
            break
        if otro["estado"] == "en_cola":
            pos += 1
    return pos


async def _correr_trabajo(t):
    """Vida completa de un trabajo: espera su turno, corre el pipeline y deja
    el resultado (o el error) en el registro. Nunca levanta hacia afuera salvo
    la cancelación del apagado."""
    try:
        turno = await _esperar_cupo(t["huella"], tipo="trabajo")
        if turno == "cupo":
            # Mismo chequeo que el camino sincrónico: el servicio pudo
            # degradarse mientras esperábamos el turno.
            if _saturado():
                _cupos.release()
                raise _503("el servidor está degradado; reintentá más tarde", 30)
            t["estado"] = "procesando"
            datos, t["datos"] = t["datos"], None
            # Techo del TRABAJO, además del techo del cupo: si el hilo queda
            # trabado, el watchdog devuelve el cupo pero esta corrutina
            # seguiría esperando el future para siempre y el trabajo quedaría
            # "procesando" eterno, reteniendo su registro. Pasado el techo,
            # el trabajo muere con error aunque el hilo siga por ahí.
            respuesta = await asyncio.wait_for(
                _correr_con_cupo(datos, t["contexto"], t["verificar"],
                                 t["huella"]),
                TECHO_TRABAJO + 30)
        else:
            # "cache": un pedido idéntico terminó mientras esperábamos.
            respuesta = _cache_leer(t["huella"])
            if respuesta is None:
                raise _503("el servidor está ocupado; reintentá en un momento")
        t["resultado"] = _publica(respuesta)
        t["estado"] = "listo"
    except HTTPException as e:
        t["estado"], t["detalle"] = "error", e.detail
    except asyncio.TimeoutError:
        t["estado"], t["detalle"] = "error", "el análisis superó el tiempo máximo"
    except asyncio.CancelledError:
        # Apagado del servidor: el registro muere con el proceso igual, pero
        # que no quede "procesando" si alguien consulta en la ventana final.
        t["estado"], t["detalle"] = "error", "el servidor se está apagando"
        raise
    except Exception:  # noqa: BLE001 - un trabajo jamás muere sin estado final
        t["estado"], t["detalle"] = "error", "falla interna al procesar la foto"
    finally:
        t["fin"] = time.monotonic()
        t["datos"] = None
        _podar_trabajos()


@app.post("/trabajos")
@app.post("/trabajos/")
async def crear_trabajo(request: Request, file: UploadFile = File(...),
                        verificar: str = "auto", contexto: str = Form("")):
    datos = await _leer_acotado(file)
    contexto = (contexto or "").strip()[:500]
    huella = hashlib.sha256(
        datos + b"\x00" + contexto.encode() + b"\x00" + verificar.encode()).hexdigest()
    # Foto ya resuelta: el resultado va en la misma respuesta, sin crear ni
    # retener registro (repetir una foto cacheada no debe ocupar memoria).
    respuesta = _cache_leer(huella)
    if respuesta is not None:
        return JSONResponse({"trabajo": None, "estado": "listo",
                             "resultado": _publica(respuesta)})
    if _saturado():
        raise _503("el servidor está degradado; reintentá más tarde", 30)
    _podar_trabajos()
    pendientes = _trabajos_pendientes()
    if len(pendientes) >= TRABAJOS_MAX:
        raise _503("no hay lugar en la cola de trabajos; reintentá en un rato", 30)
    ip = _ip_cliente(request)
    if sum(1 for p in pendientes if p["ip"] == ip) >= TRABAJOS_POR_IP:
        raise HTTPException(
            429, "demasiados trabajos pendientes desde esta dirección; "
                 "esperá a que terminen antes de mandar más",
            headers={"Retry-After": "30"})
    tid = secrets.token_urlsafe(16)
    t = {"id": tid, "ip": ip, "estado": "en_cola", "creado": time.monotonic(),
         "fin": None, "resultado": None, "detalle": None, "datos": datos,
         "contexto": contexto, "verificar": verificar, "huella": huella}
    _trabajos[tid] = t
    # La referencia a la tarea vive en el registro: sin ella, una excepción
    # en una tarea ya recolectada se loguea como "never retrieved".
    t["tarea"] = asyncio.create_task(_correr_trabajo(t))
    return JSONResponse({"trabajo": tid, "estado": "en_cola",
                         "posicion": _posicion_trabajo(t)}, status_code=202)


def _estado_trabajo(tid):
    _podar_trabajos()
    t = _trabajos.get(tid)
    if t is None:
        raise HTTPException(404, "trabajo desconocido o vencido; reenviá la foto")
    r = {"trabajo": tid, "estado": t["estado"]}
    if t["estado"] == "en_cola":
        r["posicion"] = _posicion_trabajo(t)
    elif t["estado"] == "listo":
        r["resultado"] = t["resultado"]
    elif t["estado"] == "error":
        r["detail"] = t["detalle"]
    return r


# El alias con query string va ANTES que el segmento dinámico y existe porque
# el despliegue público pasa por un forwarder que solo rutea subcarpetas
# fijas: /trabajos/abc123 jamás le llegaría, /trabajos/?id=abc123 sí.
@app.get("/trabajos")
@app.get("/trabajos/")
async def ver_trabajo_query(id: str = ""):
    if not id:
        raise HTTPException(400, "falta el parámetro id")
    return _estado_trabajo(id)


@app.get("/trabajos/{tid}")
async def ver_trabajo(tid: str):
    return _estado_trabajo(tid)


@app.get("/", response_class=HTMLResponse)
def portada():
    return PAGINA


PAGINA = r"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ojo Urbano · clasificador de incidencias urbanas</title>
<style>
  :root{--bg:#f4f4f4;--surface:#fff;--soft:#fafafa;--ink:#111;--muted:#666;
        --muted2:#8a8a8a;--line:#dedede;--line2:#bdbdbd;--rojo:#8a2b2b}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;
       background:linear-gradient(180deg,rgba(255,255,255,.86),rgba(255,255,255,0) 320px),var(--bg);color:var(--ink)}
  .wrap{width:min(1040px,100%);margin:0 auto;padding:28px 20px 44px}
  .masthead{margin-bottom:20px;padding-bottom:22px;border-bottom:1px solid var(--line)}
  h1{font-size:19px;letter-spacing:.14em;margin:0}
  .tagline{margin-top:3px;color:var(--muted);font-size:13px;font-weight:700}
  .sub{color:var(--muted2);font-size:13px;margin-top:5px;max-width:640px}
  .mode{display:inline-block;font-size:12px;padding:3px 10px;border-radius:20px;
        border:1px solid var(--line2);background:var(--surface);color:var(--muted)}
  .ctxlabel{display:block;margin-top:18px;font-size:12px;text-transform:uppercase;
            letter-spacing:.07em;color:var(--muted);font-weight:700}
  #ctx{width:100%;margin-top:6px;padding:9px 11px;border:1px solid var(--line2);
       border-radius:8px;font:inherit;background:var(--surface);color:var(--ink)}
  #ctx::placeholder{color:var(--muted2)}
  .ctxhint{font-size:12px;color:var(--muted2);margin-top:4px}
  #drop{margin-top:12px;border:1px dashed var(--line2);border-radius:8px;background:var(--surface);
        padding:30px 20px;text-align:center;cursor:pointer;transition:.15s}
  #drop:hover,#drop:focus-visible,#drop.over{border-color:var(--ink);background:var(--soft);outline:none}
  #drop p{margin:6px 0;color:var(--muted)}
  #drop strong{color:var(--ink)}
  .err{display:none;font-size:13px;font-weight:600;margin-top:12px;border:1px solid var(--line2);
       border-radius:8px;background:var(--surface);padding:10px 12px}
  /* barra de control pegajosa: en lotes largos el estado global queda a la vista */
  .barra{display:none;position:sticky;top:0;z-index:5;margin:16px -8px 0;padding:12px 8px 10px;
         background:linear-gradient(180deg,var(--bg) 82%,rgba(244,244,244,0));
         flex-direction:column;gap:9px}
  .barra.on{display:flex}
  .fila{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .btn{padding:9px 14px;font:13.5px inherit;font-weight:600;border:1px solid var(--line2);
       border-radius:8px;background:var(--surface);color:var(--muted);cursor:pointer}
  .btn:hover:not(:disabled){color:var(--ink);border-color:var(--ink)}
  .btn:disabled{opacity:.45;cursor:default}
  .btn.primario{background:var(--ink);border-color:var(--ink);color:#fff}
  .btn.primario:hover:not(:disabled){opacity:.85;color:#fff}
  .leyenda{margin-left:auto;display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--muted)}
  .leyenda b{color:var(--ink);font-weight:700}
  .lej{display:inline-flex;align-items:center;gap:6px}
  .pip{width:9px;height:9px;border-radius:50%;border:1.5px solid var(--line2);background:var(--surface);flex:none}
  .pip.cola{background:repeating-linear-gradient(45deg,#fff,#fff 2px,var(--line2) 2px,var(--line2) 3px)}
  .pip.proc{border-color:var(--ink);background:var(--ink)}
  .pip.ok{border-color:var(--ink);background:#fff;box-shadow:inset 0 0 0 2.5px var(--ink)}
  .pip.mal{border-color:var(--rojo);background:var(--rojo)}
  .ptrack{height:5px;border-radius:3px;background:var(--line);overflow:hidden}
  .pfill{height:100%;width:0%;background:var(--ink);border-radius:3px;transition:width .4s}
  #tarjetas{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-top:12px}
  .tar{border:1px solid var(--line);border-radius:8px;background:var(--surface);overflow:hidden;
       display:flex;flex-direction:column;transition:border-color .3s}
  .tar.activa{border-color:var(--ink);box-shadow:0 1px 8px rgba(0,0,0,.09)}
  .tar.fallida{border-color:var(--rojo)}
  .miniatura{position:relative}
  .miniatura img{width:100%;aspect-ratio:4/3;object-fit:cover;background:#111;display:block;transition:filter .3s}
  .tar.apagada .miniatura img{filter:grayscale(.75) opacity(.55)}
  .puesto{position:absolute;top:8px;right:8px;min-width:34px;height:34px;border-radius:17px;
          background:rgba(17,17,17,.82);color:#fff;font-size:12px;font-weight:700;
          display:flex;align-items:center;justify-content:center;padding:0 9px;letter-spacing:.03em}
  .velo{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
        background:rgba(17,17,17,.28)}
  .velo .spin{width:34px;height:34px;border:3px solid rgba(255,255,255,.35);border-top-color:#fff;
        border-radius:50%;animation:gira .8s linear infinite}
  @keyframes gira{to{transform:rotate(360deg)}}
  /* banda de estado: la línea de vida de cada foto, siempre en el mismo lugar */
  .banda{display:flex;align-items:center;gap:8px;padding:8px 12px;font-size:12.5px;font-weight:600;
         border-bottom:1px solid var(--line);color:var(--muted);background:var(--soft);min-height:37px}
  .banda .der{margin-left:auto;font-weight:400;color:var(--muted2);font-variant-numeric:tabular-nums}
  .banda.proc{background:var(--ink);color:#fff;border-bottom-color:var(--ink)}
  .banda.proc .der{color:rgba(255,255,255,.75)}
  .banda.ok{background:var(--surface);color:var(--ink)}
  .banda.mal{background:var(--rojo);color:#fff;border-bottom-color:var(--rojo)}
  .banda.mal .der{color:rgba(255,255,255,.8)}
  .spinmini{width:11px;height:11px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;
            border-radius:50%;animation:gira .8s linear infinite;flex:none}
  .banda .spinoscuro{border-color:var(--line2);border-top-color:var(--ink)}
  .grav{display:inline-flex;align-items:center;justify-content:center;min-width:38px;height:22px;
        border-radius:6px;background:var(--ink);color:#fff;font-size:11.5px;font-weight:700;padding:0 7px}
  .grav.g0{background:var(--surface);color:var(--muted);border:1px solid var(--line2)}
  .tarbody{padding:10px 12px;display:flex;flex-direction:column;gap:8px;flex:1}
  .tarnombre{font-size:11.5px;color:var(--muted2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .tarres{display:flex;flex-direction:column;gap:8px}
  .tarconcl{font-size:13.5px;font-weight:600}
  .minicats{display:flex;flex-wrap:wrap;gap:6px}
  .minicat{border:1px solid var(--line2);border-radius:6px;background:var(--soft);padding:4px 9px;font-size:12px}
  .minicat b{display:block;font-size:12.5px}
  .minicat span{color:var(--muted);font-size:11.5px}
  .minicat.ctx{border-style:dashed;background:var(--surface)}
  .tardesc{font-size:12.5px;color:var(--muted)}
  details.tardet{border-top:1px solid var(--line);margin:2px -12px -10px}
  details.tardet>summary{font-size:12px;padding:9px 12px;color:var(--muted);cursor:pointer;font-weight:600}
  details.tardet>.detbody{padding:0 12px 12px;display:flex;flex-direction:column;gap:6px}
  .voto{font-size:12px;color:var(--muted)}
  .voto b{color:var(--ink);font-weight:600}
  h4.mini{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:6px 0 0}
  pre.json{background:#111;color:#f2f2f2;padding:10px;border-radius:8px;overflow:auto;
       font:11.5px/1.5 ui-monospace,Menlo,monospace;margin:0;max-height:260px}
  details.det{margin-top:18px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
  details.det>summary{cursor:pointer;padding:10px 14px;font-size:13px;font-weight:600;color:var(--muted);
       list-style-position:inside}
  details.det[open]>summary{border-bottom:1px solid var(--line);color:var(--ink)}
  details.det>.detbody{padding:14px}
  .endpoint{font:13px ui-monospace,Menlo,monospace;background:var(--surface);
            border:1px solid var(--line);border-radius:8px;padding:8px 11px;margin-bottom:10px}
  .apinote{font-size:12.5px;color:var(--muted);margin-bottom:12px}
  .tabs{display:flex;gap:6px;margin-bottom:8px}
  .tab{padding:5px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface);
       cursor:pointer;font:13px inherit;color:var(--muted)}
  .tab.active{color:#fff;border-color:var(--ink);background:var(--ink)}
  pre.code{background:#111;color:#f2f2f2;padding:14px;border-radius:8px;overflow:auto;
       font:12.5px/1.55 ui-monospace,Menlo,monospace;margin:0;display:none}
  pre.code.active{display:block}
  @media (prefers-reduced-motion: reduce){
    .spin,.spinmini{animation-duration:2.4s}
    .pfill{transition:none}
  }
</style></head>
<body><div class="wrap">
  <header class="masthead">
    <h1>OJO URBANO</h1>
    <div class="tagline">Reconocimiento visual de incidencias urbanas</div>
    <div class="sub">Subí una o varias fotos de problemas en la vía pública (basura fuera del contenedor,
      muebles abandonados, veredas rotas, vehículos sobre la ciclovía) y el sistema identifica qué reporte
      corresponde a cada una. Las fotos pasan de a una por el análisis: las demás esperan su turno en cola
      y los resultados van apareciendo en esta misma página. Al final podés bajar todo en un CSV. La foto
      y el contexto se envían a modelos de IA de terceros vía OpenRouter para la verificación cruzada.</div>
  </header>
  <span id="modechip" class="mode">cargando…</span>

  <label class="ctxlabel" for="ctx">Contexto vecinal (opcional)</label>
  <input id="ctx" type="text" maxlength="500"
         placeholder="Contá algo que quizá no se vea en la foto, p. ej. «todo huele mal» o «hay ratas»">
  <div class="ctxhint">Tiene peso propio: si la foto no muestra lo que contás, el reclamo se arma con
    lo que escribiste y la foto se marca como no válida. Se aplica a todas las fotos del lote, así que
    escribilo antes de tocar «Analizar».</div>

  <div id="drop" role="button" tabindex="0" aria-label="Elegir fotos para analizar">
    <p><strong>Arrastrá una o varias fotos acá</strong> o hacé clic para elegir</p>
    <p>JPG / PNG / WEBP · después tocá «Analizar»</p>
    <input id="file" type="file" accept="image/*" multiple hidden>
  </div>
  <div class="err" id="err"></div>

  <div class="barra" id="barra">
    <div class="fila">
      <button id="analizar" class="btn primario">Analizar</button>
      <button id="csvbtn" class="btn" disabled>Descargar CSV</button>
      <button id="limpiar" class="btn" disabled>Quitar terminadas</button>
      <button id="reerr" class="btn" style="display:none">Reintentar errores</button>
      <div class="leyenda" id="leyenda" aria-live="polite"></div>
    </div>
    <div class="ptrack" aria-hidden="true"><div class="pfill" id="pfill"></div></div>
  </div>

  <div id="tarjetas"></div>

  <details class="det">
    <summary>API para desarrolladores</summary>
    <div class="detbody">
      <div class="endpoint"><b>POST</b> <span id="ep"></span> · multipart/form-data, campo <b>file</b> ·
        campo opcional <b>contexto</b> · espera el resultado en la misma conexión (25-60 s)</div>
      <div class="endpoint"><b>POST</b> <span id="ep2"></span> · mismos campos · responde al instante con
        {"trabajo": id, "estado": "en_cola", "posicion": n}<br>
        <b>GET</b> <span id="ep3"></span> · estado del trabajo: en_cola, procesando, listo (trae
        <b>resultado</b>) o error</div>
      <div class="apinote">La vía asíncrona (<b>/trabajos</b>) es la recomendada para lotes: el servidor
        procesa de a una foto y encola el resto; hay un tope global de trabajos pendientes y otro por IP,
        y pasado el tope el POST devuelve 429/503 con <b>Retry-After</b> para reintentar. Los trabajos
        viven en memoria: un 404 al consultar significa que el servidor se reinició y hay que reenviar la
        foto. Parámetro opcional <b>?verificar=</b> <b>auto</b> (default: verifica si hay clave de
        OpenRouter) · <b>1</b> (forzar) · <b>0</b> (sin verificación: respuesta degradada). Las categorías
        que el contexto describe pero la foto no confirma vuelven en <b>categorias_contexto</b>.</div>
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
// convierte el POST en GET y rompe la subida). La consulta de trabajos va por
// query string (?id=) porque el proxy solo rutea subcarpetas fijas.
const O=location.origin+location.pathname.replace(/\/$/,'');
const SUF=location.pathname.replace(/\/$/,'')?'/':'';
const T=O+'/trabajos'+SUF;
$('#ep').textContent=O+'/clasificar'+SUF;
$('#ep2').textContent=T;
$('#ep3').textContent=T+'?id=...';
const GRAV={1:'mínima',2:'leve',3:'alta',4:'grave',5:'muy grave'};
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const SNIP={
 curl:`# una foto, esperando el resultado en la conexión (25-60 s)
curl -s -F "file=@foto.jpg" -F "contexto=vidrios rotos" ${O}/clasificar${SUF}

# lote: encolar al instante y consultar hasta que esté listo
curl -s -F "file=@foto.jpg" ${T}
curl -s "${T}?id=ID_DEVUELTO"`,
 python:`import time, requests

with open("foto.jpg", "rb") as f:
    t = requests.post("${T}", files={"file": f},
                      data={"contexto": "vidrios rotos"}).json()
while t["estado"] not in ("listo", "error"):
    time.sleep(4)
    t = requests.get("${T}", params={"id": t["trabajo"]}).json()
print(t.get("resultado") or t)`,
 js:`const fd = new FormData();
fd.append("file", fileInput.files[0]);
let t = await (await fetch("${T}", { method: "POST", body: fd })).json();
while (t.estado !== "listo" && t.estado !== "error") {
  await new Promise(r => setTimeout(r, 4000));
  t = await (await fetch("${T}?id=" + t.trabajo)).json();
}
console.log(t.resultado || t.detail);`
};
['curl','python','js'].forEach(l=>$('#code-'+l).textContent=SNIP[l]);
$('#tabs').onclick=e=>{const b=e.target.closest('.tab');if(!b)return;
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t===b));
  document.querySelectorAll('.code').forEach(c=>c.classList.remove('active'));
  $('#code-'+b.dataset.l).classList.add('active');};
fetch(O+'/salud'+SUF).then(r=>r.json()).then(h=>{
  const chip=$('#modechip');
  chip.textContent=h.verificacion?'Análisis completo activo':'Sin verificación cruzada: el análisis está suspendido';
  chip.title=h.verificacion?('Verificadores: '+h.verificadores.join(' + ')+(h.arbitro?' · árbitro: '+h.arbitro:'')):'Configurá OPENROUTER_API_KEY para activar la verificación';});

// ---- lote de fotos ----------------------------------------------------------
// Cada foto es una tarjeta con una banda de estado que cuenta su vida entera:
// en espera -> enviando -> en cola (con puesto y espera estimada) ->
// analizando (con cronómetro) -> listo (con duración y gravedad) o error.
// El navegador manda de a MAX_VUELO trabajos (el servidor procesa de a uno y
// tiene tope por IP) y consulta el estado de todos en una pasada cada POLL_MS.
const MAX_VUELO=3,POLL_MS=3500,REINTENTOS_ENVIO=60,SEG_POR_FOTO=45;
let items=[],iniciado=false,pollTimer=null,seq=0,sondeando=false;

const drop=$('#drop'),file=$('#file');
drop.onclick=()=>file.click();
drop.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();file.click();}});
['dragover','dragenter'].forEach(e=>document.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(e=>document.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('over')}));
document.addEventListener('drop',ev=>{if(ev.dataTransfer.files.length)agregar(ev.dataTransfer.files)});
file.addEventListener('change',()=>{if(file.files.length){agregar(file.files);file.value='';}});
window.addEventListener('beforeunload',e=>{
  const pendientes=items.some(i=>['enviando','en_cola','procesando'].includes(i.estado))
    ||(iniciado&&items.some(i=>i.estado==='espera'));
  if(pendientes){e.preventDefault();e.returnValue='';}
});

function agregar(lista){
  let rechazadas=0;
  for(const f of lista){
    if(!f.type.startsWith('image/')){rechazadas++;continue;}
    const it={n:++seq,file:f,estado:'espera',trabajo:null,resultado:null,detalle:'',
              posicion:null,reenvios:0,reenvios404:0,card:null,nota:'',noAntes:null,
              tProc:null,tFin:null,dur:null};
    items.push(it);crearTarjeta(it);pintar(it);
  }
  const err=$('#err');
  if(rechazadas){err.textContent=rechazadas+(rechazadas===1?' archivo no es una imagen y se ignoró.':' archivos no son imágenes y se ignoraron.');err.style.display='block';}
  else err.style.display='none';
  actualizarBarra();
  if(iniciado)bombear();
}

function crearTarjeta(it){
  const el=document.createElement('div');el.className='tar';
  const mini=document.createElement('div');mini.className='miniatura';
  const img=document.createElement('img');
  img.alt=it.file.name;img.src=URL.createObjectURL(it.file);
  img.style.cursor='zoom-in';img.title='Ver la foto completa';
  img.onclick=()=>window.open(img.src,'_blank');
  mini.appendChild(img);
  const banda=document.createElement('div');banda.className='banda';
  const cuerpo=document.createElement('div');cuerpo.className='tarbody';
  cuerpo.innerHTML=`<div class="tarnombre" title="${esc(it.file.name)}">${esc(it.file.name)}</div><div class="tarres"></div>`;
  el.appendChild(mini);el.appendChild(banda);el.appendChild(cuerpo);
  it.card=el;$('#tarjetas').appendChild(el);
}

function enVuelo(){return items.filter(i=>['enviando','en_cola','procesando'].includes(i.estado)).length}

function bombear(){
  if(!iniciado)return;
  for(const it of items){
    if(enVuelo()>=MAX_VUELO)break;
    if(it.estado==='espera'&&(!it.noAntes||Date.now()>=it.noAntes))enviar(it);
  }
  actualizarBarra();
}

async function enviar(it){
  it.estado='enviando';it.nota='';pintar(it);
  const fd=new FormData();fd.append('file',it.file);
  const ctx=$('#ctx').value.trim();if(ctx)fd.append('contexto',ctx);
  try{
    const r=await fetch(T,{method:'POST',body:fd});
    if(r.status===429||r.status===503){
      // servidor lleno: la tarjeta vuelve a la espera y se reintenta sola
      const ra=parseInt(r.headers.get('Retry-After'),10);
      const pausa=(isNaN(ra)||ra<1?15:Math.max(5,ra))*1000;
      it.reenvios++;
      if(it.reenvios>REINTENTOS_ENVIO){fallar(it,'el servidor estuvo ocupado demasiado tiempo; reintentá a mano');return;}
      it.estado='espera';it.nota='servidor lleno, reintenta solo';it.noAntes=Date.now()+pausa;pintar(it);
      setTimeout(bombear,pausa+80);
      return;
    }
    if(!r.ok){
      const e=await r.json().catch(()=>null);
      fallar(it,(e&&e.detail)||('no se pudo enviar (HTTP '+r.status+')'));
      return;
    }
    const d=await r.json();
    if(d.estado==='listo'&&d.resultado){it.tProc=it.tProc||Date.now();resolver(it,d.resultado);return;}
    it.trabajo=d.trabajo;it.posicion=d.posicion;it.estado='en_cola';pintar(it);
  }catch(e){
    fallar(it,'no se pudo enviar: '+e.message);
  }
  actualizarBarra();
}

function arrancarPoll(){
  if(pollTimer)return;
  pollTimer=setInterval(sondear,POLL_MS);
  setTimeout(sondear,900);
  setInterval(tic,1000);
}

async function sondear(){
  if(sondeando)return;
  sondeando=true;
  try{
    for(const it of items){
      if(!it.trabajo||!['en_cola','procesando'].includes(it.estado))continue;
      try{
        const r=await fetch(T+'?id='+encodeURIComponent(it.trabajo));
        if(r.status===404){
          // el servidor se reinició y perdió el registro: se reenvía una vez
          it.trabajo=null;
          if(it.reenvios404){fallar(it,'el trabajo se perdió; reintentá a mano');}
          else{it.reenvios404=1;it.estado='espera';it.nota='el servidor se reinició, se reenvía';pintar(it);bombear();}
          continue;
        }
        if(!r.ok)continue;
        const d=await r.json();
        if(d.estado==='listo')resolver(it,d.resultado);
        else if(d.estado==='error')fallar(it,d.detail||'falla en el análisis');
        else{
          if(d.estado==='procesando'&&!it.tProc)it.tProc=Date.now();
          it.estado=d.estado;it.posicion=d.posicion;pintar(it);
        }
      }catch(e){/* corte de red transitorio: la próxima pasada lo cubre */}
    }
  }finally{sondeando=false;}
}

function resolver(it,resultado){
  it.tFin=Date.now();
  if(it.tProc)it.dur=Math.max(1,Math.round((it.tFin-it.tProc)/1000));
  it.estado='listo';it.resultado=resultado;pintar(it);bombear();
}

function fallar(it,motivo){
  it.tFin=Date.now();
  it.estado='error';it.detalle=motivo||'falla';pintar(it);bombear();
}

// cronómetro y espera estimada, refrescados por segundo sin repintar la tarjeta
function tic(){
  for(const it of items){
    const der=it.card.querySelector('.banda .der');
    if(!der)continue;
    if(it.estado==='procesando'&&it.tProc)
      der.textContent=Math.round((Date.now()-it.tProc)/1000)+' s';
    else if(it.estado==='en_cola'&&it.posicion)
      der.textContent='espera aprox. '+etaTexto(it.posicion);
  }
}

function etaTexto(puesto){
  const s=puesto*SEG_POR_FOTO;
  return s<90?('~'+s+' s'):('~'+Math.round(s/60)+' min');
}

function pintar(it){
  const banda=it.card.querySelector('.banda');
  const mini=it.card.querySelector('.miniatura');
  const res=it.card.querySelector('.tarres');
  mini.querySelectorAll('.puesto,.velo').forEach(x=>x.remove());
  it.card.classList.toggle('activa',it.estado==='procesando');
  it.card.classList.toggle('fallida',it.estado==='error');
  it.card.classList.toggle('apagada',it.estado==='espera'||it.estado==='en_cola'||it.estado==='enviando');
  banda.className='banda';
  if(it.estado==='espera'){
    banda.innerHTML='Por enviar'+(it.nota?`<span class="der">${esc(it.nota)}</span>`:'<span class="der">todavía en tu navegador</span>');
  }else if(it.estado==='enviando'){
    banda.innerHTML='<span class="spinmini spinoscuro"></span>Enviando<span class="der"></span>';
  }else if(it.estado==='en_cola'){
    banda.innerHTML=`En cola · puesto ${it.posicion||'?'}<span class="der">espera aprox. ${etaTexto(it.posicion||1)}</span>`;
    if(it.posicion){
      const p=document.createElement('div');p.className='puesto';p.textContent=it.posicion+'º';
      mini.appendChild(p);
    }
  }else if(it.estado==='procesando'){
    banda.className='banda proc';
    banda.innerHTML='<span class="spinmini"></span>Analizando<span class="der">0 s</span>';
    const v=document.createElement('div');v.className='velo';v.innerHTML='<div class="spin"></div>';
    mini.appendChild(v);
  }else if(it.estado==='listo'){
    banda.className='banda ok';
    const d=it.resultado||{};
    const g=d.gravedad_maxima;
    const insignia=d.hay_problema
      ?`<span class="grav">G ${g||'?'}</span>`
      :'<span class="grav g0">sin problema</span>';
    banda.innerHTML=`${insignia} Listo<span class="der">${it.dur?it.dur+' s':''}</span>`;
  }else{
    banda.className='banda mal';
    banda.innerHTML='Error<span class="der"></span>';
  }
  if(it.estado==='error'){
    res.innerHTML=`<div class="tardesc">${esc(it.detalle)}</div>`+
      `<button class="btn retrybtn" style="align-self:flex-start">Reintentar</button>`;
    res.querySelector('.retrybtn').onclick=()=>{
      reintentar(it);iniciado=true;bombear();arrancarPoll();};
  }else if(it.estado==='listo'){
    res.innerHTML=renderResultado(it.resultado);
  }else if(res.innerHTML){
    res.innerHTML='';
  }
  actualizarBarra();
}

function renderResultado(d){
  const probs=d.problemas||[];
  const aviso=(d.foto_valida===false&&d.hay_problema)
    ?' La foto no muestra lo que contaste: el reclamo salió de tu texto.':'';
  const concl=d.hay_problema
    ?(probs.length===1?'1 incidencia confirmada':probs.length+' incidencias confirmadas')
      +(d.gravedad_maxima?` · gravedad ${d.gravedad_maxima}/5 (${GRAV[d.gravedad_maxima]||''})`:'')
    :d.hay_reclamo?'Reclamo por texto, sin confirmación en la foto'
    :'Sin problemas confirmados';
  let h=`<div class="tarconcl">${esc(concl+aviso)}</div>`;
  if(probs.length)h+='<div class="minicats">'+probs.map(c=>
    `<div class="minicat"><b>${esc(c.nombre)}</b><span>${c.gravedad?c.gravedad+'/5':''}`+
    `${c.confianza?' · conf. '+esc(c.confianza):''}${c.patente?' · patente '+esc(c.patente):''}</span></div>`).join('')+'</div>';
  const cc=d.categorias_contexto||[];
  if(cc.length)h+='<div class="minicats">'+cc.map(c=>
    `<div class="minicat ctx"><b>${esc(c.nombre)}</b><span>según tu texto</span></div>`).join('')+'</div>';
  if(d.descripcion)h+=`<div class="tardesc">${esc(d.descripcion)}</div>`;
  const pos=d.posibles||[],pres=d.elementos_detectados||[],duda=d.en_duda||[];
  let det='';
  if(pos.length)det+='<h4 class="mini">Posibles (sin confirmar)</h4>'+pos.map(p=>
    `<div class="voto"><b>${esc(String(p.nombre||p.key||'').replace(/_/g,' '))}</b>`+
    `${p.origen==='contexto_vecinal'?' · por tu texto':''}${p.motivo?': '+esc(p.motivo):''}</div>`).join('');
  if(pres.length)det+='<h4 class="mini">Elementos detectados</h4><div class="voto">'+
    pres.map(e=>esc(e.nombre||e.key)).join(', ')+'</div>';
  if(duda.length)det+='<h4 class="mini">Sin consenso</h4><div class="voto">'+
    esc(duda.map(k=>k.replace(/_/g,' ')).join(', '))+'</div>';
  if(d.verificacion_activa&&(d.modelos||[]).length)
    det+='<h4 class="mini">Qué vio cada modelo</h4>'+(d.modelos||[]).map(x=>x.ok
      ?`<div class="voto"><b>${esc(x.modelo)}</b>: ${(x.categorias||[]).length?esc(x.categorias.map(c=>c.key.replace(/_/g,' ')).join(', ')):'sin hallazgos'}${x.descripcion?' · '+esc(x.descripcion):''}</div>`
      :`<div class="voto"><b>${esc(x.modelo)}</b>: no respondió</div>`).join('');
  if(!d.verificacion_activa)det+=`<div class="voto">Sin verificación cruzada (${esc(d.verificacion_motivo||'desactivada')}): no hay resultado confiable.</div>`;
  det+='<h4 class="mini">JSON</h4><pre class="json">'+esc(JSON.stringify(d,null,2))+'</pre>';
  h+=`<details class="tardet"><summary>Más detalle</summary><div class="detbody">${det}</div></details>`;
  return h;
}

function actualizarBarra(){
  const n=items.length;
  $('#barra').classList.toggle('on',n>0);
  const c=e=>items.filter(i=>i.estado===e).length;
  const listas=c('listo'),errs=c('error'),proc=c('procesando'),
        cola=c('en_cola')+c('enviando'),espera=c('espera');
  const partes=[];
  if(espera)partes.push(`<span class="lej"><span class="pip"></span><b>${espera}</b> por enviar</span>`);
  if(cola)partes.push(`<span class="lej"><span class="pip cola"></span><b>${cola}</b> en cola</span>`);
  if(proc)partes.push(`<span class="lej"><span class="pip proc"></span><b>${proc}</b> analizando</span>`);
  partes.push(`<span class="lej"><span class="pip ok"></span><b>${listas}</b>${n?' de '+n:''} listas</span>`);
  if(errs)partes.push(`<span class="lej"><span class="pip mal"></span><b>${errs}</b> con error</span>`);
  $('#leyenda').innerHTML=partes.join('');
  $('#pfill').style.width=(n?Math.round(100*(listas+errs)/n):0)+'%';
  const ba=$('#analizar');
  ba.textContent=iniciado?'Analizar nuevas':'Analizar '+(n===1?'1 foto':n+' fotos');
  ba.disabled=!espera;
  $('#csvbtn').disabled=!listas;
  $('#limpiar').disabled=!items.some(i=>i.estado==='listo'||i.estado==='error');
  $('#reerr').style.display=errs>1?'':'none';
}

$('#analizar').onclick=()=>{iniciado=true;bombear();arrancarPoll();};

function reintentar(it){
  it.estado='espera';it.detalle='';it.nota='';it.trabajo=null;it.reenvios=0;it.reenvios404=0;
  it.noAntes=null;it.tProc=null;it.tFin=null;it.dur=null;
  pintar(it);
}

$('#reerr').onclick=()=>{
  items.filter(i=>i.estado==='error').forEach(reintentar);
  iniciado=true;bombear();arrancarPoll();
};

$('#limpiar').onclick=()=>{
  items=items.filter(it=>{
    if(it.estado==='listo'||it.estado==='error'){
      const img=it.card.querySelector('img');
      if(img&&img.src.startsWith('blob:'))URL.revokeObjectURL(img.src);
      it.card.remove();
      return false;
    }
    return true;
  });
  if(!items.length)iniciado=false;
  actualizarBarra();
};

$('#csvbtn').onclick=()=>{
  const cab=['archivo','estado','hay_problema','gravedad_maxima','predominante','problemas',
    'patente','elementos_detectados','posibles','en_duda','hay_reclamo','foto_valida_estado',
    'verificacion_activa','verificacion_motivo','descripcion','error','trabajo'];
  const filas=[cab];
  for(const it of items){
    const d=it.resultado||{};
    const probs=(d.problemas||[]).map(p=>
      (p.key||'')+(p.gravedad?' (g'+p.gravedad+(p.confianza?', '+p.confianza:'')+')':'')).join(' | ');
    const pat=(d.problemas||[]).map(p=>p.patente).filter(Boolean).join(' | ')||d.patente||'';
    filas.push([it.file.name,it.estado,d.hay_problema??'',d.gravedad_maxima??'',d.predominante??'',
      probs,pat,(d.elementos_detectados||[]).map(e=>e.key).join(' | '),
      (d.posibles||[]).map(p=>p.key||p.codigo).join(' | '),(d.en_duda||[]).join(' | '),
      d.hay_reclamo??'',d.foto_valida_estado??'',d.verificacion_activa??'',
      d.verificacion_motivo??'',d.descripcion??'',it.estado==='error'?it.detalle:'',it.trabajo||'']);
  }
  // comillas para separadores y saltos; el apóstrofo inicial neutraliza
  // fórmulas (=, +, -, @) si el CSV se abre en una planilla
  const celda=v=>{
    let s=String(v??'');
    if(/^\s*[=+@-]/.test(s))s="'"+s;
    if(/[",;\r]/.test(s)||s.includes(String.fromCharCode(10)))s='"'+s.replace(/"/g,'""')+'"';
    return s;
  };
  const txt=String.fromCharCode(0xFEFF)+filas.map(f=>f.map(celda).join(',')).join(String.fromCharCode(13,10));
  const blob=new Blob([txt],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='ojo-urbano-resultados.csv';
  a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href),5000);
};
</script></body></html>"""


if __name__ == "__main__":
    estado = "activa" if verificador.disponible() else "desactivada (sin OPENROUTER_API_KEY)"
    print(f"Ojo Urbano -> http://{HOST}:{PORT}  ·  verificación {estado}")
    uvicorn.run(app, host=HOST, port=PORT)
