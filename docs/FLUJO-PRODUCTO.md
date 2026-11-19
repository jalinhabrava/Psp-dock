# PSP Dock: sesión completa de producto

## Estado actual — instalado; aceptación física pendiente

El nombre visible en ambas pantallas es **PSP Dock**. La revisión de
desarrollo actual, 0302, quedó instalada y verificada el 2 de noviembre de
2026 a las **13:14:30 UTC**: cuatro archivos, **371.488 bytes**. Su ciclo
completo en la consola todavía requiere aceptación física. El paquete y
las pruebas de software no acreditan por sí solos esa aceptación.
[Evidencia actual](validation/native-0302-product-fix.json).

Las aplicaciones anteriores `PSPDock` y `PSPDockStorageTest` se retiraron el
2 de noviembre a las **13:22:12 UTC**, después de verificar su copia completa
en el PC. En el XMB queda una sola entrada: **PSP Dock**. El recibo conserva
los ocho archivos y hashes retirados; no se borraron juegos ni partidas.

## Requisito de David — 2 de noviembre de 2026

El hito incluye toda la sesión, desde el inventario inicial hasta devolver
las partidas al salir. El acceso a la Memory Stick es un medio para ese
flujo. Windows se abre manualmente y no instala un servicio, tarea USB ni
monitor al iniciar sesión.

1. Abrir PSP Dock en Windows: **Conecta tu PSP**.
2. Abrir **PSP Dock** en la PSP con el cable retirado y conectar cuando
   indique **LISTO**. Windows identifica la consola y la PSP espera su
   confirmación. Después, ambas pantallas muestran la preparación y
   **Sincronizando PSP** mientras se comparan juegos, ROMs y partidas.
3. Elegir **Todo**, **Partidas y estados**, **Sólo juegos** o **Sin copiar ahora**.
   El inventario se realiza aunque no se elija copiar. Las opciones sin
   sincronización de partidas avisan de que se usarán las del PC.
4. Completar la selección y entrar en **modo mando**, con el inventario que
   indica qué está en la PSP, qué está en el PC y qué falta por copiar.
5. Para un juego pendiente: triángulo abre el menú lateral; elegir copiar
   registra una intención concreta. **Copiando** bloquea temporalmente la
   navegación, verifica la copia y devuelve el mando al terminar.
6. Cerrar un juego vuelve al menú. No inicia una devolución de partidas ni
   cambia a almacenamiento por el mero cierre del emulador.
7. **Salir del Dock** vuelve a **Sincronizando PSP** y devuelve únicamente
   partidas modificadas y datos de emuladores con formato compatible.
   Los juegos no se copian de vuelta. No se propagan borrados.
8. Mostrar **Puedes desconectar** sólo tras completar la verificación y
   obtener la liberación normal del dispositivo por Windows.

La primera implementación bloquea la navegación durante las transferencias.
No necesita prometer navegación y copia simultáneas para satisfacer el flujo.

## Conexión y estados comprensibles

Windows distingue entre esperar la PSP, identificarla, esperar su
almacenamiento y una conexión bloqueada. Una conexión parcial ya tiene
una explicación visible. Un fallo muestra qué ha ocurrido y qué hacer;
el detalle técnico se puede desplegar sin sustituir esa explicación.

La PSP muestra **Esperando a Windows** hasta recibir la confirmación de la
sesión. Detectar el cable o consultar el dispositivo no inicia una
sincronización. Windows comprueba que el almacenamiento y el mando
pertenecen a la misma PSP y confirma las transiciones antes de habilitar
copias o navegación. Los dos canales pueden permanecer enumerados aunque
la navegación esté bloqueada durante una transferencia.

La carpeta interna sigue siendo `PSP/GAME/PSPDockNative`. El nombre normal
es **PSP Dock**, con el thumbnail y los fondos aprobados. Las revisiones,
identificadores y códigos se reservan al diagnóstico; en la PSP, el botón
de nota musical permite mostrarlo u ocultarlo. El contrato del protocolo
y la distinción entre revisión USB y firmware están en la
[validación técnica](validation/native-0302-product-fix.json).

## Progreso de entrada, copia y salida

Windows muestra la sincronización tanto al entrar como al salir. La PSP
presenta la actividad comunicada por Windows. El inventario puede mostrar
elementos examinados sin un total conocido; en ese caso el indicador es
indeterminado. Un porcentaje sólo aparece cuando existe un total real del
plan: bytes de un juego o guardados cuya copia ya se ha comprobado.

La verificación posterior y la preparación de la desconexión tienen
estados propios. El total de bytes transferidos no autoriza a retirar el
cable. Si una etapa carece de medida real, se muestra actividad, sin
inventar porcentaje ni tiempo restante.

## Cancelación durante la entrada

Antes de establecer una sesión, cerrar la app de Windows la termina sin
iniciar una devolución ni entrar en un bucle de salida solicitada.
Cancelar antes del primer modo mando impide iniciar nuevas copias. Una
operación ya admitida termina su comprobación y después se finaliza la
conexión y se solicita la liberación del dispositivo. Esa cancelación no
se anuncia como si se hubiesen sincronizado todas las partidas.

## Partidas y recuperación

El inventario de entrada conserva por separado los hashes PSP y PC. Al
salir, sólo se consideran los cambios del PC desde esa entrada. Si la PSP
también cambió, se requiere una decisión explícita. Conservar la partida PSP
durante la salida no importa esa versión al PC como efecto lateral.

Las copias usan temporales, relectura SHA-256, backups y journals. Reiniciar
la aplicación no reanuda escrituras por sí solo. Una sesión de emulador que
pueda seguir abierta bloquea la recuperación hasta revisarla.

La devolución y la expulsión son fases separadas. Si Windows veta la
expulsión, el reintento sólo completa esa liberación: no vuelve a copiar las
partidas.

Después de entrar al modo mando, la primera X registra una petición de salida y
restaura una sola ventana de la misma instancia. Como el navegador es otro
proceso, hay un breve cierre y restauración. El worker espera a confirmar la
ventana y su heartbeat; si hay un juego abierto, espera a su cierre normal.
Las copias ya admitidas terminan y los conflictos o vetos permanecen visibles.
Una segunda X no restaura otra ventana: conserva los journals y drena las
operaciones en curso. El modo manual anterior conserva su comportamiento.

## Emuladores y estados de memoria

Esta integración incorpora la detección, biblioteca, controles y guardados
de las ramas `feat/retro-emulator-dock` y `feat/retro-controls-saves`.
Las SRAM de los perfiles ya reconocidos usan sus comprobaciones de formato,
tamaño, ruta, versión y conflicto. No se equipara una extensión de archivo
con compatibilidad entre motores.

Los estados de memoria de emuladores diferentes no tienen un formato
universal. Esta versión conserva en el PC los estados sin un adaptador de
compatibilidad verificado y los informa en la interfaz; no los publica en la
PSP con otro nombre ni afirma haber convertido esos estados. Su conversión
es un requisito pendiente para las parejas de emuladores que la necesiten.

`psp_retro_backup.py` añade un respaldo previo de sólo PSP a PC para los
guardados y estados de emuladores reconocidos bajo PSP/GAME. Incluye estados
comprimidos reconocidos como bytes opacos, sin extraerlos ni convertirlos.
El manifiesto registra identidad, tamaños, SHA-256 y archivos excluidos; el
marcador de respaldo incompleto sólo se retira tras verificar el conjunto.

## Retirada de aplicaciones anteriores

La retirada usa un plan de sólo lectura y una ejecución explícita con
nombres y hashes revisados. `psp_dock_retire.py` comprueba primero la
instalación actual y conserva una copia completa en el PC, verificada con
manifiesto y journal antes de eliminar archivos. Su alcance se limita a
`PSP/GAME/PSPDock` y `PSP/GAME/PSPDockStorageTest` incluidos en el plan;
excluye la aplicación actual, partidas, homebrew ajeno y aparcamientos.

Un cambio de dispositivo, de contenido o del respaldo detiene la operación.
Una interrupción conserva la evidencia y requiere revisión; no reanuda el
borrado automáticamente. Esta descripción de la herramienta no acredita
una retirada física ya completada.

## Verificación y límite físico

Para 0302 han pasado la compilación MIPS, las auditorías, 182 pruebas Windows
y 15 vistas centradas en conexión, progreso y cancelación. La instalación
se verificó según el registro enlazado al principio. **La aceptación física
de la sesión completa sigue pendiente:** debe recorrer confirmación de
Windows, inventario, elección, mando, copia puntual, devolución y liberación.

0301 sí se ejecutó físicamente: Windows observó almacenamiento y mando bajo
el mismo padre USB. El host rechazó la revisión del descriptor Sony al
confundirla con la versión del producto; no hubo sincronización. La PSP
anunciaba actividad antes de la confirmación de Windows y el rechazo no era
visible en la pantalla de espera. La corrección actual separa ambas
identidades y exige una confirmación explícita de sesión.
[Registro histórico 0301](validation/native-0301-startup-fix.json).

El primer arranque físico de 0300 falló al iniciar `usbstorboot.prx`,
dependencia 5, con `8002013C` en la etapa 3, antes de activar USB. 0301
eliminó esa dependencia de activación PC, que los módulos de almacenamiento
no necesitan. [Fallo 0300](validation/native-0300-start-failure.json).
Las [pruebas de integración 0300](validation/product-session-0300.json)
permanecen como evidencia histórica. La aceptación de 0207 corresponde a
su disco sintético. Ninguna de ellas acredita la sesión completa de 0302.
