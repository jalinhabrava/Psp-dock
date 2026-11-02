# PSP Dock: sesión completa de producto

## Estado actual — 0301, pendiente de prueba física

La revisión actual es **0301**. La primera ejecución física de 0300 se
detuvo al iniciar `usbstorboot.prx`, dependencia 5, con `8002013C` en la
etapa 3. El fallo ocurrió antes de registrar HID y activar USB; no se
verificó una sesión nativa ni se realizó sincronización en ese intento.
[Evidencia del fallo 0300](validation/native-0300-start-failure.json).

0301 retira esa dependencia de activación PC y corrige la presentación de
los errores de arranque. El flujo descrito a continuación sigue siendo el
objetivo implementado en el producto; **su funcionamiento completo en la
PSP con 0301 aún requiere aceptación física**.

## Requisito de David — 2 de noviembre de 2026

El hito incluye toda la sesión, desde el inventario inicial hasta devolver
las partidas al salir. El acceso a la Memory Stick es un medio para ese
flujo. Windows se abre manualmente y no instala un servicio, tarea USB ni
monitor al iniciar sesión.

1. Abrir PSP Dock en Windows: **Conecta tu PSP**.
2. Abrir Dock en la PSP. En la variante nativa 0301, iniciar con el cable
   retirado y conectar cuando indique **LISTO**. Tras verificar la conexión,
   mostrar **Sincronizando PSP** mientras se comparan juegos, ROMs y
   partidas de ambos dispositivos.
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

## Transporte 0301

`homebrew/native-dock` es una variante separada de la 0207 ya validada.
Carga las dependencias oficiales de Sony y combina su controlador de
almacenamiento con el mando HID del Dock. Sony mantiene la propiedad del
sistema de archivos; el módulo nuevo no importa `sceIo*` ni redirige a flash.

Las dependencias obligatorias son `semawm`, `usbstor`, `usbstormgr` y
`usbstorms`; un fallo en cualquiera de ellas sigue deteniendo el arranque.
0301 elimina `usbstorboot.prx`: sus imports de firmware 6.61 incluyen
activación PC mediante Pcact/NPDRM, y los cuatro módulos del transporte no
dependen de Boot. La foto 0300 identifica el módulo y la etapa del fallo,
pero no cuál de sus bibliotecas importadas faltaba. No se añaden módulos
de activación para resolverlo. El launcher y el kernel 0301 usan el magic
`PDN1`, que impide mezclar ambos componentes con los de 0300.

Un error de arranque prevalece ahora sobre la fase lógica inicial:
la pantalla muestra **Arranque detenido**, el contexto y los códigos,
sin presentar **Sincronizando PSP** como una operación activa.
Una interfaz HID aún no asignada aparece como `sin_asignar`.

El protocolo PDK2 solicita fases y espera confirmaciones de la misma sesión
y secuencia. Los cambios de fase son lógicos: ambos canales USB pueden seguir
enumerados, mientras el producto decide cuándo autoriza lectura, escritura o
navegación. La aceptación de una orden no prueba por sí sola el montaje.

Windows vincula la interfaz HID, el disco Sony y la letra de unidad a un
único padre USB físico. El PID previsto es `054C:01CD`, revisión `0301`.
Las guardas del HID estable y del diagnóstico 0207 no se amplían para aceptar
una tarjeta genérica ni un dispositivo con otra revisión.

El paquete PSP instala sólo `PSP/GAME/PSPDockNative`, conserva la instalación
estable y la 0207 e incorpora el nombre, ICON0, PIC0 y PIC1 aprobados.

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

La primera X durante una sesión nativa registra una petición de salida y
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

## Verificación y límite físico

La compilación 0301 con PSP GCC, las pruebas C normales y con ASan/UBSan,
los casos de presentación PSP, las auditorías ELF/PBP, 65 pruebas Windows
y 163 aserciones de interfaz han pasado. La 0301 quedó instalada y releída
por SHA-256, con copia previa de la 0300 en el PC, a las 11:46:08 UTC del
2 de noviembre. [Validación 0301](validation/native-0301-startup-fix.json). Son
comprobaciones de software: no demuestran que el bus Sony componga ambas
funciones en la PSP concreta ni que retirar Boot resuelva por sí solo todo
el arranque en hardware.

La compilación y los ensayos previos de 0300, junto con las pruebas Windows
y las vistas sintéticas de su interfaz, se conservan como evidencia de
aquella integración. **No se transfieren como aceptación de 0301.** La
prueba física posterior de 0300 falló antes de activar USB, como registra
el informe enlazado al principio; no validó acceso real a la Memory Stick,
entrada HID ni sincronización. [Registro histórico de integración 0300](validation/product-session-0300.json).

La comprobación física pendiente de 0301 debe confirmar primero el arranque,
observar PDK2, HID y la Memory Stick real bajo el mismo padre, y recorrer
entrada, selección, copia puntual y salida. Hasta entonces no se marca como
validado en hardware el transporte 0301 ni la sesión completa. La evidencia
de 0207 corresponde a su disco sintético y permanece independiente.
