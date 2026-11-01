/* SPDX-License-Identifier: GPL-3.0-only
 * FuSa's padded descriptor layout, with PSPSDK's actual wire declarations.
 * The extra pointers are PSP USBBus metadata, not bytes sent to the host.
 */
#ifndef BRIDGE_DESCRIPTORS_H
#define BRIDGE_DESCRIPTORS_H
#include <stddef.h>
#include <pspusbbus.h>
#include "report_descriptor.h"

typedef struct {
    struct EndpointDescriptor wire;
    uint8_t padding;
    uint8_t *extra;
    int extra_length;
} BridgeEndpointDesc;
typedef struct {
    struct InterfaceDescriptor wire;
    uint8_t padding[3];
    BridgeEndpointDesc *endpoints;
    uint8_t *extra;
    int extra_length;
} BridgeInterfaceDesc;
typedef struct {
    BridgeInterfaceDesc *descriptors;
    unsigned int alternate;
    unsigned int count;
} BridgeSettings;
typedef struct {
    struct ConfigDescriptor wire;
    uint8_t padding[3];
    BridgeSettings *settings;
    uint8_t *extra;
    int extra_length;
} BridgeConfigDesc;
typedef struct {
    BridgeConfigDesc *config;
    BridgeSettings *settings;
    BridgeInterfaceDesc *interfaces;
    BridgeEndpointDesc *endpoints;
} BridgeConfig;
typedef struct {
    struct DeviceDescriptor wire;
    uint8_t padding[2];
} BridgeDeviceDesc;

_Static_assert(sizeof(BridgeDeviceDesc) == 20, "PSP device padding");
_Static_assert(sizeof(BridgeConfigDesc) == 24, "PSP config padding");
_Static_assert(sizeof(BridgeInterfaceDesc) == 24, "PSP interface padding");
_Static_assert(sizeof(BridgeEndpointDesc) == 16, "PSP endpoint stride");
_Static_assert(offsetof(BridgeConfigDesc, settings) == 12, "PSP config pointer");
_Static_assert(offsetof(BridgeInterfaceDesc, endpoints) == 12, "PSP endpoint pointer");

static struct UsbEndpoint endpoints[2] = {{0, 0, 0}, {1, 0, 0}};
static struct UsbInterface interface = {-1, 0, 1};
/* Firmware may substitute the manufacturer/product device identity. */
static struct StringDescriptor strings[] = {
    {.bLength = 26, .bDescriptorType = 3,
     .bString = {'P','S','P',' ','D','o','c','k',' ','H','I','D'}},
    {.bLength = 0, .bDescriptorType = 3}
};
static uint8_t hid_descriptor[9] __attribute__((aligned(64))) = {
    9, 0x21, 0x10, 0x01, 0, 1, 0x22,
    sizeof(report_descriptor) & 255, sizeof(report_descriptor) >> 8
};
#define DEVICE_DESC(bcd, packet) { .wire = { \
    .bLength = 18, .bDescriptorType = 1, .bcdUSB = bcd, \
    .bMaxPacketSize = packet, .bcdDevice = 0x0104, .bNumConfigurations = 1 } }
static BridgeDeviceDesc device_hi = DEVICE_DESC(0x0200, 64);
static BridgeDeviceDesc device_full = DEVICE_DESC(0x0110, 8);
#define ENDPOINT_DESC { .wire = {.bLength = 7, .bDescriptorType = 5, \
    .bEndpointAddress = 0x81, .bmAttributes = 3, .wMaxPacketSize = 8, .bInterval = 10} }
static BridgeEndpointDesc endpoint_hi[2] __attribute__((aligned(16))) = {
    ENDPOINT_DESC, {.wire = {.bLength = 0}}
};
static BridgeEndpointDesc endpoint_full[2] __attribute__((aligned(16))) = {
    ENDPOINT_DESC, {.wire = {.bLength = 0}}
};
#define INTERFACE_DESC(ep) { .wire = {.bLength = 9, .bDescriptorType = 4, \
    .bNumEndpoints = 1, .bInterfaceClass = 3, .iInterface = 1}, \
    .endpoints = ep, .extra = hid_descriptor, .extra_length = sizeof(hid_descriptor) }
static BridgeInterfaceDesc interface_hi[2] = { INTERFACE_DESC(endpoint_hi), {.wire = {.bLength = 0}} };
static BridgeInterfaceDesc interface_full[2] = { INTERFACE_DESC(endpoint_full), {.wire = {.bLength = 0}} };
static BridgeSettings settings_hi = {interface_hi, 0, 1};
static BridgeSettings settings_full = {interface_full, 0, 1};
#define CONFIG_DESC(s) { .wire = {.bLength = 9, .bDescriptorType = 2, \
    .wTotalLength = 34, .bNumInterfaces = 1, .bConfigurationValue = 1, \
    .bmAttributes = 0xc0}, .settings = s }
static BridgeConfigDesc configdesc_hi = CONFIG_DESC(&settings_hi);
static BridgeConfigDesc configdesc_full = CONFIG_DESC(&settings_full);
static BridgeConfig config_hi = {&configdesc_hi, &settings_hi, interface_hi, endpoint_hi};
static BridgeConfig config_full = {&configdesc_full, &settings_full, interface_full, endpoint_full};
#endif
