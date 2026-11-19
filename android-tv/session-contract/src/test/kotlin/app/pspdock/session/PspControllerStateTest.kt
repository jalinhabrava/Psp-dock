package app.pspdock.session

import org.junit.Assert.*
import org.junit.Test

class PspControllerStateTest {
    private class Events : PspControllerState.Sink {
        val downs = mutableListOf<Int>()
        val ups = mutableListOf<Int>()
        var axes = listOf(0f, 0f, 0f, 0f)
        override fun keyDown(key: Int) { downs += key }
        override fun keyUp(key: Int) { ups += key }
        override fun axes(x: Float, y: Float, hatX: Float, hatY: Float) { axes = listOf(x, y, hatX, hatY) }
    }

    private fun PspControllerState.frame(sequence: Long, buttons: Int = 0, hat: Int = 0,
                                        x: Int = 127, y: Int = 127, active: Boolean = true): Boolean =
        accept(1, sequence, sequence * 10, sequence * 10, active, buttons, hat, x, y)

    @Test fun heldLaunchButtonDoesNotLeakIntoGame() {
        val events = Events()
        val state = PspControllerState(events)
        state.frame(0, buttons = 1)
        state.frame(1, buttons = 1)
        assertTrue(events.downs.isEmpty())
        state.frame(2)
        state.frame(3, buttons = 1)
        assertEquals(listOf(96), events.downs)
    }

    @Test fun mapsAllPspButtonsAndReleasesOnlyPressedKeys() {
        val events = Events()
        val state = PspControllerState(events)
        state.frame(0)
        state.frame(1, buttons = 0x1ff)
        assertEquals(listOf(96, 97, 99, 100, 102, 103, 109, 108, 104), events.downs)
        state.frame(2)
        assertEquals(events.downs, events.ups)
        state.neutralize()
        assertEquals(9, events.ups.size)
    }

    @Test fun decodesPspHatAndAnalogEndpoints() {
        val events = Events()
        val state = PspControllerState(events)
        state.frame(0)
        val hats = listOf(0f to -1f, 1f to -1f, 1f to 0f, 1f to 1f,
                         0f to 1f, -1f to 1f, -1f to 0f, -1f to -1f)
        hats.forEachIndexed { index, expected ->
            state.frame(index + 1L, hat = index + 1, x = 0, y = 255)
            assertEquals(listOf(-1f, 1f, expected.first, expected.second), events.axes)
        }
        state.frame(9)
        assertEquals(listOf(0f, 0f, 0f, 0f), events.axes)
    }

    @Test fun timeoutNeutralizesAndRequiresAFreshNeutralFrame() {
        val events = Events()
        val state = PspControllerState(events)
        state.frame(0)
        state.frame(1, buttons = 1, x = 0)
        state.expire(261)
        assertEquals(listOf(96), events.ups)
        assertEquals(listOf(0f, 0f, 0f, 0f), events.axes)
        state.frame(30, buttons = 1)
        assertEquals(1, events.downs.size)
        state.frame(31)
        state.frame(32, buttons = 1)
        assertEquals(2, events.downs.size)
    }

    @Test fun staleReplayedFutureAndMalformedFramesCannotHoldButtons() {
        val invalid = listOf(
            longArrayOf(1, 1, 10, 20, 1, 0, 127, 127), // repeated sequence
            longArrayOf(1, 2, 20, 300, 1, 0, 127, 127), // old sample
            longArrayOf(1, 2, 30, 20, 1, 0, 127, 127), // future sample
            longArrayOf(2, 2, 20, 20, 1, 0, 127, 127), // unknown protocol
            longArrayOf(1, 2, 20, 20, 1, 9, 127, 127), // invalid hat
            longArrayOf(1, 2, 20, 20, 4096, 0, 127, 127), // invalid buttons
            longArrayOf(1, 2, 20, 20, 1, 0, 256, 127)) // invalid axis
        invalid.forEach { p ->
            val events = Events()
            val state = PspControllerState(events)
            state.frame(0)
            state.frame(1, buttons = 1)
            assertFalse(state.accept(p[0].toInt(), p[1], p[2], p[3], true,
                p[4].toInt(), p[5].toInt(), p[6].toInt(), p[7].toInt()))
            assertEquals(listOf(96), events.ups)
        }
    }

    @Test fun inactiveAndNewBindingRequireFreshNeutralInsteadOfReplay() {
        val events = Events()
        val state = PspControllerState(events)
        state.frame(0)
        state.frame(1, buttons = 1)
        state.frame(2, active = false)
        state.frame(3, buttons = 1)
        assertEquals(1, events.downs.size)
        assertEquals(listOf(96), events.ups)
        state.reset()
        state.frame(0)
        state.frame(1, buttons = 2)
        assertEquals(listOf(96, 97), events.downs)
    }
}
