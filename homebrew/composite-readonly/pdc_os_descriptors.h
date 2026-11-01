/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef PDC_OS_DESCRIPTORS_H
#define PDC_OS_DESCRIPTORS_H
#include <stddef.h>
#include <stdint.h>

/* Microsoft OS 1.0 descriptors for the vendor interface ONLY.
 * HID interface 0 continues to use Windows' HID driver, never WinUSB.
 * PSP USBBus may intercept string index 0xEE: hardware enumeration must
 * confirm routing before claiming automatic binding actually works. */
#define PDC_OS_PROPERTY_MAX 256u
size_t pdc_os_string(uint8_t *out, size_t capacity);
size_t pdc_os_compatible(uint8_t *out, size_t capacity);
size_t pdc_os_properties(uint8_t *out, size_t capacity);
size_t pdc_os_control(uint8_t type, uint8_t request, uint16_t value,
                      uint16_t index, uint16_t requested,
                      uint8_t *out, size_t capacity);
#endif
