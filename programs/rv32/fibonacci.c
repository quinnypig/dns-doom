#include "io.h"
volatile unsigned int results[12];
volatile unsigned int count = 12;
int main(void) {
    unsigned int a=0,b=1;
    for(unsigned int i=0;i<count;i++) {
        results[i]=a;
        unsigned int next=a+b;
        a=b; b=next;
    }
    output("C on DNS: Fibonacci finished.\n");
    return results[11] == 89 ? 0 : 1;
}
