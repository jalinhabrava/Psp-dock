# PSP Dock 0307 — mando, devolución y memoria de dispositivo

## Primera sesión real 0306 completada

El usuario llegó al selector y XMB de Windows pero no pudo navegar con la PSP. La salida tardó mucho y terminó tras START+SELECT y Círculo, sin retirar el cable.

Los registros retenidos muestran `safe_to_disconnect`, propósito `exit`, devolución verificada y cierre nativo `complete`, secuencia 6 y un intento sin error de entrega. LOCK, DISMOUNT y EJECT devolvieron éxito. El nonce del cierre coincide con el de la devolución. Esto no demuestra que START+SELECT fuese necesario: la salida normal ya estaba solicitada desde Windows.

La elección registrada fue `none`: inventario de 1.467 títulos/ROMs, no una copia masiva de juegos. No hubo callback de sincronización inicial. El registro anterior no conserva contadores de archivos devueltos: no atribuirle una prueba de una partida nueva jugada en esta sesión. Se añaden contadores para las próximas sesiones. Los registros originales están preservados localmente en `data/native-0306-first-success-evidence`, fuera de Git; el registro window-close de 0305 es histórico, no describe esta sesión.

| Etapa real | Tiempo |
|---|---:|
| HOST_BEGIN incluido ACK | 37,171 ms |
| Inventario inicial | 169.044,438 ms |
| Peor publicación de interfaz | 55.881,067 ms |
| Devolución | 30.537,643 ms |
| Cierre | 6.376,984 ms |

## Bloqueo del mando

El rearme esperaba `Buttons & ~PSP_CTRL_NOTE == 0`. El campo kernel incluye estados persistentes MS, DISC, WLAN_UP y REMOTE: MS presente impide satisfacer ese predicado aunque no se pulse ningún botón. Fuente primaria: https://pspdev.github.io/pspsdk/pspctrl_8h_source.html (`PspCtrlButtons`).

La máscara nueva considera controles reales y HOLD. Conserva el centrado analógico, el bloqueo de botones mantenidos al cambiar de fase, NOTE, el acorde de salida y las fases que prohíben entrada. El transporte fragmentado de 0306 no cambia.

La regresión usa el controlador C real con flags persistentes en CHOOSING y CONTROLLER. La guarda anterior falla por assertion en una copia temporal; la corregida pasa. Falta comprobar que ésta era la causa concreta en la PSP física.

## Publicación y devolución

Publicar una fase nativa reutiliza los planes ya calculados; no vuelve a enumerar guardados ni calcula tres veces el retorno. La presentación está vinculada a nonce y tarjeta, no autoriza escrituras y conserva dirección y revisiones de conflictos.

El inventario inicial compara cada grupo de guardados una vez. La salida compara primero las fuentes del PC con su baseline de entrada. Sin cambios, no vuelve a recorrer cientos de rutas SRAM de la PSP: no hay archivos que devolver. Se conservan las comprobaciones de identidad, fase y journals nativos. Con cambios, se mantiene la comparación fresca, conflictos, backup, copia y verificación originales. Cada devolución registra `return_summary` con copias PSP/retro y si el PC estaba sin cambios.

## Recordar consola y elección

La preferencia se vincula al padre USB físico, serial de tarjeta y destinos PC. Se recuerda la última elección, pidiendo confirmación por defecto. Un botón navegable con mando permite aplicarla automáticamente en conexiones futuras. Los conflictos detienen esa automatización. Ajustes permite volver a preguntar sin borrar la última elección.

La caché persistente sólo guarda catálogo. Cada conexión enumera nombres, tipos, tamaños y fechas para detectar cambios y evitar volver a analizar EBOOT/ZIP sin cambios. Se renueva al cambiar la huella o tras 24 horas. Un cambio de contenido que conserve todos esos metadatos no se detecta por esta huella: no es una verificación criptográfica de la tarjeta. Las comprobaciones de contenido existentes y las copias verificadas no se sustituyen por ella. Guardados y autorización USB no se almacenan como permiso reutilizable.

## Validación y entrega

252 pruebas Windows/Python 3.12 PASS. JavaScript: 316 aserciones de producto y 156 de conexión, PASS. C/GCC estricto, ASan/UBSan, UI, MIPS y auditoría ELF/PBP PASS; LSan desactivado en este entorno. Regresión de rearme: guarda anterior FAIL esperado, corregida PASS.

Candidato 0307/03070001, capacidades000F. Host admite 0306 y 0307 con el mismo transporte de fragmentos/CRC16. Paquete `data/native-dock-0307-package`, **no instalado ni probado físicamente aún**. No se han escrito juegos ni guardados durante este trabajo.

| Archivo | SHA-256 |
|---|---|
| EBOOT.PBP | `92c8c683f2975d11530b18c6e88212a9e77bc0b7c49948d8dfe017b39fde609e` |
| docknative.prx | `15847cada01843359d9b8b9910f34874e376157252e05723229ed16c7ca55b3c` |

Pendiente: instalar desde USB normal XMB con backup nuevo; navegar selector y XMB con PSP; jugar y guardar; salir sólo desde Windows; verificar retorno y Círculo al XMB con cable conectado. Medir la segunda conexión para evaluar la caché real, sin prometer tiempos antes de esa prueba.
