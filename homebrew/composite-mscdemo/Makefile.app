TARGET = pspdockstoragetest
OBJS = app.o
CFLAGS = -std=gnu11 -O2 -G0 -Wall -Wextra -Werror
ASFLAGS = $(CFLAGS)
BUILD_PRX = 1
EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSP Dock - USB Storage + HID 0207
PSP_EBOOT_ICON = assets/ICON0.PNG
PSP_EBOOT_UNKPNG = assets/PIC0.PNG
PSP_EBOOT_PIC1 = assets/PIC1.PNG
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak

# The SDK recipe must rebuild the cached SFO when the XMB title changes.
PARAM.SFO: Makefile
