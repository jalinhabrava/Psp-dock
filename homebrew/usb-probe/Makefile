TARGET = dockprobe
OBJS = main.o
BUILD_PRX = 1
CFLAGS = -O2 -G0 -Wall -Wextra
LIBS = -lpspusb_driver -lpspusbstor -lpspkernel
EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSP Dock - USB Probe
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
