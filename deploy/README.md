# Intermediario de Ojo Urbano

`ojourbano.php` es la copia versionada del intermediario que se instala como
`public_html/ojourbano/index.php`. Reenvía al servicio local en el puerto 8091.

Si PHP ya leyó el formulario, rechaza `modo` como lista, objeto o archivo antes
de reconstruir el multipart. Los valores escalares repetidos conservan el último,
como hace PHP. Si el cuerpo llega crudo, lo reenvía intacto para que la API aplique
su validación. El resto de los campos conserva el reenvío existente.

Para verificar ambas rutas sin ejecutar modelos:

```sh
python3 -m unittest test_intermediario -v
```

La prueba necesita PHP con cURL y abre dos servidores locales temporales. Comprueba
ambos endpoints, los modos escalares, el valor omitido, los repetidos y los formatos
no escalares. La validación de la API para cuerpos crudos está en
`test_modos_analisis.py`.

Antes de instalar, compará la huella de la copia vigente y guardá un respaldo en el
directorio privado del servicio. Instalá el archivo de forma atómica y verificá la
página y ambos endpoints. Esta actualización de PHP no necesita reiniciar Python.
Para revertir, restaurá la copia respaldada y repetí esas comprobaciones.
