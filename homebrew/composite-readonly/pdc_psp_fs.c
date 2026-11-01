/* SPDX-License-Identifier: GPL-3.0-only
 * All filesystem operations are read-only. PSP is sole owner of ms0:.
 * There are deliberately no sceIoWrite/Remove/Rename/Mkdir/Devctl calls.
 */
#include <pspkernel.h>
#include <pspiofilemgr.h>
#include <string.h>
#include "pdc_psp_fs.h"
#include "pdc_text.h"

static int full_path(const char *relative,int directory,char out[PDC_PATH_MAX+6]) {
    if(!pdc_path_valid(relative,directory))return -1;
    memcpy(out,"ms0:/",5);
    memcpy(out+5,relative,strlen(relative)+1);
    return 0;
}
static uint64_t stamp(const SceIoStat *st) {
    /* Fingerprint all mtime fields, not a claimed FAT volume identity. */
    const unsigned char *p=(const unsigned char *)&st->sce_st_mtime;
    uint64_t h=1469598103934665603ULL;
    size_t i;
    for(i=0;i<sizeof(st->sce_st_mtime);i++){h^=p[i];h*=1099511628211ULL;}
    return h;
}
static int open_read(void *ctx,const char *relative,uint64_t *size,uint64_t *time) {
    char path[PDC_PATH_MAX+6];SceIoStat st;int fd;
    (void)ctx;
    if(full_path(relative,0,path)<0)return -1;
    memset(&st,0,sizeof(st));
    if(sceIoGetstat(path,&st)<0 || !FIO_S_ISREG(st.st_mode) || st.st_size<0)return -1;
    fd=sceIoOpen(path,PSP_O_RDONLY,0);
    if(fd<0)return fd;
    *size=(uint64_t)st.st_size;*time=stamp(&st);return fd;
}
static int read_bytes(void *ctx,int fd,uint8_t *buffer,size_t length) {
    (void)ctx;
    if(!buffer || !length || length>PDB2_MAX_PAYLOAD)return -1;
    return sceIoRead(fd,buffer,(unsigned)length);
}
static void close_read(void *ctx,int fd) {(void)ctx;if(fd>=0)sceIoClose(fd);}
static int unchanged(void *ctx,const char *relative,uint64_t size,uint64_t time) {
    char path[PDC_PATH_MAX+6];SceIoStat st;(void)ctx;
    if(full_path(relative,0,path)<0)return 0;
    memset(&st,0,sizeof(st));
    return sceIoGetstat(path,&st)>=0 && FIO_S_ISREG(st.st_mode) &&
           (uint64_t)st.st_size==size && stamp(&st)==time;
}
static int safe_name(const char *name) {
    size_t i,n=0;
    while(n<256 && name[n])n++;
    if(!n || n>255 || name[0]=='.')return 0;
    for(i=0;i<n;i++){
        unsigned char c=(unsigned char)name[i];
        if(c<32 || c>126 || c=='"' || c=='\\' || c=='/' || c==':' ||
           c=='<' || c=='>' || c=='|' || c=='?' || c=='*')return 0;
    }
    return name[n-1]!='.' && name[n-1]!=' ';
}
static int list_json(void *ctx,const char *relative,uint32_t start,uint8_t *out,size_t cap) {
    char path[PDC_PATH_MAX+6];SceIoDirent ent;int fd,result;
    PdcText json;unsigned index=0,count=0;int more=0;(void)ctx;
    if(full_path(relative,1,path)<0 || start>100000 || cap<4096)return -1;
    fd=sceIoDopen(path);if(fd<0)return fd;
    pdc_text_init(&json,out,cap);pdc_text_append(&json,"{\"entries\":[");
    for(;;){
        memset(&ent,0,sizeof(ent));result=sceIoDread(fd,&ent);
        if(result<=0)break;
        if(index++<start)continue;
        if(!safe_name(ent.d_name))continue;
        if(!FIO_S_ISREG(ent.d_stat.st_mode) && !FIO_S_ISDIR(ent.d_stat.st_mode))continue;
        if(count>=64 || cap-json.used<512){more=1;index--;break;}
        if(count)pdc_text_char(&json,',');
        pdc_text_append(&json,"{\"name\":\"");pdc_text_append(&json,ent.d_name);
        pdc_text_append(&json,"\",\"size\":");pdc_text_number(&json,(uint64_t)ent.d_stat.st_size);
        pdc_text_append(&json,",\"directory\":");
        pdc_text_append(&json,FIO_S_ISDIR(ent.d_stat.st_mode)?"true}":"false}");
        if(json.failed){result=-1;break;}
        count++;
    }
    sceIoDclose(fd);if(result<0)return -1;
    pdc_text_append(&json,"],\"next\":");
    if(more)pdc_text_number(&json,index);
    else pdc_text_append(&json,"null");
    pdc_text_char(&json,'}');
    return json.failed ? -1 : (int)json.used;
}
void pdc_psp_readonly_fs(PdcReadOnlyFS *out) {
    memset(out,0,sizeof(*out));out->open_read=open_read;out->read=read_bytes;
    out->unchanged=unchanged;out->close=close_read;out->list_json=list_json;
}
