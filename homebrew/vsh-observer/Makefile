TARGET = pspdockvsh
OBJS = main.o
BUILD_PRX = 1
PRX_EXPORTS = exports.exp
LDFLAGS = -nostartfiles
CFLAGS = -O2 -G0 -Wall -Wextra -Werror
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
