package app.pspdock.tv.core

import org.junit.Assert.*
import org.junit.Test

class WindowsProtocolParityTest {
    private val vectors get() = checkNotNull(javaClass.getResourceAsStream("/pdk2-windows.tsv"))
        .bufferedReader().useLines { lines -> lines.filter { it.isNotBlank() && !it.startsWith('#') }.map { it.split('\t') }.toList() }
    private fun hex(value: String) = value.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    private fun ByteArray.hex() = joinToString("") { "%02x".format(it.toInt() and 255) }

    @Test fun allStatusVectorsMatchActualWindowsDecoder() {
        val statuses = vectors.filter { it[0] == "status" }
        assertTrue(statuses.size >= 30)
        for ((_, name, bytes, expected) in statuses) {
            if (expected == "reject") {
                try { Pdk2.decode(hex(bytes)); fail("Windows rejected $name but Android accepted it") }
                catch (_: IllegalArgumentException) { }
            } else {
                val s = Pdk2.decode(hex(bytes))
                val actual = listOf(s.firmwareRevision, s.capabilities, s.buildId, s.phase, s.sequence, s.result,
                    s.session, s.activity, s.unit, s.completed, s.total, s.transportWord, s.flags).joinToString("|")
                assertEquals(name, expected, actual)
            }
        }
    }

    @Test fun allCommandsMatchWindowsEncoderByteForByte() {
        for ((_, name, bytes, spec) in vectors.filter { it[0] == "command" }) {
            val s = spec.split('|')
            val actual = Pdk2.encodeCommand(s[0], s[1].toLong(), s[2].toInt(), s[3].toInt(), s[4].toInt(),
                s[5].toLong(), s[6].toInt(), s[7].toInt(), s[8].toULong(), s[9].toULong())
            assertEquals(name, bytes, actual.hex())
        }
    }

    @Test fun fragmentedOutputsAndCrcMatchWindowsWireContract() {
        for ((_, name, bytes, spec) in vectors.filter { it[0] == "output" }) {
            val (payload, token) = spec.split('|')
            val reports = Pdk2.outputReports(hex(payload), token.toInt())
            assertEquals(33, reports.size)
            assertTrue(reports.all { it.size == 4 })
            assertEquals(name, bytes, reports.flatMap { it.toList() }.toByteArray().hex())
        }
    }
}
