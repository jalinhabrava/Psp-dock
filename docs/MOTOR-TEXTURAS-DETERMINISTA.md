# Motor determinista y reutilizable de texturas PSP

## Objetivo

No utilizar un LLM ni instrucciones específicas de un juego **en tiempo de
ejecución**. Cada función de descompresión, representación gráfica o
verificación se desarrolla y valida una única vez por **formato/versión**.
Un juego nuevo que utilice un formato ya admitido se procesa sin intervención
de un LLM. Los formatos desconocidos reciben un diagnóstico inequívoco,
nunca se adivinan por nombre o extensión.

## Capas de procesamiento

1. **Descubrimiento:** inventario de ISO9660/CSO o recursos individuales;
   el detector usa bytes de cabecera y no confía en la extensión. Guarda
   familia, estado, posible longitud y los primeros bytes como huella opaca.
   Las coincidencias de prefijo **no son** prueba de igual algoritmo.
2. **Descompresión:** códecs deterministas incluidos: identidad, zlib,
   gzip y raw deflate seleccionado explícitamente. Salida con límite,
   longitud original **exacta**, sin basura al final. Cada códec se valida
   con referencias y SHA256, sin ejecutar recursos o módulos de juegos.
3. **Interpretación GPU:** decodificación de rgba8888, rgb565,
   rgba5551, rgba4444, clut4, clut8 (con formato y contenido de
   paleta conocidos) y reordenación de bloques PSP de 16x8 bytes.
   Requiere dimensiones, buffer width, swizzle y paleta correctos:
   **no se infieren automáticamente**.
4. **Compatibilidad PPSSPP:** calculamos la parte de hash de contenido
   XXH64 de los bytes originales de RAM PSP cuando se conocen todos
   sus parámetros. Para usar el identificador de reemplazo completo
   también hacen falta cachekey, CLUT y, según configuración, dirección.
   Sin coincidencia comprobada no se publica un paquete.
5. **Generación HD:** solo después de validar la asociación con el
   identificador de PPSSPP se permite el procesado por lotes del pack.
   La PR #3 ya dispone de un procesador de dumps PNG capturados por PPSSPP.

El módulo psp_texture_engine.py contiene esta arquitectura; es una biblioteca
local sin consultas de red, dependencias LLM, evaluaciones dinámicas ni
escritura a la PSP. Pillow es opcional para decodificar PNG/JPEG; los
formatos de píxel GE se convierten mediante la biblioteca estándar.

## Qué significa realmente DZIP

La ISO local de Dariusburst PSP ULJM05558 (1.01) contiene:

| Formato | Archivos con cabecera DZIP |
| --- | ---: |
| TX2 (imágenes/HUD) | 27 |
| XXI (escenas) | 805 |
| BXN (partículas) | 123 |
| **Total** | **955** |

El inventario genérico, **sin reglas por DISC_ID**, reconoce todos los 955.
También detecta dos imágenes PNG independientes. Los 955 contenedores
presentan nueve **prefijos de cuatro bytes distintos tras la cabecera**,
repartidos en 3 patrones TX2, 4 XXI y 2 BXN. Estas huellas no tienen
semántica confirmada (podrían depender del contenido, no de la versión).
Se registran sin llamarles variantes de compresión verificadas.

**DZIP no es un estándar universal.** Los archivos Dzip de Marmalade SDK
utilizan una firma DTRZ y otro tipo de archivo, y el programa dzip para
archivos .dz tampoco debe asumirse compatible. La coincidencia del
nombre de una herramienta no prueba compatibilidad. El motor clasifica
DZIP y DTRZ por separado y rechaza descomprimir DZIP hasta que se
reproduzca su bitstream real.

Como pista para ampliar familias de juegos, Dariusburst Chronicle Saviours
en PC también distribuye archivos TX2, XXI y BXN. Coincidir en nombres
de formatos sugiere recursos relacionados, **no demuestra** que compartan
los mismos encabezados ni códecs. Se necesitarán muestras legales y
verificación bit a bit antes de soportar otras ediciones/plataformas.

## Añadir un decodificador una sola vez

1. Registrar familia, cabecera y estructura del recurso con evidencia real.
   Comprobar que no es otro formato homónimo.
2. Reproducir el códec en código puro con límites de RAM, tamaño de salida
   y tiempo de ejecución. Aislarlo de archivos, USB y procesos del sistema.
3. Crear **al menos dos muestras independientes** de entrada y resultado
   exacto (SHA256) y validar salidas completas y truncamientos. Para
   certificar compatibilidad entre juegos, probar también recursos de **dos
   títulos diferentes**; muestras de un solo juego no bastan.
4. Probar separado el decodificador de imágenes o texeles; comprobar
   dimensiones y transparencia contra capturas reales de PPSSPP.
5. Demostrar que la porción de hash y la clave PPSSPP completa coinciden,
   sin asumir CLUT y sin colisiones entre variantes.
6. Solo después añadir el códec al registro estático y dejar que
   el detector lo aplique automáticamente a cualquier título compatible.

El LLM puede ayudar al desarrollador a construir y auditar el códec nuevo,
**no participa** en la aplicación instalada. Si un formato sigue siendo
desconocido, PSP Dock lo clasifica y continúa con los formatos conocidos.

## Limitaciones actuales

- **Todavía no hay decodificador DZIP demostrado.** El resultado real es
  detección universal por firma y análisis, no extracción de sus imágenes.
- Los formatos TX2/XXI/BXN no están decodificados. El número de archivos
  no equivale a número de texturas.
- CLUT4/8 soportan tablas explícitas, pero no se deducen automáticamente
  tablas de índices y parámetros CLUT del juego. No hay soporte DXT
  ni interpretación completa de los recursos propietarios.
- Los hashes parciales y las coincidencias RGB no autorizan por sí solos
  reemplazos PPSSPP. El generador original sigue funcionando con los
  PNG capturados por PPSSPP.
- Las pruebas son sintéticas y el detector se ha verificado con la ISO
  local de Dariusburst. No se ha probado otro juego con una muestra DZIP
  genuina y un resultado descomprimido conocido.

## Tests

Ejecutar desde el repositorio:

    python -m unittest tests.test_psp_texture_engine tests.test_psp_dariusburst_re tests.test_ppsspp_texture_hash -v

Incluye dos juegos sintéticos con recursos de la misma familia y diferentes
prefijos, códecs zlib/gzip/deflate con longitud verificada y rechazo de
basura/bombas, prueba de RGBA/565/4444/5551, CLUT4/8, swizzle,
recursos comprimidos que contienen PNG, y escaneo ISO genérico.

Fuentes para investigación posterior:

- https://www.ppsspp.org/docs/reference/texture-replacement/
- https://github.com/hrydgard/ppsspp/blob/master/GPU/Common/TextureReplacer.cpp
- https://steamdb.info/depot/377871/
- https://github.com/knot126/Marmalade-Modding/blob/trunk/docs/Dzip%20format.md
