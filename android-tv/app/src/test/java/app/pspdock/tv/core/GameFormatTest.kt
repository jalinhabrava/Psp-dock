package app.pspdock.tv.core

import org.junit.Assert.*
import org.junit.Test

class GameFormatTest {
    @Test fun validatesRealSignaturesRatherThanExtensionAlone() {
        assertTrue(GameFormat.validates("cso", "CISO".toByteArray()))
        assertTrue(GameFormat.validates("chd", "MComprHD".toByteArray()))
        assertTrue(GameFormat.validates("pbp", byteArrayOf(0, 80, 66, 80)))
        assertFalse(GameFormat.validates("iso", "CISO".toByteArray()))
        assertFalse(GameFormat.validates("cso", "fake file renamed.cso".toByteArray()))
        val iso = ByteArray(32774)
        "CD001".toByteArray().copyInto(iso, 32769)
        assertTrue(GameFormat.validates("iso", iso))
        assertFalse(GameFormat.validates("iso", iso.copyOf(32772)))
    }

    @Test fun providerIdsCannotEscapeLibrary() {
        val id = "a".repeat(64) + ".iso"
        assertTrue(GameFormat.validId(id))
        for (unsafe in listOf("../" + id, id + "/extra", "%2e%2e", "index.json", "A".repeat(64) + ".iso")) {
            assertFalse(unsafe, GameFormat.validId(unsafe))
        }
        assertEquals("game.ISO", GameFormat.title("../../game.ISO"))
        assertEquals("iso", GameFormat.extension("game.ISO"))
    }
}
