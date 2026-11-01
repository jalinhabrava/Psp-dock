/*
 * PSP Dock USB Bus v0.8 guard.
 * Pure logic shared by the actual PRX and Linux native tests.
 * Every condition must pass before starting a USB bus driver.
 */
#ifndef PSP_DOCK_GATE_V08_GUARD_H
#define PSP_DOCK_GATE_V08_GUARD_H
#include <stdint.h>

#define GATE_V08_USB_NOENT UINT32_C(0x80243005)
#define GATE_V08_USB_ACTIVATED UINT32_C(0x200)
#define GATE_V08_USB_CABLE UINT32_C(0x020)
#define GATE_V08_USB_LINKED UINT32_C(0x002)
#define GATE_V08_STOPPED 2
#define GATE_V08_STARTED 1
#define GATE_V08_OK 0
#define GATE_V08_BLOCK_USB_ERROR 1
#define GATE_V08_BLOCK_CONNECTION 2
#define GATE_V08_BLOCK_BUS_OWNERSHIP 3
#define GATE_V08_BLOCK_STORAGE 4

static inline int gate_v08_precheck(int32_t usb, int32_t bus, int32_t storage) {
    if (usb < 0)
        return GATE_V08_BLOCK_USB_ERROR;
    if ((uint32_t)usb &
        (GATE_V08_USB_ACTIVATED | GATE_V08_USB_CABLE | GATE_V08_USB_LINKED))
        return GATE_V08_BLOCK_CONNECTION;
    if (bus != GATE_V08_STOPPED)
        return GATE_V08_BLOCK_BUS_OWNERSHIP;
    if (storage != GATE_V08_STOPPED &&
        (uint32_t)storage != GATE_V08_USB_NOENT)
        return GATE_V08_BLOCK_STORAGE;
    return GATE_V08_OK;
}
#endif
