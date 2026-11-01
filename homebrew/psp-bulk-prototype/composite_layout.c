/* PSP Dock: build-only USB descriptor/layout experiment for PSPDEV.
 *
 * USB DEVICE ACCESS: NONE. This source only instantiates the descriptor
 * structures in PSPSDK's padded USBBus format; it does not register a
 * driver, start USB, inspect FAT, mutate flash, or install a module.
 *
 * The existing physically verified FuSa/PSPDockHID is ONE interface.
 * This experiment proposes combining it with a vendor-specific
 * BULK interface under one configuration, preserving the joystick
 * and adding two endpoints to request files while PSP owns FAT.
 * PSP 6.61 kernel's support for this combined layout is UNPROVEN.
 *
 * Because this is not an authorized USB VID/PID assignment, the
 * prototype uses the already observed Sony PSP HID identifiers only
 * for internal tests. Before distributing commercial binaries, the
 * product must obtain an appropriate authorized USB product identity.
 */
#include <stdint.h>
#include <stddef.h>
#include <pspusbbus.h>
#include "../bridge-hid/report_descriptor.h"

typedef struct {
    struct EndpointDescriptor wire;
    uint8_t padding;
    uint8_t *extra;
    int extra_length;
} PdbEndpointDescriptor;
typedef struct {
    struct InterfaceDescriptor wire;
    uint8_t padding[3];
    PdbEndpointDescriptor *endpoints;
    uint8_t *extra;
    int extra_length;
} PdbInterfaceDescriptor;
typedef struct {
    PdbInterfaceDescriptor *descriptors;
    unsigned int alternate;
    unsigned int count;
} PdbInterfaceSettings;
typedef struct {
    struct ConfigDescriptor wire;
    uint8_t padding[3];
    PdbInterfaceSettings *settings;
    uint8_t *extra;
    int extra_length;
} PdbConfigDescriptor;
typedef struct {
    PdbConfigDescriptor *config;
    PdbInterfaceSettings *settings;
    PdbInterfaceDescriptor *interfaces;
    PdbEndpointDescriptor *endpoints;
} PdbConfig;
typedef struct {
    struct DeviceDescriptor wire;
    uint8_t padding[2];
} PdbDeviceDescriptor;

_Static_assert(sizeof(PdbDeviceDescriptor) == 20, "PSP device pad 20");
_Static_assert(sizeof(PdbConfigDescriptor) == 24, "PSP config pad 24");
_Static_assert(sizeof(PdbInterfaceDescriptor) == 24, "PSP interface pad 24");
_Static_assert(sizeof(PdbEndpointDescriptor) == 16, "PSP endpoint stride 16");
_Static_assert(offsetof(PdbConfigDescriptor, settings) == 12,
               "PSP config pointer offset");
_Static_assert(offsetof(PdbInterfaceDescriptor, endpoints) == 12,
               "PSP interface pointer offset");

/* Compare the actual custom bulk endpoint and HID interrupt endpoints.
 * IN/OUT: HID interrupt IN 0x81; BULK IN 0x82; BULK OUT 0x03. */
static struct UsbEndpoint usb_endpoints[4] __attribute__((unused)) = {
    {0,0,0},{1,0,0},{2,0,0},{3,0,0}
};
static struct UsbInterface usb_interfaces[2] __attribute__((unused)) = {
    {-1,0,1},{-1,1,2}
};

static uint8_t hid_class_descriptor[9] __attribute__((aligned(64))) = {
    9,0x21,0x11,0x01,0,1,0x22,
    sizeof(report_descriptor)&255,sizeof(report_descriptor)>>8
};

#define INTERRUPT_ENDPOINT { \
    .wire = {.bLength = 7, .bDescriptorType = 5, \
             .bEndpointAddress = 0x81, .bmAttributes = 3, \
             .wMaxPacketSize = 8, .bInterval = 10 } }
#define BULK_IN(max_packet) { \
    .wire = {.bLength = 7, .bDescriptorType = 5, \
             .bEndpointAddress = 0x82, .bmAttributes = 2, \
             .wMaxPacketSize = (max_packet), .bInterval = 0 } }
#define BULK_OUT(max_packet) { \
    .wire = {.bLength = 7, .bDescriptorType = 5, \
             .bEndpointAddress = 0x03, .bmAttributes = 2, \
             .wMaxPacketSize = (max_packet), .bInterval = 0 } }

static PdbEndpointDescriptor endpoints_high[] = {
    INTERRUPT_ENDPOINT,
    BULK_IN(512),
    BULK_OUT(512),
};
static PdbEndpointDescriptor endpoints_full[] = {
    INTERRUPT_ENDPOINT,
    BULK_IN(64),
    BULK_OUT(64),
};

#define HID_INTERFACE(eps) { \
    .wire = {.bLength = 9, .bDescriptorType = 4, \
             .bInterfaceNumber = 0, .bNumEndpoints = 1, \
             .bInterfaceClass = 3, .bInterfaceSubClass = 0, \
             .bInterfaceProtocol = 0 }, \
    .endpoints = (eps), .extra = hid_class_descriptor, \
    .extra_length = sizeof(hid_class_descriptor) \
}
#define BULK_INTERFACE(eps) { \
    .wire = {.bLength = 9, .bDescriptorType = 4, \
             .bInterfaceNumber = 1, .bNumEndpoints = 2, \
             .bInterfaceClass = 0xff, .bInterfaceSubClass = 0, \
             .bInterfaceProtocol = 0 }, \
    .endpoints = (eps) \
}
static PdbInterfaceDescriptor interfaces_high[2] = {
    HID_INTERFACE(&endpoints_high[0]),
    BULK_INTERFACE(&endpoints_high[1]),
};
static PdbInterfaceDescriptor interfaces_full[2] = {
    HID_INTERFACE(&endpoints_full[0]),
    BULK_INTERFACE(&endpoints_full[1]),
};
static PdbInterfaceSettings settings_high = {interfaces_high,0,2};
static PdbInterfaceSettings settings_full = {interfaces_full,0,2};

/* Wire total = 9 config + 9 HID iface + 9 HID desc + 7 endpoint
 *            + 9 vendor iface + 7 BULK IN + 7 BULK OUT = 57. */
#define CONFIG(s) { \
    .wire = {.bLength = 9, .bDescriptorType = 2, \
             .wTotalLength = 57, .bNumInterfaces = 2, \
             .bConfigurationValue = 1, .bmAttributes = 0xc0 }, \
    .settings = (s) \
}
static PdbConfigDescriptor config_high = CONFIG(&settings_high);
static PdbConfigDescriptor config_full = CONFIG(&settings_full);
static PdbConfig usb_config_high __attribute__((unused)) = {
    &config_high,&settings_high,interfaces_high,endpoints_high
};
static PdbConfig usb_config_full __attribute__((unused)) = {
    &config_full,&settings_full,interfaces_full,endpoints_full
};

/* Output artifact is only an Allegrex object for inspecting static data.
 * There is deliberately NO UsbDriver instance and NO module entrypoint.
 * The next stage must audit the real PSPSDK/kernel implementation of
 * interfaces array and bus enumeration before building an installable PRX. */
