TARGET = dockusbsteps
# RETIRADO: este Makefile arrastra bibliotecas ForKernel y no es válido para
# distribuir un EBOOT PSP_MODULE_USER. El código fuente contiene #error.
OBJS = main.o
BUILD_PRX = 1
CFLAGS = -O2 -G0 -Wall -Wextra -Werror
LIBS = -lpspusb -lpspusbstor -lpspkernel
EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSP Dock - USB Step Probe SAFE
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
