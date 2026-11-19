package app.pspdock.session

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.concurrent.TimeUnit

class SessionLedgerTest {
    @get:Rule val temporary = TemporaryFolder()
    private fun ledger() = SessionLedger(temporary.newFolder())

    @Test fun cleanCloseNeedsMatchingTokenAndReleasedProcessLock() {
        val ledger = ledger()
        assertTrue(ledger.observe(null).mayLaunch)
        val token = ledger.prepare()
        assertEquals(SessionLedger.State.STARTING, ledger.observe(token).state)
        ledger.begin(token).use { active ->
            assertEquals(SessionLedger.State.RUNNING, ledger.observe(token).state)
            assertTrue(ledger.observe(token).mayResume)
            active.completeShutdown()
            assertEquals(SessionLedger.State.STOPPING, ledger.observe(token).state)
            assertFalse(ledger.observe(token).mayLaunch)
            assertFalse(ledger.observe(token).cleanShutdown)
        }
        val closed = ledger.observe(token)
        assertTrue(closed.cleanShutdown)
        assertTrue(closed.mayLaunch)
        assertFalse(closed.maySynchronizeToPsp)
        assertEquals(SessionLedger.State.UNKNOWN, ledger.observe("0".repeat(32)).state)
        assertFalse(ledger.observe(null).cleanShutdown)
    }

    @Test fun nativeCrashAndExplicitRecoveryAreNotCleanShutdown() {
        val ledger = ledger()
        val token = ledger.prepare()
        ledger.begin(token).close() // Simulate abrupt process death without native shutdown.
        val interrupted = ledger.observe(token)
        assertEquals(SessionLedger.State.INTERRUPTED, interrupted.state)
        assertTrue(interrupted.mayRecover)
        assertFalse(interrupted.mayLaunch)
        assertFalse(interrupted.cleanShutdown)
        val recovery = ledger.recover(token)
        assertTrue(ledger.observe(recovery).mayLaunch)
        assertFalse(ledger.observe(recovery).cleanShutdown)
        assertFalse(ledger.observe(recovery).maySynchronizeToPsp)
    }

    @Test fun duplicateStartWrongTokenAndActiveRecoveryAreRejected() {
        val ledger = ledger()
        val token = ledger.prepare()
        fails { ledger.prepare() }
        fails { ledger.begin("1".repeat(32)) }
        ledger.begin(token).use {
            fails { ledger.begin(token) }
            fails { ledger.prepare() }
            fails { ledger.recover(token) }
            fails { ledger.markLaunchFailed(token) }
        }
    }

    @Test fun cancelledPendingStartCannotInitializeNativeLater() {
        val ledger = ledger()
        val token = ledger.prepare()
        val recovered = ledger.recover(token)
        fails { ledger.begin(token) }
        assertEquals(SessionLedger.State.RECOVERED, ledger.observe(recovered).state)
        val replacement = ledger.prepare()
        assertNotEquals(token, replacement)
        ledger.markLaunchFailed(replacement)
        assertTrue(ledger.observe(replacement).mayLaunch)
        assertFalse(ledger.observe(replacement).cleanShutdown)
    }

    @Test fun corruptOrStaleRecordFailsClosedUntilExplicitRecovery() {
        val folder = temporary.newFolder()
        val ledger = SessionLedger(folder)
        File(folder, "session.state").writeText("CLOSED")
        val snapshot = ledger.observe(null)
        assertEquals(SessionLedger.State.UNKNOWN, snapshot.state)
        assertFalse(snapshot.mayLaunch)
        fails { ledger.prepare() }
        val recovered = ledger.recover(null)
        assertTrue(ledger.observe(recovered).mayLaunch)
        val token = ledger.prepare()
        fails { ledger.recover(recovered) }
        assertEquals(SessionLedger.State.STARTING, ledger.observe(token).state)
    }

    @Test(timeout = 15000) fun separateProcessOwnsLockAndDeathRemainsUnclean() {
        val directory = temporary.newFolder()
        val ledger = SessionLedger(directory)
        val token = ledger.prepare()
        val classpath = listOf(SessionLedgerTest::class.java, SessionLedger::class.java, Unit::class.java)
            .map { File(it.protectionDomain.codeSource.location.toURI()).path }.distinct()
            .joinToString(File.pathSeparator)
        val java = File(System.getProperty("java.home"), "bin/java").path
        val process = ProcessBuilder(java, "-cp", classpath, "app.pspdock.session.LockOwner",
            directory.path, token).redirectErrorStream(true).start()
        try {
            assertEquals("LOCKED", process.inputStream.bufferedReader().readLine())
            assertEquals(SessionLedger.State.RUNNING, ledger.observe(token).state)
            fails { ledger.recover(token) }
            process.destroyForcibly()
            assertTrue(process.waitFor(3, TimeUnit.SECONDS))
            assertEquals(SessionLedger.State.INTERRUPTED, ledger.observe(token).state)
            assertFalse(ledger.observe(token).cleanShutdown)
        } finally { if (process.isAlive) process.destroyForcibly() }
    }

    private fun fails(action: () -> Unit) {
        try { action(); fail("Expected fail-closed rejection") }
        catch (_: IllegalStateException) { }
        catch (_: IllegalArgumentException) { }
    }
}

/** A distinct JVM stands in for Android's separate :emulation process. */
object LockOwner {
    @JvmStatic fun main(args: Array<String>) {
        SessionLedger(File(args[0])).begin(args[1]).use {
            println("LOCKED")
            System.out.flush()
            System.`in`.read()
        }
    }
}
