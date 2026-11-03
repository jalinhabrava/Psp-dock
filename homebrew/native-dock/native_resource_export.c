/* SPDX-License-Identifier: GPL-3.0-only */
#include "native_resource_export.h"
#include <string.h>

/* Audited in the compiled USER ELF; no external paths are accepted. */
const char pdn_export_source_path[]="flash0:/vsh/resource/system_plugin.rco";
const char pdn_export_final_path[]="ms0:/PSP/SYSTEM/PSPDock/xmb/system_plugin.rco";
const char pdn_export_temp_path[]="ms0:/PSP/SYSTEM/PSPDock/xmb/.system_plugin.rco.pspdock-tmp";
const uint8_t pdn_export_contract[12] __attribute__((used))={
    'P','D','E','X',1,1,1,1, /* version, optional, read-only source, closed/synced gate */
    0,0,2,0 /* max 128 KiB, little endian */
};

static int export_read(const PdnExportIO *io,int fd,void *data,unsigned size) {
    unsigned done=0;int got;
    while(done<size){
        if(io->cancelled(io->context))return -1;
        got=io->read(io->context,fd,(uint8_t *)data+done,size-done);
        if(got<=0 || (unsigned)got>size-done)return -1;
        done+=(unsigned)got;
    }
    return 0;
}
static int export_equal(const PdnExportIO *io,int first,int second,unsigned size) {
    uint8_t a[PDN_EXPORT_BUFFER_BYTES],b[PDN_EXPORT_BUFFER_BYTES];unsigned offset=0,n;
    if(io->length(io->context,second)!=(int)size || io->rewind(io->context,first)<0 ||
       io->rewind(io->context,second)<0)return 0;
    while(offset<size){
        n=size-offset;if(n>sizeof(a))n=sizeof(a);
        if(export_read(io,first,a,n)<0 || export_read(io,second,b,n)<0 || memcmp(a,b,n))return 0;
        offset+=n;
    }
    return 1;
}
static void export_close(const PdnExportIO *io,int *fd,int *unsafe) {
    if(*fd>=0){if(io->close(io->context,*fd)<0)*unsafe=1;*fd=-1;}
}

int pdn_export_resource(const PdnExportIO *io) {
    int source=-1,other=-1,temporary=-1,size,got,written;
    int result=PDN_EXPORT_SKIPPED,unsafe=0,created=0,dirty=0;
    unsigned offset=0,n,done;uint8_t buffer[PDN_EXPORT_BUFFER_BYTES];
    static const char *const directories[]={
        "ms0:/PSP/SYSTEM","ms0:/PSP/SYSTEM/PSPDock","ms0:/PSP/SYSTEM/PSPDock/xmb"
    };
    unsigned i;
    if(io->cancelled(io->context))return PDN_EXPORT_SKIPPED;
    source=io->open_read(io->context,pdn_export_source_path);
    if(source<0)return PDN_EXPORT_SKIPPED;
    size=io->length(io->context,source);
    if(size<4 || (unsigned)size>PDN_EXPORT_MAX_BYTES || io->rewind(io->context,source)<0 ||
       export_read(io,source,buffer,4)<0 || memcmp(buffer,"\0PRF",4))goto done;
    other=io->open_read(io->context,pdn_export_final_path);
    if(other>=0){
        int equal=export_equal(io,source,other,(unsigned)size);
        export_close(io,&other,&unsafe);
        if(unsafe)goto done;
        if(equal){result=PDN_EXPORT_READY;goto done;}
    }
    if(io->cancelled(io->context) || io->rewind(io->context,source)<0)goto done;
    for(i=0;i<sizeof(directories)/sizeof(directories[0]);i++){
        if(io->cancelled(io->context))goto done;
        /* A partially successful mkdir may dirty FAT even if a later one
         * fails. Synchronize before telling the kernel it may start USB. */
        dirty=1;
        if(io->directory(io->context,directories[i])<0)goto done;
    }
    temporary=io->create_new(io->context,pdn_export_temp_path);
    if(temporary<0)goto done; /* Pre-existing temporary files are never removed. */
    created=1;
    while(offset<(unsigned)size){
        n=(unsigned)size-offset;if(n>sizeof(buffer))n=sizeof(buffer);
        if(export_read(io,source,buffer,n)<0)goto done;
        done=0;
        while(done<n){
            if(io->cancelled(io->context))goto done;
            written=io->write(io->context,temporary,buffer+done,n-done);
            if(written<=0 || (unsigned)written>n-done)goto done;
            done+=(unsigned)written;
        }
        offset+=n;
    }
    /* Refuse a source that grew after its bounded size was measured. */
    got=io->read(io->context,source,buffer,1);
    if(got!=0)goto done;
    export_close(io,&temporary,&unsafe);
    if(unsafe)goto done;
    other=io->open_read(io->context,pdn_export_temp_path);
    if(other<0 || !export_equal(io,source,other,(unsigned)size))goto done;
    export_close(io,&other,&unsafe);export_close(io,&source,&unsafe);
    if(unsafe || io->cancelled(io->context))goto done;
    if(io->sync(io->context)<0){unsafe=1;goto done;}
    if(io->publish(io->context,pdn_export_temp_path,pdn_export_final_path)<0)goto done;
    created=0; /* Never delete the final resource after publication. */
    result=PDN_EXPORT_READY;
done:
    export_close(io,&temporary,&unsafe);export_close(io,&other,&unsafe);export_close(io,&source,&unsafe);
    if(created && !unsafe){
        /* Only a file successfully created O_EXCL by this invocation. */
        if(io->remove_created(io->context,pdn_export_temp_path)<0)unsafe=1;
    }
    if(dirty && io->sync(io->context)<0)unsafe=1;
    return unsafe?PDN_EXPORT_UNSAFE:result;
}
