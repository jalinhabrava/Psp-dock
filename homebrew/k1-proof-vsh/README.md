# PSP Dock — K1 export proof (kernel, VSH, laboratorio)

Módulo experimental nuevo que enlaza contra el **exportado real**
`pspDockK1Read` del K1 ya instalado y probado en el XMB. Usa el
NID `0xC81C4981` generado por `psp-build-exports` desde la
tabla original `homebrew/kernel-usb-observer-k1/exports.exp`.

**No es el gestor USBSTOR↔HID.** No arranca/detiene USB, no cambia
controladores, no modifica firmware y nunca accede a SAVEDATA.

Al cargar en VSH crea un hilo que espera seis segundos. El indicador
`ARM.TXT` independiente de K1Proof está ausente por defecto y el
módulo termina sin generar archivos. En un ensayo posterior, con
ARM explícito y solo si no existe USB activo/cable o driver de
almacenamiento iniciado, ejecuta `pspDockK1Read`, verifica
magic/version/estado/capacidades y deja una **única prueba diagnóstica**
`K1PROOF.TXT` en su propia carpeta, sin sobrescribir archivos previos.

Este texto es retrospectivo, no autoriza cambiar roles ni constituye
un ACK de liberación FAT.

**Fases necesarias:** compilar y auditar imports; instalar con plugin
OFF, luego ensayo ON *sin ARM* para confirmar XMB estable; solo después
prueba ARM controlada y recuperación. Si no resuelve el import de K1,
ARK debería omitir la carga del módulo sin alterar el original, pero
no debe asumirse compatibilidad sin hardware real.

Nunca reemplazar el K1 ni VSH anteriores y nunca activar un driver
HID durante este ensayo.
