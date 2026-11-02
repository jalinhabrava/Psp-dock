package app.pspdock.tv

import android.content.Context
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.view.InputDevice
import app.pspdock.tv.core.Pdk2

class UsbDiagnostics(context: Context) {
    private val manager = context.getSystemService(UsbManager::class.java)

    fun candidates(): List<UsbDevice> = manager.deviceList.values.filter {
        it.vendorId == Pdk2.VID && it.productId in setOf(Pdk2.PID, 0x01cc, 0x01cb, 0x02d2)
    }.sortedBy { it.deviceName }

    fun describe(device: UsbDevice): String {
        val interfaces = (0 until device.interfaceCount).map { device.getInterface(it) }
        val functions = interfaces.map {
            when (it.interfaceClass) {
                UsbConstants.USB_CLASS_HID -> "mando HID"
                UsbConstants.USB_CLASS_MASS_STORAGE -> "almacenamiento USB"
                else -> "interfaz " + it.interfaceClass
            }
        }.distinct().joinToString(" + ")
        return "PSP USB · " + "%04X:%04X".format(device.vendorId, device.productId) +
            "\n" + functions + "\nPermiso de la aplicación: " +
            if (manager.hasPermission(device)) "concedido" else "pendiente"
    }

    fun inputSummary(): String {
        val inputs = InputDevice.getDeviceIds().map { InputDevice.getDevice(it) }.filterNotNull().filter {
            (it.sources and InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD ||
                (it.sources and InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK
        }
        val psp = inputs.count { it.vendorId == Pdk2.VID && it.productId in setOf(Pdk2.PID, 0x01cc, 0x01cb) }
        return "Mandos reconocidos por Android: " + inputs.size +
            "\nCon identificador PSP: " + psp +
            "\nEsta detección no verifica la identidad de una carpeta de guardados."
    }

    fun readStatus(deviceName: String): String {
        val all = candidates()
        require(all.size == 1) { "Conecta una sola PSP para comprobar el protocolo." }
        val device = all.single()
        require(device.deviceName == deviceName && manager.hasPermission(device)) {
            "La conexión o el permiso USB han cambiado."
        }
        val connection = manager.openDevice(device) ?: error("Android no permite abrir el dispositivo USB.")
        try {
            val revision = Pdk2.usbRevision(connection.rawDescriptors)
            require(Pdk2.mayReadStatus(device.vendorId, device.productId, revision, all.size)) {
                "bcdDevice observado " + "%04X".format(revision) +
                    ". Se admite 0100 o 0302 como descriptor USB; la identidad del firmware se comprueba en NAT3. No se enviará ninguna orden."
            }
            val hid = (0 until device.interfaceCount).map { device.getInterface(it) }.filter {
                it.interfaceClass == UsbConstants.USB_CLASS_HID && it.interfaceSubclass == 0 &&
                    (0 until it.endpointCount).any { index ->
                        val endpoint = it.getEndpoint(index)
                        endpoint.direction == UsbConstants.USB_DIR_IN &&
                            endpoint.type == UsbConstants.USB_ENDPOINT_XFER_INT &&
                            endpoint.maxPacketSize == 8
                    }
            }.singleOrNull() ?: error("No se observa una única interfaz HID esperada.")
            val hasStorage = (0 until device.interfaceCount).any {
                device.getInterface(it).interfaceClass == UsbConstants.USB_CLASS_MASS_STORAGE
            }
            require(hasStorage) { "La PSP no presenta almacenamiento y mando en el mismo dispositivo." }
            val payload = ByteArray(Pdk2.PAYLOAD_SIZE)
            // Deliberately no claimInterface, detach, setConfiguration or SET_REPORT.
            // Class GET_REPORT may fail while Android owns HID. We preserve that driver.
            val count = connection.controlTransfer(0xa1, 1, Pdk2.FEATURE_VALUE, hid.id, payload, payload.size, 750)
            if (count < 0) return "Android no ha permitido leer el estado PDK2 (resultado " + count +
                "). Puede existir un conflicto con el controlador HID, un error de permiso o de transporte. " +
                "No se ha reclamado la interfaz ni desconectado el mando. El diagnóstico no identifica errno desde Java."
            require(count == payload.size) { "Respuesta PDK2/v3 incompleta: " + count + " de 64 bytes. Las revisiones 0300 y 0301 están retiradas." }
            require(manager.deviceList[deviceName]?.deviceId == device.deviceId) {
                "La PSP se desconectó durante la consulta."
            }
            val status = Pdk2.decode(payload)
            val progress = when (status.unit) {
                0 -> "sin medida"
                1 -> status.completed.toString() + " / " + status.total + " bytes"
                2 -> status.completed.toString() + " / " + status.total + " archivos"
                else -> "unidad desconocida en estado de error"
            }
            return "PDK2/v3 responde · firmware " + "%04X".format(status.firmwareRevision) +
                " · build " + "%08X".format(status.buildId) +
                "\nbcdDevice USB: " + "%04X".format(revision) + " · capacidades: " + "%04X".format(status.capabilities) +
                "\nFase: " + status.phaseName +
                "\nSecuencia: " + status.sequence + " · resultado: " + status.result +
                "\nEnlace: " + ifYes(status.connected) + " · controlador Sony: " + ifYes(status.sonyStorageStarted) +
                "\nOrden pendiente: " + ifYes(status.pending) +
                "\nHost confirmado por la PSP: " + ifYes(status.hostConfirmed) +
                "\nEntrada habilitada por firmware: " + ifYes(status.inputEnabled) +
                "\nProgreso: " + progress +
                "\nEste diagnóstico no envía HOST_BEGIN ni habilita el mando PSP. Usa el mando de la televisión." +
                "\nNo se han cambiado fases ni escrito archivos. Este estado no demuestra montaje, identidad del volumen o expulsión segura."
        } finally {
            connection.close()
        }
    }

    private fun ifYes(value: Boolean) = if (value) "sí" else "no"
}
