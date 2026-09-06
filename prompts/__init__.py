"""Textos de análisis, cargados una vez al importar el verificador."""
from pathlib import Path

_DIRECTORIO = Path(__file__).resolve().parent


def cargar_prompt(nombre):
    """Lee UTF-8; una barra al final de línea continúa el texto sin un salto."""
    texto = (_DIRECTORIO / (nombre + '.txt')).read_text(encoding='utf-8')
    return texto.removesuffix('\n').replace('\\\n', '')


REGLA_SUBTIPO_HUMEDOS = cargar_prompt('compartidos/subtipo_humedos')

RUBRICA_CATEGORIAS = (
    'retiro_muebles',
    'retiro_escombros',
    'recoleccion',
    'barrido',
    'retiro_poda',
    'destape_sumidero',
    'reparacion_vereda',
    'tapa_vereda',
    'tapa_calle',
    'situacion_calle',
    'manteros',
    'ocupacion_comercial',
    'obstruccion',
    'contenedor_secos',
    'contenedor_humedos_lateral',
    'contenedor_humedos_bilateral',
    'reparacion_contenedor',
    'reposicion_contenedor',
    'lavado_contenedor',
    'vehiculo_mal_estacionado',
    'columna_poste_cable',
    'puesto_diarios',
    'puesto_flores',
    'volquete_mal_dispuesto',
    'luminaria_apagada',
    'desratizacion',
    'contenedor_desbordado',
    'vaciado_contenedor',
    'vaciado_cesto',
    'reparacion_cesto',
    'lavado_cesto',
    'hidrolavado_grafitis',
    'vehiculo_abandonado',
    'reparacion_bache',
    'reparacion_cordon',
    'retiro_afiches',
    'plantacion_arbol',
    'poda_arbol',
    'problemas_arbolado',
    'ocupacion_gastronomica',
    'residuos_establecimiento',
    'acopio_recuperadores',
    'mayor_iluminacion',
)

_RUBRICA_KEYS = set(RUBRICA_CATEGORIAS)

_RUBRICA = (
    cargar_prompt('rubrica/general')
    .replace('{{CATEGORIAS}}', ''.join(
        cargar_prompt(f'rubrica/categorias/{key}')
        for key in RUBRICA_CATEGORIAS))
    .replace('{{REGLA_SUBTIPO_HUMEDOS}}', REGLA_SUBTIPO_HUMEDOS)
)

_PROMPT_PATENTE = cargar_prompt('dirigidos/patente')

_PROMPT_ALCANCE_ESCOMBROS = cargar_prompt('dirigidos/alcance_escombros')
_PROMPT_OBRA_SERVICIOS_CONTEXTO = cargar_prompt('dirigidos/obra_servicios_contexto')

_PROMPT_SEGUNDA_MIRADA = cargar_prompt('segundas_miradas/escombros')

_PROMPT_SEGUNDA_MIRADA_BASE = cargar_prompt('segundas_miradas/base')
_PROMPT_RELACION_CONTENEDOR = cargar_prompt('segundas_miradas/relacion_contenedor')

_PROMPT_SEGUNDA_MIRADA_DANO = cargar_prompt('segundas_miradas/dano')

_PROMPT_SEGUNDA_MIRADA_POSTES = cargar_prompt('segundas_miradas/postes')

_PROMPT_SEGUNDA_MIRADA_VOLCADO = cargar_prompt('segundas_miradas/volcado')

_PROMPT_SEGUNDA_MIRADA_SUBTIPO = cargar_prompt('segundas_miradas/subtipo').replace(
    '{{REGLA_SUBTIPO_HUMEDOS}}', REGLA_SUBTIPO_HUMEDOS)

_PROMPT_REPREGUNTA = cargar_prompt('dirigidos/repregunta')

_PROMPT_REPREGUNTA_ESTADO = cargar_prompt('dirigidos/repregunta_estado')

_CONTRASTE_CONTENEDOR_SECOS = cargar_prompt('dirigidos/contraste_contenedor_secos')

_PROMPT_PREGUNTA_ABIERTA = cargar_prompt('dirigidos/pregunta_abierta')

_PROMPT_SEGUNDA_MIRADA_VOLUMINOSO = cargar_prompt('segundas_miradas/voluminoso')

_PROMPT_SEGUNDA_MIRADA_DESBORDE = cargar_prompt('segundas_miradas/desborde')
_CONTRASTE_CUERPO_DESTRUIDO = cargar_prompt('dirigidos/contraste_cuerpo_destruido')

_PROMPT_SEGUNDA_MIRADA_PRESENCIA = cargar_prompt('segundas_miradas/presencia')

_PROMPT_SEGUNDA_MIRADA_PRESENCIA_CLAVE = cargar_prompt('segundas_miradas/presencia_clave')

_SISTEMA_ARBITRO_TEXTO = cargar_prompt('arbitro/sistema_texto')

_SISTEMA_ARBITRO_FOTO = _SISTEMA_ARBITRO_TEXTO.replace(
    "analizaron la misma foto (vos no la ves)",
    "analizaron la misma foto, y VOS LA TENÉS ADJUNTA: mirala vos"
) + cargar_prompt('arbitro/instrucciones_foto')

_PROMPT_USUARIO = cargar_prompt('primera_pasada/usuario')

_PROMPT_USUARIO_CONTEXTO = cargar_prompt('primera_pasada/contexto')

_PROMPT_USUARIO_PRESTACIONES = cargar_prompt('primera_pasada/prestaciones')

_ARBITRO_DATOS = cargar_prompt('arbitro/datos')

_ARBITRO_DESCRIPCION = cargar_prompt('arbitro/descripcion')

_ARBITRO_CONTEXTO = cargar_prompt('arbitro/contexto')

_ARBITRO_SUGESTION = cargar_prompt('arbitro/sugestion')

_ARBITRO_SUBTIPOS = cargar_prompt('arbitro/subtipos')

_ARBITRO_DISPUTAS = cargar_prompt('arbitro/disputas')

_ARBITRO_SOLO_VISION = cargar_prompt('arbitro/solo_vision')

_ARBITRO_UNA_FUENTE = cargar_prompt('arbitro/una_fuente')

_ARBITRO_ESCOMBROS = cargar_prompt('arbitro/escombros')

_ARBITRO_ESCOMBROS_FOTO = cargar_prompt('arbitro/escombros_foto')

_CONTEXTO_SISTEMA = cargar_prompt('contexto/sistema')

_CONTEXTO_USUARIO = cargar_prompt('contexto/usuario')
