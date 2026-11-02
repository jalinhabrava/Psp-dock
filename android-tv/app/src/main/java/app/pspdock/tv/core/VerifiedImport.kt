package app.pspdock.tv.core

import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.io.InterruptedIOException
import java.security.MessageDigest
import java.util.UUID

/**
 * Writes only to the caller's private library. The supplied source is read-only.
 * A file becomes visible only after rereading its full SHA-256 and checking its signature.
 */
object VerifiedImport {
    const val MAX_BYTES = 8L * 1024 * 1024 * 1024
    const val FREE_RESERVE = 64L * 1024 * 1024
    data class Result(val id: String, val bytes: Long, val sha256: String, val alreadyPresent: Boolean)
    enum class Stage { COPYING, VERIFYING }

    fun copy(
        source: InputStream,
        directory: File,
        name: String,
        expectedSize: Long?,
        cancelled: () -> Boolean,
        availableBytes: () -> Long,
        progress: (Long, Stage) -> Unit
    ): Result {
        val extension = GameFormat.extension(name)
        require(expectedSize == null || expectedSize in 1..MAX_BYTES) { "Tamaño de juego no admitido." }
        require(directory.isDirectory || directory.mkdirs()) { "No se puede abrir la biblioteca local." }
        if (expectedSize != null) require(availableBytes() >= expectedSize + FREE_RESERVE) {
            "No hay espacio suficiente para importar el juego."
        }
        val temporary = File(directory, ".import-" + UUID.randomUUID() + ".part")
        var total = 0L
        val digest = MessageDigest.getInstance("SHA-256")
        val header = java.io.ByteArrayOutputStream(65536)
        fun checkCancelled() {
            if (cancelled() || Thread.currentThread().isInterrupted) {
                throw InterruptedIOException("Importación cancelada.")
            }
        }
        try {
            FileOutputStream(temporary).use { output ->
                val chunk = ByteArray(256 * 1024)
                var emptyReads = 0
                while (true) {
                    checkCancelled()
                    val count = source.read(chunk)
                    if (count < 0) break
                    if (count == 0) {
                        require(++emptyReads < 16) { "El proveedor no entrega datos." }
                        continue
                    }
                    emptyReads = 0
                    require(total <= MAX_BYTES - count) { "El archivo supera el límite de importación." }
                    require(availableBytes() >= count + FREE_RESERVE) { "El dispositivo se está quedando sin espacio." }
                    output.write(chunk, 0, count)
                    digest.update(chunk, 0, count)
                    if (header.size() < 65536) header.write(chunk, 0, minOf(count, 65536 - header.size()))
                    total += count
                    progress(total, Stage.COPYING)
                }
                output.fd.sync()
            }
            checkCancelled()
            require(total > 0 && (expectedSize == null || total == expectedSize)) {
                "El archivo recibido está vacío o incompleto."
            }
            require(GameFormat.validates(extension, header.toByteArray())) {
                "El contenido no corresponde al formato de juego seleccionado."
            }
            val hash = digest.digest().toHex()
            progress(total, Stage.VERIFYING)
            val diskHash = hashFile(temporary, cancelled)
            require(diskHash == hash) { "La copia no supera la verificación de integridad." }
            checkCancelled()
            val id = hash + "." + extension
            val destination = File(directory, id)
            val present = destination.exists()
            if (present) {
                require(destination.isFile && destination.length() == total &&
                    hashFile(destination, cancelled) == hash) { "Hay una copia local dañada; no se reemplazará automáticamente." }
                check(temporary.delete()) { "No se pudo retirar el temporal verificado." }
            } else {
                check(temporary.renameTo(destination)) { "No se pudo finalizar la importación." }
            }
            return Result(id, total, hash, present)
        } finally {
            if (temporary.exists()) temporary.delete()
        }
    }

    fun hashFile(file: File, cancelled: () -> Boolean = { false }): String {
        val hash = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { stream ->
            val buffer = ByteArray(256 * 1024)
            while (true) {
                if (cancelled() || Thread.currentThread().isInterrupted) {
                    throw InterruptedIOException("Verificación cancelada.")
                }
                val count = stream.read(buffer)
                if (count < 0) break
                if (count > 0) hash.update(buffer, 0, count)
            }
        }
        return hash.digest().toHex()
    }

    private fun ByteArray.toHex() = joinToString("") { "%02x".format(it.toInt() and 255) }
}
