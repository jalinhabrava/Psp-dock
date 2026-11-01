# Generación híbrida HD para PPSSPP (solo PC)

Esta función es opt-in y por juego. Usa la carpeta privada de PPSSPP del PC,
nunca la Memory Stick física de la PSP. No modifica ISO/CSO, partidas, firmware,
controladores ni packs comunitarios.

## Dos procesos complementarios

1. **Durante la partida:** PPSSPP reconoce y vuelca las texturas utilizadas a
   PSP/TEXTURES/<DISC_ID>/new. PSP Dock habilita SaveNewTextures=True y
   ReplaceTextures=True en el perfil gráfico privado. El fichero textures.ini
   utiliza hash=xxh64 e ignoreAddress=true. Un trabajador de baja prioridad
   revisa PNG completos, espera a que se estabilicen y genera reemplazos HD
   usando el mismo nombre hash. La publicación es atómica y no sobreescribe
   los archivos existentes. Los cambios se comprueban al reiniciar el juego.
2. **Sin jugar:** el XMB ofrece Buscar imágenes dentro de ISO/CSO. El análisis
   de la copia local enumera PNG/JPEG/GIM/TM2 accesibles directamente desde
   ISO9660/CSO v1. PNG/JPEG validados se guardan en un caché del PC. GIM/TM2
   solo se inventarían, todavía no se decodifican.

**Limitación importante:** la segunda vía no puede inferir el hash runtime de
PPSSPP ni garantizar que estén todas las texturas; deja candidatos separados
y no los publica como reemplazos. Muchos juegos utilizan contenedores
propietarios que requieren ingeniería inversa específica. Los candidatos
necesitan correspondencia verificada con los hashes de PPSSPP.

## Cooperación con PPSSPP

- En Gráficos > Mejorar texturas están las opciones CPU ya existentes y los
  nuevos shaders PPSSPP GPU 2xBRZ/4xBRZ, con backend Vulkan.
- Si Vulkan no está activo, la opción GPU queda inactiva en el perfil de
  PPSSPP y se informa al usuario. No se afirma que haya soporte DLSS.
- Los reemplazos persistentes evitan tener que reescalar desde cero cada
  vez. Si el rendimiento se resiente, evitar combinar escalado CPU y el
  generador HD en el mismo juego.
- La primera versión conserva archivos PNG. KTX2/UASTC y mipmaps son mejoras
  posteriores que requieren comprobar dimensiones y compatibilidad.

## Motores de procesamiento

- **Lanczos 2× / 4×**: requiere Pillow (instalar en el Python que ejecuta el
  Dock con python -m pip install Pillow). Es interpolación, no IA generativa.
- **Real-ESRGAN opcional**: instalar manualmente el paquete oficial
  realesrgan-ncnn-vulkan y colocar realesrgan-ncnn-vulkan.exe junto a
  models/realesrgan-x4plus.bin y models/realesrgan-x4plus.param en la carpeta
  tools/realesrgan-ncnn-vulkan del proyecto. La acción solo aparece cuando
  esos archivos existen. Usa GPU Vulkan local y puede consumir recursos
  durante el juego. No requiere contratar una API ni descarga binarios sola.

**Uso:** conectar, copiar y verificar el juego; abrir Gráficos en el XMB,
seleccionar juego > Generador HD híbrido, activar el modo y jugar. La fila de
progreso muestra capturas, reemplazos y candidatos offline sin mapear. Se
puede pausar la captura conservando el pack y procesar pendientes aparte.
La identificación usa el DISC_ID de la copia verificada en PC, nunca el
nombre arbitrario de la ISO.

## Integridad

- Un textures.zip existente o textures.ini ajeno bloquea el generador: no
  mezcla ni sobrescribe packs comunitarios. Estos siguen instalándose mediante
  la vía existente con confirmación y comprobación SHA256.
- Solo se procesan PNG con nombres de hash PPSSPP, tamaño y dimensiones
  acotados, bajo la carpeta privada del juego. No se siguen enlaces.
- Las imágenes originales volcadas no se borran y las texturas publicadas
  no se sobreescriben. Finalizar el juego detiene el trabajador.
- No se permite mover los datos del PC durante un trabajo HD activo. Los
  candidatos ISO se incluyen en la migración verificada de almacenamiento.
- Puede haber errores gráficos en interfaces, fuentes, transparencias y
  animaciones. Se requiere revisión visual.

## Pruebas

Pruebas nuevas:
python -m unittest tests.test_psp_hd_textures -v

Regresiones de gráficos y packs:
python -m unittest tests.test_ppsspp_graphics tests.test_dock_graphics_metadata tests.test_psp_texture_packs tests.test_dock_relocation -v

Queda por validar el ciclo real de captura y reemplazo con PPSSPP 1.20.4+
en Windows y un juego. Las pruebas sintéticas no prueban calidad visual ni
compatibilidad de todos los formatos propietarios de juegos PSP.

Documentación oficial:
- https://www.ppsspp.org/docs/reference/texture-replacement/
- https://www.ppsspp.org/docs/development/developer-tools/
- https://github.com/hrydgard/ppsspp/blob/master/assets/shaders/defaultshaders.ini
