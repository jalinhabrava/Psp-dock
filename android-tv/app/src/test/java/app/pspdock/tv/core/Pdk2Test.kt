package app.pspdock.tv.core

import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class Pdk2Test {
    private fun frame(phase: Int = 2, flags: Int = 21, sequence: Long = 1, result: Int = 2,
                      activity: Int? = null, unit: Int = 0, done: ULong = 0uL, total: ULong = 0uL,
                      firmware: Int = Pdk2.FIRMWARE_REVISION, capabilities: Int = Pdk2.CAPABILITIES,
                      build: Long = Pdk2.BUILD_ID): ByteArray {
        val data = ByteBuffer.allocate(64).order(ByteOrder.LITTLE_ENDIAN)
        val work = activity ?: if (flags and 16 == 0) 0 else when (phase) {
            1 -> 1; 2 -> 5; 3 -> 4; 4 -> 6; 5 -> 7; else -> 0
        }
        data.put(byteArrayOf(80, 68, 75, 50, 3, 1, phase.toByte(), flags.toByte()))
        data.put(ByteArray(16) { (it + 1).toByte() })
        data.putInt(sequence.toInt()).putInt(result)
        data.put(byteArrayOf(78, 65, 84, 51))
        data.putShort(firmware.toShort()).putShort(capabilities.toShort()).putInt(build.toInt())
        data.put(work.toByte()).put(unit.toByte()).putShort(0)
        data.putLong(done.toLong()).putLong(total.toLong())
        return data.array()
    }

    @Test fun decodesLittleEndianIdentityAndUnsignedSequence() {
        val result = Pdk2.decode(frame(sequence = 0xffffffffL))
        assertEquals(0xffffffffL, result.sequence)
        assertEquals("0102030405060708090a0b0c0d0e0f10", result.session)
        assertEquals(0x0302, result.firmwareRevision)
        assertEquals(0x0007, result.capabilities)
        assertEquals(0x03020001L, result.buildId)
        assertTrue(result.connected)
        assertTrue(result.sonyStorageStarted)
        assertTrue(result.hostConfirmed)
        assertTrue(result.inputEnabled)
        assertFalse(result.pending)
    }

    @Test fun safePhaseDoesNotCertifyUnmount() {
        val status = Pdk2.decode(frame(phase = 5))
        assertEquals("Trabajo del cliente terminado", status.phaseName)
        assertTrue(status.sonyStorageStarted)
        assertFalse(status.inputEnabled)
    }

    @Test fun featureValueUsbDescriptorAndFirmwareIdentityAreDistinct() {
        assertEquals(0x0300, Pdk2.FEATURE_VALUE)
        assertTrue(Pdk2.mayReadStatus(0x054c, 0x01cd, 0x0100, 1))
        assertTrue(Pdk2.mayReadStatus(0x054c, 0x01cd, 0x0302, 1))
        assertFalse(Pdk2.mayReadStatus(0x054c, 0x01cd, 0x0300, 1))
        assertFalse(Pdk2.mayReadStatus(0x054c, 0x01cd, 0x0301, 1))
        assertFalse(Pdk2.mayReadStatus(0x054c, 0x01cd, 0x0302, 2))
        assertFalse(Pdk2.mayReadStatus(0x054c, 0x01cc, 0x0302, 1))
        assertFalse(Pdk2.mayReadStatus(0x1234, 0x01cd, 0x0302, 1))
        rejects { Pdk2.decode(frame(firmware = 0x0301)) }
        rejects { Pdk2.decode(frame(capabilities = 3)) }
        rejects { Pdk2.decode(frame(build = 0x03020002)) }
    }

    @Test fun sonyCompositeDescriptorDoesNotIdentifyFirmware() {
        val bytes = ByteArray(18)
        bytes[0] = 18
        bytes[1] = 1
        bytes[12] = 0
        bytes[13] = 1
        assertEquals(0x0100, Pdk2.usbRevision(bytes))
        assertNotEquals(Pdk2.FIRMWARE_REVISION, Pdk2.usbRevision(bytes))
    }

    @Test fun rejectsOldFrameWindowsPrefixAndUnknownHeaders() {
        rejects { Pdk2.decode(frame().copyOf(32)) }
        rejects { Pdk2.decode(frame().copyOf(63)) }
        rejects { Pdk2.decode(byteArrayOf(0) + frame()) }
        rejects { Pdk2.decode(frame().also { it[4] = 2 }) }
        rejects { Pdk2.decode(frame().also { it[5] = 2 }) }
        rejects { Pdk2.decode(frame().also { it[32] = 0 }) }
        rejects { Pdk2.decode(frame().also { it[46] = 1 }) }
        rejects { Pdk2.decode(frame(phase = 9)) }
        rejects { Pdk2.decode(frame(flags = 0x85)) }
        rejects { Pdk2.decode(frame().also { java.util.Arrays.fill(it, 8, 24, 0.toByte()) }) }
    }

    @Test fun rejectsIncoherentAcknowledgements() {
        rejects { Pdk2.decode(frame(flags = 23, sequence = 0, result = 1)) }
        rejects { Pdk2.decode(frame(flags = 23, result = 2)) }
        rejects { Pdk2.decode(frame(sequence = 0, result = 2)) }
        rejects { Pdk2.decode(frame(result = 1)) }
        assertEquals(-6, Pdk2.decode(frame(result = -6)).result)
        assertEquals(0L, Pdk2.decode(frame(phase = 1, flags = 5, sequence = 0, result = 0)).sequence)
    }

    @Test fun unclaimedSessionDoesNotEnablePspInputOrPublishProgress() {
        val status = Pdk2.decode(frame(phase = 1, flags = 5, sequence = 0, result = 0))
        assertFalse(status.hostConfirmed)
        assertFalse(status.inputEnabled)
        assertEquals(0uL, status.completed)
        rejects { Pdk2.decode(frame(phase = 1, flags = 5, activity = 1)) }
        rejects { Pdk2.decode(frame(phase = 1, flags = 5, unit = 1, done = 1uL, total = 2uL)) }
        assertTrue(Pdk2.decode(frame(phase = 1, activity = 2)).inputEnabled)
        assertFalse(Pdk2.decode(frame(phase = 1, activity = 1)).inputEnabled)
    }

    @Test fun progressUsesRealUnsigned64BitCountersAndMatchingActivity() {
        val status = Pdk2.decode(frame(phase = 3, unit = 1, done = ULong.MAX_VALUE - 1uL, total = ULong.MAX_VALUE))
        assertEquals(ULong.MAX_VALUE - 1uL, status.completed)
        assertEquals(ULong.MAX_VALUE, status.total)
        rejects { Pdk2.decode(frame(phase = 3, activity = 1)) }
        rejects { Pdk2.decode(frame(phase = 3, unit = 0, done = 1uL)) }
        rejects { Pdk2.decode(frame(phase = 3, unit = 1, done = 2uL, total = 1uL)) }
        rejects { Pdk2.decode(frame(phase = 3, unit = 2, total = 0uL)) }
        rejects { Pdk2.decode(frame(phase = 3, unit = 3, total = 1uL)) }
    }

    private fun rejects(block: () -> Unit) {
        try { block(); fail("Expected rejection") } catch (_: IllegalArgumentException) { }
    }
}
