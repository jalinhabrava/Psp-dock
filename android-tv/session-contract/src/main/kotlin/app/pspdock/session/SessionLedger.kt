package app.pspdock.session

import java.io.Closeable
import java.io.File
import java.io.FileOutputStream
import java.io.RandomAccessFile
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID

/**
 * Cross-process launch/shutdown evidence, stored only in application-private storage.
 * A CLOSED record is insufficient while the emulator still owns the OS file lock.
 * None of these states grants permission to synchronize to an external device.
 */
class SessionLedger(private val directory: File) {
    enum class State { IDLE, STARTING, RUNNING, STOPPING, CLOSED, INTERRUPTED, FAILED, RECOVERED, UNKNOWN }

    data class Snapshot(val state: State, val token: String?, val lockHeld: Boolean) {
        val mayLaunch: Boolean get() = !lockHeld && state in setOf(State.IDLE, State.CLOSED, State.FAILED, State.RECOVERED)
        val mayResume: Boolean get() = lockHeld && state == State.RUNNING
        val mayRecover: Boolean get() = !lockHeld && state in setOf(State.STARTING, State.INTERRUPTED, State.UNKNOWN)
        val cleanShutdown: Boolean get() = !lockHeld && state == State.CLOSED
        val maySynchronizeToPsp: Boolean get() = false
    }

    private enum class Stored { REQUESTED, RUNNING, CLOSED, FAILED, RECOVERED }
    private data class Record(val token: String, val state: Stored)
    private val recordFile get() = File(directory, "session.state")

    /** Reserve a launch before Android starts the Activity. A second launch cannot replace it. */
    fun prepare(): String = withFreeLock {
        val record = readRecord()
        check(!recordFile.exists() || record != null) { "El registro de sesión necesita recuperación." }
        check(record == null || record.state in setOf(Stored.CLOSED, Stored.FAILED, Stored.RECOVERED)) {
            "Ya hay un arranque o una sesión sin resolver."
        }
        newToken().also { writeRecord(Record(it, Stored.REQUESTED)) }
    }

    fun markLaunchFailed(token: String) = withFreeLock {
        val record = requireRecord(token)
        check(record.state == Stored.REQUESTED) { "El motor ya ha iniciado la sesión." }
        writeRecord(Record(token, Stored.FAILED))
    }

    /**
     * expectedToken is persisted by the Dock before launching. A stale CLOSED record
     * from another launch therefore cannot certify the current session.
     */
    fun observe(expectedToken: String?): Snapshot {
        ensureDirectory()
        RandomAccessFile(File(directory, "session.lock"), "rw").use { handle ->
            val lock = tryLock(handle)
            try {
                val held = lock == null
                val record = readRecord()
                if (record == null) {
                    val idle = !recordFile.exists() && expectedToken == null && !held
                    return Snapshot(if (idle) State.IDLE else State.UNKNOWN, null, held)
                }
                if (record.token != expectedToken) return Snapshot(State.UNKNOWN, record.token, held)
                val state = when (record.state) {
                    Stored.REQUESTED -> State.STARTING
                    Stored.RUNNING -> if (held) State.RUNNING else State.INTERRUPTED
                    Stored.CLOSED -> if (held) State.STOPPING else State.CLOSED
                    Stored.FAILED -> if (held) State.UNKNOWN else State.FAILED
                    Stored.RECOVERED -> if (held) State.UNKNOWN else State.RECOVERED
                }
                return Snapshot(state, record.token, held)
            } finally { lock?.release() }
        }
    }

    /** Explicit acknowledgement of an interrupted/uncertain session; never a clean close. */
    fun recover(expectedToken: String?): String = withFreeLock {
        val record = readRecord()
        if (record != null && expectedToken != null) {
            check(record.token == expectedToken) { "La sesión cambió durante la recuperación." }
        }
        newToken().also { writeRecord(Record(it, Stored.RECOVERED)) }
    }

    internal fun begin(token: String): Lease {
        require(validToken(token)) { "Token de sesión no válido." }
        ensureDirectory()
        val file = RandomAccessFile(File(directory, "session.lock"), "rw")
        var lock: FileLock? = null
        try {
            lock = tryLock(file) ?: error("El motor ya está activo.")
            val record = requireRecord(token)
            check(record.state == Stored.REQUESTED) { "Este lanzamiento ya no está autorizado." }
            writeRecord(Record(token, Stored.RUNNING))
            return Lease(token, file, lock)
        } catch (error: Exception) {
            lock?.release()
            file.close()
            throw error
        }
    }

    internal inner class Lease(
        val token: String,
        private val file: RandomAccessFile,
        private val lock: FileLock
    ) : Closeable {
        var shutdownRecorded = false
            private set

        fun completeShutdown() {
            check(lock.isValid && !shutdownRecorded) { "La sesión no mantiene su bloqueo." }
            check(requireRecord(token).state == Stored.RUNNING) { "La sesión del motor ha cambiado." }
            writeRecord(Record(token, Stored.CLOSED))
            shutdownRecorded = true
            // Keep the lock. Only process death (or test teardown) releases it.
        }

        override fun close() {
            try { if (lock.isValid) lock.release() } finally { file.close() }
        }
    }

    private fun <T> withFreeLock(action: () -> T): T {
        ensureDirectory()
        RandomAccessFile(File(directory, "session.lock"), "rw").use { file ->
            val lock = tryLock(file) ?: error("El motor sigue activo; vuelve al juego para cerrarlo.")
            try { return action() } finally { lock.release() }
        }
    }

    private fun tryLock(file: RandomAccessFile): FileLock? =
        try { file.channel.tryLock() } catch (_: OverlappingFileLockException) { null }

    private fun ensureDirectory() {
        check(directory.isDirectory || directory.mkdirs()) { "No se puede abrir el registro de sesión." }
    }

    private fun requireRecord(token: String): Record {
        require(validToken(token)) { "Token de sesión no válido." }
        return checkNotNull(readRecord()?.takeIf { it.token == token }) { "El token no corresponde a la sesión pendiente." }
    }

    private fun readRecord(): Record? {
        if (!recordFile.isFile || recordFile.length() !in 1L..512L) return null
        val fields = recordFile.readText(Charsets.UTF_8).split('\n')
        if (fields.size != 4 || fields[0] != "PSP_DOCK_SESSION_1" || !validToken(fields[1]) || fields[3].isNotEmpty()) return null
        val state = Stored.entries.firstOrNull { it.name == fields[2] } ?: return null
        return Record(fields[1], state)
    }

    private fun writeRecord(record: Record) {
        val temporary = File(directory, ".session-" + newToken() + ".tmp")
        try {
            FileOutputStream(temporary).use { output ->
                output.write(("PSP_DOCK_SESSION_1\n" + record.token + "\n" + record.state.name + "\n")
                    .toByteArray(Charsets.UTF_8))
                output.fd.sync()
            }
            Files.move(temporary.toPath(), recordFile.toPath(),
                StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally { if (temporary.exists()) temporary.delete() }
    }

    companion object {
        const val TOKEN_EXTRA = "com.pspdock.SESSION_TOKEN"
        private val tokenPattern = Regex("[a-f0-9]{32}")
        private fun validToken(token: String) = tokenPattern.matches(token)
        private fun newToken() = UUID.randomUUID().toString().replace("-", "")
    }
}
