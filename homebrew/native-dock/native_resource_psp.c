/* SPDX-License-Identifier: GPL-3.0-only
 * Narrow USER file API adapter; compiled imports are audited separately.
 * No flash assignment, raw devices, directory enumeration or USB imports.
 */
#include <pspiofilemgr.h>
#include "native_common.h"
#include "native_resource_export.h"

static int source_open(void *context,const char *path) {
    (void)context;return sceIoOpen(path,PSP_O_RDONLY,0);
}
static int temporary_create(void *context,const char *path) {
    (void)context;return sceIoOpen(path,PSP_O_WRONLY|PSP_O_CREAT|PSP_O_EXCL,0600);
}
static int file_length(void *context,int fd) {
    (void)context;return sceIoLseek32(fd,0,PSP_SEEK_END);
}
static int file_rewind(void *context,int fd) {
    (void)context;return sceIoLseek32(fd,0,PSP_SEEK_SET);
}
static int file_read(void *context,int fd,void *data,unsigned count) {
    (void)context;return sceIoRead(fd,data,count);
}
static int file_write(void *context,int fd,const void *data,unsigned count) {
    (void)context;return sceIoWrite(fd,data,count);
}
static int file_close(void *context,int fd) {(void)context;return sceIoClose(fd);}
static int directory(void *context,const char *path) {
    SceIoStat stat;(void)context;
    if(sceIoGetstat(path,&stat)>=0)return FIO_S_ISDIR(stat.st_mode)?0:-1;
    return sceIoMkdir(path,0700);
}
static int publish(void *context,const char *from,const char *to) {
    (void)context;return sceIoRename(from,to);
}
static int remove_created(void *context,const char *path) {(void)context;return sceIoRemove(path);}
static int sync_card(void *context) {(void)context;return sceIoSync("ms0:",0);}
static int cancelled(void *context) {
    PdnSharedStatus *state=context;
    return state->export_request!=PDN_EXPORT_REQUESTED || state->request_stop || state->exit_requested;
}
int pdn_export_user(PdnSharedStatus *state) {
    const PdnExportIO io={state,source_open,temporary_create,file_length,file_rewind,
        file_read,file_write,file_close,directory,publish,remove_created,sync_card,cancelled};
    return pdn_export_resource(&io);
}
