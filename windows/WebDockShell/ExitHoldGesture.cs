namespace PspDockWebShell;

/// <summary>A continuous hold from one controller, measured by a monotonic clock.</summary>
internal sealed class ExitHoldGesture
{
    internal const long HoldMilliseconds = 2000;
    private string? _source;
    private long? _since;
    private bool _sent;

    internal bool Update(string? source, bool held, long now)
    {
        if (source != _source)
        {
            Reset();
            _source = source;
        }
        if (!held || source == null)
        {
            _since = null;
            _sent = false;
            return false;
        }
        if (_since == null || now < _since.Value) _since = now;
        if (_sent || now - _since.Value < HoldMilliseconds) return false;
        _sent = true;
        return true;
    }

    internal void Reset()
    {
        _source = null;
        _since = null;
        _sent = false;
    }
}
