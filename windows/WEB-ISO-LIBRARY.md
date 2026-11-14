# Biblioteca local de imágenes para el catálogo web

El módulo `psp_dock.media.web_iso_library` permite elegir una carpeta existente
del PC e indexar los metadatos de sus imágenes. No abre el contenido de las
imágenes, copia archivos, extrae archivos comprimidos, calcula hashes de
contenidos, hace peticiones de red ni publica los archivos por HTTP.

## Integración

```python
from psp_dock.media.web_iso_library import (
    WebIsoLibraryError,
    choose_web_iso_folder,
    validate_web_iso_folder,
    scan_web_iso_folder,
    match_web_iso_candidates,
    resolve_web_iso_entry,
)

# Invocar el selector después de una acción explícita de la persona.
# Se devuelve la carpeta elegida, sin añadir un subdirectorio "PSP Dock".
folder = choose_web_iso_folder()
if folder is not None:
    report = scan_web_iso_folder(folder)
    suggestions = match_web_iso_candidates(catalog_game, report)
```

La aplicación conserva la carpeta configurada en sus ajustes locales. El
módulo no guarda ajustes ni modifica los datos de sesiones, USB o partidas.
Si conoce una unidad PSP activa, puede pasar `excluded_roots=[psp_drive]` a
las funciones de selección, validación, escaneo y resolución.

| Función | Resultado |
| --- | --- |
| `choose_web_iso_folder(*, run=None, excluded_roots=())` | Ruta `str` de la carpeta elegida o `None` al cancelar. Selector nativo Windows en STA. |
| `validate_web_iso_folder(path, *, excluded_roots=())` | `Path` de carpeta existente en un disco fijo local. |
| `scan_web_iso_folder(path, max_entries=20000, max_depth=8, *, max_seconds=5, excluded_roots=())` | Informe de metadatos y límites. |
| `match_web_iso_candidates(catalog_game, entries, *, max_candidates=20)` | Sugerencias sin confirmar; acepta la lista de entradas o el informe de escaneo completo. |
| `resolve_web_iso_entry(path, entry_id, *, confirmed=False, ...)` | `Path` local tras confirmación explícita y escaneo nuevo; nunca abre el archivo. |

Todos los errores controlados son `WebIsoLibraryError`, subclase de
`ValueError`. La plataforma debe ser Windows; las pruebas multiplataforma
sustituyen únicamente las comprobaciones de plataforma y tipo de disco.

## Informe de escaneo

Se reconocen `.iso`, `.cso`, `.bin`, `.cue` y `.chd`, sin distinguir mayúsculas.
No se inspeccionan ZIP, 7z ni el contenido de los CUE; los archivos BIN/CUE
aparecen como entradas independientes. La extensión no garantiza el formato
real, la integridad, la plataforma o la compatibilidad con un port.

Cada entrada contiene exclusivamente:

```json
{
  "id": "iso_0123456789abcdef0123456789abcdef",
  "name": "Game (Europe).iso",
  "size": 123456789,
  "mtime": 1794657600.0,
  "extension": ".iso"
}
```

`size` está en bytes y `mtime` es una marca temporal Unix en segundos.
El identificador es un digest de la carpeta, la ruta relativa y los metadatos
del sistema de archivos. Es estable mientras estos datos no cambien. Un
cambio de tamaño, fecha, identidad del archivo o ubicación invalida el ID
anterior; no es un checksum del contenido ni un token de autorización.
Los informes públicos no contienen rutas absolutas ni relativas de archivo.

El informe incluye `entries`, `count`, `scanned_entries`,
`scanned_directories`, `complete`, `truncated`, `limit_reasons`, `skipped`,
`limits`, `metadata_only` y `requires_confirmation`.

Todos los elementos encontrados cuentan para `max_entries`, también los
archivos con otras extensiones y los enlaces descartados. La profundidad
cero solo examina la carpeta elegida. Los máximos configurables son 50 000
entradas, 32 niveles y 30 segundos. El presupuesto temporal se comprueba
entre operaciones de metadatos; no interrumpe una llamada al sistema que ya
esté en curso.

La aplicación debe mostrar que el resultado está incompleto si
`complete` es falso. `limit_reasons` identifica `max_entries`, `max_depth`
o `max_seconds`; `skipped` cuenta enlaces, elementos no compatibles,
elementos especiales y errores de acceso. Un resultado parcial vacío no
demuestra que no haya imágenes en las carpetas que faltan por examinar.

## Sugerencias y confirmación

Se usan `title` y los alias explícitos de `iso_aliases` (o `aliases`).
Se normalizan acentos y puntuación. Solo se eliminan anotaciones reconocidas
de región, idioma, disco, revisión o número de catálogo; los subtítulos
desconocidos se conservan. Los números de secuela deben coincidir. Las
equivalencias entre números romanos y arábigos deben indicarse mediante un
alias explícito: «Mega Man X» y «Mega Man 10» son juegos distintos.

El informe devuelve `status` (`none`, `suggested` o `ambiguous`),
`candidates`, `candidate_count`, `suggested_id`, `ambiguous`, `has_more`,
`scan_complete`, `requires_confirmation` y `confirmed`.
Cada candidato añade `confidence`, `confidence_label`, `matched_by` y
`matched_title`. La confianza expresa similitud del nombre, nunca
verificación del contenido.

Varias regiones, discos o formatos permanecen como candidatos distintos.
No se selecciona el más grande, el más nuevo ni el primero. Incluso una
coincidencia única de alta confianza necesita una elección manual.
Pasar el informe completo permite impedir `suggested_id` cuando el escaneo
es incompleto.

Después de la elección explícita, la aplicación puede resolver ese ID:

```python
local_path = resolve_web_iso_entry(folder, selected_id, confirmed=True)
```

Esta ruta es para una interacción nativa local autorizada. No debe
convertirse en una URL de descarga, una ruta HTTP ni una subida automática
al sitio web del port. El módulo no registra asociaciones confirmadas.

## Límites de seguridad

La selección rechaza raíces de disco, rutas relativas, segmentos de
traversal, UNC, rutas de dispositivo, ADS, nombres reservados y discos
extraíbles o de red. Se comprueban los antecesores antes de resolver la
ruta y se rechazan los enlaces y todos los puntos de reanálisis de Windows.
El recorrido utiliza metadatos sin seguir enlaces, comprueba de nuevo las
carpetas y aborta si cambia su identidad.

Estas comprobaciones de rutas no constituyen una garantía atómica frente
a otro proceso que cambie el árbol entre llamadas al sistema. El alcance
del módulo es solo metadatos; cualquier futuro lector de contenido necesita
su propia validación mediante un identificador de archivo abierto.

## Validación

```text
python -m unittest discover -s tests -p test_web_iso_library.py -v
```

Los tests cubren selección real frente a subcarpeta, cancelación, Unicode,
plataforma, tipos de disco, exclusión PSP, rutas inseguras, reparse points,
límites, archivos enormes, privacidad de rutas, IDs obsoletos, secuelas,
alias, ambigüedad y confirmación obligatoria.
