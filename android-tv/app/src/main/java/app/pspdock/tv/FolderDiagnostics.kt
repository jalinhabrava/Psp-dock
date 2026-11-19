package app.pspdock.tv

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract

object FolderDiagnostics {
    fun inspect(context: Context, tree: Uri): String {
        require(tree.scheme == "content" && DocumentsContract.isTreeUri(tree)) {
            context.getString(R.string.folder_invalid)
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
        } ?: error(context.getString(R.string.folder_unreadable))
        return context.getString(R.string.folder_details, if (names.isEmpty())
            context.getString(R.string.folder_no_structure) else names.joinToString(", "))
    }
}
