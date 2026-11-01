/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef PSP_DOCK_BRIDGE_HID_H
#define PSP_DOCK_BRIDGE_HID_H
#include <stdint.h>

#define BRIDGE_HID_DRIVER "PSPDockHID"
#define BRIDGE_HID_PID 0x01cb
#define BRIDGE_FEATURE_SIZE 32
#define BRIDGE_SESSION_SIZE 16
#define BRIDGE_PROTOCOL_VERSION 1
#define BRIDGE_OP_STATUS 1
#define BRIDGE_OP_REQUEST_STORAGE 2
#define BRIDGE_MODE_HID 2
#define BRIDGE_FLAG_CONNECTED 1
#define BRIDGE_FLAG_PENDING 2
#define BRIDGE_FLAG_CONSUMED 4
#define BRIDGE_RESULT_IDLE 0
#define BRIDGE_RESULT_QUEUED 1
#define BRIDGE_RESULT_CONSUMED 2
#define BRIDGE_ERROR_FRAME -1
#define BRIDGE_ERROR_SESSION -2
#define BRIDGE_ERROR_BUSY -3

/* Kernel callers only. Caller serializes lifecycle calls and owns USBBusDriver.
 * No driver starts at PRX load. Stop USBSTOR before Start; do not access FAT
 * until the storage owner has explicitly released it. Session must be fresh,
 * nonzero for EVERY activation. Negative results are errors, never success.
 */
int bridgeHidStart(const uint8_t session[BRIDGE_SESSION_SIZE]);
int bridgeHidStop(void);
/* 1: consumed once, 0: none; never switches drivers. Call from app thread. */
int bridgeHidPollStorageRequest(uint32_t *sequence);
/* Copies the 32-byte wire status, without Windows' leading report-ID byte. */
int bridgeHidGetStatus(uint8_t status[BRIDGE_FEATURE_SIZE]);
#endif
