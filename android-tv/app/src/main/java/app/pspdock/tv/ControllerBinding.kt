package app.pspdock.tv

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.os.SystemClock
import app.pspdock.tv.core.PspInput
import app.pspdock.tv.core.InputEpoch

/** Foreground UI consumer. Binding alone never starts the USB service or opens a device. */
class ControllerBinding(
    private val context: Context,
    private val input: (PspInput) -> Unit,
    private val status: (Boolean, String) -> Unit
) {
    private var remote: Messenger? = null
    private var bound = false
    private val epoch = InputEpoch()
    private var receiver: Messenger? = null
    private var connection: ServiceConnection? = null
    private var lastSequence = -1L
    private var lastSample = 0L
    private val handler = Handler(Looper.getMainLooper())
    private val watchdog = object : Runnable {
        override fun run() {
            if (!bound) return
            if (SystemClock.elapsedRealtime() - lastSample > 250) input(PspInput())
            handler.postDelayed(this, 100)
        }
    }
    private fun receive(message: Message) {
        val data = message.data
        if (data.getInt("protocol") == 1) {
            if (message.what == UsbControllerService.STATUS) {
                val active = data.getBoolean("active")
                if (!active) input(PspInput())
                status(active, data.getString("text").orEmpty())
            } else if (message.what == UsbControllerService.INPUT) {
                val sequence = data.getLong("sequence", -1)
                val sample = data.getLong("elapsedRealtime", -1)
                val age = SystemClock.elapsedRealtime() - sample
                if (sequence > lastSequence && age in 0..250) {
                    lastSequence = sequence
                    lastSample = sample
                    val x = data.getInt("x", 127)
                    val y = data.getInt("y", 127)
                    val hat = data.getInt("hat", 0)
                    val buttons = data.getInt("buttons", 0)
                    if (data.getBoolean("active") && x in 0..255 && y in 0..255 && hat in 0..8 && buttons in 0..4095) {
                        input(PspInput(x, y, hat, buttons))
                    } else input(PspInput())
                } else input(PspInput())
            }
        }
    }
    private fun connection(generation: Long) = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            if (!bound || !epoch.accepts(generation)) return
            lastSequence = -1
            lastSample = 0
            remote = Messenger(binder)
            send(UsbControllerService.REGISTER)
        }
        override fun onServiceDisconnected(name: ComponentName) {
            if (!bound || !epoch.accepts(generation)) return
            remote = null
            input(PspInput())
            status(false, context.getString(R.string.controller_stopped))
        }
        override fun onBindingDied(name: ComponentName) { onServiceDisconnected(name) }
        override fun onNullBinding(name: ComponentName) { onServiceDisconnected(name) }
    }

    fun bind(createAfterConsent: Boolean = false) {
        if (bound) return
        val generation = epoch.begin()
        receiver = Messenger(Handler(Looper.getMainLooper()) { message ->
            if (bound && epoch.accepts(generation)) receive(message)
            true
        })
        val nextConnection = connection(generation)
        connection = nextConnection
        bound = context.bindService(Intent(context, UsbControllerService::class.java), nextConnection,
            if (createAfterConsent) Context.BIND_AUTO_CREATE else 0)
        if (bound) watchdog.run()
        else {
            epoch.end(); receiver = null; connection = null
            input(PspInput()); status(false, context.getString(R.string.controller_stopped))
        }
    }

    fun unbind() {
        epoch.end()
        if (!bound) return
        send(UsbControllerService.UNREGISTER)
        bound = false
        connection?.let(context::unbindService)
        connection = null
        receiver = null
        remote = null
        handler.removeCallbacks(watchdog)
        input(PspInput())
    }

    private fun send(type: Int) {
        try { remote?.send(Message.obtain(null, type).apply { replyTo = receiver }) }
        catch (_: android.os.RemoteException) { input(PspInput()) }
    }
}
