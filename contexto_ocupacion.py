"""Compatibilidad entre una foto y un pedido de ocupación de la vereda."""

import re
import unicodedata


_TIPO = "ocupacion_comercial"
_OBSTRUCCION = "obstruccion"
_SERVICIOS = {
    _TIPO: ("118001", "Ocupación indebida de la vereda/calzada por local comercial"),
    _OBSTRUCCION: ("118020", "Obstrucción de calle / vereda"),
}
_EXPLICITO = re.compile(r"\bocupacion\s+(?:indebida\s+)?(?:comercial|(?:por (?:un )?)?local(?: comercial)?)\b")
_GENERICO = re.compile(r"\b(?:ocupacion(?:\s+indebida)?|uso\s+indebido)"
                       r"\s+(?:de(?:l|\s+la)?\s+)?(?:espacio\s+pu(?:blico|bilco)|vereda)\b")
_OTRO_PEDIDO = re.compile(r"\b(?:gastronom\w*|ambulantes?|manteros?|motos?|vehiculos?|"
                           r"mal estacionad\w*|retiro de|retirar|levantar)\b")
_NEGACION = re.compile(r"\b(?:no (?:es|hay|denuncio)|sin)\s+(?:una?\s+)?$")
_DENTRO_DEL_LOCAL = re.compile(
    r"\b(?P<negado>(?:no|ni)\s+(?:(?:estan?|quedan?|se (?:ven?|encuentran?))\s+)?(?:en el )?)?"
    r"(?:(?:adentro|dentro|en el interior) (?:del local|de la vidriera)|"
    r"detras (?:del vidrio|de la vidriera)|en la vidriera|mostrador del local)\b|^vidriera$")
_CONTENEDORES = {"contenedor_desbordado", "vaciado_contenedor", "reparacion_contenedor",
                 "reposicion_contenedor", "contenedor_secos", "contenedor_humedos_lateral",
                 "contenedor_humedos_bilateral", "contenedor_sobrecargado"}
_RESIDUOS_DE_CONTENEDOR = _CONTENEDORES | {"recoleccion"}


def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(letra for letra in texto if not unicodedata.combining(letra))


def mercaderia_visible(valor):
    if not isinstance(valor, str) or not valor.strip():
        return None
    nombre = " ".join(valor.split())[:180]
    if re.match(r"^(?:no\b|sin\b|ningun\w*\b|null\b|indeterminad\w*\b|"
                r"(?:pizarra|cartel) de precios\b|cajones de apoyo\b)", _normalizar(nombre)):
        return None
    return nombre


def mercaderia_en_interior(*descripciones):
    return any(not ubicacion.group("negado")
               for texto in descripciones if isinstance(texto, str)
               for ubicacion in _DENTRO_DEL_LOCAL.finditer(_normalizar(texto.strip())))


def _pide_ocupacion(contexto):
    texto = _normalizar(contexto)
    pedido = _EXPLICITO.search(texto) or _GENERICO.search(texto)
    return (pedido is not None and not _NEGACION.search(texto[:pedido.start()])
            and not _OTRO_PEDIDO.search(texto))


def aplicar(publica, contexto):
    """Propone el servicio del vecino según lo que la foto permite verificar."""
    if not _pide_ocupacion(contexto):
        return

    problemas = {c.get("key") for c in publica.get("problemas", [])}
    barrera_para_el_pedido = publica.get("foto_valida") is False and any(
        c.get("key") == _OBSTRUCCION and (c.get("fuentes") or 0) >= 2
        and (c.get("motivo_descarte") or "").startswith("la foto no corresponde")
        for c in publica.get("descartados_por_foto", [])
    )
    elementos = {c.get("key") for c in publica.get("elementos_detectados", [])}
    respaldos = {c.get("respaldo_visual") for c in publica.get("categorias_contexto", [])
                 if c.get("key") == _TIPO or str(c.get("codigo")) == _SERVICIOS[_TIPO][0]}
    foto_rechazada = (publica.get("evaluacion_foto") or {}).get("rechazada")
    indicio_de_venta = any(
        c.get("key") == _TIPO and not c.get("anulada_por")
        and mercaderia_visible(c.get("mercaderia_visible"))
        for modelo in publica.get("modelos", []) for c in modelo.get("categorias", [])
    )
    solo_contenedor = (bool((problemas | elementos) & _CONTENEDORES)
                       and problemas <= _RESIDUOS_DE_CONTENEDOR)
    servicios = []
    if foto_rechazada:
        estado, motivo = "sin_evidencia", "La foto no permite ver bien el lugar del reclamo."
    elif problemas & _SERVICIOS.keys() or barrera_para_el_pedido:
        servicios = [key for key in _SERVICIOS if key in problemas
                     or (key == _OBSTRUCCION and barrera_para_el_pedido)]
        estado, motivo = "compatible", "La foto muestra qué ocupa la vereda."
    elif publica.get("foto_valida") is False or ("contradice" in respaldos and _TIPO not in problemas):
        estado, motivo = "no_corresponde", "La foto no coincide con el pedido de ocupación de la vereda."
    elif solo_contenedor:
        estado, motivo = "no_corresponde", "La foto muestra un contenedor. No se ve la ocupación reportada."
    elif indicio_de_venta:
        servicios = [_TIPO]
        estado = "compatible"
        motivo = "Lo que se ve en la vereda es compatible con el pedido de ocupación."
    else:
        estado, motivo = "sin_evidencia", "En la foto no se alcanza a ver la ocupación de la vereda."

    servicio = servicios[0] if servicios else _TIPO
    codigo, nombre = _SERVICIOS[servicio]
    publica["reclamo_foto"] = {
        "key": servicio, "codigo": codigo, "servicios": servicios, "estado": estado, "motivo": motivo,
    }
    if servicio not in problemas and not any(
        c.get("key") == servicio or str(c.get("codigo")) == codigo
        for c in publica.get("categorias_contexto", [])
    ):
        categoria = {
            "key": servicio, "nombre": nombre,
            "respaldo_visual": {"compatible": "compatible", "no_corresponde": "contradice",
                                "sin_evidencia": "neutral"}[estado],
            "fuentes": 0,
        }
        publica["categorias_contexto"] = [*(publica.get("categorias_contexto") or []), categoria]
    publica["hay_reclamo"] = True
    compatible = bool(servicios)
    publica["solicitudes_sugeridas"] = {
        "estado": "propuesta" if compatible else "requiere_contexto",
        "servicios": servicios,
        "candidatos": servicios or [_TIPO],
        "requiere_confirmacion": True,
        "motivo": motivo,
    }
    publica["conclusion"] = {
        "estado": "por_texto",
        "categorias": [{"key": key, "nombre": _SERVICIOS[key][1]} for key in servicios or [_TIPO]],
        "requiere_revision": not compatible or any(key not in problemas for key in servicios),
        "texto": motivo,
    }
