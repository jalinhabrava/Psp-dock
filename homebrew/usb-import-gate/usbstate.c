/*
 * Read-only USB import probe. Loaded only after the independent user EBOOT
 * is visible. Imports sceUsb, not sceUsbstorBoot and no kernel-only NIDs.
 * Return sceUsbGetState via sceKernelStartModule's pStatus in the UI.
 */
#include <pspkernel.h>
#include <pspusb.h>

PSP_MODULE_INFO("DockUsbStateRead", PSP_MODULE_USER, 1, 0);

int module_start(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    return sceUsbGetState();
}
int module_stop(SceSize args, void *argp) {
    (void)args;
    (void)argp;
    return 0;
}
