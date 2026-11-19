package app.pspdock.tv.core

/** Unnumbered, eight-byte FuSa-compatible 0308 input report; hat occupies the low nibble. */
data class PspInput(val x: Int = 127, val y: Int = 127, val hat: Int = 0, val buttons: Int = 0) {
    val neutral get() = buttons == 0 && hat == 0 && x in 111..143 && y in 111..143

    companion object {
        fun decode(report: ByteArray): PspInput {
            require(report.size == 8) { "Incomplete controller input report." }
            val hat = report[4].toInt() and 15
            require(hat in 0..8 && report[6].toInt() == 0 && report[7].toInt() == 0) { "Invalid controller input layout." }
            return PspInput(report[0].toInt() and 255, report[1].toInt() and 255, hat,
                ((report[4].toInt() and 255) shr 4) or ((report[5].toInt() and 255) shl 4))
        }
    }
}

/** A new consumer must see a neutral sample before held input can reach its UI/game. */
class PspInputGate {
    private var armed = false
    fun reset() { armed = false }
    fun accept(input: PspInput): PspInput {
        if (!armed) {
            if (input.neutral) armed = true
            return PspInput()
        }
        return input
    }
}
