TARGET = pspdockclient
OBJS = main.o PSPDockHid.o
BUILD_PRX = 1
CFLAGS = -O2 -G0 -Wall -Wextra -Werror
LIBS = -lpspusb_driver -lpspusbstor -lpspkernel
EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSP Dock - Client LAB 0.1
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
