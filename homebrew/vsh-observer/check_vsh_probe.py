"""Static safety checks for the preflight VSH plugin.

Deliberately avoids PSP-specific dependencies. A build and static check
cannot replace a hardware test of automatic plugin loading.
"""
from pathlib import Path
import sys


def check(code: str, abi: str) -> None:
    if 'PSP_MODULE_INFO("PSPDockVSHProbe", PSP_MODULE_USER' not in code:
        raise ValueError("The startup plugin must be a USER-mode module")
    if 'sceKernelDelayThread(PROBE_DELAY_US)' not in code:
        raise ValueError("No delay before probing")
    if 'if (stopping || !read_arm_token())' not in code:
        raise ValueError("Observation must be explicitly armed")
    if '(0x200u | 0x020u | 0x002u)' not in code:
        raise ValueError("Must skip writes with active USB or attached cable")
    if 'query_with_verified_helper() == 0' not in code:
        raise ValueError("Must only report successful and completed helper")
    if 'sceKernelStopModule(mod, 0, NULL, &stop_status' not in code:
        raise ValueError("Must stop helper before unload")
    for forbidden in ("sceUsbActivate(", "sceUsbDeactivate(", "sceUsbStart(",
                      "sceUsbStop(", "sceUsbstorBoot", "flash0:", "PSP_MODULE_KERNEL"):
        if forbidden in code:
            raise ValueError("Forbidden USB/flash/kernel operation: " + forbidden)
    for token in ('#define GATE_V07_MAGIC 0x50444737u',
                  '#define GATE_V07_RESULT_MAGIC 0x52444737u',
                  'sizeof(GateResult) == 16'):
        if token not in abi:
            raise ValueError("Incompatible proven 0.7 helper ABI")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_vsh_probe.py main.c vsh_probe_protocol.h")
    check(Path(sys.argv[1]).read_text(), Path(sys.argv[2]).read_text())
    print("VSH observer static preflight OK")


if __name__ == "__main__":
    main()
