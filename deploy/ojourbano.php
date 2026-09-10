<?php
/**
 * Proxy liviano hacia la API local de Ojo Urbano (127.0.0.1:8091).
 * Reenvía el cuerpo crudo (multipart incluido: enable_post_data_reading=0
 * en .user.ini) y devuelve la respuesta tal cual. Siempre manda
 * X-Forwarded-For con la IP real del visitante (pisando lo que el cliente
 * haya mandado), para que los límites por IP de la app funcionen de verdad
 * con CONFIAR_PROXY=1.
 *
 * @package OjoUrbano
 */

// Este intermediario no carga WordPress. Reenvía URI, encabezados y formularios sin aplicar sus filtros.
// phpcs:disable WordPress.WP.AlternativeFunctions, WordPress.WP.GlobalVariablesOverride -- Uso las funciones nativas en este proceso independiente.
// phpcs:disable WordPress.Security.ValidatedSanitizedInput, WordPress.Security.NonceVerification -- La API pública valida el cuerpo recibido; acá conservo sus bytes y no hay sesión de WordPress.
$backend = 'http://127.0.0.1:8091';
$base    = '/ojourbano';

$uri  = parse_url( $_SERVER['REQUEST_URI'], PHP_URL_PATH );
$qs   = isset( $_SERVER['QUERY_STRING'] ) ? $_SERVER['QUERY_STRING'] : '';
$path = substr( $uri, strlen( $base ) );
if ( '' === $path || false === $path ) {
	header( 'Location: ' . $base . '/' );
	exit;
}
$path = rtrim( $path, '/' );
if ( '' === $path ) {
	$path = '/';
}
$destino = $backend . $path . ( '' !== $qs ? '?' . $qs : '' );
header( 'Cache-Control: no-store' );

// La IP real: detrás de Cloudflare viene en CF-Connecting-IP; si no, la del
// socket. NUNCA el X-Forwarded-For del cliente, que es falsificable.
$ip_real = isset( $_SERVER['HTTP_CF_CONNECTING_IP'] )
	? $_SERVER['HTTP_CF_CONNECTING_IP'] : $_SERVER['REMOTE_ADDR'];
$xff     = 'X-Forwarded-For: ' . $ip_real;

$ch = curl_init( $destino );
curl_setopt_array(
	$ch,
	array(
		CURLOPT_CUSTOMREQUEST  => $_SERVER['REQUEST_METHOD'],
		CURLOPT_RETURNTRANSFER => true,
		CURLOPT_HEADER         => true,
		CURLOPT_TIMEOUT        => 280,
		CURLOPT_CONNECTTIMEOUT => 5,
		CURLOPT_HTTPHEADER     => array( $xff ),
	)
);
if ( 'POST' === $_SERVER['REQUEST_METHOD'] ) {
	// PHP convierte modo[] y modo[clave] en arrays al leer multipart.
	// Los rechazo antes de que cURL pueda convertirlos en un valor escalar.
	if ( in_array( $path, array( '/clasificar', '/trabajos' ), true )
		&& ( ( isset( $_POST['modo'] ) && ! is_string( $_POST['modo'] ) )
			|| isset( $_FILES['modo'] ) ) ) {
		http_response_code( 422 );
		header( 'Content-Type: application/json; charset=utf-8' );
		echo '{"detail":"modo debe ser un único valor: bajo, medio o alto."}';
		exit;
	}
	$body = file_get_contents( 'php://input' );
	if ( '' !== $body && false !== $body ) {
		// Cuerpo crudo disponible: reenviarlo tal cual.
		$tipo = isset( $_SERVER['CONTENT_TYPE'] ) ? $_SERVER['CONTENT_TYPE'] : 'application/octet-stream';
		curl_setopt( $ch, CURLOPT_POSTFIELDS, $body );
		curl_setopt(
			$ch,
			CURLOPT_HTTPHEADER,
			array(
				$xff,
				'Content-Type: ' . $tipo,
				'Content-Length: ' . strlen( $body ),
				'Expect:',
			)
		);
	} else {
		// PHP ya consumió el multipart: reconstruirlo desde $_FILES/$_POST.
		$campos = array();
		foreach ( $_POST as $k => $v ) {
			$campos[ $k ] = $v;
		}
		foreach ( $_FILES as $k => $f ) {
			if ( isset( $f['tmp_name'] ) && '' !== $f['tmp_name'] && is_uploaded_file( $f['tmp_name'] ) ) {
				$campos[ $k ] = new CURLFile(
					$f['tmp_name'],
					isset( $f['type'] ) ? $f['type'] : 'application/octet-stream',
					isset( $f['name'] ) ? $f['name'] : 'archivo'
				);
			}
		}
		curl_setopt( $ch, CURLOPT_POSTFIELDS, $campos );
		curl_setopt( $ch, CURLOPT_HTTPHEADER, array( $xff, 'Expect:' ) );
	}
}
$resp = curl_exec( $ch );
if ( false === $resp ) {
	http_response_code( 502 );
	header( 'Content-Type: text/plain; charset=utf-8' );
	// phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- La respuesta es texto plano, no HTML.
	echo 'Ojo Urbano no está disponible en este momento (' . curl_error( $ch ) . ')';
	exit;
}
$hsize = curl_getinfo( $ch, CURLINFO_HEADER_SIZE );
$code  = curl_getinfo( $ch, CURLINFO_HTTP_CODE );
$ctype = curl_getinfo( $ch, CURLINFO_CONTENT_TYPE );
unset( $ch );
http_response_code( $code );
if ( $ctype ) {
	header( 'Content-Type: ' . $ctype );
}
// phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- Reenvío el cuerpo y Content-Type de la API sin modificarlos.
echo substr( $resp, $hsize );
