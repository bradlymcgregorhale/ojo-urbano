"""Compatibilidad entre una foto y un pedido de ocupación de la vereda."""

import re
import unicodedata


_TIPO = "ocupacion_comercial"
_CODIGO = "118001"
_NOMBRE = "Ocupación indebida de la vereda/calzada por local comercial"
_EXPLICITO = re.compile(r"\bocupacion\s+(?:indebida\s+)?(?:comercial|por (?:un )?local comercial)\b")
_GENERICO = re.compile(r"\b(?:ocupacion(?:\s+indebida)?|uso\s+indebido)"
                       r"\s+(?:de(?:l|\s+la)?\s+)?(?:espacio\s+pu(?:blico|bilco)|vereda)\b")
_OTRO_PEDIDO = re.compile(r"\b(?:gastronom\w*|ambulantes?|manteros?|motos?|vehiculos?|"
                           r"mal estacionad\w*|retiro de|retirar|levantar)\b")
_NEGACION = re.compile(r"\b(?:no (?:es|hay|denuncio)|sin)\s+(?:una?\s+)?$")
_CONTENEDORES = {"contenedor_desbordado", "vaciado_contenedor", "reparacion_contenedor",
                 "reposicion_contenedor", "contenedor_secos", "contenedor_humedos_lateral",
                 "contenedor_humedos_bilateral", "contenedor_sobrecargado"}
_RESIDUOS_DE_CONTENEDOR = _CONTENEDORES | {"recoleccion"}


def _pide_ocupacion(contexto):
    texto = unicodedata.normalize("NFKD", contexto.casefold())
    texto = "".join(letra for letra in texto if not unicodedata.combining(letra))
    pedido = _EXPLICITO.search(texto) or _GENERICO.search(texto)
    return (pedido is not None and not _NEGACION.search(texto[:pedido.start()])
            and not _OTRO_PEDIDO.search(texto))


def aplicar(publica, contexto):
    """Propone el servicio del vecino según lo que la foto permite verificar."""
    if not _pide_ocupacion(contexto):
        return

    problemas = {c.get("key") for c in publica.get("problemas", [])}
    elementos = {c.get("key") for c in publica.get("elementos_detectados", [])}
    respaldos = {c.get("respaldo_visual") for c in publica.get("categorias_contexto", [])
                 if c.get("key") == _TIPO or str(c.get("codigo")) == _CODIGO}
    foto_rechazada = (publica.get("evaluacion_foto") or {}).get("rechazada")
    evidencia_compatible = (_TIPO in problemas or "compatible" in respaldos
                            or "retiro_muebles" in problemas)
    solo_contenedor = (bool((problemas | elementos) & _CONTENEDORES)
                       and problemas <= _RESIDUOS_DE_CONTENEDOR)
    if foto_rechazada:
        estado, motivo = "sin_evidencia", "La foto no permite ver bien el lugar del reclamo."
    elif publica.get("foto_valida") is False or ("contradice" in respaldos and _TIPO not in problemas):
        estado, motivo = "no_corresponde", "La foto no coincide con el pedido de ocupación de la vereda."
    elif solo_contenedor:
        estado, motivo = "no_corresponde", "La foto muestra un contenedor. No se ve la ocupación reportada."
    elif evidencia_compatible:
        estado = "compatible"
        motivo = "Lo que se ve en la vereda es compatible con el pedido de ocupación."
    else:
        estado, motivo = "sin_evidencia", "En la foto no se alcanza a ver la ocupación de la vereda."

    publica["reclamo_foto"] = {"key": _TIPO, "codigo": _CODIGO, "estado": estado, "motivo": motivo}
    if not respaldos and _TIPO not in problemas:
        categoria = {
            "key": _TIPO, "nombre": _NOMBRE,
            "respaldo_visual": {"compatible": "compatible", "no_corresponde": "contradice",
                                "sin_evidencia": "neutral"}[estado],
            "fuentes": 0,
        }
        publica["categorias_contexto"] = [*(publica.get("categorias_contexto") or []), categoria]
    publica["hay_reclamo"] = True
    compatible = estado == "compatible"
    publica["solicitudes_sugeridas"] = {
        "estado": "propuesta" if compatible else "requiere_contexto",
        "servicios": [_TIPO] if compatible else [],
        "candidatos": [_TIPO],
        "requiere_confirmacion": True,
        "motivo": motivo,
    }
    publica["conclusion"] = {
        "estado": "por_texto",
        "categorias": [{"key": _TIPO, "nombre": _NOMBRE}],
        "requiere_revision": not compatible or _TIPO not in problemas,
        "texto": motivo,
    }
