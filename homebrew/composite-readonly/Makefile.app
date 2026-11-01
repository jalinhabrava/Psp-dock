TARGET = pspdockcompositetest
OBJS = app.o
CFLAGS = -std=gnu11 -O2 -G0 -Wall -Wextra -Werror
ASFLAGS = $(CFLAGS)
BUILD_PRX = 1
EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSP Dock Composite TEST - Read Only
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
