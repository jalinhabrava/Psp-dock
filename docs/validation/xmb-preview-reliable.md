# PSP Dock — fiabilidad de la música de previsualización del XMB

## Problema

La música del juego (SND0.AT3) no suena siempre al enfocarlo en el XMB.
Es un recurso distinto a los cuatro clics del sistema XMB.

Se encontraron tres fallos independientes:

1. En Windows el navegador intenta reproducir el MP3 una vez; un rechazo de
   autoplay provocado por el mando de la PSP no se reintenta mientras se
   mantiene seleccionado el juego.
2. La precarga de audio existía en el flujo USB antiguo, pero no se iniciaba
   al llegar a choosing en la sesión nativa PSP Dock 0308. Al pasar al modo
   HID, el contenido de la tarjeta deja de estar disponible para una lectura
   improvisada de SND0.
3. La conversión aceptaba MP3 con cabecera ID3 o cabecera de trama MPEG,
   pero la lectura posterior de caché reconocía únicamente ID3.

## Solución

- En Windows se prioriza un único canal MCI del sistema para reproducir en
  bucle el MP3 original convertido y guardado en el PC, sin depender de la
  política autoplay de Edge. Es independiente del canal de los clics y se
  detiene al cambiar de selección, silenciar música, ocultar/cerrar la app,
  entrar en sincronización o iniciar PPSSPP/RetroArch.
- La API del reproductor solo acepta el ID fijo de un juego ya inventariado y
  una marca temporal reciente. Una parada más nueva impide reanudar audio
  procedente de una petición anterior. La reproducción consulta exclusivamente
  la caché del ordenador y no inicia acceso USB ni conversión.
- La precarga ya se lanza también durante choosing en sesiones nativas
  verificadas. Recorre primero las canciones y después imágenes. Cada lectura
  de la PSP exige la fase Sony STORAGE y la identidad nativa aún válidas, con
  el bloqueo original durante la operación. Si el acceso cambia o se cancela,
  la caché incompleta no se marca como terminada y puede reintentarse en una
  conexión posterior.
- Si un audio aún se está preparando en el PC, la interfaz reintenta durante
  una ventana acotada mientras permanezca seleccionado ese juego. Tras cada
  cambio de enfoque, las peticiones tardías se descartan.
- Linux y otros hosts sin MCI conservan la reproducción web tradicional, sin
  alterar permisos autoplay ni simular entradas de teclado.
- La validación de MP3 ahora reconoce tanto ID3 como tramas MPEG válidas.

No se incluyen canciones, AT3, WAV, MP3, firmware ni ROMs en el código o en
la distribución. No se instala servicio residente. Los juegos que no contienen
SND0 permanecen silenciosos.

## Validación

- Pruebas del reproductor MCI con comandos simulados: creación de canal,
  repetición, reuso de selección, cambio, cierre, fallo y rutas no fiables.
- Pruebas host: no acceder a USB para reproducir; no sonar sobre el emulador;
  parada con orden nueva y peticiones obsoletas; precarga de audio antes de
  imágenes; abortar lecturas cuando deja de estar verificado STORAGE.
- Pruebas JavaScript: selección rápida, sonido nativo sin duplicar HTML,
  reintento si la caché todavía no existe, respuesta atrasada, silencio al
  lanzar y regreso tras cerrar.
- HTTP sintético: autenticación de origen/token, rutas arbitrarias rechazadas,
  y demo sin reproducción física.
- Edge real con servidor simulado: previsualización en bucle, interrupción
  inmediata al lanzar, silencio mientras juega y reanudación al volver.
- Windows real con MP3 y WAV silenciosos generados para la prueba: apertura
  y reproducción MCI en bucle correctas; un clic PlaySound simultáneo no
  interrumpe el canal de música.

La aceptación acústica con los juegos originales de la PSP conectada sigue
pendiente. Esto no demuestra que todos los juegos tengan música ni garantiza
que una primera conexión sin tiempo suficiente para precargar todas las pistas
disponga de cada SND0 antes de abandonar STORAGE.
