/* SPDX-License-Identifier: GPL-3.0-only
 * Bounded text builder usable in the PSP kernel without user-libc snprintf.
 * On overflow it preserves a valid NUL-terminated prefix and records error.
 */
#ifndef PDC_TEXT_H
#define PDC_TEXT_H
#include <stddef.h>
#include <stdint.h>
typedef struct { char *data; size_t cap, used; int failed; } PdcText;
static inline void pdc_text_init(PdcText *t,void *data,size_t cap) {
    t->data=(char *)data;t->cap=cap;t->used=0;t->failed=(!data || !cap);
    if(data && cap)t->data[0]=0;
}
static inline void pdc_text_char(PdcText *t,char c) {
    if(t->failed)return;
    if(t->used>=t->cap-1){t->failed=1;return;}
    t->data[t->used++]=c;t->data[t->used]=0;
}
static inline void pdc_text_append(PdcText *t,const char *value) {
    if(!value){t->failed=1;return;}
    while(*value && !t->failed)pdc_text_char(t,*value++);
}
static inline void pdc_text_number(PdcText *t,uint64_t value) {
    char digits[20];unsigned n=0;
    do {digits[n++]=(char)('0'+value%10);value/=10;} while(value && n<20);
    while(n)pdc_text_char(t,digits[--n]);
}
#endif
