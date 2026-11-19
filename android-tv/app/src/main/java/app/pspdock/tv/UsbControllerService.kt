package app.pspdock.tv

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import android.hardware.usb.UsbRequest
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.os.SystemClock
import app.pspdock.tv.core.Pdk2
import app.pspdock.tv.core.Pdk2Controller
import app.pspdock.tv.core.PspInput
import app.pspdock.tv.core.PspInputGate
import java.io.IOException
import java.nio.ByteBuffer
import java.util.concurrent.Executors
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/**
 * Explicit, visible controller session. Only the HID interface is claimed; Sony storage is never
 * claimed, written, reset or certified ejected. This service has no boot receiver or restart policy.
 * The emulator is another process in this APK and receives bounded, timestamped input over Binder.
 */
class UsbControllerService : Service() {
    private data class Client(val messenger: Messenger, val gate: PspInputGate = PspInputGate())
    private val clients = mutableMapOf<IBinder, Client>()
    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private val stopping = AtomicBoolean(false)
    private val sequence = AtomicLong(0)
    private var active = false
    private var started = false
    private var statusText = ""
    private val endpoint = Messenger(Handler(Looper.getMainLooper()) { message ->
        val client = message.replyTo
        when (message.what) {
            REGISTER -> if (client != null) {
                clients[client.binder] = Client(client)
                sendStatus(client)
            }
            UNREGISTER -> if (client != null) clients.remove(client.binder)
        }
        true
    })

    override fun onBind(intent: Intent?): IBinder = endpoint.binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            stopController()
            return START_NOT_STICKY
        }
        val deviceName = intent?.getStringExtra(DEVICE_NAME)
        // No implicit start, reconnect or claim after process recreation.
        if (intent?.action != START || deviceName.isNullOrEmpty() ||
            intent.getBooleanExtra(CONSENT, false) != true || started) {
            if (!started) stopSelf()
            return START_NOT_STICKY
        }
        started = true
        stopping.set(false)
        statusText = getString(R.string.controller_connecting)
        try {
            val notification = notification(statusText)
            if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            else startForeground(NOTIFICATION, notification)
        } catch (error: Exception) {
            started = false
            setStatus(false, getString(R.string.controller_failed, error.message ?: error.javaClass.simpleName))
            stopSelf()
            return START_NOT_STICKY
        }
        worker.execute { runController(deviceName) }
        return START_NOT_STICKY
    }

    private fun runController(name: String) {
        var connection: UsbDeviceConnection? = null
        var hid: UsbInterface? = null
        var request: UsbRequest? = null
        var claimed = false
        var previousIdle: Int? = null
        try {
            val manager = getSystemService(UsbManager::class.java)
            val candidates = manager.deviceList.values.filter { it.vendorId == Pdk2.VID && it.productId == Pdk2.PID }
            val device = candidates.singleOrNull()?.takeIf { it.deviceName == name }
                ?: error(getString(R.string.usb_ambiguous))
            require(manager.hasPermission(device)) { getString(R.string.usb_denied) }
            val opened = manager.openDevice(device) ?: error(getString(R.string.usb_open_failed))
            connection = opened
            val revision = Pdk2.usbRevision(opened.rawDescriptors)
            require(Pdk2.mayReadStatus(device.vendorId, device.productId, revision, candidates.size)) {
                getString(R.string.usb_revision_unsupported, revision)
            }
            val interfaces = (0 until device.interfaceCount).map(device::getInterface)
            require(interfaces.any { it.interfaceClass == UsbConstants.USB_CLASS_MASS_STORAGE }) {
                getString(R.string.usb_no_storage)
            }
            val selectedHid = interfaces.filter {
                it.interfaceClass == UsbConstants.USB_CLASS_HID && it.interfaceSubclass == 0 && it.interfaceProtocol == 0 &&
                    (0 until it.endpointCount).count { n ->
                        val ep = it.getEndpoint(n)
                        ep.direction == UsbConstants.USB_DIR_IN && ep.type == UsbConstants.USB_ENDPOINT_XFER_INT && ep.maxPacketSize == 8
                    } == 1
            }.singleOrNull() ?: error(getString(R.string.usb_no_hid))
            hid = selectedHid
            val input = (0 until selectedHid.endpointCount).map(selectedHid::getEndpoint).single {
                it.direction == UsbConstants.USB_DIR_IN && it.type == UsbConstants.USB_ENDPOINT_XFER_INT && it.maxPacketSize == 8
            }
            check(!stopping.get())
            // Force is restricted to this explicit controller session. A working interrupt reader
            // and Binder consumers replace the kernel's PSP input path; diagnostics never do this.
            claimed = opened.claimInterface(selectedHid, true)
            check(claimed) { getString(R.string.usb_claim_failed) }
            val open = opened
            val iface = selectedHid
            val wire = object : Pdk2Controller.Wire {
                override fun readStatus(): ByteArray {
                    checkAttached()
                    val payload = ByteArray(Pdk2.PAYLOAD_SIZE)
                    val count = open.controlTransfer(0xa1, 1, Pdk2.FEATURE_VALUE, iface.id, payload, payload.size, 750)
                    if (count != payload.size) throw IOException("GET_REPORT $count/${payload.size}")
                    checkAttached()
                    return payload
                }
                override fun writeOutput(report: ByteArray) {
                    checkAttached()
                    require(report.size == 4)
                    val count = open.controlTransfer(0x21, 9, Pdk2.OUTPUT_VALUE, iface.id, report, report.size, 750)
                    if (count != report.size) throw IOException("SET_REPORT Output $count/${report.size}")
                }
                private fun checkAttached() {
                    check(!stopping.get() && manager.deviceList[name]?.deviceId == device.deviceId) {
                        getString(R.string.usb_gone)
                    }
                }
            }
            val inputRequest = UsbRequest()
            request = inputRequest
            check(inputRequest.initialize(opened, input)) { getString(R.string.usb_request_failed) }
            val protocol = Pdk2Controller(wire, now = SystemClock::elapsedRealtime)
            protocol.connect()
            // Firmware only sends changed reports with idle=0. A verified 32ms HID idle rate
            // provides real device samples for both stale-input guards, even while holding a key.
            val idle = ByteArray(1)
            check(opened.controlTransfer(0xa1, 2, 0, selectedHid.id, idle, 1, 750) == 1)
            previousIdle = idle[0].toInt() and 255
            check(opened.controlTransfer(0x21, 10, 8 shl 8, selectedHid.id, null, 0, 750) == 0)
            check(opened.controlTransfer(0xa1, 2, 0, selectedHid.id, idle, 1, 750) == 1 && idle[0].toInt() == 8)
            main.post { setStatus(true, getString(R.string.controller_connected)) }
            val buffer = ByteBuffer.allocateDirect(8)
            var queued = false
            var lastCheck = 0L
            var lastInput = SystemClock.elapsedRealtime()
            while (!stopping.get()) {
                val now = SystemClock.elapsedRealtime()
                if (now - lastCheck >= 500) {
                    val status = protocol.status()
                    val statusTime = SystemClock.elapsedRealtime()
                    if (status.exitRequested) {
                        main.post { setStatus(false, getString(R.string.controller_exit_requested), exitRequested = true, observedAt = statusTime) }
                        break
                    }
                    lastCheck = now
                }
                if (!queued) {
                    buffer.clear()
                    check(inputRequest.queue(buffer)) { getString(R.string.usb_request_failed) }
                    queued = true
                }
                val completed = try { opened.requestWait(100) } catch (_: TimeoutException) { null }
                if (completed == inputRequest) {
                    queued = false
                    require(buffer.position() == 8) { "Incomplete USB input packet." }
                    buffer.flip()
                    val bytes = ByteArray(8)
                    buffer.get(bytes)
                    val inputState = PspInput.decode(bytes)
                    lastInput = SystemClock.elapsedRealtime()
                    val sampleTime = lastInput
                    main.post { publishInput(inputState, sampleTime) }
                } else if (completed != null) throw IOException("Unexpected USB request completion.")
                if (SystemClock.elapsedRealtime() - lastInput > 250) {
                    // Clear pressed buttons even if the kernel stops completing interrupt requests.
                    main.post { neutralize() }
                }
                check(SystemClock.elapsedRealtime() - lastInput < 3000) { getString(R.string.usb_input_stale) }
            }
        } catch (error: Exception) {
            if (!stopping.get()) main.post {
                setStatus(false, getString(R.string.controller_failed, error.message ?: error.javaClass.simpleName))
            }
        } finally {
            runCatching { request?.cancel() }
            runCatching { request?.close() }
            if (claimed && hid != null) {
                previousIdle?.let { idle -> runCatching {
                    connection?.controlTransfer(0x21, 10, idle shl 8, hid.id, null, 0, 300)
                } }
                runCatching { connection?.releaseInterface(hid) }
            }
            runCatching { connection?.close() }
            main.post {
                neutralize()
                active = false
                started = false
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
    }

    private fun setStatus(connected: Boolean, message: String, exitRequested: Boolean = false, observedAt: Long = SystemClock.elapsedRealtime()) {
        active = connected
        statusText = message
        if (!connected) neutralize()
        clients.values.toList().forEach { sendStatus(it.messenger, exitRequested, observedAt) }
        if (connected) {
            if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION, notification(message),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            else startForeground(NOTIFICATION, notification(message))
        }
    }

    private fun sendStatus(client: Messenger, exitRequested: Boolean = false, observedAt: Long = SystemClock.elapsedRealtime()) {
        send(client, STATUS, Bundle().apply {
            putInt("protocol", 1)
            putBoolean("active", active)
            putBoolean("exitRequested", exitRequested)
            putLong("elapsedRealtime", observedAt)
            putString("text", statusText)
        })
    }

    private fun publishInput(input: PspInput, sampledAt: Long) {
        if (!active || stopping.get() || SystemClock.elapsedRealtime() - sampledAt > 250) return
        val serial = sequence.incrementAndGet()
        clients.values.toList().forEach { client ->
            val state = client.gate.accept(input)
            send(client.messenger, INPUT, Bundle().apply {
                putInt("protocol", 1)
                putBoolean("active", true)
                putLong("sequence", serial)
                putLong("elapsedRealtime", sampledAt)
                putInt("buttons", state.buttons)
                putInt("hat", state.hat)
                putInt("x", state.x)
                putInt("y", state.y)
            })
        }
    }

    private fun neutralize() {
        clients.values.forEach { it.gate.reset() }
        val serial = sequence.incrementAndGet()
        clients.values.toList().forEach { client ->
            send(client.messenger, INPUT, Bundle().apply {
                putInt("protocol", 1)
                putBoolean("active", false)
                putLong("sequence", serial)
                putLong("elapsedRealtime", SystemClock.elapsedRealtime())
                putInt("buttons", 0); putInt("hat", 0); putInt("x", 127); putInt("y", 127)
            })
        }
    }

    private fun send(client: Messenger, type: Int, data: Bundle) {
        try { client.send(Message.obtain(null, type).apply { this.data = data }) }
        catch (_: android.os.RemoteException) { clients.remove(client.binder) }
    }

    private fun notification(text: String): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.controller_channel),
            NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 2, Intent(this, UsbControllerService::class.java).setAction(STOP),
            PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_dock)
            .setContentTitle(getString(R.string.app_name)).setContentText(text).setContentIntent(open)
            .setOngoing(true).setCategory(Notification.CATEGORY_SERVICE)
            .addAction(Notification.Action.Builder(null, getString(R.string.controller_stop), stop).build()).build()
    }

    private fun stopController() {
        stopping.set(true)
        setStatus(false, getString(R.string.controller_stopped))
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onTaskRemoved(rootIntent: Intent?) { stopController() }

    override fun onDestroy() {
        stopping.set(true)
        neutralize()
        worker.shutdownNow()
        clients.clear()
        super.onDestroy()
    }

    companion object {
        const val REGISTER = 1
        const val UNREGISTER = 2
        const val INPUT = 10
        const val STATUS = 11
        const val START = "app.pspdock.tv.START_CONTROLLER"
        const val STOP = "app.pspdock.tv.STOP_CONTROLLER"
        const val DEVICE_NAME = "deviceName"
        const val CONSENT = "controllerConsent"
        private const val CHANNEL = "psp_controller"
        private const val NOTIFICATION = 30
    }
}
