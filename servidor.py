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
# Cola FIFO de pedidos esperando el cupo. Solo la toca el event loop (un
# único hilo), por eso una deque pelada sin lock alcanza y la justicia FIFO
# no tiene carreras.
_cola = collections.deque()
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


async def _esperar_cupo(huella):
    """Espera el turno FIFO por el cupo. Devuelve "cupo" (quedó tomado) o
    "cache" (un pedido idéntico terminó mientras esperábamos y el resultado
    ya está guardado). Levanta 503 si la cola está llena o se agotó la
    espera. Corre entero en el event loop: _cola no tiene lock y la justicia
    FIFO depende de que un solo hilo la toque."""
    # Sin nadie esperando, el camino rápido de siempre. Con cola, el nuevo
    # se forma atrás: si pudiera tomar el cupo directo se lo robaría a los
    # que llegaron antes.
    if not _cola and _cupos.acquire(blocking=False):
        return "cupo"
    if len(_cola) >= COLA_MAX or ESPERA_CUPO <= 0:
        raise _503("el servidor está ocupado; reintentá en un momento")
    token = object()
    _cola.append(token)
    try:
        vence = time.monotonic() + ESPERA_CUPO
        while True:
            # Si la misma foto terminó mientras esperábamos, no hace falta
            # cupo ni volver a pagarla: el hilo ya la dejó en la caché.
            if _cache_leer(huella) is not None:
                return "cache"
            # Solo el primero de la cola puede tomar el cupo.
            if _cola[0] is token and _cupos.acquire(blocking=False):
                return "cupo"
            if time.monotonic() >= vence:
                raise _503("el servidor está ocupado; reintentá en un momento")
            await asyncio.sleep(0.3)
    finally:
        # Sale de la cola pase lo que pase: también si el cliente abortó y
        # la corrutina se canceló en el sleep. Un token filtrado dejaría la
        # cola llena para siempre.
        try:
            _cola.remove(token)
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
  <div class="ctxhint">Tiene peso propio: si la foto no muestra lo que contás, el reclamo se arma con
    lo que escribiste y la foto se marca como no válida. Podés escribirlo antes o después
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
            <b id="espmain">Analizando la foto</b>
            <div class="esptxt" id="espsub">Modelo local + verificación cruzada con varios modelos de visión.
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
        <div id="poswrap" style="display:none">
          <h3>Posibles (sin confirmar)</h3>
          <div id="poscats"></div>
          <div class="mini">Lo que alguna fuente vio o el contexto sugiere, pero no alcanzó el consenso.
            Sirven para repreguntar, no para reportar.</div>
        </div>
        <details class="det">
          <summary>Cómo se obtuvo este resultado</summary>
          <div class="detbody">
            <div class="estado" id="estado"></div>
            <div id="votos"></div>
            <div class="mini" style="margin:8px 0 0">La confirmación surge del consenso: una categoría queda
              confirmada con al menos 2 fuentes. Lo que ve una sola fuente no se confirma: queda como posible.</div>
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
        de OpenRouter) · <b>1</b> (forzar verificación) · <b>0</b> (sin verificación: respuesta degradada,
        sin clasificación). Las categorías que el contexto describe pero la foto no confirma vuelven en
        <b>categorias_contexto</b>.</div>
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
 python:`import requests\n\nwith open("foto.jpg", "rb") as f:\n    r = requests.post("${O}/clasificar", files={"file": f},\n                      data={"contexto": "vidrios rotos en la vereda"})\ndata = r.json()\nif data["hay_problema"]:\n    print(data["descripcion"])\n    for p in data["problemas"]:\n        print(p.get("key") or p.get("codigo"), p["gravedad"], p["fuentes"])\nelse:\n    print("sin problema:", data["descripcion"])`,
 js:`const fd = new FormData();\nfd.append("file", fileInput.files[0]);\nfd.append("contexto", "vidrios rotos en la vereda"); // opcional\nconst res = await fetch("${O}/clasificar", { method: "POST", body: fd });\nconst d = await res.json();\nif (d.hay_problema) console.log(d.problemas, d.descripcion);`
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
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const chip=(c,extra)=>`<div class="cat${extra?' ctx':''}"><b>${esc(c.nombre)}</b>${c.gravedad?`<span>${c.gravedad}/5 · ${GRAV[c.gravedad]||''}${typeof c.fuentes==='number'?` · ${c.fuentes} fuente${c.fuentes!==1?'s':''}`:''}${c.patente?` · patente <b>${esc(c.patente)}</b>`:''}${c.parte?` · parte <b>${esc(c.parte)}</b>`:''}</span>`:''}</div>`;
let ctrl=null,cronoIv=null,ultimoArchivo=null,reintentoT=null;
// Presupuesto total de espera en cola antes de rendirse y mostrar el error.
const ESPERA_MAX_MS=180000,REINTENTO_MS=6000,SIGUE=Symbol('encola');
function esperaMsg(enCola){
  $('#espmain').textContent=enCola?'Tu foto está en cola':'Analizando la foto';
  $('#espsub').textContent=enCola
    ?'Hay otra foto procesándose en este momento: la tuya espera su turno y se envía sola. No cierres la página.'
    :'Modelo local + verificación cruzada con varios modelos de visión. Suele tardar entre 20 y 60 segundos.';
}
$('#reenviar').onclick=()=>{if(ultimoArchivo)enviar(ultimoArchivo);};
function enviar(f){
  if(ctrl)ctrl.abort();
  if(cronoIv)clearInterval(cronoIv);
  if(reintentoT){clearTimeout(reintentoT);reintentoT=null;}
  ctrl=new AbortController();
  ultimoArchivo=f;
  $('#reenviar').style.display='none';
  $('#err').style.display='none';$('#err').textContent='';
  $('#res').style.display='none';$('#cats').innerHTML='';$('#poscats').innerHTML='';
  $('#estado').textContent='';$('#votos').innerHTML='';$('#concl').textContent='';
  ['descwrap','ctxwrap','preswrap','dudawrap','poswrap'].forEach(id=>$('#'+id).style.display='none');
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
  esperaMsg(false);
  const miCtrl=ctrl;
  intentar();
  function intentar(){
  fetch(O+'/clasificar'+SUF,{method:'POST',body:fd,signal:miCtrl.signal}).then(async r=>{
    // 503 = hay otra foto usando el único lugar de procesamiento. No es un
    // error para quien mira: quedamos en cola y reintentamos solos. El
    // servidor cachea por foto, así que si la nuestra ya terminó en el
    // medio, el reintento vuelve al instante con el resultado.
    if(r.status===503&&Date.now()-t0<ESPERA_MAX_MS){
      // El servidor dice cuándo volver (Retry-After): 5 s si está ocupado,
      // 30 s si está degradado. Si el proxy se comió el header, 6 s.
      const ra=parseInt(r.headers.get('Retry-After'),10);
      const pausa=(isNaN(ra)||ra<1?6:Math.max(6,ra))*1000;
      esperaMsg(true);
      reintentoT=setTimeout(()=>{if(!miCtrl.signal.aborted)intentar();},pausa);
      return SIGUE;
    }
    if(!r.ok){const e=await r.json().catch(()=>null);
      throw new Error(e&&e.detail?e.detail
        :r.status===503?'el servidor sigue ocupado con otras fotos; reintentá en unos minutos'
        :'no pude procesar la foto (HTTP '+r.status+')');}
    return r.json()})
   .then(d=>{
     if(d===SIGUE)return;
     clearInterval(cronoIv);
     $('#espera').style.display='none';$('#res').style.display='block';
     const probs=d.problemas;
     const desc=(d.descartados_por_foto||[]).length;
     // La foto puede no corresponder al reclamo AUNQUE haya problemas: en ese
     // caso salieron del texto, no de la foto, y hay que decirlo.
     const aviso=(d.foto_valida===false&&d.hay_problema)
       ?' La foto no muestra lo que contaste: el reclamo se armó con tu descripción.'
       :(desc&&d.hay_problema?' Se dejaron de lado '+desc+' hallazgo(s) de la foto porque no venían al caso.':'');
     $('#concl').textContent=(d.hay_problema
       ?(probs.length===1?'Se identificó 1 incidencia':'Se identificaron '+probs.length+' incidencias')
         +(d.gravedad_maxima?` · gravedad máxima ${d.gravedad_maxima}/5 (${GRAV[d.gravedad_maxima]})`:'')+'.'
       :d.hay_reclamo?'Hay un reclamo en el texto, pero sin problema confirmado en la foto.'
       :(desc?'La foto muestra otra cosa y lo que contaste no corresponde a ningún reclamo.'
             :'No se identificaron problemas en la foto.'))+aviso
       +(d.patente?' Patente leída: '+d.patente+'.':'');
     $('#cats').innerHTML=probs.map(c=>chip(c)).join('');
     if(d.descripcion){$('#desc').textContent=d.descripcion;$('#descwrap').style.display='block';}
     const cc=d.categorias_contexto||[];
     const RESPALDO={compatible:'la foto es compatible con el reclamo',neutral:'no visible en la foto',contradice:'la foto lo contradice'};
     if(cc.length){$('#ctxcats').innerHTML=cc.map(c=>`<div class="cat ctx"><b>${esc(c.nombre)}</b><span>${RESPALDO[c.respaldo_visual]||'según el contexto'}</span></div>`).join('');$('#ctxwrap').style.display='block';}
     const pres=d.elementos_detectados||[];
     if(pres.length){$('#prescats').innerHTML=pres.map(c=>`<div class="cat"><b>${esc(c.nombre)}</b></div>`).join('');$('#preswrap').style.display='block';}
     if(d.en_duda.length){$('#dudas').textContent='Reportadas por una sola fuente y sin decisión del árbitro: '
       +d.en_duda.map(k=>k.replace(/_/g,' ')).join(', ')+'. No se incluyen entre las confirmadas.';
       $('#dudawrap').style.display='block';}
     const pos=d.posibles||[];
     if(pos.length){$('#poscats').innerHTML=pos.map(p=>`<div class="voto"><b>${esc((p.nombre||p.key||p.codigo||'').toString().replace(/_/g,' '))}</b>${
       p.origen==='contexto_vecinal'?' · sugerida por tu texto':''}${
       p.motivo?`<span> — ${esc(p.motivo)}</span>`:''}</div>`).join('');
       $('#poswrap').style.display='block';}
     $('#estado').textContent=d.verificacion_activa
       ?'Verificación cruzada completada en '+Math.round((Date.now()-t0)/1000)+' s.'
       :'Sin verificación cruzada ('+(d.verificacion_motivo||'desactivada')+'): no hay resultado confiable, solo un acuse de recibo.';
     if(d.verificacion_activa){$('#votos').innerHTML=(d.modelos||[]).map(x=>x.ok
       ?`<div class="voto"><b>${esc(x.modelo)}</b>: ${x.categorias.length?esc(x.categorias.map(c=>c.key.replace(/_/g,' ')).join(', ')):'sin hallazgos'}${
         x.descripcion?`<span> — ${esc(x.descripcion)}</span>`:''}</div>`
       :`<div class="voto"><b>${esc(x.modelo)}</b>: no respondió</div>`).join('');}
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
