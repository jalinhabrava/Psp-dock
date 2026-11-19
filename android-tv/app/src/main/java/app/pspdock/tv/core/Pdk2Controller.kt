package app.pspdock.tv.core

import java.io.IOException

/** One owner, one command at a time. Transport uncertainty is never permission to replay. */
class Pdk2Controller(
    private val wire: Wire,
    private val now: () -> Long = { System.nanoTime() / 1_000_000L },
    private val pause: (Long) -> Unit = Thread::sleep,
    private val token: () -> Int = { java.security.SecureRandom().nextInt(256) }
) {
    interface Wire {
        fun readStatus(): ByteArray
        fun writeOutput(report: ByteArray)
    }
    class Uncertain(message: String, cause: Throwable? = null) : IOException(message, cause)
    private var identity: List<Long>? = null
    private var session: String? = null
    private var acknowledgedSequence: Long? = null
    private var poisoned = false

    fun connect(): Pdk2.Status {
        check(session == null && !poisoned) { "Create a new controller after an interrupted connection." }
        val status = Pdk2.decode(wire.readStatus())
        require(status.firmwareRevision == Pdk2.FIRMWARE_REVISION) { "Controller preview requires current firmware 0308." }
        require(status.phase in setOf(Pdk2.STORAGE, Pdk2.CONTROLLER) &&
            (status.hostConfirmed || status.phase == Pdk2.STORAGE)) { "Restart PSP Dock on the console before connecting." }
        requireUsable(status)
        identity = status.identity
        session = status.session
        acknowledgedSequence = status.sequence
        if (!status.hostConfirmed) command(Pdk2.HOST_BEGIN, Pdk2.STORAGE)
        return command(Pdk2.SET_PHASE, Pdk2.CONTROLLER)
    }

    private fun requireUsable(status: Pdk2.Status) {
        require(status.connected && status.sonyStorageStarted && !status.pending &&
            !status.shutdownRequested && !status.exitRequested && status.result >= 0 && status.phase != Pdk2.ERROR) {
            "PSP connection is pending, stopping, disconnected or in error."
        }
    }

    private fun sameDevice(status: Pdk2.Status) {
        require(status.session == session && status.identity == identity &&
            status.connected && status.sonyStorageStarted) { "PSP session or firmware identity changed." }
    }

    private fun command(operation: Int, phase: Int): Pdk2.Status {
        check(!poisoned && session != null)
        require(operation in setOf(Pdk2.HOST_BEGIN, Pdk2.SET_PHASE)) // No sync, SAFE or terminal shutdown API.
        require(phase in setOf(Pdk2.STORAGE, Pdk2.CONTROLLER))
        val before = Pdk2.decode(wire.readStatus())
        sameDevice(before)
        requireUsable(before)
        require(before.sequence == acknowledgedSequence) { "Another command changed this session." }
        require(operation == Pdk2.HOST_BEGIN || before.hostConfirmed) { "Host handshake is missing." }
        if ((operation == Pdk2.SET_PHASE && before.phase == phase) ||
            (operation == Pdk2.HOST_BEGIN && before.hostConfirmed)) return before
        val sequence = before.sequence + 1L
        val payload = Pdk2.encodeCommand(before.session, sequence, phase, operation,
            before.firmwareRevision, before.buildId)
        try {
            // Any partial write or failed commit invalidates this client. No automatic resubmission.
            for (report in Pdk2.outputReports(payload, token())) wire.writeOutput(report)
            val deadline = now() + 2500L
            while (now() < deadline) {
                val after = Pdk2.decode(wire.readStatus())
                sameDevice(after)
                require(!after.exitRequested && !after.shutdownRequested && after.sequence <= sequence && after.result >= 0) {
                    "PSP rejected or superseded the command."
                }
                if (after.sequence == sequence && !after.pending && after.result == 2) {
                    require(after.phase == phase && after.hostConfirmed) { "ACK phase or host identity mismatch." }
                    acknowledgedSequence = sequence
                    return after
                }
                pause(25)
            }
            throw Uncertain("Command ACK timed out. Reconnect explicitly; the command will not be replayed.")
        } catch (error: Exception) {
            poisoned = true
            throw if (error is Uncertain) error else Uncertain("Command delivery is uncertain; no replay.", error)
        }
    }

    /** Live read guard also catches nonce changes, foreign commands and firmware's exit chord. */
    fun status(): Pdk2.Status {
        check(!poisoned && session != null)
        return Pdk2.decode(wire.readStatus()).also {
            sameDevice(it)
            require(it.sequence == acknowledgedSequence) { "PSP command sequence changed outside this controller." }
            if (!it.exitRequested) {
                requireUsable(it)
                require(it.phase == Pdk2.CONTROLLER && it.inputEnabled) { "PSP no longer confirms controller mode." }
            }
        }
    }
}
