# PSP Dock 0306: órdenes pequeñas, bienvenida y recuperación visible

## Estado al 2 de noviembre de 2026

0305 se instaló y verificó a las 20:10:19 UTC, pero no resolvió la recepción de HOST_BEGIN ni HOST_SHUTDOWN en la PSP real. La lectura posterior conserva secuencia 0, resultado 0 y host sin confirmar. La tarjeta sí fue liberada mediante LOCK/DISMOUNT/EJECT_MEDIA; el fallo de cierre es posterior a esa liberación.

0306 está compilada, auditada y empaquetada en el PC. **Todavía no está instalada ni validada en hardware.** No afirmar que el cierre con cable o la latencia de conexión estén resueltos hasta comprobarlos en la consola.

## Evidencia y límite del diagnóstico

La captura 0304 mostró los 64 bytes completos de la orden con completion cancelada (-3), sin entrar en el parser. El cambio de control-request de 0305 no bastó: en su intento real SET tardó aproximadamente 5015 ms y la espera de ACK añadió unos 2007 ms, sin aceptación. En la prueba previa, un Output de cuatro bytes completó en 0,196 ms. Es evidencia de una ruta pequeña que funciona, no una explicación definitiva de todos los detalles internos de Sony USBBus.

La liberación comprobada del medio no puede sustituir la orden terminal ni la descarga de módulos. El estado SAFE, un GET fallido y una solicitud local de salida tampoco prueban por sí solos que Windows haya terminado de usar la tarjeta.

## Transporte 0306

Se conserva el núcleo PDK2/v3 de 64 bytes y su GET_FEATURE de estado. No se modifican las longitudes del descriptor: Input 8, Output 4 y Feature 64 bytes, sin contar el identificador cero que añade la API Windows.

Las órdenes de 0306 se envían mediante `HidD_SetOutputReport` en 32 fragmentos de cuatro bytes y un commit final, también de cuatro bytes. Cada fragmento contiene índice, token de ensamblado y dos bytes del núcleo. El commit contiene el mismo token y CRC16-CCITT del núcleo completo. La PSP exige orden estricto, misma generación USB, completions exitosas, longitud exacta y CRC correcto antes de invocar el parser original. El token distingue ensamblados; no es autenticación.

El parser sigue verificando nonce de sesión, secuencia monotónica, firmware/build/capacidades y transición permitida. Ningún fragmento aislado ejecuta una acción. Un envío incompleto detiene al host, sin reenvío automático. Se mantienen el drenaje de entrada, la autorización terminal ligada a la liberación del medio y las comprobaciones reales de Stop/Unload antes de ofrecer Círculo para volver al XMB.

Los firmware 0303, 0304 y 0305 mantienen su transporte anterior. El selector depende de la identidad comprobada, no sólo del bcdDevice de Sony. El contrato compilado `PDF4` registra los 32 fragmentos, el tamaño de dos bytes de datos, el commit y CRC16; la auditoría verifica el símbolo ELF.

## Diagnóstico desde Windows

0306 usa dos bytes reservados del estado para publicar un resumen RAM de recepción: descarte, longitud recibida y resultado. Las órdenes siguen exigiendo cero en esos bytes. No se escribe un log en la Memory Stick mientras pertenece al host ni se modifica el informe de botones. Ante timeout de inicio o cierre, Windows puede conservar estos datos sin pedir otra fotografía.

El diagnóstico se lee mediante GET_FEATURE. No utiliza `HidD_GetOutputReport`, función que no está exportada por la API Windows observada. Un intento de declarar ese nombre se detectó y eliminó antes de empaquetar la versión final; el probe real de 0305 volvió a ejecutarse correctamente.

## Bienvenida y reintento

El estado interno de recuperación no se borra ni se convierte en permiso de copia. Al abrir la app, la presentación muestra **Bienvenido a PSP Dock**, con un aviso contextual cuando queda trabajo anterior. Los errores nuevos de la sesión siguen siendo visibles.

La pantalla de cierre decía que se pulsara Reintentar, pero su modelo requería `resume_phase`, dato que el snapshot del backend no incluía. Se añade ese campo al snapshot y se prueba la recuperación con él. El cierre anterior ofrece **Completar cierre anterior**, sin volver a inventariar ni copiar. Las acciones de juego y acceso a la tarjeta siguen bloqueadas durante ese cierre.

Se abrió la app actualizada y se comprobó su API real: recuperación inicial, finalización pendiente y `can_retry=true`. El estado real pasado por el modelo JavaScript devuelve Bienvenido a PSP Dock y el botón Completar cierre anterior. Abrir la ventana no envió otra orden USB.

## Validación realizada

- Windows/Python 3.12: 197 pruebas seleccionadas, PASS. Incluyen transporte real bajo dobles de DLL, fragmentación/CRC, cierre, recuperación, paquete y actualización/rollback desde la 0305 instalada.
- JavaScript: 310 comprobaciones de producto y 156 de conexión/ventana, PASS.
- C: transporte y UI, GCC estricto, ASan y UBSan, PASS. LSan desactivado en este entorno.
- Compilación MIPS kernel/USER, auditoría ELF, identidad, imports y recursos PBP, PASS.
- Relectura real de la sesión 0305 y del recibo de liberación: misma sesión, medio ausente con ERROR_NOT_READY (21). No se enviaron nuevas órdenes ni se modificaron juegos o partidas durante este diagnóstico.

Una ejecución amplia bajo el Python 3.10 de WSL falló porque el proyecto utiliza `hashlib.file_digest`, disponible en el Python de Windows utilizado por el producto. Esa ejecución no se cuenta como superada; las 197 pruebas indicadas corresponden a Windows/Python 3.12.

Las pruebas cubren fragmentos ausentes, duplicados, fuera de orden, token mezclado, CRC incorrecto, transferencia cancelada o corta, desconexión, nonce incorrecto y GET entre fragmentos. También comprueban que el commit es el último paso y que no haya acción antes de recibirlo. No sustituyen la prueba física del nuevo transporte.

## Paquete final

Usar `data/native-dock-0306-package-final`, no el paquete intermedio `native-dock-0306-package`.

| Archivo | Bytes | SHA-256 |
|---|---:|---|
| EBOOT.PBP | 313161 | `e0aabb4a5aa4b65ff6c6926a7500e60ba97a0b7aea3acc35d8bb84ba93597a3b` |
| docknative.prx | 27146 | `69a1b2cf3c0189745a3d7f39b69c54d1f2445b6c4afab7a997b49feb614fe505` |

Firmware/build: 0306/03060001; capacidades 000F. Se conservan los cuatro archivos del paquete y el mecanismo de actualización reversible con backup previo. Falta instalar desde USB normal del XMB y comprobar inicio, sincronización, mando y salida desde Windows con el cable conectado.
