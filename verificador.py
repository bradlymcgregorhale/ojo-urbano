"""Verificación cruzada de clasificaciones con modelos de visión vía OpenRouter.

El modelo local propone categorías; dos modelos de visión (por defecto Kimi y
Qwen3-VL) miran la foto de forma independiente. Una categoría queda confirmada
cuando la reportan al menos 2 de las 3 fuentes (modelo local + 2 verificadores).
Las categorías con una sola fuente van a un árbitro de texto (por defecto
DeepSeek), que lee ambos veredictos y las probabilidades del modelo local y
decide. Sin árbitro configurado, quedan marcadas "en_duda".

Config por variables de entorno (ver .env.example):
    OPENROUTER_API_KEY   requerida para verificar; sin ella la API responde
                         solo con el modelo local.
    VERIFICADORES        lista separada por comas de modelos de visión.
    ARBITRO              modelo de texto para desempates ("" lo desactiva).
    VERIFICADOR_TIMEOUT  segundos por llamada (default 120).
"""
import base64
import concurrent.futures
import io
import json
import os
import re
import urllib.error
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
VERIFICADORES = [m.strip() for m in os.environ.get(
    "VERIFICADORES", "moonshotai/kimi-k2.5,qwen/qwen3-vl-8b-instruct").split(",") if m.strip()]
ARBITRO = os.environ.get("ARBITRO", "deepseek/deepseek-v4-flash").strip()
TIMEOUT = int(os.environ.get("VERIFICADOR_TIMEOUT", "120"))
LADO_MAX = 1024  # la foto se reduce a este lado máximo antes de enviarla


def api_key():
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def disponible():
    return bool(api_key()) and bool(VERIFICADORES)


def _imagen_data_url(img):
    """PIL.Image -> data URL JPEG reducida (menos tokens, misma señal)."""
    img = img.copy()
    img.thumbnail((LADO_MAX, LADO_MAX))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _llamar(modelo, mensajes, max_tokens=2500, intentos=2):
    body = json.dumps({"model": modelo, "max_tokens": max_tokens,
                       "messages": mensajes}).encode()
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
        "Authorization": "Bearer " + api_key(),
        "Content-Type": "application/json",
    })
    ultimo = None
    for _ in range(intentos):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.load(r)
            msg = data["choices"][0]["message"]
            # algunos modelos razonadores dejan el JSON en "reasoning"
            contenido = msg.get("content") or msg.get("reasoning") or ""
            if "{" in contenido:
                return contenido
            ultimo = ValueError("respuesta sin JSON")
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, OSError) as e:
            ultimo = e
    raise ultimo


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


def _prompt_verificador(categorias):
    lineas = "\n".join(f"- {k}: {v['nombre']}" for k, v in categorias.items()
                       if k != "sin_problema")
    return (
        "Sos un inspector de incidencias urbanas. Mirá la foto y decidí qué "
        "problemas hay, usando SOLO estas categorías (clave: descripción):\n"
        f"{lineas}\n\n"
        "Reglas:\n"
        "- Reportá únicamente lo que se ve con certeza en la foto; ante la duda, omití la categoría.\n"
        "- Una foto puede tener varias categorías (una por problema visible).\n"
        "- gravedad: 1 (mínima) a 5 (muy grave) para cada categoría.\n"
        "- Si no hay ningún problema, devolvé la lista vacía y sin_problema en true.\n\n"
        "Respondé SOLO con JSON válido, sin texto adicional:\n"
        '{"categorias": [{"key": "...", "gravedad": 1-5, "evidencia": "qué se ve"}], '
        '"sin_problema": true|false}'
    )


def _verificar_uno(modelo, data_url, categorias):
    try:
        contenido = _llamar(modelo, [{"role": "user", "content": [
            {"type": "text", "text": _prompt_verificador(categorias)},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}])
        veredicto = _extraer_json(contenido)
        vistas = [c for c in veredicto.get("categorias", [])
                  if isinstance(c, dict) and c.get("key") in categorias]
        return {"modelo": modelo, "ok": True, "categorias": vistas,
                "sin_problema": bool(veredicto.get("sin_problema"))}
    except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError, OSError) as e:
        return {"modelo": modelo, "ok": False, "error": str(e)[:200]}


def _arbitrar(disputadas, veredictos, probabilidades, categorias):
    """El árbitro (modelo de texto) decide las categorías con una sola fuente."""
    if not ARBITRO:
        return None
    probas = {p["key"]: p["score"] for p in probabilidades[:12]}
    prompt = (
        "Actuás como árbitro de un clasificador de fotos de incidencias urbanas. "
        "Un modelo local y dos modelos de visión analizaron la misma foto (vos no la ves). "
        "Estas categorías fueron reportadas por UNA sola fuente y hay que decidir si se confirman.\n\n"
        f"Categorías (clave: nombre): {json.dumps({k: v['nombre'] for k, v in categorias.items()}, ensure_ascii=False)}\n\n"
        f"Probabilidades del modelo local (entrenado con miles de fotos reales): {json.dumps(probas, ensure_ascii=False)}\n\n"
        f"Veredictos de los modelos de visión: {json.dumps(veredictos, ensure_ascii=False)}\n\n"
        f"Categorías en disputa: {json.dumps(sorted(disputadas), ensure_ascii=False)}\n\n"
        "Confirmá una categoría solo si la evidencia citada es concreta o la probabilidad "
        "local es alta; ante la duda, rechazala. Respondé SOLO con JSON:\n"
        '{"decisiones": [{"key": "...", "veredicto": "confirmar"|"rechazar", "motivo": "..."}]}'
    )
    try:
        contenido = _llamar(ARBITRO, [{"role": "user", "content": prompt}])
        data = _extraer_json(contenido)
        decisiones = [d for d in data.get("decisiones", [])
                      if isinstance(d, dict) and d.get("key") in disputadas]
        return {"modelo": ARBITRO, "ok": True, "decisiones": decisiones}
    except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError, OSError) as e:
        return {"modelo": ARBITRO, "ok": False, "error": str(e)[:200]}


def verificar(img, categorias, prediccion_local):
    """Corre los verificadores en paralelo y consolida un veredicto final.

    img: PIL.Image ya abierta.
    categorias: dict de categorias.json.
    prediccion_local: dict con "predichas" y "probabilidades" (del modelo local).
    """
    data_url = _imagen_data_url(img)
    with concurrent.futures.ThreadPoolExecutor(len(VERIFICADORES)) as pool:
        veredictos = list(pool.map(
            lambda m: _verificar_uno(m, data_url, categorias), VERIFICADORES))

    grav = {}      # key -> max gravedad reportada por verificadores
    fuentes = {}   # key -> lista de fuentes que la reportan
    for p in prediccion_local["predichas"]:
        if p["key"] != "sin_problema":
            fuentes.setdefault(p["key"], []).append("modelo_local")
    for v in veredictos:
        if not v.get("ok"):
            continue
        for c in v["categorias"]:
            k = c["key"]
            fuentes.setdefault(k, []).append(v["modelo"])
            try:
                grav[k] = max(grav.get(k, 0), min(5, max(1, int(c.get("gravedad", 1)))))
            except (TypeError, ValueError):
                grav.setdefault(k, 1)

    activos = [v for v in veredictos if v.get("ok")]
    confirmadas = {k for k, f in fuentes.items() if len(f) >= 2}
    disputadas = {k for k, f in fuentes.items() if len(f) == 1}

    arbitro = None
    en_duda = []
    if disputadas and activos:
        arbitro = _arbitrar(disputadas, activos, prediccion_local["probabilidades"], categorias)
        if arbitro and arbitro.get("ok"):
            decididas = set()
            for d in arbitro["decisiones"]:
                decididas.add(d["key"])
                if d.get("veredicto") == "confirmar":
                    confirmadas.add(d["key"])
            en_duda = sorted(disputadas - decididas - confirmadas)
        else:
            en_duda = sorted(disputadas)
    elif disputadas:
        # ningún verificador respondió: no hay con qué arbitrar
        en_duda = sorted(disputadas)

    grav_local = (prediccion_local.get("gravedad") or {}).get("value")
    finales = []
    for k in sorted(confirmadas):
        finales.append({
            "key": k,
            "nombre": categorias.get(k, {}).get("nombre", k),
            "gravedad": grav.get(k) or grav_local,
            "fuentes": fuentes.get(k, []),
        })

    return {
        "activa": True,
        "verificadores": veredictos,
        "arbitro": arbitro,
        "confirmadas": finales,
        "en_duda": en_duda,
    }
