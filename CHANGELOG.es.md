# Historial de versiones

**[English](CHANGELOG.md)** · **Español**

## Pendiente de nueva versión — Solo el código final

- `main` conserva únicamente el programa Windows, el homebrew PSP 0308 y las herramientas necesarias para compilar, instalar, actualizar y respaldar esa versión.
- Se han retirado los experimentos USB/HID/K1/VSH, los firmwares antiguos, los scripts de laboratorio y sus pruebas obsoletas. Android TV continúa disponible en su rama de desarrollo, pero no mezclado con la versión final.
- Las dependencias y licencias del homebrew final están ahora en su propia carpeta: la compilación auditada del EBOOT es idéntica a la publicada en la v0.1.2.
- No cambia el protocolo entre PC y PSP, el catálogo de juegos, las transferencias ni la sincronización de partidas. El código anterior puede recuperarse desde el historial Git.

## v0.1.3 — Juega a tus ROMs sin pasos innecesarios · 4 de noviembre de 2026

- RetroArch y sus núcleos compatibles permitidos se preparan **en segundo plano en Windows** al detectar emuladores PSP. Las instalaciones antiguas con política manual pueden iniciarlo al seleccionar el emulador, sin pedir que la PSP vuelva a almacenamiento USB.
- El XMB muestra el progreso en cada icono, con una animación discreta o un indicador fijo si se prefiere reducir el movimiento.
- Selecciona emulador → abre el menú lateral de ROMs → selecciona una ROM preparada para jugar en RetroArch, conservando las comprobaciones del mando y los guardados.
- Las ROMs que falten en el PC requieren una copia USB aprobada expresamente. No se modifican instalaciones externas de RetroArch ni se descargan núcleos restringidos, ROMs o BIOS.
- Prueba real PC-only: RetroArch 1.22.2 oficial y el núcleo mGBA se descargaron, extrajeron y verificaron en una carpeta temporal aislada; se limpió después, sin ejecutar el contenido descargado ni conectar la PSP.
- No se ha cambiado el firmware, el EBOOT, el protocolo USB ni la lógica de sincronización de partidas.

## v0.1.2 — Un mismo Dock, dos idiomas · 4 de noviembre de 2026

- El mismo ejecutable de Windows y el mismo EBOOT incluyen **inglés y español**; no necesitas instalar versiones separadas ni mover los guardados al cambiar de idioma.
- Windows detecta el idioma del navegador/interfaz y permite cambiarlo manualmente en Ajustes del XMB.
- PSP Dock para PSP utiliza el idioma configurado en los ajustes del sistema de la consola.
- README, guía de instalación y banner tienen versiones inglesa y española.
- No se han modificado las fases USB, el protocolo nativo 0308 ni el comportamiento de copia y sincronización.

La traducción se comprueba mediante pruebas de software, sin que eso sustituya la validación con todas las combinaciones de consolas y ordenadores.

## v0.1.1 — Descargas para Windows y PSP · 4 de noviembre de 2026

Primera release con archivos listos para descargar: ejecutable portátil para Windows (sin exigir Python al usuario) y paquete PSP 0308 que incluye EBOOT.PBP, docknative.prx y licencias. Se acompaña de una [guía de instalación](INSTALL.md). Nuevo README con un mensaje más cercano y banner generado a partir del logotipo original aprobado de PSP Dock.

**Importante:** el ejecutable Windows todavía no está firmado digitalmente y no incluye PPSSPP; la versión de PSP requiere homebrew compatible y debe verificarse en cada consola. No se ha cambiado el protocolo, ni el código de sincronización o de guardados de la versión 0.1.0 por esta publicación de binarios.

## v0.1.0 — Primera publicación del código · 3 de noviembre de 2026

**Windows (MVP):** XMB con animaciones medidas en navegador real; biblioteca cacheada por identidad de Memory Stick; Sony USBSTOR y mando PSP HID con flujo de sesión nativa 0308; decisión explícita de copias; guardados PSP/SRAM compatibles con comparación, consentimiento y verificación de retorno; imágenes, fondos y sonidos originales importables desde la consola cuando son compatibles; soporte de PPSSPP/RetroArch.

**Código incluido, experimental:** interfaz premium del homebrew; pipeline híbrido de texturas HD y pruebas deterministas DZIP (no se declara descifrado DZIP ni compatibilidad universal); prototipo Android TV con motor integrado en una sola aplicación, aún sin validación hardware.

**No integrado:** el trabajo Linux se conserva en la rama separada `feat/linux-dock` porque aún supone protocolo nativo 0301 frente al 0308 del MVP. No debe instalarse como si fuera compatible con esta versión.

**Validación conocida:** el usuario confirmó un ciclo completo de conexión y salida en hardware PSP para Windows. Las pruebas automáticas del host/cliente (incluidas las de fotogramas intermedios del XMB mediante Chromium) no sustituyen las pruebas físicas de cada función, ni constituyen una distribución instalable firmada.

**Licencia:** PolyForm Noncommercial 1.0.0 para el código original, con crédito obligatorio a `jalinhabrava`; software derivado de FuSa GAMEPAD bajo GPL-3.0 y otros materiales de terceros bajo sus propias licencias. Consulta [NOTICE.md](NOTICE.md) y [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
