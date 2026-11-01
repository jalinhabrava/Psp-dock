TARGET = pspdockapp
OBJS = main.o
BUILD_PRX = 1
CFLAGS = -O2 -G0 -Wall -Wextra -Werror
EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSP Dock
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
