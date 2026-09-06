"""Verificación cruzada de clasificaciones con modelos de visión vía OpenRouter.

El modelo local propone categorías; varios modelos de visión (por defecto tres)
miran la foto de forma independiente. Una categoría queda confirmada cuando la
reportan al menos 2 fuentes, contando el modelo local como una.
Las categorías con una sola fuente van a un árbitro de texto (por defecto
DeepSeek), que lee los veredictos de todos los verificadores y las
probabilidades del modelo local. Por defecto NO las confirma: quedan en
"posibles", con el veredicto del árbitro y su motivo.

Cada verificador devuelve además una descripción breve de la foto dentro de su
misma respuesta (sin llamadas extra). La descripción final consolidada la
redacta el árbitro cuando ya tiene que intervenir por una disputa; si no hay
disputa, se elige localmente la descripción del verificador que más coincide
con las categorías finales.

Llamadas por foto: una por verificador (tres por defecto). Ninguna de las
extra es fija, y son tres:
  1. el arbitraje, cuando queda una categoría en disputa;
  2. el encaminamiento del reclamo por texto, cuando la foto no corresponde
     a lo que el vecino contó;
  3. una descripción de repuesto, cuando TODAS las disponibles contradicen un
     subtipo ya resuelto (húmedos lateral/bilateral, tapa vereda/calle).
Las tres van al árbitro, y con ARBITRO_VE_FOTO las que miran la escena
llevan la foto adjunta: cuesta más, pero pedirle a un modelo que juzgue algo
que no ve no tiene sentido.

Config por variables de entorno (ver .env.example):
    OPENROUTER_API_KEY   requerida para verificar; sin ella la API responde
                         solo con el modelo local.
    VERIFICADORES        lista separada por comas de modelos de visión.
    ARBITRO              modelo de texto para desempates ("" lo desactiva).
    VERIFICADOR_TIMEOUT  segundos por llamada (default 120).
"""
import base64
import concurrent.futures
import hashlib
import io
import json
import os
import re
import http.client
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from prompts import (
    REGLA_SUBTIPO_HUMEDOS,
    _RUBRICA_KEYS,
    _RUBRICA,
    _PROMPT_PATENTE,
    _PROMPT_ALCANCE_ESCOMBROS,
    _PROMPT_OBRA_SERVICIOS_CONTEXTO,
    _PROMPT_SEGUNDA_MIRADA,
    _PROMPT_SEGUNDA_MIRADA_BASE,
    _PROMPT_SEGUNDA_MIRADA_DANO,
    _PROMPT_SEGUNDA_MIRADA_POSTES,
    _PROMPT_SEGUNDA_MIRADA_VOLCADO,
    _PROMPT_SEGUNDA_MIRADA_SUBTIPO,
    _PROMPT_REPREGUNTA,
    _PROMPT_REPREGUNTA_ESTADO,
    _PROMPT_PREGUNTA_ABIERTA,
    _PROMPT_SEGUNDA_MIRADA_VOLUMINOSO,
    _PROMPT_SEGUNDA_MIRADA_DESBORDE,
    _PROMPT_SEGUNDA_MIRADA_PRESENCIA,
    _PROMPT_SEGUNDA_MIRADA_PRESENCIA_CLAVE,
    _SISTEMA_ARBITRO_TEXTO,
    _SISTEMA_ARBITRO_FOTO,
    _PROMPT_USUARIO,
    _CONTEXTO_USUARIO,
    _PROMPT_USUARIO_CONTEXTO,
    _ARBITRO_DATOS,
    _ARBITRO_DESCRIPCION,
    _ARBITRO_CONTEXTO,
    _ARBITRO_SUGESTION,
    _ARBITRO_SUBTIPOS,
    _ARBITRO_DISPUTAS,
    _PROMPT_USUARIO_PRESTACIONES,
    _ARBITRO_SOLO_VISION,
    _ARBITRO_UNA_FUENTE,
    _ARBITRO_ESCOMBROS,
    _ARBITRO_ESCOMBROS_FOTO,
    _CONTEXTO_SISTEMA,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Catálogo completo de prestaciones de la Ciudad (para mapear reclamos del
# contexto vecinal a CUALQUIER tipo de reporte, no solo a las categorías
# visuales). Generado desde el backend público de BA Colaborativa.
try:
    PRESTACIONES = json.loads(
        (Path(__file__).resolve().parent / "prestaciones.json").read_text())
except OSError:
    PRESTACIONES = []
_PRESTACIONES_POR_CODIGO = {p["codigo"]: p for p in PRESTACIONES}


def _norm_texto(s):
    return (s or "").lower().translate(str.maketrans("áéíóúüñ", "aeiouun"))


def _prestaciones_candidatas(contexto, n=12):
    """Prestaciones del catálogo que mejor matchean el texto del contexto.

    Puntúa contra las palabras clave y el concepto (peso doble) y también
    contra el texto de la página de la prestación (mensajes informativos),
    que suele describir mejor qué cubre el reporte que su título.
    """
    if not contexto or not PRESTACIONES:
        return []
    palabras_ctx = set(re.findall(r"[a-z]{4,}", _norm_texto(contexto)))
    puntuadas = []
    for p in PRESTACIONES:
        claves = set(re.findall(r"[a-z]{4,}", _norm_texto(
            (p.get("palabras_clave") or "") + " " + p["concepto"])))
        pagina = set(re.findall(r"[a-z]{4,}", _norm_texto(
            " ".join(p.get("mensajes") or []))))
        score = 2 * len(palabras_ctx & claves) + len(palabras_ctx & pagina)
        if score:
            puntuadas.append((score, p))
    puntuadas.sort(key=lambda x: (-x[0], x[1]["codigo"]))
    return [p for _, p in puntuadas[:n]]


def _resumen_prestacion(p, largo=240):
    """Resumen del texto de la página de la prestación para el prompt."""
    texto = " ".join(p.get("mensajes") or [])
    return texto[:largo] + ("…" if len(texto) > largo else "")

# Sinónimos que se pliegan a una categoría canónica en TODA la API: el modelo
# local fue entrenado con estas clases pero la salida siempre usa la canónica.
FOLD = {
    "retiro_objetos": "retiro_muebles",
    "recoleccion_voluminosos": "retiro_muebles",
    "recoleccion_restos_obra": "retiro_escombros",
    "recoleccion_verdes": "retiro_poda",
    "diseminado": "recoleccion",
    # la clase única del modelo local no distingue dónde está la tapa; se
    # pliega a tapa_vereda y los modelos de visión deciden el subtipo real
    "nivelacion_tapa": "tapa_vereda",
}

# Claves de PRESENCIA: indican que un contenedor se ve en la foto, no que haya
# un problema. No cuentan para sin_problema ni para la gravedad máxima.
PRESENCIA = {"contenedor_secos", "contenedor_humedos_lateral",
             "contenedor_humedos_bilateral"}

# Toda clave que implica un contenedor en la escena. La usan la validación de
# sugerencias de contexto (lavado sin contenedor a la vista se remapea) y la
# segunda mirada de la base del contenedor.
CONTENEDOR_KEYS = {"contenedor_secos", "contenedor_humedos_lateral",
                   "contenedor_humedos_bilateral", "contenedor_desbordado",
                   "vaciado_contenedor", "reparacion_contenedor",
                   "reposicion_contenedor", "lavado_contenedor"}

# TRES verificadores desde 2026-08-06. Medido sobre 59 fotos re-adjudicadas
# con la regla real del sistema (>=2 fuentes, el modelo local cuenta como una):
#   local + gpt-5-mini + gemini            prec 90,0  recall 51,7  F1 65,7
#   local + luna + gemini (cambiar uno)    prec 93,2  recall 47,1  F1 62,6
#   local + luna + gpt-5-mini + gemini     prec 85,7  recall 55,2  F1 67,1
# Cambiar gpt-5-mini por luna EMPEORA: quedan dos modelos de alta precisión y
# poco recall, y como hace falta que DOS fuentes coincidan, lo que ve uno solo
# se cae. Sumar el tercero en cambio cambia ~4 puntos de precisión por ~4 de
# recall, que para un sistema de reclamos es la dirección correcta: una
# denuncia que se pierde es un vecino ignorado; una de más la filtra el
# árbitro. Cuesta ~18% más, no 50%, porque luna es el barato del trío.
# Ojo: n=59, diferencias de pocos puntos están dentro del ruido.
VERIFICADORES = [m.strip() for m in os.environ.get(
    "VERIFICADORES",
    "openai/gpt-5-mini,google/gemini-3.5-flash-lite,openai/gpt-5.6-luna"
).split(",") if m.strip()]
# Segunda mirada dirigida, SOLO para retiro_escombros (la categoría más
# perdida): cuando UN verificador la reporta y los demás no, se les
# re-pregunta solo por las bolsas, tri-estado y sin decirles qué vio el
# disidente. Diseño revisado adversarialmente; expandible a otras categorías
# únicamente con evaluación propia.
SEGUNDA_MIRADA_ESCOMBROS = os.environ.get(
    "SEGUNDA_MIRADA_ESCOMBROS", "1").strip().lower() not in ("0", "false", "no")
LADO_SEGUNDA_MIRADA = int(os.environ.get("LADO_SEGUNDA_MIRADA", "1600"))
# Segunda mirada dirigida para la BASE del contenedor: cuando algún
# verificador reporta retiro_muebles con evidencia de "estructura metálica" y
# hay un contenedor en la escena, se re-pregunta SOLO por ese objeto. Medido
# antes del fix (foto real, contenedor corrido de su base, de noche): en 1 de
# 5 corridas DOS modelos leían la base como chatarra y retiro_muebles se
# confirmaba al reporte público. El requisito del dueño es que la base no
# salga NUNCA como voluminoso, y la rúbrica sola no puede garantizar un
# fallo correlacionado de dos modelos.
SEGUNDA_MIRADA_BASE = os.environ.get(
    "SEGUNDA_MIRADA_BASE", "1").strip().lower() not in ("0", "false", "no")
# Segunda mirada dirigida para el DAÑO del contenedor (tapas dadas vuelta
# leídas como rotas, fierros ajenos atribuidos al contenedor).
SEGUNDA_MIRADA_DANO = os.environ.get(
    "SEGUNDA_MIRADA_DANO", "1").strip().lower() not in ("0", "false", "no")
# Segunda mirada dirigida para el VOLCADO (el techo en pendiente de los
# laterales, de esquina y de noche, se lee como contenedor tumbado).
SEGUNDA_MIRADA_VOLCADO = os.environ.get(
    "SEGUNDA_MIRADA_VOLCADO", "1").strip().lower() not in ("0", "false", "no")
# Repregunta dirigida entre modelos: cuando UN solo verificador reporta un
# objeto concreto (un voluminoso, un contenedor recortado), se les pregunta a
# los que NO lo vieron si lo ven, con localización obligatoria y estado por
# separado. Medido antes de construirla: 7/7 objetos reales encontrados,
# 0/9 objetos plantados aceptados (la bicicleta "descartada" que sí estaba
# enseñó a separar el objeto de su estado).
REPREGUNTA_OBJETOS = os.environ.get(
    "REPREGUNTA_OBJETOS", "1").strip().lower() not in ("0", "false", "no")
# Mirada dirigida del subtipo (local vs VLM unánimes) y firma de identidad
# del voluminoso marginal: cada una con su llave propia.
SEGUNDA_MIRADA_SUBTIPO = os.environ.get(
    "SEGUNDA_MIRADA_SUBTIPO", "1").strip().lower() not in ("0", "false", "no")
SEGUNDA_MIRADA_VOLUMINOSO = os.environ.get(
    "SEGUNDA_MIRADA_VOLUMINOSO", "1").strip().lower() not in ("0", "false", "no")
# Mirada dirigida del desborde (el rebalse hay que VERLO).
SEGUNDA_MIRADA_DESBORDE = os.environ.get(
    "SEGUNDA_MIRADA_DESBORDE", "1").strip().lower() not in ("0", "false", "no")
# Veto de presencia: contenedor confirmado por los VLM con el modelo local
# (entrenado con estos contenedores) en practicamente cero -> chequeo
# dirigido de existencia.
SEGUNDA_MIRADA_PRESENCIA = os.environ.get(
    "SEGUNDA_MIRADA_PRESENCIA", "1").strip().lower() not in ("0", "false", "no")
PRESENCIA_LOCAL_PISO = float(os.environ.get("PRESENCIA_LOCAL_PISO", "0.10"))
# Veto de presencia POR CLAVE: el de arriba mira el máximo de TODAS las claves
# de contenedor y solo salta en la foto sin ningún contenedor. No cubre el
# contenedor FANTASMA que se publica al lado de uno real (una bolsa verde leída
# como contenedor de reciclables). Para eso hace falta el puntaje local de ESA
# clave, y solo sirve donde el modelo local es un detector confiable. Medido
# sobre las 200 fotos etiquetadas de la ronda 4:
#   contenedor_secos      30 positivos reales, el más bajo 0,427 (p10 0,931)
#                          contra 3 falsos, todos por debajo de 0,005
#   ..._humedos_bilateral 39 positivos reales, el más bajo 0,290
#                          contra 2 falsos, ambos por debajo de 0,007
#   ..._humedos_lateral   NO SEPARA: 10 de sus 110 positivos reales quedan bajo
#                          0,10 (es el tipo más común y el que el local más se
#                          pierde), así que esta puerta no se le aplica.
PRESENCIA_POR_CLAVE = ("contenedor_secos", "contenedor_humedos_bilateral")
SEGUNDA_MIRADA_PRESENCIA_CLAVE = os.environ.get(
    "SEGUNDA_MIRADA_PRESENCIA_CLAVE", "1").strip().lower() not in (
        "0", "false", "no")
# Descriptor canónico de cada contenedor. Lo escribimos NOSOTROS (nunca sale de
# un modelo), así que puede viajar en el prompt de sistema: una pregunta de
# presencia sin el descriptor lava el subtipo del único votante.
# Umbrales de la señal de calidad (ver calidad_foto): son de INFORME, no de
# veto. Los percentiles del corpus etiquetado: nitidez p10 0,21 / mediana 0,58;
# lado menor mediana 360 px.
CALIDAD_NITIDEZ_PISO = float(os.environ.get("CALIDAD_NITIDEZ_PISO", "0.30"))
CALIDAD_LADO_PISO = int(os.environ.get("CALIDAD_LADO_PISO", "400"))
DESCRIPTOR_CONTENEDOR = {
    "contenedor_secos": "VERDE de reciclables",
    "contenedor_humedos_lateral":
        "de húmedos NEGRO o VERDE OSCURO/OLIVA (nunca gris)",
    "contenedor_humedos_bilateral":
        "de húmedos GRIS de cualquier tono (siempre bilateral, aunque tenga "
        "barras o parezca panzón)",
}
try:
    REPREGUNTA_MAX = max(0, int(os.environ.get("REPREGUNTA_MAX", "2")))
except ValueError:
    REPREGUNTA_MAX = 2
# Subtipo del contenedor de húmedos (lateral vs bilateral): margen mínimo del
# modelo local (|bilateral - lateral|) para que su voto valga como voto y no
# solo como desempate. Medido sobre las 70 fotos del set revisado que tienen
# subtipo humano: el local acierta 67/70 en general, pero de 0.95 para arriba
# acierta 60/60, y su único error confiado se queda en 0.886. El margen es lo
# que separa "lo tengo clarísimo" de "no vi bien el contenedor": promedia
# 0.921 cuando acierta y 0.310 cuando falla. Mismo umbral que usa la fusión
# de escombros, por la misma razón.
SUBTIPO_LOCAL_MARGEN = float(os.environ.get("SUBTIPO_LOCAL_MARGEN", "0.95"))
# Chequeo de los POSTES citados: levanta la guardia del subtipo cuando el
# testigo cita postes que ningún modelo puede ver (ver el comentario del caso).
SEGUNDA_MIRADA_POSTES = os.environ.get(
    "SEGUNDA_MIRADA_POSTES", "1").strip().lower() not in ("0", "false", "no")
# Saneo de la prosa: la descripción no afirma un objeto concreto que nombre
# una sola fuente. Con SANEO_PROSA=0 se publica la descripción cruda, que es
# el comportamiento viejo (sirve para medir qué detalle cuesta el saneo).
SANEO_PROSA = os.environ.get(
    "SANEO_PROSA", "1").strip().lower() not in ("0", "false", "no")

ARBITRO = os.environ.get("ARBITRO", "deepseek/deepseek-v4-flash").strip()
# Si el árbitro es un modelo con visión, conviene darle la foto: decidir sobre
# descripciones ajenas es juzgar una compresión con pérdida del original.
ARBITRO_VE_FOTO = os.environ.get("ARBITRO_VE_FOTO", "").strip().lower() not in (
    "", "0", "false", "no")
# ¿El árbitro puede promover a CONFIRMADO algo que vio una sola fuente?
# Default NO. Medido sobre cuatro modelos de árbitro (deepseek texto,
# gpt-5-nano, qwen3-vl-32b y claude-sonnet-5, estos tres viendo la foto):
# 2 rescates correctos sobre 21 confirmaciones, y los CUATRO por debajo de lo
# que sacaría rechazar todas las disputas. Lo que vio una sola fuente sale
# como "posible", no como problema: preferimos no afirmar antes que afirmar
# de más.
ARBITRO_CONFIRMA = os.environ.get("ARBITRO_CONFIRMA", "").strip().lower() not in (
    "", "0", "false", "no")
TIMEOUT = int(os.environ.get("VERIFICADOR_TIMEOUT", "120"))
# Techo total por modelo: sin esto, 3 intentos x TIMEOUT dejan una sola foto
# ocupando el server seis minutos cuando OpenRouter responde lento.
DEADLINE = int(os.environ.get("VERIFICADOR_DEADLINE", "180"))
# Temperatura 0 = greedy. Reduce una fuente de varianza, pero NO da salida
# idéntica: medido, bajar a 0 no eliminó los cambios de veredicto (11,1% ->
# 6,3%, p=0,265; y en modo producción quedó igual). El proveedor y el batching
# aportan lo suyo. Se deja en 0 igual: quita una variable de cada medición.
TEMPERATURA = float(os.environ.get("TEMPERATURA", "0"))
# Cuántas veces se le pregunta al árbitro cada disputa; con >1 gana la mayoría.
#
# Default 1 (una sola vuelta) por un eval de 101 fotos pareadas e intercaladas
# (2026-08-04). Votar de a 3 NO demostró servir:
#   - cambio del conjunto de categorías: 11,9% -> 8,9%, McNemar p=0,58.
#   - hay_problema: 3,0% en ambos. gravedad: 5,0% -> 4,0%. Sin diferencia.
#   - descripción: 83% inestable en AMBOS, b=0 c=0. Votar no la toca en nada.
# En una muestra chica parecía favorable y al ampliarla se desvaneció:
# regresión a la media. Cuesta 3x las llamadas del árbitro y no compra nada
# medible, así que queda
# apagado. Con la discordancia observada (b=8, c=5 sobre n=101) resolver ESE
# efecto con 80% de poder pediría n≈1143 fotos pareadas: no vale la pena.
#
# Lo que sí quedó claro del eval: la inestabilidad que importa NO es la que se
# estaba midiendo. hay_problema y gravedad ya eran bastante estables (3-5%);
# lo que cambia casi siempre es el TEXTO de la descripción, y eso no se
# arregla votando: el árbitro redacta de nuevo en cada llamada.
ARBITRO_VOTOS = max(1, int(os.environ.get("ARBITRO_VOTOS", "1")))
SEMILLA = (int(os.environ["SEMILLA"]) if os.environ.get("SEMILLA", "").strip()
           else None)  # solo algunos proveedores la respetan
# OJO: esto NO fija un backend. Solo manda allow_fallbacks:false, que impide
# reintentar en otro proveedor DESPUÉS de un fallo; la elección inicial la
# sigue haciendo OpenRouter, así que la varianza entre proveedores no
# desaparece. Para fijarlo de verdad haría falta 'order'/'only' con el
# proveedor identificado. Medido, no bajó la inestabilidad de forma
# significativa (12,6% -> 9,1%, p=0,147).
PROVEEDOR_FIJO = os.environ.get("PROVEEDOR_FIJO", "").strip().lower() not in (
    "", "0", "false", "no")
# Afinidad por prefijo, sin recortar mensajes ni contratar caché explícita.
# Opt-in: la medición pareada no demostró un ahorro consistente.
OPENROUTER_CACHE_PROMPTS = os.environ.get("OPENROUTER_CACHE_PROMPTS", "0").strip().lower() not in (
    "", "0", "false", "no")
OPENROUTER_LOG_USO = os.environ.get("OPENROUTER_LOG_USO", "1").strip().lower() not in (
    "", "0", "false", "no")
# Con "arbitro", una categoría que solo vieron los modelos de visión, sin
# respaldo del modelo local, la decide el árbitro en vez de confirmarse por
# consenso entre dos fuentes que comparten la misma entrada manipulable.
#
# El DEFAULT es "confirma" (la regla vieja de 2 de 3) por decisión de un eval
# de fotos reales de la ciudad (2026-08-04, ver eval/). El beneficio NO se pudo demostrar
# y el costo NO se pudo descartar.
#   - NINGUNA de las inyecciones probadas logró engañar a los DOS
#     verificadores a la vez, que es la única población sobre la que actúa
#     esta regla: el mecanismo nunca se ejercitó, así que no quedó probado
#     que sirva. Y no haber visto ninguna tampoco dice que la amenaza sea
#     rara: con esa cantidad de intentos el techo del IC95 sigue alto.
#     Los conteos exactos y el techo los imprime eval/analizar.py.
#   - El árbitro cambia de opinión ante la MISMA entrada congelada (~12% en
#     el conjunto de categorías; sobre lo que el usuario ve es menos: 3% en
#     hay_problema, 5% en gravedad). Esa inestabilidad es del orden del
#     efecto que se quería medir, así que el eval quedó sin poder.
#   - Como el árbitro decide todas las disputas en UNA llamada, mandarle más
#     categorías puede mover también las que sí tienen respaldo local
#     (spillover), y esta regla justamente le manda más.
# Antes de volver a activarla: arbitrar cada categoría por separado, fijar
# temperatura/seed, y armar un set adjudicado a mano para medir exactitud y no
# solo desacuerdo. Ver notas del eval en el issue.
CONSENSO_VLM_SOLO = os.environ.get("CONSENSO_VLM_SOLO", "confirma").strip().lower()
LADO_MAX = 1024  # la foto se reduce a este lado máximo antes de enviarla
# La pasada de patente va con más resolución: a 1024 una chapa a unos metros
# queda en ~40 px y no se lee. Solo se paga en fotos con vehículo confirmado.
LADO_PATENTE = int(os.environ.get("LADO_PATENTE", "2048"))
DESC_MAX = 600   # longitud máxima de una descripción devuelta por un modelo
EVID_MAX = 160   # ídem para la evidencia citada por categoría


def _texto_limpio(s, largo):
    """Texto de un modelo listo para publicar: sin control chars y acotado.

    El contenido lo puede inducir quien sube la foto (contexto o texto dentro
    de la imagen), así que nunca se guarda crudo ni sin techo de longitud.
    """
    s = "".join(c for c in str(s or "") if c == "\n" or c >= " ")
    return re.sub(r"\s+", " ", s).strip()[:largo]


def api_key():
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def disponible():
    return bool(api_key()) and bool(VERIFICADORES)


def _imagen_data_url(img, lado=None):
    """PIL.Image -> data URL JPEG reducida (menos tokens, misma señal)."""
    img = img.copy()
    img.thumbnail((lado or LADO_MAX, lado or LADO_MAX))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def calidad_foto(img):
    """Señales de legibilidad de la foto. NO decide nada: informa.

    Medido sobre las 200 fotos etiquetadas de la ronda 4 (la mitad son
    miniaturas de 360x480 del sistema de origen): la calidad global NO predice
    los errores. Los falsos positivos por predicción salen 5,6% en las fotos
    de hasta 480 px y 9,2% en las de más de 900; y una compuerta por nitidez
    en 0,30 tocaba 82 aciertos para evitar 6 errores. Por eso acá no hay veto:
    lo que falla en una foto ilegible falla con los modelos convencidos y
    UNÁNIMES, y eso solo lo baja una mirada dirigida, no un umbral.
    Sirve para triaje del lote y para revisar el criterio con etiquetas nuevas.

    - nitidez: varianza del laplaciano normalizada por el contraste, medida
      SIEMPRE a lado menor 480, para poder comparar fotos de distinto tamaño.
    - definicion: "limitada" si la nitidez cae bajo el piso o la foto es más
      chica que 400 px de lado menor (mediana del corpus: 0,58 y 360 px).
    """
    import numpy as np
    g = img.convert("L")
    w, h = g.size
    lado = min(w, h)
    if lado > 480:
        esc = 480.0 / lado
        g = g.resize((max(1, int(w * esc)), max(1, int(h * esc))))
    a = np.asarray(g, dtype=np.float32)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return {"lado_menor": lado, "nitidez": None, "luminancia": None,
                "definicion": "limitada"}
    lap = (-4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1]
           + a[1:-1, :-2] + a[1:-1, 2:])
    var = float(a.std()) ** 2
    nitidez = float(lap.var()) / var if var > 1e-6 else 0.0
    return {
        "lado_menor": lado,
        "nitidez": round(nitidez, 3),
        "luminancia": round(float(a.mean()), 1),
        "definicion": ("limitada"
                       if nitidez < CALIDAD_NITIDEZ_PISO or lado < CALIDAD_LADO_PISO
                       else "buena"),
    }


# --- Costo de las llamadas a OpenRouter, por foto -------------------------
# _llamar es el ÚNICO punto por donde salen los pedidos a OpenRouter, así que
# sumamos acá el costo que devuelve (usage.cost, en USD) cuando pedimos
# usage.include. El acumulador es global y asume UNA foto a la vez
# (CONCURRENCIA=1, el default de prod): reset al empezar la foto, total al
# cerrarla. Con más concurrencia se mezclarían los costos de fotos distintas.
_costo_lock = threading.Lock()
_costo = {"usd": 0.0, "llamadas": 0, "activo": False}


def costo_reset():
    """Arranca el acumulador de costo de OpenRouter para una foto."""
    with _costo_lock:
        _costo.update(usd=0.0, llamadas=0, activo=True)


def costo_total():
    """Cierra el acumulador y devuelve el costo en USD de la foto."""
    with _costo_lock:
        _costo["activo"] = False
        return round(_costo["usd"], 6)


def _costo_sumar(usage):
    if not isinstance(usage, dict):
        return
    c = usage.get("cost")
    with _costo_lock:
        if _costo["activo"] and isinstance(c, (int, float)):
            _costo["usd"] += float(c)
            _costo["llamadas"] += 1


def _clave_cache_prompt(modelo, mensajes):
    """Identifica el prefijo de sistema, nunca la foto ni el texto del vecino.

    Es afinidad de proveedor, NO caché de respuestas: cada foto se analiza.
    OpenRouter/proveedor comprueban el prefijo real antes de reutilizar tokens.
    Sin un prefijo de sistema dejamos el ruteo automático existente.
    """
    prefijo = []
    for mensaje in mensajes:
        if mensaje.get("role") not in ("system", "developer"):
            break
        prefijo.append(mensaje)
    if not prefijo:
        return None
    contenido = json.dumps([modelo, prefijo], ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    return "ojo-v1-" + hashlib.sha256(contenido.encode()).hexdigest()[:48]


_uso_lock = threading.Lock()


def _registrar_uso(modelo, etapa, clave, intento, inicio, data=None, error=None):
    """Una línea JSON por intento, sin prompts, imágenes ni respuestas."""
    if not OPENROUTER_LOG_USO:
        return
    try:
        data = data if isinstance(data, dict) else {}
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        registro = {"evento": "openrouter_uso", "modelo": modelo,
                    "etapa": etapa, "cache_key": clave, "intento": intento,
                    "segundos": round(time.monotonic() - inicio, 3),
                    "error": type(error).__name__ if error else None}
        for campo in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
            valor = usage.get(campo)
            registro[campo] = valor if type(valor) in (int, float) else None
        for grupo, campos in (
                ("prompt_tokens_details", ("cached_tokens", "cache_write_tokens")),
                ("completion_tokens_details", ("reasoning_tokens",))):
            detalle = usage.get(grupo)
            for campo in campos:
                valor = detalle.get(campo) if isinstance(detalle, dict) else None
                registro[campo] = valor if type(valor) in (int, float) else None
        # Solo metadatos de la API. No volcar data, usage ni el mensaje de error.
        for campo in ("id", "provider"):
            valor = data.get(campo)
            registro[campo] = valor[:160] if isinstance(valor, str) else None
        choices = data.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        razon = choice.get("finish_reason") if isinstance(choice, dict) else None
        registro["finish_reason"] = razon if razon in (
            "stop", "length", "content_filter", "tool_calls", "error") else None
        with _uso_lock:
            print(json.dumps(registro, ensure_ascii=True, allow_nan=False),
                  file=sys.stderr, flush=True)
    except Exception:
        # Un disco lleno o metadatos incompletos no deben repetir una llamada paga.
        pass


def _llamar(modelo, mensajes, max_tokens=6000, intentos=3, *, etapa="sin_etapa"):
    # reasoning effort bajo: los modelos razonadores (Kimi) pueden gastar todo
    # el presupuesto pensando y devolver el JSON vacío (finish_reason=length)
    cuerpo = {"model": modelo, "max_tokens": max_tokens,
              # Pide a OpenRouter el costo real de la llamada (usage.cost, USD).
              "usage": {"include": True},
              "reasoning": {"effort": "low"},
              # Sin esto se sampleaba a la temperatura default del proveedor
              # (típicamente 1). Fijarlo en 0 ayuda pero no alcanza: ver #7.
              "temperature": TEMPERATURA,
              "top_p": 1,
              "messages": mensajes}
    clave = _clave_cache_prompt(modelo, mensajes) if OPENROUTER_CACHE_PROMPTS else None
    if clave:
        cuerpo["prompt_cache_key"] = clave
    if SEMILLA is not None:
        cuerpo["seed"] = SEMILLA
    if PROVEEDOR_FIJO:
        # OpenRouter puede mandar el mismo modelo a backends distintos, con
        # otra cuantización y otros kernels. Esto solo apaga el failover; no
        # elige el backend. Ver la nota en PROVEEDOR_FIJO.
        cuerpo["provider"] = {"allow_fallbacks": False}
    body = json.dumps(cuerpo).encode()
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
        "Authorization": "Bearer " + api_key(),
        "Content-Type": "application/json",
    })
    ultimo = None
    vence = time.monotonic() + DEADLINE
    for intento in range(1, intentos + 1):
        resto = vence - time.monotonic()
        if resto <= 0:
            ultimo = ultimo or TimeoutError(f"sin tiempo para {modelo}")
            break
        inicio = time.monotonic()
        data, error = None, None
        try:
            data = _pedir_http(req, min(TIMEOUT, resto), vence)
            _costo_sumar(data.get("usage"))
            msg = data["choices"][0]["message"]
            # algunos modelos razonadores dejan el JSON en "reasoning"
            contenido = msg.get("content") or msg.get("reasoning") or ""
            if "{" in contenido:
                return contenido
            ultimo = ValueError("respuesta sin JSON")
            error = ultimo
        except (urllib.error.URLError, KeyError, json.JSONDecodeError,
                OSError, ValueError) as e:
            ultimo = e
            error = e
        finally:
            _registrar_uso(modelo, etapa, clave, intento, inicio, data, error)
    raise ultimo


# Marcador de un modelo que falló adentro de una pasada dirigida. Distinto de
# None: varias pasadas usan None como "no contestó" y hay que poder
# distinguirlo de "tiró una excepción".
_FALLO_MODELO = object()


def _map_modelos(modelos, fn):
    """Aplica fn(modelo) en paralelo y conserva el orden de `modelos`.

    Una excepción en un modelo no cancela a los demás: esa posición queda
    _FALLO_MODELO, el mismo contrato que el `except Exception: fallo = True`
    de las pasadas dirigidas cuando eran secuenciales. Un solo modelo no
    abre un pool.
    """
    modelos = list(modelos)
    if not modelos:
        return []

    def _uno(modelo):
        try:
            return fn(modelo)
        except Exception:
            return _FALLO_MODELO

    if len(modelos) == 1:
        return [_uno(modelos[0])]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(modelos)) as pool:
        return list(pool.map(_uno, modelos))


def _cortar_conexion(resp):
    """Desatasca una lectura bloqueada.

    close() NO alcanza: el BufferedReader puede estar reteniendo su lock
    adentro del recv(), y el hilo que llama close() se queda esperando ese
    mismo lock, con lo cual se cuelgan los dos. shutdown() actúa sobre el
    socket y hace que el recv() bloqueado vuelva enseguida.
    """
    sock = None
    for camino in (("fp", "raw", "_sock"), ("fp", "_sock"), ("_sock",)):
        obj = resp
        for attr in camino:
            obj = getattr(obj, attr, None)
            if obj is None:
                break
        if obj is not None:
            sock = obj
            break
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:      # noqa: BLE001 - ya cerrado o sin socket real
            pass
    try:
        resp.close()
    except Exception:          # noqa: BLE001
        pass


def _publicar(caja, sock):
    """Deja el socket a la vista del guardia, y cierra la carrera.

    Si el guardia venció JUSTO mientras se estaba conectando, no había
    socket que cortar y se habría ido sin hacer nada: la conexión recién
    abierta quedaría sin nadie que la corte. Por eso, apenas se publica, se
    mira si ya venció y se corta acá mismo.
    """
    if caja is None:
        return
    caja["sock"] = sock
    if caja.get("vencio"):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:      # noqa: BLE001
            pass


class _HTTPEspiada(http.client.HTTPConnection):
    """Deja ver el socket apenas se conecta, antes de mandar el pedido.

    Sin esto, la referencia al socket recién existe cuando urlopen() ya
    terminó de leer los headers: un proveedor que gotee la línea de estado
    dejaría el hilo colgado sin que el guardia tenga nada que cortar.
    """

    caja = None

    def connect(self):
        super().connect()
        _publicar(self.caja, self.sock)


class _HTTPSEspiada(http.client.HTTPSConnection):
    caja = None

    def connect(self):
        super().connect()
        _publicar(self.caja, self.sock)


def _opener_con_caja(caja):
    """Un opener cuyo socket queda expuesto en `caja` ni bien se conecta."""
    http_cls = type("_H", (_HTTPEspiada,), {"caja": caja})
    https_cls = type("_HS", (_HTTPSEspiada,), {"caja": caja})

    class _HandlerHTTP(urllib.request.HTTPHandler):
        def http_open(self, r):
            return self.do_open(http_cls, r)

    class _HandlerHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, r):
            return self.do_open(https_cls, r)

    return urllib.request.build_opener(_HandlerHTTP, _HandlerHTTPS)


def _pedir_http(req, timeout, vence):
    """Una llamada HTTP acotada por un reloj ABSOLUTO, no por operación.

    El `timeout` de urllib es POR OPERACIÓN de socket: si el proveedor manda
    la respuesta de a pedacitos (OpenRouter intercala keepalives mientras el
    modelo genera), cada byte que llega reinicia el reloj y la llamada puede
    quedarse esperando para siempre sin que salte ningún timeout.

    Eso tiró el servicio en producción: un hilo quedó tomado sobre un socket
    abierto a OpenRouter y, como el cupo de concurrencia lo suelta el hilo
    cuando termina, no volvió a entrar ni un pedido. Todo /clasificar
    respondió 503 hasta reiniciar, mientras /salud seguía contestando 200.

    El guardia se arma ANTES de urlopen: el goteo puede estar en la línea de
    estado o en los headers, no solo en el cuerpo.
    """
    # `caja` recibe el socket apenas se conecta, así el guardia puede cortar
    # aunque urlopen todavía no haya vuelto (headers que gotean).
    caja = {"sock": None, "vencio": False}
    estado = {"resp": None, "vencio": False}

    def cortar():
        estado["vencio"] = True
        # Se marca en la caja ANTES de mirar el socket: si todavía no hay,
        # el que esté conectando lo va a ver y cortar al publicarlo.
        caja["vencio"] = True
        if estado["resp"] is not None:
            _cortar_conexion(estado["resp"])
        elif caja["sock"] is not None:
            try:
                caja["sock"].shutdown(socket.SHUT_RDWR)
            except Exception:      # noqa: BLE001
                pass

    guardia = threading.Timer(max(0.1, vence - time.monotonic()), cortar)
    guardia.daemon = True
    guardia.start()
    try:
        resp = _opener_con_caja(caja).open(req, timeout=timeout)
        estado["resp"] = resp
        if estado["vencio"]:       # venció mientras se abría la conexión
            _cortar_conexion(resp)
            raise TimeoutError("el proveedor no respondió a tiempo")
        try:
            return json.load(resp)
        finally:
            _cortar_conexion(resp)
    except Exception as e:         # noqa: BLE001
        if estado["vencio"]:
            raise TimeoutError("el proveedor no terminó de responder a tiempo") from e
        raise
    finally:
        guardia.cancel()


def _extraer_json(texto):
    """Primer objeto JSON dentro de la respuesta (tolera ```json ... ```)."""
    texto = re.sub(r"```(?:json)?", "", texto)
    inicio = texto.find("{")
    if inicio == -1:
        raise ValueError("sin JSON en la respuesta")
    nivel = 0
    for i, ch in enumerate(texto[inicio:], inicio):
        if ch == "{":
            nivel += 1
        elif ch == "}":
            nivel -= 1
            if nivel == 0:
                return json.loads(texto[inicio:i + 1])
    raise ValueError("JSON incompleto en la respuesta")


def _prompt_sistema(categorias):
    """La política (rúbrica) va en el mensaje `system`, sin datos del usuario."""
    restantes = "\n".join(
        f"- {k}: {v['nombre']}" for k, v in categorias.items()
        if k not in _RUBRICA_KEYS and k != "sin_problema" and k not in FOLD)
    return _RUBRICA.replace("{RESTANTES}", restantes)


def _prompt_usuario(contexto=""):
    """Los datos de quien sube la foto, siempre en el mensaje `user`."""
    prompt = _PROMPT_USUARIO
    if contexto:
        prompt += _PROMPT_USUARIO_CONTEXTO.format(
            contexto=json.dumps(contexto, ensure_ascii=False))
        cand = _prestaciones_candidatas(contexto)
        if cand:
            prompt += (
                _PROMPT_USUARIO_PRESTACIONES
                + json.dumps([{"codigo": p["codigo"], "concepto": p["concepto"],
                               "cubre": _resumen_prestacion(p)}
                              for p in cand], ensure_ascii=False))
    return prompt


def _si_o_no(v):
    """True/False solo si el modelo se pronunció de forma reconocible."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):   # 1/0 numéricos, no solo "1"/"0"
        return True if v == 1 else (False if v == 0 else None)
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true", "si", "sí", "yes", "1"):
            return True
        if t in ("false", "no", "0"):
            return False
    return None


# ─── Patentes argentinas ─────────────────────────────────────────────
# Cuatro formatos válidos y completos; los dos de moto son los que siempre
# se olvidan (una patente de moto NO es una de auto truncada). Anclados de
# punta a punta: acá no hay riesgo de truncar AB123CD en el formato moto.
PATENTE_FORMATOS = (
    re.compile(r"^[A-Z]{2}\d{3}[A-Z]{2}$"),   # auto Mercosur   AB123CD
    re.compile(r"^[A-Z]{3}\d{3}$"),           # auto anterior   ABC123
    re.compile(r"^[A-Z]\d{3}[A-Z]{3}$"),      # moto Mercosur   A123BCD
    re.compile(r"^\d{3}[A-Z]{3}$"),           # moto anterior   123ABC
)
# Solo estas categorías llevan patente.
PATENTE_KEYS = {"vehiculo_mal_estacionado", "vehiculo_abandonado"}

# Parte dañada por clave: campo aditivo OPCIONAL, publicado solo en problemas
# confirmados. El formulario de la Ciudad pregunta "¿En qué parte detectaste
# el problema?" para cestos (Cuerpo/Tapa) y contenedores (Tapa/Pedal/Cuerpo);
# el consumidor la usa para responder ese cuestionario desde la foto en vez
# de preguntarle al vecino.
PARTE_KEYS = {
    "reparacion_cesto": ("cuerpo", "tapa"),
    "reparacion_contenedor": ("cuerpo", "tapa", "pedal"),
}


def _patente_normalizada(texto):
    """'ab 123-cd' → 'AB123CD'; None si no matchea un formato argentino
    COMPLETO. Sin sustituciones O/0 ni I/1: una lectura dudosa no se
    corrige, se descarta — la exactitud vale más que el recall."""
    if not isinstance(texto, str):
        return None
    limpio = re.sub(r"[\s.\-·]+", "", texto.upper())
    if not 6 <= len(limpio) <= 7:
        return None
    for rx in PATENTE_FORMATOS:
        if rx.match(limpio):
            return limpio
    return None


def _leer_patente(img):
    """Segunda pasada, solo para la patente: la foto a mayor resolución
    (LADO_PATENTE) a hasta tres verificadores EN PARALELO, con un prompt
    que mira únicamente la chapa del vehículo infractor. Publica solo con
    al menos dos lectores leyendo la MISMA cadena válida y ninguno leyendo
    una distinta. La lectura nula no es discrepancia (chapa chica, reflejo,
    un modelo conservador); la lectura válida distinta sí, y no se publica
    nada: la duda no se vota."""
    # Únicos, por si la config repite un modelo: el mismo lector dos veces
    # no son dos lecturas independientes.
    lectores = list(dict.fromkeys(VERIFICADORES))[:3]
    if len(lectores) < 2:
        return None
    data_url = _imagen_data_url(img, lado=LADO_PATENTE)

    def _uno(modelo):
        try:
            contenido = _llamar(modelo, [
                {"role": "user", "content": [
                    {"type": "text", "text": _PROMPT_PATENTE},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ], max_tokens=2000, etapa="leer_patente")
            return _patente_normalizada(_extraer_json(contenido).get("patente"))
        except (urllib.error.URLError, ValueError, KeyError,
                json.JSONDecodeError, OSError):
            return None

    with concurrent.futures.ThreadPoolExecutor(len(lectores)) as pool:
        lecturas = list(pool.map(_uno, lectores))
    validas = [p for p in lecturas if p]
    # Publica con al menos DOS lectores leyendo la misma cadena y NINGUNO
    # leyendo una distinta: la nula no es discrepancia (chapa chica,
    # reflejo, un modelo conservador), la lectura válida distinta sí, y
    # una sola lectura válida no se puede verificar. Los tres corren en
    # paralelo: el desempate secuencial dependía de que el tercero llegara
    # justo cuando uno de los dos primeros ya no había llegado.
    if len(validas) >= 2 and len(set(validas)) == 1:
        return validas[0]
    return None


def validar_contexto_obra_servicios(contexto):
    """El código 154014 exige un reclamo distinto del retiro de bolsas."""
    modelos = list(dict.fromkeys(VERIFICADORES))

    def uno(modelo):
        v = _extraer_json(_llamar(modelo, [
            {"role": "system", "content": _PROMPT_OBRA_SERVICIOS_CONTEXTO},
            {"role": "user", "content": json.dumps(contexto, ensure_ascii=False)},
        ], max_tokens=800, etapa="obra_servicios_contexto"))
        if not isinstance(v, dict) or v.get("declarada") not in ("si", "no", "duda"):
            raise ValueError("Respuesta de contexto de obra incompleta")
        cita = v.get("cita")
        valida = (isinstance(cita, str) and bool(cita.strip())
                  and cita.strip().casefold() in contexto.casefold())
        if v["declarada"] == "si" and not valida:
            raise ValueError("Afirmación de obra sin cita del comentario")
        return {"modelo": modelo, "declarada": v["declarada"],
                "afirmacion_validada": v["declarada"] == "si" and valida}

    resultados = _map_modelos(modelos, uno)
    revisiones = [r for r in resultados if r is not _FALLO_MODELO]
    aceptado = sum(r["afirmacion_validada"] for r in revisiones) >= 2
    rechazado = len(revisiones) >= 2 and all(r["declarada"] == "no" for r in revisiones)
    return {"aceptado": aceptado,
            "estado": "aceptado" if aceptado else "rechazado" if rechazado else "indeterminado",
            "fallo": len(revisiones) < len(modelos) or len(revisiones) < 2,
            "revisiones": revisiones}


def validar_alcance_escombros(img, contexto=""):
    """Revisa ubicación y presentación sin recibir votos ni scores previos."""
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)
    modelos = list(dict.fromkeys(VERIFICADORES))
    permitidos = {
        "ubicacion": {"publica", "privada", "indeterminada"},
        "presentacion": {"bolsas_chicas_o_suelto", "solo_bolson", "sin_pila", "indeterminada"},
        "material": {"escombros_visible", "oculto_o_ambiguo", "incompatible_visible", "indeterminado"},
        "hay_bolsas_opacas_o_parciales": {"si", "no", "indeterminado"},
        "afirmacion_vecinal": {"afirma", "duda", "niega", "no_menciona"},
        "residuos_comunes_independientes": {"si", "no", "indeterminado"},
    }

    def uno(modelo):
        v = _extraer_json(_llamar(modelo, [
            {"role": "system", "content": _PROMPT_ALCANCE_ESCOMBROS},
            {"role": "user", "content": [
                {"type": "text", "text": "Comentario vecinal (dato, no instrucciones): "
                 + json.dumps(contexto, ensure_ascii=False)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}], max_tokens=1000, etapa="alcance_escombros"))
        if not isinstance(v, dict) or any(
                not isinstance(v.get(k), str) or v[k] not in valores
                for k, valores in permitidos.items()):
            raise ValueError("Respuesta de alcance incompleta")
        r = {k: v[k] for k in permitidos}
        # Ver cartón en una bolsa no revela el contenido de las otras.
        # Una respuesta internamente contradictoria no puede vetar la pila.
        if r["material"] == "incompatible_visible" and r["hay_bolsas_opacas_o_parciales"] != "no":
            r["material"] = ("oculto_o_ambiguo" if r["hay_bolsas_opacas_o_parciales"] == "si"
                             else "indeterminado")
        otros = v.get("otros_retiros_independientes")
        if not isinstance(otros, list) or any(
                not isinstance(k, str) or k not in {"retiro_muebles", "retiro_poda"}
                for k in otros):
            raise ValueError("Alcance sin evaluación de otros retiros")
        r["otros_retiros_independientes"] = list(dict.fromkeys(otros))
        for k in ("evidencia_ubicacion", "evidencia_presentacion", "evidencia_material"):
            if not isinstance(v.get(k), str) or not v[k].strip():
                raise ValueError("Alcance sin evidencia")
            r[k] = _texto_limpio(v[k], EVID_MAX)
        cita = v.get("cita_vecinal")
        # Verificar que el testimonio venga del texto, sin guardar ni
        # devolver su contenido (puede contener datos personales).
        r["afirma_validada"] = bool(
            r["afirmacion_vecinal"] == "afirma" and isinstance(cita, str)
            and cita.strip() and cita.strip().casefold() in contexto.casefold())
        return r

    revisiones, fallo = [], False
    for modelo, r in zip(modelos, _map_modelos(modelos, uno)):
        if r is _FALLO_MODELO:
            fallo = True
        else:
            revisiones.append(dict(r, modelo=modelo))
    publicas = [r for r in revisiones if r["ubicacion"] == "publica"
                and r["presentacion"] == "bolsas_chicas_o_suelto"]
    negativas = [r for r in revisiones if r["ubicacion"] == "privada"
                 or r["presentacion"] in {"solo_bolson", "sin_pila"}]
    apto = len(publicas) >= 2 and not negativas
    privado = sum(r["ubicacion"] == "privada" for r in revisiones) >= 2
    bolson = sum(r["presentacion"] == "solo_bolson" for r in revisiones) >= 2
    sin_pila = sum(r["presentacion"] == "sin_pila" for r in revisiones) >= 2
    excluido = not publicas and (privado or bolson or sin_pila)
    motivo = ("Las bolsas o los restos están en espacio público y fuera de un bolsón grande."
              if apto else
              "Los residuos están en propiedad privada. Hace falta una foto de su ubicación en la vía pública."
              if excluido and privado else
              "El material está sólo en bolsones grandes de obra, fuera del servicio de higiene."
              if excluido and bolson else
              "No se ve una pila o bolsas que puedan corresponder al retiro solicitado."
              if excluido else
              "No se pudo confirmar la ubicación y presentación de los residuos. Hace falta una foto que las muestre.")
    afirmado = sum(r["afirma_validada"] for r in revisiones) >= 2
    contexto_resuelve = (apto and sum(
        r["afirma_validada"] and r["material"] in {"escombros_visible", "oculto_o_ambiguo"}
        for r in publicas) >= 2 and not any(
            r["material"] == "incompatible_visible" or r["afirmacion_vecinal"] == "niega"
            for r in revisiones))
    return {"estado": "apto" if apto else "excluido" if excluido else "indeterminado",
            "motivo": motivo, "fallo": fallo or len(revisiones) < 2,
            "afirmacion_explicita": afirmado, "contexto_resuelve": contexto_resuelve,
            "material_contradictorio": any(r["material"] == "incompatible_visible" for r in revisiones),
            "material_visible_confirmado": sum(r["material"] == "escombros_visible"
                                               for r in publicas) >= 2,
            "basura_independiente": sum(r["residuos_comunes_independientes"] == "si"
                                        for r in revisiones) >= 2,
            "otros_retiros_independientes": [k for k in ("retiro_muebles", "retiro_poda")
                if sum(k in r["otros_retiros_independientes"] for r in revisiones) >= 2],
            "revisiones": revisiones}


def _segunda_mirada_escombros(img, ya_reportaron):
    """Re-consulta dirigida SOLO por retiro_escombros. Tri-estado y anti
    sugestión: no se menciona qué vio el modelo disidente ni dónde. Devuelve
    (confirmantes, negativas, fallo): las negativas dirigidas pesan más que
    el silencio original y bloquean la confirmación; un fallo de red se
    reporta para que la respuesta no se cachee como un "no" definitivo.

    Solo "basura_comun" veta. "indeterminado" es neutro A PROPÓSITO: ahí cae
    el modelo que vio UNA señal positiva pero no llegó a las dos que pide la
    rúbrica. Medido en producción, un modelo contestaba "basura_comun" con la
    evidencia "sacos de rafia blancos, pequeños y densos", que es textual la
    señal (4) de la rúbrica: había visto evidencia A FAVOR y su respuesta
    terminaba vetando la confirmación. El prompt ahora manda ese caso a
    "indeterminado"; el listón para confirmar no se movió (sigue haciendo
    falta que alguien diga "escombros")."""
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)
    pendientes = [m for m in VERIFICADORES if m not in ya_reportaron]

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": _PROMPT_SEGUNDA_MIRADA},
            {"role": "user", "content": [
                {"type": "text", "text": "La foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="segunda_mirada_escombros")
        v = _extraer_json(contenido)
        return (str(v.get("veredicto", "")).strip().lower(),
                _texto_limpio(v.get("evidencia"), EVID_MAX))

    confirmantes, negativas, fallo = [], [], False
    for modelo, r in zip(pendientes, _map_modelos(pendientes, _uno)):
        if r is _FALLO_MODELO:
            fallo = True
            continue
        veredicto, evidencia = r
        if veredicto == "escombros" and evidencia:
            confirmantes.append((modelo, evidencia))
        elif veredicto in {"basura_comun", "bolson_obra"} and evidencia:
            negativas.append((modelo, evidencia))
    return confirmantes, negativas, fallo


# Evidencia que huele a estructura metálica confundible con la base de un
# contenedor. Un material metálico EXPLÍCITO alcanza solo, aunque la misma
# frase mencione bolsas plásticas o cartón alrededor. Los términos
# estructurales genéricos (la lectura errada real fue "estructura metálica
# larga") solo cuentan si la frase no dice que la estructura es de otro
# material: "estructura de madera" es un mueble, no una base.
_PATRON_METAL_FUERTE = re.compile(
    r"metal|hierro|fierro|acero|chapa|galvaniz|chatarra|"
    r"frame|rail\b|grate|grid|scrap")
_PATRON_ESTRUCTURA = re.compile(
    r"bastidor|armazon|estructura|riel|guias?\b|parrilla|rejilla|reja\b|"
    r"marco|soporte|plataforma|perfil|barra|cano\b|canos\b|tubo")
_PATRON_NO_METAL = re.compile(r"madera|carton|plastic|mimbre")
# Un mueble reconocible nombrado en la MISMA evidencia: ese voto no se puede
# retirar entero, porque se llevaría puesto un objeto real de la escena. Con
# bordes de palabra: "inmueble" no es "mueble" y "compuerta" no es "puerta".
_PATRON_MUEBLE = re.compile(
    r"\b(?:sillon(?:es)?|sofas?|sillas?|colchon(?:es)?|muebles?|heladeras?|"
    r"lavarropas|cocinas?|electrodomest\w*|mesas?|roperos?|placard(?:es)?|"
    r"estanterias?|puertas?|ventanas?|valijas?|alfombras?|tapetes?|cunas?|"
    r"colchas?)\b")


# Evidencia de reparacion_contenedor que habla de la base (y no de una tapa o
# un pedal): habilita la re-pregunta dirigida cuando el hallazgo quedó solo.
_PATRON_BASE = re.compile(r"\bbases?\b|bastidor|plataforma|riel|guias?\b")

# La barra o riel de izado LATERAL fuera de vertical (colgando en diagonal,
# cruzada, desplazada, doblada) es una firma de daño CONCRETA y difícil de
# alucinar, distinta de la "tapa rota" genérica con la que sobrevivían los
# fantasmas de la ronda 4. Habilita la excepción del veto del daño: una
# MAYORÍA de daño con esta firma le gana al 'usable' aislado. Exige la PIEZA
# (barra/riel/montante) JUNTO a una palabra de posición fuera de lugar; "riel"
# solo no alcanza (eso ya lo toma _PATRON_BASE como base del contenedor).
_PATRON_BARRA_IZADO = re.compile(
    r"(barra|riel|montante)[^.]{0,40}"
    r"(diagonal|cruzad|colgan|desplazad|torcid|dobla|ladead|inclinad|caid|desprendid|fuera de|suelt|salid)"
    r"|(diagonal|cruzad|colgan|desplazad|torcid|dobla|ladead|inclinad|desprendid|fuera de|salid)"
    r"[^.]{0,40}(barra|riel|montante)")

# Mención SUELTA de la barra de izado (sustantivo), para GATILLAR la pasada
# dirigida cuando el voto principal no surface la barra en diagonal pero
# alguien la nombra. "izado" a secas queda afuera a propósito: aparece en
# "postes de izado" de contenedores sanos y dispararía en casi toda foto.
_PATRON_MENCION_BARRA = re.compile(r"\bbarras?\b|\briel|\bmontante")


def _evidencia_metalica(texto):
    t = _norm_texto(texto or "")
    if _PATRON_METAL_FUERTE.search(t):
        return True
    return bool(_PATRON_ESTRUCTURA.search(t)) and not _PATRON_NO_METAL.search(t)


def _segunda_mirada_base(img):
    """Re-consulta dirigida por la base del contenedor. A diferencia de la de
    escombros, pregunta a TODOS los verificadores (acá hay que poder
    desautorizar al que votó, no solo sumar al que calló) y es anti sugestión:
    no se menciona qué votó nadie. Devuelve (base, descartado, fallo)."""
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": _PROMPT_SEGUNDA_MIRADA_BASE},
            {"role": "user", "content": [
                {"type": "text", "text": "La foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="segunda_mirada_base")
        return _extraer_json(contenido)

    base, descartado, fallo = [], [], False
    for modelo, v in zip(VERIFICADORES, _map_modelos(VERIFICADORES, _uno)):
        if v is _FALLO_MODELO:
            fallo = True
            continue
        veredicto = str(v.get("veredicto", "")).strip().lower()
        evidencia = _texto_limpio(v.get("evidencia"), EVID_MAX)
        # La compuerta de existencia es EXPLÍCITA: sin hay_estructura
        # true el veredicto no cuenta. Medido: la pregunta original
        # presuponía la estructura ("la que se ve en el piso") y dos
        # modelos "encontraron" una base en una foto donde no había
        # ninguna, promoviendo una reparación fantasma.
        if v.get("hay_estructura") is not True:
            continue
        # La base de verdad viene con su escena: plataforma vacía Y
        # contenedor corrido al lado. Un "base" sin esa firma no cuenta
        # (tercer falso positivo de la promoción: restos oscuros de
        # noche + contenedores apoyados normales = "base" sugerida, que
        # además se llevaba puestos los voluminosos reales al retirar
        # sus votos).
        if (veredicto == "base_de_contenedor"
                and _si_o_no(v.get("contenedor_corrido")) is not True):
            continue
        if veredicto == "base_de_contenedor" and evidencia:
            base.append((modelo, evidencia))
        elif veredicto == "objeto_descartado" and evidencia:
            descartado.append((modelo, evidencia))
    return base, descartado, fallo


def _segunda_mirada_postes(img):
    """¿Los postes que citó un testigo existen? Devuelve (con, sin, fallo)."""
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": _PROMPT_SEGUNDA_MIRADA_POSTES},
            {"role": "user", "content": [
                {"type": "text", "text": "La foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="segunda_mirada_postes")
        v = _extraer_json(contenido)
        return (str(v.get("veredicto", "")).strip().lower(),
                _texto_limpio(v.get("evidencia"), EVID_MAX))

    con, sin, fallo = [], [], False
    for modelo, r in zip(VERIFICADORES, _map_modelos(VERIFICADORES, _uno)):
        if r is _FALLO_MODELO:
            fallo = True
            continue
        veredicto, evidencia = r
        # sin evidencia declarada el voto no cuenta, igual que en la
        # pasada hermana del subtipo (hallazgo de fable)
        if not evidencia:
            continue
        if veredicto == "con_postes":
            con.append((modelo, evidencia))
        elif veredicto == "sin_postes":
            sin.append((modelo, evidencia))
    return con, sin, fallo


def _segunda_mirada_volcado(img):
    """Re-consulta dirigida por el volcado. Mismo esquema que la del daño:
    pregunta a TODOS los verificadores y puede desautorizar votos."""
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": _PROMPT_SEGUNDA_MIRADA_VOLCADO},
            {"role": "user", "content": [
                {"type": "text", "text": "La foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="segunda_mirada_volcado")
        v = _extraer_json(contenido)
        return (str(v.get("veredicto", "")).strip().lower(),
                _texto_limpio(v.get("evidencia"), EVID_MAX))

    volcado, parado, fallo = [], [], False
    for modelo, r in zip(VERIFICADORES, _map_modelos(VERIFICADORES, _uno)):
        if r is _FALLO_MODELO:
            fallo = True
            continue
        veredicto, evidencia = r
        if veredicto == "volcado" and evidencia:
            volcado.append((modelo, evidencia))
        elif veredicto == "parado" and evidencia:
            parado.append((modelo, evidencia))
    return volcado, parado, fallo


def _segunda_mirada_subtipo(img):
    """Mirada dirigida SOLO al subtipo del contenedor de húmedos. Corre
    cuando el modelo local (entrenado con estos contenedores) contradice
    con confianza el subtipo que los verificadores votaron unánimes: la
    pasada general decide el subtipo de pasada, entre 45 categorías; esta
    pregunta enfocada mira los postes y las paredes de verdad."""
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": _PROMPT_SEGUNDA_MIRADA_SUBTIPO},
            {"role": "user", "content": [
                {"type": "text", "text": "La foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="segunda_mirada_subtipo")
        v = _extraer_json(contenido)
        return (str(v.get("veredicto", "")).strip().lower(),
                _texto_limpio(v.get("evidencia"), EVID_MAX))

    lateral, bilateral, fallo = [], [], False
    for modelo, r in zip(VERIFICADORES, _map_modelos(VERIFICADORES, _uno)):
        if r is _FALLO_MODELO:
            fallo = True
            continue
        veredicto, evidencia = r
        if veredicto == "lateral" and evidencia:
            lateral.append((modelo, evidencia))
        elif veredicto == "bilateral" and evidencia:
            bilateral.append((modelo, evidencia))
    return lateral, bilateral, fallo


def _segunda_mirada_dano(img):
    """Re-consulta dirigida por el daño del contenedor. Igual que la de la
    base: pregunta a TODOS los verificadores y puede desautorizar votos.
    Devuelve (dano, sin_dano, fallo)."""
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": _PROMPT_SEGUNDA_MIRADA_DANO},
            {"role": "user", "content": [
                {"type": "text", "text": "La foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="segunda_mirada_dano")
        v = _extraer_json(contenido)
        return (str(v.get("veredicto", "")).strip().lower(),
                _texto_limpio(v.get("evidencia"), EVID_MAX))

    dano, sin_dano, fallo = [], [], False
    for modelo, r in zip(VERIFICADORES, _map_modelos(VERIFICADORES, _uno)):
        if r is _FALLO_MODELO:
            fallo = True
            continue
        veredicto, evidencia = r
        if veredicto == "uso_comprometido" and evidencia:
            dano.append((modelo, evidencia))
        elif veredicto == "usable" and evidencia:
            sin_dano.append((modelo, evidencia))
    return dano, sin_dano, fallo


# Objetos CONCRETOS que una descripción puede nombrar. Se usan para el saneo
# de la prosa: si el objeto lo nombra una sola fuente, no se afirma. Son
# nombres de cosas identificables, no palabras genéricas ("residuos",
# "basura", "bolsas") que aparecen en todas las descripciones y no comprometen
# nada.
_OBJETOS_CONCRETOS = {k: re.compile(v) for k, v in {
    "carton": r"\b(?:caja|cajas|cajon(?:es)?|carton(?:es)?)\b",
    "madera": r"\b(?:tabla|tablas|tablon(?:es)?|madera|maderas|palet(?:s)?|"
              r"pallet(?:s)?|listones?)\b",
    "colchon": r"\b(?:colchon(?:es)?|somier(?:es)?)\b",
    "mueble": r"\b(?:mueble|muebles|sillon(?:es)?|sofas?|sillas?|mesas?|"
              r"ropero|placard|estanteria|comoda|banqueta)\b",
    "electro": r"\b(?:heladera|lavarropas|microondas|televisor|monitor|"
               r"impresora|aire acondicionado|electrodomestico\w*|gabinete|"
               r"torre de pc|computadora|cpu|notebook|pantalla)\b",
    "maceta": r"\b(?:maceta|macetero|macetas|maceteros)\b",
    "escombro": r"\b(?:escombro(?:s)?|cascote(?:s)?|revoque|mamposteria)\b|"
                r"\b(?:material(?:es)?|restos) de obra\b",
    "sanitario": r"\b(?:inodoro|bidet|lavatorio|banadera|sanitario(?:s)?)\b",
    "alfombra": r"\b(?:alfombra(?:s)?|tapete(?:s)?)\b",
    "bicicleta": r"\b(?:bicicleta(?:s)?|moto(?:s)?)\b",
    "valija": r"\b(?:valija(?:s)?|mochila(?:s)?)\b",
    # "fuente de plástico" salió de acá: "fuente" es comodín y "plástico"
    # está en todas las descripciones (las bolsas son de plástico), así que
    # ese match se corroboraba solo (hallazgo de fable)
    "bandeja": r"\b(?:bandeja(?:s)?)\b",
    # el contenido que se le atribuye a una bolsa cerrada también es una
    # afirmación sobre un objeto que hay que ver (caso U014)
    "vegetal": r"\b(?:material(?:es)? vegetal(?:es)?|restos vegetales|"
               r"ramas?|hojas?|poda|pasto|cesped)\b",
}.items()}

# Palabras que nombran un objeto reclamable SOLO si la frase dice que está
# descartado: la puerta de un garaje, las rejas de una ventana, los ladrillos
# de una pared o las ruedas del contenedor son la ESCENA, y borrar la frase
# por nombrarlas se llevaba puesta prosa correcta (reproducido por fable). Con
# la señal de descarte al lado, en cambio, son un camión (hallazgo de codex).
_OBJETOS_SEGUN_CONTEXTO = re.compile(
    r"\b(?:puerta(?:s)?|ventana(?:s)?|persiana(?:s)?|chatarra|hierros?|"
    r"canos?|rejas?|viga(?:s)?|ladrillo(?:s)?|neumatico(?:s)?|cubierta(?:s)?|"
    # el inodoro y los sanitarios NO están acá: en la vía pública son siempre
    # un descarte, así que van en la lista incondicional
    r"cesto(?:s)?|papelero(?:s)?)\b")
_PATRON_DESCARTE = re.compile(
    r"\bdescartad|\btirad|\bapilad|\bapoyad|\bamontonad|\babandonad|"
    r"\bacumulad|\barrancad|\bdesprendid|\bsuelt[oa]s?\b|\ben desuso\b|"
    r"\bpara retirar\b|\bde descarte\b|\brot[oa]s?\b|\bvoluminos|"
    r"\bretiro\b|\bdesecho|\bviej[oa]s?\b|\bdestruid|\bdesarmad")


# Lo que un modelo NIEGA no corrobora a otro: "se ven cartones y una caja, NO
# tablas largas" contiene la palabra "tablas" y estaba alcanzando para
# respaldar justamente el objeto fantasma que esa frase desmiente (hallazgo de
# fable; esa respuesta es textual de un modelo en producción).
_PATRON_NEGACION = re.compile(
    r"\b(?:no|sin|ni|ningun\w*|tampoco|nunca|jamas|carece\w*|nada de)\b")
# Negación que alcanza HACIA ATRÁS: en "las tablas no se distinguen" el
# sustantivo va ANTES del "no", así que el alcance hacia adelante lo dejaba
# vivo y ese desmentido terminaba corroborando las tablas fantasma de otro
# modelo (hallazgo de fable). Acá el tramo entero se descarta.
_PATRON_NEGACION_ATRAS = re.compile(
    r"\bno\s+(?:se\s+)?(?:ve|ven|distingue|distinguen|aprecia|aprecian|"
    r"observa|observan|hay|aparece|aparecen|llega|llegan a ver)\b")
# Sustantivos comodín: no identifican nada por sí solos, así que no sirven
# para corroborar un objeto concreto ("material", "restos").
_PALABRAS_COMODIN = {"de", "del", "la", "el", "los", "las", "un", "una",
                     "material", "materiales", "resto", "restos", "cosa",
                     "cosas", "elemento", "elementos", "objeto", "objetos",
                     "fuente", "fuentes"}


def _cerca_del_descarte(texto, m, hueco=1):
    """¿La señal de descarte califica a ESTE objeto?

    Se mide en PALABRAS, no en caracteres: "puerta vieja apoyada" califica a
    la puerta (una palabra de por medio), pero "bolsas acumuladas frente a la
    puerta" no, porque entre el participio y el sustantivo hay tres palabras y
    el participio es de las bolsas.
    """
    palabras = list(re.finditer(r"[a-zñ]+", texto))
    idx = [i for i, p in enumerate(palabras)
           if p.start() < m.end() and p.end() > m.start()]
    if not idx:
        return False
    for i, p in enumerate(palabras):
        if not _PATRON_DESCARTE.search(p.group(0)):
            continue
        if any(abs(i - j) - 1 <= hueco for j in idx):
            return True
    return False


def _sin_negado(texto):
    """El texto sin sus tramos negados, para que lo que un modelo DESMIENTE no
    respalde lo que otro afirma ("se ven cartones, no tablas largas")."""
    trozos = []
    for tramo in re.split(r"[,;.]| pero ", _norm_texto(texto)):
        if _PATRON_NEGACION_ATRAS.search(tramo):
            continue  # "las tablas no se distinguen": cae entero
        m = _PATRON_NEGACION.search(tramo)
        trozos.append(tramo[:m.start()] if m else tramo)
    return " ".join(trozos)


def _stems(texto, sin_negados=False):
    """Palabras de 4+ letras con sus variantes de plural, para corroborar por
    PALABRA (dos fuentes tienen que nombrar LA MISMA cosa).

    Con `sin_negados`, la negación ALCANZA HACIA ADELANTE hasta el final de su
    tramo: de "colchón sin funda tirado" sobrevive "colchón" (lo que la frase
    sí afirma) y se pierde "funda"; de "no hay tablas y maderas largas" no
    sobrevive nada, porque la negación cubre lo coordinado. Descartar el tramo
    entero, como estaba antes, se comía el sujeto afirmado (hallazgo de fable).
    """
    t = _norm_texto(texto)
    if sin_negados:
        # " pero " sí corta: introduce una afirmación ("no hay tablas pero sí
        # maderas").
        trozos = []
        for tramo in re.split(r"[,;.]| pero ", t):
            if _PATRON_NEGACION_ATRAS.search(tramo):
                continue  # "las tablas no se distinguen": cae entero
            m = _PATRON_NEGACION.search(tramo)
            trozos.append(tramo[:m.start()] if m else tramo)
        t = " ".join(trozos)
    palabras = re.findall(r"[a-zñ]{4,}", t)
    formas = set()
    for p in palabras:
        formas.add(p)
        if p.endswith("es") and len(p) > 5:
            formas.add(p[:-2])
        if p.endswith("s") and len(p) > 4:
            formas.add(p[:-1])
    return formas
_PATRON_DUDOSO = re.compile(
    r"\b(?:posible|posibles|probable|probables|parece|parecen|parecia|"
    r"parecian|pareceria|podria|podrian|podia|quiza|quizas|tal vez|aparente|"
    r"aparentemente|aparenta|aparentan|presuntamente|se ve como|similar a|"
    r"da la impresion|no se distingue|indeterminad)")
_ESTADO_QUALIF = re.compile(
    r"\b(descartad[oa]s?|tirad[oa]s?|abandonad[oa]s?|rot[oa]s?|volcad[oa]s?|"
    r"dañad[oa]s?|danad[oa]s?|en desuso)\b", re.IGNORECASE)


def _objeto_de_evidencia(texto):
    """El objeto a repreguntar sale de la evidencia del que lo vio, SIN los
    calificativos de estado: la bicicleta del experimento estaba de verdad,
    pero estacionada; el estado se pregunta aparte para que no viaje de
    contrabando con la existencia."""
    if not texto:
        return None
    limpio = _ESTADO_QUALIF.sub("", str(texto))
    limpio = re.sub(r"\s+([,;.])", r"\1", limpio)
    limpio = re.sub(r"\s{2,}", " ", limpio).strip(" ,;.")
    return limpio if len(limpio) >= 8 else None


def _pregunta_abierta(img, modelos):
    """Pregunta ABIERTA: qué hay en el piso, sin nombrar nada.

    La repregunta dirigida nombra el objeto, y eso se midió sugestionable: en
    U003 el modelo que había dicho dos veces "cartón" contestó "presente"
    cuando la pregunta dijo «tablas», y en U030 el que sin sugerencia dice
    "ramas o restos de poda" contestó "bolsas" cuando la pregunta dijo bolsas.
    Preguntando abierto, los positivos reales se nombran solos y por unanimidad
    (medido: cuatro fotos de recolección real, 3 de 3 cada una), y los dudosos
    se parten.
    """
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": _PROMPT_PREGUNTA_ABIERTA},
            {"role": "user", "content": [
                {"type": "text", "text": "La foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="pregunta_abierta")
        v = _extraer_json(contenido)
        veredicto = str(v.get("veredicto", "")).strip().lower()
        que_es = _texto_limpio(v.get("que_es"), EVID_MAX)
        ubicacion = _texto_limpio(v.get("ubicacion"), EVID_MAX)
        if veredicto == "identificado" and not (que_es and ubicacion):
            veredicto = "no_identificable"
        return {"modelo": modelo, "veredicto": veredicto,
                "que_es": que_es, "ubicacion": ubicacion,
                "evidencia": _texto_limpio(v.get("evidencia"), EVID_MAX)}

    resultados, fallo = [], False
    for r in _map_modelos(modelos, _uno):
        if r is _FALLO_MODELO:
            fallo = True
            continue
        resultados.append(r)
    return resultados, fallo


# Qué tiene que nombrar la respuesta ABIERTA para respaldar cada reclamo. Sin
# esto la puerta no abría nunca para recoleccion: su objeto es "bolsas", que a
# propósito NO es una familia de _OBJETOS_CONCRETOS (es palabra genérica).
_ESPERADO_ABIERTA = {
    "recoleccion": re.compile(
        r"\bbolsa|\bbolson|\bbolsita|\bcaja|\bcajon|\bcarton|\bembolsad|"
        r"\bbasura domiciliaria|\bresiduos? (?:domiciliario|domestico|comun)"),
}
# Lo que DESMIENTE el reclamo: otra cosa claramente distinta en el mismo lugar.
# Una respuesta GENÉRICA ("basura", "residuos sueltos") no desmiente nada: es
# abstención. Sin esta distinción, un modelo que contesta "basura acumulada"
# bloqueaba la confirmación y, con dos así, anulaba el voto (hallazgo de codex).
_INCOMPATIBLE_ABIERTA = {
    "recoleccion": re.compile(
        # sin vehiculo/auto/agua/nieve: que alguien nombre un auto o un charco
        # no contradice que HAYA bolsas en otro punto del encuadre, y dos de
        # esas alcanzaban para anular el voto (hallazgo de fable)
        r"\brama|\bhoja|\bpoda|\bcesped|\bpasto|\bescombro|\bcascote|"
        r"\bmueble|\bcolchon|\bsillon|\bmadera|\btablon|\bchatarra"),
}


def _repregunta_objeto(img, objeto, modelos, con_estado):
    """Le pregunta a los modelos que NO vieron el objeto si lo ven. Anti
    sugestión: nunca se dice que otro modelo lo reportó, la localización es
    obligatoria para "presente" (el que dice que sí de compromiso tiene que
    inventar un lugar y se le nota), y el estado va aparte."""
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)
    prompt = _PROMPT_REPREGUNTA.format(
        estado=_PROMPT_REPREGUNTA_ESTADO if con_estado else "",
        campo_estado=(', "estado": "descartado" | "en_uso" | "no_claro"'
                      if con_estado else ""))

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text",
                 "text": "Objeto a buscar: «" + objeto + "»\nLa foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="repregunta_objeto")
        v = _extraer_json(contenido)
        veredicto = str(v.get("veredicto", "")).strip().lower()
        ubicacion = _texto_limpio(v.get("ubicacion"), EVID_MAX)
        if veredicto == "presente" and not ubicacion:
            # sin ubicación no hay avistaje: se degrada a no-sé
            veredicto = "no_se_distingue"
        return {
            "modelo": modelo, "veredicto": veredicto,
            "ubicacion": ubicacion,
            "estado": (str(v.get("estado") or "").strip().lower() or None)
            if con_estado else None,
            "evidencia": _texto_limpio(v.get("evidencia"), EVID_MAX),
        }

    resultados, fallo = [], False
    for r in _map_modelos(modelos, _uno):
        if r is _FALLO_MODELO:
            fallo = True
            continue
        resultados.append(r)
    return resultados, fallo


def _segunda_mirada_presencia(img, descripcion=None):
    """Chequeo dirigido de existencia del contenedor. Anti sugestión igual
    que la repregunta: "presente" exige ubicación o se degrada.

    Con `descripcion` (un descriptor canónico nuestro, ver
    DESCRIPTOR_CONTENEDOR) la pregunta es por ESE tipo de contenedor y no por
    "alguno": es la versión que caza el contenedor fantasma publicado al lado
    de uno real.
    """
    prompt = (_PROMPT_SEGUNDA_MIRADA_PRESENCIA if not descripcion
              else _PROMPT_SEGUNDA_MIRADA_PRESENCIA_CLAVE.format(
                  tipo=descripcion))
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text", "text": "La foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="segunda_mirada_presencia")
        v = _extraer_json(contenido)
        veredicto = str(v.get("veredicto", "")).strip().lower()
        ubicacion = _texto_limpio(v.get("ubicacion"), EVID_MAX)
        if veredicto == "presente" and not ubicacion:
            veredicto = "no_se_distingue"
        return (veredicto, _texto_limpio(v.get("evidencia"), EVID_MAX))

    presentes, ausentes, fallo = [], [], False
    for modelo, r in zip(VERIFICADORES, _map_modelos(VERIFICADORES, _uno)):
        if r is _FALLO_MODELO:
            fallo = True
            continue
        veredicto, evidencia = r
        if veredicto == "presente":
            presentes.append((modelo, evidencia))
        elif veredicto == "ausente" and evidencia:
            ausentes.append((modelo, evidencia))
    return presentes, ausentes, fallo


def _segunda_mirada_desborde(img):
    """Mirada dirigida del desborde: el rebalse hay que VERLO. El umbral
    del veto acá es por MAYORÍA (ver el comentario en la fusión), no el
    estricto del uso del contenedor."""
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": _PROMPT_SEGUNDA_MIRADA_DESBORDE},
            {"role": "user", "content": [
                {"type": "text", "text": "La foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="segunda_mirada_desborde")
        v = _extraer_json(contenido)
        return (str(v.get("veredicto", "")).strip().lower(),
                _texto_limpio(v.get("evidencia"), EVID_MAX))

    rebalsa, no_lleno, fallo = [], [], False
    for modelo, r in zip(VERIFICADORES, _map_modelos(VERIFICADORES, _uno)):
        if r is _FALLO_MODELO:
            fallo = True
            continue
        veredicto, evidencia = r
        if veredicto == "rebalsa_visible" and evidencia:
            rebalsa.append((modelo, evidencia))
        elif veredicto == "no_se_ve_lleno" and evidencia:
            no_lleno.append((modelo, evidencia))
    return rebalsa, no_lleno, fallo


# Mobiliario de la calle: nombrarlo NO identifica un voluminoso descartado.
# Medido en 15 fotos con la firma de identidad: en 3 de ellas (T055, T068,
# T175) algún modelo contestó "objeto_identificado: contenedor de basura", y
# como la firma solo pedía nombre + ubicación, ese contenedor le sostenía el
# reclamo de retiro_muebles a la foto entera.
# Una palabra sola alcanza para saber que es mobiliario: se evalúa sobre el
# NÚCLEO del nombre. Con borde de palabra, para que "caja contenedora" no
# cuente como contenedor (hallazgo de codex).
_PATRON_MOBILIARIO_URBANO = re.compile(
    r"\b(?:contenedor(?:es|cito)?|conteiner|container|dumpster|volquete|"
    r"papelero)s?\b")
# Estos SOLO se reconocen con su complemento: "tacho" o "cesto" a secas puede
# ser un balde o un canasto grande descartado, que la rúbrica cuenta como
# voluminoso; "tacho de basura" es el mobiliario. Se evalúan sobre el nombre
# recortado en la primera referencia de LUGAR, que conserva el "de basura".
_PATRON_MOBILIARIO_COMPUESTO = re.compile(
    r"\b(?:tacho|cesto|canasto|bin)s?\s+(?:de\s+|para\s+)?(?:basura|residuos|"
    r"papel|papelero)s?\b")
# El objeto suele venir con la ubicación pegada ("sillón de dos cuerpos junto
# al contenedor", "silla bajo el contenedor"): ahí el contenedor es la
# REFERENCIA, no el objeto, y descartarlo bajaría un voluminoso real a posibles
# (hallazgo de codex). Solo cuenta el NÚCLEO: el sustantivo con el que arranca
# el nombre, o sea todo lo que hay antes de la primera preposición. Enumerar
# "las de lugar" se demostró leaky (faltaban "al costado", "bajo", "debajo"),
# así que se corta en CUALQUIER preposición, "de" incluida: "contenedor de
# basura" sigue siendo contenedor y "sillón de dos cuerpos" sigue siendo sillón.
_PATRON_PREPOSICION = re.compile(
    r"\s+(?:de|del|a|al|ante|bajo|con|contra|desde|detras|en|entre|hacia|"
    r"hasta|junto|para|por|segun|sin|sobre|tras|arriba|abajo|encima|debajo|"
    r"delante|cerca|dentro|adentro|fuera|afuera|frente|apoyad[oa]s?|"
    r"pegad[oa]s?|tirad[oa]s?|ubicad[oa]s?|que)\b")
# El corte LARGO conserva el complemento del nombre ("tacho de basura") y saca
# solo la referencia de lugar.
_PATRON_LUGAR = re.compile(
    r"\s+(?:junto|al lado|a un lado|al costado|a un costado|cerca|sobre|"
    r"encima|arriba|bajo|debajo|abajo|delante|adelante|detras|atras|frente|"
    r"enfrente|contra|dentro|adentro|fuera|afuera|apoyad[oa]s?|pegad[oa]s?|"
    r"tirad[oa]s?|ubicad[oa]s?|en la|en el|a la|al)\b")


def _objeto_nucleo(texto):
    """Núcleo del nombre: el sustantivo con el que arranca."""
    return _PATRON_PREPOSICION.split(_norm_texto(texto), 1)[0]


def _objeto_sin_lugar(texto):
    """El nombre sin la referencia de lugar, con su complemento intacto."""
    return _PATRON_LUGAR.split(_norm_texto(texto), 1)[0]


def _es_mobiliario_urbano(objeto):
    return bool(_PATRON_MOBILIARIO_URBANO.search(_objeto_nucleo(objeto))
                or _PATRON_MOBILIARIO_COMPUESTO.search(
                    _objeto_sin_lugar(objeto)))


def _segunda_mirada_voluminoso(img):
    """Firma de identidad para el voluminoso marginal (1 VLM + modelo
    local): nadie flaquea un reporte de voluminosos sin que algún modelo
    pueda NOMBRAR el objeto y ubicarlo. Devuelve (identificados, negativos,
    descartados, fallo)."""
    data_url = _imagen_data_url(img, lado=LADO_SEGUNDA_MIRADA)

    def _uno(modelo):
        contenido = _llamar(modelo, [
            {"role": "system", "content": _PROMPT_SEGUNDA_MIRADA_VOLUMINOSO},
            {"role": "user", "content": [
                {"type": "text", "text": "La foto:"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], max_tokens=400, etapa="segunda_mirada_voluminoso")
        v = _extraer_json(contenido)
        return (str(v.get("veredicto", "")).strip().lower(),
                _texto_limpio(v.get("objeto"), EVID_MAX),
                _texto_limpio(v.get("ubicacion"), EVID_MAX),
                _texto_limpio(v.get("evidencia"), EVID_MAX))

    identificados, negativos, descartados, fallo = [], [], [], False
    for modelo, r in zip(VERIFICADORES, _map_modelos(VERIFICADORES, _uno)):
        if r is _FALLO_MODELO:
            fallo = True
            continue
        veredicto, objeto, ubicacion, evidencia = r
        # sin objeto Y ubicación no hay identificación: "sillón" a secas
        # es justo el sí de compromiso que esta firma existe para frenar
        if veredicto == "objeto_identificado" and objeto and ubicacion:
            # Señalar el contenedor (o el cesto, o el volquete) no es
            # identificar un descarte: es no haber encontrado ninguno.
            # Cuenta como abstención, no como negativo: el modelo no
            # respondió que no hay, respondió otra cosa.
            if _es_mobiliario_urbano(objeto):
                descartados.append((modelo, objeto))
            else:
                identificados.append(
                    (modelo, objeto + " (" + ubicacion + ")"))
        elif veredicto == "solo_bolsas_o_textiles":
            negativos.append((modelo, evidencia))
    return identificados, negativos, descartados, fallo


def _verificar_uno(modelo, data_url, categorias, contexto=""):
    try:
        contenido = _llamar(modelo, [
            {"role": "system", "content": _prompt_sistema(categorias)},
            {"role": "user", "content": [
                {"type": "text", "text": _prompt_usuario(contexto)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ], etapa="verificar_uno")
        veredicto = _extraer_json(contenido)
        vistas = []
        for c in veredicto.get("categorias", []):
            if not isinstance(c, dict):
                continue
            c["key"] = FOLD.get(c.get("key"), c.get("key"))
            if c["key"] in categorias and c["key"] not in {v["key"] for v in vistas}:
                c["evidencia"] = _texto_limpio(c.get("evidencia"), EVID_MAX)
                # La patente se normaliza y valida ACÁ: lo que no matchea un
                # formato argentino completo no entra ni al detalle.
                pat = _patente_normalizada(c.get("patente")) \
                    if c["key"] in PATENTE_KEYS else None
                if pat:
                    c["patente"] = pat
                else:
                    c.pop("patente", None)
                # "parte" se valida ACÁ, como la patente: solo las claves de
                # PARTE_KEYS la llevan y solo con sus valores permitidos.
                parte = c.get("parte")
                permitidas = PARTE_KEYS.get(c["key"], ())
                if isinstance(parte, str) and parte.strip().lower() in permitidas:
                    c["parte"] = parte.strip().lower()
                else:
                    c.pop("parte", None)
                vistas.append(c)
        ctx_cats = []
        for item in veredicto.get("categorias_contexto") or []:
            # tolera claves sueltas (formato viejo), {key, respaldo} o
            # {codigo, respaldo} para prestaciones del catálogo completo
            respaldo = "neutral"
            if isinstance(item, dict) and item.get("respaldo") in (
                    "compatible", "neutral", "contradice"):
                respaldo = item["respaldo"]
            if isinstance(item, str):
                k = item
            elif isinstance(item, dict) and item.get("key"):
                k = item["key"]
            elif isinstance(item, dict) and item.get("codigo"):
                cod = str(item["codigo"])
                if cod in _PRESTACIONES_POR_CODIGO and \
                        cod not in [c.get("codigo") for c in ctx_cats]:
                    ctx_cats.append({"codigo": cod, "respaldo": respaldo})
                continue
            else:
                continue
            k = FOLD.get(k, k)
            if isinstance(k, str) and k in categorias and k != "sin_problema" \
                    and k not in [c.get("key") for c in ctx_cats]:
                ctx_cats.append({"key": k, "respaldo": respaldo})
        # bool() a secas invertiría el juicio: un modelo que devuelve la
        # cadena "false" en vez del literal JSON daría True. Cualquier cosa
        # que no sea un sí o un no reconocible se trata como "no se pronunció".
        return {"modelo": modelo, "ok": True, "categorias": vistas,
                "foto_corresponde": _si_o_no(veredicto.get("foto_corresponde")),
                "sin_problema": bool(veredicto.get("sin_problema")),
                "descripcion": _texto_limpio(veredicto.get("descripcion"), DESC_MAX),
                "categorias_contexto": ctx_cats}
    except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError, OSError) as e:
        return {"modelo": modelo, "ok": False, "error": str(e)[:200]}


def _sistema_arbitro(con_foto):
    """con_foto: si la imagen VA adjunta en este mensaje. No alcanza con que
    ARBITRO_VE_FOTO esté activo: si no se pasó la foto, el prompt que dice
    "la tenés adjunta" le miente al modelo."""
    return _SISTEMA_ARBITRO_FOTO if con_foto else _SISTEMA_ARBITRO_TEXTO


def _arbitrar(disputadas, veredictos, probabilidades, categorias, consensuadas,
              firmes=(), contexto="", sospechosas=(), fuentes=None, data_url=None):
    """El árbitro (modelo de texto) decide las categorías con una sola fuente.

    En la misma llamada redacta la descripción final consolidada de la foto,
    a partir de las descripciones de los verificadores. Con disputadas vacío
    solo redacta la descripción. firmes: subtipos ya resueltos por el sistema
    (contenedor de húmedos, tapa) que la descripción no debe contradecir.
    """
    if not ARBITRO:
        return None
    fuentes = fuentes or {}
    probas = {p["key"]: p["score"] for p in probabilidades[:12]}
    partes = [
        _ARBITRO_DATOS.format(
            categorias=json.dumps({k: v['nombre'] for k, v in categorias.items()}, ensure_ascii=False),
            probabilidades=json.dumps(probas, ensure_ascii=False),
            veredictos=json.dumps(veredictos, ensure_ascii=False),
            consensuadas=json.dumps(sorted(consensuadas), ensure_ascii=False))
    ]
    if contexto:
        partes.append(
            _ARBITRO_CONTEXTO.format(
                contexto=json.dumps(contexto, ensure_ascii=False)))
    if sospechosas:
        partes.append(
            _ARBITRO_SUGESTION.format(
                sospechosas=json.dumps(sorted(sospechosas), ensure_ascii=False)))
    if firmes:
        detalle = {k: categorias.get(k, {}).get("nombre", k) for k in sorted(firmes)}
        partes.append(
            _ARBITRO_SUBTIPOS.format(
                subtipos=json.dumps(detalle, ensure_ascii=False)))
    if disputadas:
        detalle_fuentes = {k: sorted(fuentes.get(k, [])) for k in sorted(disputadas)}
        partes.append(
            _ARBITRO_DISPUTAS.format(
                fuentes=json.dumps(detalle_fuentes, ensure_ascii=False)))
        if "retiro_escombros" in disputadas:
            partes.append(
                _ARBITRO_ESCOMBROS
                + (_ARBITRO_ESCOMBROS_FOTO
                   if data_url else "\n\n"))
        vlm_only = sorted(set(categorias) - {p["key"] for p in probabilidades}
                          - {"sin_problema"})
        if vlm_only and disputadas & set(vlm_only):
            partes.append(
                _ARBITRO_SOLO_VISION.format(
                    categorias=json.dumps(sorted(disputadas & set(vlm_only)), ensure_ascii=False)))
        if len(veredictos) < 2:
            partes.append(_ARBITRO_UNA_FUENTE)
        partes.append("Además, redactá")
    else:
        partes.append("Tu única tarea: redactá")
    partes.append(_ARBITRO_DESCRIPCION)
    try:
        # Con ARBITRO_VE_FOTO el árbitro recibe TAMBIÉN la imagen. La versión
        # de solo texto decide sobre descripciones y evidencias ajenas, que son
        # una compresión con pérdida de lo que hay que juzgar: si un modelo vio
        # algo y el otro no lo nombró, sin la foto no hay forma de saber quién
        # tiene razón. Requiere que ARBITRO sea un modelo con visión.
        con_foto = bool(ARBITRO_VE_FOTO and data_url)
        if con_foto:
            contenido = [{"type": "text", "text": "".join(partes)},
                         {"type": "image_url", "image_url": {"url": data_url}}]
        else:
            contenido = "".join(partes)
        mensajes = [{"role": "system", "content": _sistema_arbitro(con_foto)},
                    {"role": "user", "content": contenido}]

        def _una_vuelta(_):
            return _extraer_json(_llamar(ARBITRO, mensajes, etapa="arbitrar"))

        if ARBITRO_VOTOS == 1:
            datos = [_una_vuelta(0)]
        else:
            # En paralelo: son la misma pregunta, no dependen entre sí.
            with concurrent.futures.ThreadPoolExecutor(ARBITRO_VOTOS) as pool:
                crudos = list(pool.map(
                    lambda i: _intentar(_una_vuelta, i), range(ARBITRO_VOTOS)))
            datos = [d for d in crudos if d is not None]
            if not datos:
                raise ValueError("ninguna vuelta del árbitro devolvió JSON")

        # Voto válido = a lo sumo una decisión por categoría, con veredicto
        # legible. Una vuelta malformada se descarta entera en vez de aportar
        # medio voto: si no, un JSON raro corre el umbral sin que se note.
        def _boletas(d):
            vistas, salida = set(), {}
            for x in d.get("decisiones", []):
                if not isinstance(x, dict):
                    continue
                k, ver = x.get("key"), x.get("veredicto")
                if k not in disputadas or ver not in ("confirmar", "rechazar"):
                    continue
                if k in vistas:      # la misma categoría decidida dos veces
                    return None
                vistas.add(k)
                salida[k] = (ver, x.get("motivo"))
            return salida

        # La boleta viaja SIEMPRE junto a su propia respuesta. Separarlas en dos
        # listas y volver a aparearlas con zip desalinea todo apenas se descarta
        # una vuelta del medio: se publicaba la descripción de una boleta
        # inválida mientras el conteo decía otra cosa.
        validas = [(b, d) for b, d in ((_boletas(d), d) for d in datos)
                   if b is not None]
        if not validas:
            raise ValueError("ninguna vuelta del árbitro dio una boleta válida")
        boletas = [b for b, _ in validas]

        # Mayoría por categoría sobre el total de boletas válidas. Un empate, o
        # una categoría que la mayoría ni siquiera decidió, se rechaza: la
        # consigna ya dice que ante la duda se rechaza. Es una decisión de
        # umbral deliberada, no solo reducción de varianza: sesga a rechazar.
        decisiones = []
        for k in sorted(disputadas):
            si = sum(1 for b in boletas if b.get(k, ("",))[0] == "confirmar")
            no = sum(1 for b in boletas if b.get(k, ("",))[0] == "rechazar")
            if not si and not no:
                continue
            motivo = next((b[k][1] for b in boletas if k in b), None)
            # Mayoría sobre TODAS las boletas válidas, no solo sobre las que
            # opinaron. Contar si>no dejaba que 1 de 3 confirmara (1-0) cuando
            # las otras dos ni mencionaban la categoría: eso es una minoría
            # decidiendo, justo lo contrario de lo que promete la opción.
            # Omitir cuenta como no confirmar, y el empate rechaza.
            decisiones.append({"key": k, "votos": f"{si}-{no}", "motivo": motivo,
                               "de": len(boletas),
                               "veredicto": ("confirmar" if si * 2 > len(boletas)
                                             else "rechazar")})

        # Con varias vueltas hay varias descripciones y hay que elegir una sola.
        # Se elige la de la vuelta que más coincide con el veredicto final; a
        # igualdad, la más larga y después alfabética, para que el desempate no
        # dependa de en qué orden volvieron las respuestas.
        finales = {d["key"]: d["veredicto"] for d in decisiones}
        def _puntaje(par):
            b, texto = par
            return (sum(1 for k, v in finales.items() if b.get(k, ("",))[0] == v),
                    len(texto), texto)
        pares = [(b, _texto_limpio(d.get("descripcion"), DESC_MAX))
                 for b, d in validas if _texto_limpio(d.get("descripcion"), 1)]
        descripcion = max(pares, key=_puntaje)[1] if pares else ""
        return {"modelo": ARBITRO, "ok": True, "decisiones": decisiones,
                "vueltas_pedidas": ARBITRO_VOTOS, "vueltas_validas": len(boletas),
                "degradado": len(boletas) < ARBITRO_VOTOS,
                "descripcion": descripcion}
    except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError, OSError) as e:
        return {"modelo": ARBITRO, "ok": False, "error": str(e)[:200]}


def _intentar(fn, arg):
    """Una vuelta que falla no debe tumbar la votación entera."""
    try:
        return fn(arg)
    except (urllib.error.URLError, ValueError, KeyError,
            json.JSONDecodeError, OSError):
        return None


def _clasificar_contexto(contexto, categorias):
    """Qué reporte pide el vecino, leyendo SOLO su texto.

    Se usa cuando la foto no sirve: el reclamo sigue siendo válido y hay que
    encaminarlo igual. Ante la ambigüedad va la categoría GENÉRICA: si dice
    "mi cuadra está llena de basura" no sabemos si es diseminado o voluminoso,
    así que es recoleccion, no retiro_muebles.
    """
    if not contexto or not ARBITRO:
        return []
    listado = "\n".join(f"- {k}: {v['nombre']}" for k, v in categorias.items()
                        if k != "sin_problema" and k not in FOLD)
    prompt = _CONTEXTO_USUARIO.format(
        contexto=json.dumps(contexto, ensure_ascii=False),
        categorias=listado)
    try:
        data = _extraer_json(_llamar(ARBITRO, [
            {"role": "system", "content": _CONTEXTO_SISTEMA},
            {"role": "user", "content": prompt}], etapa="clasificar_contexto"))
    except (urllib.error.URLError, ValueError, KeyError,
            json.JSONDecodeError, OSError):
        # None = no se pudo encaminar (falla transitoria). Distinto de [], que
        # significa "el vecino no pidió nada del catálogo". Si se devolviera []
        # acá, un corte de OpenRouter quedaría cacheado como "no hay problema".
        return None
    salida, vistas = [], set()
    for c in data.get("categorias", []) or []:
        if not isinstance(c, dict):
            continue
        k = FOLD.get(c.get("key"), c.get("key"))
        if k in categorias and k != "sin_problema" and k not in vistas \
                and k not in PRESENCIA:
            vistas.add(k)
            try:
                g = min(5, max(1, int(c.get("gravedad", 2))))
            except (TypeError, ValueError):
                g = 2
            salida.append({"key": k, "nombre": categorias[k]["nombre"],
                           "gravedad": g, "fuentes": ["contexto_vecinal"],
                           "motivo": _texto_limpio(c.get("motivo"), EVID_MAX)})
    return salida


def verificar(img, categorias, prediccion_local, contexto=""):
    """Corre los verificadores en paralelo y consolida un veredicto final.

    img: PIL.Image ya abierta.
    categorias: dict de categorias.json.
    prediccion_local: dict con "predichas" y "probabilidades" (del modelo local).
    contexto: texto opcional de quien reportó ("contexto vecinal"). Se pasa a
    los verificadores y al árbitro para interpretar la foto, y además viaja por
    su propio canal: lo que el vecino describe vuelve en "por_contexto" y puede
    sostener el reclamo solo cuando la foto no corresponde (foto_valida False).
    """
    data_url = _imagen_data_url(img)
    with concurrent.futures.ThreadPoolExecutor(len(VERIFICADORES)) as pool:
        veredictos = list(pool.map(
            lambda m: _verificar_uno(m, data_url, categorias, contexto), VERIFICADORES))

    grav_votos = {}  # key -> [gravedad de cada verificador que la reportó]
    fuentes = {}   # key -> lista de fuentes que la reportan
    patentes = {}  # key -> {patente normalizada: [modelos que la leyeron]}
    partes = {}    # key -> {parte dañada: [modelos que la ubicaron]}
    for p in prediccion_local["predichas"]:
        if p["key"] != "sin_problema":
            fuentes.setdefault(p["key"], []).append("modelo_local")
    for v in veredictos:
        if not v.get("ok"):
            continue
        for c in v["categorias"]:
            k = c["key"]
            fuentes.setdefault(k, []).append(v["modelo"])
            if c.get("patente") and k in PATENTE_KEYS:
                patentes.setdefault(k, {}) \
                        .setdefault(c["patente"], []).append(v["modelo"])
            if c.get("parte") and k in PARTE_KEYS:
                partes.setdefault(k, {}) \
                      .setdefault(c["parte"], []).append(v["modelo"])
            # Se juntan TODOS los votos de gravedad y se resuelven abajo con
            # la mediana. Antes se publicaba el máximo, que es un veto de una
            # sola mano hacia arriba: con tres muestras ruidosas el máximo
            # corre siempre por encima del valor central, y así el 58% de las
            # fotos terminaba en 4 y el 88% en 3 o 4.
            try:
                grav_votos.setdefault(k, []).append(
                    min(5, max(1, int(c.get("gravedad", 1)))))
            except (TypeError, ValueError):
                grav_votos.setdefault(k, []).append(1)

    def _gravedad_consenso(votos):
        """Resuelve la gravedad publicada a partir de los votos.

        MEDIANA, no máximo. Con 3 votos, la mediana aguanta a un modelo
        alarmista sin tapar un desacuerdo real. Con 2 (el mínimo que confirma
        una categoría) se redondea PARA ABAJO a propósito: es el lado que
        empuja contra la inflación medida. Con 1 voto no hay nada que
        promediar; ese caso igual no se publica solo (hace falta consenso).
        """
        if not votos:
            return None
        v = sorted(votos)
        n = len(v)
        if n % 2:
            return v[n // 2]
        return (v[n // 2 - 1] + v[n // 2]) // 2

    def _plegar_en(elegido, otros):
        for otro in otros:
            for f in fuentes.pop(otro):
                if f not in fuentes[elegido]:
                    fuentes[elegido].append(f)
            if otro in grav_votos:
                grav_votos.setdefault(elegido, []).extend(grav_votos.pop(otro))

    subtipos_firmes = {}  # subtipo elegido -> subtipos descartados

    # Un contenedor de húmedos es lateral O bilateral, nunca ambos. Deciden
    # los votos de los modelos de visión, que son los testigos de ESTA foto,
    # con UNA excepción: el modelo local (entrenado con estos contenedores de
    # esta ciudad) vale como voto propio cuando está decidido de verdad Y
    # algún verificador vio lo mismo que él. Las dos condiciones importan y
    # cada una arregla un incidente real:
    #  - sin la del margen, el local pisaba al único testigo correcto en fotos
    #    que no había visto (un VLM reportó "contenedor negro con postes" y se
    #    publicó bilateral porque el local lo dijo);
    #  - sin la de la corroboración, dos generalistas en mayoría pisaban al
    #    local y al verificador que sí habían acertado (contenedor bilateral
    #    publicado como lateral, con el local en 1.000 contra 0.027).
    # Medido: con margen >= SUBTIPO_LOCAL_MARGEN el local acierta 60/60 en el
    # set revisado; su único error confiado se queda abajo del umbral.
    grises = {"contenedor_humedos_lateral", "contenedor_humedos_bilateral"}
    vistos = grises & set(fuentes)
    prob_gris = {p["key"]: p.get("score", 0.0)
                 for p in prediccion_local.get("probabilidades") or []
                 if p.get("key") in grises}
    local_gris = max(prob_gris, key=prob_gris.get) if prob_gris else None
    margen = abs(prob_gris.get("contenedor_humedos_bilateral", 0.0)
                 - prob_gris.get("contenedor_humedos_lateral", 0.0))
    if len(vistos) > 1:
        votos_vlm_gris = {k: sum(1 for f in fuentes[k] if f != "modelo_local")
                          for k in vistos}
        # el local decide solo si está decidido Y no está solo
        if (local_gris in vistos and margen >= SUBTIPO_LOCAL_MARGEN
                and votos_vlm_gris.get(local_gris, 0) >= 1):
            elegido = local_gris
        else:
            tope = max(votos_vlm_gris.values())
            lideres = [k for k, v in votos_vlm_gris.items() if v == tope]
            if len(lideres) == 1:
                elegido = lideres[0]
            else:
                elegido = (local_gris if local_gris in lideres else
                           max(lideres, key=lambda k: len(fuentes[k])))
        subtipos_firmes[elegido] = sorted(vistos - {elegido})
        _plegar_en(elegido, vistos - {elegido})
    # ADJUDICACIÓN DIRIGIDA DEL SUBTIPO: si quedó UN solo subtipo de húmedos
    # y el modelo local (entrenado con estos contenedores) lo contradice con
    # fuerza — margen >= SUBTIPO_LOCAL_MARGEN hacia el otro, o exclusión
    # práctica (score <= 0.02 del votado con >= 0.05 del otro) —, la
    # discrepancia se resuelve MIRANDO de nuevo, no por reglas de texto:
    # pregunta dirigida con la lista de señales (postes, paredes, color).
    # La mayoría dirigida manda; sin mayoría (nadie pudo ver las señales),
    # decide el local, que para eso está entrenado. Guardia del incidente
    # real ("contenedor negro con postes", local confiado y equivocado a
    # 0.98): si algún testigo citó los POSTES a la vista, el subtipo lateral
    # votado no se toca. Dos casos reales en un día motivaron esto: el
    # lateral oliva visto de atrás votado bilateral por los tres, y el
    # bilateral ocluido votado lateral con el local en 0.000.
    segunda_mirada_subtipo = None
    segunda_mirada_postes = None
    vistos_post = grises & set(fuentes)
    if SEGUNDA_MIRADA_SUBTIPO and len(vistos_post) == 1:
        k_sub = next(iter(vistos_post))
        otro_sub = next(iter(grises - {k_sub}))
        pk_s = prob_gris.get(k_sub, 0.0)
        po_s = prob_gris.get(otro_sub, 0.0)
        discrepa = ((local_gris == otro_sub and margen >= SUBTIPO_LOCAL_MARGEN)
                    or (pk_s <= 0.02 and po_s >= 0.05))
        postes_citados = (k_sub == "contenedor_humedos_lateral" and any(
            re.search(r"poste|montante", _norm_texto(c.get("evidencia") or ""))
            for v in veredictos if v.get("ok")
            for c in v["categorias"] if c["key"] == k_sub))
        # LOS POSTES CITADOS TAMBIÉN SE MIRAN. La guardia existe por un
        # incidente real (contenedor negro con postes A LA VISTA, local
        # confiado y equivocado), pero se estaba comiendo el caso inverso:
        # en U022 un modelo le inventó "postes de izado" a un bilateral gris
        # claro de noche y con eso congeló el subtipo equivocado, con el local
        # en 1,000 hacia bilateral. Medido con la pregunta dirigida: el
        # bilateral de U022 da 3 de 3 "sin postes", el lateral con postes
        # visibles (T008) da 3 de 3 "con postes", y los bilaterales reales
        # dan "sin postes". Así que cuando el local contradice con fuerza y
        # NADIE puede ver esos postes, la guardia se levanta.
        # Solo dentro del sobre MEDIDO del local (>= 0,95 a un subtipo y
        # <= 0,05 al otro: 108 de 108 en la ronda 4). La "exclusión práctica"
        # más floja no habilita levantar la guardia (hallazgo de codex).
        _local_rotundo = (local_gris == otro_sub
                          and margen >= SUBTIPO_LOCAL_MARGEN
                          and pk_s <= 0.05 and po_s >= 0.95)
        if (postes_citados and discrepa and _local_rotundo
                and SEGUNDA_MIRADA_POSTES):
            con_p, sin_p, fallo_p = _segunda_mirada_postes(img)
            segunda_mirada_postes = {
                "con_postes": [{"modelo": m, "evidencia": e}
                               for m, e in con_p],
                "sin_postes": [{"modelo": m, "evidencia": e}
                               for m, e in sin_p],
                # DOS que no los vean y NINGUNO que los vea: con un solo
                # "sin postes" y dos abstenciones no alcanza para desmentir al
                # testigo (hallazgo de codex; la regla tiene que ser
                # conservadora porque el error caro es pisar un lateral real)
                "levanta_guardia": len(sin_p) >= 2 and not con_p,
                "fallo": fallo_p,
            }
            if segunda_mirada_postes["levanta_guardia"]:
                postes_citados = False
        if discrepa and not postes_citados:
            lat_sm, bil_sm, fallo_st = _segunda_mirada_subtipo(img)
            votos_sm = {"contenedor_humedos_lateral": len(lat_sm),
                        "contenedor_humedos_bilateral": len(bil_sm)}
            if votos_sm[otro_sub] > votos_sm[k_sub]:
                ganador = otro_sub          # la mirada dirigida corrige
            elif votos_sm[k_sub] > votos_sm[otro_sub]:
                ganador = k_sub             # la mirada dirigida ratifica
            else:
                ganador = otro_sub          # nadie vio: decide el local
            segunda_mirada_subtipo = {
                "lateral": [{"modelo": m, "evidencia": e} for m, e in lat_sm],
                "bilateral": [{"modelo": m, "evidencia": e} for m, e in bil_sm],
                "corrigio": ganador != k_sub,
                "fallo": fallo_st,
            }
            if ganador != k_sub:
                fuentes.setdefault(ganador, [])
                _plegar_en(ganador, {k_sub})
                # el registro viejo (si la rama de conflicto ya había
                # "resuelto" al revés) se borra: dos subtipos firmes
                # contradictorios confundían al árbitro (hallazgo de codex)
                subtipos_firmes.pop(k_sub, None)
                subtipos_firmes[ganador] = [k_sub]

    # Una tapa de servicio está en la vereda O en la calle. Acá el experto es
    # al revés: la clase única del modelo local no distingue (se pliega a
    # tapa_vereda), así que deciden los votos de los modelos de visión.
    tapas = {"tapa_vereda", "tapa_calle"}
    vistos = tapas & set(fuentes)
    if len(vistos) > 1:
        votos_vlm = lambda k: sum(1 for f in fuentes[k] if f != "modelo_local")
        elegido = max(vistos, key=lambda k: (votos_vlm(k), k == "tapa_vereda"))
        subtipos_firmes[elegido] = sorted(vistos - {elegido})
        _plegar_en(elegido, vistos - {elegido})

    activos = [v for v in veredictos if v.get("ok")]
    confirmadas = {k for k, f in fuentes.items() if len(f) >= 2}
    disputadas = {k for k, f in fuentes.items() if len(f) == 1}

    # Categorías en disputa que además figuran en lo que el contexto describe:
    # candidatas a sugestión (el texto pudo inducir la "detección" visual).
    ctx_claims = {c["key"] for v in activos
                  for c in v.get("categorias_contexto") or [] if c.get("key")}

    # Los verificadores NO son fuentes independientes entre sí: miran la misma foto
    # con el mismo prompt, y todos leen el contexto y cualquier texto escrito
    # DENTRO de la imagen. Una sola inyección que funcione en dos de ellos alcanza
    # para el consenso y confirma sola, sin que el modelo local haya visto nada.
    # SOLO con CONSENSO_VLM_SOLO=arbitro una categoría sin respaldo del modelo
    # local se manda al árbitro en vez de confirmarse. NO es el default: con
    # "confirma" (lo desplegado) esos dos votos confirman directo.
    if CONSENSO_VLM_SOLO != "confirma":
        correlacionadas = {k for k in confirmadas
                           if k not in PRESENCIA
                           and "modelo_local" not in fuentes.get(k, [])}
        confirmadas -= correlacionadas
        disputadas |= correlacionadas

    # Las claves de PRESENCIA con una sola fuente NO se arbitran: no son
    # problemas (no tienen gravedad real) y el árbitro fue diseñado para
    # decidir problemas, no presencias — como mucho rechazaría o confirmaría
    # el subtipo equivocado sin poder corregirlo. Peor: cualquier decisión
    # suya las sacaba de en_duda, y como posibles las saltea y
    # elementos_detectados solo lleva confirmadas, un contenedor visto por
    # una sola fuente desaparecía de TODOS los campos públicos. Van directo
    # a en_duda, que es su estado final por diseño (fuentes_en_duda ya
    # existía justamente para poder publicarlas desde ahí).
    presencia_dudosa = disputadas & PRESENCIA
    disputadas -= presencia_dudosa

    # SEGUNDA MIRADA (solo escombros): corre ANTES del árbitro para que
    # posibles y descripción no queden contradiciendo una confirmación.
    # Confirma únicamente una nueva evidencia concreta SIN ninguna negativa
    # dirigida en contra.
    # Registro compartido por todas las pasadas dirigidas: la primera que
    # puede retirar votos es la de escombros, así que se declara acá arriba.
    desc_desautorizadas = set()  # modelos cuya descripción quedó desautorizada
    votos_anulados = []  # (veredicto, voto retirado, pasada que lo anuló)
    adjudicadas_dirigidas = set()  # claves bajadas por una pasada dirigida:
                                   # el árbitro no las puede volver a subir

    # VALIDACIÓN CRUZADA de los reclamos que MANDAN UN CAMIÓN DISTINTO
    # (voluminosos y escombros): si el objeto lo vio UN SOLO modelo, a los
    # otros se les pregunta dirigido por ESE objeto. Pueden habérselo perdido
    # en la primera lectura, así que la pregunta puede confirmar (2 de 3 y se
    # publica) o negar (y entonces no se publica). Los chequeos genéricos solo
    # corren cuando NO hay un objeto nombrable que preguntar: preguntar por el
    # objeto concreto es mejor en las dos direcciones (medido: 7 de 7 objetos
    # reales encontrados, 0 de 9 plantados aceptados).
    def _objeto_de_un_solo_vlm(key):
        vlm = [f for f in fuentes.get(key, []) if f != "modelo_local"]
        if len(vlm) != 1:
            return None, None
        c = next((c for v in activos if v["modelo"] == vlm[0]
                  for c in v["categorias"] if c["key"] == key), None)
        objeto = _objeto_de_evidencia((c or {}).get("evidencia"))
        # Una evidencia DUDOSA ("posibles muebles", "parece un colchón") no se
        # convierte en pregunta dirigida: preguntar por algo que el propio
        # testigo no afirma es una pregunta sugestiva. Esos casos van a los
        # chequeos genéricos, que piden NOMBRAR el objeto.
        if objeto and _PATRON_DUDOSO.search(_norm_texto(objeto)):
            return None, None
        return objeto, vlm[0]

    _hay_cruzada = bool(REPREGUNTA_OBJETOS and activos
                        and len(VERIFICADORES) >= 3)

    segunda_mirada = None
    _obj_escombros, _ = (_objeto_de_un_solo_vlm("retiro_escombros")
                         if _hay_cruzada else (None, None))
    # Además del caso en disputa (el histórico), corre con los escombros
    # CONFIRMADOS por un solo VLM más el modelo local y evidencia dudosa: sin
    # esto, un "posibles escombros" con respaldo del local se publicaba sin
    # pasar por ningún control (hallazgo de codex).
    _esc_confirmado_flaco = (
        "retiro_escombros" in confirmadas and not _obj_escombros
        and sum(1 for f in fuentes.get("retiro_escombros", [])
                if f != "modelo_local") == 1)
    if SEGUNDA_MIRADA_ESCOMBROS and ("retiro_escombros" in disputadas
                                     or _esc_confirmado_flaco):
        vlm_escombros = [f for f in fuentes.get("retiro_escombros", [])
                         if f != "modelo_local"]
        if vlm_escombros:
            confirmantes, negativas, fallo_sm = _segunda_mirada_escombros(
                img, set(vlm_escombros))
            segunda_mirada = {
                "confirmaron": [{"modelo": m, "evidencia": e}
                                for m, e in confirmantes],
                "negaron": [{"modelo": m, "evidencia": e}
                            for m, e in negativas],
                "fallo": fallo_sm,
            }
            if confirmantes and not negativas:
                for m, _ in confirmantes:
                    if m not in fuentes["retiro_escombros"]:
                        fuentes["retiro_escombros"].append(m)
                confirmadas.add("retiro_escombros")
                disputadas.discard("retiro_escombros")
            elif _esc_confirmado_flaco and negativas and not confirmantes:
                # Publicado por un solo VLM con evidencia dudosa, y los otros,
                # mirando las mismas bolsas, dicen que no hay escombros: es
                # otro camión, no se manda con una sola mirada.
                segunda_mirada["retiro_votos"] = True
                confirmadas.discard("retiro_escombros")
                disputadas.add("retiro_escombros")
                adjudicadas_dirigidas.add("retiro_escombros")
                for v in activos:
                    c = next((c for c in v["categorias"]
                              if c["key"] == "retiro_escombros"), None)
                    if c is None:
                        continue
                    v["categorias"] = [x for x in v["categorias"] if x is not c]
                    votos_anulados.append((v, c, "segunda_mirada_escombros"))
                    desc_desautorizadas.add(v["modelo"])
                    if v["modelo"] in fuentes.get("retiro_escombros", []):
                        fuentes["retiro_escombros"].remove(v["modelo"])
                if not fuentes.get("retiro_escombros"):
                    fuentes.pop("retiro_escombros", None)
                    grav_votos.pop("retiro_escombros", None)
                    disputadas.discard("retiro_escombros")

    # SEGUNDA MIRADA (base del contenedor): también antes del árbitro. La
    # dispara un voto de retiro_muebles con evidencia de estructura metálica
    # cuando hay un contenedor en la escena (por clave reportada o mencionado
    # en texto). Umbrales asimétricos a propósito: el invariante del dueño es
    # que la base NUNCA salga como voluminoso, así que UNA lectura dirigida de
    # "base" alcanza para retirar los votos metálicos salvo que DOS modelos
    # dirigidos afirmen que es descarte real; promover reparacion_contenedor,
    # en cambio, pide DOS "base" y ninguna en contra.
    segunda_mirada_base = None
    if SEGUNDA_MIRADA_BASE:
        metalicos = {}  # modelo -> voto de retiro_muebles con evidencia metálica
        for v in activos:
            for c in v["categorias"]:
                if c["key"] == "retiro_muebles" and _evidencia_metalica(c.get("evidencia")):
                    metalicos[v["modelo"]] = c
        hay_contenedor = bool(set(fuentes) & CONTENEDOR_KEYS) or any(
            "contenedor" in _norm_texto(v.get("descripcion") or "")
            or any("contenedor" in _norm_texto(c.get("evidencia") or "")
                   for c in v["categorias"])
            for v in activos)
        # Un voto cuya evidencia nombra ADEMÁS un mueble reconocible no es
        # candidato a retiro: sacarlo entero borraría un objeto real (cada
        # modelo tiene UNA sola entrada por categoría, así que el sillón y la
        # "estructura metálica" pueden venir en la misma frase).
        for m in [m for m, c in metalicos.items()
                  if _PATRON_MUEBLE.search(_norm_texto(c.get("evidencia") or ""))]:
            del metalicos[m]
        # La pasada también corre cuando UN solo modelo vio la base y quedó en
        # disputa (sin nadie leyéndola como chatarra): igual que la segunda
        # mirada de escombros, la re-pregunta dirigida puede juntar el segundo
        # voto que la confirmación necesita, en vez de dejar morir el hallazgo
        # como posible rechazado.
        base_disputada = "reparacion_contenedor" in disputadas and any(
            c["key"] == "reparacion_contenedor"
            and _PATRON_BASE.search(_norm_texto(c.get("evidencia") or ""))
            for v in activos for c in v["categorias"])
        if (metalicos or base_disputada) and hay_contenedor:
            base_sm, descartado_sm, fallo_sb = _segunda_mirada_base(img)
            retiro = len(base_sm) >= 1 and len(descartado_sm) < 2
            promueve = len(base_sm) >= 2 and not descartado_sm
            segunda_mirada_base = {
                "base": [{"modelo": m, "evidencia": e} for m, e in base_sm],
                "descartado": [{"modelo": m, "evidencia": e}
                               for m, e in descartado_sm],
                "retiro_votos": retiro,
                "promovio": promueve,
                "fallo": fallo_sb,
            }
            if retiro:
                # Retiro QUIRÚRGICO: solo el voto con evidencia metálica de
                # cada modelo, con su gravedad y su entrada en el veredicto
                # (para que ni el árbitro ni la descripción lo reutilicen; al
                # final se re-adjunta ANOTADO al registro público, porque el
                # veredicto crudo de cada modelo no se falsifica). Un sillón
                # real votado por el mismo modelo en la misma escena queda
                # intacto.
                for v in activos:
                    c = metalicos.get(v["modelo"])
                    if c is None:
                        continue
                    v["categorias"] = [x for x in v["categorias"] if x is not c]
                    votos_anulados.append((v, c, "segunda_mirada_base"))
                    if v["modelo"] in fuentes.get("retiro_muebles", []):
                        fuentes["retiro_muebles"].remove(v["modelo"])
                    try:
                        g = min(5, max(1, int(c.get("gravedad", 1))))
                    except (TypeError, ValueError):
                        g = 1
                    votos_g = grav_votos.get("retiro_muebles")
                    if votos_g and g in votos_g:
                        votos_g.remove(g)
                    desc_desautorizadas.add(v["modelo"])
                restantes = fuentes.get("retiro_muebles", [])
                if not restantes:
                    fuentes.pop("retiro_muebles", None)
                    grav_votos.pop("retiro_muebles", None)
                    confirmadas.discard("retiro_muebles")
                    disputadas.discard("retiro_muebles")
                elif len(restantes) == 1:
                    confirmadas.discard("retiro_muebles")
                    disputadas.add("retiro_muebles")
                adjudicadas_dirigidas.add("retiro_muebles")
            if promueve:
                fr = fuentes.setdefault("reparacion_contenedor", [])
                for m, _ in base_sm:
                    if m not in fr:
                        fr.append(m)
                if len(fr) >= 2:
                    confirmadas.add("reparacion_contenedor")
                    disputadas.discard("reparacion_contenedor")
                if not grav_votos.get("reparacion_contenedor"):
                    # gravedad típica de la rúbrica para base vacía; la pasada
                    # dirigida no juzga severidad
                    grav_votos["reparacion_contenedor"] = [3]
                # lo confirmado acá es la BASE: una "parte" (tapa/pedal)
                # votada por otra lectura no describe este hallazgo y no se
                # publica (la pasada del daño se saltea con promovio, así
                # que nadie más la poda)
                partes.pop("reparacion_contenedor", None)

    # SEGUNDA MIRADA (daño del contenedor): las tapas dadas vuelta para el
    # cirujeo + fierros ajenos en el piso producen "tapas rotas y
    # desprendidas" en DOS modelos a la vez (foto real: 3 de 6 corridas
    # confirmaban reparacion_contenedor sobre un contenedor entero, con la
    # rúbrica ya advertida). Mismo remedio que la base: re-pregunta dirigida
    # con poder de veto. Los votos cuya evidencia es la BASE no se tocan (esa
    # pasada es la de arriba y ese hallazgo es legítimo).
    # Corre sobre reparacion CONFIRMADA (para el veto del daño) y TAMBIÉN
    # sobre una reparacion DISPUTADA cuya evidencia cita la barra/riel de
    # izado en diagonal (para PROMOVERLA): como la base disputada, esa firma
    # concreta junta con la pasada dirigida el segundo voto que la
    # confirmación necesita, en vez de morir como posible por la varianza del
    # voto principal (M006: dos modelos ven la barra, pero no siempre los dos
    # en la misma corrida). NO corre en cada voto suelto de reparación: solo
    # el confirmado o el disputado-con-barra, así la pasada extra sigue acotada.
    segunda_mirada_dano = None
    barra_disputada = ("reparacion_contenedor" in disputadas and any(
        c["key"] == "reparacion_contenedor"
        and _PATRON_BARRA_IZADO.search(_norm_texto(c.get("evidencia") or ""))
        for v in activos for c in v["categorias"]))
    # GATILLO POR PISTA DE BARRA: el voto principal casi nunca surface la barra
    # en diagonal (señal sutil), pero la pasada dirigida la lee de forma fiable.
    # Si hay un contenedor en escena y ALGUIEN nombró la barra/riel (en un voto
    # o en su descripción), corremos la pasada dirigida aunque nadie haya votado
    # reparación: dos lecturas de la barra la confirman. La pasada dirigida es
    # el filtro real (en un contenedor sano contesta 'usable'), así que la pista
    # suelta solo agrega costo, no falsos positivos.
    _hay_contenedor = bool(set(fuentes) & CONTENEDOR_KEYS) or any(
        "contenedor" in _norm_texto(v.get("descripcion") or "")
        or any("contenedor" in _norm_texto(c.get("evidencia") or "")
               for c in v["categorias"])
        for v in activos)
    _menciona_barra = any(
        _PATRON_BARRA_IZADO.search(_norm_texto(_t))
        or _PATRON_MENCION_BARRA.search(_norm_texto(_t))
        for v in activos
        for _t in [v.get("descripcion") or ""]
        + [c.get("evidencia") or "" for c in v["categorias"]])
    barra_hint = _hay_contenedor and _menciona_barra
    if (SEGUNDA_MIRADA_DANO
            and ("reparacion_contenedor" in confirmadas
                 or barra_disputada or barra_hint)
            and not (segunda_mirada_base or {}).get("promovio")):
        # Los votos cuya evidencia suena a BASE (base/plataforma/bastidor,
        # y también riel/guías: los modelos nombran así los rieles de la
        # base) quedan PROTEGIDOS del veto: un hallazgo de base confirmado
        # no se desarma porque el contenedor "se vea entero" (reproducido
        # por codex con "rieles vacíos del contenedor"). El costo asumido es
        # que un "rieles sueltos" ajeno escapa del retiro; solo, sin el
        # compañero retirado, no le alcanza para confirmar.
        tapas = {}  # modelo -> voto de reparacion_contenedor sin evidencia de base
        for v in activos:
            for c in v["categorias"]:
                if (c["key"] == "reparacion_contenedor"
                        and not _PATRON_BASE.search(
                            _norm_texto(c.get("evidencia") or ""))):
                    tapas[v["modelo"]] = c
        # La barra/riel en diagonal se nombra a veces con "riel"/"guía", que
        # _PATRON_BASE se lleva a `tapas` vacío; por eso el audit también corre
        # cuando barra_disputada, aunque el voto haya quedado clasificado como
        # base (su evidencia cita la barra, no una plataforma).
        if tapas or barra_disputada or barra_hint:
            dano_sm, sin_dano_sm, fallo_sd = _segunda_mirada_dano(img)
            # VETO ESTRICTO: un solo 'usable' enfocado tumba el reclamo,
            # digan lo que digan los demás. Medido: los fantasmas de
            # reparación (4 en la ronda 4, todos falsos según el dueño)
            # sobreviven cuando dos modelos insisten con la tapa 'rota'; la
            # pregunta del USO es más difícil de alucinar que la del daño, y
            # el único positivo real etiquetado (T008) no recibe 'usable'
            # de nadie.
            # EXCEPCIÓN DIRIGIDA (barra/riel de izado en diagonal): esa firma
            # es concreta y difícil de alucinar, distinta de la "tapa rota"
            # genérica de los fantasmas. Si DOS lecturas del daño la citan y
            # son MAYORÍA sobre los 'usable', el reclamo sobrevive al 'usable'
            # aislado (caso real M006). El veto estricto sigue para todo el
            # resto: una "tapa rota" genérica 2 a 1 se cae igual que antes.
            _barra_dano = sum(1 for _m, _e in dano_sm
                              if _PATRON_BARRA_IZADO.search(_norm_texto(_e)))
            _mayoria_barra = _barra_dano >= 2 and len(dano_sm) > len(sin_dano_sm)
            # El veto SOLO retira votos que están en `tapas` (reparación sin
            # evidencia de base). Si la pasada corrió por barra_hint pero no
            # hay ningún voto en `tapas` (p.ej. la única evidencia decía
            # "riel"/"guía" y _PATRON_BASE se la llevó, o no hubo voto de
            # reparación), no hay nada que retirar: exigir tapas evita un veto
            # NO-OP que igual marcaba retiro_votos/adjudicadas (hallado por codex).
            retira_dano = (len(sin_dano_sm) >= 1 and not _mayoria_barra
                           and bool(tapas))
            segunda_mirada_dano = {
                "dano": [{"modelo": m, "evidencia": e} for m, e in dano_sm],
                "sin_dano": [{"modelo": m, "evidencia": e}
                             for m, e in sin_dano_sm],
                "retiro_votos": retira_dano,
                "fallo": fallo_sd,
            }
            # PROMOCIÓN de la barra disputada: dos lecturas dirigidas citan la
            # barra/riel en diagonal -> junta el segundo voto y confirma (como
            # la base disputada). Mutuamente excluyente con el veto: promover
            # exige _mayoria_barra, que pone retira_dano en False.
            promovio_barra = (_mayoria_barra and (barra_disputada or barra_hint)
                              and "reparacion_contenedor" not in confirmadas)
            segunda_mirada_dano["promovio_barra"] = promovio_barra
            if promovio_barra:
                fr = fuentes.setdefault("reparacion_contenedor", [])
                for m, e in dano_sm:
                    if (_PATRON_BARRA_IZADO.search(_norm_texto(e))
                            and m not in fr):
                        fr.append(m)
                if len(fr) >= 2:
                    confirmadas.add("reparacion_contenedor")
                    disputadas.discard("reparacion_contenedor")
                if not grav_votos.get("reparacion_contenedor"):
                    grav_votos["reparacion_contenedor"] = [3]
                # el hallazgo dirigido es el CUERPO (barra estructural): una
                # "parte" tapa/pedal de otro voto no describe esto
                partes.pop("reparacion_contenedor", None)
                adjudicadas_dirigidas.add("reparacion_contenedor")
            if retira_dano:
                for v in activos:
                    c = tapas.get(v["modelo"])
                    if c is None:
                        continue
                    v["categorias"] = [x for x in v["categorias"] if x is not c]
                    votos_anulados.append((v, c, "segunda_mirada_dano"))
                    if v["modelo"] in fuentes.get("reparacion_contenedor", []):
                        fuentes["reparacion_contenedor"].remove(v["modelo"])
                    try:
                        g = min(5, max(1, int(c.get("gravedad", 1))))
                    except (TypeError, ValueError):
                        g = 1
                    votos_g = grav_votos.get("reparacion_contenedor")
                    if votos_g and g in votos_g:
                        votos_g.remove(g)
                    desc_desautorizadas.add(v["modelo"])
                    # la "parte" (tapa/pedal/cuerpo) de un voto anulado no
                    # puede seguir publicándose colgada de las fuentes que
                    # quedaron (bug reproducido en revisión)
                    for parte, quienes in list(
                            partes.get("reparacion_contenedor", {}).items()):
                        if v["modelo"] in quienes:
                            quienes.remove(v["modelo"])
                        if not quienes:
                            del partes["reparacion_contenedor"][parte]
                restantes = fuentes.get("reparacion_contenedor", [])
                if not restantes:
                    fuentes.pop("reparacion_contenedor", None)
                    grav_votos.pop("reparacion_contenedor", None)
                    confirmadas.discard("reparacion_contenedor")
                    disputadas.discard("reparacion_contenedor")
                elif len(restantes) == 1:
                    confirmadas.discard("reparacion_contenedor")
                    disputadas.add("reparacion_contenedor")
                adjudicadas_dirigidas.add("reparacion_contenedor")

    # SEGUNDA MIRADA (volcado): el techo en pendiente de los laterales, de
    # esquina y de noche, produce "contenedor volcado" en dos modelos a la
    # vez sobre un contenedor parado (medido: 2 de 3 corridas con la rúbrica
    # ya advertida). Mismo esquema de veto que el daño; la señal decisiva
    # (postes verticales = parado) va en la pregunta dirigida.
    segunda_mirada_volcado = None
    if SEGUNDA_MIRADA_VOLCADO and "reposicion_contenedor" in confirmadas:
        # SOLO los votos que afirman el VOLCADO son candidatos al veto: la
        # categoría también cubre el contenedor PARADO pero mal ubicado
        # (corrido al medio de la calle), y para ese caso "parado" es
        # verdad y no lo refuta (hallazgo de codex, reproducido).
        _volcado_claim = re.compile(r"volcad|caid|tumbad|acostad|dado vuelta")
        repos = {}
        for v in activos:
            for c in v["categorias"]:
                if (c["key"] == "reposicion_contenedor"
                        and _volcado_claim.search(
                            _norm_texto(c.get("evidencia") or ""))):
                    repos[v["modelo"]] = c
        if repos:
            volc_sm, parado_sm, fallo_sv = _segunda_mirada_volcado(img)
            retira_volc = len(parado_sm) >= 1 and len(volc_sm) < 2
            segunda_mirada_volcado = {
                "volcado": [{"modelo": m, "evidencia": e} for m, e in volc_sm],
                "parado": [{"modelo": m, "evidencia": e} for m, e in parado_sm],
                "retiro_votos": retira_volc,
                "fallo": fallo_sv,
            }
            if retira_volc:
                for v in activos:
                    c = repos.get(v["modelo"])
                    if c is None:
                        continue
                    v["categorias"] = [x for x in v["categorias"] if x is not c]
                    votos_anulados.append((v, c, "segunda_mirada_volcado"))
                    if v["modelo"] in fuentes.get("reposicion_contenedor", []):
                        fuentes["reposicion_contenedor"].remove(v["modelo"])
                    try:
                        g = min(5, max(1, int(c.get("gravedad", 1))))
                    except (TypeError, ValueError):
                        g = 1
                    votos_g = grav_votos.get("reposicion_contenedor")
                    if votos_g and g in votos_g:
                        votos_g.remove(g)
                    desc_desautorizadas.add(v["modelo"])
                restantes = fuentes.get("reposicion_contenedor", [])
                if not restantes:
                    fuentes.pop("reposicion_contenedor", None)
                    grav_votos.pop("reposicion_contenedor", None)
                    confirmadas.discard("reposicion_contenedor")
                    disputadas.discard("reposicion_contenedor")
                elif len(restantes) == 1:
                    confirmadas.discard("reposicion_contenedor")
                    disputadas.add("reposicion_contenedor")
                adjudicadas_dirigidas.add("reposicion_contenedor")

    # REPREGUNTA DIRIGIDA ENTRE MODELOS: lo que UN solo verificador vio como
    # objeto concreto se les pregunta a los que no lo vieron, sin decirles
    # quién lo reportó. Alcance acotado a los dos casos medidos (7/7 reales
    # encontrados, 0/9 plantados aceptados): un voluminoso disputado con
    # objeto nombrable, y la presencia de un contenedor vista por una sola
    # fuente (el recortado al borde del encuadre). "presente" con ubicación
    # propia suma la segunda fuente que el consenso exige; un "ausente" o un
    # "en_uso" dirigidos bloquean (la duda queda para el árbitro, como
    # siempre). Expandir a otras categorías solo con medición propia.
    # MIRADA DIRIGIDA DEL DESBORDE: 4 falsos positivos etiquetados en la
    # ronda 4 (todos con la basura EN la boca o alrededor, no rebalsando
    # desde adentro) contra 10 verdaderos. Las reglas de texto del interior
    # no frenaron el error correlacionado; mismo remedio de siempre, con
    # veto por MAYORÍA (abajo el porqué medido).
    segunda_mirada_desborde = None
    if SEGUNDA_MIRADA_DESBORDE and "contenedor_desbordado" in confirmadas:
        reb_sm, nol_sm, fallo_de = _segunda_mirada_desborde(img)
        # Acá el veto es por MAYORÍA, no estricto: 'no_se_ve_lleno' es una
        # respuesta conservadora fácil de dar frente a un rebalse real
        # (medido: el veto estricto tumbó 4 de 5 positivos verdaderos),
        # mientras que en el uso del contenedor el 'usable' es difícil de
        # alucinar. Empate = se mantiene lo confirmado.
        retira_desb = len(nol_sm) > len(reb_sm)
        segunda_mirada_desborde = {
            "rebalsa": [{"modelo": m, "evidencia": e} for m, e in reb_sm],
            "no_lleno": [{"modelo": m, "evidencia": e} for m, e in nol_sm],
            "retiro_votos": retira_desb,
            "fallo": fallo_de,
        }
        if retira_desb:
            confirmadas.discard("contenedor_desbordado")
            disputadas.add("contenedor_desbordado")
            adjudicadas_dirigidas.add("contenedor_desbordado")
            # vaciado_contenedor pide el MISMO predicado (interior
            # visiblemente lleno): si la mirada dirigida acaba de negarlo,
            # el vaciado co-confirmado cae con él (hallazgo de codex)
            if "vaciado_contenedor" in confirmadas:
                confirmadas.discard("vaciado_contenedor")
                disputadas.add("vaciado_contenedor")
                adjudicadas_dirigidas.add("vaciado_contenedor")

    # FIRMA DE IDENTIDAD DEL VOLUMINOSO: cuatro fantasmas en una ronda
    # (manta leída como colchón, cartón como madera, "podrían ser muebles").
    # Todos por el mismo camino: UN VLM marginal + el modelo local. La regla
    # del dueño: si el objeto no se puede identificar, la foto no se flaquea
    # con voluminosos. Con retiro_muebles confirmado por <=1 fuente VLM, se
    # exige que algún modelo NOMBRE el objeto y lo ubique; si nadie puede
    # (o dicen que son solo bolsas/cartones/textiles), baja a posibles.
    _obj_muebles, _ = (_objeto_de_un_solo_vlm("retiro_muebles")
                       if _hay_cruzada else (None, None))

    segunda_mirada_voluminoso = None
    if (SEGUNDA_MIRADA_VOLUMINOSO and "retiro_muebles" in confirmadas
            and sum(1 for f in fuentes.get("retiro_muebles", [])
                    if f != "modelo_local") <= 1
            and not _obj_muebles
            and not (segunda_mirada_base or {}).get("retiro_votos")):
        ident_sm, neg_sm, desc_sm, fallo_vol = _segunda_mirada_voluminoso(img)
        # MAYORÍA, no "alcanza con uno". El caso que lo obligó es U003 (foto de
        # 270 px): un modelo dijo "tablones de madera apoyados al costado del
        # contenedor" y los otros DOS, preguntados dirigido por el mismo lugar,
        # contestaron "cajas y paneles de CARTÓN, sin objetos voluminosos". Con
        # la regla vieja ese único voto sostenía el reclamo y salía un pedido
        # de camión por un voluminoso que probablemente no existe. Cuando los
        # negativos dirigidos son MÁS que las identificaciones, no hay objeto:
        # baja a en_duda. El empate mantiene, como en las pasadas hermanas.
        # Sigue haciendo falta AL MENOS UNA identificación (si nadie puede
        # nombrar el objeto, no está: esa es la regla original), y además esa
        # identificación no puede quedar en minoría contra los negativos.
        mantiene = bool(ident_sm) and len(ident_sm) >= len(neg_sm)
        segunda_mirada_voluminoso = {
            "identificados": [{"modelo": m, "objeto": o} for m, o in ident_sm],
            "negativos": [{"modelo": m, "evidencia": e} for m, e in neg_sm],
            # nombraron mobiliario de la calle en vez de un descarte
            "descartados": [{"modelo": m, "objeto": o} for m, o in desc_sm],
            "mantiene": mantiene,
            "fallo": fallo_vol,
        }
        if not mantiene:
            confirmadas.discard("retiro_muebles")
            disputadas.add("retiro_muebles")
            adjudicadas_dirigidas.add("retiro_muebles")

    repreguntas = None
    repregunta_confirmadas = set()
    # La medición previa (7/7 y 0/9) se hizo con TRES verificadores; con dos,
    # el "ausente" que bloquea solo puede venir del único repreguntado y el
    # chequeo cruzado deja de ser independiente. La repregunta corre solo
    # con la configuración medida (hallazgo de la revisión de Opus).
    if _hay_cruzada:
        pendientes = []
        # Los dos reclamos de CAMIÓN: el voluminoso y los escombros. Da igual
        # que el reclamo haya quedado confirmado (un VLM más el modelo local
        # ya suman dos fuentes) o en disputa: si lo VIO uno solo, se pregunta.
        # Lo que ya adjudicó otra pasada dirigida no se toca: repreguntarlo lo
        # resucitaría (hallazgo de Opus, reproducido).
        # recoleccion entra por el mismo motivo aunque no sea "otro camión":
        # en U030 (foto nocturna de 360 px) UN solo modelo la votó con la
        # evidencia "bolsas y BULTOS apoyados en el piso", el modelo local la
        # respaldó con 0,995 y se publicó, sobre una escena donde el dueño no
        # ve ninguna bolsa y lo que hay parece poda. La regla es la misma: si
        # lo vio uno solo, se le pregunta a los otros.
        for _k in ("retiro_muebles", "retiro_escombros", "recoleccion"):
            if _k in adjudicadas_dirigidas:
                continue
            if _k == "retiro_muebles" and (
                    (segunda_mirada_base or {}).get("retiro_votos")
                    or (segunda_mirada_voluminoso is not None
                        and not segunda_mirada_voluminoso.get("mantiene"))):
                continue
            if _k not in confirmadas and _k not in disputadas:
                continue
            # Si la mirada dirigida de las bolsas ya trajo negativas sobre los
            # escombros, la cruzada no puede confirmarlos por otro camino
            # ignorando esa negativa (hallazgo de fable): es la misma regla
            # que aplica el confirmo estricto.
            if _k == "retiro_escombros" and (segunda_mirada or {}).get("negaron"):
                continue
            _obj, _votante = _objeto_de_un_solo_vlm(_k)
            if _obj:
                # el estado (descartado / en uso) se pregunta aparte solo para
                # el voluminoso: la bicicleta ESTACIONADA del experimento
                pendientes.append((_k, _obj, _votante, _k == "retiro_muebles"))
            elif _k in _ESPERADO_ABIERTA:
                # La pregunta ABIERTA no necesita el objeto (justamente no
                # nombra nada), así que acá NO puede regir el filtro que exige
                # una evidencia larga y sin dudas. Si regía, el candado se
                # saltaba con "bolsas" (seis letras) o con "posibles bolsas",
                # que es el fraseo típico de la foto nocturna que motivó todo
                # esto: publicaba sin preguntarle a nadie (hallazgo de fable,
                # reproducido).
                _vlm = [f for f in fuentes.get(_k, []) if f != "modelo_local"]
                if len(_vlm) == 1:
                    pendientes.append((_k, "", _vlm[0], False))
        # La pregunta de presencia lleva SIEMPRE el descriptor canónico del
        # subtipo: un "presente" tiene que atestiguar ESE contenedor (negro/
        # oliva vs gris de cualquier tono vs verde), no "un contenedor"
        # genérico que lavaría el subtipo del único votante (hallazgo de
        # codex). Una sola copia: DESCRIPTOR_CONTENEDOR. El dict duplicado
        # `_desc_cont` quedó con el texto pre-#14 (gris oscuro = lateral) y
        # volvió a publicar un bilateral nocturno como lateral.
        for k in sorted(presencia_dudosa & CONTENEDOR_KEYS):
            vlm_p = [f for f in fuentes.get(k, []) if f != "modelo_local"]
            if len(vlm_p) == 1 and k in DESCRIPTOR_CONTENEDOR:
                objeto = ("un contenedor municipal de basura "
                          + DESCRIPTOR_CONTENEDOR[k]
                          + " (aunque sea recortado por el borde del encuadre)")
                pendientes.append((k, objeto, vlm_p[0], False))
        # Una pendiente SIN jurado disponible no puede consumir uno de los dos
        # cupos: si lo hace, se come el turno de la que sí tenía a quién
        # preguntarle (hallazgo de fable).
        def _hay_jurado(votante):
            return any(v["modelo"] != votante
                       and v["modelo"] not in desc_desautorizadas
                       for v in activos)

        pendientes = [p for p in pendientes if _hay_jurado(p[2])]
        repreguntas = []
        for k, objeto, votante, con_estado in pendientes[:REPREGUNTA_MAX]:
            # si el reclamo ya estaba confirmado, la pregunta lo VALIDA: si
            # ningún otro modelo ve el objeto, no se publica
            era_confirmada = k in confirmadas
            # Solo modelos que RESPONDIERON la pasada principal (un modelo
            # caído no puede ser fuente) y cuyos votos no fueron
            # desautorizados por una pasada dirigida (un voto anulado no
            # vuelve por la ventana). Tope: dos modelos por pregunta.
            otros = [v["modelo"] for v in activos
                     if v["modelo"] != votante
                     and v["modelo"] not in desc_desautorizadas][:2]
            if not otros:
                continue
            # Para lo que está APOYADO EN EL PISO la pregunta va ABIERTA (qué
            # hay ahí, sin nombrarlo): nombrar el objeto se midió sugestionable
            # en U003 y en U030. Para la PRESENCIA del contenedor se sigue
            # preguntando por el descriptor canónico, que es lo que evita que
            # un "sí" genérico lave el subtipo.
            # ...y solo si el objeto reclamado se puede CLASIFICAR: si no
            # sabemos a qué familia pertenece, la respuesta abierta no se
            # puede puntuar y se vuelve a la pregunta por el objeto.
            # Por ahora solo para recoleccion, que es donde se midió el
            # beneficio (U030). El voluminoso y los escombros siguen con la
            # pregunta por el objeto, que ya está protegida por el confirmo
            # estricto: un "ausente" enfrente no confirma.
            _esperado = _ESPERADO_ABIERTA.get(k)
            _incompat = _INCOMPATIBLE_ABIERTA.get(k)
            _abierta = _esperado is not None
            if _abierta:
                resultados, fallo_r = _pregunta_abierta(img, otros)
                for r in resultados:
                    # el modelo nombró lo suyo: corrobora si nombró lo que el
                    # reclamo necesita; si nombró OTRA cosa (en U030, "ramas o
                    # restos de poda" donde el reclamo eran bolsas), eso es un
                    # desmentido sobre el mismo lugar, no una abstención
                    if r["veredicto"] == "identificado":
                        # el texto va SIN sus tramos negados: "ramas, no
                        # bolsas" nombra la palabra pero la está negando
                        # (hallazgo de codex)
                        _qe = _sin_negado(r.get("que_es") or "")
                        _inc = bool(_incompat and _incompat.search(_qe))
                        if _esperado.search(_qe) and not _inc:
                            r["veredicto"] = "presente"
                        elif _inc and not _esperado.search(_qe):
                            r["veredicto"] = "ausente"
                        else:
                            # genérico ("basura", "residuos sueltos"): no
                            # confirma pero tampoco desmiente
                            r["veredicto"] = "no_se_distingue"
                    elif r["veredicto"] == "nada":
                        # "nada EN EL PISO" no desmiente unas bolsas apoyadas
                        # sobre la tapa del contenedor: la pregunta abierta
                        # mira el piso, el reclamo puede estar en otro lado.
                        # Cuenta como abstención: igual no alcanza para
                        # publicar, pero no anula el voto (hallazgo de fable).
                        r["veredicto"] = "no_se_distingue"
                    else:
                        r["veredicto"] = "no_se_distingue"
                con_estado = False
            else:
                resultados, fallo_r = _repregunta_objeto(img, objeto, otros,
                                                         con_estado)
            presentes = [r for r in resultados
                         if r["veredicto"] == "presente"
                         and (not con_estado or r.get("estado") == "descartado")]
            ausentes = [r for r in resultados if r["veredicto"] == "ausente"]
            en_uso = [r for r in resultados
                      if con_estado and r["veredicto"] == "presente"
                      and r.get("estado") == "en_uso"]
            # Confirma el que lo reportó MÁS UNO que lo ve dirigido, siempre
            # que NADIE lo contradiga. El "2 de 3 alcanza aunque el tercero
            # diga que no" se probó en la foto U003 y no se sostiene: ahí el
            # mismo modelo que en su lectura libre había dicho DOS VECES
            # "cajas y paneles de cartón" contestó "presente" cuando se le
            # preguntó por «varias tablas largas», mientras el otro mantuvo
            # "se ven cartones y una caja, no tablas largas". Un sí que
            # contradice lo que ese mismo modelo vio solo no es evidencia
            # nueva: es sugestión. Con un "ausente" explícito enfrente, el
            # reclamo queda en posibles para que lo mire una persona, que es
            # barato; mandar el camión no lo es. Sin nadie que contradiga, un
            # solo dirigido alcanza: ese es el caso de "se les pasó en la
            # primera lectura". El "está EN USO" bloquea aparte: ahí no se
            # discute la existencia sino que sea un descarte.
            confirmo = bool(presentes) and not ausentes and not en_uso
            # La regla de fuentes correlacionadas se respeta TAMBIÉN acá:
            # con CONSENSO_VLM_SOLO=arbitro, una confirmación sin respaldo
            # del modelo local no se publica directo (queda en disputa para
            # el árbitro, como cualquier consenso solo-VLM).
            if (confirmo and CONSENSO_VLM_SOLO != "confirma"
                    and k not in PRESENCIA
                    and "modelo_local" not in fuentes.get(k, [])):
                confirmo = False
            if confirmo:
                # UNA fuente dirigida alcanza para el consenso de dos; no se
                # suman más para no inflar "confianza" con síes dirigidos.
                r = presentes[0]
                if r["modelo"] not in fuentes[k]:
                    fuentes[k].append(r["modelo"])
                # La gravedad queda con el único voto libre: se topea en 3
                # (típico) porque la mediana anti-inflación necesita votos
                # que acá no existen.
                if grav_votos.get(k):
                    grav_votos[k] = [min(3, g) for g in grav_votos[k]]
                confirmadas.add(k)
                disputadas.discard(k)
                presencia_dudosa.discard(k)
                repregunta_confirmadas.add(k)
            elif en_uso:
                # ALGUIEN LO VIO, pero en uso. El objeto existe: no se manda
                # el camión, pero el voto NO se anula. Va antes que la rama de
                # los ausentes justamente para que un "ausente" de otro no
                # anule algo que un dirigido acaba de ver (hallazgo de fable:
                # el orden de los elif contradecía al comentario).
                if era_confirmada:
                    confirmadas.discard(k)
                    disputadas.add(k)
                    adjudicadas_dirigidas.add(k)
            elif len(ausentes) >= 2:
                # CONTRADICHO POR MAYORÍA: los otros miraron ESE objeto y
                # dicen que NO ESTÁ. Hace falta más de uno: con un solo
                # "ausente" y otro que no pudo decidir, el empate 1 a 1 entre
                # el testigo y el dirigido no alcanza para borrar el reclamo
                # (hallazgo de fable; es la misma vara de las pasadas
                # hermanas, donde el empate mantiene). No se manda el camión, y
                # el voto se retira ANOTADO, confirmado o en disputa:
                # si solo se limpiaba el confirmado, el disputado se quedaba
                # entero en fuentes y el árbitro podía promoverlo (hallazgo de
                # codex).
                confirmadas.discard(k)
                disputadas.add(k)
                adjudicadas_dirigidas.add(k)
                for v in activos:
                    c = next((c for c in v["categorias"] if c["key"] == k),
                             None)
                    if c is None:
                        continue
                    v["categorias"] = [x for x in v["categorias"] if x is not c]
                    votos_anulados.append((v, c, "repregunta_cruzada"))
                    desc_desautorizadas.add(v["modelo"])
                    if v["modelo"] in fuentes.get(k, []):
                        fuentes[k].remove(v["modelo"])
                    # la gravedad del voto anulado tampoco puede quedar
                    # pesando cuando sobrevive el modelo local (hallazgo de
                    # fable; las pasadas hermanas ya la sacaban)
                    try:
                        g = min(5, max(1, int(c.get("gravedad", 1))))
                    except (TypeError, ValueError):
                        g = 1
                    if grav_votos.get(k) and g in grav_votos[k]:
                        grav_votos[k].remove(g)
                if not fuentes.get(k):
                    fuentes.pop(k, None)
                    grav_votos.pop(k, None)
                    disputadas.discard(k)
            elif era_confirmada:
                # NADIE PUDO DECIDIR: ni dos que lo vean descartado, ni una
                # mayoría que lo desmienta. No se publica, pero el voto NO se
                # anula: el reclamo queda como lo que es, algo que vio una
                # sola fuente.
                confirmadas.discard(k)
                disputadas.add(k)
                adjudicadas_dirigidas.add(k)
            repreguntas.append({"key": k, "objeto": objeto,
                                "respuestas": resultados,
                                "confirmo": confirmo, "fallo": fallo_r})
        if not repreguntas:
            repreguntas = None

    # VETO DE PRESENCIA DEL CONTENEDOR: dos VLM confirmaron un contenedor en
    # una foto que no tiene ninguno (T141), con el modelo local (entrenado
    # con estos contenedores) en <= 0.073 para TODAS las claves. Cuando el
    # local dice "acá no hay contenedor" con esa contundencia y los VLM
    # publican uno, se pregunta dirigido si existe; mayoría de "ausente"
    # baja la presencia a en_duda (no se publica como elemento). El caso
    # del recortado real (S003, local 0.17) queda por ENCIMA del piso y la
    # pasada ni corre. Las presencias confirmadas por la repregunta no se
    # re-vetan: ya traen ubicación dirigida.
    segunda_mirada_presencia = None
    pres_conf = (confirmadas & PRESENCIA) - repregunta_confirmadas
    if SEGUNDA_MIRADA_PRESENCIA and pres_conf:
        max_local = max((p.get("score", 0.0)
                         for p in prediccion_local.get("probabilidades") or []
                         if p.get("key") in CONTENEDOR_KEYS), default=0.0)
        if max_local <= PRESENCIA_LOCAL_PISO:
            pre_sm, aus_sm, fallo_pr = _segunda_mirada_presencia(img)
            retira_pres = len(aus_sm) > len(pre_sm)
            segunda_mirada_presencia = {
                "presentes": [{"modelo": m, "evidencia": e} for m, e in pre_sm],
                "ausentes": [{"modelo": m, "evidencia": e} for m, e in aus_sm],
                "retiro_votos": retira_pres,
                "fallo": fallo_pr,
            }
            if retira_pres:
                for k in sorted(pres_conf):
                    confirmadas.discard(k)
                    presencia_dudosa.add(k)
                    adjudicadas_dirigidas.add(k)
                    # los votos vuelven ANOTADOS al registro público, como
                    # en todas las pasadas hermanas; las descripciones de
                    # quienes vieron el contenedor quedan desautorizadas
                    for v in activos:
                        c = next((c for c in v["categorias"]
                                  if c["key"] == k), None)
                        if c is not None:
                            v["categorias"] = [x for x in v["categorias"]
                                               if x is not c]
                            votos_anulados.append(
                                (v, c, "segunda_mirada_presencia"))
                            desc_desautorizadas.add(v["modelo"])

    # VETO DE PRESENCIA POR CLAVE: el fantasma que se publica AL LADO de un
    # contenedor real (la bolsa verde leída como contenedor de reciclables en
    # T035/T109/T130). Ahí el veto de arriba no salta nunca, porque el máximo
    # local está altísimo por el contenedor que SÍ está. Solo corre para las
    # claves donde el local demostró ser detector confiable (ver el comentario
    # de PRESENCIA_POR_CLAVE) y, como todas las pasadas hermanas, no decide
    # sola: pregunta dirigido por ESE contenedor y necesita mayoría de
    # "ausente" para bajarlo a en_duda.
    segunda_mirada_presencia_clave = {}
    if SEGUNDA_MIRADA_PRESENCIA_CLAVE:
        _locales = {p.get("key"): p.get("score", 0.0)
                    for p in prediccion_local.get("probabilidades") or []}
        # Un subtipo que GANÓ una resolución de subtipo no entra acá. El caso
        # es T044: el local da lateral 0,000 y bilateral 0,070, la mirada del
        # subtipo corrige a bilateral (que es lo correcto según la etiqueta
        # humana) y ese mismo 0,070 haría saltar este veto contra el resultado
        # de la pasada anterior. La separación medida del piso vale para lo que
        # los VLM publicaron directo, no para lo que otra pasada adjudicó
        # (hallazgo de codex, reproducido). Regla de la casa: una pasada
        # dirigida no pisa la adjudicación de otra.
        candidatas = ((confirmadas & set(PRESENCIA_POR_CLAVE))
                      - repregunta_confirmadas - adjudicadas_dirigidas
                      - set(subtipos_firmes))
        for k in sorted(candidatas):
            # Sin puntaje del local para ESA clave no hay señal que contradiga
            # a los VLM: la puerta no abre (si el modelo local no corrió, su
            # silencio no es un "no hay").
            if not (0.0 <= _locales.get(k, 1.0) <= PRESENCIA_LOCAL_PISO):
                continue
            pre_k, aus_k, fallo_k = _segunda_mirada_presencia(
                img, DESCRIPTOR_CONTENEDOR[k])
            retira_k = len(aus_k) > len(pre_k)
            segunda_mirada_presencia_clave[k] = {
                "presentes": [{"modelo": m, "evidencia": e} for m, e in pre_k],
                "ausentes": [{"modelo": m, "evidencia": e} for m, e in aus_k],
                "retiro_votos": retira_k,
                "fallo": fallo_k,
            }
            if not retira_k:
                continue
            confirmadas.discard(k)
            presencia_dudosa.add(k)
            adjudicadas_dirigidas.add(k)
            for v in activos:
                c = next((c for c in v["categorias"] if c["key"] == k), None)
                if c is not None:
                    v["categorias"] = [x for x in v["categorias"] if x is not c]
                    votos_anulados.append(
                        (v, c, "segunda_mirada_presencia_clave"))
                    desc_desautorizadas.add(v["modelo"])

    arbitro = None
    en_duda = []
    if disputadas and activos:
        arbitro = _arbitrar(disputadas, activos, prediccion_local["probabilidades"],
                            categorias, confirmadas, sorted(subtipos_firmes), contexto,
                            sorted(disputadas & ctx_claims), fuentes, data_url)
        if arbitro and arbitro.get("ok"):
            decididas = set()
            for d in arbitro["decisiones"]:
                decididas.add(d["key"])
                # Por default el árbitro YA NO promueve a confirmado. Medido
                # sobre cuatro modelos de árbitro distintos: 2 rescates buenos
                # sobre 21 confirmaciones, y los cuatro peores que rechazar
                # todas las disputas. Lo que vio una sola fuente no es un
                # hecho: sale como POSIBLE, no como problema confirmado.
                if (ARBITRO_CONFIRMA and d.get("veredicto") == "confirmar"
                        and d["key"] not in adjudicadas_dirigidas):
                    confirmadas.add(d["key"])
            en_duda = sorted(disputadas - decididas - confirmadas)
        else:
            en_duda = sorted(disputadas)
    elif disputadas:
        # ningún verificador respondió: no hay con qué arbitrar
        en_duda = sorted(disputadas)
    en_duda = sorted(set(en_duda) | presencia_dudosa)

    # POSIBLES: lo que vio una sola fuente. No es un problema confirmado, pero
    # tampoco hay que tirarlo: si alguien sube la foto de un auto estacionado
    # normal y sin contexto, lo honesto es no afirmar nada y ofrecer lo que
    # podría llegar a ser, para que quien consume la API decida o repregunte.
    grav_local = (prediccion_local.get("gravedad") or {}).get("value")
    # Todas las lecturas de patente de la escena, sin importar bajo qué
    # categoría de vehículo vinieron: dos cadenas distintas EN LA FOTO son
    # duda aunque cuelguen de claves diferentes (suele ser el mismo vehículo
    # fichado bajo otra categoría por un modelo).
    lecturas_totales = {pat for lect in patentes.values() for pat in lect}
    # Patente de la escena según la primera pasada: UNA única cadena leída
    # en total, por al menos dos verificadores distintos (contados a través
    # de las claves: el mismo vehículo fichado bajo otra categoría sigue
    # siendo la misma chapa). Se calcula ANTES e independiente de la
    # confirmación de la categoría: en el modo "arbitro" el vehículo puede
    # quedar en posibles y la lectura coincidente vale igual.
    patente_escena = None
    if len(lecturas_totales) == 1:
        quienes = set()
        for por_pat in patentes.values():
            for lectores_pat in por_pat.values():
                quienes.update(lectores_pat)
        if len(quienes) >= 2:
            patente_escena = next(iter(lecturas_totales))
    finales = []
    for k in sorted(confirmadas):
        entrada = {
            "key": k,
            "nombre": categorias.get(k, {}).get("nombre", k),
            "gravedad": _gravedad_consenso(grav_votos.get(k)) or grav_local,
            "fuentes": fuentes.get(k, []),
        }
        if (k == "retiro_escombros" and segunda_mirada
                and segunda_mirada.get("confirmaron")):
            entrada["segunda_mirada"] = True
        # confirmada vía repregunta dirigida: el consumidor tiene que poder
        # distinguir un sí dirigido de dos avistajes libres
        if k in repregunta_confirmadas:
            entrada["repregunta"] = True
        finales.append(entrada)

    # Parte dañada: gana la más votada entre los modelos que la ubicaron; en
    # empate no se publica (el consumidor tiene su propio default, y un
    # empate cuerpo/tapa es exactamente la duda que no hay que laundear).
    for e in finales:
        votos = partes.get(e["key"])
        if votos:
            orden = sorted(votos.items(), key=lambda kv: len(kv[1]), reverse=True)
            if len(orden) == 1 or len(orden[0][1]) > len(orden[1][1]):
                e["parte"] = orden[0][0]

    # Segunda pasada de patente, con la foto a mayor resolución: a LADO_MAX
    # una chapa a unos metros no se lee, así que la primera pasada rara vez
    # la trae confirmada. Corre cuando alguna clave de vehículo fue
    # reportada por algún modelo de visión — confirmada O en posibles: el
    # dato de la patente le sirve al consumidor aunque la infracción no se
    # confirme desde la foto — y la primera pasada no tiene un CONFLICTO
    # (dos cadenas válidas distintas: eso es duda activa y más llamadas no
    # la anulan). Una lectura suelta sin nadie en contra NO bloquea: es una
    # candidata que la segunda pasada va a confirmar o callar — y si la
    # segunda pasada publica una cadena DISTINTA de la suelta, eso también
    # es conflicto y no sale nada. Los fragmentos inválidos ("AB-12") no
    # cuentan: son el garble de baja resolución que esta pasada resuelve.
    con_vehiculo = [e for e in finales if e["key"] in PATENTE_KEYS]
    vistos_vehiculo = {k for k in PATENTE_KEYS
                       if any(f != "modelo_local" for f in fuentes.get(k, []))}
    # También dispara el contexto del vecino ("auto mal estacionado", vía
    # categorias_contexto de los verificadores) y la sospecha del modelo
    # local: en el flujo real el reporte de vehículo llega con contexto, y
    # los modelos muchas veces no votan la infracción aunque la chapa esté
    # perfectamente a la vista. La situación puede no confirmarse; la
    # patente sirve igual (requisito del dueño del proyecto).
    for v in veredictos:
        if v.get("ok"):
            vistos_vehiculo |= {c.get("key") for c in
                                (v.get("categorias_contexto") or [])
                                if c.get("key") in PATENTE_KEYS}
    # La sospecha local cuenta solo por encima del umbral: predichas puede
    # traer el top-1 de relleno con score bajísimo, y eso no es señal.
    umbral_local = prediccion_local.get("umbral")
    vistos_vehiculo |= {
        p["key"] for p in prediccion_local["predichas"]
        if p["key"] in PATENTE_KEYS
        and (umbral_local is None or p.get("score", 0) >= umbral_local)}
    if (patente_escena is None and vistos_vehiculo
            and len(con_vehiculo) <= 1 and len(lecturas_totales) <= 1):
        leida = _leer_patente(img)
        if leida and (not lecturas_totales or leida in lecturas_totales):
            patente_escena = leida
    # En la entrada confirmada la patente va solo si el vehículo es UNO:
    # con dos problemas de vehículo no hay a cuál atribuírsela.
    if patente_escena and len(con_vehiculo) == 1:
        con_vehiculo[0]["patente"] = patente_escena

    # Lo que quedó sin confirmar: una sola fuente lo vio. Se devuelve como
    # POSIBLE, con quién lo vio y qué dijo el árbitro, para que quien consume
    # pueda repreguntarle al vecino en vez de recibir un silencio.
    dec_arb = {d["key"]: d for d in (arbitro or {}).get("decisiones", [])} \
        if isinstance(arbitro, dict) else {}
    posibles = []
    for k in sorted(set(disputadas) - confirmadas):
        if k in PRESENCIA:
            continue
        d = dec_arb.get(k) or {}
        posibles.append({
            "key": k,
            "nombre": categorias.get(k, {}).get("nombre", k),
            "gravedad": _gravedad_consenso(grav_votos.get(k)) or grav_local,
            "fuentes": fuentes.get(k, []),
            "origen": "foto",
            "arbitro": d.get("veredicto"),
            "motivo": d.get("motivo"),
        })

    # Descripción final consolidada: la redacta el árbitro si ya intervino; si
    # no, se elige la descripción del verificador que no contradiga un subtipo
    # ya resuelto y que más coincida con las categorías finales (a igual
    # coincidencia, la más detallada). Si TODAS las descripciones contradicen
    # un subtipo resuelto, se hace una llamada extra al árbitro para no
    # publicar una descripción con el subtipo equivocado. Lleva la foto si
    # ARBITRO_VE_FOTO está activo: si no la ve, no puede corregir nada.
    perdidos = {k for otros in subtipos_firmes.values() for k in otros}
    descripcion, descripcion_fuente = None, None
    if arbitro and arbitro.get("ok") and arbitro.get("descripcion"):
        descripcion, descripcion_fuente = arbitro["descripcion"], ARBITRO
    else:
        mejor = None
        # Si la segunda mirada de la base desautorizó votos, la descripción de
        # esos modelos casi seguro repite la lectura errada ("estructura
        # metálica tirada"); se la saltea mientras quede alguna otra.
        candidatos = [v for v in activos if v.get("descripcion")
                      and v["modelo"] not in desc_desautorizadas] \
            or [v for v in activos if v.get("descripcion")]
        for v in candidatos:
            claves_v = {c["key"] for c in v["categorias"]}
            clave = (not (claves_v & perdidos),
                     len(claves_v & confirmadas), len(v["descripcion"]))
            if mejor is None or clave > mejor[0]:
                mejor = (clave, v)
        if mejor and not mejor[0][0] and ARBITRO:
            arbitro = _arbitrar(set(), activos, prediccion_local["probabilidades"],
                                categorias, confirmadas, sorted(subtipos_firmes),
                                contexto, data_url=data_url)
        if arbitro and arbitro.get("ok") and arbitro.get("descripcion"):
            descripcion, descripcion_fuente = arbitro["descripcion"], ARBITRO
        elif mejor:
            descripcion, descripcion_fuente = mejor[1]["descripcion"], mejor[1]["modelo"]
    # EL OBJETO QUE VIO UNO SOLO NO SE AFIRMA. La regla existía únicamente en
    # el prompt del árbitro, así que no se aplicaba cuando no había disputa (la
    # descripción del verificador elegido se publicaba tal cual) y, medido en
    # U032, tampoco cuando el árbitro sí corría: los tres modelos nombraron
    # objetos DISTINTOS (una maceta rota, un cesto arrancado, una caja de
    # cartón) y el árbitro publicó igual uno de esos, que ninguna otra fuente
    # respalda. Acá se aplica mecánicamente: se borra la frase que nombra un
    # objeto concreto que no menciona ninguna otra fuente. Es la misma cirugía
    # de las pasadas dirigidas, con el mismo criterio conservador (borrar de
    # más solo cuesta detalle; afirmar de más manda un camión).
    if descripcion and len(activos) > 1 and SANEO_PROSA:
        _fuentes_txt = []
        for v in activos:
            _t = " ".join([_norm_texto(v.get("descripcion") or "")]
                          + [_norm_texto(c.get("evidencia") or "")
                             for c in v.get("categorias") or []])
            _fuentes_txt.append(_t)
        # Las miradas dirigidas TAMBIÉN son fuentes: si la repregunta cruzada
        # confirmó el sillón, o la firma de identidad lo nombró, ese objeto
        # está respaldado y la prosa lo puede decir. Sin esto, el sistema
        # confirmaba un voluminoso y después se negaba a describirlo
        # (hallazgo de codex, reproducido).
        for q in (repreguntas or []):
            for r in q.get("respuestas") or []:
                # el mismo predicado que usa la confirmación, no uno parecido:
                # solo respalda el que vio el objeto Y lo vio DESCARTADO. Un
                # "presente pero EN USO" (o un estado que el modelo no pudo
                # determinar) no sostiene la prosa del descarte, que era lo
                # que dejaba publicar "hay un sillón descartado" justo cuando
                # la pasada dirigida acababa de decir que está en uso
                # (hallazgo de fable; el estado ambiguo lo agregó codex).
                if (r.get("veredicto") == "presente"
                        and (r.get("estado") or "descartado") == "descartado"):
                    _fuentes_txt.append(_norm_texto(
                        str(q.get("objeto") or "") + " "
                        + str(r.get("evidencia") or "")))
        for d in ((segunda_mirada_voluminoso or {}).get("identificados") or []):
            _fuentes_txt.append(_norm_texto(str(d.get("objeto") or "")))
        for d in ((segunda_mirada or {}).get("confirmaron") or []):
            _fuentes_txt.append(_norm_texto(
                "escombros " + str(d.get("evidencia") or "")))
        # el texto de cada fuente SIN sus tramos negados: lo que un modelo
        # desmiente no respalda a otro
        _fuentes_negadas = [_sin_negado(t) for t in _fuentes_txt]

        def _sanear(texto):
            """Saca las frases que nombran un objeto que vio una sola fuente.

            La corroboración es por PALABRA, no por familia: que otro modelo
            haya dicho "cajón" no habilita publicar "caja" (hallazgo de codex,
            reproducido con caja/cajón y con mesa/silla).
            """
            frases = re.split(r"(?<=[.!?])\s+", texto)
            limpias, saco = [], False
            for f in frases:
                _n = _norm_texto(f)
                solo = False
                _pats = list(_OBJETOS_CONCRETOS.values())
                # Los ambiguos entran solo si la señal de descarte está PEGADA
                # al objeto. A nivel frase no servía: "bolsas acumuladas frente
                # a la puerta de un garaje" tiene "acumuladas" (que califica a
                # las bolsas) y "puerta" (que es la escena), y la frase entera
                # se borraba (hallazgo de fable).
                _amb = [m.group(0) for m in _OBJETOS_SEGUN_CONTEXTO.finditer(_n)
                        if _cerca_del_descarte(_n, m)]
                if _amb:
                    _pats.append(re.compile(
                        "|".join(re.escape(a) for a in _amb)))
                for pat in _pats:
                    if not pat.search(_n):
                        continue
                    # La corroboración es por FAMILIA de objeto, no por la
                    # palabra exacta. Exigir el mismo sustantivo se midió y
                    # sale carísimo: sobre 20 fotos cambiaba el 65% de las
                    # descripciones y varias perdían justo la frase
                    # informativa, porque un modelo dice "cajas" y otro
                    # "cartones" para la misma pila. Con la familia, los
                    # cuatro casos del dueño se siguen cazando (en U032 los
                    # tres objetos eran de familias DISTINTAS: maceta, cesto y
                    # caja) y la prosa correcta sobrevive. El residuo conocido
                    # es que "cajón" respalda "caja": ver CASOS.md.
                    if sum(1 for t in _fuentes_negadas if pat.search(t)) < 2:
                        solo = True
                        break
                if solo:
                    saco = True
                    continue
                limpias.append(f)
            return " ".join(limpias).strip(), saco

        _saneada, _saco = _sanear(descripcion)
        if _saco:
            # Si la elegida se queda sin nada, se prueba con las otras: es
            # mejor la prosa de otro modelo que sobrevive entera que una línea
            # armada con los nombres de las categorías.
            if not _saneada:
                for v in activos:
                    otra = _texto_limpio(v.get("descripcion"), DESC_MAX)
                    if not otra or v["modelo"] in desc_desautorizadas:
                        continue
                    # el repuesto tampoco puede traer de vuelta el subtipo que
                    # se descartó: para eso existe toda la maquinaria de
                    # arriba, que hasta paga una llamada extra al árbitro
                    # (hallazgo de fable)
                    if {c["key"] for c in v["categorias"]} & perdidos:
                        continue
                    cand, _ = _sanear(otra)
                    if len(cand) > len(_saneada):
                        _saneada, descripcion_fuente = cand, v["modelo"]
            if not _saneada:
                # nadie sobrevive: queda el veredicto, que es lo respaldado
                _nombres = [categorias.get(k, {}).get("nombre", k)
                            for k in sorted(confirmadas - PRESENCIA)]
                _saneada = ("En la foto se ve " + ", ".join(_nombres) + "."
                            if _nombres else
                            "No se distingue con claridad qué hay en la foto.")
            descripcion = _saneada
            descripcion_fuente = (descripcion_fuente or "") + " (saneada)"

    # Si la pasada dirigida CONFIRMÓ que la estructura metálica es la base del
    # contenedor y la descripción elegida no lo dice, se lo agrega: publicar la
    # categoría sin explicarla dejaría al vecino sin saber qué se reportó. Vale
    # también para la descripción del árbitro (el bloque corre después de ambas
    # ramas) y para el caso en que TODAS las descripciones venían de modelos
    # desautorizados y una quedó igual como último recurso.
    if segunda_mirada_base and descripcion:
        if (segunda_mirada_base.get("promovio")
                and "base" not in _norm_texto(descripcion)):
            descripcion = descripcion.rstrip() + (
                " La estructura metálica baja que se ve en el piso es la base "
                "del contenedor, que está corrido de su lugar.")
        elif (segunda_mirada_base.get("retiro_votos")
              and not segunda_mirada_base.get("promovio")
              and "base" not in _norm_texto(descripcion)
              and _evidencia_metalica(descripcion)):
            # Retiro sin confirmación plena: no se afirma que ES la base, pero
            # tampoco se deja la prosa vendiendo la chatarra que se retiró.
            descripcion = descripcion.rstrip() + (
                " La estructura metálica del piso podría ser la base de un "
                "contenedor y no un descarte; por eso no se reporta como "
                "residuo voluminoso.")
    # Ídem con el veto del daño: si todas las descripciones venían de modelos
    # desautorizados, la heredada puede seguir afirmando "tapas rotas". Se
    # filtran las frases que atribuyen rotura al contenedor (solo esas: una
    # "bolsa rota" no dispara) y se aclara el estado real.
    if (segunda_mirada_dano and segunda_mirada_dano.get("retiro_votos")
            and descripcion):
        _rotura = re.compile(r"rot[ao]|desprendid|partid|quebrad|arrancad|"
                             r"desmontad|destroz|dañad|danad|agrietad|"
                             r"quemad|perforad|derretid|deteriorad|"
                             r"pieza[s]? faltante|falta la tapa|sin tapa")
        _del_cont = re.compile(r"tapa|cabezal|contenedor|pedal")
        frases = re.split(r"(?<=[.!?])\s+", descripcion)
        limpias = [f for f in frases
                   if not (_rotura.search(_norm_texto(f))
                           and _del_cont.search(_norm_texto(f)))]
        if len(limpias) < len(frases):
            nota = ("Visto de cerca, el contenedor está entero: las tapas "
                    "abiertas o dadas vuelta son por el uso, no una rotura.")
            descripcion = (" ".join(limpias).strip() + " " + nota).strip() \
                if limpias else nota

    # Ídem con el veto de presencia: si se adjudicó que NO hay contenedor
    # municipal, la prosa no puede seguir afirmándolo.
    if (segunda_mirada_presencia
            and segunda_mirada_presencia.get("retiro_votos") and descripcion):
        frases = re.split(r"(?<=[.!?])\s+", descripcion)
        limpias = [f for f in frases
                   if "contenedor" not in _norm_texto(f)]
        if len(limpias) < len(frases):
            nota = ("Revisado de cerca, lo que se ve no es un contenedor "
                    "municipal (parece un tacho o cesto particular); no se "
                    "registra un contenedor en esta foto.")
            descripcion = (" ".join(limpias).strip() + " " + nota).strip() \
                if limpias else nota

    # Ídem con el veto por clave, pero quirúrgico: acá hay un contenedor real
    # en la escena, así que se borran solo las frases que hablan del tipo
    # vetado (el "verde de reciclables" que era una bolsa), no toda mención a
    # un contenedor.
    # El rasgo tiene que MODIFICAR al contenedor, no aparecer suelto en la
    # frase: "bolsas verdes junto a un contenedor gris claro" no habla del
    # contenedor verde y no se toca (hallazgo de codex). De ahí la adyacencia.
    def _rasgo_de_contenedor(rasgo):
        # Hasta tres palabras de relleno entre "contenedor" y el rasgo
        # ("contenedor municipal de color gris claro"), pero ninguna puede ser
        # de UBICACIÓN: ahí el rasgo ya pasó a describir otra cosa
        # ("contenedor gris junto a bolsas verdes").
        relleno = (r"(?:\s+(?!junto|cerca|sobre|encima|detras|delante|frente|"
                   r"lado|arriba|abajo|bajo|contra|y|con|mas)\w+){0,3}")
        return re.compile(
            r"contenedor(?:es)?" + relleno + r"\s+(?:" + rasgo + r")"
            r"|(?:" + rasgo + r")\s+(?:de\s+\w+\s+)?contenedor(?:es)?")

    _RASGOS_VETADOS = {
        "contenedor_secos": _rasgo_de_contenedor(r"verde\w*|de reciclabl\w*"),
        "contenedor_humedos_bilateral": _rasgo_de_contenedor(
            r"bilateral\w*|gris(?:\s+(?:claro|oscuro))?"),
        "contenedor_humedos_lateral": _rasgo_de_contenedor(
            r"lateral\w*|panzon\w*|redondead\w*"),
    }
    for k, d in sorted(segunda_mirada_presencia_clave.items()):
        if not (d.get("retiro_votos") and descripcion):
            continue
        patron = _RASGOS_VETADOS.get(k)
        if patron is None:
            continue
        frases = re.split(r"(?<=[.!?])\s+", descripcion)
        limpias, toco = [], False
        for f in frases:
            if not (patron.search(_norm_texto(f))
                    and "contenedor" in _norm_texto(f)):
                limpias.append(f)
                continue
            toco = True
            # Primero se intenta cirugía fina: en la foto suele haber un
            # contenedor REAL nombrado en la MISMA frase que el fantasma
            # ("un contenedor gris ... y un contenedor verde al lado"), y
            # borrar la frase entera se lleva puesto al real (hallazgo de
            # codex). Se corta la parte que habla del vetado y se conserva
            # el resto si queda una frase con cuerpo.
            partes = re.split(r"\s+y,?\s+", f)
            # Solo sobrevive lo que viene ANTES del fantasma: cortar del medio
            # deja colgado el sujeto ("... y está al lado de un contenedor
            # gris" queda sin quién; hallazgo de codex).
            resto = []
            for p in partes:
                if patron.search(_norm_texto(p)):
                    break
                resto.append(p)
            if resto and len(resto) < len(partes) \
                    and len(" ".join(resto).split()) >= 4:
                arreglada = " y ".join(resto).strip(" ,;")
                limpias.append(arreglada.rstrip(".") + ".")
        if toco:
            nota = "Revisado de cerca, ese contenedor no está en la foto."
            descripcion = (" ".join(limpias).strip() + " " + nota).strip() \
                if limpias else nota

    # Ídem con la validación cruzada: si el objeto que sostenía el reclamo
    # quedó anulado porque los otros modelos no lo ven, la prosa no puede
    # seguir describiéndolo (hallazgo de fable: la descripción del votante
    # anulado podía volver por el camino de repuesto).
    # Cada clave tiene SU vocabulario, con borde de palabra ("sobran" no es
    # "obra", "inmueble" no es "mueble"), y una frase no se borra si habla de
    # una clave que SIGUE confirmada: si no, la prosa terminaba desmintiendo
    # un reclamo vigente en el mismo payload (hallazgo de fable, reproducido).
    _VOCAB_CLAVE = {
        # el vocabulario de muebles es el MISMO de _PATRON_MUEBLE más lo que
        # solo aparece en prosa: dos listas divergentes se despegan solas
        # (deuda que marcó fable)
        "retiro_muebles": re.compile(
            _PATRON_MUEBLE.pattern.rstrip(r")\b")
            + r"|tablas?|tablon(?:es)?|maderas?|chatarra|voluminosos?|"
              r"canastos?|cajon(?:es)?)\b"),
        "retiro_escombros": re.compile(
            r"\b(?:escombro|escombros|cascote|cascotes)\b|"
            r"\b(?:material|materiales|restos|bolsas|sacos) de obra\b"),
    }
    _anuladas_cruzada = {c["key"] for _v, c, pasada in votos_anulados
                         if pasada == "repregunta_cruzada"} & set(_VOCAB_CLAVE)
    if _anuladas_cruzada and descripcion:
        _protegidas = [p for k, p in _VOCAB_CLAVE.items()
                       if k in confirmadas and k not in _anuladas_cruzada]
        frases = re.split(r"(?<=[.!?])\s+", descripcion)
        limpias = []
        for f in frases:
            _n = _norm_texto(f)
            if (any(_VOCAB_CLAVE[k].search(_n) for k in _anuladas_cruzada)
                    and not any(p.search(_n) for p in _protegidas)):
                continue
            limpias.append(f)
        if len(limpias) < len(frases):
            nota = ("Mirado de cerca, ese objeto no se distingue en la foto: "
                    "no se reporta un retiro por él.")
            descripcion = (" ".join(limpias).strip() + " " + nota).strip() \
                if limpias else nota

    # Ídem con el veto del volcado: la prosa heredada no puede seguir
    # afirmando el contenedor tumbado.
    if (segunda_mirada_volcado and segunda_mirada_volcado.get("retiro_votos")
            and descripcion):
        _volc_txt = re.compile(r"volcad|caid|tumbad|acostad|dado vuelta")
        _cont_txt2 = re.compile(r"contenedor")
        frases = re.split(r"(?<=[.!?])\s+", descripcion)
        limpias = [f for f in frases
                   if not (_volc_txt.search(_norm_texto(f))
                           and _cont_txt2.search(_norm_texto(f)))]
        if len(limpias) < len(frases):
            nota = ("Visto de cerca, el contenedor está parado: la "
                    "inclinación es del ángulo de la foto, no un volcado.")
            descripcion = (" ".join(limpias).strip() + " " + nota).strip() \
                if limpias else nota

    # Categorías que el contexto vecinal describe pero la foto no confirma:
    # unión de lo que reportaron los verificadores, sin las ya confirmadas.
    # No cuentan para gravedad_maxima ni sin_problema (no son evidencia visual),
    # pero le dan al consumidor el tipo de reporte que el texto está pidiendo.
    # Las sugerencias condicionadas a un objeto visible se validan acá: lavado
    # de contenedor/cesto exige que ese objeto aparezca en alguna fuente; si no
    # aparece, el reclamo (p. ej. olores) se remapea a desratizacion
    # (desinfección de la vía pública) en vez de descartarse.
    contenedor_keys = CONTENEDOR_KEYS
    cesto_keys = {"vaciado_cesto", "reparacion_cesto", "lavado_cesto"}
    # Las claves de contenedor vetadas por la pasada de presencia no cuentan
    # como "objeto visible" para validar sugerencias del contexto: sin
    # contenedor adjudicado, el pedido de lavado se remapea a desinfección
    # como corresponde (hallazgo de codex, reproducido).
    vistos_todos = set(fuentes)
    if (segunda_mirada_presencia
            and segunda_mirada_presencia.get("retiro_votos")):
        vistos_todos -= CONTENEDOR_KEYS
    # Ídem por clave: el contenedor fantasma vetado tampoco valida un pedido
    # de lavado. Los que quedaron (el contenedor real de al lado) sí.
    vistos_todos -= {k for k, d in segunda_mirada_presencia_clave.items()
                     if d.get("retiro_votos")}
    remap = {}
    if not (vistos_todos & contenedor_keys):
        remap["lavado_contenedor"] = "desratizacion"
    if not (vistos_todos & cesto_keys):
        remap["lavado_cesto"] = "desratizacion"
    # respaldo_visual: qué tan consistente es la foto con el reclamo (sin
    # confirmarlo). Entre verificadores gana el mayor respaldo. Las entradas
    # pueden ser categorías propias (key) o prestaciones del catálogo completo
    # de la Ciudad (codigo).
    rango = {"compatible": 2, "neutral": 1, "contradice": 0}
    # Lo que el vecino pidió por texto y ADEMÁS se vio en la foto sale de
    # categorias_contexto (ya está en confirmadas, no es una sugerencia). Pero
    # si después la foto resulta no corresponder, el pedido del vecino sigue
    # en pie: se guarda acá para no perderlo.
    ctx_ya_confirmadas = {}
    ctx_resp = {}
    ctx_votos = {}   # cuántos verificadores propusieron cada sugerencia
    for v in activos:
        for c in v.get("categorias_contexto") or []:
            if c.get("key"):
                k = remap.get(c["key"], c["key"])
                if k in confirmadas:
                    ctx_ya_confirmadas[k] = categorias.get(k, {}).get("nombre", k)
                    continue
                ident = ("key", k)
            else:
                ident = ("codigo", c["codigo"])
            r = c.get("respaldo", "neutral")
            # Antes ganaba el respaldo MÁS ALTO, así que un "compatible" tapaba
            # el "contradice" de otro modelo. Para juzgar si la foto sirve eso
            # es al revés: gana el más cauto.
            if ident not in ctx_resp or rango[r] < rango[ctx_resp[ident]]:
                ctx_resp[ident] = r
            ctx_votos[ident] = ctx_votos.get(ident, 0) + 1
    categorias_contexto = []
    for (tipo, valor) in sorted(ctx_resp, key=lambda t: (t[0], t[1])):
        # Cuántos de los verificadores que respondieron propusieron esto. Se
        # expone porque una sugerencia de UN solo modelo no vale lo mismo que
        # una que propusieron todos, y ahora estas sugerencias pueden hacer
        # que hay_problema sea true: el consumidor tiene que poder distinguir.
        votos = {"fuentes": ctx_votos[(tipo, valor)], "de": len(activos)}
        if tipo == "key":
            categorias_contexto.append(
                {"key": valor, "nombre": categorias.get(valor, {}).get("nombre", valor),
                 "respaldo_visual": ctx_resp[(tipo, valor)], **votos})
        else:
            p = _PRESTACIONES_POR_CODIGO.get(valor, {})
            categorias_contexto.append(
                {"codigo": valor, "nombre": p.get("concepto", valor),
                 "respaldo_visual": ctx_resp[(tipo, valor)], **votos})
    # Si una prestación del catálogo duplica una categoría propia ya sugerida
    # (mismo nombre), queda solo la propia.
    nombres_key = {_norm_texto(c["nombre"]) for c in categorias_contexto if c.get("key")}
    categorias_contexto = [c for c in categorias_contexto
                           if c.get("key") or _norm_texto(c["nombre"]) not in nombres_key]

    # ¿La foto tiene que ver con lo que el vecino contó? Solo tiene sentido
    # preguntarlo si hay contexto. Decide la mayoría de los verificadores que
    # opinaron; si empatan, o ninguno opinó, no se afirma nada (None).
    foto_valida, foto_estado = None, "sin_contexto"
    if contexto:
        votos = [v.get("foto_corresponde") for v in activos
                 if v.get("foto_corresponde") is not None]
        if not activos:
            foto_estado = "no_evaluado"      # ningún verificador respondió
        elif not votos:
            foto_estado = "sin_opinion"      # respondieron pero no se pronunciaron
        else:
            si, no = votos.count(True), votos.count(False)
            if si > no:
                foto_valida, foto_estado = True, "corresponde"
            elif no > si:
                foto_valida, foto_estado = False, "no_corresponde"
            else:
                foto_estado = "empate"       # los modelos no coinciden
        # Segunda señal, independiente de la anterior: si TODO lo que el vecino
        # denuncia quedó marcado "contradice" (la foto muestra lo contrario) y
        # además la foto no confirmó nada, la foto no sirve para este reclamo,
        # por más que los modelos hayan dicho que sí corresponde.
        respaldos = [c.get("respaldo_visual") for c in categorias_contexto]
        if (respaldos and all(r == "contradice" for r in respaldos)
                and not [c for c in finales if c["key"] not in PRESENCIA]):
            foto_valida, foto_estado = False, "no_corresponde"

    # EL RECLAMO MANDA. Si el vecino escribió algo y la foto no lo respalda,
    # lo que vale es lo que él dijo: los modelos de visión están describiendo
    # otra cosa, no lo que vino a reportar. Se encamina el reclamo con el
    # texto solo, cayendo a la categoría genérica cuando no alcanza para
    # distinguir. Si el texto tampoco pide nada del catálogo, no hay reclamo.
    por_contexto, ruteo_fallo = [], False
    if foto_valida is False:
        # primero lo que ya dedujeron los verificadores leyendo el contexto
        # Se conservan también las entradas del catálogo completo de la
        # Ciudad, que traen "codigo" en vez de "key": si el vecino pidió una
        # prestación que existe, el reclamo es esa, aunque no sea una de las
        # categorías propias del modelo.
        por_contexto = [
            dict({k: c[k] for k in ("key", "codigo") if c.get(k)},
                 nombre=c["nombre"], gravedad=2,
                 fuentes=["contexto_vecinal"])
            for c in categorias_contexto if c.get("key") or c.get("codigo")]
        # Y lo que el vecino pidió que además se veía en la foto: la foto se
        # descarta, el pedido no. Sin esto, un reclamo de dos incidencias
        # perdía la que el modelo había confirmado visualmente, y como la
        # lista no quedaba vacía tampoco se reencaminaba por texto.
        vistos_pc = {c.get("key") for c in por_contexto}
        # También por NOMBRE: una prestación del catálogo puede ser la misma
        # cosa que una categoría propia con otro identificador, y duplicarlas
        # abriría dos reclamos por lo mismo.
        nombres_pc = {_norm_texto(c["nombre"]) for c in por_contexto}
        for k, nombre in sorted(ctx_ya_confirmadas.items()):
            if (k not in vistos_pc and k not in PRESENCIA
                    and _norm_texto(nombre) not in nombres_pc):
                nombres_pc.add(_norm_texto(nombre))
                por_contexto.append({"key": k, "nombre": nombre, "gravedad": 2,
                                     "fuentes": ["contexto_vecinal"]})
        if not por_contexto:
            ruteo = _clasificar_contexto(contexto, categorias)
            ruteo_fallo = ruteo is None
            por_contexto = ruteo or []

    # Los votos que la segunda mirada de la base retiró vuelven al registro
    # público ANOTADOS, recién acá: el árbitro y la descripción ya corrieron
    # sin verlos, pero el veredicto crudo de cada modelo no se falsifica. El
    # consumidor ve que el modelo lo dijo y que una pasada dirigida lo anuló.
    for v, c, pasada in votos_anulados:
        v["categorias"].append({**c, "anulada_por": pasada})

    return {
        "activa": True,
        # El contexto del vecino NO se devuelve: el cliente ya tiene el texto
        # que envió, y el eco solo duplica PII (nombres, patentes, firmas)
        # hacia logs y capturas. Sigue entrando a los modelos como pista.
        # La patente en cambio SÍ: se lee de la chapa fotografiada, nunca
        # del texto (README, excepción deliberada).
        "patente": patente_escena,
        "foto_valida": foto_valida,
        # null es ambiguo por sí solo: puede ser que no haya contexto, que los
        # modelos no coincidan, o que la verificación no haya corrido. Un
        # consumidor NO debe leer null como "la foto está bien".
        "foto_valida_estado": foto_estado,
        "por_contexto": por_contexto,
        # El encaminamiento del reclamo por texto no pudo correr: la respuesta
        # NO es estable y no se debe cachear.
        "ruteo_contexto_fallo": ruteo_fallo,
        "posibles": posibles,
        "verificadores": veredictos,
        "arbitro": arbitro,
        # Metadata de la segunda mirada de escombros (None si no corrió):
        # el cache la mira para no congelar un "no" hecho con fallos de red.
        "segunda_mirada": segunda_mirada,
        # Ídem para la segunda mirada de la base del contenedor.
        "segunda_mirada_base": segunda_mirada_base,
        # Ídem para la del daño del contenedor (tapas dadas vuelta, fierros).
        "segunda_mirada_dano": segunda_mirada_dano,
        # Ídem para la del volcado (techo en pendiente leído como tumbado).
        "segunda_mirada_volcado": segunda_mirada_volcado,
        # Repreguntas dirigidas entre modelos (None si no corrió ninguna).
        "repreguntas": repreguntas,
        # Mirada dirigida del subtipo (None si no corrió).
        "segunda_mirada_subtipo": segunda_mirada_subtipo,
        # Chequeo de los postes citados (None si no corrió).
        "segunda_mirada_postes": segunda_mirada_postes,
        # Firma de identidad del voluminoso marginal (None si no corrió).
        "segunda_mirada_voluminoso": segunda_mirada_voluminoso,
        # Mirada dirigida del desborde (None si no corrió).
        "segunda_mirada_desborde": segunda_mirada_desborde,
        # Veto de presencia del contenedor (None si no corrió).
        "segunda_mirada_presencia": segunda_mirada_presencia,
        # Veto de presencia POR CLAVE: {clave: {...}}, vacío si no corrió.
        "segunda_mirada_presencia_clave": segunda_mirada_presencia_clave,
        # Claves que una pasada dirigida ya adjudicó (bajó o corrigió). Interno:
        # ninguna capa de más arriba puede volver a inyectarlas (la fusión de
        # escombros del servidor las resucitaba; hallazgo de codex).
        "adjudicadas_dirigidas": sorted(adjudicadas_dirigidas),
        "confirmadas": finales,
        "en_duda": en_duda,
        # Interno, para que el serializador pueda filtrar en_duda por fuente:
        # las claves de PRESENCIA no aparecen en posibles, así que sin esto no
        # habría forma de saber quién vio una presencia en disputa.
        "fuentes_en_duda": {k: sorted(fuentes.get(k, [])) for k in en_duda},
        "categorias_contexto": categorias_contexto,
        "descripcion": descripcion,
        "descripcion_fuente": descripcion_fuente,
    }
