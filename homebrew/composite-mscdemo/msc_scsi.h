/* SPDX-License-Identifier: GPL-3.0-only
 * Original, portable USB MSC BOT/SCSI engine for a SYNTHETIC read-only disk.
 * It has no file, block-device, USB, allocation, or Memory Stick API calls.
 */
#ifndef PSPDOCK_MSC_SCSI_H
#define PSPDOCK_MSC_SCSI_H
#include <stddef.h>
#include <stdint.h>

#define MSC_BLOCK_SIZE 512u
#define MSC_BLOCK_COUNT 1024u
#define MSC_DATA_LBA 9u
#define MSC_PROBE_BYTES 150003u
#define MSC_PROBE_FIRST_CLUSTER 2u
#define MSC_PROBE_LAST_CLUSTER 294u
#define MSC_SESSION_CLUSTER 295u
#define MSC_VOLUME_SERIAL UINT32_C(0x50444334)
#define MSC_CBW_SIZE 31u
#define MSC_CSW_SIZE 13u
#define MSC_PROBE_SHA256 "e2d2ed8fba5bff4fc1e3e2cf9fd4508a6e3fd8f2d8429d44196c6b7ec8ba001e"

enum MscDirection { MSC_DIR_NONE=0, MSC_DIR_IN=1, MSC_DIR_OUT=2 };
enum MscStatus { MSC_STATUS_PASSED=0, MSC_STATUS_FAILED=1, MSC_STATUS_PHASE_ERROR=2 };

typedef struct {
    uint32_t host_length;
    uint32_t device_length;
    uint32_t transfer_length;
    uint8_t direction;
    uint8_t status;
    uint8_t case_id; /* USB MSC BOT 1.0, table 6.1 (1..13). */
    uint8_t valid_cbw;
    /* Apply these stalls AFTER transfer_length data bytes, BEFORE the CSW.
     * The USB wrapper must handle the host's CLEAR_FEATURE(ENDPOINT_HALT).
     * For invalid CBW, both are set and NO CSW may be returned. */
    uint8_t stall_in;
    uint8_t stall_out;
    uint8_t reset_required;
} MscPlan;

typedef struct {
    MscPlan plan;
    uint32_t tag;
    uint32_t completed; /* Actual successfully transferred data bytes. */
    uint32_t first_lba;
    uint32_t response_length;
    uint8_t response[64];
    uint8_t session[16];
    uint8_t sense_key, sense_asc, sense_ascq;
    uint8_t source; /* Private: 0 no data, 1 response, 2 generated sectors. */
    uint8_t command_active;
    uint8_t reset_locked;
    uint8_t medium_present;
    uint8_t removal_prevented;
} MscBot;

void msc_init(MscBot *bot, const uint8_t session[16]);
/* Call after a class-specific Bulk-Only Reset, not merely clearing HALT.
 * The USB wrapper cancels/drains transfers but PRESERVES endpoint HALTs and
 * data toggles until the host's separate CLEAR_FEATURE requests. It must
 * wait for both IN and OUT reset-recovery clears before accepting a CBW.
 * Session/media identity are retained; no file operations are performed. */
void msc_reset(MscBot *bot);
/* General BOT matrix helper, also useful for native matrix tests. */
MscPlan msc_plan_transfer(uint32_t host_length, enum MscDirection host_direction,
                          uint32_t device_length, enum MscDirection device_direction,
                          enum MscStatus command_status);
/* Returns 0 for an accepted CBW (inspect plan.status even on success),
 * -1 invalid/nonmeaningful wrapper, -2 reset recovery required/in progress.
 * CBW buffer must contain exactly one 31-byte USB transfer. */
int msc_begin(MscBot *bot, const uint8_t *cbw, size_t size);
/* Generate up to cap bytes of this command's IN data at offset. Does not
 * count data as sent: call msc_data_complete after USB completion. */
size_t msc_read_data(const MscBot *bot, uint32_t offset, uint8_t *out, size_t cap);
/* Returns 0 for an exact in-range completion, -1 for overrun/state error. */
int msc_data_complete(MscBot *bot, uint32_t bytes);
/* Serialize the 13-byte CSW only after the entire planned data phase.
 * Returns 13 on success or -1. Phase errors keep CBW intake locked until
 * msc_reset. The caller must finish sending this CSW before another CBW. */
int msc_csw(MscBot *bot, uint8_t out[MSC_CSW_SIZE]);
/* Generate any synthetic volume sector; returns 0 or -1 for out-of-range.
 * Sector contents are independent of command sequencing. */
int msc_read_sector(const MscBot *bot, uint32_t lba, uint8_t out[MSC_BLOCK_SIZE]);

#endif
