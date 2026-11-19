package app.pspdock.session

/** Pure input state shared with the embedded engine; it never opens USB or storage. */
class PspControllerState(private val sink: Sink) {
    interface Sink {
        fun keyDown(key: Int)
        fun keyUp(key: Int)
        fun axes(x: Float, y: Float, hatX: Float, hatY: Float)
    }

    private var sequence = -1L
    private var sampledAt = -1L
    private var buttons = 0
    private var axes = floatArrayOf(0f, 0f, 0f, 0f)
    private var armed = false

    /** Reject queued/replayed samples and require neutral after every hand-off. */
    fun accept(protocol: Int, nextSequence: Long, sampleTime: Long, now: Long,
               active: Boolean, nextButtons: Int, hat: Int, x: Int, y: Int): Boolean {
        if (protocol != PROTOCOL || nextSequence < 0 || nextSequence <= sequence ||
            sampleTime < 0 || sampleTime > now || now - sampleTime > STALE_MILLIS ||
            sampleTime < sampledAt || nextButtons !in 0..4095 || hat !in 0..8 ||
            x !in 0..255 || y !in 0..255) {
            neutralize()
            return false
        }
        sequence = nextSequence
        sampledAt = sampleTime
        if (!active) {
            neutralize()
            return true
        }
        if (!armed) {
            if (nextButtons == 0 && hat == 0 && kotlin.math.abs(x - 127) <= 8 &&
                kotlin.math.abs(y - 127) <= 8) armed = true
            return true
        }
        val known = nextButtons and 0x1ff
        for (bit in KEYS.indices) {
            val mask = 1 shl bit
            if ((buttons and mask) != (known and mask)) {
                if ((known and mask) != 0) sink.keyDown(KEYS[bit]) else sink.keyUp(KEYS[bit])
            }
        }
        buttons = known
        val hatX = when (hat) { 2, 3, 4 -> 1f; 6, 7, 8 -> -1f; else -> 0f }
        val hatY = when (hat) { 1, 2, 8 -> -1f; 4, 5, 6 -> 1f; else -> 0f }
        sendAxes(floatArrayOf(axis(x), axis(y), hatX, hatY))
        return true
    }

    fun expire(now: Long) {
        if (sampledAt >= 0 && (now < sampledAt || now - sampledAt > STALE_MILLIS)) neutralize()
    }

    /** Release only controls this client pressed; another frame must first be neutral. */
    fun neutralize() {
        for (bit in KEYS.indices) if ((buttons and (1 shl bit)) != 0) sink.keyUp(KEYS[bit])
        buttons = 0
        sendAxes(floatArrayOf(0f, 0f, 0f, 0f))
        sampledAt = -1L
        armed = false
    }

    fun reset() {
        neutralize()
        sequence = -1L
    }

    private fun sendAxes(next: FloatArray) {
        if (!axes.contentEquals(next)) {
            axes = next
            sink.axes(next[0], next[1], next[2], next[3])
        }
    }

    private fun axis(value: Int): Float =
        if (value <= 127) (value - 127) / 127f else (value - 127) / 128f

    companion object {
        const val PROTOCOL = 1
        const val STALE_MILLIS = 250L
        // Android KeyEvent: A,B,X,Y,L1,R1,SELECT,START,L2. PPSSPP's Android
        // default map uses L2 for pause, giving the PSP Home button that action.
        private val KEYS = intArrayOf(96, 97, 99, 100, 102, 103, 109, 108, 104)
    }
}
