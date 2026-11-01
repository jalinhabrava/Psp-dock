/* SPDX-License-Identifier: GPL-3.0-only
 * Tests run on the development PC only. All PSP filesystem calls are mocked.
 */
#include "pdc_service.h"
#include "pdc_os_descriptors.h"
#include "pdc_text.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t command[PDC_COMMAND_CELL],answer[PDC_RESPONSE_CELL],scratch[PDB2_MAX_PAYLOAD];
static uint8_t game[150003],nonce[16]={1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
static unsigned position,open_count,close_count;static int changed;
static int fake_open(void *p,const char *path,uint64_t *size,uint64_t *stamp){
    (void)p;if(strcmp(path,"ISO/Example.iso"))return -1;
    position=0;*size=sizeof(game);*stamp=42;open_count++;return 123;
}
static int fake_read(void *p,int fd,uint8_t *out,size_t n){
    (void)p;assert(fd==123);if(n>sizeof(game)-position)n=sizeof(game)-position;
    memcpy(out,game+position,n);position+=(unsigned)n;return (int)n;
}
static int fake_unchanged(void *p,const char *path,uint64_t size,uint64_t stamp){
    (void)p;return !changed && !strcmp(path,"ISO/Example.iso") && size==sizeof(game) && stamp==42;
}
static void fake_close(void *p,int fd){(void)p;assert(fd==123);close_count++;}
static int fake_list(void *p,const char *path,uint32_t start,uint8_t *out,size_t cap){
    static const char body[]="{\"entries\":[{\"name\":\"Example.iso\",\"size\":150003,\"directory\":false}],\"next\":null}";
    (void)p;if(strcmp(path,"ISO") || start!=0 || cap<sizeof(body))return -1;
    memcpy(out,body,sizeof(body)-1);return sizeof(body)-1;
}
static PdcReadOnlyFS fake_fs={NULL,fake_open,fake_read,fake_unchanged,fake_close,fake_list};
static int exchange(PdcService *s,unsigned op,unsigned seq,const uint8_t *data,size_t n,uint64_t offset,Pdb2Frame *out){
    Pdb2Frame req;int bytes;
    memset(&req,0,sizeof(req));memcpy(req.session,nonce,16);
    req.op=(uint8_t)op;req.seq=seq;req.payload=data;req.len=(uint32_t)n;req.offset=offset;
    memset(command,0,sizeof(command));
    assert(pdb2_encode(&req,command,sizeof(command))>0);
    bytes=pdc_service_process(s,command,sizeof(command),answer,sizeof(answer),scratch,sizeof(scratch));
    if(bytes>0){assert(pdb2_decode(answer,(size_t)bytes,out)==0);}
    return bytes;
}
static void digest_hex(const uint8_t *bytes,char *text){
    unsigned i;for(i=0;i<32;i++)sprintf(text+i*2,"%02x",bytes[i]);
}
static void test_sha(void){
    PdcSha256 h;uint8_t digest[32];char text[65];unsigned i;
    pdc_sha256_init(&h);pdc_sha256_finish(&h,digest);digest_hex(digest,text);
    assert(!strcmp(text,"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
    pdc_sha256_init(&h);pdc_sha256_update(&h,(const uint8_t *)"abc",3);pdc_sha256_finish(&h,digest);digest_hex(digest,text);
    assert(!strcmp(text,"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
    pdc_sha256_init(&h);for(i=0;i<1000000;i++)pdc_sha256_update(&h,(const uint8_t *)"a",1);
    pdc_sha256_finish(&h,digest);digest_hex(digest,text);
    assert(!strcmp(text,"cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"));
}
static void test_descriptors(void){
    uint8_t out[256];size_t n;
    n=pdc_os_control(0x80,6,0x03ee,0,18,out,sizeof(out));assert(n==18 && out[16]==0x22);
    n=pdc_os_control(0xc0,0x22,0,4,65535,out,sizeof(out));
    assert(n==40 && out[16]==1 && !memcmp(out+18,"WINUSB",6));
    assert(pdc_os_control(0xc0,0x22,0,4,16,out,sizeof(out))==16);
    n=pdc_os_control(0xc1,0x22,1,5,65535,out,sizeof(out));assert(n==146 && out[14]==7);
    assert(!pdc_os_control(0xc1,0x22,0,5,65535,out,sizeof(out)));
    assert(!pdc_os_control(0x40,0x22,0,4,65535,out,sizeof(out)));
}
static void test_service(void){
    PdcService s;Pdb2Frame reply;PdcSha256 h;uint8_t want[32],readreq[100];
    const char *name="ISO/Example.iso";unsigned seq=1,copied=0,i;
    for(i=0;i<sizeof(game);i++)game[i]=(uint8_t)(i*17+3);
    pdc_sha256_init(&h);pdc_sha256_update(&h,game,sizeof(game));pdc_sha256_finish(&h,want);
    pdc_service_init(&s,nonce,&fake_fs);
    assert(exchange(&s,PDB2_OP_HELLO,seq++,(const uint8_t *)"PDB2-READONLY",13,0,&reply)>0);
    assert(reply.op==PDB2_OP_HELLO_ACK && s.hello);
    assert(exchange(&s,PDB2_OP_LIST,seq++,(const uint8_t *)"ISO",3,0,&reply)>0);
    assert(reply.op==PDB2_OP_LIST_DATA);
    assert(exchange(&s,PDB2_OP_STAT,seq++,(const uint8_t *)name,strlen(name),0,&reply)>0);
    assert(reply.op==PDB2_OP_META && open_count==1);
    readreq[0]=0x00;readreq[1]=0xf0;readreq[2]=0;readreq[3]=0;
    memcpy(readreq+4,name,strlen(name));
    while(copied<sizeof(game)){
        assert(exchange(&s,PDB2_OP_READ,seq++,readreq,strlen(name)+4,copied,&reply)>0);
        assert(reply.op==PDB2_OP_DATA && reply.len>0);
        assert(!memcmp(reply.payload,game+copied,reply.len));copied+=reply.len;
    }
    assert(exchange(&s,PDB2_OP_READ,seq++,readreq,strlen(name)+4,copied,&reply)>0);
    assert(reply.op==PDB2_OP_EOF && reply.len==32 && !memcmp(reply.payload,want,32));
    assert(close_count==1);
    assert(exchange(&s,PDB2_OP_LIST,seq-1,(const uint8_t *)"ISO",3,0,&reply)<0);
    assert(exchange(&s,PDB2_OP_STAT,seq++,(const uint8_t *)"flash0:/test",12,0,&reply)>0);
    assert(reply.op==PDB2_OP_ERROR && open_count==1);
    assert(!pdc_path_valid("PSP/SAVEDATA/../SYSTEM/a",0));
    assert(!pdc_path_valid("ISO/x/../a.iso",0));
    assert(!pdc_path_valid("PSP/SYSTEM/controls.ini",0));
    assert(pdc_path_valid("PSP/GAME/PSPDockCompositeTest/PROBE.DAT",0));
    pdc_service_reset(&s);assert(s.file<0 && !s.hello);
    /* CRC damage and zero padding abuse never invoke a filesystem callback. */
    assert(exchange(&s,PDB2_OP_HELLO,1,(const uint8_t *)"PDB2-READONLY",13,0,&reply)>0);
    command[511]=1;
    assert(pdc_service_process(&s,command,sizeof(command),answer,sizeof(answer),scratch,sizeof(scratch))<0);
}
int main(void){
    {
        char buf[24];PdcText t;
        pdc_text_init(&t,buf,sizeof(buf));pdc_text_number(&t,UINT64_MAX);
        assert(!t.failed && !strcmp(buf,"18446744073709551615"));
        pdc_text_init(&t,buf,4);pdc_text_append(&t,"12345");
        assert(t.failed && t.used==3 && !strcmp(buf,"123"));
        pdc_text_init(&t,NULL,0);pdc_text_number(&t,0);assert(t.failed);
    }
    test_sha();test_descriptors();test_service();
    puts("PDC2 native tests OK: SHA256, OS descriptors, read-only stream, EOF hash, replay and paths.");
    return 0;
}
