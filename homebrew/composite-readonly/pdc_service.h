/* SPDX-License-Identifier: GPL-3.0-only
 * Read-only server for the user's PSP game library. There are no file
 * mutation methods in this interface. It cannot write saves or firmware.
 */
#ifndef PDC_SERVICE_H
#define PDC_SERVICE_H
#include "pdc_common.h"
#include "pdc_sha256.h"
#include "pdb2_wire.h"
typedef struct {
    void *context;
    int (*open_read)(void *,const char *,uint64_t *,uint64_t *);
    int (*read)(void *,int,uint8_t *,size_t);
    int (*unchanged)(void *,const char *,uint64_t,uint64_t);
    void (*close)(void *,int);
    int (*list_json)(void *,const char *,uint32_t,uint8_t *,size_t);
} PdcReadOnlyFS;
typedef struct {
    uint8_t session[16];
    uint32_t next_seq;
    int hello, file;
    uint64_t size, position, stamp;
    char path[PDC_PATH_MAX+1];
    PdcSha256 hash;
    PdcReadOnlyFS fs;
} PdcService;
void pdc_service_init(PdcService *,const uint8_t session[16],const PdcReadOnlyFS *);
void pdc_service_reset(PdcService *);
int pdc_path_valid(const char *,int directory);
int pdc_service_process(PdcService *,const uint8_t *,size_t,
                       uint8_t *,size_t,uint8_t *,size_t);
#endif
