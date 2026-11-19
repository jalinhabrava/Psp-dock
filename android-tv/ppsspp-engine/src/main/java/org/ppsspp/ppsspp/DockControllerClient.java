package org.ppsspp.ppsspp;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Message;
import android.os.Messenger;
import android.os.RemoteException;
import android.os.SystemClock;
import android.util.Log;
import android.view.MotionEvent;

import app.pspdock.session.PspControllerState;

/** Receives the already-started app's USB controller; never claims or starts USB. */
final class DockControllerClient {
    private static final String TAG = "PSPDockController";
    private static final int REGISTER = 1, UNREGISTER = 2, INPUT = 10, STATUS = 11;
    private static final int[] AXES = { MotionEvent.AXIS_X, MotionEvent.AXIS_Y,
            MotionEvent.AXIS_HAT_X, MotionEvent.AXIS_HAT_Y };
    private final Context context;
    private final Runnable requestExit;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final PspControllerState state = new PspControllerState(new PspControllerState.Sink() {
        public void keyDown(int key) { NativeApp.keyDown(NativeApp.DEVICE_ID_PAD_0, key, false); }
        public void keyUp(int key) { NativeApp.keyUp(NativeApp.DEVICE_ID_PAD_0, key); }
        public void axes(float x, float y, float hatX, float hatY) {
            NativeApp.joystickAxis(NativeApp.DEVICE_ID_PAD_0, AXES,
                    new float[] { x, y, hatX, hatY }, AXES.length);
        }
    });
    private Messenger service;
    private Messenger callback;
    private boolean started, bound, exitRequested;
    private int generation;

    DockControllerClient(Context context, Runnable requestExit) {
        this.context = context.getApplicationContext();
        this.requestExit = requestExit;
    }

    private final Runnable watchdog = new Runnable() {
        public void run() {
            if (!started) return;
            state.expire(SystemClock.elapsedRealtime());
            handler.postDelayed(this, 50);
        }
    };

    private final ServiceConnection connection = new ServiceConnection() {
        public void onServiceConnected(ComponentName name, IBinder binder) {
            if (!started) return;
            service = new Messenger(binder);
            if (!send(REGISTER)) disconnected();
        }
        public void onServiceDisconnected(ComponentName name) { disconnected(); }
        public void onBindingDied(ComponentName name) { disconnected(); }
        public void onNullBinding(ComponentName name) { disconnected(); }
    };

    void start() {
        if (started) return;
        started = true;
        exitRequested = false;
        state.reset();
        final int current = ++generation;
        callback = new Messenger(new Handler(Looper.getMainLooper(), message -> {
            if (started && current == generation && !exitRequested) receive(message);
            return true;
        }));
        Intent intent = new Intent().setComponent(new ComponentName(context.getPackageName(),
                "app.pspdock.tv.UsbControllerService"));
        try {
            // Flags=0: entering a game must never start a service, prompt for USB,
            // or detach a kernel driver. The Dock starts it from an explicit action.
            bound = context.bindService(intent, connection, 0);
        } catch (SecurityException | IllegalArgumentException failure) {
            Log.w(TAG, "Controller service unavailable", failure);
        }
        if (bound) handler.postDelayed(watchdog, 50);
    }

    void stop() {
        if (!started && !bound) return;
        started = false;
        generation++;
        handler.removeCallbacks(watchdog);
        send(UNREGISTER);
        service = null;
        callback = null;
        state.reset();
        if (bound) {
            try { context.unbindService(connection); }
            catch (IllegalArgumentException failure) { Log.w(TAG, "Controller already unbound", failure); }
            bound = false;
        }
    }

    private boolean send(int kind) {
        if (service == null || callback == null) return false;
        Message message = Message.obtain(null, kind);
        message.replyTo = callback;
        message.getData().putInt("protocol", PspControllerState.PROTOCOL);
        try { service.send(message); return true; }
        catch (RemoteException failure) { return false; }
    }

    private void disconnected() {
        service = null;
        state.neutralize();
    }

    private void receive(Message message) {
        Bundle data = message.getData();
        long now = SystemClock.elapsedRealtime();
        if (message.what == INPUT) {
            state.accept(data.getInt("protocol", -1), data.getLong("sequence", -1),
                    data.getLong("elapsedRealtime", -1), now, data.getBoolean("active", false),
                    data.getInt("buttons", -1), data.getInt("hat", -1),
                    data.getInt("x", -1), data.getInt("y", -1));
        } else if (message.what == STATUS) {
            if (!data.getBoolean("active", false)) state.neutralize();
            long sampledAt = data.getLong("elapsedRealtime", -1);
            if (data.getInt("protocol", -1) == PspControllerState.PROTOCOL &&
                    data.getBoolean("exitRequested", false) && sampledAt >= 0 &&
                    sampledAt <= now && now - sampledAt <= PspControllerState.STALE_MILLIS) {
                exitRequested = true;
                state.neutralize();
                requestExit.run();
            }
        }
    }
}
