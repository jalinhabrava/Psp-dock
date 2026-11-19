package app.pspdock.tv.core

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.io.File
import java.io.InterruptedIOException
import java.security.MessageDigest

class VerifiedImportTest {
    @get:Rule val temporary = TemporaryFolder()
    private fun game() = "CISO".toByteArray() + ByteArray(600000) { (it % 251).toByte() }
    private fun run(bytes: ByteArray, folder: File, expected: Long? = bytes.size.toLong(),
                    cancelled: () -> Boolean = { false }, free: () -> Long = { 20L * 1024 * 1024 * 1024 }) =
        VerifiedImport.copy(ByteArrayInputStream(bytes), folder, "Test.cso", expected, cancelled, free) { _, _ -> }

    @Test fun verifiedImportIsContentAddressedAndDuplicateIsPreserved() {
        val folder = temporary.newFolder()
        val bytes = game()
        val result = run(bytes, folder)
        assertEquals(bytes.size.toLong(), result.bytes)
        assertArrayEquals(bytes, File(folder, result.id).readBytes())
        val expected = MessageDigest.getInstance("SHA-256").digest(bytes)
            .joinToString("") { "%02x".format(it.toInt() and 255) }
        assertEquals(expected + ".cso", result.id)
        assertFalse(result.alreadyPresent)
        assertTrue(run(bytes, folder).alreadyPresent)
        assertEquals(1, folder.listFiles()!!.size)
    }

    @Test fun interruptedImportNeverPublishesFile() {
        val folder = temporary.newFolder()
        var checks = 0
        try {
            run(game(), folder, cancelled = { ++checks >= 2 })
            fail("Expected cancellation")
        } catch (_: InterruptedIOException) { }
        assertEquals(0, folder.listFiles()!!.size)
    }

    @Test fun rejectsWrongSizeAndMisnamedFilesWithoutPublishing() {
        val folder = temporary.newFolder()
        for ((content, expected) in listOf(game() to 999L, "not a game".toByteArray() to 10L)) {
            try { run(content, folder, expected); fail("Expected rejection") }
            catch (_: IllegalArgumentException) { }
        }
        assertEquals(0, folder.listFiles()!!.size)
    }

    @Test fun lowSpaceAndUnknownLengthAreHandled() {
        val folder = temporary.newFolder()
        try {
            run(game(), folder, expected = null, free = { VerifiedImport.FREE_RESERVE })
            fail("Expected space rejection")
        } catch (_: IllegalArgumentException) { }
        assertEquals(0, folder.listFiles()!!.size)
        assertTrue(run(game(), folder, expected = null).bytes > 0)
    }

    @Test fun corruptExistingFileIsNotSilentlyReplaced() {
        val folder = temporary.newFolder()
        val result = run(game(), folder)
        val existing = File(folder, result.id)
        existing.writeText("corrupt")
        try { run(game(), folder); fail("Expected integrity rejection") }
        catch (_: IllegalArgumentException) { }
        assertEquals("corrupt", existing.readText())
        assertEquals(1, folder.listFiles()!!.size)
    }
}
