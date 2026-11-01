# Demostración de compatibilidad DZIP — resultados verificables

Fecha: 1 de noviembre de 2026. Juego de investigación: Dariusburst PSP
(edición japonesa ULJM05558, DISC_VERSION 1.01).

## Qué se ha conseguido realmente

1. Se inspeccionó la copia ISO local del PC **sin modificarla**.
   Tamaño: 193.789.952 bytes. Huella SHA256 (prefijo):
   60ec90a4d9fe84a1aab38bc3.
2. Se revisaron 1.002 archivos ISO; exactamente **955 tienen la firma
   DZIP**: 27 TX2, 805 XXI y 123 BXN. Sus cabeceras presentan nueve
   prefijos opacos diferentes. No se deduce una versión de códec de ellos.
   Errores del inventario: cero.
3. Se preparó una **copia separada de PPSSPP 1.20.4** con Memory Stick y
   configuración propias, sin usar la biblioteca de partidas LIVE.
   Se confirmó en el código fuente de esa versión la opción:
   General -> DumpFileTypes = 1. La opción antigua DumpDecryptedEboots
   **no funciona en PPSSPP 1.20.4**.
4. Tras arrancar Dariusburst exclusivamente en ese laboratorio, PPSSPP
   generó el ejecutable descifrado ULJM05558_EBOOT.BIN como ELF MIPS LE
   de 9.988.128 bytes, con firma ELF y secciones .text/.rodata válidas.
   Prefijo SHA256: 39751d0cdd35ef83f3dff1ff.
   El código tiene referencias concretas a imágenes TX2 y escenas XXI.
5. Se activó la captura en el PPSSPP aislado, que generó un PNG de textura
   con hash de PPSSPP y dimensiones 256×256, sin tocar la ISO ni la PSP
   física. Se conserva únicamente en el laboratorio del PC.

## Qué NO se ha conseguido aún

- **No se ha identificado ni reproducido el algoritmo DZIP de este juego.**
  Hay evidencia del envoltorio, pero no un resultado de descompresión
  byte a byte validado. Por tanto, el número de TX2/XXI/BXN no es el número
  de texturas extraídas.
- **No se ha conseguido una muestra descomprimida de otro juego** que
  comparta esta misma implementación. Las referencias antiguas a ejemplos
  de Dariusburst Chronicle Saviours no son por sí mismas evidencia
  binaria de compatibilidad. En esta ejecución no había otro juego
  legalmente disponible y validado como segunda fuente DZIP.
- El ELF descifrado no equivale a un descompresor portable. Se inspeccionó
  con herramientas de MIPS de solo lectura. El descompilador headless
  Ghidra 12.2 se preparó de forma local, pero en este entorno Windows
  falló al crear su proyecto con «The system cannot find the file specified»;
  **no se obtuvo pseudocódigo verificado** de la rutina DZIP.
- PPSSPP 1.20.4 permite un depurador por WebSocket, pero su servidor
  puede enlazar a interfaces no locales. **No se activó ese servicio**
  para evitar exponer el acceso a memoria del emulador a la red.
- No se generaron reemplazos HD basados en un supuesto formato DZIP.

## Nuevo verificador independiente de juegos

El módulo psp_dzip_compatibility.py permite:

- Examinar DZIP en cualquier ISO/CSO PSP reconocida por PARAM.SFO,
  usando únicamente estructura ISO9660 y cabeceras; no requiere
  listas de nombres o reglas particulares de Dariusburst.
- Comparar perfiles de cabecera de dos juegos, sin confundir
  coincidir en el nombre o en la firma con compartir el algoritmo.
- Probar un decodificador determinista frente a **cuatro referencias
  completas, al menos dos de cada uno de dos DISC_ID diferentes**.
  Debe recuperar **exactamente** los bytes que suministra el verificador
  como referencias, en la longitud declarada y consumiendo todo el
  payload comprimido. Cualquier diferencia, muestra repetida,
  sobra o truncamiento hace fallar el certificado.
- El informe incluye las huellas SHA256 de las muestras y la longitud,
  **nunca almacena los recursos originales**. La procedencia de una
  referencia debe auditarse externamente y se declara no auditada
  por el propio verificador. Un éxito de pruebas de muestra tampoco
  significa compatibilidad universal con cualquier DZIP.

La batería de pruebas usa exclusivamente datos inventados en el test
(una compresión zlib artificial bajo envoltorio DZIP), **NO un DZIP
comercial real**. Prueba que el verificador funciona, pero no valida
la implementación real del juego.

## Ejecución y criterio de aceptación

Escanear otro juego legalmente disponible desde su copia local:

    python -m psp_dzip_compatibility "RUTA_DEL_JUEGO.iso"

Pruebas del verificador:

    python -m unittest tests.test_psp_dzip_compatibility -v

Para conseguir el primer certificado **real** hay que:

1. Recuperar dos salidas byte a byte de la rutina del juego cargado en
   PPSSPP y localizar con precisión su entrada comprimida de origen.
2. Implementar el mismo algoritmo en el motor PSP Dock y verificar que
   consume exactamente cada payload DZIP y reproduce bytes originales.
3. Identificar otro juego con el *mismo formato real*, conseguir dos
   pares entrada/salida independientes y verificar igualmente ambos.
4. Decodificar la estructura gráfica TX2 y demostrar la correspondencia
   con la clave completa de reemplazo PPSSPP (incluida CLUT).

**Criterio estricto actual:** DZIP identificado: sí.
Ejecutable MIPS descifrado: sí. Captura PPSSPP: sí.
Algoritmo DZIP validado: **no**. Compatibilidad entre juegos: **no**.
Packs HD generados desde DZIP: **no**.

## Seguridad y reproducibilidad

El código nuevo no abre, modifica ni extrae imágenes de ningún juego; los
informes de consola incluyen solo identificación y métricas. No se añade
a Git el ELF descifrado, PNG del juego, firmware, ISO, savestate, binarios
de PPSSPP ni herramientas de análisis. Las herramientas de laboratorio
se encuentran en el directorio tools, excluido de control de versiones.
Se cerraron todas las instancias de PPSSPP creadas para estas pruebas.
Las partidas originales y la PSP física no se tocaron.

Fuentes técnicas:

- https://github.com/hrydgard/ppsspp/blob/v1.20.4/Core/Config.cpp
- https://github.com/hrydgard/ppsspp/blob/v1.20.4/Core/System.cpp
- https://github.com/hrydgard/ppsspp/blob/v1.20.4/Windows/main.cpp
- https://github.com/hrydgard/ppsspp/blob/v1.20.4/Common/Net/HTTPServer.cpp
