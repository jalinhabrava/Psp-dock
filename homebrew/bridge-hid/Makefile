TARGET = bridgehid
OBJS = bridge_hid.o
LIBS = -lpspusb_driver -lpspusbbus_driver
USE_KERNEL_LIBC = 1
USE_KERNEL_LIBS = 1
BUILD_PRX = 1
PRX_EXPORTS = exports.exp
CFLAGS = -O2 -G0 -Wall -Wextra -Werror
ASFLAGS = $(CFLAGS)
LDFLAGS = -nostartfiles
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
