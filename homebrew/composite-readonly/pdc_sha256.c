/* SPDX-License-Identifier: GPL-3.0-only
 * SHA-256 per FIPS 180-4, streaming implementation with known-answer tests.
 * Hashes verify copied bytes; no claim of authentication/signature.
 */
#include "pdc_sha256.h"
#include <string.h>
static const uint32_t K[64]={
0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
static uint32_t rr(uint32_t v,unsigned n){return(v>>n)|(v<<(32-n));}
static void transform(PdcSha256 *c,const uint8_t *p){
    uint32_t w[64],a,b,d,e,f,g,h,x,t1,t2;unsigned i;
    for(i=0;i<16;i++)w[i]=((uint32_t)p[4*i]<<24)|((uint32_t)p[4*i+1]<<16)|((uint32_t)p[4*i+2]<<8)|p[4*i+3];
    for(i=16;i<64;i++)w[i]=(rr(w[i-15],7)^rr(w[i-15],18)^(w[i-15]>>3))+w[i-16]+
        (rr(w[i-2],17)^rr(w[i-2],19)^(w[i-2]>>10))+w[i-7];
    a=c->h[0];b=c->h[1];x=c->h[2];d=c->h[3];e=c->h[4];f=c->h[5];g=c->h[6];h=c->h[7];
    for(i=0;i<64;i++){
        t1=h+(rr(e,6)^rr(e,11)^rr(e,25))+((e&f)^((~e)&g))+K[i]+w[i];
        t2=(rr(a,2)^rr(a,13)^rr(a,22))+((a&b)^(a&x)^(b&x));
        h=g;g=f;f=e;e=d+t1;d=x;x=b;b=a;a=t1+t2;
    }
    c->h[0]+=a;c->h[1]+=b;c->h[2]+=x;c->h[3]+=d;c->h[4]+=e;c->h[5]+=f;c->h[6]+=g;c->h[7]+=h;
}
void pdc_sha256_init(PdcSha256 *c){
    static const uint32_t initial[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    memset(c,0,sizeof(*c));memcpy(c->h,initial,sizeof(initial));
}
void pdc_sha256_update(PdcSha256 *c,const uint8_t *p,size_t n){
    c->bytes+=n;
    while(n){size_t take=64-c->used;if(take>n)take=n;
        memcpy(c->block+c->used,p,take);c->used+=take;p+=take;n-=take;
        if(c->used==64){transform(c,c->block);c->used=0;}}
}
void pdc_sha256_finish(PdcSha256 *c,uint8_t out[32]){
    uint64_t bits=c->bytes*8;unsigned i;
    c->block[c->used++]=0x80;
    if(c->used>56){memset(c->block+c->used,0,64-c->used);transform(c,c->block);c->used=0;}
    memset(c->block+c->used,0,56-c->used);
    for(i=0;i<8;i++)c->block[63-i]=(uint8_t)(bits>>(8*i));
    transform(c,c->block);
    for(i=0;i<8;i++){out[4*i]=(uint8_t)(c->h[i]>>24);out[4*i+1]=(uint8_t)(c->h[i]>>16);out[4*i+2]=(uint8_t)(c->h[i]>>8);out[4*i+3]=(uint8_t)c->h[i];}
    memset(c,0,sizeof(*c));
}
