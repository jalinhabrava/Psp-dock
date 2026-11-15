# Probar los ports importados en PSP Dock

Esta prueba abre las URL de los autores recogidas en el directorio original
https://ported2browser.com/. Se activa exclusivamente en la demostración local;
no sustituye la instalación habitual ni convierte las fichas en juegos certificados.

Desde la raíz del repositorio en Windows, con Python y el SDK .NET 8 o posterior:

```powershell
& .\windows\preview-web-games.ps1
```

El script compila un contenedor WebView2 separado, inicia el servidor con
`--demo --preview-web-ports` y abre la ventana **PSP Dock · prueba local de juegos**.
`-SkipBuild` reutiliza el binario ya compilado y `-NoWindow` inicia solo el servidor.
Los datos y el perfil de navegador quedan en `PSP-Dock-SOCOM-Preview`, bajo
LocalAppData. No se copian sesiones, cookies ni guardados de la instalación normal.

Entra en **Juegos Web → Catálogo experimental · Ported2Browser**, filtra por
plataforma y abre una ficha. **Probar enlace · experimental** solicita abrir
su destino directo, sin proxy ni modificación de las políticas del autor.
Los runtimes que requieren ISO, ROM o archivos del juego conservan su selector
manual; PSP Dock no los lee ni los envía automáticamente.

## Mando, menús y salida

Que un juego responda al mando durante la partida no garantiza que sus páginas
de carga, selección de archivos, ajustes o menús previos admitan mando. Algunos
necesitan **ratón o teclado** en esas pantallas. Los controles declarados por el
directorio y las observaciones de una partida se conservan como datos distintos.

Para regresar al XMB, mantén **Start + Select juntos durante 2 segundos**.
En Xbox corresponden a **Menu + View**; en PSP, **START + SELECT**.
No se necesitan botones L/R. Soltar cualquiera interrumpe la cuenta. Al volver,
hay que soltar ambos antes de volver a navegar por el XMB. También se conserva
el botón de ratón **Volver al XMB** de la barra superior.

La salida Xbox se comprueba mediante XInput directamente en el contenedor,
independientemente del ritmo de dibujo del runtime o de una petición al backend
PSP. El puente del juego y la vista iframe admiten el mismo gesto para los demás
mandos compatibles. No se prometen adaptaciones universales de todos los menús.

## Qué contiene esta captura

El inventario conserva330 fichas y separa190 candidatos a port de140 entradas
de otras categorías. De esos190 candidatos,146 destinos HTTPS permiten intentar
una prueba:143 constaban activos y3 pendientes de comprobación. Los43 enlaces
rotos y el enlace HTTP quedan bloqueados; las otras categorías son informativas.
Es un estado de la captura, no un recuento de partidas verificadas.

Mario Kart64 llegó a una carrera dentro de WebView2 y el usuario confirmó que
la partida funcionaba con su mando Xbox. Sus menús previos no quedaron certificados
de extremo a extremo. Se observaron también Super Mario Kart y Diablo/DevilutionX
en el navegador del autor; esas observaciones no certifican otros mandos ni toda
la integración WebView2.

## Comprobaciones de desarrollo

```text
python -m unittest tests.test_preview_web_ports tests.test_http -q
node tests/preview_web_ports.test.js
node tests/web_port_xbox_navigation.test.js
```

La política nativa comparte pruebas con el detector de salida en
`tests/native_preview`. Este ejecutable puede publicarse self-contained cuando
el equipo no tiene el runtime .NET 8 instalado. La compilación no implica instalar
PSP Dock ni valida por sí sola un mando físico.
