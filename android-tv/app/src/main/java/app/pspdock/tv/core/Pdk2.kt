package app.pspdock.tv.core

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** PDK2/v3 payload, not the Windows HIDAPI report-id-prefixed 65-byte buffer. */
object Pdk2 {
    const val VID = 0x054c
    const val PID = 0x01cd
    const val PROTOCOL_VERSION = 3
    const val FIRMWARE_REVISION = 0x0302
    const val CAPABILITIES = 0x0007
    const val BUILD_ID = 0x03020001L
    const val FEATURE_VALUE = 0x0300 // Feature report type (3), report id 0; NOT firmware revision.
    const val PAYLOAD_SIZE = 64
    private val usbRevisions = setOf(0x0100, 0x0302)
    private val phases = setOf(0, 1, 2, 3, 4, 5, 255)

    data class Status(
        val phase: Int,
        val flags: Int,
        val session: String,
        val sequence: Long,
        val result: Int,
        val firmwareRevision: Int,
        val capabilities: Int,
        val buildId: Long,
        val activity: Int,
        val unit: Int,
        val completed: ULong,
        val total: ULong
    ) {
        val connected get() = flags and 1 != 0
        val pending get() = flags and 2 != 0
        val sonyStorageStarted get() = flags and 4 != 0
        val exitRequested get() = flags and 8 != 0
        val hostConfirmed get() = flags and 16 != 0
        val inputEnabled get() = hostConfirmed && !pending &&
            (phase == 2 || (phase == 1 && activity == 2))
        val phaseName get() = when (phase) {
            0 -> "Preparando"
            1 -> "Sincronización"
            2 -> "Mando"
            3 -> "Copia"
            4 -> "Salida"
            5 -> "Trabajo del cliente terminado"
            else -> "Error"
        }
    }

    fun decode(payload: ByteArray): Status {
        require(payload.size == PAYLOAD_SIZE) { "Informe PDK2 incompleto." }
        require(payload.copyOfRange(0, 4).contentEquals(byteArrayOf(80, 68, 75, 50))) {
            "La respuesta no pertenece a PDK2."
        }
        require(payload[4].toInt() == PROTOCOL_VERSION && payload[5].toInt() == 1) {
            "Versión u operación de PDK2 desconocida. Se requiere v3 / firmware 0302."
        }
        val phase = payload[6].toInt() and 255
        val flags = payload[7].toInt() and 255
        require(phase in phases && flags and 0xe0 == 0) { "Fase o indicadores desconocidos." }
        val session = payload.copyOfRange(8, 24)
        require(session.any { it.toInt() != 0 }) { "Sesión PDK2 vacía." }
        val buffer = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        val sequence = buffer.getInt(24).toLong() and 0xffffffffL
        val result = buffer.getInt(28)
        val pending = flags and 2 != 0
        require(!pending || (sequence > 0 && result == 1)) { "Orden pendiente incoherente." }
        require(pending || sequence != 0L || result == 0) { "Estado inicial incoherente." }
        require(pending || sequence == 0L || result < 0 || result == 2) {
            "Confirmación de fase incoherente."
        }
        require(payload.copyOfRange(32, 36).contentEquals(byteArrayOf(78, 65, 84, 51)) &&
            buffer.getShort(46).toInt() == 0) { "Extensión de identidad NAT3 inválida." }
        val firmware = buffer.getShort(36).toInt() and 65535
        val capabilities = buffer.getShort(38).toInt() and 65535
        val build = buffer.getInt(40).toLong() and 0xffffffffL
        require(firmware == FIRMWARE_REVISION && capabilities == CAPABILITIES && build == BUILD_ID) {
            "Firmware, capacidades o compilación no admitidos. Se requiere 0302 / 03020001."
        }
        val activity = payload[44].toInt() and 255
        val unit = payload[45].toInt() and 255
        val completed = buffer.getLong(48).toULong()
        val total = buffer.getLong(56).toULong()
        val confirmed = flags and 16 != 0
        if (confirmed && phase != 255) {
            val activities = when (phase) {
                1 -> setOf(1, 2, 3)
                2 -> setOf(5)
                3 -> setOf(4)
                4 -> setOf(6)
                5 -> setOf(7)
                else -> emptySet()
            }
            require(activity in activities) { "Actividad incompatible con la fase." }
            require(if (unit == 0) completed == 0uL && total == 0uL
                else unit in setOf(1, 2) && total > 0uL && completed <= total) { "Progreso PDK2 incoherente." }
        } else if (!confirmed) {
            require(activity == 0 && unit == 0 && completed == 0uL && total == 0uL) {
                "La PSP publica progreso sin confirmar al host."
            }
        }
        return Status(phase, flags, session.joinToString("") { "%02x".format(it.toInt() and 255) }, sequence, result,
            firmware, capabilities, build, activity, unit, completed, total)
    }

    /** Sony's composite descriptor may report 0100. It does not identify firmware. */
    fun usbRevision(deviceDescriptor: ByteArray): Int {
        require(deviceDescriptor.size >= 18 && deviceDescriptor[0].toInt() >= 18 &&
            deviceDescriptor[1].toInt() == 1) { "Descriptor de dispositivo incompleto." }
        return (deviceDescriptor[12].toInt() and 255) or
            ((deviceDescriptor[13].toInt() and 255) shl 8)
    }

    fun mayReadStatus(vendor: Int, product: Int, usbRevision: Int, candidates: Int): Boolean =
        vendor == VID && product == PID && usbRevision in usbRevisions && candidates == 1
}
