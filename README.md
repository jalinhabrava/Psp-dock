# PSP Dock

**Primera versión pública del proyecto · v0.1.0 (MVP para Windows)**

PSP Dock convierte una PSP compatible en un mando para jugar a su biblioteca desde el ordenador, con un lanzador de estilo XMB, PPSSPP y sincronización controlada de partidas. No incluye juegos, BIOS, firmware de Sony ni emuladores propietarios.

## Estado de la primera versión

**Windows es la plataforma principal.** El 3 de noviembre de 2026 se completó una sesión real de conexión, inventario, elección de copia, uso del mando y salida con retorno de partidas. El XMB y su navegación fluida tienen, además, comprobaciones de animación en Chromium. No todas las opciones y combinaciones de juegos tienen todavía aceptación física independiente.

| Componente | Estado |
|---|---|
| Windows + PSP Dock nativo 0308 | MVP funcional; un ciclo completo observado en hardware |
| Inventario persistente | Recuerda catálogos y evita releer juegos que no han cambiado |
| XMB de Windows | Navegación con cruceta, transiciones animadas, opciones y sonidos importados de la PSP |
| PPSSPP | Lanzamiento desde la biblioteca del PC y retorno al XMB al cerrar el juego |
| Guardados y copias | Comparación, confirmación, verificación y recuperación; no sobrescribe partidas silenciosamente |
| Homebrew de PSP | Fuente del firmware nativo 0308, más pruebas/variantes históricas |
| RetroArch / otras consolas | Adaptadores y guardados SRAM compatibles; validar cada emulador/perfil |
| Texturas HD | Captura y motor determinista experimentales; DZIP no se declara descifrado |
| Android TV | Código experimental incluido; **no** es una versión instalable validada |
| Linux | Desarrollo **separado**: su rama usa el protocolo 0301 y necesita actualizarse a 0308 antes de integrarse |

### Flujo de uso en Windows

1. Instala una versión compatible del homebrew **PSP Dock 0308** en la PSP, conforme a la documentación de [la implementación nativa](homebrew/native-dock/README.md). La instalación del homebrew es una operación separada del arranque del programa de Windows.
2. En Windows (Python 3.12+), abre **Iniciar-PSP-Dock.cmd**. No se instala un servicio residente de inicio obligatorio. Configura las carpetas y PPSSPP en los ajustes si es la primera vez.
3. Abre PSP Dock en la consola y conéctala mediante USB. Espera el inventario y decide desde la PSP qué falta copiar; la elección «Sin copiar ahora» también permite continuar.
4. En modo mando, usa el XMB del ordenador para seleccionar un juego y abrir PPSSPP. Al cerrar el juego se regresa al XMB.
5. Al salir del Dock, conserva el cable conectado y sigue el proceso de devolución/confirmación de los guardados. No desconectes durante las copias o verificaciones.

Para examinar la interfaz sin hardware ni partidas reales, utiliza **Ver-demostracion.cmd**. La demostración no representa una prueba física del protocolo USB.

## Desarrollo y pruebas

El host Windows utiliza Python estándar y una interfaz HTML, CSS y JavaScript. Las fuentes del homebrew PSP están bajo `homebrew/`; sus dependencias de compilación son independientes. Hay pruebas Python y Node:

```shell
python -m unittest tests.test_device_memory tests.test_dock_product_session tests.test_native_dock -q
node --test "tests/*.test.js"
```

La prueba `tests/xmb_browser_motion.test.js` necesita Chrome o Edge: inicia un servidor de demostración aislado y verifica fotogramas intermedios de la navegación **sin utilizar la PSP física**. El código Android TV y los trabajos de texturas/ingeniería inversa pueden tener sus propios requisitos. La sincronización y las operaciones sobre USB deben tratarse con precaución y requieren pruebas específicas antes de un uso con datos valiosos.

## Licencia y autoría

**Required Notice: Copyright (c) 2026 jalinhabrava (https://github.com/jalinhabrava)**

El código original de PSP Dock se ofrece bajo la **[PolyForm Noncommercial License 1.0.0](LICENSE.md)**: se permite usar, copiar, estudiar, modificar y compartir el software para fines no comerciales, conservando el aviso de atribución anterior y los términos de la licencia. El uso comercial requiere una autorización separada de los titulares correspondientes.

**Importante:** esto **no** convierte en no comercial el código de terceros. Partes del homebrew y del driver HID están derivadas de **FuSa GAMEPAD** y sujetas a **GPL-3.0** (que permite uso comercial bajo sus condiciones); también hay componentes y avisos de PSPSDK, DejaVu, y dependencias externas como PPSSPP. Su licencia original prevalece sobre la licencia del código propio. Consulta [NOTICES](NOTICE.md) y [Licencias de terceros](THIRD_PARTY_NOTICES.md). Por su restricción comercial, el código propio bajo PolyForm se denomina *source-available*, no *open source* conforme a la definición OSI.

PSP Dock no está afiliado ni respaldado por Sony, PlayStation, PPSSPP ni RetroArch. Las marcas pertenecen a sus respectivos titulares. No se suministran obras de juegos ni recursos de firmware de terceros.

## Documentación

Consulta el [flujo del producto](docs/FLUJO-PRODUCTO.md), el [protocolo nativo 0308](homebrew/native-dock/README.md), la [investigación de texturas](docs/MOTOR-TEXTURAS-DETERMINISTA.md) y el [registro de cambios de la primera versión](CHANGELOG.md). Los documentos históricos describen experimentos anteriores y no deben confundirse con el estado del MVP.
