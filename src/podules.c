#include <string.h>

#include "arc.h"
#include "podules.h"

/*Podules -
  0 is reserved for extension ROMs
  1 is for additional IDE interface
  2-3 are free*/
static podule podules[4];
static int freepodule;

/**
 * Reset and empty all the podule slots
 *
 * Safe to call on program startup and user instigated virtual machine
 * reset.
 */
void
podules_reset(void)
{
	int c;

	/* Call any reset functions that an open podule may have to allow
	   then to tidy open files etc */
	for (c = 0; c < 4; c++) {
		if (podules[c].reset) {
                        podules[c].reset(&podules[c]);
                }
	}

	/* Blank all 8 podules */
	memset(podules, 0, 4 * sizeof(podule));

	freepodule = 2;
}
  
podule *addpodule(void (*writel)(podule *p, int easi, uint32_t addr, uint32_t val),
              void (*writew)(podule *p, int easi, uint32_t addr, uint16_t val),
              void (*writeb)(podule *p, int easi, uint32_t addr, uint8_t val),
              uint32_t  (*readl)(podule *p, int easi, uint32_t addr),
              uint16_t (*readw)(podule *p, int easi, uint32_t addr),
              uint8_t  (*readb)(podule *p, int easi, uint32_t addr),
              int (*timercallback)(podule *p),
              void (*reset)(podule *p),
              int broken)
{
//        return NULL;
        if (freepodule==4) return NULL; /*All podules in use!*/
        podules[freepodule].readl=readl;
        podules[freepodule].readw=readw;
        podules[freepodule].readb=readb;
        podules[freepodule].writel=writel;
        podules[freepodule].writew=writew;
        podules[freepodule].writeb=writeb;
        podules[freepodule].timercallback=timercallback;
        podules[freepodule].reset=reset;
        podules[freepodule].broken=broken;
//        rpclog("Podule added at %i\n",freepodule);
        freepodule++;
        return &podules[freepodule-1];
}

void rethinkpoduleints(void)
{
        int c;
        ioc.irqb &= ~(0x21);
        ioc.fiq  &= ~0x40;
        for (c=0;c<4;c++)
        {
                if (podules[c].irq)
                {
//                        rpclog("Podule IRQ! %02X %i\n", ioc.mskb, c);
                        ioc.irqb |= 0x20;
                }
                if (podules[c].fiq)
                {
                        ioc.irqb |= 0x01;
                        ioc.fiq  |= 0x40;
                }
        }
        ioc_updateirqs();
}

void writepodulel(int num, int easi, uint32_t addr, uint32_t val)
{
        int oldirq=podules[num].irq,oldfiq=podules[num].fiq;
        if (podules[num].writel)
           podules[num].writel(&podules[num], easi,addr,val);
/*        if (oldirq!=podules[num].irq || oldfiq!=podules[num].fiq) */rethinkpoduleints();
}

void writepodulew(int num, int easi, uint32_t addr, uint32_t val)
{
        int oldirq=podules[num].irq,oldfiq=podules[num].fiq;
        if (podules[num].writew)
        {
//                rpclog("WRITE PODULEw 1 %08X %08X %04X\n",addr,armregs[15]-8,val);
                if (podules[num].broken) podules[num].writel(&podules[num], easi,addr,val);
                else                     podules[num].writew(&podules[num], easi,addr,val>>16);
        }
/*        if (oldirq!=podules[num].irq || oldfiq!=podules[num].fiq) */rethinkpoduleints();
}

void writepoduleb(int num, int easi, uint32_t addr, uint8_t val)
{
        int oldirq=podules[num].irq,oldfiq=podules[num].fiq;
        if (podules[num].writeb)
        {
//                output=0;
//                rpclog("WRITE PODULEb 1 %08X %08X %02X\n",addr,armregs[15]-8,val);
                podules[num].writeb(&podules[num], easi,addr,val);
        }
/*        if (oldirq!=podules[num].irq || oldfiq!=podules[num].fiq) */rethinkpoduleints();
}

uint32_t readpodulel(int num, int easi, uint32_t addr)
{
        int oldirq=podules[num].irq,oldfiq=podules[num].fiq;
        uint32_t temp;
        if (podules[num].readl)
        {
//                if (num==1) rpclog("READ PODULEl 1 %08X %08X\n",addr,armregs[15]-8);
                temp=podules[num].readl(&podules[num], easi, addr);
//                if (num==2) printf("%08X\n",temp);
/*                if (oldirq!=podules[num].irq || oldfiq!=podules[num].fiq) */rethinkpoduleints();
                return temp;
        }
        return 0xFFFFFFFF;
}

uint32_t readpodulew(int num, int easi, uint32_t addr)
{
        int oldirq=podules[num].irq,oldfiq=podules[num].fiq;
        uint32_t temp;
        if (podules[num].readw)
        {
//                rpclog("READ PODULEw 1 %08X %08X\n",addr,armregs[15]-8);
                if (podules[num].broken) temp=podules[num].readl(&podules[num],easi, addr);
                else                     temp=podules[num].readw(&podules[num],easi, addr);
/*                if (oldirq!=podules[num].irq || oldfiq!=podules[num].fiq) */rethinkpoduleints();
                return temp;
        }
        return 0xFFFF;
}

uint8_t readpoduleb(int num, int easi, uint32_t addr)
{
        int oldirq=podules[num].irq,oldfiq=podules[num].fiq;
        uint8_t temp;
//        rpclog("READ PODULE %i %08X %02X %i\n",num,addr,temp,easi);
        if (podules[num].readb)
        {
//                if (addr>=0x3000) output=1;
//                rpclog("READ PODULEb %i %08X %08X\n", num, addr, armregs[15]-8);
                temp=podules[num].readb(&podules[num], easi, addr);
//                rpclog("READ PODULE %i %08X %02X\n",num,addr,temp);
//                rpclog("%02X %i\n",temp, podules[num].irq);
/*                if (oldirq!=podules[num].irq || oldfiq!=podules[num].fiq) */rethinkpoduleints();
                return temp;
        }
        return 0xFF;
}

void runpoduletimers(int t)
{
        int c,d;
//        return;
        for (c=0;c<4;c++)
        {
                if (podules[c].timercallback && podules[c].msectimer)
                {
                        podules[c].msectimer-=t;
                        d=1;
                        while (podules[c].msectimer<=0 && d)
                        {
                                int oldirq=podules[c].irq,oldfiq=podules[c].fiq;
//                                rpclog("Callback! podule %i  %i %i\n",c,podules[c].irq,podules[c].fiq);
                                d=podules[c].timercallback(&podules[c]);
                                if (!d)
                                {
                                        podules[c].msectimer=0;
                                }
                                else podules[c].msectimer+=d;
//                                rpclog("%i %i\n", d, podules[c].irq);
/*                                if (oldirq!=podules[c].irq || oldfiq!=podules[c].fiq)
                                {
                                        rpclog("Now rethinking podule ints...\n");*/
                                        rethinkpoduleints();
/*                                }*/
                        }
                }
        }
}
