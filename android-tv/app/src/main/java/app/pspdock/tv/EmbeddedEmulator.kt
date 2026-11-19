package app.pspdock.tv

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import app.pspdock.session.SessionLedger
import java.io.File

/** Explicit component in this APK. No package discovery, market intent or external application. */
object EmbeddedEmulator {
    const val ACTIVITY = "org.ppsspp.ppsspp.PpssppActivity"
    const val VERSION = "1.20.4"

    fun launch(activity: Activity, token: String, game: File? = null) {
        if (game != null) require(game.isFile) { "El juego local no está disponible." }
        val intent = Intent().setComponent(ComponentName(activity.packageName, ACTIVITY))
        val args = "--pause-menu-exit" +
            if (game == null) "" else " \"" + game.canonicalPath.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
        intent.putExtra("org.ppsspp.ppsspp.Args", args)
        intent.putExtra(SessionLedger.TOKEN_EXTRA, token)
        // In PPSSPP 1.20.4 a data URI takes precedence over Args. The local path belongs
        // to this same UID, so pass it in Args without a URI or temporary permission grant.
        activity.startActivity(intent)
    }

    fun resume(activity: Activity, token: String) {
        activity.startActivity(Intent().setComponent(ComponentName(activity.packageName, ACTIVITY))
            .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
            .putExtra(SessionLedger.TOKEN_EXTRA, token))
    }
}
