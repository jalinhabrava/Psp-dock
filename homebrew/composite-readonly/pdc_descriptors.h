/* SPDX-License-Identifier: GPL-3.0-only
 * PSPSDK's padded internal tables; NOT raw USB descriptors in memory.
 * Preserve the tested FuSa HID report; add a separate vendor bulk interface.
 */
#ifndef PDC_DESCRIPTORS_H
#define PDC_DESCRIPTORS_H
#include <stddef.h>
#include <pspusbbus.h>
#include "pdc_common.h"
#include "report_descriptor.h"

typedef struct {
    struct EndpointDescriptor wire;
    uint8_t pad;
    uint8_t *extra;
    int extra_size;
} PdcEndpoint;
typedef struct {
    struct InterfaceDescriptor wire;
    uint8_t pad[3];
    PdcEndpoint *endpoints;
    uint8_t *extra;
    int extra_size;
} PdcInterface;
typedef struct {
    PdcInterface *descriptors;
    uint32_t alternate;
    uint32_t count;
} PdcSettings;
typedef struct {
    struct ConfigDescriptor wire;
    uint8_t pad[3];
    PdcSettings *settings;
    uint8_t *extra;
    int extra_size;
} PdcConfigDescriptor;
typedef struct {
    PdcConfigDescriptor *config;
    PdcSettings *settings;
    PdcInterface *interfaces;
    PdcEndpoint *endpoints;
} PdcConfiguration;
typedef struct {
    struct DeviceDescriptor wire;
    uint8_t pad[2];
} PdcDevice;

_Static_assert(sizeof(PdcDevice)==20,"PSP device table stride");
_Static_assert(sizeof(PdcEndpoint)==16,"PSP endpoint table stride");
_Static_assert(sizeof(PdcInterface)==24,"PSP interface table stride");
_Static_assert(sizeof(PdcSettings)==12,"PSP per-interface settings stride");
_Static_assert(offsetof(PdcSettings,alternate)==4,"PSP alternate-setting field");
_Static_assert(offsetof(PdcSettings,count)==8,"PSP alternate descriptor count");
_Static_assert(sizeof(PdcConfigDescriptor)==24,"PSP config table stride");

static struct UsbEndpoint pdc_endpoints[4]={{0,0,0},{1,0,0},{2,0,0},{3,0,0}};
/* One registration owns TWO interfaces; not two incompatible registrations. */
static struct UsbInterface pdc_interface={-1,0,2};
static uint8_t pdc_hid_descriptor[9] __attribute__((aligned(64)))={
    9,0x21,0x10,0x01,0,1,0x22,
    sizeof(report_descriptor)&255,sizeof(report_descriptor)>>8
};
static struct StringDescriptor pdc_strings[]={
    {.bLength=44,.bDescriptorType=3,
     .bString={'P','S','P',' ','D','o','c','k',' ','C','o','m','p','o','s','i','t','e',' ','R','O'}},
    {.bLength=0,.bDescriptorType=3}
};
#define PDC_DEVICE(bcd,packet) {.wire={.bLength=18,.bDescriptorType=1, \
    .bcdUSB=(bcd),.bMaxPacketSize=(packet),.bcdDevice=PDC_REVISION,.bNumConfigurations=1}}
static PdcDevice pdc_device_hi __attribute__((aligned(64)))=PDC_DEVICE(0x0200,64);
/* USB version is not current bus speed. Windows skips OS descriptors for
 * bcdUSB 1.0/1.1, so full-speed must still declare this USB 2.0 device. */
static PdcDevice pdc_device_full __attribute__((aligned(64)))=PDC_DEVICE(0x0200,64);
#define PDC_ENDPOINT(addr,kind,packet,interval) {.wire={.bLength=7,.bDescriptorType=5, \
    .bEndpointAddress=(addr),.bmAttributes=(kind),.wMaxPacketSize=(packet),.bInterval=(interval)}}
static PdcEndpoint pdc_endpoints_hi[4] __attribute__((aligned(64)))={
    PDC_ENDPOINT(PDC_HID_IN,3,8,10),PDC_ENDPOINT(PDC_BULK_IN,2,512,0),
    PDC_ENDPOINT(PDC_BULK_OUT,2,512,0),{.wire={.bLength=0}}
};
static PdcEndpoint pdc_endpoints_full[4] __attribute__((aligned(64)))={
    PDC_ENDPOINT(PDC_HID_IN,3,8,10),PDC_ENDPOINT(PDC_BULK_IN,2,64,0),
    PDC_ENDPOINT(PDC_BULK_OUT,2,64,0),{.wire={.bLength=0}}
};
#define PDC_HID_IF(ep) {.wire={.bLength=9,.bDescriptorType=4,.bInterfaceNumber=0, \
    .bNumEndpoints=1,.bInterfaceClass=3,.iInterface=1},.endpoints=(ep), \
    .extra=pdc_hid_descriptor,.extra_size=sizeof(pdc_hid_descriptor)}
#define PDC_FILE_IF(ep) {.wire={.bLength=9,.bDescriptorType=4,.bInterfaceNumber=1, \
    .bNumEndpoints=2,.bInterfaceClass=0xff,.bInterfaceSubClass=0x50,.bInterfaceProtocol=2, \
    .iInterface=1},.endpoints=(ep)}
static PdcInterface pdc_interfaces_hi[3] __attribute__((aligned(64)))={
    PDC_HID_IF(&pdc_endpoints_hi[0]),PDC_FILE_IF(&pdc_endpoints_hi[1]),{.wire={.bLength=0}}
};
static PdcInterface pdc_interfaces_full[3] __attribute__((aligned(64)))={
    PDC_HID_IF(&pdc_endpoints_full[0]),PDC_FILE_IF(&pdc_endpoints_full[1]),{.wire={.bLength=0}}
};
/* Sony's internal table has ONE {first_descriptor,alternate,count} record
 * PER interface. PSPSDK's older UsbInterfaces header misleadingly labels
 * the middle word a pointer. Match the actual multiple-interface layout
 * used by xerpi/psp-uvc-usb-video-class, commit 68f950a, include/usb.h and
 * include/usb_descriptors.h. Here each interface has one alternate setting. */
static PdcSettings pdc_settings_hi[2]={
    {&pdc_interfaces_hi[0],0,1},{&pdc_interfaces_hi[1],0,1}
};
static PdcSettings pdc_settings_full[2]={
    {&pdc_interfaces_full[0],0,1},{&pdc_interfaces_full[1],0,1}
};
#define PDC_CONFIG(table) {.wire={.bLength=9,.bDescriptorType=2,.wTotalLength=57, \
    .bNumInterfaces=2,.bConfigurationValue=1,.bmAttributes=0xc0},.settings=(table)}
static PdcConfigDescriptor pdc_configdesc_hi=PDC_CONFIG(pdc_settings_hi);
static PdcConfigDescriptor pdc_configdesc_full=PDC_CONFIG(pdc_settings_full);
static PdcConfiguration pdc_config_hi={&pdc_configdesc_hi,pdc_settings_hi,pdc_interfaces_hi,pdc_endpoints_hi};
static PdcConfiguration pdc_config_full={&pdc_configdesc_full,pdc_settings_full,pdc_interfaces_full,pdc_endpoints_full};
#endif
