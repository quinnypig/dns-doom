/* Bare-metal I/O adapter. Game logic and renderer remain PureDOOM C code. */
#include "io.h"
#define DOOM_IMPLEMENTATION
#include "../../vendor/PureDOOM/PureDOOM.h"

static unsigned char heap[16*1024*1024];
static unsigned int allocated;
static void *allocate(int size) {
    unsigned int base=(allocated+15)&~15u;
    allocated=base+(unsigned int)size;
    if(allocated>sizeof(heap)) { output("DNS Doom: out of heap\n"); device(0,99,0,0,0); }
    return heap+base;
}
static void release(void *p) { (void)p; }
static void print_text(const char *s) { output(s); }
static void *open_file(const char *name,const char *mode) { return (void *)device(3,(long)name,(long)mode,0,0); }
static void close_file(void *f) { device(7,(long)f,0,0,0); }
static int read_file(void *f,void *buf,int count) { return device(4,(long)f,(long)buf,count,0); }
static int write_file(void *f,const void *buf,int count) { (void)f;(void)buf;(void)count; return 0; }
static int seek_file(void *f,int offset,doom_seek_t origin) { return device(5,(long)f,offset,origin,0); }
static int tell_file(void *f) { return device(6,(long)f,0,0,0); }
static int eof_file(void *f) { return device(9,(long)f,0,0,0); }
static void clock_time(int *sec,int *usec) { unsigned int ms=device(8,0,0,0,0); *sec=ms/1000;*usec=(ms%1000)*1000; }
static void quit(int code) { device(0,code,0,0,0); for(;;){} }
static char *environment(const char *name) { (void)name; return "."; }

/* Compiler-generated aggregate copies can reference these symbols. */
void *memcpy(void *to,const void *from,unsigned int count) { unsigned char *d=to;const unsigned char *s=from;while(count--)*d++=*s++;return to; }
void *memset(void *to,int value,unsigned int count) { unsigned char *d=to;while(count--)*d++=value;return to; }

int main(void) {
    output("DNS Doom: entered original engine binary\n");
    doom_set_print(print_text);
    doom_set_malloc(allocate,release);
    doom_set_file_io(open_file,close_file,read_file,write_file,seek_file,tell_file,eof_file);
    doom_set_gettime(clock_time);
    doom_set_exit(quit);
    doom_set_getenv(environment);
#ifdef DNS_SMALL_VIEW
    doom_set_default_int("screenblocks",3);
#endif
    char *argv[]={"dnsdoom","-iwad","doom1.wad","-nosound","-nomusic","-warp","1","1"};
    doom_init(8,argv,0);
    output("DNS Doom: initialization complete\n");
    doom_force_update();
    device(2,(long)screens[0],(long)screen_palette,SCREENWIDTH,SCREENHEIGHT);
    output("DNS Doom: first frame emitted\n");
#ifdef DNS_SMALL_VIEW
    /* Skip the cosmetic screen-melt transition before the measured game tick. */
    is_wiping_screen=0;
    doom_key_down(DOOM_KEY_RIGHT_ARROW);
    doom_force_update();
    device(2,(long)screens[0],(long)screen_palette,SCREENWIDTH,SCREENHEIGHT);
    output("DNS Doom: second frame emitted\n");
#endif
    return 0;
}
