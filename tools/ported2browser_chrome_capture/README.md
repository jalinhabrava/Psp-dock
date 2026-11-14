# Captura pasiva del directorio original

La extensión trabaja únicamente con la web original
https://ported2browser.com/ en el Chrome normal del usuario. No usa
Playwright ni una sesión de navegador con verificación humana fallida.

## Instalación en Chrome

1. Abre chrome://extensions y habilita el Modo de desarrollador.
2. Pulsa Cargar descomprimida y selecciona esta carpeta:
   tools/ported2browser_chrome_capture
3. Abre https://ported2browser.com/ en tu Chrome habitual y recarga.
4. Navega normalmente por el directorio (las páginas 2, 3, etc.) y
   visita las fichas de los ports que quieras recopilar.
5. En el menú de extensiones, abre PSP Dock - Ported2Browser capture
   y pulsa Exportar JSON a tu PC.
6. Adjunta el archivo ported2browser-original-pspdock.json en ChatGPT.

Si la página ya estaba abierta antes de instalar, recárgala una vez.

## Qué se captura y qué NO

Se guardan en chrome.storage.local solo metadatos públicos visibles:
IDs, título, autor, ficha original, controles declarados, avisos,
requisitos de disco y href del botón Play in browser de las páginas
que el usuario haya abierto.

La extensión NO realiza solicitudes de red, NO navega por enlaces
automáticamente, NO supera retos CAPTCHA, NO descarga juegos y NO lee
cookies, sesiones, cabeceras, archivos ISO o guardados. Su único permiso
es storage y solo se ejecuta en la web original .com.

El exportador contiene exactamente lo capturado. Nunca convierte
una ficha en juego compatible con mando sin más pruebas. Tampoco
mezcla los dominios .org o .sbs que anteriormente se confundieron
con el directorio auténtico.

El usuario puede quitar la extensión desde chrome://extensions.
Se deben respetar las condiciones de acceso y uso del directorio.
