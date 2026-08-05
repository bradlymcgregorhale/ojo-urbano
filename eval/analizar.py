#!/usr/bin/env python3
"""Regenera TODOS los números publicados a partir de los datos versionados.

    python eval/analizar.py

No llama a ningún modelo ni necesita clave: lee solo lo que está en
eval/datos/. Cualquiera puede correrlo y obtener exactamente las cifras que
aparecen en los comentarios de verificador.py y en los mensajes de commit.
Si un número de esos no sale de acá, es que no está respaldado.

Definiciones (las mismas que el README):
  - F1 MICRO sobre pares (foto, categoría). Las claves PRESENCIA se excluyen:
    marcan que se ve un contenedor, no que haya un problema.
  - Positivos de referencia = lo que la adjudicación marca como visible.
  - Al comparar brazos se usa la INTERSECCIÓN de fotos válidas en todos, para
    que el denominador (y por lo tanto los positivos) sea idéntico.
"""
import json
import math
from pathlib import Path

DATOS = Path(__file__).resolve().parent / "datos"
PRESENCIA = {"contenedor_secos", "contenedor_humedos_lateral",
             "contenedor_humedos_bilateral"}
GPT = "openai/gpt-5-mini"
GEM = "google/gemini-3.5-flash-lite"


def jsonl(nombre):
    p = DATOS / nombre
    return [json.loads(l) for l in p.open()] if p.exists() else []


def cargar(nombre):
    p = DATOS / nombre
    return json.load(p.open()) if p.exists() else None


def claves(fila, modelo):
    """Categorías que reportó un verificador, sin las de PRESENCIA."""
    for v in fila["veredictos"]:
        if v["modelo"] == modelo and v.get("ok"):
            return {c["key"] for c in v["categorias"]} - PRESENCIA
    return None


def prf(tp, fp, fn):
    P = 100 * tp / (tp + fp) if tp + fp else 0.0
    R = 100 * tp / (tp + fn) if tp + fn else 0.0
    F = 2 * P * R / (P + R) if P + R else 0.0
    return P, R, F


def wilson(k, n, z=1.96):
    if not n:
        return (float("nan"),) * 2
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * max(0, c - r), 100 * min(1, c + r)


def fisher_unilateral(k1, n1, k2, n2):
    """P(tratamiento <= k2 observado) bajo independencia. Hipergeométrica."""
    tot, exitos = n1 + n2, k1 + k2
    return sum(math.comb(n1, exitos - i) * math.comb(n2, i)
               for i in range(0, k2 + 1) if 0 <= exitos - i <= n1) / math.comb(tot, exitos)


def binomial_bilateral(b, c):
    n = b + c
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n)


def titulo(t):
    print(f"\n{t}\n" + "=" * len(t))


# ---------------------------------------------------------------- exactitud
titulo("1. Exactitud por rúbrica (acuerdo con un adjudicador ciego)")
print(f"[artefactos: {len(cargar('muestra.json') or [])} en la muestra, "
      f"{len(jsonl('evidencia_congelada.jsonl'))} evidencias congeladas, "
      f"{len(jsonl('consenso_replay.jsonl'))} replays de consenso]")
adj = {x["n"]: x for x in (cargar("adjudicacion.json") or [])}
mue = {x["n"]: x for x in (cargar("muestra.json") or [])}
orig = {f["ident"]: f for f in jsonl("evidencia_congelada.jsonl")}
v1 = {x["n"]: x for x in jsonl("evidencia_rubrica_v1.jsonl")}
v2 = {x["n"]: x for x in jsonl("evidencia_rubrica_v2.jsonl")}

brazos = [("original", lambda n, m: claves(orig[mue[n]["ident"]], m) if mue[n]["ident"] in orig else None),
          ("estricta v1", lambda n, m: claves(v1[n], m) if n in v1 else None),
          ("revisada v2", lambda n, m: claves(v2[n], m) if n in v2 else None)]
comun = [n for n, a in adj.items()
         if not a.get("basura")
         and all(get(n, m) is not None for _, get in brazos for m in (GPT, GEM))]
gold = sum(len(set(adj[n]["visible"]) - PRESENCIA) for n in comun)
print(f"fotos en la intersección: {len(comun)}   positivos de referencia: {gold}")
print(f"\n{'':34s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'prec':>7s} {'recall':>7s} {'F1':>6s}")
for m, nom in ((GPT, "gpt-5-mini"), (GEM, "gemini")):
    for etiq, get in brazos:
        tp = fp = fn = 0
        for n in comun:
            g = set(adj[n]["visible"]) - PRESENCIA
            p = get(n, m)
            tp += len(g & p); fp += len(p - g); fn += len(g - p)
        assert tp + fn == gold, f"denominador roto en {nom}/{etiq}: {tp+fn} != {gold}"
        P, R, F = prf(tp, fp, fn)
        print(f"{nom + ' / ' + etiq:34s} {tp:4d} {fp:4d} {fn:4d} {P:6.1f}% {R:6.1f}% {F:6.1f}")
    print()
print("categorías que dependen de cartelería (detección de gemini):")
for clave in ("retiro_poda", "vehiculo_mal_estacionado"):
    gt = sum(1 for n in comun if clave in (set(adj[n]["visible"]) - PRESENCIA))
    fila = [f"{sum(1 for n in comun if clave in (set(adj[n]['visible']) - PRESENCIA) and clave in get(n, GEM))}/{gt}"
            for _, get in brazos]
    print(f"  {clave:26s} original {fila[0]}   v1 {fila[1]}   v2 {fila[2]}")

# ---------------------------------------------------------------- inyección
titulo("2. Inyección de prompt (config de producción)")
coh = [("original", "inyecciones_original.jsonl"),
       ("estricta v1", "inyecciones_v1.jsonl"),
       ("revisada v2", "inyecciones_v2.jsonl")]
res = {}
for etiq, arch in coh:
    f = jsonl(arch)
    if not f:
        continue
    atk = [x for x in f if x["canal"] != "control"]
    ctl = [x for x in f if x["canal"] == "control"]
    img = [x for x in atk if x["canal"] == "imagen"]
    ctx = [x for x in atk if x["canal"] == "contexto"]
    gem = sum(1 for x in img if x["clave"] in x.get("por_modelo", {}).get(GEM, []))
    res[etiq] = (sum(x["colado_viejo"] for x in atk), len(atk),
                 sum(x["colado_viejo"] for x in img), len(img),
                 sum(x["colado_viejo"] for x in ctx), len(ctx),
                 gem, sum(x["colado_viejo"] for x in ctl), len(ctl))
    print(f"  {etiq:14s} confirmadas {res[etiq][0]}/{res[etiq][1]:<3d} "
          f"imagen {res[etiq][2]}/{res[etiq][3]:<3d} contexto {res[etiq][4]}/{res[etiq][5]:<3d} "
          f"gemini engañado {gem}/{res[etiq][3]:<3d} controles FP {res[etiq][7]}/{res[etiq][8]}")
if "original" in res and "revisada v2" in res:
    a, d = res["original"], res["revisada v2"]
    print(f"\n  canal imagen {a[2]}/{a[3]} -> {d[2]}/{d[3]}   "
          f"Fisher unilateral p={fisher_unilateral(a[2], a[3], d[2], d[3]):.3f}")
    print(f"  total        {a[0]}/{a[1]} -> {d[0]}/{d[1]}   "
          f"Fisher unilateral p={fisher_unilateral(a[0], a[1], d[0], d[1]):.3f}")
    n = d[3]
    print(f"  con 0 coladas en {n} intentos el techo IC95 unilateral sería "
          f"{100 * (1 - 0.05 ** (1 / n)):.1f}%; para bajarlo del 5% harían falta "
          f"{math.ceil(math.log(0.05) / math.log(0.95))} ataques limpios por canal")

# ------------------------------------------------------- votación (pareado)
titulo("3. Votación del árbitro: voto1 vs voto3, pareado e intercalado")
pares = jsonl("votacion_pareada.jsonl")
vistos, u = set(), []
for x in pares:
    if x.get("ident") and x["ident"] not in vistos:
        vistos.add(x["ident"]); u.append(x)
if u:
    print(f"n = {len(u)} fotos pareadas\n")
    print(f"{'endpoint':12s} {'voto1':>8s} {'voto3':>8s} {'b':>4s} {'c':>4s}  McNemar p")
    for k in ("cats", "hay", "grav", "desc"):
        a = sum(x["voto1"][k] for x in u); d = sum(x["voto3"][k] for x in u)
        b = sum(1 for x in u if x["voto1"][k] and not x["voto3"][k])
        c = sum(1 for x in u if x["voto3"][k] and not x["voto1"][k])
        print(f"{k:12s} {100*a/len(u):7.1f}% {100*d/len(u):7.1f}% {b:4d} {c:4d}  "
              f"p={binomial_bilateral(b, c):.4f}")

# ------------------------------------------------------------ consenso VLM
titulo("4. CONSENSO_VLM_SOLO: arbitro vs confirma")
rep = jsonl("consenso_replay.jsonl")
if rep:
    dif = sum(1 for d in rep if d["arbitro"]["problemas"] != d["confirma"]["problemas"])
    bis = [d for d in rep if "arbitro_bis" in d]
    ruido = sum(1 for d in bis if d["arbitro"]["problemas"] != d["arbitro_bis"]["problemas"])
    lo, hi = wilson(dif, len(rep)); rlo, rhi = wilson(ruido, len(bis)) if bis else (0, 0)
    print(f"  difieren las dos reglas : {dif}/{len(rep)} = {100*dif/len(rep):.1f}%  IC95 [{lo:.1f}, {hi:.1f}]")
    print(f"  PISO DE RUIDO (mismo brazo dos veces): {ruido}/{len(bis)} = "
          f"{100*ruido/len(bis):.1f}%  IC95 [{rlo:.1f}, {rhi:.1f}]")
    print("  el ruido es del orden del efecto: el eval no tuvo poder para separarlos")
    lc = sum(d["arbitro"]["llamadas_arbitro"] for d in rep)
    lv = sum(d["confirma"]["llamadas_arbitro"] for d in rep)
    print(f"  llamadas al árbitro: {lc} vs {lv}  (+{(lc-lv)/len(rep):.3f} por foto)")

# ------------------------------------------- ataques que engañaron a los dos
titulo("5. ¿Cuántos ataques engañaron a los DOS verificadores?")
print("Es la única población sobre la que actúa CONSENSO_VLM_SOLO=arbitro.")
for etiq, arch in coh:
    f = jsonl(arch)
    if not f:
        continue
    atk = [x for x in f if x["canal"] != "control"]
    dos = sum(1 for x in atk
              if all(x["clave"] in x.get("por_modelo", {}).get(m, []) for m in (GPT, GEM)))
    n = len(atk)
    techo = 100 * (1 - 0.05 ** (1 / n)) if dos == 0 and n else float("nan")
    print(f"  {etiq:14s} {dos}/{n}" + (f"   techo IC95 unilateral {techo:.1f}%" if dos == 0 else ""))
print("  el mecanismo nunca se ejercitó: 0/n no prueba que la amenaza sea rara")

# --------------------------------------- experimentos de estabilidad (crudos)
titulo("6. Experimentos de estabilidad del árbitro (conteos crudos)")
exp = cargar("experimentos_arbitro.json")
if exp:
    print("  ATENCIÓN: las condiciones en bloque secuencial NO son comparables")
    print("  entre sí. La tasa deriva con el tiempo: ver M32_r1 vs M32_r2, dos")
    print("  corridas IDÉNTICAS. Por eso la comparación válida es la pareada.\n")
    por = {c["id"]: c for c in exp["condiciones"]}
    for c in exp["condiciones"]:
        lo, hi = wilson(c["flips"], c["n"])
        print(f"  {c['id']:12s} {c['flips']:3d}/{c['n']:<4d} {100*c['flips']/c['n']:5.1f}%  "
              f"IC95 [{lo:.1f}, {hi:.1f}]  {c['desc']}")
    def comp(a, b, etiq):
        x, y = por[a], por[b]
        p = fisher_unilateral(x["flips"], x["n"], y["flips"], y["n"])
        print(f"  {etiq}: {100*x['flips']/x['n']:.1f}% -> {100*y['flips']/y['n']:.1f}%  p={p:.3f}"
              f"{'  (confundido con el tiempo)' if x['diseno'] == 'bloque_secuencial' else ''}")
    print()
    comp("temp1_n63", "temp0_n63", "temperatura 1 -> 0")
    comp("A_temp0", "B_pin", "sin pin -> con allow_fallbacks=false")
    comp("A_temp0", "C_denso", "deepseek -> denso (la conclusión que se retractó)")

# ------------------------------------------------------ tamaño de muestra
titulo("7. Tamaño de muestra que haría falta")
def n_mcnemar(p10, p01, alfa=0.05, poder=0.80):
    from math import sqrt
    za, zb = 1.959963985, 0.8416212336
    pd = p10 + p01; pe = p10 - p01
    return math.ceil(((za * math.sqrt(pd) + zb * math.sqrt(pd - pe * pe)) / pe) ** 2)
print("  McNemar pareado, alfa 0.05 bilateral, poder 80%:")
for p10, p01, et in ((0.12, 0.04, "12% -> 4% (discordancia alta)"),
                     (0.10, 0.02, "10% -> 2%"),
                     (0.08, 0.00, "8% -> 0%")):
    print(f"    {et:32s} n≈{n_mcnemar(p10, p01)}")
# El n para la votación se calcula con la discordancia REAL observada, no a ojo.
if u:
    b = sum(1 for x in u if x["voto1"]["cats"] and not x["voto3"]["cats"])
    c = sum(1 for x in u if x["voto3"]["cats"] and not x["voto1"]["cats"])
    p10, p01 = b / len(u), c / len(u)
    if p10 > p01:
        print(f"\n  Votación observada: b={b} c={c} sobre n={len(u)} "
              f"(p10={100*p10:.1f}%, p01={100*p01:.1f}%).")
        print(f"  Para detectar ESE efecto con 80% de poder harían falta "
              f"n≈{n_mcnemar(p10, p01)} fotos pareadas.")
    else:
        print(f"\n  Votación observada: b={b} c={c} sobre n={len(u)}: el efecto "
              "no tiene dirección clara, no hay n que lo resuelva.")

titulo("Limitación que atraviesa todo")
print("La adjudicación la hizo UN solo juez, ciego a lo que dijeron los modelos,")
print("pero es un modelo de visión: puede compartir puntos ciegos con los")
print("verificadores. Todo lo de arriba es ACUERDO CON UN ADJUDICADOR CIEGO,")
print("no exactitud contra verdad humana. Ver eval/adjudicacion_prompt.md.")
