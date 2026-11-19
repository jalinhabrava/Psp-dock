package app.pspdock.tv.core

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Exact PDK2/v3 wire contract from Windows main ff30333. No HIDAPI report-ID prefix. */
object Pdk2 {
    const val VID = 0x054c
    const val PID = 0x01cd
    const val PROTOCOL_VERSION = 3
    const val FIRMWARE_REVISION = 0x0308
    const val CAPABILITIES = 0x000f
    const val BUILD_ID = 0x03080001L
    const val FEATURE_VALUE = 0x0300 // Feature type 3, report ID 0; NOT a firmware revision.
    const val OUTPUT_VALUE = 0x0200 // Output type 2, report ID 0.
    const val PAYLOAD_SIZE = 64
    const val STATUS = 1
    const val SET_PHASE = 2
    const val HOST_BEGIN = 3
    const val HOST_PROGRESS = 4
    const val HOST_SHUTDOWN = 5
    const val STORAGE = 1
    const val CONTROLLER = 2
    const val COPYING = 3
    const val EXIT_SYNC = 4
    const val SAFE = 5
    const val ERROR = 255
    val firmwareBuilds = (0x0303..0x0308).associateWith { (it.toLong() shl 16) or 1L }
    val firmwareCapabilities = (0x0303..0x0308).associateWith { if (it < 0x0305) 7 else 15 }
    val usbRevisions = firmwareBuilds.keys + 0x0100
    private val fragmentRevisions = setOf(0x0306, 0x0307, 0x0308)
    private val phaseActivities = mapOf(STORAGE to setOf(1, 2, 3), CONTROLLER to setOf(5),
        COPYING to setOf(4), EXIT_SYNC to setOf(6), SAFE to setOf(7))

    data class Status(
        val phase: Int, val flags: Int, val session: String, val sequence: Long, val result: Int,
        val firmwareRevision: Int, val capabilities: Int, val buildId: Long,
        val activity: Int, val unit: Int, val completed: ULong, val total: ULong,
        val transportWord: Int = 0
    ) {
        val connected get() = flags and 1 != 0
        val pending get() = flags and 2 != 0
        val sonyStorageStarted get() = flags and 4 != 0
        val exitRequested get() = flags and 8 != 0
        val hostConfirmed get() = flags and 16 != 0
        val shutdownRequested get() = flags and 32 != 0
        val uiSoundsReady get() = flags and 64 != 0
        val inputEnabled get() = connected && sonyStorageStarted && hostConfirmed && !pending &&
            !shutdownRequested && !exitRequested && (phase == CONTROLLER || (phase == STORAGE && activity == 2))
        val identity get() = listOf(firmwareRevision.toLong(), capabilities.toLong(), buildId)
    }

    private fun identity(firmware: Int, capabilities: Int, build: Long) {
        require(firmwareCapabilities[firmware] == capabilities && firmwareBuilds[firmware] == build) {
            "Unverified firmware/build/capabilities; current firmware is 0308 / 03080001."
        }
    }

    private fun progress(phase: Int, activity: Int, unit: Int, completed: ULong, total: ULong) {
        require(activity in phaseActivities[phase].orEmpty()) { "Activity does not match phase." }
        require(if (unit == 0) completed == 0uL && total == 0uL
            else unit in 1..2 && total > 0uL && completed <= total) { "Incoherent progress counters." }
    }

    fun decode(payload: ByteArray): Status {
        require(payload.size == PAYLOAD_SIZE) { "Expected a 64-byte PDK2 Feature payload." }
        require(payload.copyOfRange(0, 4).contentEquals(byteArrayOf(80, 68, 75, 50)) &&
            payload[4].toInt() == PROTOCOL_VERSION && payload[5].toInt() == STATUS) {
            "Unknown PDK2 header/version/operation."
        }
        val phase = payload[6].toInt() and 255
        val flags = payload[7].toInt() and 255
        require(phase in setOf(0, STORAGE, CONTROLLER, COPYING, EXIT_SYNC, SAFE, ERROR)) { "Unknown phase." }
        val session = payload.copyOfRange(8, 24)
        require(session.any { it.toInt() != 0 }) { "Empty PDK2 session." }
        val buffer = ByteBuffer.wrap(payload).order(ByteOrder.LITTLE_ENDIAN)
        val sequence = buffer.getInt(24).toLong() and 0xffffffffL
        val result = buffer.getInt(28)
        val pending = flags and 2 != 0
        require(!pending || (sequence > 0 && result == 1)) { "Incoherent pending ACK." }
        require(pending || sequence != 0L || result == 0) { "Incoherent initial ACK." }
        require(pending || sequence == 0L || result < 0 || result == 2) { "Incoherent ACK result." }
        val firmware = buffer.getShort(36).toInt() and 65535
        val capabilities = buffer.getShort(38).toInt() and 65535
        val build = buffer.getInt(40).toLong() and 0xffffffffL
        val transport = buffer.getShort(46).toInt() and 65535
        require(payload.copyOfRange(32, 36).contentEquals(byteArrayOf(78, 65, 84, 51)) &&
            (transport == 0 || firmware in fragmentRevisions)) { "Invalid NAT3 extension." }
        require(firmware !in fragmentRevisions || transport and 15 <= 9) { "Invalid transport diagnostic." }
        identity(firmware, capabilities, build)
        require(flags and (if (firmware == 0x0308) 0x7f else 0x3f).inv() == 0) { "Unsupported status flags." }
        val status = Status(phase, flags, session.joinToString("") { "%02x".format(it.toInt() and 255) },
            sequence, result, firmware, capabilities, build, payload[44].toInt() and 255,
            payload[45].toInt() and 255, buffer.getLong(48).toULong(), buffer.getLong(56).toULong(), transport)
        require(!status.shutdownRequested || (capabilities and 8 != 0 && !pending && phase == SAFE &&
            sequence > 0 && result == 2)) { "Incoherent terminal shutdown ACK." }
        if (status.hostConfirmed && phase != ERROR) {
            progress(phase, status.activity, status.unit, status.completed, status.total)
        } else if (!status.hostConfirmed) {
            require(status.activity == 0 && status.unit == 0 && status.completed == 0uL && status.total == 0uL) {
                "Unclaimed firmware cannot publish work progress."
            }
        }
        return status
    }

    fun encodeCommand(session: String, sequence: Long, phase: Int, operation: Int,
                      firmware: Int = FIRMWARE_REVISION, build: Long = BUILD_ID,
                      activity: Int = 0, unit: Int = 0, completed: ULong = 0uL, total: ULong = 0uL): ByteArray {
        val capabilities = requireNotNull(firmwareCapabilities[firmware]) { "Unsupported firmware." }
        identity(firmware, capabilities, build)
        require(session.matches(Regex("[0-9a-fA-F]{32}"))) { "Invalid session nonce." }
        val nonce = session.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
        require(nonce.any { it.toInt() != 0 }) { "Empty session nonce." }
        require(sequence in 1L..0xffffffffL && phase in 1..5 && operation in 2..5) { "Invalid command fields." }
        require(operation != HOST_BEGIN || phase == STORAGE) { "HOST_BEGIN requires STORAGE." }
        require(operation != HOST_SHUTDOWN || (phase == SAFE && capabilities and 8 != 0)) { "Unsupported shutdown." }
        if (operation == HOST_PROGRESS) progress(phase, activity, unit, completed, total)
        else require(activity == 0 && unit == 0 && completed == 0uL && total == 0uL) { "Unexpected progress fields." }
        return ByteBuffer.allocate(PAYLOAD_SIZE).order(ByteOrder.LITTLE_ENDIAN).apply {
            put(byteArrayOf(80, 68, 75, 50, PROTOCOL_VERSION.toByte(), operation.toByte(), phase.toByte(), 0))
            put(nonce).putInt(sequence.toInt()).putInt(0).put(byteArrayOf(78, 65, 84, 51))
            putShort(firmware.toShort()).putShort(capabilities.toShort()).putInt(build.toInt())
            put(activity.toByte()).put(unit.toByte()).putShort(0).putLong(completed.toLong()).putLong(total.toLong())
        }.array()
    }

    /** 0306+ control channel: 32 Output reports and one CRC16 commit, all four bytes on Android. */
    fun outputReports(command: ByteArray, token: Int): List<ByteArray> {
        require(command.size == PAYLOAD_SIZE && token in 0..255) { "Invalid fragment batch." }
        val firmware = (command[36].toInt() and 255) or ((command[37].toInt() and 255) shl 8)
        require(firmware in fragmentRevisions) { "This firmware does not use fragmented Output reports." }
        val buffer = ByteBuffer.wrap(command).order(ByteOrder.LITTLE_ENDIAN)
        val nonce = command.copyOfRange(8, 24).joinToString("") { "%02x".format(it.toInt() and 255) }
        val canonical = encodeCommand(nonce, buffer.getInt(24).toLong() and 0xffffffffL,
            command[6].toInt() and 255, command[5].toInt() and 255, firmware,
            buffer.getInt(40).toLong() and 0xffffffffL, command[44].toInt() and 255,
            command[45].toInt() and 255, buffer.getLong(48).toULong(), buffer.getLong(56).toULong())
        require(command.contentEquals(canonical)) { "Noncanonical command cannot reach USB." }
        val reports = (0 until 32).map { index ->
            byteArrayOf((0xc0 or index).toByte(), token.toByte(), command[index * 2], command[index * 2 + 1])
        }.toMutableList()
        var crc = 0xffff
        for (value in command) {
            crc = crc xor ((value.toInt() and 255) shl 8)
            repeat(8) { crc = if (crc and 0x8000 != 0) ((crc shl 1) xor 0x1021) and 0xffff else (crc shl 1) and 0xffff }
        }
        reports += byteArrayOf(0xe0.toByte(), token.toByte(), crc.toByte(), (crc shr 8).toByte())
        return reports
    }

    /** Sony bcdDevice=0100 is a USB filter, never the firmware identity. */
    fun usbRevision(deviceDescriptor: ByteArray): Int {
        require(deviceDescriptor.size >= 18 && deviceDescriptor[0].toInt() >= 18 &&
            deviceDescriptor[1].toInt() == 1) { "Incomplete USB device descriptor." }
        return (deviceDescriptor[12].toInt() and 255) or ((deviceDescriptor[13].toInt() and 255) shl 8)
    }

    fun mayReadStatus(vendor: Int, product: Int, usbRevision: Int, candidates: Int): Boolean =
        vendor == VID && product == PID && usbRevision in usbRevisions && candidates == 1
}
