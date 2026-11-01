# PSP Dock · prototipo para Windows

## Controles PSP HID y continuidad de partidas retro (PR siguiente a #4)

Perfil RetroArch DirectInput PDK1 privado y reversible, selección de
distribución de ✕/○, menú SELECT+△ y rutas por juego para SRAM y estados.
Las SRAM raw de gpSP/TempGBA y Snes9xTYL/Euphoria cuentan con importación
y devolución individual previa comparación SHA-256, con backups, journals y
resolución explícita de conflictos; no se convierten estados de emulación.
La salida del Dock espera la revisión de guardados retro antes de devolver
las partidas PSP. Ver [RETRO-CONTROLS-SAVES.md](RETRO-CONTROLS-SAVES.md).

## Emuladores retro de PSP → Windows (nueva sección XMB)

La categoría **Retro** reconoce homebrew de emulación en `PSP/GAME`,
separa sus `EBOOT.PBP` de los juegos de PSP, inventaría las ROMs de sus
carpetas y las enlaza con el núcleo RetroArch equivalente a cada consola
(gpSP → mGBA, Snes9xTYL → Snes9x, NesterJ → Mesen, etc.). La copia al PC
es individual, consentida y verificada por SHA-256; el lanzamiento exige
RetroArch instalado con el núcleo, una ROM local verificada, partidas
preparadas y mando PSP HID PDK1 verificado. Las partidas retro no se
importan ni exportan entre emuladores automáticamente.

**Guía, tabla de equivalencias, límites y pruebas:** [RETRO-EMULATORS.md](RETRO-EMULATORS.md).

## New feature branch: media, storage location and graceful shutdown

See MVP-HANDOFF-USB-MEDIA.md for the changes based on the previous
five-category XMB design (now extended by the Retro section). PSP ISO/PBP previews now include original
PIC0/PIC1 images and SND0 music, cached privately on the PC.
PC storage location can be selected from Windows and verified
before activation. HD texture installation requires curated,
hash-verified archive sources. The triangle button still opens
the RIGHT-SIDE menu, never directly starts a game transfer.

USBSTOR to HID and back is NOT physically automated yet. The
feature branch contains no PSP firmware changes and must not
be presented as a fully seamless one-cable product.

## Arranque USB sin monitor residente (piloto)

**No hay proceso de PSP Dock lanzado al iniciar sesión de Windows.**
Se retiró el autoarranque anterior. El homebrew PSP tiene en desarrollo
un menú explícito X: Iniciar PSP Dock en PC antes de enumerar HID;
Windows dispone de una tarea puntual de Plug and Play que abre el
selector si detecta el mando Sony PSP Dock.

El piloto PnP está registrado en Windows, pero NO se ha verificado
todavía que el evento 410 se produzca en cada reconexión USB.
No se usan teclas simuladas ni AutoRun inseguro. El EBOOT nuevo
está compilado solo en el PC y no sustituye todavía el instalado
en la PSP real mientras haya una devolución pendiente.
Ver docs/ARRANQUE-HID-AUTOMATICO.md.

## Estado actual — un solo PSP Dock, sin límite temporal

El usuario pidió abandonar las sesiones artificiales de 90 segundos.
Se compiló y se instaló realmente un único cliente PSP Dock
en PSP/GAME/PSPDock, sin watchdog por tiempo. HID continúa
hasta que se mantenga START+SELECT para salir; desconectar
el cable también detiene HID en kernel.

Se limpiaron 12 carpetas de laboratorios y plugins antiguos.
Se dejaron intactos ARK-5, FastRecovery, emuladores del usuario,
ISO, SAVEDATA y el firmware. Dos backups PC completos por
SHA-256 están en Documents/PSP-Dock-Backups: uno de los
48 archivos críticos vigentes y otro de los 25 elementos
de laboratorio retirados. Los 40 archivos SAVEDATA
conservaron idénticos hashes antes y después de la limpieza.
PLUGINS.TXT está vacío; apagar completamente PSP antes
de la siguiente prueba descarga los observadores antiguos.

El nuevo homebrew aún necesita prueba física de entrada
y salida HID; compilación no implica éxito de hardware.
La transición USBSTOR→HID sigue MANUAL y la pantalla
de la PSP no se apaga automáticamente, pendientes de un
protocolo kernel seguro. No instalar la versión 15 minutos.
Ver HANDOFF.md y docs/PRUEBA-XMB-PSP-SIGUIENTE.md.

## Última implementación — XMB de una sola ventana y prueba HID extendida

El acceso Iniciar-PSP-Dock.cmd reutiliza un servidor LIVE ya abierto
mediante start_dock.py, server.json y comprobación HTTP de identidad,
sin iniciar otro sobre los mismos guardados. La nueva instancia usa un
puerto local libre si no existe una anterior. La ventana XMB de
Chrome/Edge se maximiza por título exacto y se reutiliza al
volver a iniciar el acceso, sin abrir ventanas duplicadas.

Mientras termina el inventario del dispositivo, el XMB puede
mostrar juegos conocidos del catálogo del PC marcados «comprobando»
(no jugables hasta validar nuevamente la PSP). La elección de
omitir copias pendientes se conserva para la misma lista de juegos
y el mismo volumen Sony; no copia ninguna ISO automáticamente.

Los diálogos de gráficos también funcionan con PSP: cruceta
arriba/abajo enfoca cada opción, izquierda/derecha cambia su valor.
Los tests JavaScript se pueden ejecutar con
node tests/xmb_controller_navigation.test.js.

**Interfaz XMB (01/11/2026).** La pantalla principal funciona como el XMB de
la PSP: Ajustes · Dock · Juegos · Retro · Partidas · Gráficos en horizontal y su
contenido en vertical. ✕ entra o juega, ○ vuelve, △ abre las opciones del
juego, START vuelve a Juegos y SELECT lleva a «Salir del Dock» (que pide
Sí/No). Los gráficos son por juego: cada valor se guarda en el perfil del
juego abierto y muestra si es PROPIO o heredado del predeterminado. El
fondo cambia de color con el mes y la hora. Modelo puro probado con
node tests/xmb_navigation_model.test.js.

Se compiló en PC una variante experimental del HID-only LAB con
15 minutos máximos, en homebrew/hid-extended/dist. **NO se instaló
en la PSP ni reemplaza el lab de 90 segundos ya demostrado.**
START+SELECT, desconexión y watchdog siguen siendo mecanismos
de salida; requerirá una prueba reversible antes de utilizarse.
Esto NO es todavía la transición USBSTOR ↔ HID automática.

La última prueba visual está guardada en
docs/XMB-preview-graphics-20261101.png.

## Actualización de desarrollo — selector PSP y gráficos ultrawide

**El lanzamiento automático de Dariusburst al conectar HID era un
laboratorio anterior, no la experiencia Dock.** Se ha desactivado el
acceso directo legacy y se requiere una confirmación explícita si alguien
invoca su CLI de laboratorio. El inicio normal del Dock presenta el
selector XMB: ningún juego se ejecuta sin elegirlo.

La interfaz tiene lectura directa de la PSP HID mediante Windows WinMM,
independiente de Chromium Gamepad API; CROSS confirma, CIRCLE vuelve,
SQUARE abre partidas, TRIANGLE abre mejoras gráficas, L/R cambian páginas,
START abre ajustes y SELECT enfoca Salir. La primera muestra del
controlador se usa para detectar botones ya pulsados sin ejecutarlos.

Los gráficos por juego se adaptan al monitor principal, detectado en
este PC como 3440×1440 con NVIDIA RTX 3070 Ti. Perfiles Automático (5×),
Equilibrado (4×), Calidad (6×), Ultra (7×), Manual y encuadre original
sin deformación o estiramiento ultrawide explícito. Solo modifica el
PPSSPP PRIVADO del PC, con backups. No hay DLSS nativo ni ampliación
real de campo de visión sin parche de juego compatible. Ver
docs/GRAFICOS-PPSSPP.md.

En el flujo actual, al elegir Salir del Dock se autoriza la devolución
de partidas. Tras regresar **manualmente** al almacenamiento Sony
original, el Dock las devuelve automáticamente una sola vez si
HID está ausente y no hay conflictos; registra fallos para revisión,
sin sobrescrituras ciegas. Los siete saves de la última sesión física
se comprobaron idénticos entre PSP y PC, incluido Dariusburst.

El autoinicio de preparación se limita a guardados idénticos sin cambios:
no copia silenciosamente ningún juego ni save. Si hay partidas nuevas,
faltantes o conflictivas, pide decisión.

El futuro cambio automático USBSTOR ↔ HID y apagado de pantalla PSP
siguen pendientes de driver kernel, canal host→PSP fuera de FAT, ACK vivo
y una prueba física reversible con Memory Stick descartable. Solo se
implementó la política C pura de restauración de brillo, NO un PRX ni
una transición real. Ver docs/PROTOCOLO-ROLE-BRIDGE-V2.md.

Iniciar-PSP-Dock.cmd abre el XMB en ventana fullscreen de Chrome/Edge
sin barra habitual del navegador cuando están disponibles.
Para reproducir la siguiente sesión HID manual con el selector, consultar
docs/PRUEBA-XMB-PSP-SIGUIENTE.md.

## Estado vigente (01/11/2026) — leer antes de los hitos históricos

**Validación final de esta fase:** 320 pruebas Python OK, compilación de
módulos Python OK y node --check web/app.js OK. El launcher XMB se
comprobó visualmente en Chrome con una demo aislada; el puente USB
automático sigue SIN implementar. El inicio automático de Windows sigue
DESACTIVADO y la biblioteca histórica de Dariusburst se conserva.

PSP Dock persigue un funcionamiento tipo Nintendo Switch: conectar PSP al
PC como almacenamiento, detectar biblioteca y guardados, elegir copias,
sincronizar con consentimiento, cambiar a mando USB, abrir launcher XMB,
jugar varios títulos en PPSSPP, salir del Dock, devolver partidas y
desconectar.

**Ya probado en hardware real:** PSP-2003 con firmware ARK-5 como gamepad
HID PDK1 en Windows; Dariusburst ejecutado y jugado en PPSSPP. Tras
corregir los botones cruzados de DirectInput, el usuario confirmó una
SEGUNDA sesión real con todos los botones correctos y sin lag apreciable.
NO es necesario volver a probar joy.cpl ni el mapeado HID.

**Implementado en Windows y comprobable sin la PSP:** inventario
persistente, comparación y aprobación de copias de ISO/CSO/EBOOT,
comparación de saves y conflictos, transferencia con verificación,
progreso por bytes y cancelación cooperativa sin publicar archivos
parciales ni borrar juegos ya copiados,
ICON0.PNG original de ISO/PBP/CSO v1, perfil PPSSPP privado con controles
correctos y prewarming antes del modo HID, interfaz web XMB, preflight,
mando HID manual y acción de salida
con retorno manual. El simulador experience_simulator.py reproduce el ciclo
completo con pruebas sintéticas que NO justifican el puente USB.

Existe una forma opt-in de mantener PSP Dock al iniciar Windows y abrir el
XMB al conectar USBSTOR. Se implementó en setup_startup.py y los accesos
Activar/Desactivar-inicio-automatico-Windows.cmd, pero **no se instaló
en Windows automáticamente**. Ver docs/INICIO-AUTOMATICO-WINDOWS.md
y docs/PLAN-PRUEBA-FISICA-COMBINADA.md.

El acceso Iniciar-PSP-Dock.cmd reutiliza la biblioteca LIVE original
del contenedor Codex aunque aquella no tenga settings.json, en lugar
de crear otra biblioteca vacía. Si existen varias candidatas, requiere
selección explícita con --data. En el PC se verificó que la biblioteca
histórica contiene Dariusburst y permanece intacta.

Captura verificada de la interfaz demo a 1600×980:
docs/XMB-preview-20261101.png. La demo visual temporal fue cerrada;
el modo demo no se usa para jugar títulos reales.

**Aún falta:** el puente real seguro USBSTOR↔HID en el mismo cable,
exclusión FAT/ACK vivo de PSP, retorno automático al almacenamiento y
verificación física del ciclo integral. Hasta tenerlo, el modo mando se
activa MANUALMENTE en el homebrew probado; la aplicación Windows NO
expulsa ni desmonta la PSP y las partidas solo se devuelven con aprobación.
No ejecutar los homebrews antiguos legacy-unsafe. Consultar HANDOFF.md
para el estado autoritativo y docs/PRUEBA-INTEGRAL-SIMULADA.md para tests.

### Historial de etapas (texto posterior no necesariamente vigente)

**Resultado actual:** corrección del mapeo DirectInput
aplicada y verificada en PC. Suite Python 213 OK.
No se modificaron la PSP, sus partidas ni el
controlador HID. Comprobar el nuevo mapeo cuando
se vuelva a jugar en PPSSPP.

**NUEVO HITO:** el usuario jugó realmente Dariusburst
en PPSSPP controlado por la PSP HID. Todos los botones
respondieron, pero cuadrado físico enviaba círculo virtual.
Se corrigió el mapeo DirectInput de los ocho controles
físicos en el perfil privado de PPSSPP, con backup
verificado y sin tocar PSP/firmware/guardados.
El launcher ahora evita abrir juegos con mapeo viejo.
Falta comprobación física del ajuste en la siguiente partida.
Leer docs/PPSSPP-PSP-BOTONES-CORREGIDOS.md.

**Estado de pruebas actual:** **205 tests Python OK** y
comprobación de sintaxis JS correcta. El lanzador
`Jugar-PPSSPP-con-PSP-HID.cmd` espera la llegada
de una sesión HID PDK1 válida y entonces abre PPSSPP
automáticamente con Dariusburst ya copiado en el PC.
Entorno privado y guardados aislados preparados. No
realiza ningún cambio USBSTOR→HID, expulsión, escritura
en la Memory Stick real ni sincronización de guardados.
El gameplay HID dentro de PPSSPP aún necesita
su primera prueba física.
**ACTUALIZACIÓN: botones/joystick HID confirmados por el usuario
y gameplay PPSSPP preparado.** La captura roja de `joy.cpl`
no significaba doce botones pulsados: el usuario comprobó
que respondían individualmente, igual que cruceta y stick.
No se necesita repetir ninguna prueba de controles Windows.
Se creó `ppsspp_hid_session.py` y los accesos
`Preparar-PPSSPP-para-PSP-HID.cmd` /
`Jugar-PPSSPP-con-PSP-HID.cmd`. La copia local
de Dariusburst y un PPSSPP privado 3× Direct3D11
ya están preparados en el PC. El lanzamiento exige
PDK1 conectado, ausencia de volumen USBSTOR
y sesiones de saves sin conflictos. Durante la prueba,
los saves se almacenan SOLO en el PC. Procedimiento
en `docs/PPSSPP-CON-PSP-HID.md`; el gameplay
con HID está pendiente de prueba física.

**Validación reciente:** 195 tests Python OK. Visores de botones
HID y WinMM preparados. PSP de nuevo en modo almacenamiento
Sony; K1 Proof OFF y sin ARM; K1/VSH ON. Próximo ensayo útil:
comprobar botones reales y control de PPSSPP, no repetir la
enumeración HID ya validada.

**HITO REAL LOGRADO (01/11/2026):** la PSP funciona como
gamepad HID PDK1 en Windows (`"PSP" Type D` en `joy.cpl`)
y el registro conserva DOS sesiones HID válidas. El módulo
kernel K1 también respondió físicamente a `pspDockK1Read`
con prueba `K1PROOF.TXT` válida. Se retiró ARM y se
desactivó únicamente K1 Proof; VSH y K1 continúan ON.
La PSP volvió al almacenamiento Sony USB normal.
Todavía faltan verificación de **botones individuales**,
integración DirectInput con PPSSPP y automatizar el
handoff USBSTOR↔HID con exclusividad FAT. Consultar
`docs/HITO-HID-REAL-20261101.md` y el primer apartado
de `HANDOFF.md`. Las entradas siguientes son históricas.

**PRÓXIMA PRUEBA FÍSICA COMBINADA (01/11/2026):**
K1 Proof ON ya ha sido **armado con backup verificado** para
medir la exportación real del kernel en un único reboot sin cable.
Se ha instalado también `PSP Dock - HID Only LAB` en una carpeta
separada de Juegos, **sin autoarranque ni tocar USBSTOR**,
para comprobar el mando HID en Windows desde GAME mode.
Lanzar `Monitor-HID-Only-PSP.cmd` y seguir estrictamente
`docs/PRUEBA-COMBINADA-K1-HID.md`. Pruebas Windows actuales:
**186 OK** + compilación PSPDEV y prueba C nativa del gate.
**La transición automática USBSTOR↔HID y FAT ownership siguen pendientes.**
El historial siguiente refleja etapas anteriores.

**ACTUALIZACIÓN 01/11/2026 — nuevo checkpoint K1 Proof:** el usuario
confirmó que el primer arranque K1 ON funcionó y Windows lo reconoció
de nuevo. Se ha añadido `homebrew/k1-proof-vsh` con import real
de `pspDockK1Read` y NID `0xC81C4981`. El nuevo PRX se ha instalado
en PSP real y está **ON sin ARM**, con backup y recuperación exacta;
**su primer arranque físico todavía NO se ha probado**. La suite
Python supera **178 tests**. No se ha iniciado HID, expulsión USB
experimental ni transferencia de saves de prueba. Consultar
`HANDOFF.md` y `docs/PRIMER-ARRANQUE-K1-PROOF.md` antes de
seguir; el historial inferior contiene estados anteriores.

**ACTUALIZACIÓN MÁS RECIENTE (01/11/2026):**
`usb_transport_observer.py` observa de forma pasiva identidad Sony,
estado PnP/almacenamiento y candidatos HID. Ejecutado sobre la PSP real:
almacenamiento `G:\` operativo, PnP código 0, cero HID, sin conmutación.
Se ha compilado también `homebrew/kernel-usb-timeline-k2` para
registrar transiciones USB en RAM, pero **K2 no está instalado**.
El kernel K1 continúa ON **en configuración** sin confirmación
explícita del primer arranque físico. No expulsar automáticamente la
PSP: PnP eject del padre podría exigir desconexión física. Suite
**165 tests OK**. Consultar `HANDOFF.md`,
`docs/HANDSHAKE-USB-SIN-DESCONECTAR.md` y
`docs/PRIMER-ARRANQUE-K1-KERNEL.md`; las entradas siguientes son
históricas.

**SIGUIENTE PRUEBA REAL PENDIENTE (01/11/2026):** módulo
`PSPDockKernelK1/pspdockk1observer.prx` instalado y configurado
**ON** en la PSP-2003; sigue sin arrancar físicamente con esta
configuración. El VSH original continúa ON y sin ARM.
Los scripts `k1_plugin_stage.py`, `k1_plugin_enable.py`,
`k1_plugin_recovery.py` y `k1_module_check_install.py`
prepararon el cambio de forma reversible, con backups verificados
del serial USB de la PSP. Pruebas Python: **155 tests OK**.
El binario K1 solo consulta estados USB en memoria; ningún HID
instalado ni cambio de rol efectuado. **Consultar
`HANDOFF.md` y `docs/PRIMER-ARRANQUE-K1-KERNEL.md` para el primer
arranque y recuperación**. El texto antiguo a continuación es
histórico y no refleja que K1 ya está ON en configuración.

**ESTADO ACTUAL (31/10–01/11/2026):** PSPDockVSH comprobado en hardware
real. El usuario verificó XMB estable en varios arranques y, con ARM
voluntario, el observador generó BOOT.TXT válido con USB=0x00000111,
BUS=0x00000001 y STORAGE=0x80243005. Se guardó copia verificada en PC,
se retiró `ARM.TXT` de la PSP y se conservaron los PRX, la entrada VSH
en ON y el marcador BOOT. Backup CRITICAL verificado (40 archivos).
El controlador HID derivado de FuSa compila y su protocolo pasa tests
nativos, pero **NO se ha probado HID físicamente ni instalado el kernel**.
`usb_handoff.py` requiere una prueba de rol/fat/HID real, no admite el
marcador de observador como permiso para expulsar USB.
Se ha preparado también `homebrew/kernel-usb-observer-k1`, PRX kernel
de observación **solo en memoria**, compilado PSPDEV y NO instalado en
la PSP. **141 tests Windows OK**. Consultar primero `HANDOFF.md` y
`docs/HITO-USBSTOR-HID.md`. Los avances más antiguos que aparecen
debajo son históricos, y algunos describen pruebas ya superadas.

**Nuevo avance (31/10/2026):** compilado el primer plugin pasivo
para el XMB de ARK-5 en homebrew/vsh-observer, junto con un
esqueleto kernel inerte reservado para una fase futura. Se han
preparado scripts Windows de vista previa, instalación **OFF**,
armado explícito y consulta de estado. NINGUNO se ha ejecutado
sobre la PSP real; no se ha habilitado autorun ni modificado el
firmware ni las partidas. El proyecto mantiene el objetivo
de conectar la PSP y usarla como mando de PPSSPP, pero USBSTOR
y HID no se han conmutado aún. Ver
docs/PSP-ARK5-VSH-STAGING.md. Suite total: 105 pruebas aprobadas.

**Avance de la orquestación:** creado usb_handoff.py como máquina de
estados puramente lógica para el dock tipo Switch, con tests de
desconexiones, expulsión segura, sesiones HID, PPSSPP y retorno
al mismo volumen USB. Está diseñado para conectarse a los adaptadores
Windows y un futuro plugin VSH de ARK-5, pero NO ejecuta aún ninguna
transición real. Véase docs/DOCK-FSM-AND-VSH.md. La prueba USB Bus
0.8 ya superó la validación física en PSP: Start=0, bus on=1,
Stop=0 y bus off=2. USBSTOR y HID todavía sin probar en hardware.

**Último avance técnico, 31/10/2026:** foto de PSP real confirmó
USB Driver Status 0.7: bus detenido (0x00000002) y USBStor_Driver
sin registrar (0x80243005 / SCE_USB_ERROR_NOENT). Carga, inicio,
parada y descarga del PRX USER fueron correctos. Se preparó
USB Bus Test 0.8, una prueba CONTROLADA que solo puede intentar
start/stop del bus USB sin cable, sin activar almacenamiento y
sin acceder al FAT. Pruebas de seguridad nativas y suite Python
superadas; PPSSPP reconoce el nuevo EBOOT. **No instalado ni
probado en PSP física.** Véase
homebrew/usb-bus-cycle-v08/README.md. No ejecutar los probes
USB 0.1/1.0 que provocaron apagados.

**Incidente reciente (31/10/2026):** USB Step Probe SAFE también provocó
pantalla negra/apagado ANTES de mostrar su interfaz en PSP real, igual que
el probe anterior. La versión sin USB funcionó. Se confirmó un problema
estructural de imports (ForKernel y sceUsbstorBoot en EBOOT USER);
ver [análisis y nueva arquitectura](docs/IMPORT-GATE-PSP.md).
Ambos probes USB antiguos están retirados y su reinstalación bloqueada.
Se preparó USB Import Gate 0.5, con interfaz USER sin USB y módulo USB de
lectura diferida: compilado y probado al arrancar en PPSSPP, todavía NO
instalado ni probado en PSP real. No activar USBSTOR ni expulsión experimental.

**IMPORTANTE — HOME BREW USBSTOR ANTIGUO FALLA:** PSPDockProbe 0.1 (el
que se instaló en la consola) provocó **pantalla negra y apagado** en PSP-2003
real. **No volver a abrirlo.** Se archivó su ejecutable anterior en el PC
y se prohibió recompilarlo; la copia antigua de PSP NO se ha tocado porque
la consola no estaba conectada. La prueba correcta ahora empieza por
[Startup Check SAFE](docs/FALLO-HOMEBREW-PSP.md), que no llama a USB:
primero instalarlo con Instalar-prueba-pantalla-PSP.cmd cuando la PSP vuelva
a aparecer en almacenamiento. Solo si arranca y sale correctamente se
continúa con USB Step Probe. Ambos EBOOT nuevos se han compilado y PPSSPP
1.20.4 los reconoce, **sin prueba aún en hardware físico**.

**Para continuar el trabajo, leer primero [HANDOFF.md](HANDOFF.md).** Ya se conectó la PSP real: 34 entradas, 6 carpetas de guardados importadas y Dariusburst transferido y abierto. El cambio automático de USB a mando sigue pendiente; las notas iniciales de validación se complementan con el handoff.

Proyecto local para una PSP-2003 con ARK-5 sobre 6.61, según la información del propietario. Incluye launcher con estética inspirada en XMB, servicio local, sincronización de partidas y PPSSPP portable 1.20.4 oficial. **Todavía no es un dock automático terminado:** el cambio almacenamiento ↔ mando y la aceptación con una PSP real siguen pendientes.

**Continuación Chat on Steroids (31/10/2026):** se compiló el controlador HID y el homebrew de conmutación en modo laboratorio (homebrew/dock-client/dist), y se añadieron utilidades de diagnóstico de Windows (psp_usb.py, Probar-expulsion-USB.cmd, Probar-retorno-USB.cmd). **No se instaló el nuevo cliente en la PSP ni se demostró aún la conmutación.** Antes de usar las utilidades de laboratorio, leer [el procedimiento y las precauciones](docs/PRUEBA-PUENTE-USB.md). La última suite tiene 42 pruebas aprobadas.

## Abrir

- `Ver-demostracion.cmd`: interfaz con tres títulos y una partida sintéticos, en una carpeta separada. Permite probar la copia y las confirmaciones; rechaza ejecutar esos archivos como juegos.
- `Iniciar-PSP-Dock.cmd`: modo real. Abre el navegador y examina unidades extraíbles mientras el programa permanece abierto. Requiere Python 3.12 o posterior, disponible en este equipo. Elige un puerto libre automáticamente.
- Ajustes: la copia incluida de PPSSPP se selecciona automáticamente al empezar con una configuración nueva. Biblioteca y partidas quedan en `%LOCALAPPDATA%\PSP-Dock\live`. Si usas otra biblioteca, indica su raíz: `ISO/...` y `PSP/GAME/...`.

No se instala un servicio, no se cambia el inicio de Windows, no se modifica ARK ni se copia homebrew a la consola. No se descargan juegos ni firmware. El proceso termina al cerrar su ventana de consola; cerrar solo la pestaña deja el servicio activo.

## Qué funciona en el prototipo

| Función | Implementación y evidencia |
|---|---|
| Detección | Sondeo Windows cada 3 segundos de unidades extraíbles con carpeta `PSP`; son candidatas, no identificación criptográfica de la consola. Probado el estado sin PSP. Detección física pendiente. Con varias unidades se pide elegir. |
| Inventario | ISO/CSO dentro de `ISO`, y `EBOOT.PBP` dentro de `PSP/GAME`. Nombres de archivos/carpetas; aún no lee títulos, carátulas ni categorías de PARAM.SFO. |
| Comparación | SHA-256 de contenido; detecta un juego idéntico con otro nombre dentro de la biblioteca. ISO y CSO equivalentes con distinta compresión cuentan como contenidos distintos. |
| Transferencia | Confirmación en el launcher. Solo ausentes. Copia provisional, verifica hash y publica el archivo completo. EBOOT lleva su carpeta entera y auxiliares. No sobrescribe juegos diferentes. |
| Partidas | Comparación de tres versiones por carpeta completa, sin mezclar archivos. Sincronización automática mientras la unidad está disponible y PPSSPP cerrado. Conflictos requieren elegir PSP o PC, con backup del destino. |
| Protección | Sin propagación automática de borrados. Baseline por número de serie del volumen Windows. Sesión pendiente vinculada a ese volumen; no se devuelve a otra tarjeta. Rechaza carpetas solapadas, enlaces y junctions. |
| Interrupciones | Publicación por renombrado con registro de recuperación y copia previa. Un registro pendiente bloquea las escrituras y el lanzamiento. La resistencia a corte físico de alimentación FAT aún necesita probarse. |
| Launcher | Navegación con flechas/Enter, ratón y Gamepad API. No da por verificado un mando PSP ni su mapeo en PPSSPP. |
| PPSSPP | Copia privada de ejecutable y assets; `installed.txt` propio apunta a la memoria del Dock. Perfil inicial 3× (1440×816 internos), Direct3D 11, pantalla completa. Sin DLSS. Arranque real documentado por separado. |

Las partidas son **guardados del juego** en `PSP/SAVEDATA`; los savestates del emulador no se llevan a la PSP. Que una carpeta EBOOT aparezca en el inventario no prueba su compatibilidad: homebrew, contenido PSN y EBOOT de PS1 requieren evaluación individual. PPSSPP 1.20.4 no ofrece la futura ruta PS1 de 1.21.

## Flujo normal y límite actual

1. Con Dock abierto, conectar la PSP con almacenamiento USB activo. `USB Auto Connect`, si se habilita en la consola, ayuda a entrar desde el XMB; no convierte una partida PSP en una sesión emulada.
2. Dock inventaría, compara y sincroniza las partidas sin conflicto. Solo pide confirmar transferencias de juegos ausentes y resolver partidas divergentes.
3. Seleccionar un juego y jugar en PPSSPP con la memoria privada del Dock. El prototipo admite iniciar mientras está en almacenamiento para verificar la ruta PC, usando los controles que tenga PPSSPP configurados.
4. Para usar la PSP como mando con FuSa hay que pasar a ese homebrew: **esa transición no está automatizada**. Mientras FuSa está activo la unidad deja de estar disponible; Dock conserva el catálogo en memoria durante esta ejecución.
5. Al cerrar PPSSPP, Dock marca la devolución pendiente. Cuando regresa el almacenamiento de la **misma** tarjeta, sincroniza. Desenchufar antes de devolver los guardados no los lleva a la consola: se conservan en el PC y se muestran pendientes.

FuSa se documenta y examina, pero no se incluye instalado ni se ejecuta en la PSP. La navegación del launcher por Gamepad API y la entrada DirectInput de PPSSPP son dos rutas distintas: falta comprobar detección, ejes, botones y mapeo en ambas.

## Si hubo una interrupción

- Si Dock se cerró con PPSSPP abierto: cerrar ambos y ejecutar `Recuperar-sesion.cmd`. Comprueba que no exista ningún proceso PPSSPP antes de quitar el bloqueo; conserva la devolución pendiente. Después abrir Dock y conectar la tarjeta original.
- Si aparece un registro `.dock-journal-*.json` en cualquiera de las carpetas SAVEDATA: cerrar Dock y PPSSPP. El registro contiene las rutas `target`, `stage`, `rollback` y `backup`. Conservar una copia de las carpetas y del registro antes de decidir qué versión recuperar. No borrar registros a ciegas: el programa deliberadamente no elige una partida tras un fallo.
- Las versiones sustituidas quedan en `live/backups`, con carpeta e identificador único. Para restaurar, cerrar ambos programas, conservar la versión actual y copiar la carpeta completa elegida; al reconectar se vuelve a comparar y puede aparecer conflicto.

El bloqueo cubre las instancias del Dock con la misma carpeta de datos y los procesos que inicia. No coordina otro PPSSPP abierto por fuera del Dock sobre la misma memoria. Usa la memoria privada solo desde este proyecto. El número de volumen distingue tarjetas, no garantiza distinguir clones de una tarjeta; un formato nuevo exige nuevo emparejamiento.

## Validación y desarrollo

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
python dock.py --demo
```

No requiere paquetes de Python ni Node para ejecutarse; Node solo se usa para comprobar el JavaScript. El navegador accede únicamente al servidor en `127.0.0.1`. Las mutaciones requieren Origin local exacto y token de sesión. No se permiten peticiones remotas ni lecturas arbitrarias de archivos.

Consulte [viabilidad y diseño del puente](docs/VIABILIDAD.md), [evidencia de validación](docs/VALIDACION.md) y [salida de pruebas](docs/tests.txt). Se entregan fuentes editables; sin publicación, instalación de firmware ni modificación de discos.

## Componentes externos

`tools/PPSSPP` contiene la distribución Windows x64 oficial 1.20.4 obtenida de https://www.ppsspp.org/files/1_20_4/ppsspp_win.zip. Se conserva su README. PPSSPP es software GPL-2.0-or-later; fuentes de esa versión: https://github.com/hrydgard/ppsspp/tree/v1.20.4. No se incorpora código de FuSa a este proyecto. Las referencias a PSP y XMB describen la plataforma y la inspiración visual; no se incluyen recursos gráficos de Sony.
