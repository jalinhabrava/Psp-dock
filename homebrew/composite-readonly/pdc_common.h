/* SPDX-License-Identifier: GPL-3.0-only
 * PSP Dock composite read-only controller, experimental (not installed).
 * Independent from the stable PSP/GAME/PSPDock client.
 */
#ifndef PDC_COMMON_H
#define PDC_COMMON_H
#include <stdint.h>
#include <stddef.h>

#define PDC_DRIVER_NAME "PSPDockCompositeRO"
#define PDC_TEST_PRODUCT 0x01cb
#define PDC_REVISION 0x0204
#define PDC_USB_INTERFACE 1
#define PDC_HID_IN 0x81
#define PDC_BULK_IN 0x82
#define PDC_BULK_OUT 0x03
#define PDC_COMMAND_CELL 512u
#define PDC_RESPONSE_CELL 65536u
#define PDC_STATUS_REQUEST 0x53u
#define PDC_OS_VENDOR_REQUEST 0x22u
#define PDC_LAUNCH_MAGIC 0x32434450u
#define PDC_CAP_READ 1u
#define PDC_CAP_LIST 2u
#define PDC_CAP_SHA256_STREAM 4u
#define PDC_CAPABILITIES 7u
#define PDC_PATH_MAX 256u

enum PdcStage {
    PDC_STAGE_INIT=0, PDC_STAGE_GUARD=1, PDC_STAGE_READY=2,
    PDC_STAGE_CONNECTED=3, PDC_STAGE_FILE=4, PDC_STAGE_STOPPING=5,
    PDC_STAGE_STOPPED=6, PDC_STAGE_ERROR=255
};

/* Diagnostic shared user memory; NEVER an authority for firmware or FAT.
 * Kernel owns all fields except request_stop, user keeps it alive until
 * safe_to_unload. No timer is used to assume USB cleanup completed. */
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
