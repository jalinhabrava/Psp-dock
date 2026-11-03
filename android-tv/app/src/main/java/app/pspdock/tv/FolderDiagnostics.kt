package app.pspdock.tv

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract

object FolderDiagnostics {
    fun inspect(context: Context, tree: Uri): String {
        require(tree.scheme == "content" && DocumentsContract.isTreeUri(tree)) {
            "El selector no devolvió una carpeta compatible."
        }
        val id = DocumentsContract.getTreeDocumentId(tree)
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, id)
        val names = mutableListOf<String>()
        context.contentResolver.query(children,
            arrayOf(DocumentsContract.Document.COLUMN_DISPLAY_NAME, DocumentsContract.Document.COLUMN_MIME_TYPE),
            null, null, null)?.use { cursor ->
            var inspected = 0
            while (inspected < 128 && cursor.moveToNext()) {
                inspected++
                val name = cursor.getString(0) ?: continue
                if (cursor.getString(1) == DocumentsContract.Document.MIME_TYPE_DIR &&
                    name.uppercase(java.util.Locale.ROOT) in setOf("PSP", "GAME", "SAVEDATA", "ISO")) names += name
            }
        } ?: error("Android no permite leer esta carpeta.")
        return "Lectura de carpeta disponible.\nEstructura reconocida: " +
            if (names.isEmpty()) "sin carpetas PSP conocidas en este nivel.\n" +
                "La selección no demuestra pertenencia a la consola." else
                names.joinToString(", ") + ".\nLa selección no demuestra pertenencia a la consola."
    }
}
