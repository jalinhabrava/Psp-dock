TARGET = pspdocknative
OBJS = app.o
CFLAGS = -std=gnu11 -O2 -G0 -Wall -Wextra -Werror
ASFLAGS = $(CFLAGS)
BUILD_PRX = 1
EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSP Dock
PSP_EBOOT_ICON = assets/ICON0.PNG
PSP_EBOOT_UNKPNG = assets/PIC0.PNG
PSP_EBOOT_PIC1 = assets/PIC1.PNG
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak

PARAM.SFO: Makefile
app.o: app.c native_common.h native_protocol.h native_ui.h
