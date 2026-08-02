"""Verificación cruzada de clasificaciones con modelos de visión vía OpenRouter.

El modelo local propone categorías; dos modelos de visión (por defecto Kimi y
Qwen3-VL) miran la foto de forma independiente. Una categoría queda confirmada
cuando la reportan al menos 2 de las 3 fuentes (modelo local + 2 verificadores).
Las categorías con una sola fuente van a un árbitro de texto (por defecto
DeepSeek), que lee ambos veredictos y las probabilidades del modelo local y
decide. Sin árbitro configurado, quedan marcadas "en_duda".

Cada verificador devuelve además una descripción breve de la foto dentro de su
misma respuesta (sin llamadas extra). La descripción final consolidada la
redacta el árbitro cuando ya tiene que intervenir por una disputa; si no hay
disputa, se elige localmente la descripción del verificador que más coincide
con las categorías finales. El conteo de llamadas por foto no cambia.

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

# Sinónimos que se pliegan a una categoría canónica en TODA la API: el modelo
# local fue entrenado con estas clases pero la salida siempre usa la canónica.
FOLD = {
    "retiro_objetos": "retiro_muebles",
    "recoleccion_voluminosos": "retiro_muebles",
    "recoleccion_restos_obra": "retiro_escombros",
    "recoleccion_verdes": "retiro_poda",
    "diseminado": "recoleccion",
}

# Claves de PRESENCIA: indican que un contenedor se ve en la foto, no que haya
# un problema. No cuentan para sin_problema ni para la gravedad máxima.
PRESENCIA = {"contenedor_secos", "contenedor_humedos_lateral",
             "contenedor_humedos_bilateral"}

VERIFICADORES = [m.strip() for m in os.environ.get(
    "VERIFICADORES", "qwen/qwen3-vl-235b-a22b-instruct,moonshotai/kimi-k2.6").split(",") if m.strip()]
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
    restantes = "\n".join(
        f"- {k}: {v['nombre']}" for k, v in categorias.items()
        if k not in _RUBRICA_KEYS and k != "sin_problema" and k not in FOLD)
    return _RUBRICA.replace("{RESTANTES}", restantes)


# Rúbrica detallada por categoría, calibrada contra fotos reales etiquetadas a
# mano. Las claves deben existir en categorias.json.
_RUBRICA_KEYS = {
    "retiro_muebles", "retiro_escombros", "recoleccion", "barrido",
    "retiro_poda", "destape_sumidero", "reparacion_vereda", "nivelacion_tapa",
    "situacion_calle", "manteros", "contenedor_secos",
    "contenedor_humedos_lateral", "contenedor_humedos_bilateral",
    "reparacion_contenedor", "contenedor_desbordado", "vaciado_contenedor",
    "vaciado_cesto", "reparacion_cesto",
}

_RUBRICA = """Sos un verificador experto de reportes de higiene urbana en la vía pública. Mirá la foto adjunta (puede ser de noche/oscura; prestá atención a objetos voluminosos como muebles, estanterías o cajones delante o al lado de un contenedor) y reportá los problemas visibles.

Categorías y criterios (usá SOLO estas claves):

- retiro_muebles: CUALQUIER objeto voluminoso descartado: muebles, electrodomésticos, colchones, puertas, ventanas, estanterías, tablas/tablones/placas de madera o melamina, caños/tubos/hierros/rejas/chatarra (aunque salgan de una refacción), sanitarios, valijas descartadas. Si ves con claridad cualquier objeto voluminoso descartado (incluida una sola tabla de madera), reportalo. Exige un objeto RÍGIDO identificable: el cartón, la ropa/textiles y las bolsas de basura (llenas o vacías, sueltas o apiladas) NUNCA son voluminosos, van a recoleccion (o a retiro_escombros si son la pila densa de obra descrita abajo). NO cuentan la mercadería ni el mobiliario EN USO de un vendedor, ni objetos en uso.
- retiro_escombros: material INERTE Y SUELTO de obra o refacción; el cascote. Reportalo solo ante evidencia CLARA: escombros o cascotes visibles, ladrillos, baldosas/cerámicos rotos, cemento o revoque, arena de obra; bolsas de material de construcción etiquetadas (cemento, cal). Que algo venga de una obra NO lo hace escombros: un OBJETO ENTERO (caños, hierros, rejas, maderas/tablones, puertas, ventanas, sanitarios) es un voluminoso = retiro_muebles, NO escombros. TAMBIÉN: una PILA ORDENADA de muchas bolsas llenas, pesadas y del mismo tipo (bolsas de arpillera apiladas contra una pared o contenedor, con forma tensa de contenido denso) es escombros embolsados; en ese caso NO es recoleccion. NO lo uses por baldes genéricos, pocas bolsas de basura común, muebles, madera de mueble, cartones o basura domiciliaria variada.
- recoleccion: basura DOMICILIARIA suelta en el piso, típicamente alrededor de un contenedor: bolsas de residuos sueltas, cajas de cartón descartadas, papeles desparramados, envoltorios, botellas, envases. Una bolsa o caja sola SÍ cuenta (con gravedad 1-2); NO cuenta una botella suelta o basurita chica entre las hojas (eso es solo barrido). El cartón y la ropa/textiles son basura común, NUNCA voluminosos. Si la basura visible es material de obra es escombros, NO recoleccion. Muebles u objetos voluminosos SOLOS no son recoleccion: exige basura común además.
- barrido: acumulación de material fino y liviano para BARRER, sobre todo hojas secas, ramitas, tierra o polvo, juntada en el cordón o la vereda. Si PREDOMINAN las hojas, reportá barrido aunque haya basurita mezclada (y si esa basura mezclada es grande o abundante, reportá TAMBIÉN recoleccion). No lo uses cuando lo que predomina es basura suelta o bolsas.
- retiro_poda: ramas, troncos o restos de poda/jardinería acumulados.
- destape_sumidero: un sumidero o alcantarilla TAPADO, obstruido o desbordado (NO si solo se ve la rejilla sin problema).
- reparacion_vereda: la vereda claramente ROTA: baldosas partidas, faltantes, levantadas o hundidas, visibles con nitidez. NO si la vereda solo está sucia, mojada, cubierta de hojas o con desgaste normal. Si el hueco es RECTANGULAR con MARCO metálico es nivelacion_tapa, NO reparacion_vereda.
- nivelacion_tapa: una TAPA de empresa de servicio público (agua/luz/gas/teléfono) rota, hundida o FALTANTE: hueco RECTANGULAR con marco o borde METÁLICO prolijo en la vereda o la calle. Señal típica: objetos metidos en el hueco (cajones, tablas, conos, sillas) como advertencia; esos objetos NO son voluminosos descartados, no los reportes como retiro_muebles.
- situacion_calle: una persona claramente viviendo en la calle: alguien durmiendo o instalado con colchón ARMADO como cama, refugio o pertenencias habitadas. NO es un colchón o mueble descartado sin nadie. Una persona parada revolviendo un contenedor junto a colchones/mantas desparramados NO está "instalada"; eso es descarte (retiro_muebles, y recoleccion si hay textiles desparramados en cantidad).
- manteros: un puesto informal con mercadería nueva exhibida para la venta en el piso, sobre una manta, mesa o lona.
- contenedor_secos [PRESENCIA]: se ve un contenedor municipal inequívocamente VERDE (reciclables). Los contenedores negros, grises o gris oscuro NO son secos.
- contenedor_humedos_lateral [PRESENCIA]: se ve un contenedor de húmedos con POSTES o montantes metálicos VERTICALES en los costados (el brazo del camión los toma para izarlo). Suele ser negro o gris oscuro, cuerpo plástico grande redondeado.
- contenedor_humedos_bilateral [PRESENCIA]: se ve un contenedor de húmedos SIN postes metálicos: cuerpo RECTANGULAR de paredes laterales PLANAS y techo abovedado, gris (claro o dos tonos). El discriminador NO es el color sino los POSTES: si el contenedor NO tiene postes verticales metálicos en los costados es BILATERAL, aunque el gris se vea oscuro o sucio; si los tiene es LATERAL. Reportá solo UNO de los dos tipos de húmedos.
- reparacion_contenedor: un contenedor VOLCADO (acostado, dado vuelta) o visiblemente ROTO/vandalizado/quemado (tapa desprendida, pedal roto, cuerpo agrietado). Un contenedor volcado siempre va acá. Un contenedor parado y en buen estado NO.
- contenedor_desbordado: el contenedor mismo REBALSA por su boca, con residuos sobresaliendo por encima. La basura en el piso alrededor NO lo hace desbordado (eso es recoleccion).
- vaciado_contenedor: contenedor lleno que necesita vaciado (residuos visibles hasta la boca), sin llegar a rebalsar.
- vaciado_cesto: un cesto papelero (canasto chico sobre poste) desbordado o lleno.
- reparacion_cesto: TODO problema físico de un cesto papelero: roto, caído, desprendido, colgando, o la base/soporte sin canasto montado. Un cesto sano y en su lugar NO.

Otras categorías posibles (reportalas solo con evidencia clara):
{RESTANTES}

Gravedad por categoría (no aplica a las claves [PRESENCIA]): 1 mínima (apenas presente, incidental) · 2 leve · 3 alta · 4 grave · 5 muy grave. Calibración para recoleccion (sé exigente): 1-2 = una bolsa sola o poca basura aislada; 3 = basura claramente presente pero acotada (algunas cajas y restos junto al contenedor); 4 = mucha basura variada ocupando un área notable; 5 = acumulación masiva cubriendo la vereda.

Reglas finales:
- En "descripcion" contá en 1 o 2 frases qué se ve en la foto: la escena, los objetos principales y su estado, coherente con las categorías que reportás.
- Reportá únicamente lo que se ve con certeza; ante la duda, omití la categoría.
- Una foto puede tener varias categorías (una por problema visible; las claves [PRESENCIA] se reportan siempre que el contenedor se vea, haya problema o no, con gravedad 1).
- Si no hay ningún problema, devolvé sin_problema en true, aunque reportes claves [PRESENCIA] por contenedores visibles sanos: una calle limpia con un contenedor parado y en buen estado sigue siendo sin_problema true. Un contenedor volcado, roto o desbordado sí ES un problema.

Respondé SOLO con JSON válido, sin texto adicional ni markdown:
{"categorias": [{"key": "...", "gravedad": 1-5, "evidencia": "qué se ve, máx 10 palabras"}], "sin_problema": true|false, "descripcion": "1-2 frases sobre qué se ve en la foto"}"""


def _verificar_uno(modelo, data_url, categorias):
    try:
        contenido = _llamar(modelo, [{"role": "user", "content": [
            {"type": "text", "text": _prompt_verificador(categorias)},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}])
        veredicto = _extraer_json(contenido)
        vistas = []
        for c in veredicto.get("categorias", []):
            if not isinstance(c, dict):
                continue
            c["key"] = FOLD.get(c.get("key"), c.get("key"))
            if c["key"] in categorias and c["key"] not in {v["key"] for v in vistas}:
                vistas.append(c)
        return {"modelo": modelo, "ok": True, "categorias": vistas,
                "sin_problema": bool(veredicto.get("sin_problema")),
                "descripcion": str(veredicto.get("descripcion") or "").strip()}
    except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError, OSError) as e:
        return {"modelo": modelo, "ok": False, "error": str(e)[:200]}


def _arbitrar(disputadas, veredictos, probabilidades, categorias, consensuadas):
    """El árbitro (modelo de texto) decide las categorías con una sola fuente.

    En la misma llamada redacta la descripción final consolidada de la foto,
    a partir de las descripciones de los verificadores (sin llamadas extra).
    """
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
        f"Categorías ya confirmadas por consenso: {json.dumps(sorted(consensuadas), ensure_ascii=False)}\n\n"
        f"Categorías en disputa: {json.dumps(sorted(disputadas), ensure_ascii=False)}\n\n"
        "Criterio: confirmá una categoría de un modelo de visión solo si su evidencia citada "
        "es concreta y coherente con lo que reportaron los demás. Si una categoría la reporta "
        "SOLO el modelo local y ninguno de los dos modelos de visión la vio al mirar la foto, "
        "rechazala aunque la probabilidad local sea alta, salvo que la evidencia de los "
        "verificadores describa lo mismo con otras palabras. Categorías que nombran el mismo "
        "objeto físico ya reportado por consenso no deben duplicarse: rechazá la redundante. "
        "Ante la duda, rechazá.\n\n"
        'Además, redactá "descripcion": 1 a 3 frases en español que describan la foto '
        "integrando las descripciones y evidencias de los dos modelos de visión, y que "
        "respalden las categorías confirmadas (las de consenso más las que confirmes acá). "
        "No inventes detalles que ninguna fuente haya mencionado.\n\n"
        "Respondé SOLO con JSON:\n"
        '{"decisiones": [{"key": "...", "veredicto": "confirmar"|"rechazar", "motivo": "..."}], "descripcion": "..."}'
    )
    try:
        contenido = _llamar(ARBITRO, [{"role": "user", "content": prompt}])
        data = _extraer_json(contenido)
        decisiones = [d for d in data.get("decisiones", [])
                      if isinstance(d, dict) and d.get("key") in disputadas]
        return {"modelo": ARBITRO, "ok": True, "decisiones": decisiones,
                "descripcion": str(data.get("descripcion") or "").strip()}
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

    # Un contenedor de húmedos es lateral O bilateral, nunca ambos. Los modelos
    # de visión confunden el subtipo seguido; el modelo local es el experto acá,
    # así que sus votos deciden el subtipo y los votos VLM del otro subtipo
    # cuentan como "hay un contenedor de húmedos".
    grises = {"contenedor_humedos_lateral", "contenedor_humedos_bilateral"}
    vistos = grises & set(fuentes)
    if len(vistos) > 1:
        local_gris = next((p["key"] for p in prediccion_local["predichas"]
                           if p["key"] in grises), None)
        elegido = local_gris or max(vistos, key=lambda k: len(fuentes[k]))
        for otro in vistos - {elegido}:
            for f in fuentes.pop(otro):
                if f not in fuentes[elegido]:
                    fuentes[elegido].append(f)
            if otro in grav:
                grav[elegido] = max(grav.get(elegido, 0), grav.pop(otro))

    activos = [v for v in veredictos if v.get("ok")]
    confirmadas = {k for k, f in fuentes.items() if len(f) >= 2}
    disputadas = {k for k, f in fuentes.items() if len(f) == 1}

    arbitro = None
    en_duda = []
    if disputadas and activos:
        arbitro = _arbitrar(disputadas, activos, prediccion_local["probabilidades"],
                            categorias, confirmadas)
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

    # Descripción final consolidada, siempre sin llamadas extra: la redacta el
    # árbitro si ya intervino; si no, se elige la descripción del verificador
    # que más coincide con las categorías finales (a igual coincidencia, la
    # más detallada).
    descripcion, descripcion_fuente = None, None
    if arbitro and arbitro.get("ok") and arbitro.get("descripcion"):
        descripcion, descripcion_fuente = arbitro["descripcion"], ARBITRO
    else:
        mejor = None
        for v in activos:
            if not v.get("descripcion"):
                continue
            clave = (len({c["key"] for c in v["categorias"]} & confirmadas),
                     len(v["descripcion"]))
            if mejor is None or clave > mejor[0]:
                mejor = (clave, v)
        if mejor:
            descripcion, descripcion_fuente = mejor[1]["descripcion"], mejor[1]["modelo"]

    return {
        "activa": True,
        "verificadores": veredictos,
        "arbitro": arbitro,
        "confirmadas": finales,
        "en_duda": en_duda,
        "descripcion": descripcion,
        "descripcion_fuente": descripcion_fuente,
    }
