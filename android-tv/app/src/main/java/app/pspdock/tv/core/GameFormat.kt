package app.pspdock.tv.core

/** File signatures provide format validation, not ownership or game compatibility. */
object GameFormat {
    val extensions = setOf("iso", "cso", "chd", "pbp")
    private val safeId = Regex("[a-f0-9]{64}[.](iso|cso|chd|pbp)")

    fun extension(name: String): String {
        val result = name.substringAfterLast('.', "").lowercase(java.util.Locale.ROOT)
        require(result in extensions) { "Elige un archivo ISO, CSO, CHD o PBP." }
        return result
    }

    fun validates(extension: String, head: ByteArray): Boolean {
        fun has(offset: Int, signature: ByteArray) =
            head.size >= offset + signature.size &&
                head.copyOfRange(offset, offset + signature.size).contentEquals(signature)
        return when (extension) {
            "iso" -> has(32769, "CD001".toByteArray(Charsets.US_ASCII))
            "cso" -> has(0, "CISO".toByteArray(Charsets.US_ASCII))
            "chd" -> has(0, "MComprHD".toByteArray(Charsets.US_ASCII))
            "pbp" -> has(0, byteArrayOf(0, 80, 66, 80))
            else -> false
        }
    }

    fun validId(id: String): Boolean = safeId.matches(id)

    fun title(name: String): String = name.substringAfterLast('/').substringAfterLast('\\')
        .filter { !it.isISOControl() }.trim().take(180).ifEmpty { "Juego PSP" }
}
