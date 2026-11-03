/* Actual portable exporter under file-API doubles: no PSP/filesystem access. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../native_resource_export.c"

enum { SOURCE=10, FINAL=11, TEMP=12, CAP=PDN_EXPORT_MAX_BYTES+1 };
static struct {
    uint8_t source[CAP],final[CAP],temp[CAP];
    int source_size,final_size,temp_size,reported_size;
    int source_exists,final_exists,temp_exists;
    unsigned position[3];int open[3];
    int source_denied,short_read,short_write,fail_write,fail_rename;
    int fail_close,fail_sync,fail_remove,fail_directory,corrupt_temp,truncate_at;
    int cancelled,cancel_after_write;
    unsigned writes,creates,publishes,removes,syncs,directories,source_opens;
} fs;

static int slot(int fd){assert(fd>=SOURCE && fd<=TEMP);return fd-SOURCE;}
static uint8_t *content(int fd){return fd==SOURCE?fs.source:fd==FINAL?fs.final:fs.temp;}
static int size_of(int fd){return fd==SOURCE?fs.source_size:fd==FINAL?fs.final_size:fs.temp_size;}
static int open_read(void *context,const char *path){
    int fd;(void)context;
    if(!strcmp(path,pdn_export_source_path)){
        assert(!strcmp(path,"flash0:/vsh/resource/system_plugin.rco"));
        fs.source_opens++;if(!fs.source_exists || fs.source_denied)return -1;fd=SOURCE;
    }else if(!strcmp(path,pdn_export_final_path)){
        if(!fs.final_exists)return -1;
        fd=FINAL;
    }else{
        assert(!strcmp(path,pdn_export_temp_path));if(!fs.temp_exists)return -1;fd=TEMP;
    }
    assert(!fs.open[slot(fd)]);fs.open[slot(fd)]=1;fs.position[slot(fd)]=0;return fd;
}
static int create_new(void *context,const char *path){
    (void)context;assert(!strcmp(path,pdn_export_temp_path));
    if(fs.temp_exists)return -1;
    assert(!fs.open[2]);fs.creates++;fs.temp_exists=1;fs.temp_size=0;fs.open[2]=1;fs.position[2]=0;
    return TEMP;
}
static int length(void *context,int fd){
    (void)context;assert(fs.open[slot(fd)]);
    return fd==SOURCE && fs.reported_size>=0?fs.reported_size:size_of(fd);
}
static int rewind_file(void *context,int fd){(void)context;assert(fs.open[slot(fd)]);fs.position[slot(fd)]=0;return 0;}
static int read_file(void *context,int fd,void *data,unsigned count){
    unsigned index=slot(fd),available;(void)context;assert(fs.open[index]);
    if(fd==SOURCE && fs.truncate_at>=0 && fs.position[index]>=(unsigned)fs.truncate_at)return 0;
    available=(unsigned)size_of(fd)-fs.position[index];if(count>available)count=available;
    if(fs.short_read && count>17)count=17;
    memcpy(data,content(fd)+fs.position[index],count);
    if(fd==TEMP && fs.corrupt_temp && count)((uint8_t *)data)[0]^=1;
    fs.position[index]+=count;return (int)count;
}
static int write_file(void *context,int fd,const void *data,unsigned count){
    (void)context;assert(fd==TEMP && fs.open[2]);fs.writes++;
    if(fs.fail_write)return -1;
    if(fs.short_write && count>13)count=13;
    assert(fs.position[2]+count<=PDN_EXPORT_MAX_BYTES);
    memcpy(fs.temp+fs.position[2],data,count);fs.position[2]+=count;fs.temp_size=(int)fs.position[2];
    if(fs.cancel_after_write)fs.cancelled=1;
    return (int)count;
}
static int close_file(void *context,int fd){
    (void)context;assert(fs.open[slot(fd)]);
    if(fs.fail_close==fd)return -1;
    fs.open[slot(fd)]=0;return 0;
}
static int directory(void *context,const char *path){
    (void)context;
    assert(!strcmp(path,"ms0:/PSP/SYSTEM") || !strcmp(path,"ms0:/PSP/SYSTEM/PSPDock") ||
           !strcmp(path,"ms0:/PSP/SYSTEM/PSPDock/xmb"));
    fs.directories++;return fs.fail_directory?-1:0;
}
static int publish(void *context,const char *from,const char *to){
    (void)context;assert(!strcmp(from,pdn_export_temp_path) && !strcmp(to,pdn_export_final_path));
    assert(fs.temp_exists && !fs.open[0] && !fs.open[1] && !fs.open[2] && fs.syncs);
    if(fs.fail_rename)return -1;
    fs.publishes++;memcpy(fs.final,fs.temp,(size_t)fs.temp_size);fs.final_size=fs.temp_size;
    fs.final_exists=1;fs.temp_exists=0;return 0;
}
static int remove_created(void *context,const char *path){
    (void)context;assert(!strcmp(path,pdn_export_temp_path));
    assert(fs.creates==1 && fs.temp_exists && !fs.open[2]);
    if(fs.fail_remove)return -1;
    fs.removes++;fs.temp_exists=0;return 0;
}
static int sync_card(void *context){
    (void)context;fs.syncs++;return fs.fail_sync?-1:0;
}
static int cancelled(void *context){(void)context;return fs.cancelled;}
static const PdnExportIO io={NULL,open_read,create_new,length,rewind_file,read_file,
    write_file,close_file,directory,publish,remove_created,sync_card,cancelled};

static void fixture(void){
    unsigned i;memset(&fs,0,sizeof(fs));fs.source_exists=1;fs.source_size=89012;
    fs.reported_size=fs.truncate_at=-1;
    for(i=0;i<(unsigned)fs.source_size;i++)fs.source[i]=(uint8_t)(i*37u);
    memcpy(fs.source,"\0PRF",4);
}
static void closed(void){assert(!fs.open[0] && !fs.open[1] && !fs.open[2]);}
static void no_write(void){assert(!fs.writes && !fs.creates && !fs.publishes && !fs.removes && !fs.directories);}
static void test_export_is_exact_and_idempotent(void){
    fixture();assert(pdn_export_resource(&io)==PDN_EXPORT_READY);closed();
    assert(fs.final_size==fs.source_size && !memcmp(fs.final,fs.source,(size_t)fs.source_size));
    assert(fs.publishes==1 && fs.creates==1 && !fs.temp_exists && fs.syncs==2);
    fs.writes=fs.creates=fs.publishes=fs.removes=fs.syncs=fs.directories=0;
    assert(pdn_export_resource(&io)==PDN_EXPORT_READY);closed();no_write();assert(!fs.syncs);
    fixture();fs.short_read=fs.short_write=1;
    assert(pdn_export_resource(&io)==PDN_EXPORT_READY);closed();
    assert(!memcmp(fs.final,fs.source,(size_t)fs.source_size));
}
static void test_source_is_optional_bounded_read_only(void){
    fixture();fs.source_denied=1;assert(pdn_export_resource(&io)==PDN_EXPORT_SKIPPED);closed();no_write();
    fixture();fs.source_exists=0;assert(pdn_export_resource(&io)==PDN_EXPORT_SKIPPED);closed();no_write();
    fixture();fs.source[0]=99;assert(pdn_export_resource(&io)==PDN_EXPORT_SKIPPED);closed();no_write();
    fixture();fs.reported_size=PDN_EXPORT_MAX_BYTES+1;
    assert(pdn_export_resource(&io)==PDN_EXPORT_SKIPPED);closed();no_write();
    fixture();fs.reported_size=3;assert(pdn_export_resource(&io)==PDN_EXPORT_SKIPPED);closed();no_write();
    fixture();fs.cancelled=1;assert(pdn_export_resource(&io)==PDN_EXPORT_SKIPPED);closed();no_write();assert(!fs.source_opens);
}
static void test_old_staged_resource_is_not_current_source_evidence(void){
    unsigned missing;
    for(missing=0;missing<2;missing++){
        fixture();fs.final_exists=1;fs.final_size=7;memcpy(fs.final,"PREV123",7);
        fs.source_exists=!missing;fs.source_denied=!missing;
        assert(pdn_export_resource(&io)==PDN_EXPORT_SKIPPED);closed();no_write();
        assert(fs.final_exists && fs.final_size==7 && !memcmp(fs.final,"PREV123",7));
    }
}
static void test_partial_changes_never_publish_or_destroy_previous(void){
    unsigned scenario;
    for(scenario=0;scenario<6;scenario++){
        fixture();fs.final_exists=1;fs.final_size=7;memcpy(fs.final,"PREV123",7);
        fs.fail_write=scenario==0;fs.fail_rename=scenario==1;fs.corrupt_temp=scenario==2;
        fs.cancel_after_write=scenario==3;fs.truncate_at=scenario==4?5000:-1;
        if(scenario==5)fs.reported_size=fs.source_size-1;
        assert(pdn_export_resource(&io)==PDN_EXPORT_SKIPPED);closed();
        assert(fs.final_size==7 && !memcmp(fs.final,"PREV123",7));
        assert(!fs.publishes && !fs.temp_exists && fs.removes==1 && fs.syncs>=1);
    }
    fixture();fs.temp_exists=1;fs.temp_size=7;memcpy(fs.temp,"PRIVATE",7);
    assert(pdn_export_resource(&io)==PDN_EXPORT_SKIPPED);closed();
    assert(fs.temp_exists && fs.temp_size==7 && !memcmp(fs.temp,"PRIVATE",7));
    assert(!fs.creates && !fs.removes && !fs.publishes);
    fixture();fs.fail_directory=1;assert(pdn_export_resource(&io)==PDN_EXPORT_SKIPPED);closed();
    assert(!fs.creates && !fs.publishes && fs.syncs==1);
}
static void test_unsafe_close_sync_or_cleanup_blocks_usb_ack(void){
    fixture();fs.fail_close=TEMP;
    assert(pdn_export_resource(&io)==PDN_EXPORT_UNSAFE);assert(!fs.publishes && fs.open[2]);
    fixture();fs.fail_close=SOURCE;
    assert(pdn_export_resource(&io)==PDN_EXPORT_UNSAFE);assert(!fs.publishes && fs.open[0]);
    fixture();fs.fail_sync=1;
    assert(pdn_export_resource(&io)==PDN_EXPORT_UNSAFE);closed();assert(!fs.publishes);
    fixture();fs.fail_write=fs.fail_remove=1;
    assert(pdn_export_resource(&io)==PDN_EXPORT_UNSAFE);closed();assert(!fs.publishes && fs.temp_exists);
}
int main(void){
    test_export_is_exact_and_idempotent();test_source_is_optional_bounded_read_only();
    test_old_staged_resource_is_not_current_source_evidence();
    test_partial_changes_never_publish_or_destroy_previous();test_unsafe_close_sync_or_cleanup_blocks_usb_ack();
    puts("native resource export: fixed paths, byte equality, bounds, partial IO, cancellation, cleanup and unsafe ACK rejection PASS");
    return 0;
}
