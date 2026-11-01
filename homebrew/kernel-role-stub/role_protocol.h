/* Reserved kernel-side ABI for a future *verified* USB role manager.
 * PROTOCOL v0 has NO role-switching capability and is NOT installable.
 */
#ifndef PSPDOCK_KERNEL_ROLE_PROTOCOL_H
#define PSPDOCK_KERNEL_ROLE_PROTOCOL_H
#include <psptypes.h>

#define PSPDOCK_KSTUB_MAGIC 0x50444B30u
#define PSPDOCK_KSTUB_VERSION 0u
#define PSPDOCK_KSTUB_CAPS 0u
#define PSPDOCK_KSTUB_IDLE 0u
#define PSPDOCK_KSTUB_UNAVAILABLE (-1)

typedef struct {
    u32 magic;
    u32 version;
    u32 capabilities;
    u32 role;
    u32 started;
} PspDockKernelStatus;

_Static_assert(sizeof(PspDockKernelStatus) == 20, "Kernel stub ABI must be fixed");
#endif
