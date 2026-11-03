package app.pspdock.tv

import android.content.Context
import android.net.Uri
import android.os.StatFs
import android.provider.OpenableColumns
import android.util.AtomicFile
import app.pspdock.tv.core.GameFormat
import app.pspdock.tv.core.VerifiedImport
import org.json.JSONObject
import java.io.File
import java.io.InputStream

data class LibraryGame(val id: String, val title: String, val bytes: Long, val sha256: String)

class GameStore(private val context: Context) {
    private val library = File(context.filesDir, "library").apply { mkdirs() }
    private val manifests = File(context.filesDir, "manifests").apply { mkdirs() }

    fun removeInterruptedImports(): Int {
        val leftovers = library.listFiles().orEmpty().filter {
            it.name.startsWith(".import-") && it.name.endsWith(".part") && it.isFile
        }
        return leftovers.count { it.delete() }
    }

    fun list(): List<LibraryGame> = manifests.listFiles().orEmpty()
        .filter { it.name.endsWith(".json") }
        .mapNotNull { read(it.name.removeSuffix(".json")) }
        .sortedBy { it.title.lowercase(java.util.Locale.ROOT) }

    fun read(id: String): LibraryGame? {
        if (!GameFormat.validId(id)) return null
        return try {
            val json = JSONObject(AtomicFile(File(manifests, id + ".json")).readFully().toString(Charsets.UTF_8))
            val title = GameFormat.title(json.getString("title"))
            val hash = json.getString("sha256")
            val size = json.getLong("bytes")
            if (json.getString("id") != id || hash != id.substringBefore('.') || size <= 0) return null
            val file = File(library, id)
            if (file.canonicalFile.parentFile != library.canonicalFile || !file.isFile || file.length() != size) return null
            LibraryGame(id, title, size, hash)
        } catch (_: Exception) { null }
    }

    fun file(id: String): File {
        requireNotNull(read(id)) { "Este juego no está verificado en la biblioteca." }
        return File(library, id).canonicalFile
    }

    fun sourceMetadata(uri: Uri): Pair<String, Long?> {
        require(uri.scheme == "content") { "Elige un archivo mediante el selector de Android." }
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
            null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                val name = if (nameIndex >= 0 && !cursor.isNull(nameIndex)) cursor.getString(nameIndex) else ""
                val size = if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) cursor.getLong(sizeIndex) else null
                val title = GameFormat.title(name)
                GameFormat.extension(title)
                return title to size?.takeIf { it >= 0 }
            }
        }
        throw IllegalArgumentException("El proveedor no devuelve el nombre del juego.")
    }

    fun import(
        source: InputStream,
        name: String,
        size: Long?,
        cancelled: () -> Boolean,
        progress: (Long, VerifiedImport.Stage) -> Unit
    ): LibraryGame {
        val result = VerifiedImport.copy(source, library, name, size, cancelled,
            { StatFs(library.path).availableBytes }, progress)
        val game = LibraryGame(result.id, GameFormat.title(name), result.bytes, result.sha256)
        val json = JSONObject().put("schema", 1).put("id", game.id).put("title", game.title)
            .put("bytes", game.bytes).put("sha256", game.sha256).put("verified_at", System.currentTimeMillis())
        val manifest = AtomicFile(File(manifests, game.id + ".json"))
        val output = manifest.startWrite()
        try {
            output.write(json.toString().toByteArray(Charsets.UTF_8))
            manifest.finishWrite(output)
        } catch (error: Exception) {
            manifest.failWrite(output)
            throw error
        }
        return game
    }
}
