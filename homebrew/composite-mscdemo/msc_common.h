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
#define PDC_REVISION 0x0206
#define PDC_USB_INTERFACE 1
#define PDC_HID_IN 0x81
#define PDC_BULK_IN 0x82
#define PDC_BULK_OUT 0x03
#define PDC_COMMAND_CELL 64u
#define PDC_RESPONSE_CELL 65536u
#define PDC_STATUS_REQUEST 0x53u
#define PDC_LAUNCH_MAGIC 0x36434450u
#define PDC_CAPABILITIES 1u

enum PdcStage {
    PDC_STAGE_INIT=0, PDC_STAGE_GUARD=1, PDC_STAGE_READY=2,
    PDC_STAGE_CONNECTED=3, PDC_STAGE_FILE=4, PDC_STAGE_STOPPING=5,
    PDC_STAGE_STOPPED=6, PDC_STAGE_RECONNECT=7, PDC_STAGE_ERROR=255
};

enum PdcStopReason {
    PDC_STOP_NONE=0, PDC_STOP_USER=1, PDC_STOP_CABLE=2,
    PDC_STOP_ERROR=3, PDC_STOP_MODULE=4
};

/* Status only: never a filesystem authority. Kernel owns every field except
 * request_stop; the USER launcher retains this storage until module unload.
 * Revision 0206 changes the launch magic together with this ABI/size.
 * Legacy ctrl_os_* slots remain unused; no MS OS descriptors are served. */
typedef struct {
    uint32_t magic, size;
    volatile uint32_t request_stop, stage, safe_to_unload;
    volatile int32_t error;
    volatile uint32_t hid_reports, bulk_commands, bytes_read, rx_errors;
    volatile uint32_t ctrl_requests, ctrl_os_string, ctrl_os_compat;
    volatile uint32_t ctrl_os_properties, ctrl_status;
    volatile uint32_t last_setup, last_value_index;
    volatile uint32_t stop_reason, attach_count, detach_count, usb_state, link_generation;
    volatile int32_t detach_arg1, detach_arg2, detach_arg3;
    volatile uint32_t bot_phase, last_scsi_opcode, scsi_host_length, scsi_completed;
    volatile int32_t last_rx_code, last_tx_code;
} PdcSharedStatus;

typedef struct {
    uint32_t magic, size;
    PdcSharedStatus *shared;
} PdcLaunchArgs;

#endif
