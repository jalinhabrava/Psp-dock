/* SPDX-License-Identifier: GPL-3.0-only
 * Optional USER-only original XMB resource export. No firmware writes.
 * The kernel grants this one pre-USB window; every handle must be closed
 * before USER acknowledges it. Paths and size are fixed, not host input.
 */
#ifndef PSPDOCK_NATIVE_RESOURCE_EXPORT_H
#define PSPDOCK_NATIVE_RESOURCE_EXPORT_H
#include <stddef.h>
#include <stdint.h>

#define PDN_EXPORT_MAX_BYTES 131072u
#define PDN_EXPORT_BUFFER_BYTES 4096u
#define PDN_EXPORT_WAIT_US 10000000u
enum PdnExportResult {
    PDN_EXPORT_UNSAFE=-1, PDN_EXPORT_PENDING=0,
    PDN_EXPORT_SKIPPED=1, PDN_EXPORT_READY=2
};
enum PdnExportRequest {
    PDN_EXPORT_NOT_REQUESTED=0, PDN_EXPORT_REQUESTED=1, PDN_EXPORT_REVOKED=2
};

extern const char pdn_export_source_path[];
extern const char pdn_export_final_path[];
extern const char pdn_export_temp_path[];
extern const uint8_t pdn_export_contract[12];

/* PSP adapters are intentionally separate from the portable copy algorithm.
 * Each path passed to an adapter is one of the constants in this module.
 * No directory traversal, device IOCTL, flash mount, or recursive cleanup.
 */
typedef struct {
    void *context;
    int (*open_read)(void *,const char *);
    int (*create_new)(void *,const char *);
    int (*length)(void *,int);
    int (*rewind)(void *,int);
    int (*read)(void *,int,void *,unsigned);
    int (*write)(void *,int,const void *,unsigned);
    int (*close)(void *,int);
    int (*directory)(void *,const char *);
    int (*publish)(void *,const char *,const char *);
    int (*remove_created)(void *,const char *);
    int (*sync)(void *);
    int (*cancelled)(void *);
} PdnExportIO;

int pdn_export_resource(const PdnExportIO *io);
struct PdnSharedStatus;
int pdn_export_user(struct PdnSharedStatus *state);
#endif
