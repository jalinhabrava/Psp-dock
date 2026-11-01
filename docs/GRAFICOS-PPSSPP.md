# PSP Dock — perfiles gráficos adaptativos (01/11/2026)

## Implementado en el PC

ppsspp_graphics.py detecta el monitor principal de Windows, identifica el
nombre de la GPU y calcula la resolución interna en PPSSPP. El XMB ofrece
un selector de Mejoras gráficas, que se abre con TRIANGLE en la PSP.
Las preferencias por juego se guardan exclusivamente en
graphics-profiles.json dentro de la carpeta LIVE del PC.

El ordenador comprobado tiene una pantalla principal 3440 × 1440
ultrawide, otra pantalla 1080 × 1920 vertical y una NVIDIA RTX 3070 Ti.
PPSSPP portable 1.20.4 funciona actualmente con Direct3D 11.

La PSP no es cuadrada: su imagen original mide 480 × 272, cercana a 16:9.
En el monitor principal, las opciones calculadas son:

| Calidad | Escala de resolución | Resolución 3D interna |
|---|---:|---:|
| Equilibrado | 4× | 1920 × 1088 |
| Automático (predeterminado) | 5× | 2400 × 1360 |
| Calidad | 6× | 2880 × 1632 |
| Ultra | 7× | 3360 × 1904 |
| Manual | Escala de Ajustes | Según elección |

El perfil Ultra supera la altura física del monitor; puede suavizar
bordes gracias al sobremuestreo, pero usa más GPU y puede crear artefactos
en HUD/menús de ciertos juegos. Los perfiles son calculados, no un
benchmark de fotogramas del PC.

### Mejoras de texturas nativas de PPSSPP

Se añadió en la interfaz XMB un selector separado del renderizado 3D:
desactivado (predeterminado), xBRZ 2×, 3× o 4×, o Bicubic 2×.
Estas opciones ajustan los campos TexScalingLevel y TexScalingType
del ppsspp.ini privado. xBRZ/Bicubic se ejecutan en CPU, no usan
IA neuronal ni Tensor Cores de la RTX; pueden ser lentos y alterar
el aspecto original de gráficos 2D, por eso son opcionales.

El Dock lee de forma acotada PARAM.SFO de ISO, CSO y EBOOT/PBP
ya copiados y verificados EN EL PC. La ISO Dariusburst local es
ULJM05558, versión 1.01: ese identificador asocia packs HD.
Su partida está en NPJH50053SAVE, una carpeta de guardados con
nombre distinto. No confundir el save con el DISC_ID de la ROM.

Los packs se detectan en la Memory Stick privada de PPSSPP:
PSP/TEXTURES/DISC_ID/textures.ini o textures.zip.
La detección no copia ni descarga contenido. Los packs deben
ser compatibles con la edición específica del juego; el ZIP
puede contener textures.ini en su raíz según la documentación.

## Ultrawide sin deformaciones

- Respetar proporción (recomendado): pantalla completa, bandas laterales,
  contenido original sin estirar. A 3440 × 1440 la imagen PSP ocupa
  aproximadamente 2541 × 1440.
- Estirar para ocupar 3440 × 1440: llena el monitor, pero distorsiona
  horizontalmente. La UI lo advierte. No amplía el campo de visión.
- Widescreen 21:9 real: solo mediante parche validado para título,
  región y revisión concretos. No hay un hack universal fiable.

## Qué aporta la RTX y qué NO hace

PPSSPP ofrece escalado de renderizado, filtrado anisotrópico, reemplazo
de texturas mediante packs HD y algoritmos de escalado xBRZ/Bicubic/Hybrid.
Vulkan con MSAA es una futura opción interesante para esta GPU, según
las recomendaciones oficiales; mantenemos D3D11 como backend probado
hasta medir en un juego real compatibilidad y rendimiento.

No se ha incorporado DLSS a PPSSPP, ni se usa NVIDIA RTX Video
Super Resolution para los fotogramas de juegos. Para texturas mejoradas
con modelos externos de IA se necesitaría un flujo explícito: volcado de
texturas del juego, procesamiento offline (por ejemplo ESRGAN compatible
con su estilo artístico), revisión y pack con textures.ini. No se debe
prometer que un algoritmo mejora automáticamente todos los juegos.

## Seguridad

Los cambios se aplican únicamente en el PPSSPP privado de PSP Dock,
con respaldo atómico del archivo ppsspp.ini. Se conserva controls.ini
y no se modifican los drivers Nvidia, la PSP, los guardados ni las ISO.
El usuario puede cambiar preferencias desde el XMB sin abrir PPSSPP.

## Referencias oficiales

- https://www.ppsspp.org/docs/settings/graphics/
- https://www.ppsspp.org/docs/settings/recommended/
- https://www.ppsspp.org/docs/reference/texture-replacement/
- https://www.ppsspp.org/docs/reference/use-texture-replacement/

Estado pendiente: evaluación visual en juego real de resolución 5×/6×,
estiramiento opcional y mejoras de texturas específicas. La selección
desde mando PSP en WinMM está implementada pero aún no revalidada
físicamente en esta nueva versión.
