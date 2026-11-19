/* SPDX-License-Identifier: GPL-3.0-only
 * FuSa's padded descriptor layout, with PSPSDK's actual wire declarations.
 * The extra pointers are PSP USBBus metadata, not bytes sent to the host.
 */
#ifndef PSPDOCK_NATIVE_DESCRIPTORS_H
#define PSPDOCK_NATIVE_DESCRIPTORS_H
#include <stddef.h>
#include <pspusbbus.h>
#include "native_common.h"
#include "native_report_descriptor.h"

typedef struct {
    struct EndpointDescriptor wire;
    uint8_t padding;
    uint8_t *extra;
    int extra_length;
} PdnEndpointDesc;
typedef struct {
    struct InterfaceDescriptor wire;
    uint8_t padding[3];
    PdnEndpointDesc *endpoints;
    uint8_t *extra;
    int extra_length;
} PdnInterfaceDesc;
typedef struct {
    PdnInterfaceDesc *descriptors;
    unsigned int alternate;
    unsigned int count;
} PdnSettings;
typedef struct {
    struct ConfigDescriptor wire;
    uint8_t padding[3];
    PdnSettings *settings;
    uint8_t *extra;
    int extra_length;
} PdnConfigDesc;
typedef struct {
    PdnConfigDesc *config;
    PdnSettings *settings;
    PdnInterfaceDesc *interfaces;
    PdnEndpointDesc *endpoints;
} PdnConfig;
typedef struct {
    struct DeviceDescriptor wire;
    uint8_t padding[2];
} PdnDeviceDesc;

_Static_assert(sizeof(PdnDeviceDesc) == 20, "PSP device padding");
_Static_assert(sizeof(PdnConfigDesc) == 24, "PSP config padding");
_Static_assert(sizeof(PdnInterfaceDesc) == 24, "PSP interface padding");
_Static_assert(sizeof(PdnEndpointDesc) == 16, "PSP endpoint stride");
_Static_assert(offsetof(PdnConfigDesc, settings) == 12, "PSP config pointer");
_Static_assert(offsetof(PdnInterfaceDesc, endpoints) == 12, "PSP endpoint pointer");

static struct UsbEndpoint pdn_endpoints[2] = {{0, 0, 0}, {1, 0, 0}};
static struct UsbInterface pdn_interface = {-1, 0, 1};
/* Firmware may substitute the manufacturer/product device identity. */
static struct StringDescriptor pdn_strings[] = {
    {.bLength = 26, .bDescriptorType = 3,
     .bString = {'P','S','P',' ','D','o','c','k',' ','H','I','D'}},
    {.bLength = 0, .bDescriptorType = 3}
};
static uint8_t pdn_hid_descriptor[9] __attribute__((aligned(64))) = {
    9, 0x21, 0x10, 0x01, 0, 1, 0x22,
    sizeof(report_descriptor) & 255, sizeof(report_descriptor) >> 8
};
#define DEVICE_DESC(bcd, packet) { .wire = { \
    .bLength = 18, .bDescriptorType = 1, .bcdUSB = bcd, \
    .bMaxPacketSize = packet, .bcdDevice = PDN_REVISION, .bNumConfigurations = 1 } }
static PdnDeviceDesc pdn_device_hi __attribute__((aligned(64))) = DEVICE_DESC(0x0200, 64);
static PdnDeviceDesc pdn_device_full __attribute__((aligned(64))) = DEVICE_DESC(0x0200, 64);
#define ENDPOINT_DESC(interval) { .wire = {.bLength = 7, .bDescriptorType = 5, \
    .bEndpointAddress = 0x81, .bmAttributes = 3, .wMaxPacketSize = 8, .bInterval = interval} }
static PdnEndpointDesc pdn_endpoint_hi[2] __attribute__((aligned(64))) = {
    ENDPOINT_DESC(6), {.wire = {.bLength = 0}}
};
static PdnEndpointDesc pdn_endpoint_full[2] __attribute__((aligned(64))) = {
    ENDPOINT_DESC(4), {.wire = {.bLength = 0}}
};
#define INTERFACE_DESC(ep) { .wire = {.bLength = 9, .bDescriptorType = 4, \
    .bNumEndpoints = 1, .bInterfaceClass = 3, .iInterface = 1}, \
    .endpoints = ep, .extra = pdn_hid_descriptor, .extra_length = sizeof(pdn_hid_descriptor) }
static PdnInterfaceDesc pdn_interfaces_hi[2] __attribute__((aligned(64))) = { INTERFACE_DESC(pdn_endpoint_hi), {.wire = {.bLength = 0}} };
static PdnInterfaceDesc pdn_interfaces_full[2] __attribute__((aligned(64))) = { INTERFACE_DESC(pdn_endpoint_full), {.wire = {.bLength = 0}} };
static PdnSettings pdn_settings_hi = {pdn_interfaces_hi, 0, 1};
static PdnSettings pdn_settings_full = {pdn_interfaces_full, 0, 1};
#define CONFIG_DESC(s) { .wire = {.bLength = 9, .bDescriptorType = 2, \
    .wTotalLength = 34, .bNumInterfaces = 1, .bConfigurationValue = 1, \
    .bmAttributes = 0xc0}, .settings = s }
static PdnConfigDesc pdn_configdesc_hi = CONFIG_DESC(&pdn_settings_hi);
static PdnConfigDesc pdn_configdesc_full = CONFIG_DESC(&pdn_settings_full);
static PdnConfig pdn_config_hi = {&pdn_configdesc_hi, &pdn_settings_hi, pdn_interfaces_hi, pdn_endpoint_hi};
static PdnConfig pdn_config_full = {&pdn_configdesc_full, &pdn_settings_full, pdn_interfaces_full, pdn_endpoint_full};
#endif
