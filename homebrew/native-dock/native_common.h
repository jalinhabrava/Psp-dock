/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef PSPDOCK_NATIVE_COMMON_H
#define PSPDOCK_NATIVE_COMMON_H
#include <stdint.h>
#include "native_protocol.h"

#define PDN_DRIVER_NAME "PSPDockNativeHID"
/* Lab identity, not an allocated production Sony VID/PID. */
#define PDN_PRODUCT 0x01cdu
#define PDN_REVISION PDN_FIRMWARE_REVISION
/* PDN7 rejects a mixed launcher/kernel before starting any worker. */
#define PDN_LAUNCH_MAGIC 0x374e4450u
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
    PDN_STOP_ERROR=3, PDN_STOP_MODULE=4, PDN_STOP_HOST=5
};
/* RAM-only diagnostics. These values never authorize a protocol action. */
enum PdnEp0Drop {
    PDN_EP0_DROP_NONE=0, PDN_EP0_DROP_SUBMIT_GATE=1,
    PDN_EP0_DROP_SUBMIT_ERROR=2, PDN_EP0_DROP_RETCODE=3,
    PDN_EP0_DROP_SIZE=4, PDN_EP0_DROP_LINK=5, PDN_EP0_DROP_PARSER=6,
    PDN_EP0_DROP_ENVELOPE=7, PDN_EP0_DROP_FRAGMENT=8, PDN_EP0_ASSEMBLING=9
};
enum PdnEp0Flags {
    PDN_EP0_ACTIVE=1u, PDN_EP0_CONNECTED=2u, PDN_EP0_QUIESCING=4u,
    PDN_EP0_RESET_PENDING=8u, PDN_EP0_SAME_GENERATION=16u,
    PDN_EP0_CTL_BUSY=32u, PDN_EP0_CTL_SUBMITTING=64u,
    PDN_EP0_PROTOCOL_PENDING=128u
};
enum PdnWorkerSite {
    PDN_WORKER_INIT=0, PDN_WORKER_POLL=1, PDN_WORKER_QUIESCE=2,
    PDN_WORKER_APPLY=3, PDN_WORKER_LOCK_WAIT=4, PDN_WORKER_EP1_CANCEL=5,
    PDN_WORKER_EP1_DRAIN=6, PDN_WORKER_COMMIT=7, PDN_WORKER_SLEEP=8,
    PDN_WORKER_FAULT=9, PDN_WORKER_STOP=10
};
typedef struct {
    uint32_t generation,set_seen,recv_submissions,completions,parser_calls;
    uint32_t setup,value_index;
    int32_t submit_result,retcode,recvsize;
    uint32_t request_generation,live_generation,gate_flags,drop_reason;
    int32_t parser_result;
    /* Snapshot of the allocated buffer, not a claim that recvsize bytes
     * arrived. GET_REPORT does not replace this last-SET evidence. */
    uint8_t raw[PDN_FEATURE_SIZE];
} PdnEp0Diagnostic;
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
    volatile PdnEp0Diagnostic ep0;
    volatile uint32_t worker_loops,worker_site,apply_count;
} PdnSharedStatus;
typedef struct {
    uint32_t magic,size;
    PdnSharedStatus *shared;
} PdnLaunchArgs;
#endif
