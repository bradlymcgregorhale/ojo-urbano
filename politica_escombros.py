"""Elegibilidad final del retiro, separada del reconocimiento de material."""
import copy
import re

KEY = "retiro_escombros"
CODIGO = "1462821340520"
CODIGO_OBRA_SERVICIOS = "154014"
DESCRIPCION_CONTEXTUAL = ("El vecino informa escombros en las bolsas de la vía pública; "
                         "el contenido no se confirma sólo por la imagen.")


def es_obra_servicios(entrada):
    return str(entrada.get("codigo", "")) == CODIGO_OBRA_SERVICIOS


def requiere_obra_servicios(salida):
    return bool((salida.get("detalle", {}).get("verificacion") or {}).get("activa")) and any(
        es_obra_servicios(c) for campo in ("problemas", "categorias_contexto", "posibles")
        for c in salida.get(campo) or [])


def aplicar_obra_servicios(salida, revision):
    """No equiparar una denuncia de obras de servicios con retiro de escombros."""
    r = copy.deepcopy(salida)
    r["detalle"]["verificacion"]["contexto_obra_servicios"] = revision
    if not revision.get("aceptado"):
        rechazado = revision.get("estado") == "rechazado" and not revision.get("fallo")
        motivo = ("No se pudo validar el reclamo de obras de servicios públicos."
                  if not rechazado else
                  "El comentario no describe restos de una obra de servicios públicos que impidan el paso por la vereda.")
        quitados = []
        for campo in ("problemas", "categorias_contexto", "posibles"):
            quitados += [c for c in r[campo] if es_obra_servicios(c)]
            r[campo] = [c for c in r[campo] if not es_obra_servicios(c)]
        if quitados:
            entrada = dict(quitados[0])
            # categorias_contexto usa un conteo, no la lista de fuentes que
            # esperan problemas/posibles y su serializador público.
            if not isinstance(entrada.get("fuentes"), list):
                entrada["fuentes"] = ["contexto_vecinal"]
            r["descartados_por_foto"].append(dict(entrada, motivo_descarte=motivo))
            r["posibles"].append(dict(entrada, gravedad=None, origen="contexto_vecinal",
                arbitro="rechazar" if rechazado else None, motivo=motivo))
            # Reconstruir con lo que sigue vigente. La descripción previa
            # puede afirmar la categoría recién retirada como otro hallazgo.
            partes = []
            contextual = any(es_escombros(c) and c.get("origen") == "contexto_vecinal"
                             for c in r["problemas"])
            if contextual:
                partes.append(DESCRIPCION_CONTEXTUAL)
            alcance = r.get("verificacion_escombros") or {}
            if alcance.get("estado") in {"excluido", "indeterminado"}:
                partes.append(alcance["motivo"])
            otros = [c["nombre"] for c in r["problemas"]
                     if not (contextual and es_escombros(c))]
            if otros:
                partes.append("Otros hallazgos: " + "; ".join(otros) + ".")
            if r["categorias_contexto"]:
                partes.append("Otros reclamos del texto: " + "; ".join(
                    c.get("nombre") or c.get("codigo") or c.get("key", "")
                    for c in r["categorias_contexto"]) + ".")
            r["descripcion"] = " ".join(partes + [motivo])
    r["hay_problema"] = bool(r["problemas"])
    r["hay_reclamo"] = bool(r["problemas"] or r["categorias_contexto"])
    r["gravedad_maxima"] = max((c.get("gravedad") or 0 for c in r["problemas"]), default=0) or None
    return r


def es_escombros(entrada):
    return (entrada.get("key") in {KEY, "recoleccion_restos_obra"}
            or str(entrada.get("codigo", "")) == CODIGO)


def requiere_revision(salida, contexto):
    veri = (salida.get("detalle") or {}).get("verificacion") or {}
    if not veri.get("activa"):
        return False
    if any(es_escombros(c) for campo in
           ("problemas", "posibles", "categorias_contexto", "descartados_por_foto")
           for c in salida.get(campo) or []):
        return True
    local = (salida.get("detalle") or {}).get("modelo_local") or {}
    return (any(p.get("key") == KEY and p.get("score", 0) >= .70
                for p in local.get("probabilidades") or [])
            or bool(re.search(r"escombr|cascot|restos?\s+de\s+obra", contexto or "", re.I)))


def _recoleccion_previa(salida):
    """Recupera el consenso que una reclasificación local desplazó."""
    problemas = salida.get('problemas') or []
    candidatos = [c for c in problemas if c.get('key') == 'recoleccion']
    if (not candidatos and any(es_escombros(c) and c.get('reclasificado_por') == 'modelo_local'
                               for c in problemas)
            and any(c.get('key') == 'recoleccion' for c in salida.get('posibles') or [])):
        veri = (salida.get('detalle') or {}).get('verificacion') or {}
        candidatos = [c for c in veri.get('confirmadas') or [] if c.get('key') == 'recoleccion']
    return next((c for c in candidatos if len(
        set(c.get('fuentes') or []) - {'modelo_local', 'contexto_vecinal', 'revision_alcance'}) >= 2), None)


def _basura_publica_visible(salida, revision):
    """Una negativa sobre escombros no borra cartones públicos corroborados."""
    if (revision.get('fallo') or revision.get('afirmacion_explicita')
            or revision.get('estado') == 'excluido'
            or revision.get('material_contradictorio') is not True):
        return False
    revisiones = revision.get('revisiones') or []
    if len({r.get('modelo') for r in revisiones if r.get('modelo')}) < 2:
        return False
    for r in revisiones:
        if (r.get('ubicacion') != 'publica'
                or r.get('afirmacion_vecinal') != 'no_menciona'
                or r.get('presentacion') not in {'bolsas_chicas_o_suelto', 'sin_pila'}
                or r.get('hay_bolsas_opacas_o_parciales') == 'si'
                or r.get('material') not in {'incompatible_visible', 'indeterminado'}):
            return False
        # Un material indeterminado solo es neutro si el revisor descarta
        # una pila candidata. No extrapolar el cartón a otras bolsas cerradas.
        if r.get('material') == 'indeterminado' and r.get('presentacion') != 'sin_pila':
            return False
        if r.get('material') == 'incompatible_visible' and r.get('hay_bolsas_opacas_o_parciales') != 'no':
            return False
    return _recoleccion_previa(salida) is not None


def aplicar(salida, revision, categorias):
    """Aplica el veto después de fusión y ruteo textual, sin crear votos visuales."""
    r = copy.deepcopy(salida)
    veri = r["detalle"]["verificacion"]
    veri["alcance_escombros"] = revision
    apto = revision.get("estado") == "apto"
    contradiccion = revision.get("material_contradictorio") is True
    contextual = apto and not contradiccion and revision.get("contexto_resuelve") is True
    afirmado = revision.get("afirmacion_explicita") is True
    existentes = [c for c in r["problemas"] if es_escombros(c)]
    solo_texto = bool(existentes) and all(
        set(c.get("fuentes") or []) <= {"contexto_vecinal"} for c in existentes)
    uso_contexto = contextual and (not existentes or solo_texto or
                                    revision.get("material_visible_confirmado") is not True)
    retirar = not apto or contradiccion or (solo_texto and not contextual)
    candidato = bool(existentes or afirmado or any(
        es_escombros(c) for campo in ("posibles", "categorias_contexto", "descartados_por_foto")
        for c in salida.get(campo) or []))
    motivo = revision.get("motivo") or "No se pudo validar el alcance del retiro."
    if solo_texto and apto and not contextual:
        motivo = "La foto no confirma escombros y el comentario no resuelve el contenido de estas bolsas."
    if contradiccion and apto:
        motivo = "El contenido visible contradice el retiro de escombros. Hace falta aclarar qué contienen las bolsas."
    retirados = []
    conservar_basura = _basura_publica_visible(salida, revision)
    if retirar and conservar_basura and not any(c.get('key') == 'recoleccion' for c in r['problemas']):
        r['problemas'].append(copy.deepcopy(_recoleccion_previa(salida)))
        r['posibles'] = [c for c in r['posibles'] if c.get('key') != 'recoleccion']
    if retirar:
        retirados = existentes
        r["problemas"] = [c for c in r["problemas"] if not es_escombros(c)]
        r["categorias_contexto"] = [c for c in r["categorias_contexto"] if not es_escombros(c)]
        r["posibles"] = [c for c in r["posibles"] if not es_escombros(c)]
        if candidato:
            r["posibles"].append({
                "key": KEY, "nombre": categorias[KEY]["nombre"], "gravedad": None,
                "fuentes": ["revision_alcance"], "origen": "foto",
                "arbitro": "rechazar" if revision.get("estado") == "excluido" else None,
                "motivo": motivo})
    elif contextual:
        if uso_contexto:
            # El contenido lo informó una persona. Los revisores de alcance
            # no se cuentan como testigos visuales del material oculto.
            r["problemas"] = [c for c in r["problemas"] if not es_escombros(c)]
            r["problemas"].append({
                "key": KEY, "nombre": categorias[KEY]["nombre"],
                "gravedad": max((c.get("gravedad") or 2 for c in existentes), default=2),
                "fuentes": ["contexto_vecinal"], "origen": "contexto_vecinal",
                "respaldo_visual": "compatible"})
        r["posibles"] = [c for c in r["posibles"] if not es_escombros(c)]
        r["categorias_contexto"] = [c for c in r["categorias_contexto"] if not es_escombros(c)]
        r["descartados_por_foto"] = [c for c in r["descartados_por_foto"] if not es_escombros(c)]
        reclamos_originales = list(salida.get("categorias_contexto") or []) + list(
            veri.get("por_contexto") or [])
        if not r["categorias_contexto"] and (
                salida.get("foto_valida") is not False or
                (reclamos_originales and all(es_escombros(c) for c in reclamos_originales))):
            r["foto_valida"], r["foto_valida_estado"] = True, "corresponde"

    # No mandar un camión de basura por las mismas bolsas excluidas o por
    # las que el vecino identificó como escombros. Conservar otra basura
    # pública únicamente cuando la revisión identifica residuos aparte.
    if contextual or (retirar and candidato) or revision.get("estado") == "excluido":
        quitar = {"recoleccion"} if (revision.get("basura_independiente") is not True
                                     and not conservar_basura) else set()
        quitar.update({"retiro_muebles", "retiro_poda"} - set(
            revision.get("otros_retiros_independientes") or []))
        retirados += [c for c in r["problemas"] if c.get("key") in quitar]
        for campo in ("problemas", "categorias_contexto", "posibles"):
            r[campo] = [c for c in r[campo] if c.get("key") not in quitar]
    if retirar or contextual:
        r["en_duda"] = [k for k in r["en_duda"] if k != KEY]
        if retirar and revision.get("estado") != "excluido" and any(
                es_escombros(c) for c in r["posibles"]):
            r["en_duda"].append(KEY)
            veri.setdefault("fuentes_en_duda", {})[KEY] = ["revision_alcance"]
        for c in retirados:
            r["descartados_por_foto"].append(dict(c, motivo_descarte=motivo))
        # No conservar una descripción consolidada que niegue el material
        # contextual o afirme un retiro que acaba de quedar excluido.
        otros = [c["nombre"] for c in r["problemas"] if not es_escombros(c)]
        nota = ("Se observan residuos comunes en la vía pública; no se confirmaron escombros."
                if conservar_basura else
                DESCRIPCION_CONTEXTUAL if uso_contexto else
                "Se observan escombros en bolsas chicas o sueltos en la vía pública."
                if contextual else motivo)
        if contextual or retirados or any(es_escombros(c) for c in r["posibles"]):
            r["descripcion"] = nota + (" Otros hallazgos: " + "; ".join(otros) + "." if otros else "")
    r["verificacion_escombros"] = {
        "estado": revision.get("estado", "indeterminado"),
        "motivo": motivo, "basado_en_contexto": uso_contexto,
        "requiere_nueva_foto": not apto}
    r["hay_problema"] = bool(r["problemas"])
    r["hay_reclamo"] = bool(r["problemas"] or r["categorias_contexto"])
    r["gravedad_maxima"] = max((c.get("gravedad") or 0 for c in r["problemas"]), default=0) or None
    return r
