package app.pspdock.tv.core

import org.junit.Assert.*
import org.junit.Test

class UiInputHandoffTest {
    @Test fun anUpFromTheGameCannotActivateTheDock() {
        val focus = Any()
        val gate = UiPressGate<Any>()
        assertFalse(gate.up(96, 1, focus, true, false))
        assertFalse(gate.up(97, 1, focus, false, false))
        gate.down(96, 1, focus, false)
        gate.clear() // pause and later resume
        assertFalse(gate.up(96, 1, focus, true, false))
    }
    @Test fun repeatWithoutInitialDownAndCancelledReleaseAreIgnored() {
        val focus = Any()
        val gate = UiPressGate<Any>()
        gate.down(96, 1, focus, true)
        assertFalse(gate.up(96, 1, focus, true, false))
        gate.down(96, 1, focus, false)
        assertFalse(gate.up(96, 1, focus, true, true))
    }
    @Test fun changedFocusOrAnotherControllerCannotCompleteAPress() {
        val focus = Any()
        val gate = UiPressGate<Any>()
        gate.down(96, 1, focus, false)
        assertFalse(gate.up(96, 2, focus, true, false))
        assertFalse(gate.up(96, 1, Any(), true, false))
        gate.down(96, 1, focus, false)
        assertTrue(gate.up(96, 1, focus, true, false))
        assertFalse(gate.up(96, 1, focus, true, false))
    }
    @Test fun backRequiresItsOwnPressButDoesNotRequireAFocusableView() {
        val gate = UiPressGate<Any>()
        gate.down(97, 1, null, false)
        assertTrue(gate.up(97, 1, null, false, false))
    }
    @Test fun callbacksFromPreviousBindingRemainInvalidAfterRebind() {
        val gate = InputEpoch()
        val first = gate.begin()
        assertTrue(gate.accepts(first))
        gate.end()
        assertFalse(gate.accepts(first))
        val second = gate.begin()
        assertFalse(gate.accepts(first)) // queued neutral and press from the previous Messenger
        assertTrue(gate.accepts(second))
        gate.end()
        assertFalse(gate.accepts(second))
    }
}
