# Ingeniería inversa de texturas — Dariusburst PSP

Investigación iniciada sobre el juego PSP **Dariusburst (Japón), DISC_ID
ULJM05558, DISC_VERSION 1.01**, a partir de una copia verificada en el PC.
El identificador NPJH50053SAVE es un **guardado**, no el DISC_ID del juego.
Ninguna ISO, partida o dato de la PSP ha sido modificado.

## Resultados comprobados sobre la ISO local (1 de noviembre de 2026)

| Grupo/formato | Archivos | Observación |
| --- | ---: | --- |
| PSP_GAME/USRDIR/IMAGES/*.TX2 | 27 | Recursos gráficos de menú, ilustraciones, HUD y fuentes |
| PSP_GAME/USRDIR/SCENES/**/*.XXI | 805 | Recursos de escenas, enemigos y movimientos; **no equivale a 805 texturas distintas** |
| PSP_GAME/USRDIR/PARTICLES/**/*.BXN | 123 | Recursos de partículas, no necesariamente mapas de textura independientes |
| Otros archivos ISO | 47 | Ejecutables, sonido, metadatos y artwork |
| **Total ISO** | **1.002** | ISO de 193.789.952 bytes |

Los **955 archivos TX2/XXI/BXN** comienzan por la firma ASCII DZIP.
Los bytes 4..7 son un entero little-endian que parece declarar el tamaño
descomprimido. La suma de esos tamaños es **70.911.616 bytes**. El analizador
comprueba límites, recoge SHA256 de la fuente y de cada recurso comprimido,
offsets ISO9660, tamaño y extensión en un informe que contiene **solo
metadatos**. Esta cifra NO demuestra que el contenido esté descifrado.

Indicaciones de formato: ILLUST01.TX2 declara 263.208 bytes
(262.144 + 1.064), compatible en tamaño con un mapa de 512×512 texeles
de 8 bits más una posible cabecera y paleta. FONT.TX2 declara 16.424
(16.384 + 40). HUDALL01.TX2 declara 17.448 (16.384 + 1.064).
Estas **son hipótesis de tamaños**, NO una decodificación confirmada:
faltan las estructuras de la cabecera TX2, orden de texeles, compresión
y tratamiento de CLUT.

No se encontraron imágenes de juego PNG/JPEG directamente utilizables
en USRDIR: la vía genérica de búsqueda PNG de la PR anterior no puede
descubrir por sí sola los assets internos de Dariusburst.

## Qué implementa esta PR

- psp_dariusburst_re.py: inventario read-only de ISO9660/CSO v1 validado
  por PARAM.SFO, encabezados DZIP, agrupación, SHA256 y hipótesis
  de tamaños sin revelar recursos propietarios ni fabricar hashes.
- ppsspp_texture_hash.py: implementación Python sin dependencias de
  XXH64, con semilla **0xBACD7814**, y hash de contenido de PPSSPP
  para datos **originales de RAM de PSP** con ancho, stride y
  formato conocidos. Se valida con vectores XXH64 estándar.
  Para construir un nombre de reemplazo completo siguen faltando
  **CLUT/paleta**, contexto y, si aplica, dirección de memoria.
- Desde XMB > Gráficos > Dariusburst > Generador HD híbrido:
  Investigar Dariusburst · ISO y DZIP ejecuta la lectura offline,
  guarda un informe JSON de metadatos en el almacenamiento privado
  del PC y muestra la cifra de recursos DZIP.
- Comparar capturas de Dariusburst lee el subdirectorio new que
  PPSSPP rellenará durante las partidas con captura activada. Dos
  solicitudes sucesivas comparan nombres hash+mip y **SHA256 de píxeles
  RGBA decodificados** para medir estabilidad, cambios y nuevas capturas.
  Son trabajos separados del renderizado y no se inician juegos.
  **Dos inventarios idénticos no demuestran dos sesiones de juego distintas**:
  PPSSPP puede conservar los archivos de la primera partida. Las cifras son
  repeticiones observadas en disco, no cobertura validada del juego completo.
- Las coincidencias exploratorias entre píxeles externos y PNG volcados
  quedan marcadas como **sin procedencia verificada**; ninguna se
  publica como reemplazo por esta vía.

## Cómo avanzar hasta una extracción real de TX2

1. Disponer de dos capturas PPSSPP autorizadas del mismo título:
   menú inicial y al menos una partida habitual, para medir qué hashes
   realmente son estables, cómo cambian CLUT y mipmaps, y qué falta
   por aparecer. Esta PR implementa la comparación sin abrir PPSSPP.
2. Reproducir el descompresor **DZIP de este juego**. Es una cabecera
   propietaria; no se ha verificado que sea zlib, LZMA ni el formato
   genérico llamado «Dzip» en otros programas. Pruebas read-only
   con zlib y LZMA estándar no decodificaron las muestras. La vía
   fiable es inspeccionar la rutina cargada por el juego en memoria
   usando el depurador de PPSSPP y un entorno de investigación aislado.
   El EBOOT de la ISO está encapsulado como ~PSP; no basta con
   desensamblar los bytes almacenados en disco directamente.
3. Validar la descompresión contra varios archivos TX2/XXI/BXN:
   consumo exacto de bytes, tamaño de salida, cabeceras internas
   reproducibles, y pruebas con entradas truncadas y maliciosas.
4. Investigar la cabecera TX2, formatos GE y disposición de texeles
   (swizzle, índices CLUT, alpha, paleta), con comprobación visual
   respecto a PNG capturados por PPSSPP; no inferir solo de tamaños.
5. Con **bytes GE originales** y parámetros de textura contrastar
   nuestra huella de contenido con los 8 dígitos finales del hash
   de PPSSPP. Validar también la huella de paleta para construir la
   clave de reemplazo completa, diferenciando variantes de CLUT
   y posibles colisiones.
6. Solo después publicar automáticamente las texturas sin haber
   llegado a ellas jugando; nunca insertar nombres de reemplazo
   hipotéticos ni sobreescribir packs comunitarios.

## Validaciones

Tests sin assets propietarios:

    python -m unittest tests.test_psp_dariusburst_re tests.test_ppsspp_texture_hash -v

Regresiones del generador HD y Dock:

    python -m unittest tests.test_psp_hd_textures tests.test_ppsspp_graphics tests.test_dock_graphics_metadata tests.test_psp_texture_packs tests.test_dock_relocation -v

Se ejecutó el inventario read-only sobre la ISO **local** real del usuario,
sin exportarla, sin volcar bytes gráficos al repositorio y sin ejecutar PPSSPP.
Queda pendiente validar capturas reales: la carpeta de pack
PSP/TEXTURES/ULJM05558 todavía no existía en el PC al iniciar
esta investigación.

Referencias de implementación:
- https://www.ppsspp.org/docs/reference/texture-replacement/
- https://www.ppsspp.org/docs/psp-hardware/gpu/image-formats/
- https://github.com/hrydgard/ppsspp/blob/master/GPU/Common/TextureReplacer.cpp
