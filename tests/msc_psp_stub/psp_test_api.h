/* Native transport test doubles only: no PSP hardware claims. */
#ifndef PSP_TEST_API_H
#define PSP_TEST_API_H
#include <stddef.h>
#include <stdint.h>
typedef int SceUID;
typedef unsigned SceSize;
typedef unsigned SceUInt;
typedef struct {unsigned Buttons;uint8_t Lx,Ly;} SceCtrlData;
#define PSP_MODULE_INFO(a,b,c,d)
#define PSP_MODULE_KERNEL 0
#define PSP_CTRL_MODE_ANALOG 1
#define PSP_CTRL_CROSS (1u<<0)
#define PSP_CTRL_CIRCLE (1u<<1)
#define PSP_CTRL_SQUARE (1u<<2)
#define PSP_CTRL_TRIANGLE (1u<<3)
#define PSP_CTRL_LTRIGGER (1u<<4)
#define PSP_CTRL_RTRIGGER (1u<<5)
#define PSP_CTRL_SELECT (1u<<6)
#define PSP_CTRL_START (1u<<7)
#define PSP_CTRL_HOME (1u<<8)
#define PSP_CTRL_VOLDOWN (1u<<9)
#define PSP_CTRL_VOLUP (1u<<10)
#define PSP_CTRL_SCREEN (1u<<11)
#define PSP_CTRL_NOTE (1u<<12)
#define PSP_CTRL_UP (1u<<13)
#define PSP_CTRL_RIGHT (1u<<14)
#define PSP_CTRL_DOWN (1u<<15)
#define PSP_CTRL_LEFT (1u<<16)
#define PSP_USBSTOR_DRIVERNAME "USBStor"
#define PSP_USBBUS_DRIVERNAME "USBBus"
#define PSP_USB_CABLE_CONNECTED 0x020
struct UsbEndpoint {int endpnum,unk1,unk2;};
struct DeviceRequest {uint8_t bmRequestType,bRequest;uint16_t wValue,wIndex,wLength;};
struct UsbdDeviceReq {
    struct UsbEndpoint *endp;void *data;int size;
    int (*func)(struct UsbdDeviceReq *,int,int);
    int retcode,recvsize;
};
struct UsbDriver {
    const char *name;int endpoints;struct UsbEndpoint *endp;void *intp;
    void *devp_hi,*confp_hi,*devp,*confp,*str;
    int (*recvctl)(int,int,struct DeviceRequest *);
    int (*func28)(int,int,int);
    int (*attach)(int,void *,void *);
    int (*detach)(int,int,int);
    int (*start_func)(int,void *),(*stop_func)(int,void *);
};
int sceUsbbdReqSend(struct UsbdDeviceReq *);
int sceUsbbdReqRecv(struct UsbdDeviceReq *);
int sceUsbbdReqCancelAll(struct UsbEndpoint *);
int sceUsbbdStall(struct UsbEndpoint *);
static inline int sceKernelCpuSuspendIntr(void){return 0;}
static inline void sceKernelCpuResumeIntr(int i){(void)i;}
static inline void sceKernelDcacheInvalidateRange(void *p,size_t n){(void)p;(void)n;}
static inline void sceKernelDcacheWritebackInvalidateRange(void *p,size_t n){(void)p;(void)n;}
static inline void sceKernelDcacheWritebackRange(void *p,size_t n){(void)p;(void)n;}
static inline unsigned sceKernelGetSystemTimeLow(void){return 17;}
static inline int sceCtrlPeekBufferPositive(SceCtrlData *p,int n){(void)p;(void)n;return 0;}
static inline int sceCtrlSetSamplingCycle(int x){return x;}
static inline int sceCtrlSetSamplingMode(int x){return x;}
static inline int sceKernelWaitSema(int a,int b,void *c){(void)a;(void)b;(void)c;return 0;}
static inline int sceKernelSignalSema(int a,int b){(void)a;(void)b;return 0;}
static inline int sceKernelDelayThread(int x){(void)x;return 0;}
static inline int sceKernelCreateSema(const char *s,int a,int b,int c,void *p){(void)s;(void)a;(void)b;(void)c;(void)p;return 1;}
static inline int sceKernelCreateThread(const char *s,int(*f)(SceSize,void *),int p,int n,int a,void *o){(void)s;(void)f;(void)p;(void)n;(void)a;(void)o;return 1;}
static inline int sceKernelStartThread(int t,int a,void *p){(void)t;(void)a;(void)p;return 0;}
static inline int sceKernelWaitThreadEnd(int t,unsigned *p){(void)t;(void)p;return 0;}
static inline int sceKernelDeleteThread(int t){(void)t;return 0;}
static inline int sceKernelDeleteSema(int t){(void)t;return 0;}
static inline int sceUsbActivate(int x){(void)x;return 0;}
static inline int sceUsbDeactivate(int x){(void)x;return 0;}
static inline int sceUsbGetState(void){return 0;}
static inline int sceUsbGetDrvState(const char *s){(void)s;return 2;}
static inline int sceUsbStart(const char *s,int n,void *p){(void)s;(void)n;(void)p;return 0;}
static inline int sceUsbStop(const char *s,int n,void *p){(void)s;(void)n;(void)p;return 0;}
static inline int sceUsbbdRegister(struct UsbDriver *d){(void)d;return 0;}
static inline int sceUsbbdUnregister(struct UsbDriver *d){(void)d;return 0;}
#endif
