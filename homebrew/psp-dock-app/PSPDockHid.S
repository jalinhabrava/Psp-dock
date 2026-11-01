    .set noreorder
#include "pspstub.s"
    STUB_START "PSPDockHid",0x00090000,0x00040005
    STUB_FUNC 0x7a1d88aa,bridgeHidStart
    STUB_FUNC 0xdcd83471,bridgeHidStop
    STUB_FUNC 0x57190859,bridgeHidPollStorageRequest
    STUB_FUNC 0x9237d589,bridgeHidGetStatus
    STUB_END
