package app.pspdock.tv.core

import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder

class Pdk2ControllerTest {
    private fun frame(phase: Int = 1, flags: Int = 5, sequence: Long = 0, result: Int = 0) =
        Pdk2.encodeCommand("0102030405060708090a0b0c0d0e0f10", maxOf(sequence,1), phase, Pdk2.SET_PHASE).apply {
            this[5] = 1; this[7] = flags.toByte()
            ByteBuffer.wrap(this).order(ByteOrder.LITTLE_ENDIAN).putInt(24, sequence.toInt()).putInt(28, result)
            this[44] = if (flags and 16 == 0) 0 else if (phase == 2) 5 else 1
        }

    private inner class FakeWire : Pdk2Controller.Wire {
        var state = frame()
        val writes = mutableListOf<ByteArray>()
        var failAfter: Int? = null
        var apply = true
        var transform: ((ByteArray) -> ByteArray)? = null
        private val pending = ByteArray(64)
        override fun readStatus() = state.copyOf()
        override fun writeOutput(report: ByteArray) {
            writes += report.copyOf()
            if (writes.size == failAfter) throw IOException("uncertain transport")
            val kind = report[0].toInt() and 255
            if (kind in 0xc0..0xdf) {
                val offset = (kind and 31) * 2
                pending[offset] = report[2]; pending[offset + 1] = report[3]
            } else if (kind == 0xe0 && apply) {
                val phase = pending[6].toInt()
                val sequence = ByteBuffer.wrap(pending).order(ByteOrder.LITTLE_ENDIAN).getInt(24).toLong() and 0xffffffffL
                state = frame(phase,21,sequence,2).let { transform?.invoke(it) ?: it }
            }
        }
    }

    @Test fun beginsThenEntersControllerWithTwoAcknowledgedFragmentBatches() {
        val wire = FakeWire()
        val protocol = Pdk2Controller(wire, token = { 7 })
        val status = protocol.connect()
        assertTrue(status.inputEnabled)
        assertEquals(2L, status.sequence)
        assertEquals(66, wire.writes.size)
        assertEquals(0xe0, wire.writes[32][0].toInt() and 255)
        assertEquals(status, protocol.status())
    }
    @Test fun verifiedAlreadyControllerSessionDoesNotSendAnUnneededCommand() {
        val wire = FakeWire().apply { state = frame(2,21,4,2) }
        assertTrue(Pdk2Controller(wire).connect().inputEnabled)
        assertTrue(wire.writes.isEmpty())
    }
    @Test fun partialDeliveryIsUncertainAndNeverReplayed() {
        val wire = FakeWire().apply { failAfter = 4 }
        val client = Pdk2Controller(wire)
        try { client.connect(); fail("Expected uncertainty") } catch (_: Pdk2Controller.Uncertain) { }
        assertEquals(4, wire.writes.size)
        try { client.connect(); fail("Expected closed client") } catch (_: IllegalStateException) { }
        assertEquals(4, wire.writes.size)
    }
    @Test fun missingAckExpiresWithoutSendingAgain() {
        val wire = FakeWire().apply { apply = false }
        var time = 0L
        val client = Pdk2Controller(wire, now = { time }, pause = { time += it })
        try { client.connect(); fail("Expected timeout") } catch (_: Pdk2Controller.Uncertain) { }
        assertEquals(33, wire.writes.size)
    }
    @Test fun nonceChangeOrWrongPhaseCannotConfirmACommand() {
        for (change in listOf<(ByteArray) -> ByteArray>(
            { it.apply { this[8] = 99 } },
            { it.apply { this[6] = 2; this[44] = 5 } })) {
            val wire = FakeWire().apply { transform = change }
            try { Pdk2Controller(wire).connect(); fail("Expected bad ACK rejection") }
            catch (_: Pdk2Controller.Uncertain) { }
            assertEquals(33, wire.writes.size)
        }
    }
    @Test fun copyingOrTerminalSessionsCannotBeClaimed() {
        for (phase in listOf(3,4,5)) {
            val wire = FakeWire().apply { state = frame(phase,21,4,2).apply { this[44] = when(phase) { 3 -> 4; 4 -> 6; else -> 7 } } }
            try { Pdk2Controller(wire).connect(); fail("Expected unsafe phase rejection") }
            catch (_: IllegalArgumentException) { }
            assertTrue(wire.writes.isEmpty())
        }
    }
    @Test fun exitChordIsReportedWithoutSendingSafeOrShutdown() {
        val wire = FakeWire()
        val client = Pdk2Controller(wire)
        client.connect()
        wire.state[7] = 29
        assertTrue(client.status().exitRequested)
        assertFalse(client.status().inputEnabled)
        assertEquals(66, wire.writes.size)
    }
}
