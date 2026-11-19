package app.pspdock.tv.core

/** Reject callbacks captured before a pause/unbind, even if they still fit the input TTL. */
class InputEpoch {
    private var current = 0L
    private var active = false
    fun begin(): Long { current++; active = true; return current }
    fun end() { current++; active = false }
    fun accepts(epoch: Long) = active && epoch == current
}

/** A release belongs to this screen only if this screen observed its initial press. */
class UiPressGate<T : Any> {
    private data class Key(val code: Int, val device: Int)
    private data class Press<T>(val focus: T?)
    private val presses = mutableMapOf<Key, Press<T>>()

    fun down(code: Int, device: Int, focus: T?, repeated: Boolean) {
        if (!repeated) presses.putIfAbsent(Key(code, device), Press(focus))
    }

    fun up(code: Int, device: Int, currentFocus: T?, sameFocus: Boolean, cancelled: Boolean): Boolean {
        val press = presses.remove(Key(code, device)) ?: return false
        return !cancelled && (!sameFocus || (press.focus != null && press.focus === currentFocus))
    }

    fun clear() { presses.clear() }
}
