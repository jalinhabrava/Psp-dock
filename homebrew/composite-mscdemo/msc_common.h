/* SPDX-License-Identifier: GPL-3.0-only
 * USER/kernel ABI for the synthetic USBSTOR + HID diagnostic only.
 * This is not a mounted or mirrored view of the real Memory Stick.
 */
#ifndef PSPDOCK_MSC_COMMON_H
#define PSPDOCK_MSC_COMMON_H
#include <stdint.h>
#include <stddef.h>

#define PDC_DRIVER_NAME "PSPDockStorageTest"
/* Lab-only product value, distinct from the existing HID/vendor 01CB.
 * Sony's bus supplies VID 054C when sceUsbActivate is called. These are
 * diagnostic values, not a claim to a newly allocated production VID/PID. */
#define PDC_TEST_PRODUCT 0x01cc
#define PDC_REVISION 0x0205
#define PDC_USB_INTERFACE 1
#define PDC_HID_IN 0x81
#define PDC_BULK_IN 0x82
#define PDC_BULK_OUT 0x03
#define PDC_COMMAND_CELL 64u
#define PDC_RESPONSE_CELL 65536u
#define PDC_STATUS_REQUEST 0x53u
#define PDC_LAUNCH_MAGIC 0x35434450u
#define PDC_CAPABILITIES 1u

enum PdcStage {
    PDC_STAGE_INIT=0, PDC_STAGE_GUARD=1, PDC_STAGE_READY=2,
    PDC_STAGE_CONNECTED=3, PDC_STAGE_FILE=4, PDC_STAGE_STOPPING=5,
    PDC_STAGE_STOPPED=6, PDC_STAGE_ERROR=255
};

/* Status only: never a filesystem authority. Kernel owns every field except
 * request_stop; the USER launcher retains this storage until module unload.
 * Preserve the previously audited ABI/size. Legacy ctrl_os_* diagnostic
 * slots may remain unused; this build does not serve MS OS descriptors. */
typedef struct {
    uint32_t magic, size;
    volatile uint32_t request_stop, stage, safe_to_unload;
    volatile int32_t error;
    volatile uint32_t hid_reports, bulk_commands, bytes_read, rx_errors;
    volatile uint32_t ctrl_requests, ctrl_os_string, ctrl_os_compat;
    volatile uint32_t ctrl_os_properties, ctrl_status;
    volatile uint32_t last_setup, last_value_index;
} PdcSharedStatus;

typedef struct {
    uint32_t magic, size;
    PdcSharedStatus *shared;
} PdcLaunchArgs;

#endif
