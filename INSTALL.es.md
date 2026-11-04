# Tu primer Dock, paso a paso

[English](INSTALL.md) · **Español**

PSP Dock funciona con **dos descargas complementarias**: una para Windows y otra para la PSP. Es normal que la PSP necesite dos archivos de programa: el `EBOOT.PBP` que aparece en Juegos y el módulo `docknative.prx` que se encarga de la conexión.

## 1 · Prepara Windows

Descarga **[PSP-Dock-Windows-v0.1.2.exe](https://github.com/jalinhabrava/Psp-dock/releases/latest/download/PSP-Dock-Windows-v0.1.2.exe)** desde GitHub Releases. Guárdalo donde quieras y ejecútalo: es una aplicación portátil y **no requiere instalar Python** ni dejar un servicio ejecutándose continuamente.

Para jugar necesitarás [PPSSPP para Windows](https://www.ppsspp.org/download/) obtenido por separado. En la configuración de PSP Dock puedes indicar dónde lo tienes y elegir las carpetas de juegos y guardados. PSP Dock no incluye juegos ni software propietario de Sony.

El ejecutable todavía no dispone de firma de editor para Windows. Antes de ejecutar descargas de terceros, comprueba que vienen del repositorio oficial y su SHA-256 en los detalles de la release. **No necesitas instalar un controlador de Windows nuevo ni modificar los ajustes USB globales de tu PC para este paquete.**

## 2 · Prepara tu PSP

Descarga **[PSP-Dock-PSP-v0.1.2.zip](https://github.com/jalinhabrava/Psp-dock/releases/latest/download/PSP-Dock-PSP-v0.1.2.zip)**. Conecta tu PSP en modo almacenamiento USB de su menú original o coloca la tarjeta en un lector.

En una **instalación nueva**, copia la estructura completa del ZIP a la raíz de la Memory Stick. Debe quedar:

```text
Memory Stick/
└── PSP/
    └── GAME/
        └── PSPDockNative/
            ├── EBOOT.PBP
            ├── docknative.prx
            ├── LICENSE-FuSa-GPL-3.0.txt
            └── LICENSE-DejaVu.txt
```

El ZIP incluye además un manifiesto de integridad y un archivo de instrucciones. El EBOOT, por sí solo, **no es suficiente**: necesita `docknative.prx` y los avisos de licencia en la misma carpeta.

**Si PSPDockNative ya existe**, no copies encima sin más. Haz una copia de seguridad de la aplicación anterior y tus partidas y utiliza el procedimiento de actualización verificada documentado en [psp_native_upgrade.py](psp_native_upgrade.py). Estas herramientas de actualización también requieren verificar la PSP y el backup. No instales dos versiones distintas en la misma carpeta.

La distribución no modifica la memoria flash de la PSP, ni instala plugins ARK. Está dirigida a consolas compatibles con homebrew; la referencia principal de pruebas es PSP-2003, firmware 6.61 y ARK-5. Otras PSP o firmwares pueden necesitar trabajo adicional.

## 3 · Disfruta

Abre primero PSP Dock en Windows y después PSP Dock en la consola. Conecta el cable USB, sigue las indicaciones para inventariar tu biblioteca y elige qué copiar.

Cuando el Dock confirme el modo mando, podrás navegar por el XMB del PC con la PSP, elegir un juego y jugar desde PPSSPP. Al acabar, **cierra el juego normalmente**. Antes de cerrar el Dock, revisa las partidas pendientes de devolver y espera la confirmación de que se han verificado.

No desconectes el cable mientras el programa esté copiando o comprobando archivos.

### Idioma

El ejecutable de Windows y el EBOOT incluyen **inglés y español**, sin descargas diferentes por idioma. Windows utiliza el idioma del navegador/interfaz y permite cambiarlo desde **Ajustes → Idioma**. La PSP utiliza el idioma que hayas seleccionado en sus **Ajustes del sistema → Idioma del sistema**.

## ¿Algo no funciona?

Si no aparece la biblioteca, comprueba que el PC detecta la PSP y que la consola está ejecutando el homebrew compatible. Para partidas que hayan quedado pendientes de sincronización, **no borres los archivos**: vuelve a abrir PSP Dock y sigue el aviso de recuperación. Puedes describir el problema en [Issues](https://github.com/jalinhabrava/Psp-dock/issues), indicando versión de Windows, firmware PSP, fase de conexión y pasos para reproducirlo, sin adjuntar tus partidas ni datos privados.

**Créditos y licencias:** [LICENSE.md](LICENSE.md) · [NOTICE.md](NOTICE.md) · [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
