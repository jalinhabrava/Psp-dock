TARGET = pspdockapp
OBJS = main.o ui.o
BUILD_PRX = 1
CFLAGS = -O2 -G0 -std=gnu99 -Wall -Wextra -Werror
EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSP Dock
PSP_EBOOT_ICON = assets/ICON0.PNG
PSP_EBOOT_UNKPNG = assets/PIC0.PNG
PSP_EBOOT_PIC1 = assets/PIC1.PNG
PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
