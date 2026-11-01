/* PSP Dock PDB2 bulk framing experiment: host-testable PURE C code.
 *
 * NO PSP modules/USB drivers/kernel imports in this directory.
 * This format is designed for a future vendor-specific bulk interface
 * coexisting with HID. The PSP must remain the sole FAT owner.
 * Integrity: CRC32 detects accidental corruption, NOT authentication.
 */
#ifndef PSPDOCK_PDB2_WIRE_H
#define PSPDOCK_PDB2_WIRE_H

#include <stddef.h>
#include <stdint.h>

#define PDB2_HEADER_SIZE 40u
#define PDB2_CRC_SIZE 4u
#define PDB2_MAX_PAYLOAD (60u * 1024u)
#define PDB2_MAX_FRAME (PDB2_HEADER_SIZE + PDB2_CRC_SIZE + PDB2_MAX_PAYLOAD)

enum Pdb2Op {
    PDB2_OP_HELLO = 1,
    PDB2_OP_HELLO_ACK = 2,
    PDB2_OP_STAT = 3,
    PDB2_OP_META = 4,
    PDB2_OP_READ = 5,
    PDB2_OP_DATA = 6,
    PDB2_OP_EOF = 7,
    PDB2_OP_LIST = 8,
    PDB2_OP_LIST_DATA = 9,
    PDB2_OP_ERROR = 255
};

typedef struct {
    uint8_t op;
    uint8_t session[16];
    uint32_t seq;
    uint64_t offset;
    uint32_t len;
    const uint8_t *payload;
} Pdb2Frame;

/* Returned count includes wire header + CRC. Zero means rejection. */
size_t pdb2_encode(const Pdb2Frame *frame, uint8_t *out, size_t cap);

/* Decoded payload points INSIDE input. Caller must retain input until done. */
int pdb2_decode(const uint8_t *input, size_t len, Pdb2Frame *out);

/* Host-only cross-language test helper; CRC32 IEEE 802.3 reflected. */
uint32_t pdb2_crc32(const uint8_t *data, size_t len);

#endif
