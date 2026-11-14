using System.Runtime.InteropServices;

namespace PspDockWebShell;

/// <summary>
/// Read-only XInput bridge for Xbox controllers. Unlike the web Gamepad API,
/// XInput does not require an activation gesture in the separate game WebView2.
/// No global keys or inputs are injected into the Windows desktop.
/// </summary>
internal static class XboxInput
{
    private const uint Success = 0;
    private const ushort DpadUp = 0x0001, DpadDown = 0x0002,
        DpadLeft = 0x0004, DpadRight = 0x0008,
        Start = 0x0010, Back = 0x0020, LeftStick = 0x0040,
        RightStick = 0x0080, LeftShoulder = 0x0100, RightShoulder = 0x0200,
        A = 0x1000, B = 0x2000, X = 0x4000, Y = 0x8000;

    [StructLayout(LayoutKind.Sequential)]
    private struct Gamepad
    {
        public ushort Buttons;
        public byte LeftTrigger;
        public byte RightTrigger;
        public short LeftX;
        public short LeftY;
        public short RightX;
        public short RightY;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct State
    {
        public uint PacketNumber;
        public Gamepad Pad;
    }

    [DllImport("xinput1_4.dll", ExactSpelling = true)]
    private static extern uint XInputGetState(uint index, out State state);

    private static float Axis(short value)
    {
        var normalized = Math.Clamp(value / (value >= 0 ? 32767f : 32768f), -1f, 1f);
        return Math.Abs(normalized) < .12f ? 0f : normalized;
    }

    internal static object? Read()
    {
        try
        {
            for (uint i = 0; i < 4; i++)
            {
                if (XInputGetState(i, out var state) != Success) continue;
                var p = state.Pad;
                bool Has(ushort mask) => (p.Buttons & mask) != 0;
                return new
                {
                    connected = true,
                    id = "Xbox XInput " + i,
                    buttons = new float[]
                    {
                        Has(A) ? 1 : 0, Has(B) ? 1 : 0, Has(X) ? 1 : 0,
                        Has(Y) ? 1 : 0, Has(LeftShoulder) ? 1 : 0,
                        Has(RightShoulder) ? 1 : 0,
                        p.LeftTrigger / 255f, p.RightTrigger / 255f,
                        Has(Back) ? 1 : 0, Has(Start) ? 1 : 0,
                        Has(LeftStick) ? 1 : 0, Has(RightStick) ? 1 : 0,
                        Has(DpadUp) ? 1 : 0, Has(DpadDown) ? 1 : 0,
                        Has(DpadLeft) ? 1 : 0, Has(DpadRight) ? 1 : 0, 0
                    },
                    axes = new [] { Axis(p.LeftX), -Axis(p.LeftY), Axis(p.RightX), -Axis(p.RightY) }
                };
            }
        }
        catch (DllNotFoundException) { /* Older Windows without XInput 1.4. */ }
        catch (EntryPointNotFoundException) { /* The installed DLL lacks the entry point. */ }
        return null;
    }
}
