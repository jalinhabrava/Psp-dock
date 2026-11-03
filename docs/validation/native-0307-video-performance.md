# PSP Dock 0307 — perfilado del vídeo y correcciones en Windows

## Alcance y estado

Revisión del 3 de noviembre de 2026 sobre la grabación `Grabación de pantalla 2026-11-02 230730.mp4` y los registros de su sesión. La grabación dura 567,667 s (9:27,7). Se inspeccionaron fotogramas de las transiciones y de los periodos de espera; no se afirma haber verificado cada fotograma ni transcrito audio.

**No se modifica ni reinstala la 0307 de la consola.** Este cambio está en el host Windows. El protocolo fragmentado, las comprobaciones de sesión y el cierre que ya funcionan se conservan. La PSP no estaba visible durante el perfilado: no hay una medición nueva del ciclo físico completo ni una confirmación física del selector.

Los registros originales se copiaron a `data/video-0307-evidence-20261103-065332` antes de abrir otra instancia. Esos datos, el vídeo y las bibliotecas no se incluyen en Git.

## Prueba real anterior

En el vídeo el selector aparece aproximadamente a 4:55, el menú hacia 6:30 y el arranque del juego hacia 7:00. Estos puntos son aproximados. El vídeo incluye decisiones del usuario, navegación y juego; no todo su tiempo es carga del Dock.

| Etapa en los registros originales | Tiempo |
|---|---:|
| Inventario inicial | 270,049 s |
| Orden HOST_BEGIN y confirmación USB | 0,049 s |
| Preparación para mando, después de elegir | 51,315 s |
| Comprobación de devolución | 21,799 s |
| Finalización de conexión | 15,472 s |
| Identificación de almacenamiento, 16 consultas, incluidas dentro de las etapas anteriores | 46,285 s acumulados |

No se suman los subintervalos superpuestos. La publicación de interfaz ya no era el cuello de botella anterior: seis publicaciones acumularon 1,860 s, máximo individual 0,897 s.

La sesión eligió `none` (Sin copiar ahora), encontró 1.467 títulos/ROMs y terminó en `safe_to_disconnect`. La devolución registra `psp_copied=0`, `retro_copied=0`, `pc_unchanged=true`; el cierre terminó en `complete`, con un intento. No hubo guardados nuevos que exportar. Esto no es una prueba de transferencia de una partida nueva de PPSSPP a la PSP.

## Cambios

### Comparaciones por directorio, no por cada ruta ausente

`ReadOnlyTree` comparte el resultado de `scandir` únicamente durante una comparación. Detectar que falta el directorio de ROMs del PC resuelve miles de ausencias sin recorrer y resolver los mismos padres para cada archivo. El guardado de batería ausente tampoco exige comprobar la ruta de un estado instantáneo que no se va a utilizar.

La comparación sigue comprobando nombres, tipos, tamaños y fechas; los directorios con enlaces o junctions se rechazan. La caché temporal no autoriza escrituras ni lecturas de contenido. Un archivo existente que debe leerse mantiene las comprobaciones de ruta originales; las copias mantienen identidad, hashes, respaldo, conflicto y verificación. Cada comparación usa una vista nueva.

`retro_catalog`, el inventario de emuladores y la búsqueda de SRAM reutilizan estas consultas. La disponibilidad de núcleos se comprueba una vez por sistema, no una vez por ROM. Los contadores de progreso del catálogo publican totales reales cuando ya se conocen.

### Entrada al mando sin repetir el inventario de la PSP

Se conserva la comparación PSP capturada en el inventario o después de sincronizar. Antes de entrar al mando se actualiza la comparación del PC, sin repetir todos los guardados PSP. Mantener la versión PSP original también permite detectar modificaciones externas durante la sesión al devolver partidas.

La preparación de PPSSPP usa el catálogo de esta sesión; no vuelve a explorar toda la tarjeta dos veces para averiguar si hay un juego listo. La elección explícita de usar guardados del PC no ejecuta una comparación de guardados PSP que no se utilizaría. El lanzamiento y las copias conservan sus comprobaciones propias.

### Identificación de disco sin arrancar PowerShell en cada paso

Una consulta nativa y de solo lectura asocia el volumen al disco presente mediante `IOCTL_STORAGE_GET_DEVICE_NUMBER` y SetupAPI. Comprueba que la asociación sigue siendo la misma al acabar, y conserva las validaciones del padre Sony y del serial de la tarjeta. Los números de dispositivo no se guardan como identidad permanente. La ruta anterior mediante CIM queda como alternativa de compatibilidad si falla la consulta nativa.

Esta API se ejecutó realmente en Windows contra un volumen local, y enumeró correctamente la ausencia de discos Sony. Falta comprobarla con la PSP conectada; no se atribuye a esa prueba una mejora medida del cierre PSP.

Fuentes primarias usadas para cotejar la interfaz:

- Microsoft: [IOCTL_STORAGE_GET_DEVICE_NUMBER](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-ioctl_storage_get_device_number).
- Microsoft: [SetupDiGetDeviceInterfaceDetailW](https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupdigetdeviceinterfacedetailw).
- Microsoft: [SetupDiGetDeviceInstanceIdW](https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupdigetdeviceinstanceidw).

### Selector con mando

La admisión de entrada en `choosing` consulta el estado confirmado del transporte, no una presentación que todavía puede corresponder al inventario. Se reproduce con una prueba donde el transporte confirma la PSP pero el snapshot de interfaz conserva `connected=false`.

La selección con mando tiene un foco explícito, visible también después de usar el ratón. Se añade una indicación de recepción de botones y el registro local acotado `selector-input.json`, con hasta 64 cambios de estado. No se escribe en la Memory Stick.

**No se ha demostrado que el desfase del snapshot sea la causa del fallo completo observado por el usuario.** La próxima prueba física puede distinguir entrada bloqueada, ausencia de pulsaciones y pulsaciones recibidas que no mueven el selector, sin otra instalación del homebrew.

## Mediciones de comparación

### Rutas reales del catálogo del usuario, sin acceder a la PSP

| Trabajo de solo lectura en Windows | Antes | Después |
|---|---:|---:|
| Comprobar 1.446 ROMs ausentes en el PC | 17,205 s | 0,0176 s |
| Comprobar las 410 rutas de guardados retro | 2,887 s | 0,0085 s |

El resultado de guardados coincide con el registro de entrada. El perfil anterior efectuaba decenas de miles de consultas de metadatos y resoluciones de padres. La nueva comparación de ROMs ausentes enumera un directorio. Son tiempos de esas funciones, **no del inventario USB completo**. La medición anterior incluyó `cProfile`, por lo que tampoco se interpreta el cociente como una aceleración exacta en todas las condiciones.

### Mismo recorrido de producto, biblioteca sintética grande en Windows

Se ejecutó el código anterior del commit `20698c9` y el nuevo código contra bibliotecas temporales equivalentes: 1.446 ROMs, 410 guardados retro y el transporte falso explícito de los tests. Se llamó al coordinador real y a la publicación con catálogos reutilizados, se eligió `none` y se completó la devolución y el cierre. No se ejecutaron juegos ni se accedió al USB real. Una ejecución por versión; los tiempos pueden variar por caché y carga del sistema.

| Etapa | Código anterior | Código corregido |
|---|---:|---:|
| Inventario | 120,054 s | 12,230 s |
| Preparación del mando | 27,250 s | 0,135 s |
| Devolución sin cambios | 8,159 s | 0,176 s |

Ambos recorridos llegaron a cierre verificado, sin copias. La comprobación muestra una reducción del trabajo del host; no promete esos tiempos en una Memory Stick real.

## Validación

- 204 pruebas Python en Windows: PASS. Incluyen producto, catálogo, rutas, sincronización, conflictos, transporte, cierre, API HTTP y recuperación.
- Tras endurecer la comprobación del tipo de directorio: otras 21 pruebas focales PASS.
- JavaScript: 316 aserciones de producto y 156 de conexión/ventana PASS.
- Edge real en modo headless, HTML/CSS y funciones reales del selector, con entrada y HTTP simulados: navegar hasta «Sólo juegos», conservar el foco tras refrescar y emitir una única elección con el offer correcto: PASS. Foco visible de 3 px. No es una prueba del mando físico.
- Biblioteca sintética grande: ambos recorridos completos PASS, con tiempos anteriores y nuevos registrados.

## Pendiente físico

Ejecutar el host actualizado con la PSP 0307, comprobar la cruceta y ✕ en el selector, lanzar el juego y cerrar desde Windows sin retirar el cable. Medir de nuevo `inventory.games`, `inventory.retro`, `inventory.saves`, `prepare.pc_baseline`, `prepare.runtime` y `return.pc_scan`. Confirmar con una partida modificada el retorno real: la grabación aportada no contiene un guardado nuevo que devolver.
