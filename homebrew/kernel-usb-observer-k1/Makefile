TARGET = pspdockk1observer
OBJS = main.o
BUILD_PRX = 1
PRX_EXPORTS = exports.exp
USE_KERNEL_LIBC = 1
USE_KERNEL_LIBS = 1
LDFLAGS = -nostartfiles
CFLAGS = -O2 -G0 -Wall -Wextra -Werror
LIBS = -lpspusb_driver
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
