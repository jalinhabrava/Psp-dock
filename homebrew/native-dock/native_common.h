/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef PSPDOCK_NATIVE_COMMON_H
#define PSPDOCK_NATIVE_COMMON_H
#include <stdint.h>
#include "native_protocol.h"

#define PDN_DRIVER_NAME "PSPDockNativeHID"
/* Lab identity, not an allocated production Sony VID/PID. */
#define PDN_PRODUCT 0x01cdu
#define PDN_REVISION PDN_FIRMWARE_REVISION
/* PDN3 rejects a mixed launcher/kernel before starting any worker. */
#define PDN_LAUNCH_MAGIC 0x334e4450u
#define PDN_DEPENDENCIES 4u

enum PdnStage {
    PDN_STAGE_INIT=0, PDN_STAGE_GUARD=1, PDN_STAGE_MODULE_LOAD=2,
    PDN_STAGE_MODULE_START=3, PDN_STAGE_BUS_START=4, PDN_STAGE_HID_REGISTER=5,
    PDN_STAGE_HID_START=6, PDN_STAGE_STORAGE_START=7, PDN_STAGE_ACTIVATE=8,
    PDN_STAGE_READY=9, PDN_STAGE_CONNECTED=10, PDN_STAGE_RECONNECT=11,
    PDN_STAGE_WAIT_UNPLUG=12, PDN_STAGE_STOPPING=13, PDN_STAGE_STOPPED=14,
    PDN_STAGE_ERROR=255
};
enum PdnStopReason {
    PDN_STOP_NONE=0, PDN_STOP_USER=1, PDN_STOP_CABLE=2,
    PDN_STOP_ERROR=3, PDN_STOP_MODULE=4
};
/* Kernel owns every field except request_stop. USER keeps this allocation
 * until safe_to_unload and successful StopModule/UnloadModule. */
typedef struct {
    uint32_t magic,size;
    volatile uint32_t request_stop,stage,safe_to_unload;
    volatile int32_t error,cleanup_error;
    volatile uint32_t error_stage,dependency,modules_loaded,modules_borrowed;
    volatile uint32_t phase,sequence;
    volatile int32_t phase_result;
    volatile uint32_t hid_reports,ctrl_requests,rejected_commands;
    volatile int32_t last_rejection;
    volatile uint32_t last_setup,last_value_index,hid_interface;
    volatile uint32_t stop_reason,attach_count,detach_count,usb_state,link_generation;
    volatile int32_t bus_before,storage_before,storage_after;
    volatile uint32_t sony_storage_started,exit_requested;
    volatile uint32_t presentation_generation;
    volatile uint32_t host_confirmed,host_session_seen,activity,progress_unit;
    volatile uint32_t diagnostic_visible;
    volatile uint64_t completed,total;
} PdnSharedStatus;
typedef struct {
    uint32_t magic,size;
    PdnSharedStatus *shared;
} PdnLaunchArgs;
#endif
