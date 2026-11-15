using System.Text.Json;
using System.Text.Json.Nodes;
using PspDockWebShell;

// Optional read-only hardware acceptance aid. It never injects controller input.
if (args is ["--watch-exit"])
{
    var detector = new ExitHoldGesture();
    var deadline = Environment.TickCount64 + 120000;
    string? previous = null;
    while (Environment.TickCount64 < deadline)
    {
        var pad = XboxInput.Read();
        var status = pad == null ? "Xbox disconnected" :
            pad.id + (pad.StartSelectHeld ? " Menu+View held" : " Menu+View released");
        if (status != previous) { Console.WriteLine(status); previous = status; }
        if (detector.Update(pad?.id, pad?.StartSelectHeld == true, Environment.TickCount64))
        {
            Console.WriteLine("Physical Xbox Menu+View hold recognized after 2000 ms.");
            return;
        }
        Thread.Sleep(50);
    }
    Console.WriteLine("No complete physical hold observed.");
    Environment.ExitCode = 2;
    return;
}

static JsonElement Json(string text) => JsonDocument.Parse(text).RootElement;
static void Check(bool result, string reason)
{
    if (!result) throw new Exception(reason);
}

Check(!PreviewWebPortPolicy.Enabled(Json("{}")), "Default must be disabled");
Check(!PreviewWebPortPolicy.Enabled(Json("""
    {"product":"psp-dock","demo":false,"preview_web_ports":true}
    """)), "Production cannot opt in");
Check(PreviewWebPortPolicy.Enabled(Json("""
    {"product":"psp-dock","demo":true,"preview_web_ports":true}
    """)), "Explicit demo must opt in");
Check(!PreviewWebPortPolicy.ValidManifest(Json("""
    {"version":1,"source":"https://ported2browser.org/","demo":true,"preview_web_ports":true,"games":[]}
    """)), "Only original source accepted");

var entry = JsonNode.Parse("""
    {"id":"01ARZ3NDEKTSV4RRFFQ69G5FAV","title":"Test",
    "detail_url":"https://ported2browser.com/ports/01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "preview_allowed":true,"category":"port_candidate","link_status":"pending","verification_state":"requires_original_files",
    "url":"https://devilutionx.app/?game=demo#game=loom",
    "direct_url":"https://devilutionx.app/?game=demo#game=loom",
    "direct_candidates":["https://devilutionx.app/?game=demo#game=loom"]}
    """)!;
Check(PreviewWebPortPolicy.Parse(Json(entry.ToJsonString()))?.Url.Fragment == "#game=loom",
    "Pending runtime and own files can be tested; query and fragment survive");
foreach (var url in new[] {"http://devilutionx.app/", "https://127.0.0.1/", "https://localhost/",
    "https://pc.local/", "https://ported2browser.com/", "https://user:password@devilutionx.app/",
    "https://devilutionx.app:444/", "https://devilutionx.app/?token=secret",
    "https://devilutionx.app\\@localhost/", "file:///C:/private.iso"})
{
    Check(!PreviewWebPortPolicy.PublicHttps(url, out _), "Unsafe URL: " + url);
}
foreach (var pair in new[] {("id", "arbitrary"), ("detail_url", "https://other.com/"),
    ("link_status", "broken"), ("verification_state", "unavailable"), ("category", "repository"),
    ("url", "https://olivers.tools/kart/")})
{
    var changed = entry.DeepClone();
    changed[pair.Item1] = pair.Item2;
    Check(PreviewWebPortPolicy.Parse(Json(changed.ToJsonString())) == null, "Reject tampered " + pair.Item1);
}
var denied = entry.DeepClone();
denied["preview_allowed"] = false;
Check(PreviewWebPortPolicy.Parse(Json(denied.ToJsonString())) == null, "Manifest gate must be required");

// Optional integration with the actual manifest emitted by the Python backend.
if (args.Length == 1)
{
    using var manifest = JsonDocument.Parse(File.ReadAllText(args[0]));
    Check(PreviewWebPortPolicy.ValidManifest(manifest.RootElement), "Shipped manifest must be valid");
    int allowed = 0, blocked = 0;
    foreach (var game in manifest.RootElement.GetProperty("games").EnumerateArray())
    {
        var parsed = PreviewWebPortPolicy.Parse(game);
        Check((parsed != null) == game.GetProperty("preview_allowed").GetBoolean(),
            "Backend/native eligibility disagreement for " + game.GetProperty("id").GetString());
        if (parsed != null) allowed++; else blocked++;
    }
    Console.WriteLine($"Actual Python manifest: {allowed} allowed, {blocked} blocked; native policy agrees.");
}
Console.WriteLine("Native preview source, demo gate, ID, URL and requirement policy: OK");

// The same hold detector that runs in DockWindow, without a renderer or HTTP.
var hold = new ExitHoldGesture();
Check(!hold.Update("Xbox 0", true, 0), "A zero timestamp starts a hold");
Check(!hold.Update("Xbox 0", true, 1999), "A short hold cannot leave the game");
Check(hold.Update("Xbox 0", true, 2000), "Two seconds trigger exactly at the threshold");
Check(!hold.Update("Xbox 0", true, 4000), "One exit per continuous hold");
Check(!hold.Update("Xbox 0", false, 4100), "Release re-arms the detector");
Check(!hold.Update("Xbox 0", true, 4200), "A second hold starts from its own timestamp");
Check(hold.Update("Xbox 0", true, 6200), "A deliberate second hold works");
hold.Reset();
Check(!hold.Update("Xbox 0", true, 100), "Start another hold");
Check(!hold.Update(null, false, 1000), "Disconnect clears the deadline");
Check(!hold.Update("Xbox 0", true, 2000), "Reconnect cannot inherit the deadline");
Check(!hold.Update("Xbox 1", true, 3000), "Two controllers cannot combine their holds");
Check(!hold.Update("Xbox 1", true, 4999), "Replacement controller needs its own two seconds");
Check(hold.Update("Xbox 1", true, 5000), "Replacement controller works after its own hold");
hold.Reset();
Check(!hold.Update("psp", true, 500), "Reset on a new game discards old state");
Check(!hold.Update("psp", false, 1800), "Releasing either button interrupts a PSP hold");
Check(!hold.Update("psp", true, 1900), "A PSP re-hold starts a new interval");
Check(!hold.Update("psp", true, 3899), "PSP short re-hold cannot exit");
Check(hold.Update("psp", true, 3900), "PSP uses the same hold duration");
var xboxButtons = new float[17];
xboxButtons[4] = xboxButtons[5] = xboxButtons[8] = 1;
var xboxFrame = new XboxInput.Frame(true, "Xbox 0", xboxButtons, new float[4]);
Check(!xboxFrame.StartSelectHeld, "Old LB+RB+View gesture no longer exits");
xboxButtons[4] = xboxButtons[5] = 0;
xboxButtons[9] = 1;
Check(xboxFrame.StartSelectHeld, "View and Menu work without shoulder buttons");
xboxButtons[8] = 0;
Check(!xboxFrame.StartSelectHeld, "Menu alone cannot exit");
xboxButtons[8] = 1;
Check(!new XboxInput.Frame(false, "Xbox 0", xboxButtons, new float[4]).StartSelectHeld,
    "Disconnected gamepad state cannot exit");
Console.WriteLine("Native Start+Select hold, interruption, controller identity and Xbox mapping: OK");
