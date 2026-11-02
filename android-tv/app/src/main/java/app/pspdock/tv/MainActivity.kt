package app.pspdock.tv

import android.app.Activity
import android.app.AlertDialog
import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.hardware.input.InputManager
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.format.Formatter
import android.view.Gravity
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import app.pspdock.session.SessionLedger
import app.pspdock.tv.core.VerifiedImport
import java.io.InputStream
import java.io.InterruptedIOException
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Native TV shell for the first Android milestone.
 * Import and launch are real; device/session write-back deliberately remains unavailable.
 */
class MainActivity : Activity(), InputManager.InputDeviceListener {
    private enum class Tab { LIBRARY, CONNECTION, SETTINGS }
    private val io = Executors.newSingleThreadExecutor()
    private val cancelled = AtomicBoolean(false)
    private val activeSource = AtomicReference<InputStream?>(null)
    private lateinit var store: GameStore
    private lateinit var usb: UsbDiagnostics
    private lateinit var inputManager: InputManager
    private lateinit var page: LinearLayout
    private lateinit var status: TextView
    private lateinit var controller: TextView
    private var tab = Tab.LIBRARY
    private var busy = false
    private var importing = false
    private var statusMessage = ""
    private var usbMessage = ""
    private var folderMessage = ""
    private lateinit var ledger: SessionLedger
    private var session = SessionLedger.Snapshot(SessionLedger.State.IDLE, null, false)
    private var sessionError = ""
    private val sessionHandler = Handler(Looper.getMainLooper())
    private var resumed = false
    private val sessionPoll = object : Runnable {
        override fun run() {
            if (!resumed) return
            refreshSession()
            if (session.state in setOf(SessionLedger.State.STARTING, SessionLedger.State.RUNNING,
                SessionLedger.State.STOPPING)) sessionHandler.postDelayed(this, 350)
        }
    }
    private var requestedUsbName: String? = null
    private var lastAxis = 0
    private var lastAxisAt = 0L
    private val preferences by lazy { getSharedPreferences("dock", MODE_PRIVATE) }
    private val usbAction get() = packageName + ".USB_PERMISSION"
    private val ink = Color.rgb(237, 248, 255)
    private val muted = Color.rgb(166, 192, 209)
    private val accent = Color.rgb(120, 232, 207)
    private val panel = Color.rgb(16, 39, 60)

    private val usbReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                usbAction -> {
                    val device = deviceExtra(intent)
                    val expected = requestedUsbName
                    requestedUsbName = null
                    if (device == null || expected == null || device.deviceName != expected) return
                    if (intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)) {
                        probe(device.deviceName)
                    } else {
                        usbMessage = getString(R.string.usb_denied)
                        if (tab == Tab.CONNECTION) render()
                    }
                }
                UsbManager.ACTION_USB_DEVICE_DETACHED -> {
                    requestedUsbName = null
                    usbMessage = getString(R.string.usb_gone)
                    if (tab == Tab.CONNECTION && !busy) render()
                }
                UsbManager.ACTION_USB_DEVICE_ATTACHED -> if (tab == Tab.CONNECTION && !busy) render()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = GameStore(applicationContext)
        usb = UsbDiagnostics(applicationContext)
        inputManager = getSystemService(InputManager::class.java)
        ledger = SessionLedger(File(filesDir, "emulation"))
        statusMessage = if (store.removeInterruptedImports() > 0) getString(R.string.import_restart)
            else getString(R.string.ready)
        buildShell()
        registerUsbReceiver()
        inputManager.registerInputDeviceListener(this, null)
        render()
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        sessionHandler.removeCallbacks(sessionPoll)
        sessionPoll.run()
        if (Build.VERSION.SDK_INT >= 30) {
            window.insetsController?.hide(WindowInsets.Type.statusBars() or WindowInsets.Type.navigationBars())
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        }
    }

    override fun onPause() {
        resumed = false
        sessionHandler.removeCallbacks(sessionPoll)
        super.onPause()
    }

    private fun refreshSession() {
        val previous = session
        sessionError = ""
        session = try { ledger.observe(preferences.getString("engine_token", null)) }
        catch (error: Exception) {
            sessionError = error.message ?: "No se pudo leer el registro de sesión."
            SessionLedger.Snapshot(SessionLedger.State.UNKNOWN, null, true)
        }
        if (session != previous && ::page.isInitialized) {
            if (!busy) statusMessage = sessionText()
            render()
        }
    }

    private fun sessionText(): String = when (session.state) {
        SessionLedger.State.IDLE -> getString(R.string.ready)
        SessionLedger.State.STARTING -> "Esperando el arranque del motor integrado."
        SessionLedger.State.RUNNING -> "El motor sigue activo. Puedes volver al juego para continuar o cerrarlo."
        SessionLedger.State.STOPPING -> "El motor ha terminado; esperando que cierre su proceso."
        SessionLedger.State.CLOSED -> "Juego cerrado. Los guardados permanecen en este dispositivo."
        SessionLedger.State.INTERRUPTED -> "El motor se interrumpió sin confirmar el cierre. No se devolverán guardados a la PSP."
        SessionLedger.State.FAILED -> "El motor no llegó a iniciar. Puedes volver a intentarlo."
        SessionLedger.State.RECOVERED -> "Sesión anterior descartada como interrumpida. Los archivos locales se conservan."
        SessionLedger.State.UNKNOWN -> "No se ha podido verificar la sesión anterior. " + sessionError
    }

    private fun buildShell() {
        val shell = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(42), dp(22), dp(42), dp(20))
            background = GradientDrawable(GradientDrawable.Orientation.TL_BR,
                intArrayOf(Color.rgb(7, 21, 35), Color.rgb(16, 43, 61), Color.rgb(7, 21, 35)))
        }
        val heading = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        heading.addView(label(getString(R.string.brand), 25, accent, bold = true),
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        heading.addView(label(getString(R.string.preview), 12, muted))
        shell.addView(heading)
        shell.addView(label(getString(R.string.tagline), 16, muted).apply { setPadding(0, dp(4), 0, dp(12)) })
        val navigation = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        listOf(Tab.LIBRARY to R.string.library, Tab.CONNECTION to R.string.connection,
            Tab.SETTINGS to R.string.settings).forEach { (target, text) ->
            val button = button(getString(text)) { tab = target; render() }
            navigation.addView(button, LinearLayout.LayoutParams(0, dp(46), 1f).apply {
                marginEnd = dp(10)
            })
        }
        shell.addView(navigation)
        val scroll = ScrollView(this).apply { isFillViewport = true; clipToPadding = false }
        page = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(16), 0, dp(12))
        }
        scroll.addView(page)
        shell.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        controller = label(getString(R.string.controller_waiting), 12, muted)
        shell.addView(controller)
        setContentView(shell)
        navigation.getChildAt(0).requestFocus()
    }

    private fun render() {
        if (isFinishing || isDestroyed) return
        page.removeAllViews()
        when (tab) {
            Tab.LIBRARY -> renderLibrary()
            Tab.CONNECTION -> renderConnection()
            Tab.SETTINGS -> renderSettings()
        }
        status = label(statusMessage, 13, muted).apply { setPadding(dp(4), dp(10), dp(4), dp(8)) }
        page.addView(status)
    }

    private fun renderLibrary() {
        renderSession()
        val intro = card()
        intro.addView(label(getString(R.string.import_hint), 16, ink))
        intro.addView(button(getString(if (importing) R.string.copy_cancel else R.string.import_game)) {
            if (importing) cancelImport() else pickGame()
        }, buttonLayout())
        val games = store.list()
        if (games.isEmpty()) {
            val empty = card()
            empty.addView(label(getString(R.string.empty_library), 23, ink, true))
            empty.addView(label(getString(R.string.empty_hint), 15, muted).apply { setPadding(0, dp(8), 0, 0) })
        } else {
            page.addView(label(games.size.toString() + " juegos en este dispositivo", 18, ink, true)
                .apply { setPadding(dp(4), dp(8), 0, dp(10)) })
            games.forEach { game ->
                page.addView(button(game.title + "\n" + Formatter.formatShortFileSize(this, game.bytes) +
                    " · Jugar") { launch(game) }, buttonLayout(68))
            }
        }
        page.addView(label(getString(R.string.preview_limit), 13, muted).apply { setPadding(dp(4), dp(18), dp(4), 0) })
    }

    private fun renderSession() {
        if (session.mayLaunch) return
        val sessionCard = card()
        sessionCard.addView(label("Sesión de juego", 19, ink, true))
        sessionCard.addView(label(sessionText(), 15, muted).apply { setPadding(0, dp(8), 0, 0) })
        if (session.mayResume) sessionCard.addView(button("Volver al juego") { resumeEngine() }, buttonLayout())
        if (session.mayRecover) sessionCard.addView(button("Recuperar el Dock") { recoverSession() }, buttonLayout())
        sessionCard.addView(button("Comprobar sesión") { refreshSession(); render() }, buttonLayout())
    }

    private fun renderConnection() {
        val devices = usb.candidates()
        val connection = card()
        connection.addView(label(getString(R.string.connect_psp), 23, ink, true))
        connection.addView(label(getString(R.string.connection_hint), 15, muted).apply { setPadding(0, dp(8), 0, dp(10)) })
        if (devices.isEmpty()) {
            connection.addView(label("No hay una PSP USB identificada.", 16, ink))
        } else {
            devices.forEach { connection.addView(label(usb.describe(it), 15, ink).apply {
                setPadding(0, dp(6), 0, dp(8))
            }) }
        }
        connection.addView(button(getString(R.string.check_connection)) { render() }, buttonLayout())
        if (devices.size == 1) {
            connection.addView(button(getString(R.string.usb_probe)) { requestUsb(devices.single()) }, buttonLayout())
        } else if (devices.size > 1) {
            connection.addView(label(getString(R.string.usb_ambiguous), 15, accent))
        }
        if (usbMessage.isNotEmpty()) connection.addView(label(usbMessage, 14, accent).apply { setPadding(0, dp(10), 0, 0) })
        val inputs = card()
        inputs.addView(label("Mando y almacenamiento", 18, ink, true))
        inputs.addView(label(usb.inputSummary(), 15, muted).apply { setPadding(0, dp(8), 0, 0) })
        val folder = card()
        folder.addView(label(getString(R.string.folder_hint), 15, ink))
        folder.addView(button(getString(R.string.choose_psp_folder)) { pickFolder() }, buttonLayout())
        if (folderMessage.isNotEmpty()) folder.addView(label(folderMessage, 14, accent))
        page.addView(label(getString(R.string.safe_limit), 13, muted))
    }

    private fun renderSettings() {
        val emulator = card()
        emulator.addView(label(getString(R.string.ppsspp_title), 21, ink, true))
        emulator.addView(label("PPSSPP " + EmbeddedEmulator.VERSION, 15, muted))
        emulator.addView(label(getString(R.string.ppsspp_hint), 15, muted).apply { setPadding(0, dp(8), 0, 0) })
        emulator.addView(button(getString(R.string.open_ppsspp)) {
            launchEngine(null)
        }, buttonLayout())
        val device = card()
        device.addView(label(getString(R.string.diagnostic_title), 18, ink, true))
        device.addView(label(Build.MANUFACTURER + " " + Build.MODEL + " · Android " + Build.VERSION.RELEASE +
            "\nUSB host: " + if (packageManager.hasSystemFeature("android.hardware.usb.host")) "disponible" else "no anunciado", 15, muted))
        device.addView(label(getString(R.string.preview_limit), 14, muted).apply { setPadding(0, dp(8), 0, 0) })
        device.addView(button(getString(R.string.forget_folder)) { forgetFolder() }, buttonLayout())
        page.addView(button(getString(R.string.exit)) { confirmExit() }, buttonLayout())
    }

    private fun pickGame() {
        if (busy) return
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            putExtra(Intent.EXTRA_TITLE, getString(R.string.choose_game_title))
        }
        try { @Suppress("DEPRECATION") startActivityForResult(intent, PICK_GAME) }
        catch (_: ActivityNotFoundException) { message(getString(R.string.no_picker)) }
    }

    private fun pickFolder() {
        if (busy) return
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
        }
        try { @Suppress("DEPRECATION") startActivityForResult(intent, PICK_FOLDER) }
        catch (_: ActivityNotFoundException) { message(getString(R.string.no_picker)) }
    }

    @Deprecated("Uses the platform activity result API without an AndroidX dependency.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri = data?.data ?: return
        if (resultCode != RESULT_OK) return
        when (requestCode) {
            PICK_GAME -> importGame(uri)
            PICK_FOLDER -> inspectFolder(uri)
        }
    }

    private fun importGame(uri: Uri) {
        if (busy) return
        busy = true
        importing = true
        cancelled.set(false)
        statusMessage = getString(R.string.importing)
        render()
        io.execute {
            try {
                val (name, size) = store.sourceMetadata(uri)
                val source = contentResolver.openInputStream(uri) ?: error("No se puede abrir el archivo seleccionado.")
                activeSource.set(source)
                var lastUpdate = 0L
                val game = source.use {
                    store.import(it, name, size, cancelled::get) { bytes, stage ->
                        val now = SystemClock.elapsedRealtime()
                        if (now - lastUpdate >= 250 || stage == VerifiedImport.Stage.VERIFYING) {
                            lastUpdate = now
                            val text = if (stage == VerifiedImport.Stage.VERIFYING) getString(R.string.verifying)
                                else getString(R.string.importing) + " " + Formatter.formatShortFileSize(this, bytes) +
                                    (size?.let { total -> " / " + Formatter.formatShortFileSize(this, total) } ?: "")
                            onUi { statusMessage = text; status.text = text }
                        }
                    }
                }
                onUi {
                    busy = false
                    importing = false
                    statusMessage = "Importado y verificado: " + game.title
                    render()
                }
            } catch (error: Exception) {
                onUi {
                    busy = false
                    importing = false
                    statusMessage = if (cancelled.get() || error is InterruptedIOException) getString(R.string.import_cancelled)
                        else "Importación detenida: " + (error.message ?: error.javaClass.simpleName)
                    render()
                }
            } finally {
                activeSource.set(null)
            }
        }
    }

    private fun cancelImport() {
        if (!importing) return
        cancelled.set(true)
        runCatching { activeSource.getAndSet(null)?.close() }
        statusMessage = getString(R.string.import_cancelling)
        status.text = statusMessage
    }

    private fun inspectFolder(uri: Uri) {
        if (busy) return
        busy = true
        io.execute {
            val report = runCatching {
                val old = preferences.getString("tree", null)
                val persistent = runCatching {
                    contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }.isSuccess
                if (persistent) {
                    preferences.edit().putString("tree", uri.toString()).apply()
                    if (old != null && old != uri.toString()) runCatching {
                        contentResolver.releasePersistableUriPermission(Uri.parse(old), Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    }
                }
                FolderDiagnostics.inspect(applicationContext, uri) +
                    if (persistent) "\nPermiso de lectura recordado." else "\nPermiso disponible solo para esta sesión."
            }.getOrElse { "No se pudo comprobar la carpeta: " + (it.message ?: it.javaClass.simpleName) }
            onUi { busy = false; folderMessage = report; if (tab == Tab.CONNECTION) render() }
        }
    }

    private fun forgetFolder() {
        preferences.getString("tree", null)?.let { saved ->
            runCatching { contentResolver.releasePersistableUriPermission(Uri.parse(saved), Intent.FLAG_GRANT_READ_URI_PERMISSION) }
        }
        preferences.edit().remove("tree").apply()
        folderMessage = ""
        statusMessage = "Permiso de carpeta olvidado."
        render()
    }

    private fun requestUsb(device: UsbDevice) {
        if (busy) return
        val manager = getSystemService(UsbManager::class.java)
        if (manager.hasPermission(device)) { probe(device.deviceName); return }
        requestedUsbName = device.deviceName
        val intent = Intent(usbAction).setPackage(packageName)
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or
            if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
        val pending = PendingIntent.getBroadcast(this, 0, intent, flags)
        try { manager.requestPermission(device, pending) }
        catch (error: Exception) { requestedUsbName = null; message(error.message ?: getString(R.string.usb_denied)) }
    }

    private fun probe(name: String) {
        if (busy) return
        busy = true
        usbMessage = getString(R.string.probe_busy)
        if (tab == Tab.CONNECTION) render()
        io.execute {
            val result = runCatching { usb.readStatus(name) }
                .getOrElse { "Consulta detenida: " + (it.message ?: it.javaClass.simpleName) }
            onUi { busy = false; usbMessage = result; if (tab == Tab.CONNECTION) render() }
        }
    }

    private fun launch(game: LibraryGame) {
        launchEngine(game)
    }

    private fun launchEngine(game: LibraryGame?) {
        if (busy) return
        refreshSession()
        if (!session.mayLaunch) { message(sessionText()); return }
        var token: String? = null
        try {
            val file = game?.let { store.file(it.id) }
            token = ledger.prepare()
            check(preferences.edit().putString("engine_token", token).commit()) { "No se pudo guardar la sesión de arranque." }
            refreshSession()
            EmbeddedEmulator.launch(this, token, file)
        } catch (error: Exception) {
            token?.let { runCatching { ledger.markLaunchFailed(it) } }
            refreshSession()
            message("No se pudo abrir el juego: " + (error.message ?: error.javaClass.simpleName))
        }
    }

    private fun resumeEngine() {
        refreshSession()
        if (!session.mayResume) return
        try { EmbeddedEmulator.resume(this, checkNotNull(session.token)) }
        catch (error: Exception) { message("No se pudo volver al juego: " + error.message) }
    }

    private fun recoverSession() {
        refreshSession()
        if (!session.mayRecover || busy) return
        AlertDialog.Builder(this).setTitle("Recuperar el Dock")
            .setMessage("El motor ya no mantiene el bloqueo de sesión. Se descartará el arranque pendiente o la sesión interrumpida; los juegos y guardados locales se conservan. Esto no confirma un cierre limpio ni escribe en la PSP.")
            .setNegativeButton(R.string.stay, null)
            .setPositiveButton("Recuperar") { _, _ ->
                try {
                    val token = ledger.recover(session.token)
                    check(preferences.edit().putString("engine_token", token).commit()) { "No se pudo guardar la recuperación." }
                    refreshSession()
                } catch (error: Exception) { message(error.message ?: "No se pudo recuperar la sesión.") }
            }.show()
    }

    private fun registerUsbReceiver() {
        val filter = IntentFilter(usbAction).apply {
            addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
            addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
        }
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(usbReceiver, filter, RECEIVER_NOT_EXPORTED)
        else { @Suppress("DEPRECATION") registerReceiver(usbReceiver, filter) }
    }

    @Suppress("DEPRECATION")
    private fun deviceExtra(intent: Intent): UsbDevice? = if (Build.VERSION.SDK_INT >= 33)
        intent.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
    else intent.getParcelableExtra(UsbManager.EXTRA_DEVICE)

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (::controller.isInitialized && event.action == KeyEvent.ACTION_DOWN) {
            controller.text = if (tab == Tab.CONNECTION) "Entrada: " + KeyEvent.keyCodeToString(event.keyCode)
                else "Mando activo · Navega con la cruceta · Confirmar para elegir · Atrás para volver"
        }
        if (event.keyCode == KeyEvent.KEYCODE_BUTTON_A) {
            if (event.action == KeyEvent.ACTION_UP) currentFocus?.performClick()
            return true
        }
        if (event.keyCode == KeyEvent.KEYCODE_BUTTON_B) {
            if (event.action == KeyEvent.ACTION_UP) handleBack()
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    override fun onGenericMotionEvent(event: MotionEvent): Boolean {
        if ((event.source and InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK &&
            event.action == MotionEvent.ACTION_MOVE) {
            val hatX = event.getAxisValue(MotionEvent.AXIS_HAT_X)
            val hatY = event.getAxisValue(MotionEvent.AXIS_HAT_Y)
            val x = if (kotlin.math.abs(hatX) > 0.5f) hatX else event.getAxisValue(MotionEvent.AXIS_X)
            val y = if (kotlin.math.abs(hatY) > 0.5f) hatY else event.getAxisValue(MotionEvent.AXIS_Y)
            val direction = when {
                y < -0.55f -> View.FOCUS_UP
                y > 0.55f -> View.FOCUS_DOWN
                x < -0.55f -> View.FOCUS_LEFT
                x > 0.55f -> View.FOCUS_RIGHT
                else -> 0
            }
            val now = SystemClock.elapsedRealtime()
            if (direction != 0 && (lastAxis != direction || now - lastAxisAt > 230)) {
                currentFocus?.focusSearch(direction)?.requestFocus()
                lastAxisAt = now
            }
            lastAxis = direction
            controller.text = if (tab == Tab.CONNECTION) "Mando: analógico " + "%.2f / %.2f".format(x, y)
                else "Mando activo · Navega con la cruceta · Confirmar para elegir · Atrás para volver"
            return true
        }
        return super.onGenericMotionEvent(event)
    }

    override fun onInputDeviceAdded(deviceId: Int) { controller.text = usb.inputSummary().lineSequence().first() }
    override fun onInputDeviceRemoved(deviceId: Int) { controller.text = usb.inputSummary().lineSequence().first() }
    override fun onInputDeviceChanged(deviceId: Int) { controller.text = usb.inputSummary().lineSequence().first() }

    @Deprecated("Platform Back handling is used without an AndroidX dependency.")
    override fun onBackPressed() { handleBack() }

    private fun handleBack() {
        if (tab != Tab.LIBRARY) { tab = Tab.LIBRARY; render() } else confirmExit()
    }

    private fun confirmExit() {
        refreshSession()
        if (session.lockHeld) {
            val dialog = AlertDialog.Builder(this).setTitle("Cierra primero el juego")
                .setMessage(sessionText()).setNegativeButton(R.string.stay, null)
            if (session.mayResume) dialog.setPositiveButton("Volver al juego") { _, _ -> resumeEngine() }
            dialog.show()
            return
        }
        AlertDialog.Builder(this).setTitle(R.string.exit_title).setMessage(R.string.exit_message)
            .setNegativeButton(R.string.stay, null)
            .setPositiveButton(R.string.close) { _, _ -> cancelImport(); finish() }.show()
    }

    private fun message(text: String) {
        AlertDialog.Builder(this).setTitle(R.string.error_title).setMessage(text.take(800))
            .setPositiveButton(R.string.accept, null).show()
    }

    private fun onUi(action: () -> Unit) = runOnUiThread { if (!isFinishing && !isDestroyed) action() }

    private fun card(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(20), dp(16), dp(20), dp(16))
        background = rounded(panel, Color.rgb(39, 72, 91))
        page.addView(this, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(12) })
    }

    private fun label(text: String, size: Int, color: Int, bold: Boolean = false) = TextView(this).apply {
        this.text = text
        textSize = size.toFloat()
        setTextColor(color)
        if (bold) setTypeface(typeface, Typeface.BOLD)
        setLineSpacing(dp(2).toFloat(), 1.08f)
    }

    private fun button(text: String, onClick: () -> Unit) = Button(this).apply {
        this.text = text
        isAllCaps = false
        textSize = 16f
        gravity = Gravity.CENTER_VERTICAL or Gravity.START
        setPadding(dp(18), dp(8), dp(18), dp(8))
        isFocusable = true
        minimumHeight = dp(46)
        minHeight = dp(46)
        background = StateListDrawable().apply {
            addState(intArrayOf(android.R.attr.state_focused), rounded(accent, accent))
            addState(intArrayOf(android.R.attr.state_pressed), rounded(accent, accent))
            addState(intArrayOf(), rounded(Color.rgb(26, 56, 77), Color.rgb(49, 85, 106)))
        }
        setTextColor(ColorStateList(arrayOf(intArrayOf(android.R.attr.state_focused),
            intArrayOf(android.R.attr.state_pressed), intArrayOf()),
            intArrayOf(Color.rgb(7, 30, 35), Color.rgb(7, 30, 35), ink)))
        setOnClickListener { onClick() }
    }

    private fun buttonLayout(height: Int = 48) =
        LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(height)).apply {
            topMargin = dp(10)
        }

    private fun rounded(fill: Int, border: Int) = GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = dp(10).toFloat()
        setColor(fill)
        setStroke(dp(1), border)
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density + 0.5f).toInt()

    override fun onDestroy() {
        resumed = false
        sessionHandler.removeCallbacks(sessionPoll)
        cancelled.set(true)
        runCatching { activeSource.getAndSet(null)?.close() }
        io.shutdownNow()
        inputManager.unregisterInputDeviceListener(this)
        runCatching { unregisterReceiver(usbReceiver) }
        super.onDestroy()
    }

    companion object {
        private const val PICK_GAME = 10
        private const val PICK_FOLDER = 11
    }
}
