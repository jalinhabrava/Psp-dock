TARGET = docknative
OBJS = native_driver.o
LIBS = -lpspusb_driver -lpspusbbus_driver -lpspctrl_driver
USE_KERNEL_LIBC = 1
USE_KERNEL_LIBS = 1
BUILD_PRX = 1
PRX_EXPORTS = exports.exp
CFLAGS = -std=gnu11 -O2 -G0 -Wall -Wextra -Werror
ASFLAGS = $(CFLAGS)
LDFLAGS = -nostartfiles
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak

native_driver.o: native_driver.c native_common.h native_protocol.h native_descriptors.h lab_gate.h native_report_descriptor.h native_resource_export.h
