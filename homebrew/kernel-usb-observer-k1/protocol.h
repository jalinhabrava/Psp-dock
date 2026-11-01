/* PSP Dock K1: pure, read-only kernel USB observation ABI.
 *
 * Not a USB role manager. No control commands and NO authorization to eject
 * USB, enable HID, mount storage or synchronize game saves.
 */
#ifndef PSPDOCK_K1_USB_PROTOCOL_H
#define PSPDOCK_K1_USB_PROTOCOL_H
#include <psptypes.h>

#define PSPDOCK_K1_MAGIC 0x50444B31u
#define PSPDOCK_K1_VERSION 1u
#define PSPDOCK_K1_CAPABILITIES 0u
#define PSPDOCK_K1_PENDING 0u
#define PSPDOCK_K1_READY 1u
#define PSPDOCK_K1_CANCELLED 2u

typedef struct {
    u32 magic;
    u32 version;
    u32 capabilities;
    u32 state;
    s32 usb_state;
    s32 bus_driver_state;
    s32 storage_driver_state;
} PspDockK1Snapshot;

_Static_assert(sizeof(PspDockK1Snapshot) == 28, "K1 ABI size must be fixed");
#endif
