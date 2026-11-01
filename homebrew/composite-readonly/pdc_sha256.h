/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef PDC_SHA256_H
#define PDC_SHA256_H
#include <stddef.h>
#include <stdint.h>
typedef struct { uint32_t h[8]; uint64_t bytes; uint8_t block[64];size_t used; } PdcSha256;
void pdc_sha256_init(PdcSha256 *ctx);
void pdc_sha256_update(PdcSha256 *ctx, const uint8_t *input, size_t length);
void pdc_sha256_finish(PdcSha256 *ctx, uint8_t digest[32]);
#endif
