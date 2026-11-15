using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace PspDockWebShell;

internal sealed record PreviewPort(string Id, string Title, Uri Url);

// Independent native validation of the opt-in local manifest. XMB messages
// contain only IDs; they never supply a URL, path, script or file selection.
internal static class PreviewWebPortPolicy
{
    private const string Source = "https://ported2browser.com/";

    internal static bool Enabled(JsonElement health) =>
        Text(health, "product") == "psp-dock" &&
        True(health, "demo") && True(health, "preview_web_ports");

    internal static bool ValidManifest(JsonElement manifest) =>
        manifest.TryGetProperty("version", out var version) &&
        version.ValueKind == JsonValueKind.Number && version.TryGetInt32(out var v) && v == 1 &&
        Text(manifest, "source") == Source && True(manifest, "demo") &&
        True(manifest, "preview_web_ports") &&
        manifest.TryGetProperty("games", out var games) && games.ValueKind == JsonValueKind.Array;

    internal static PreviewPort? Parse(JsonElement item)
    {
        var id = Text(item, "id");
        var title = Text(item, "title");
        var url = Text(item, "url");
        if (!True(item, "preview_allowed") || Text(item, "category") != "port_candidate" ||
            Text(item, "link_status") == "broken" || Text(item, "verification_state") == "unavailable" ||
            !Regex.IsMatch(id, "\\A[0-9A-HJKMNP-TV-Z]{26}\\z") || title.Length is < 1 or > 120 ||
            Text(item, "detail_url") != Source + "ports/" + id ||
            Text(item, "direct_url") != url ||
            !item.TryGetProperty("direct_candidates", out var candidates) ||
            candidates.ValueKind != JsonValueKind.Array || candidates.GetArrayLength() != 1 ||
            candidates[0].ValueKind != JsonValueKind.String || candidates[0].GetString() != url ||
            !PublicHttps(url, out var uri)) return null;
        return new PreviewPort(id, title, uri!);
    }

    internal static bool PublicHttps(string value, out Uri? uri)
    {
        uri = null;
        if (value.Length is < 9 or > 2048 || value.Any(c => char.IsControl(c) || c == '\\') ||
            !Uri.TryCreate(value, UriKind.Absolute, out var target) ||
            target.Scheme != Uri.UriSchemeHttps || target.UserInfo.Length > 0 || !target.IsDefaultPort ||
            target.HostNameType != UriHostNameType.Dns) return false;
        var host = target.Host.ToLowerInvariant().TrimEnd('.');
        if (!Regex.IsMatch(host, "\\A[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\\.[a-z]{2,}\\z") ||
            host.Contains("..") || IPAddress.TryParse(host, out _) ||
            host is "localhost" or "ported2browser.com" or "ported2browser.org" or "ported2browser.sbs" ||
            new[] { ".localhost", ".local", ".internal", ".test", ".invalid", ".example", ".ported2browser.com" }
                .Any(suffix => host.EndsWith(suffix, StringComparison.Ordinal)) ||
            Regex.IsMatch(target.Query.TrimStart('?'),
                "(?:^|&)(?:[^=&]*(?:token|secret|session|auth|password|api.?key)[^=&]*)=", RegexOptions.IgnoreCase))
            return false;
        uri = target;
        return true;
    }

    private static bool True(JsonElement item, string name) =>
        item.ValueKind == JsonValueKind.Object && item.TryGetProperty(name, out var value) &&
        value.ValueKind == JsonValueKind.True;

    private static string Text(JsonElement item, string name) =>
        item.ValueKind == JsonValueKind.Object && item.TryGetProperty(name, out var value) &&
        value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : "";
}
