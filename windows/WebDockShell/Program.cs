namespace PspDockWebShell;

static class Program
{
    [STAThread]
    static void Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        var previewWebPorts = args.Length == 2 && args[1] == "--preview-web-ports";
        if ((args.Length != 1 && !previewWebPorts) ||
            !Uri.TryCreate(args[0], UriKind.Absolute, out var uri) ||
            uri.Scheme != Uri.UriSchemeHttp || uri.Host != "127.0.0.1" ||
            uri.AbsolutePath != "/" || uri.Query.Length != 0 ||
            uri.Fragment.Length != 0 || uri.Port < 1024)
        {
            MessageBox.Show("PSP Dock WebView2 solo acepta el origen local del Dock.",
                "PSP Dock", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }
        Application.Run(new DockWindow(uri, previewWebPorts));
    }
}
