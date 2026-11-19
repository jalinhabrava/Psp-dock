package app.pspdock.tv

import android.content.Context
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.view.InputDevice
import app.pspdock.tv.core.Pdk2

/** This diagnostic remains read-only; the explicit controller service owns the separate input path. */
class UsbDiagnostics(private val context: Context) {
    private val manager = context.getSystemService(UsbManager::class.java)
    private fun text(id: Int, vararg args: Any) = context.getString(id, *args)

    fun candidates(): List<UsbDevice> = manager.deviceList.values.filter {
        it.vendorId == Pdk2.VID && it.productId in setOf(Pdk2.PID, 0x01cc, 0x01cb, 0x02d2)
    }.sortedBy { it.deviceName }

    fun describe(device: UsbDevice): String {
        val functions = (0 until device.interfaceCount).map { device.getInterface(it) }.map {
            when (it.interfaceClass) {
                UsbConstants.USB_CLASS_HID -> text(R.string.usb_hid)
                UsbConstants.USB_CLASS_MASS_STORAGE -> text(R.string.usb_storage)
                else -> text(R.string.usb_interface, it.interfaceClass)
            }
        }.distinct().joinToString(" + ")
        return text(R.string.usb_description, device.vendorId, device.productId, functions,
            text(if (manager.hasPermission(device)) R.string.permission_granted else R.string.permission_pending))
    }

    fun inputSummary(): String {
        val inputs = InputDevice.getDeviceIds().map { InputDevice.getDevice(it) }.filterNotNull().filter {
            (it.sources and InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD ||
                (it.sources and InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK
        }
        val psp = inputs.count { it.vendorId == Pdk2.VID && it.productId in setOf(Pdk2.PID, 0x01cc, 0x01cb) }
        return text(R.string.input_summary, inputs.size, psp)
    }

    fun readStatus(deviceName: String): String {
        val all = candidates()
        require(all.size == 1) { text(R.string.usb_ambiguous) }
        val device = all.single()
        require(device.deviceName == deviceName && manager.hasPermission(device)) { text(R.string.usb_denied) }
        val connection = manager.openDevice(device) ?: error(text(R.string.usb_open_failed))
        try {
            val revision = Pdk2.usbRevision(connection.rawDescriptors)
            require(Pdk2.mayReadStatus(device.vendorId, device.productId, revision, all.size)) {
                text(R.string.usb_revision_unsupported, revision)
            }
            val hid = (0 until device.interfaceCount).map { device.getInterface(it) }.filter {
                it.interfaceClass == UsbConstants.USB_CLASS_HID && it.interfaceSubclass == 0 &&
                    (0 until it.endpointCount).any { index ->
                        val endpoint = it.getEndpoint(index)
                        endpoint.direction == UsbConstants.USB_DIR_IN &&
                            endpoint.type == UsbConstants.USB_ENDPOINT_XFER_INT && endpoint.maxPacketSize == 8
                    }
            }.singleOrNull() ?: error(text(R.string.usb_no_hid))
            require((0 until device.interfaceCount).any {
                device.getInterface(it).interfaceClass == UsbConstants.USB_CLASS_MASS_STORAGE
            }) { text(R.string.usb_no_storage) }
            val payload = ByteArray(Pdk2.PAYLOAD_SIZE)
            // No claimInterface, detach, configuration change, SET_REPORT or work claim in diagnostics.
            val count = connection.controlTransfer(0xa1, 1, Pdk2.FEATURE_VALUE, hid.id, payload, payload.size, 750)
            if (count < 0) return text(R.string.usb_read_blocked, count)
            require(count == payload.size) { text(R.string.usb_short_report, count) }
            require(manager.deviceList[deviceName]?.deviceId == device.deviceId) { text(R.string.usb_gone) }
            val status = Pdk2.decode(payload)
            val phase = when (status.phase) {
                0 -> R.string.phase_boot
                Pdk2.STORAGE -> R.string.phase_storage
                Pdk2.CONTROLLER -> R.string.phase_controller
                Pdk2.COPYING -> R.string.phase_copy
                Pdk2.EXIT_SYNC -> R.string.phase_exit
                Pdk2.SAFE -> R.string.phase_safe
                else -> R.string.phase_error
            }
            return text(R.string.usb_status_details, status.firmwareRevision, status.buildId, revision,
                status.capabilities, text(phase), status.sequence, status.result,
                yes(status.hostConfirmed), yes(status.exitRequested), yes(status.uiSoundsReady), status.transportWord)
        } finally { connection.close() }
    }

    private fun yes(value: Boolean) = text(if (value) R.string.yes else R.string.no)
}
