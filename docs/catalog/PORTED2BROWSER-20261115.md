# Catálogo original Ported2Browser: auditoría del 15-11-2026

Se controló la ventana de Chrome existente mediante Windows UI Automation.
No había conector DOM para sus pestañas ni Chrome iniciado con CDP.
DevTools de esa misma pestaña permitió leer el DOM y realizar solicitudes
HTML normales al mismo origen, secuenciales y con pausas de 1,6 segundos.
No se instaló extensión, reinició Chrome, copió perfil/cookies/sesión ni se
intentó superar un bloqueo del sitio.

Fuente única: https://ported2browser.com/. Se recorrieron **11 páginas, 330
identificadores distintos y sus 330 fichas**. Los filtros del propio directorio
aportaron 34 plataformas mediante 39 páginas de facetas. Cada destino procede
del botón Play con ID y ruta de ficha coincidentes. OpenLara es la excepción
HTTP: se conserva como metadato, sin autorizarlo en el lanzador HTTPS.

## Resultado y significado

| Medida | Entradas |
|---|---:|
| Fichas y enlaces directos individuales | 330 |
| Candidatos a port según categorías de la fuente | 190 |
| Emuladores | 52 |
| Fangames | 36 |
| En desarrollo | 14 |
| Recopilaciones | 7 |
| Móvil u otros | 15 |
| Web nativos | 4 |
| Flash | 4 |
| Mezclas, software, repositorios, IA y mesa | 8 |
| Destinos con HTML recibido y sin marca de enlace roto | 267 |
| Rotos según la fuente y/o HTTP 404/410 | 58 |
| Destinos pendientes por error/restricción de acceso | 5 |
| Archivos originales obligatorios declarados en las fichas | 12 |
| De ellos, ISO/CSO para la versión de navegador | 3 |
| Archivos opcionales para versión completa | 5 |
| Archivos condicionales o alternativos | 2 |
| Uso de ratón explícito sin gamepad declarado | 8 |
| Descripción menciona L2/R2 / segundo stick | 15 / 7 |
| Compatibilidad con gamepad declarada por el directorio | 216 |
| Nuevas partidas verificadas con mando físico | 0 |

Las categorías son excluyentes para contar, pero cada entrada conserva todas
sus banderas originales. «Candidato a port» no significa que el funcionamiento
o la naturaleza técnica del port hayan quedado certificados. Los requisitos
de controles no se deducen de la plataforma. Mencionar una tecla no demuestra
que sea imprescindible: esa necesidad queda sin confirmar.

Las 330 comprobaciones HTTP solo leyeron HTML público y cabeceras, sin cookies,
descarga de recursos de juego ni selección de archivos. Encontraron 271 HTTP
200, 51 HTTP 404 y 8 restricciones/errores de otro tipo. Una marca de enlace
roto del directorio se conserva aunque un dominio aparcado devuelva HTTP 200.
79 destinos publican políticas que restringen la inclusión en iframe; los
valores concretos figuran en cada observación. La ausencia de cabeceras no
certifica compatibilidad con iframe ni con WebView2.

## Comprobación real en Chrome

Las observaciones de SOCOM II, Tekken DR, Halo OpenCE, Mario Kart 64 Online,
Quake y Midnight Club II están en el JSON de observaciones de runtime.
Mario Kart entró en una carrera 3D; esto no comprueba conducción ni mando.
SOCOM pide disco y mostró que no había mando conectado. Halo solicita certificar
posesión del juego; no se confirmó. Quake tiene una declaración de edad previa
al arranque que no se envió. No se cargó ninguna ISO ni se crearon cuentas.

**Discrepancia concreta:** la ficha de Tekken DR declara ISO/CSO y EBOOT, pero
su destino actual ofrece descargar aproximadamente 1 GB desde el servidor.
Se conservan ambos hechos y se marca conflicto; no se presenta el requisito
del directorio como una comprobación del runtime actual.

El NFS Underground 2 ya aceptado por el usuario permanece en los cinco
lanzadores existentes. Las nuevas fichas no amplían la lista de dominios
autorizados ni conceden acceso a acciones nativas.

## Integración y validación

El XMB añade inventario con filtros de plataforma, categoría y estado, detalles
textuales y coincidencias de nombres en la biblioteca ISO configurada. Nunca
lee, copia, sube ni carga imágenes automáticamente. Los menús largos desplazan
la selección para que permanezca visible. El API es `/api/official-web-ports`;
el catálogo de lanzamientos aprobado sigue en `/api/web-ports`.

Validación: 44 pruebas Python centradas en HTTP, parsers, evidencia, categorías
y límites de archivos; suite JavaScript del proyecto y prueba de la aplicación
real en Chrome con 330 fichas, 35 opciones de plataforma, navegación al último
filtro y acción ISO visible. Las pruebas de los puentes Xbox/Gamepad y de salida
LB+RB+View siguen pasando. No se compiló ni sustituyó la instalación activa.
La aceptación física de mando y WebView2 para los nuevos títulos queda pendiente.

## Artefactos y reproducción

- `ported2browser-chrome-20261115.json`: captura pública, tiempos y páginas.
- `ported2browser-destinations-20261115.json`: observaciones HTML/cabeceras.
- `ported2browser-file-requirements-20261115.json`: revisión contextual de archivos.
- `ported2browser-runtime-observations-20261115.json`: observaciones de Chrome.
- `ported2browser-inventory-20261115.csv`: inventario para hoja de cálculo.
- `../../web/official-ports.json`: catálogo de metadatos que consume la aplicación.
- `socom-xmb-analysis-20261115.md`: análisis solicitado del fondo e iconos.

Desde la raíz del repositorio, regenerar sin acceder a la red:

```text
python scripts/catalog/build_official_inventory.py --capture docs/catalog/ported2browser-chrome-20261115.json --probes docs/catalog/ported2browser-destinations-20261115.json --requirements docs/catalog/ported2browser-file-requirements-20261115.json --observations docs/catalog/ported2browser-runtime-observations-20261115.json --output web/official-ports.json --csv docs/catalog/ported2browser-inventory-20261115.csv
```

`scripts/catalog/capture_existing_chrome.js` documenta la captura reproducible
en la pestaña original ya autorizada y se detiene ante un bloqueo. No inicia
Playwright ni crea perfiles. `probe_official_destinations.py` comprueba destinos
públicos con límites; sus resultados nunca habilitan por sí solos un juego.
