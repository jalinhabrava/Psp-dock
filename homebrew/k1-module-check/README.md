# PSP Dock — comprobador de módulo K1 (laboratorio)

Diagnóstico **USER** que solo pregunta a ModuleMgrForUser qué módulos
ve. No usa USB, no carga PRX, no abre archivos, no toca firmware,
controladores ni partidas. Su arranque es manual en Juegos del XMB,
**solo durante las pruebas de desarrollo**; nunca es parte del uso
diario del Dock.

AVISO DE INTERPRETACIÓN: al lanzar un homebrew desde XMB, la PSP pasa
al contexto GAME y el firmware puede descargar los plugins que estaban
activos únicamente en VSH. Por eso **no utilizar esta aplicación como
verificación concluyente de K1 en XMB**. Su instalación es inocua
(no autoarranca), pero no se necesita ejecutarla para el primer
checkpoint físico K1. Una prueba concluyente exigirá un lector de
estado que permanezca en contexto VSH.

Si se ejecuta voluntariamente como diagnóstico de ModuleMgrForUser:

- `K1 visible via USER API: YES` prueba que el administrador de
  módulos permite verlo desde el proceso USER.
- `NO / INCONCLUSIVE` **no** demuestra que K1 no esté cargado: el
  firmware puede ocultar módulos kernel a consultas de usuario o ya
  haber descargado los plugins VSH al entrar en modo GAME.

X repite lectura; START+SELECT sale sin escribir ningún archivo.
Las pruebas con módulo kernel y la lectura de su snapshot todavía
requieren un mecanismo seguro de observación de contexto kernel.

**No utilizar para conmutar USBSTOR/HID.**
