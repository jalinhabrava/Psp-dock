/* SPDX-License-Identifier: GPL-3.0-only
 * Read-only PSP file service, shared with the native PC tests.
 */
#include "pdc_service.h"
#include "pdc_text.h"
#include <string.h>

static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1]<<8) |
           ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
}
static void close_file(PdcService *s) {
    if(s->file>=0 && s->fs.close) s->fs.close(s->fs.context,s->file);
    s->file=-1; s->position=0; s->size=0; s->stamp=0; s->path[0]=0;
}
void pdc_service_init(PdcService *s,const uint8_t session[16],const PdcReadOnlyFS *fs) {
    memset(s,0,sizeof(*s)); memcpy(s->session,session,16);
    s->fs=*fs; s->next_seq=1; s->file=-1;
}
void pdc_service_reset(PdcService *s) {
    close_file(s); s->hello=0; s->next_seq=1;
}
int pdc_path_valid(const char *p,int directory) {
    size_t n,i,component=0,parts=1;
    if(!p) return 0;
    for(n=0;n<=PDC_PATH_MAX && p[n];n++) {}
    if(n==0 || n>PDC_PATH_MAX || p[0]=='/' || p[n-1]=='/') return 0;
    for(i=0;i<n;i++) {
        unsigned char c=(unsigned char)p[i];
        if(c<32 || c>126 || c==':' || c=='\\' || c=='"' ||
           c=='<' || c=='>' || c=='|' || c=='?' || c=='*') return 0;
        if(c=='/') {
            if(!component || p[i-1]==' ' || p[i-1]=='.') return 0;
            parts++; component=0;
        } else {
            if(!component && c=='.') return 0;
            component++;
        }
    }
    if(parts>12 || !component || p[n-1]==' ' || p[n-1]=='.') return 0;
    if(directory && (!strcmp(p,"ISO") || !strcmp(p,"PSP/GAME") ||
                     !strcmp(p,"PSP/SAVEDATA"))) return 1;
    if(!strncmp(p,"ISO/",4)) return parts==2 && !directory;
    if(!strncmp(p,"PSP/GAME/",9) || !strncmp(p,"PSP/SAVEDATA/",13))
        return directory ? parts>=3 : parts>=4;
    return 0;
}
static int path_copy(char out[PDC_PATH_MAX+1],const uint8_t *raw,size_t size,int dir) {
    if(!size || size>PDC_PATH_MAX || memchr(raw,0,size)) return 0;
    memcpy(out,raw,size); out[size]=0;
    return pdc_path_valid(out,dir);
}
static int respond(PdcService *s,const Pdb2Frame *r,uint8_t op,
                   const uint8_t *payload,size_t size,uint8_t *out,size_t cap) {
    Pdb2Frame f;
    memset(&f,0,sizeof(f)); f.op=op; f.seq=r->seq; f.offset=r->offset;
    f.len=(uint32_t)size; f.payload=payload; memcpy(f.session,s->session,16);
    return (int)pdb2_encode(&f,out,cap);
}
static int reject(PdcService *s,const Pdb2Frame *r,const char *why,uint8_t *out,size_t cap) {
    close_file(s);
    return respond(s,r,PDB2_OP_ERROR,(const uint8_t *)why,strlen(why),out,cap);
}

int pdc_service_process(PdcService *s,const uint8_t *cell,size_t cell_bytes,
                         uint8_t *out,size_t cap,uint8_t *scratch,size_t scratch_cap) {
    Pdb2Frame r;
    uint32_t payload;
    size_t frame_bytes,i;
    char name[PDC_PATH_MAX+1];
    int n;
    if(!s || !cell || cell_bytes!=PDC_COMMAND_CELL || !out || cap<PDC_RESPONSE_CELL ||
       !scratch || scratch_cap<PDB2_MAX_PAYLOAD) return -1;
    payload=get32(cell+36);
    frame_bytes=PDB2_HEADER_SIZE+PDB2_CRC_SIZE+(size_t)payload;
    if(frame_bytes>PDC_COMMAND_CELL || pdb2_decode(cell,frame_bytes,&r)!=0) return -1;
    for(i=frame_bytes;i<PDC_COMMAND_CELL;i++) if(cell[i]) return -1;
    if(memcmp(r.session,s->session,16) || r.seq!=s->next_seq ||
       s->next_seq==0xffffffffu) return -1;
    memset(out,0,PDC_RESPONSE_CELL);
    if(!s->hello) {
        static const char hello[]="PDB2-READONLY";
        static const char ack[]="{\"protocol\":\"PDB2-stream-v1\",\"read_only\":true,\"max_chunk\":61440,\"volume_serial\":null,\"capabilities\":7}";
        if(r.op!=PDB2_OP_HELLO || r.offset || r.len!=sizeof(hello)-1 ||
           memcmp(r.payload,hello,sizeof(hello)-1)) return -1;
        s->hello=1; s->next_seq++;
        return respond(s,&r,PDB2_OP_HELLO_ACK,(const uint8_t *)ack,sizeof(ack)-1,out,cap);
    }
    s->next_seq++;
    if(r.op==PDB2_OP_STAT) {
        PdcText json;
        close_file(s);
        if(r.offset || !path_copy(name,r.payload,r.len,0) || !s->fs.open_read)
            return reject(s,&r,"path_not_allowed",out,cap);
        s->file=s->fs.open_read(s->fs.context,name,&s->size,&s->stamp);
        if(s->file<0 || s->size>0xffffffffULL)
            return reject(s,&r,"open_failed",out,cap);
        memcpy(s->path,name,strlen(name)+1); pdc_sha256_init(&s->hash);
        pdc_text_init(&json,scratch,scratch_cap);
        pdc_text_append(&json,"{\"size\":");pdc_text_number(&json,s->size);
        pdc_text_append(&json,",\"hash_at_eof\":true,\"read_only\":true}");
        if(json.failed)return reject(s,&r,"metadata_overflow",out,cap);
        return respond(s,&r,PDB2_OP_META,scratch,json.used,out,cap);
    }
    if(r.op==PDB2_OP_READ) {
        uint32_t count;
        uint64_t remaining;
        if(s->file<0 || r.len<=4 || !path_copy(name,r.payload+4,r.len-4,0) ||
           strcmp(name,s->path) || r.offset!=s->position)
            return reject(s,&r,"stream_or_offset_mismatch",out,cap);
        count=get32(r.payload);
        if(!count || count>PDB2_MAX_PAYLOAD) return reject(s,&r,"invalid_chunk",out,cap);
        if(s->position==s->size) {
            if(!s->fs.unchanged ||
               s->fs.unchanged(s->fs.context,s->path,s->size,s->stamp)!=1)
                return reject(s,&r,"source_changed",out,cap);
            pdc_sha256_finish(&s->hash,scratch); close_file(s);
            return respond(s,&r,PDB2_OP_EOF,scratch,32,out,cap);
        }
        remaining=s->size-s->position;
        if(count>remaining) count=(uint32_t)remaining;
        n=s->fs.read ? s->fs.read(s->fs.context,s->file,scratch,count) : -1;
        if(n<=0 || (uint32_t)n>count) return reject(s,&r,"read_failed",out,cap);
        pdc_sha256_update(&s->hash,scratch,(size_t)n); s->position+=(unsigned)n;
        return respond(s,&r,PDB2_OP_DATA,scratch,(size_t)n,out,cap);
    }
    if(r.op==PDB2_OP_LIST) {
        close_file(s);
        if(r.offset>100000 || !path_copy(name,r.payload,r.len,1) || !s->fs.list_json)
            return reject(s,&r,"directory_not_allowed",out,cap);
        n=s->fs.list_json(s->fs.context,name,(uint32_t)r.offset,scratch,scratch_cap);
        if(n<0 || (size_t)n>=scratch_cap) return reject(s,&r,"directory_read_failed",out,cap);
        return respond(s,&r,PDB2_OP_LIST_DATA,scratch,(size_t)n,out,cap);
    }
    return reject(s,&r,"operation_not_readonly",out,cap);
}
