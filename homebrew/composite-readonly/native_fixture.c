/* SPDX-License-Identifier: GPL-3.0-only
 * Native PC test adapter. NOT linked into the PSP PRX.
 * Runs the exact firmware parser/service against synthetic bytes only.
 */
#include "pdc_service.h"
#include <stdlib.h>
#include <string.h>

typedef struct {
    PdcService service;
    unsigned position,commands;
    int changed;
    uint8_t session[16],bytes[150003],scratch[PDB2_MAX_PAYLOAD];
} NativeFixture;

static int f_open(void *v,const char *path,uint64_t *size,uint64_t *stamp) {
    NativeFixture *f=v;
    if(strcmp(path,"PSP/GAME/PSPDockCompositeTest/PROBE.DAT"))return -1;
    f->position=0;*size=sizeof(f->bytes);*stamp=42;return 1;
}
static int f_read(void *v,int handle,uint8_t *out,size_t size) {
    NativeFixture *f=v;if(handle!=1)return -1;
    if(size>sizeof(f->bytes)-f->position)size=sizeof(f->bytes)-f->position;
    memcpy(out,f->bytes+f->position,size);f->position+=(unsigned)size;return (int)size;
}
static int f_unchanged(void *v,const char *path,uint64_t size,uint64_t stamp) {
    NativeFixture *f=v;
    return !f->changed && !strcmp(path,"PSP/GAME/PSPDockCompositeTest/PROBE.DAT") &&
           size==sizeof(f->bytes) && stamp==42;
}
static void f_close(void *v,int handle) {(void)v;(void)handle;}
static int f_list(void *v,const char *path,uint32_t index,uint8_t *out,size_t cap) {
    static const char row[]="{\"entries\":[{\"name\":\"PROBE.DAT\",\"size\":150003,\"directory\":false}],\"next\":null}";
    (void)v;
    if(strcmp(path,"PSP/GAME/PSPDockCompositeTest") || index || cap<sizeof(row))return -1;
    memcpy(out,row,sizeof(row)-1);return sizeof(row)-1;
}
void *pdc_fixture_create(void) {
    unsigned i;PdcReadOnlyFS fs;
    NativeFixture *f=calloc(1,sizeof(*f));if(!f)return NULL;
    for(i=0;i<16;i++)f->session[i]=(uint8_t)(i+1);
    for(i=0;i<sizeof(f->bytes);i++)f->bytes[i]=(uint8_t)(i*17+3);
    memset(&fs,0,sizeof(fs));fs.context=f;fs.open_read=f_open;fs.read=f_read;
    fs.unchanged=f_unchanged;fs.close=f_close;fs.list_json=f_list;
    pdc_service_init(&f->service,f->session,&fs);return f;
}
void pdc_fixture_free(void *v) {
    NativeFixture *f=v;if(!f)return;
    pdc_service_reset(&f->service);memset(f,0,sizeof(*f));free(f);
}
int pdc_fixture_exchange(void *v,const uint8_t *in,size_t length,uint8_t *out,size_t cap) {
    NativeFixture *f=v;int n;
    if(!f)return -1;
    n=pdc_service_process(&f->service,in,length,out,cap,f->scratch,sizeof(f->scratch));
    if(n>0)f->commands++;
    return n;
}
unsigned pdc_fixture_commands(void *v) {NativeFixture *f=v;return f?f->commands:0;}
void pdc_fixture_changed(void *v) {NativeFixture *f=v;if(f)f->changed=1;}
