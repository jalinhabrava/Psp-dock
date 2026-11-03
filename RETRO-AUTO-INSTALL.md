# PSP Dock · aprovisionamiento automático de emuladores Windows

## Flujo pensado para el futuro instalador `.exe`

1. El usuario elige **una carpeta de almacenamiento en un disco fijo del PC**
   durante el instalador. El instalador guardará esa selección en
   `settings.json` como `storage_root`, usando el mismo selector existente
   del Dock; no instala ningún emulador hasta conocer los homebrew de la PSP.
2. La instalación comercial puede ofrecer la opción **«Instalar automáticamente
   los emuladores de mi PSP»** (que persiste `retro_install_policy=auto`).
   El modo seguro para una instalación anterior es `ask`, con autorización
   explícita desde la categoría **Retro** del XMB; `off` desactiva descargas.
3. Al conectar la PSP con USB Sony, Dock lee **solo** EBOOT/PARAM.SFO y ROMs
   dentro de las carpetas homebrew reconocidas. Deduce los sistemas emulados
   y los núcleos necesarios sin duplicar los compartidos. La copia de ROMs
   permanece independiente y necesita confirmación propia.
4. Un hilo PC descarga **RetroArch portable para Windows x64** desde el servidor
   oficial y comprueba el SHA-256 *fijado para la versión 1.22.2*. Después
   instala únicamente los núcleos previamente aprobados, descargándolos como
   ZIP individual desde el servidor oficial Libretro. No ejecuta contenido
   descargado hasta que se complete la verificación. El progreso y los errores
   se muestran en el XMB.
5. Los archivos se instalan en
   `<storage_root>/emulators/retroarch/retroarch.exe` y `cores/*.dll`. Los
   guardados siguen en `retro-saves/`; perfiles de mando en `retro-profiles/`.
   El Dock reutiliza esta instalación en futuras conexiones, solo añade
   núcleos nuevos que falten y no sobrescribe nada existente. Si el usuario
   eligió un `retroarch.exe` externo, **nunca se modifica**.
6. Cuando se mueven los datos mediante la migración verificada del Dock, se
   trasladan emuladores, partidas, perfiles y ROMs al directorio nuevo sin
   borrar los originales. La ruta configurada a RetroArch se actualiza si
   apuntaba a la instalación gestionada.

**Nota importante:** el instalador `.exe` de distribución todavía no existe;
se ha implementado la lógica que ese instalador podrá invocar tras guardar la
carpeta y la opción de aprovisionamiento. El XMB actual puede activar el modo
automático desde `Ajustes → Instalación de emuladores retro` o pedir la
descarga manual mediante `Retro → Instalar emuladores Windows`.

## Catálogo reconocido en la PSP conectada (1 noviembre 2026)

La inspección real **en solo lectura** reconoció 13 homebrew: gpSP Kai,
dos Snes9xTYL, NesterJ, MasterBoy, PicoDrive, DaedalusX64, PSPHugo, RACE!,
CPS1PSP, CPS2PSP, MVSPSP y MAME4All. Eso corresponde a 11 núcleos
potencialmente necesarios, de los que **9** están autorizados para
aprovisionamiento automático y **2** requieren licencia comercial específica:

| Plataforma | Núcleo libre permitido por defecto | Estado |
| --- | --- | --- |
| GBA | `mgba_libretro.dll` | Autoaprobado (MPL-2.0) |
| GB/GBC | `gambatte_libretro.dll` | Autoaprobado (GPL-2.0) |
| SNES | `bsnes_libretro.dll` | Autoaprobado (GPL-3.0) |
| NES | `mesen_libretro.dll` | Autoaprobado (GPL-3.0) |
| Mega Drive/32X | `blastem_libretro.dll` | Autoaprobado (GPL-3.0; compatibilidad por ROM a validar) |
| SMS/Game Gear | `gearsystem_libretro.dll` | Autoaprobado (GPL-3.0) |
| Nintendo 64 | `mupen64plus_next_libretro.dll` | Autoaprobado (GPL) |
| PC Engine | `mednafen_pce_fast_libretro.dll` | Autoaprobado (GPL-2.0) |
| Neo Geo Pocket | `race_libretro.dll` | Autoaprobado (GPL-2.0) |
| Atari 2600 | `stella_libretro.dll` | Autoaprobado (GPL-2.0) |
| CPS1/CPS2/Neo Geo MVS | `fbneo_libretro.dll` | **Bloqueado para descarga automática** |
| Arcade MAME4All | `mame2003_plus_libretro.dll` | **Bloqueado para descarga automática** |

En el código hay una **lista positiva** `AUTO_APPROVED_CORES`: un nuevo núcleo
no se podrá descargar sin aprobar expresamente su licencia primero. Los
núcleos con cláusulas *non-commercial* NO se incluyen ni se descargan como
parte del producto comercial, aunque estén disponibles en internet. También
se evita la descarga automática de Snes9x, PicoDrive y Genesis Plus GX:
se usan alternativas GPL cuando existen. Los requisitos de redistribución
de las licencias libres, atribuciones y entrega/oferta del código fuente se
deberán cumplir en el futuro producto y revisar antes de publicar el `.exe`.

**Arcade:** la mera presencia de un `.zip` no garantiza compatibilidad.
FBNeo, MAME2003+ y MAME moderno utilizan ROMsets diferentes. No se
transformarán ni descargarán ROMs, BIOS, juegos ni materiales protegidos.

## Integridad y seguridad

- Nunca se descargan archivos desde una URL derivada de la PSP: solo desde
  `buildbot.libretro.com` por HTTPS, sin redirecciones a otras fuentes.
- El archivo base portable tiene **SHA-256 fijado** para detectar cambios o
  suplantación. La versión no se actualiza en segundo plano arbitrariamente.
- Los núcleos vienen de builds oficiales **nightly**: se calculan hashes y se
  extrae solo el único DLL esperado, pero *todavía falta* fijar un manifiesto
  firmado o con hashes versionados para autenticación robusta equivalente a
  la del ejecutable. **No considerar aprobado el suministro de núcleos para
  una distribución comercial hasta completar esa verificación de procedencia.**
- Los ZIP no extraen rutas ajenas; todo se instala en PC, nunca en la PSP.
  No hay auto-elevación, ejecución del instalador `.exe` descargado, ni
  modificación de RetroArch configurado externamente. Si falla una descarga
  se informa del error y se conserva la instalación previa.
- La descarga no debe ejecutarse dentro del hilo que explora la Memory Stick
  ni bloquear el XMB. La cancelación, reanudación y gestor de actualizaciones
  requieren una futura iteración y pruebas reales de red/Windows.

## Estado de la prueba real

Se intentó descargar el RetroArch **1.22.2** oficial al directorio aislado
`%LOCALAPPDATA%/PSP-Dock-Retro-Lab-2026`, pero Windows devolvió
`A socket operation was attempted to an unreachable network`. La carpeta
de pruebas se creó vacía; **no se instaló ni ejecutó RetroArch/mGBA**. Las
pruebas automatizadas ejecutan el instalador contra archivos ficticios sin
acceder a red, ROMs comerciales, datos de usuario ni dispositivo USB.

Para verificar mGBA en hardware se requiere restablecer acceso HTTPS al
servidor oficial o proporcionar el archivo portátil oficial verificado.
Después: comprobar el mando PDK1 activo, cargar una ROM de prueba copiada
con confirmación, importar su SRAM tras respaldarla, y devolverla al usuario
solo después de prueba explícita en gpSP. No se automatiza el cambio físico
USB Sony ↔ HID.

## Fuentes / licencias

- https://docs.libretro.com/development/licenses/
- https://docs.libretro.com/library/bsnes/
- https://docs.libretro.com/library/blastem/
- https://docs.libretro.com/library/gearsystem/
- https://docs.libretro.com/library/race/
- https://github.com/libretro/mame2003-plus-libretro/blob/master/LICENSE.md
- https://buildbot.libretro.com/stable/1.22.2/windows/x86_64/
- https://buildbot.libretro.com/nightly/windows/x86_64/latest/
