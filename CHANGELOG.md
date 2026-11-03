# Historial de versiones

## v0.1.1 — Descargas para Windows y PSP · 4 de noviembre de 2026

Primera release con archivos listos para descargar: ejecutable portátil para Windows (sin exigir Python al usuario) y paquete PSP 0308 que incluye EBOOT.PBP, docknative.prx y licencias. Se acompaña de una [guía de instalación](INSTALL.md). Nuevo README con un mensaje más cercano y banner generado a partir del logotipo original aprobado de PSP Dock.

**Importante:** el ejecutable Windows todavía no está firmado digitalmente y no incluye PPSSPP; la versión de PSP requiere homebrew compatible y debe verificarse en cada consola. No se ha cambiado el protocolo, ni el código de sincronización o de guardados de la versión 0.1.0 por esta publicación de binarios.

## v0.1.0 — Primera publicación del código · 3 de noviembre de 2026

**Windows (MVP):** XMB con animaciones medidas en navegador real; biblioteca cacheada por identidad de Memory Stick; Sony USBSTOR y mando PSP HID con flujo de sesión nativa 0308; decisión explícita de copias; guardados PSP/SRAM compatibles con comparación, consentimiento y verificación de retorno; imágenes, fondos y sonidos originales importables desde la consola cuando son compatibles; soporte de PPSSPP/RetroArch.

**Código incluido, experimental:** interfaz premium del homebrew; pipeline híbrido de texturas HD y pruebas deterministas DZIP (no se declara descifrado DZIP ni compatibilidad universal); prototipo Android TV con motor integrado en una sola aplicación, aún sin validación hardware.

**No integrado:** el trabajo Linux se conserva en la rama separada `feat/linux-dock` porque aún supone protocolo nativo 0301 frente al 0308 del MVP. No debe instalarse como si fuera compatible con esta versión.

**Validación conocida:** el usuario confirmó un ciclo completo de conexión y salida en hardware PSP para Windows. Las pruebas automáticas del host/cliente (incluidas las de fotogramas intermedios del XMB mediante Chromium) no sustituyen las pruebas físicas de cada función, ni constituyen una distribución instalable firmada.

**Licencia:** PolyForm Noncommercial 1.0.0 para el código original, con crédito obligatorio a `jalinhabrava`; software derivado de FuSa GAMEPAD bajo GPL-3.0 y otros materiales de terceros bajo sus propias licencias. Consulta [NOTICE.md](NOTICE.md) y [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
