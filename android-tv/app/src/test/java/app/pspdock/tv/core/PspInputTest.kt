package app.pspdock.tv.core

import org.junit.Assert.*
import org.junit.Test

class PspInputTest {
    @Test fun decodesPackedHatAndAllTwelveButtons() {
        val packet = byteArrayOf(0, 255.toByte(), 127, 127, 0xf8.toByte(), 0xff.toByte(), 0, 0)
        val state = PspInput.decode(packet)
        assertEquals(0, state.x); assertEquals(255, state.y)
        assertEquals(8, state.hat); assertEquals(0xfff, state.buttons)
    }
    @Test fun crossOccupiesHighNibbleOfHatByteNotNextByte() {
        assertEquals(1, PspInput.decode(byteArrayOf(127,127,127,127,0x10,0,0,0)).buttons)
        assertEquals(16, PspInput.decode(byteArrayOf(127,127,127,127,0,1,0,0)).buttons)
    }
    @Test fun rejectsTruncatedInputInvalidHatAndReservedBits() {
        for (packet in listOf(ByteArray(7), byteArrayOf(127,127,127,127,9,0,0,0),
            byteArrayOf(127,127,127,127,0,0,1,0))) {
            try { PspInput.decode(packet); fail("Expected rejection") } catch (_: IllegalArgumentException) { }
        }
    }
    @Test fun newConsumerRequiresReleaseAndResetDoesNotReplayHeldButtons() {
        val gate = PspInputGate()
        val held = PspInput(buttons = 1)
        assertEquals(PspInput(), gate.accept(held))
        assertEquals(PspInput(), gate.accept(PspInput()))
        assertEquals(held, gate.accept(held))
        gate.reset()
        assertEquals(PspInput(), gate.accept(held))
        assertEquals(PspInput(), gate.accept(PspInput(x = 0)))
        gate.accept(PspInput())
        assertEquals(held, gate.accept(held))
    }
}
