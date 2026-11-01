/* Deterministic malformed-input stress under ASan/UBSan. No PSP imports. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pdc_service.h"
#include "pdc_os_descriptors.h"

static uint32_t random_state=0x4713ab93u;
static uint32_t next_random(void) {
    random_state^=random_state<<13;random_state^=random_state>>17;
    random_state^=random_state<<5;return random_state;
}
static int file_open(void *c,const char *p,uint64_t *size,uint64_t *stamp) {
    (void)c;(void)p;*size=0;*stamp=42;return 1;
}
static int file_read(void *c,int f,uint8_t *p,size_t n) {
    (void)c;(void)f;(void)p;(void)n;return 0;
}
static int unchanged(void *c,const char *p,uint64_t size,uint64_t stamp) {
    (void)c;(void)p;return !size && stamp==42;
}
static void file_close(void *c,int f) {(void)c;(void)f;}
static int listing(void *c,const char *p,uint32_t o,uint8_t *b,size_t cap) {
    const char *json="{\"entries\":[],\"next\":null}";
    (void)c;(void)p;(void)o;
    if(cap<strlen(json))return -1;
    memcpy(b,json,strlen(json));return (int)strlen(json);
}
int main(void) {
    static uint8_t out[PDC_RESPONSE_CELL],scratch[PDB2_MAX_PAYLOAD];
    uint8_t session[16]={1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
    uint8_t packet[PDC_COMMAND_CELL],payload[256],os[256];
    const char *paths[]={"ISO","PSP/GAME","PSP/SAVEDATA",
        "PSP/GAME/PSPDockCompositeTest/PROBE.DAT","ISO/x.iso",
        "../flash0:/kd/usb.prx","PSP/GAME/test./x.bin","PSP/SAVEDATA/a /x.bin"};
    PdcReadOnlyFS fs={NULL,file_open,file_read,unchanged,file_close,listing};
    PdcService s;Pdb2Frame request,decoded;unsigned i,j;size_t length;int result;
    for(i=0;i<20000;i++) {
        pdc_service_init(&s,session,&fs);
        memset(packet,0,sizeof(packet));memset(&request,0,sizeof(request));
        request.op=PDB2_OP_HELLO;request.seq=1;request.payload=(const uint8_t *)"PDB2-READONLY";
        request.len=13;memcpy(request.session,session,16);
        assert(pdb2_encode(&request,packet,sizeof(packet))>0);
        assert(pdc_service_process(&s,packet,sizeof(packet),out,sizeof(out),scratch,sizeof(scratch))>0);
        memset(packet,0,sizeof(packet));
        if(i%3==0) {
            for(j=0;j<sizeof(packet);j++)packet[j]=(uint8_t)next_random();
            length=next_random()%(sizeof(packet)+1u);
        }else{
            const char *path=paths[next_random()%(sizeof(paths)/sizeof(paths[0]))];
            memset(&request,0,sizeof(request));request.seq=2;
            request.op=(uint8_t)(next_random()%10+1);memcpy(request.session,session,16);
            request.offset=(uint64_t)(next_random()%8);
            if(i%5==0) {
                for(j=0;j<sizeof(payload);j++)payload[j]=(uint8_t)next_random();
                request.payload=payload;request.len=next_random()%sizeof(payload);
            }else {request.payload=(const uint8_t *)path;request.len=strlen(path);}
            length=pdb2_encode(&request,packet,sizeof(packet));
            if(!length)length=sizeof(packet);
            if(i%7==0)packet[next_random()%sizeof(packet)]^=0x80;
            if(i%2==0)length=sizeof(packet);
        }
        result=pdc_service_process(&s,packet,length,out,sizeof(out),scratch,sizeof(scratch));
        if(result>0) {
            assert(result<=(int)PDC_RESPONSE_CELL);
            assert(pdb2_decode(out,(size_t)result,&decoded)==0);
            assert(decoded.len<=PDB2_MAX_PAYLOAD);
        }
        pdc_service_reset(&s);
        /* Exercise truncated/unknown USB control descriptor queries too. */
        (void)pdc_os_control((uint8_t)next_random(),(uint8_t)next_random(),
            (uint16_t)next_random(),(uint16_t)next_random(),(uint16_t)next_random(),
            os,next_random()%(sizeof(os)+1u));
    }
    puts("20,000 deterministic PDC2 malformed-input iterations: OK");
    return 0;
}
