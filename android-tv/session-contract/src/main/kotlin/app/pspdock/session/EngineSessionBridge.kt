package app.pspdock.session

import java.io.File

/** Called by the patched upstream Activity, in the private :emulation process only. */
object EngineSessionBridge {
    private var lease: SessionLedger.Lease? = null

    /** Configuration/graphics recreation may reuse the same process and token. */
    @JvmStatic
    @Synchronized
    fun begin(directory: File, token: String?): Boolean {
        if (token == null) return false
        lease?.let { return it.token == token && !it.shutdownRecorded }
        return try {
            lease = SessionLedger(directory).begin(token)
            true
        } catch (_: Exception) { false }
    }

    /**
     * Call only after native shutdown completed for an explicit finish, never on
     * pause/resume, graphics recreation, or generic onDestroy. Then exit :emulation.
     * A write failure remains unclean; the file lock still lives until process death.
     */
    @JvmStatic
    @Synchronized
    fun completeShutdown(): Boolean {
        return try {
            val active = lease ?: return false
            active.completeShutdown()
            true
        } catch (_: Exception) { false }
    }
}
