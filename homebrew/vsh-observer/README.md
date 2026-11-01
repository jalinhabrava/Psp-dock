# PSP Dock VSH Observer 0.1 — primer plugin para el XMB

ESTADO: compilado y auditado; NO instalado ni activado en PSP real.
No es aún el plugin que gestionará USBSTOR/HID.

## Comportamiento

- PRX de usuario para ARK-5, en contexto VSH, sin imports directos USB/ForKernel.
- Crea un hilo que espera tres segundos y termina si no existe el
  consentimiento ARM.TXT exacto.
- Con ARM válido, carga dinámicamente el PRX auxiliar USB Status v0.7
  que ya se probó físicamente, consulta estados USB y lo descarga.
- Solo si USB no está activado ni conectado al cable ni al host,
  escribe un marcador de diagnóstico único BOOT.TXT en su propia carpeta.
- NO inicia o detiene USB, NO carga almacenamiento USBStor,
  NO habilita HID, NO toca firmware, NO accede a partidas guardadas.

La escritura del marcador es opcional y tiene una ventana de carrera
entre leer estado USB y abrir BOOT.TXT. Requiere autorización ARM
expresa y una copia previa de los datos importantes de la Memory Stick.
El archivo no confirma propiedad exclusiva de FAT ni autoriza
el uso de HID o sincronización.

## Compilación

Ejecutar bash homebrew/vsh-observer/build-wsl.sh con PSPDEV.
El build comprueba símbolos/importaciones y formato del protocolo.

SHA-256 del PRX:
ea5974c6d3a14f70069265a5c865c5a1b55e2df5737ea864776440dd3bebaf63

El segundo binario reutiliza el auxiliar físicamente probado v0.7
con SHA-256:
dcb2a33e7418a8f4d48d93485c9d53db2173e0bf059a857caf8f440d80dff457

## Instalación preparada, DESACTIVADA

El comando Ver-preview-plugin-VSH.cmd no escribe nada.

Preparar-plugin-VSH-desactivado.cmd requiere confirmación
STAGE DISABLED y solo entonces copia ambos PRX en una carpeta nueva:

ms0:/SEPLUGINS/PSPDockProbe/

Crea una copia de seguridad del PLUGINS.TXT previo en el PC y añade
esta entrada, que ARK-5 reconoce pero NO carga:

vsh, ms0:/SEPLUGINS/PSPDockProbe/pspdockvsh.prx, off

La preparación NO genera ARM.TXT y NO habilita el plugin. Otros
plugins y SAVEDATA no se modifican.

Armar-observador-VSH-con-confirmacion.cmd crea ARM.TXT solo
con consentimiento expreso, binarios correctos y plugin OFF.
NO lo ejecutaremos hasta validar antes las vías de recuperación.
Consultar-plugin-VSH.cmd lee solamente estado/BOOT.TXT.

## Recuperación si se prueba habilitarlo en otra fase

Se revisó ARK-5 commit 5fa0273150754413b683443ac395a7e33cd14c12.
SystemControl/src/controller.c desactiva plugins cuando START está
pulsado en la comprobación de arranque; SystemControl/src/init.c
realiza esa comprobación antes de cargar plugins en sceMediaSync.

Si aparece pantalla negra en un futuro test habilitado, mantener
START durante arranque, deshabilitar desde ARK Plugin Manager
y restaurar OFF en PLUGINS.TXT. La PSP-2000 permite retirar la
Memory Stick y editarla con un lector externo si hace falta.
Confirmar el comportamiento de recuperación en la versión concreta
instalada antes de habilitar el plugin por primera vez.

La fase de plugin VSH habilitado NO se ha probado en esta conversación.
