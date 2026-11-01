TARGET = dockcomposite
OBJS = pdc_driver.o pdc_psp_fs.o pdc_service.o pdc_sha256.o pdc_os_descriptors.o pdb2_wire.o
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
