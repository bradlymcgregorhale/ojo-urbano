# Intermediario de Ojo Urbano

`ojourbano.php` es la copia versionada del intermediario que se instala como
`public_html/ojourbano/index.php`. Reenvía al servicio local en el puerto 8091.

Si PHP ya leyó el formulario, rechaza `modo` como lista, objeto o archivo antes
de reconstruir el multipart. Los valores escalares repetidos conservan el último,
como hace PHP. Si el cuerpo llega crudo, lo reenvía intacto para que la API aplique
su validación. El resto de los campos conserva el reenvío existente.

Para verificar ambas rutas sin ejecutar modelos:

```sh
python3 -m unittest tests.test_intermediario -v
```

La prueba necesita PHP con cURL y abre dos servidores locales temporales. Comprueba
ambos endpoints, los modos escalares, el valor omitido, los repetidos y los formatos
no escalares. La validación de la API para cuerpos crudos está en
`tests/test_modos_analisis.py`.

Antes de instalar, compará la huella de la copia vigente y guardá un respaldo en el
directorio privado del servicio. Instalá el archivo de forma atómica y verificá la
página y ambos endpoints. Esta actualización de PHP no necesita reiniciar Python.
Para revertir, restaurá la copia respaldada y repetí esas comprobaciones.

### IP del visitante

El intermediario acepta `CF-Connecting-IP` solamente cuando `REMOTE_ADDR`
pertenece a los rangos oficiales de Cloudflare. Una conexión directa conserva
su IP del socket aunque mande ese encabezado o `X-Forwarded-For`.
Los rangos IPv4 e IPv6 están en `ojo_proxy_client_ip` y se comparan como CIDR.
Antes de cambiar el proveedor o su configuración, comprobar
https://www.cloudflare.com/ips/ y actualizar los rangos si cambiaron.
`CONFIAR_PROXY=1` en Python requiere que la API siga escuchando sólo en loopback.

Prueba del intermediario y de los límites IPv4/IPv6:
`python -m unittest discover -s tests -p test_intermediario.py`.
