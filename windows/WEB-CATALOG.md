# PSP Dock — Directorio ampliado de juegos web

## Alcance

La primera prueba tenía siete títulos curados y cuatro jugables con mando
sin pasos adicionales. Esta versión conserva sus enlaces directos y suma
una instantánea reproducible del índice público de Ported2Browser SBS,
consultado el 14 de noviembre de 2026:

- 1.439 fichas públicas, sin descargar sus juegos ni archivos privados
- 1.440 títulos distintos tras fusionar siete curados y deduplicar
- NFS Underground 2, HexGL, WipEout y 2048 conservan los runtimes y los
  controles ya desarrollados y probados por el usuario
- Las fichas no validadas NO reciben una URL inventada ni la insignia WEB

Los estados del catálogo conservan distinciones: FICHA para fichas sin
runtime confirmado, DATOS para archivos propios requeridos, ISO para
ejecutables curados que requieren imagen original, RATÓN para control
por puntero y L2/R2 cuando PSP carece de botones necesarios.

## Navegación XMB

La lista vertical usa el renderizado limitado a filas próximas a la
selección, independientemente de que el catálogo tenga miles de juegos.
Antes de las fichas hay cuatro opciones: Buscar, Filtrar, Carpeta de ISOs
y Actualizar catálogo. Se puede filtrar por nombre, letra, género,
mando documentado, datos originales y enlaces pendientes. La selección
por letra y género funciona con mando; escribir texto requiere teclado.

## Orígenes y política de ejecución

Al seleccionar una ficha pendiente, PSP Dock consulta solamente su
página en el catálogo conocido. Extrae el enlace original que la fuente
identifica como apertura del juego o su sitio oficial, y pide permiso
explícito antes de abrirlo. Un enlace extraído sigue siendo no verificado:
puede necesitar autenticación, ratón, un cargador, WebGPU, WebAssembly
o archivos originales. Nunca se promete compatibilidad total sin probarla.

WebView2 recibe solo un ID: consulta al backend local el enlace ya
inspeccionado. El sitio remoto se abre como documento principal sin
sortear restricciones iframe y no recibe acceso al sistema de ficheros
ni a comandos de PSP. Las redirecciones fuera del host activo se
bloquean. Una URL del directorio no se toma por un runtime jugable.

## Renovación del directorio

La instantánea está en web/catalog-snapshot.json y fue generada con el
importador scripts/catalog/import_ported2browser.py. La opción Actualizar
catálogo consulta exclusivamente el índice HTML conocido de manera
explícita, con límite de tamaño, tiempo, tarjetas y redirecciones.
La publicación es atómica; si falla se conserva el catálogo anterior.
No se rastrean los 1.439 juegos en cada inicio ni se descargan ROMs.

## Biblioteca de ISOs del usuario

Se configura una carpeta existente de un disco local fijo. PSP Dock
solo enumera metadatos de archivos ISO, CSO, BIN, CUE y CHD, con límites
de profundidad, tiempo y número de entradas. No lee bytes completos,
no genera hashes de gigabytes ni mueve los datos.

Las coincidencias se sugieren por título o alias explícito; dos discos
o ediciones se tratan como potencialmente ambiguos. El usuario debe
confirmar la imagen exacta. Elegir carpeta NO autoriza subir imágenes
a un servidor web y no cambia SAVEDATA ni la Memory Stick. La selección
e importación efectiva para un port específico requiere que el propio
runtime soporte esos archivos y una integración explícita del selector
de ficheros, todavía pendiente. Es importante no prometer ese paso
como disponible universalmente.

## Pruebas

    python -m unittest tests.test_web_port_catalog tests.test_web_iso_library tests.test_web_catalog_integration -q
    node tests/xmb_full_catalog.test.js
    node tests/xmb_guest_mode.test.js
    dotnet build windows/WebDockShell/PspDockWebShell.csproj -c Release

La máquina de estados del modo sin PSP, la sincronización de guardados,
la emulación Retro y el firmware no se han modificado para el catálogo.
