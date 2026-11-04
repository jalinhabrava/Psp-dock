/* Strict immutable preflight for HID-only lab. Portable C with no PSP imports.
 * Positive evidence only: unconfigured USB, known stopped/unregistered storage
 * and a known USB bus state. All ambiguous states deny starting HID. */
#ifndef PSP_DOCK_HID_ONLY_GATE_H
#define PSP_DOCK_HID_ONLY_GATE_H
#include <stdint.h>

#define HID_LAB_USB_BUSY_MASK (0x200u | 0x020u | 0x002u)
#define HID_LAB_STORAGE_NOENT 0x80243005u
#define HID_LAB_DRIVER_STARTED 1
#define HID_LAB_DRIVER_STOPPED 2

static inline int hid_lab_safe_to_start(int usb, int storage, int bus) {
    if (usb < 0 || ((uint32_t)usb & HID_LAB_USB_BUSY_MASK))
        return 0;
    if (!(storage == HID_LAB_DRIVER_STOPPED ||
          (uint32_t)storage == HID_LAB_STORAGE_NOENT))
        return 0;
    return bus == HID_LAB_DRIVER_STARTED || bus == HID_LAB_DRIVER_STOPPED;
}
#endif
