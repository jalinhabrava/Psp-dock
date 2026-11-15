using System.Net.Http.Json;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace PspDockWebShell;

/// <summary>
/// One top-level Windows window, two independent WebView2 views. The game view
/// navigates directly to the author's origin: it is NOT a third-party iframe.
/// Neither an untrusted game nor the XMB may ask the host to load an arbitrary
/// URL. The USB and SAVEDATA lifecycle remains owned by dock.py.
/// </summary>
public sealed class DockWindow : Form
{
    private sealed record Port(
        string Id, string Title, Uri Url, bool KeyboardArrows,
        string ControlProfile, string? StartSelector, bool AutoStart);

    private static readonly HashSet<string> AuthorHosts = new(StringComparer.OrdinalIgnoreCase)
    {
        "tonyhawkpro3.gamingretro.online", "dragonballbt3.online",
        "nfsu2.xyz",
        "phoboslab.org", "d07riv.github.io"
    };

    private readonly Uri _dock;
    private readonly bool _previewWebPorts;
    private readonly HttpClient _client;
    private readonly WebView2 _xmbView = new() { Dock = DockStyle.Fill };
    private readonly WebView2 _gameView = new() { Dock = DockStyle.Fill };
    private readonly Panel _gamePanel = new() { Dock = DockStyle.Fill, Visible = false, BackColor = Color.Black };
    private readonly Label _gameTitle = new()
    {
        Dock = DockStyle.Fill, ForeColor = Color.White, AutoEllipsis = true,
        Font = new Font("Segoe UI", 11, FontStyle.Bold), TextAlign = ContentAlignment.MiddleLeft
    };
    private readonly System.Windows.Forms.Timer _inputTimer = new() { Interval = 80 };
    private readonly Dictionary<string, Port> _ports = new(StringComparer.Ordinal);
    private readonly Dictionary<string, Port> _previewPorts = new(StringComparer.Ordinal);
    private readonly HashSet<CoreWebView2Frame> _gameFrames = new();
    private Port? _active;
    private bool _polling;
    private readonly ExitHoldGesture _xboxExit = new();
    private readonly ExitHoldGesture _pspExit = new();
    private int _gameGeneration;
    private bool HasGameFocus => _active != null && (Form.ActiveForm == this || ContainsFocus);

    public DockWindow(Uri dock, bool previewWebPorts = false)
    {
        _dock = dock;
        _previewWebPorts = previewWebPorts;
        _client = new HttpClient
        {
            BaseAddress = dock,
            Timeout = TimeSpan.FromSeconds(3)
        };
        Text = previewWebPorts ? "PSP Dock · prueba local de juegos" : "PSP Dock";
        WindowState = FormWindowState.Maximized;
        MinimumSize = new Size(800, 480);
        BackColor = Color.FromArgb(6, 37, 54);
        Icon = SystemIcons.Application;

        var top = new TableLayoutPanel
        {
            Dock = DockStyle.Top, Height = 46, BackColor = Color.FromArgb(12, 41, 56),
            ColumnCount = 3, RowCount = 1
        };
        top.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 220));
        top.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        top.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 290));
        var back = new Button
        {
            Text = "← Volver al XMB", Dock = DockStyle.Fill,
            FlatStyle = FlatStyle.Flat, ForeColor = Color.White,
            BackColor = Color.FromArgb(22, 84, 102)
        };
        back.FlatAppearance.BorderSize = 0;
        back.Click += (_, _) => ClosePort();
        var hint = new Label
        {
            Text = "Start + Select (Menu + View) · 2 s", Dock = DockStyle.Fill,
            ForeColor = Color.LightGray, TextAlign = ContentAlignment.MiddleCenter
        };
        top.Controls.Add(back, 0, 0);
        top.Controls.Add(_gameTitle, 1, 0);
        top.Controls.Add(hint, 2, 0);
        _gamePanel.Controls.Add(_gameView);
        _gamePanel.Controls.Add(top);
        Controls.Add(_xmbView);
        Controls.Add(_gamePanel);
        _inputTimer.Tick += async (_, _) => await PollPspAsync();
        Deactivate += (_, _) => { _xboxExit.Reset(); _pspExit.Reset(); };
    }

    protected override async void OnShown(EventArgs e)
    {
        base.OnShown(e);
        try
        {
            await InitializeBrowserAsync();
        }
        catch (Exception ex)
        {
            MessageBox.Show("No se pudo iniciar WebView2 en PSP Dock:\n" + ex.Message,
                "PSP Dock", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }
    }

    private async Task InitializeBrowserAsync()
    {
        // An installed WebView2 runtime is required (Edge runtime, not Edge UI).
        var userData = Path.Combine(Environment.GetFolderPath(
            Environment.SpecialFolder.LocalApplicationData),
            _previewWebPorts ? "PSP-Dock-SOCOM-Preview" : "PSP-Dock", "WebView2");
        var env = await CoreWebView2Environment.CreateAsync(userDataFolder: userData);
        await _xmbView.EnsureCoreWebView2Async(env);
        await _gameView.EnsureCoreWebView2Async(env);
        _xmbView.CoreWebView2.WebMessageReceived += OnDockMessage;
        _xmbView.CoreWebView2.NewWindowRequested += (_, args) => args.Handled = true;
        _xmbView.CoreWebView2.NavigationStarting += (_, args) =>
        {
            if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out var target) ||
                target.GetLeftPart(UriPartial.Authority) !=
                _dock.GetLeftPart(UriPartial.Authority))
                args.Cancel = true;
        };
        // The external game view cannot call privileged host actions.
        _gameView.CoreWebView2.Settings.AreHostObjectsAllowed = false;
        _gameView.CoreWebView2.Settings.AreDevToolsEnabled = false;
        _gameView.CoreWebView2.NewWindowRequested += (_, args) => args.Handled = true;
        // The untrusted game may only request to CLOSE itself, never to
        // open an arbitrary URL or perform privileged USB/save operations.
        _gameView.CoreWebView2.WebMessageReceived += (_, args) =>
        {
            if (_active == null || !HasGameFocus ||
                !Uri.TryCreate(args.Source, UriKind.Absolute, out var source) ||
                !string.Equals(source.Host, _active.Url.Host, StringComparison.OrdinalIgnoreCase))
                return;
            try
            {
                using var payload = JsonDocument.Parse(args.WebMessageAsJson);
                if (payload.RootElement.TryGetProperty("type", out var type) &&
                    type.GetString() == "game-exit-request")
                    ClosePort();
            }
            catch (JsonException) { /* Unknown data from the remote page. */ }
        };
        // Some ports host the emulator in a cross-origin child frame. Virtual
        // PSP input must reach that frame as well as the top-level author page.
        _gameView.CoreWebView2.FrameCreated += (_, args) =>
        {
            var frame = args.Frame;
            _gameFrames.Add(frame);
            frame.Destroyed += (_, _) => _gameFrames.Remove(frame);
        };
        _gameView.CoreWebView2.NavigationStarting += (_, args) =>
        {
            if (_active == null || !Uri.TryCreate(args.Uri, UriKind.Absolute, out var uri) ||
                uri.Scheme != Uri.UriSchemeHttps ||
                uri.UserInfo.Length > 0 ||
                !string.Equals(uri.GetLeftPart(UriPartial.Authority),
                    _active.Url.GetLeftPart(UriPartial.Authority), StringComparison.OrdinalIgnoreCase))
            {
                if (args.Uri != "about:blank") args.Cancel = true;
            }
        };
        _gameView.CoreWebView2.ProcessFailed += (_, _) =>
        {
            if (_active != null) ClosePort();
        };
        using var stream = Assembly.GetExecutingAssembly()
            .GetManifestResourceStream("PspDockWebShell.GamepadBridge.js")
            ?? throw new InvalidOperationException("Falta puente HID WebView2.");
        using var reader = new StreamReader(stream);
        await _gameView.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
            await reader.ReadToEndAsync());
        await LoadCatalogAsync();
        if (_previewWebPorts) await LoadPreviewCatalogAsync();
        _xmbView.Source = _dock;
    }

    private static Port? ParsePort(JsonElement item)
    {
        if (!item.TryGetProperty("id", out var idNode) ||
            !item.TryGetProperty("title", out var titleNode) ||
            !item.TryGetProperty("url", out var urlNode) ||
            idNode.ValueKind != JsonValueKind.String ||
            titleNode.ValueKind != JsonValueKind.String ||
            urlNode.ValueKind != JsonValueKind.String)
            return null;
        var id = idNode.GetString() ?? "";
        var title = titleNode.GetString() ?? "";
        var url = urlNode.GetString() ?? "";
        if (id.Length == 0 || id.Length > 64 || title.Length > 120 ||
            !Uri.TryCreate(url, UriKind.Absolute, out var uri) ||
            uri.Scheme != Uri.UriSchemeHttps ||
            !AuthorHosts.Contains(uri.Host) || uri.UserInfo.Length > 0 ||
            !uri.IsDefaultPort || uri.Query.Length > 0 || uri.Fragment.Length > 0)
            return null;
        var state = item.TryGetProperty("launch_state", out var launchNode) &&
                    launchNode.ValueKind == JsonValueKind.String
                    ? launchNode.GetString() : null;
        // Even a stale or manipulated local XMB cannot launch metadata that
        // has not passed direct-runtime and controller-only checks.
        if (state is "unverified" or "requires_original_iso" or "needs_pointer")
            return null;
        var profile = id switch
        {
            "wipeout" => "wipeout",
            _ => "gamepad"
        };
        var start = id switch
        {
            "nfs-underground-2" => "#play",
            "wipeout" => "#load-full-version",
            _ => null
        };
        return new Port(id, title, uri, profile == "arrows", profile, start,
            start is not null);
    }

    private async Task LoadCatalogAsync()
    {
        using var response = await _client.GetAsync("api/web-ports");
        response.EnsureSuccessStatusCode();
        using var doc = await response.Content.ReadFromJsonAsync<JsonDocument>()
            ?? throw new InvalidOperationException("Catálogo web vacío.");
        if (!doc.RootElement.TryGetProperty("games", out var games) ||
            games.ValueKind != JsonValueKind.Array)
            throw new InvalidOperationException("Catálogo web no válido.");
        _ports.Clear();
        foreach (var entry in games.EnumerateArray())
        {
            var port = ParsePort(entry);
            if (port != null) _ports.TryAdd(port.Id, port);
        }
    }

    private void OnDockMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        // Only the trusted local XMB gets a navigation bridge.
        if (!Uri.TryCreate(args.Source, UriKind.Absolute, out var source) ||
            source.GetLeftPart(UriPartial.Authority) != _dock.GetLeftPart(UriPartial.Authority) ||
            source.AbsolutePath != "/")
            return;
        try
        {
            using var doc = JsonDocument.Parse(args.WebMessageAsJson);
            var msg = doc.RootElement;
            var messageType = msg.GetProperty("type").GetString();
            if (messageType == "close-web-port")
            {
                if (msg.TryGetProperty("reason", out var reason) &&
                    reason.ValueKind == JsonValueKind.String &&
                    reason.GetString() == "controller-exit" && !HasGameFocus) return;
                ClosePort();
                return;
            }
            if (messageType is not ("launch-web-port" or "launch-preview-web-port")) return;
            var id = msg.GetProperty("id").GetString() ?? "";
            var catalog = messageType == "launch-preview-web-port" ? _previewPorts : _ports;
            if (catalog.TryGetValue(id, out var port))
                OpenPort(port);
            else
                NotifyDock(new { type = "web-port-error", message = "Juego no aprobado en este catálogo." });
        }
        catch (JsonException) { /* Ignore messages from invalid documents. */ }
        catch (KeyNotFoundException) { /* Invalid message schema. */ }
        catch (InvalidOperationException) { /* Invalid message type. */ }
    }

    private async Task LoadPreviewCatalogAsync()
    {
        using var health = await _client.GetFromJsonAsync<JsonDocument>("api/health");
        if (health == null || !PreviewWebPortPolicy.Enabled(health.RootElement))
            throw new InvalidOperationException("La prueba de juegos necesita un servidor --demo --preview-web-ports.");
        using var manifest = await _client.GetFromJsonAsync<JsonDocument>("api/preview-web-ports");
        if (manifest == null || !PreviewWebPortPolicy.ValidManifest(manifest.RootElement))
            throw new InvalidOperationException("Manifiesto experimental no válido.");
        _previewPorts.Clear();
        foreach (var entry in manifest.RootElement.GetProperty("games").EnumerateArray())
        {
            var preview = PreviewWebPortPolicy.Parse(entry);
            if (preview == null) continue;
            // Reuse a reviewed profile only for the exact same author URL.
            // Other experimental entries get no auto-start or file handling.
            var known = _ports.Values.FirstOrDefault(port => port.Url == preview.Url);
            var port = known is null ?
                new Port(preview.Id, preview.Title, preview.Url, false, "gamepad", null, false) :
                known with { Id = preview.Id, Title = preview.Title };
            if (!_previewPorts.TryAdd(port.Id, port))
                throw new InvalidOperationException("ID experimental duplicado.");
        }
    }

    private void NotifyDock(object message)
    {
        if (_xmbView.CoreWebView2 != null)
            _xmbView.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(message));
    }

    private void OpenPort(Port port)
    {
        if (_active != null) ClosePort();
        _active = port;
        _gameTitle.Text = port.Title;
        _xboxExit.Reset();
        _pspExit.Reset();
        _gameGeneration++;
        _gamePanel.Visible = true;
        _gamePanel.BringToFront();
        NotifyDock(new { type = "web-port-session", active = true, id = port.Id });
        _gameView.CoreWebView2.Navigate(port.Url.ToString());
        _gameView.Focus();
        _inputTimer.Start();
    }

    private void ClosePort()
    {
        if (_active == null) return;
        _inputTimer.Stop();
        _active = null;
        _xboxExit.Reset();
        _pspExit.Reset();
        _gameGeneration++;
        _gameFrames.Clear();
        _gameView.CoreWebView2.Navigate("about:blank");
        _gamePanel.Visible = false;
        NotifyDock(new { type = "web-port-session", active = false });
        _xmbView.Focus();
    }

    private static bool IsDown(JsonElement frame, string key) =>
        frame.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.True;

    private async Task PollPspAsync()
    {
        if (_active == null) return;
        // Read Xbox in the host before the HTTP re-entry guard. A busy game
        // renderer, child frame or slow PSP backend cannot starve this gesture.
        var xbox = XboxInput.Read();
        var foreground = HasGameFocus;
        if (_xboxExit.Update(foreground ? xbox?.id : null,
                foreground && xbox?.StartSelectHeld == true, Environment.TickCount64))
        {
            ClosePort();
            return;
        }
        if (_polling) return;
        _polling = true;
        var generation = _gameGeneration;
        try
        {
            using var response = await _client.GetAsync("api/controller-input");
            response.EnsureSuccessStatusCode();
            using var doc = await response.Content.ReadFromJsonAsync<JsonDocument>();
            if (doc == null || _active == null || generation != _gameGeneration) return;
            var frame = doc.RootElement;
            var connected = IsDown(frame, "connected");
            var chord = HasGameFocus && connected && IsDown(frame, "settings") && IsDown(frame, "exit");
            if (_pspExit.Update(connected ? "psp" : null, chord, Environment.TickCount64))
            {
                ClosePort();
                return;
            }
            // No keyboard or gamepad injection into arbitrary Windows windows.
            // The injected bridge exists ONLY inside this author's WebView2.
            var inputMessage = JsonSerializer.Serialize(new
            {
                type = "psp-input", input = frame, keyboardArrows = _active.KeyboardArrows,
                controlProfile = _active.ControlProfile, startSelector = _active.StartSelector,
                autoStart = _active.AutoStart, xbox = XboxInput.Read()
            });
            _gameView.CoreWebView2.PostWebMessageAsJson(inputMessage);
            foreach (var child in _gameFrames.ToArray())
            {
                try { child.PostWebMessageAsJson(inputMessage); }
                catch (COMException) { _gameFrames.Remove(child); }
                catch (InvalidOperationException) { _gameFrames.Remove(child); }
                catch (ArgumentException) { _gameFrames.Remove(child); }
            }
        }
        catch (HttpRequestException) { _pspExit.Reset(); }
        catch (TaskCanceledException) { _pspExit.Reset(); }
        catch (JsonException) { _pspExit.Reset(); }
        catch (COMException) { _pspExit.Reset(); }
        catch (InvalidOperationException) { _pspExit.Reset(); }
        finally { _polling = false; }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _inputTimer.Dispose();
            _client.Dispose();
        }
        base.Dispose(disposing);
    }
}
