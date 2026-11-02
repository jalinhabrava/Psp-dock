package org.ppsspp.ppsspp;

import android.app.Activity;
import android.util.AtomicFile;
import android.util.Log;
import android.widget.Toast;

import app.pspdock.session.EngineSessionBridge;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.lang.ref.WeakReference;
import java.nio.charset.StandardCharsets;

/** Small boundary between the unmodified native engine and the Dock session ledger. */
final class DockEngineHost {
    private static final String TAG = "PSPDockEngine";
    static final String SESSION_TOKEN = "com.pspdock.SESSION_TOKEN";
    private static File dataDirectory;
    private static WeakReference<Activity> owner = new WeakReference<>(null);

    private DockEngineHost() {}

    static boolean begin(Activity activity) {
        Activity previous = owner.get();
        if (previous != null && previous != activity && !previous.isDestroyed()) return false;
        String token = activity.getIntent().getStringExtra(SESSION_TOKEN);
        if (!EngineSessionBridge.begin(new File(activity.getFilesDir(), "emulation"), token)) {
            Log.e(TAG, "Rejected unprepared or overlapping emulator session");
            Toast.makeText(activity, "La sesión del emulador no está disponible.", Toast.LENGTH_LONG).show();
            return false;
        }
        try {
            dataDirectory = new File(activity.getFilesDir(), "ppsspp").getCanonicalFile();
            File memstick = new File(dataDirectory, "memstick");
            if (!memstick.isDirectory() && !memstick.mkdirs()) {
                throw new IOException("Cannot create private Memory Stick");
            }
            // PPSSPP reads this before loading ppsspp.ini, including scoped-storage mode.
            AtomicFile pointer = new AtomicFile(new File(dataDirectory, "memstick_dir.txt"));
            FileOutputStream stream = null;
            try {
                stream = pointer.startWrite();
                stream.write(memstick.getCanonicalPath().getBytes(StandardCharsets.UTF_8));
                pointer.finishWrite(stream);
            } catch (IOException failure) {
                if (stream != null) pointer.failWrite(stream);
                throw failure;
            }
            owner = new WeakReference<>(activity);
            return true;
        } catch (IOException failure) {
            Log.e(TAG, "Cannot initialize private engine storage", failure);
            // A failed launch is never acknowledged as clean. Release its lease by
            // process death; the Dock process can present explicit recovery.
            System.exit(1);
            return false;
        }
    }

    static File dataDirectory() {
        if (dataDirectory == null) throw new IllegalStateException("Engine session not accepted");
        return dataDirectory;
    }

    static File memstickDirectory() {
        return new File(dataDirectory(), "memstick");
    }

    static boolean readOnlyExternalStorage() {
        return true;
    }

    static void activityDestroyed(Activity activity) {
        if (owner.get() == activity) owner.clear();
    }

    static void completedShutdown() {
        boolean recorded = EngineSessionBridge.completeShutdown();
        if (!recorded) Log.e(TAG, "Native shutdown completed but session marker could not be committed");
        // The lease stays held until this private process dies. A second game cannot
        // overlap native teardown, and the next launch gets a fresh native runtime.
        System.exit(recorded ? 0 : 1);
    }
}
