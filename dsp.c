/* Seven-band stereo compressor. No lookahead, clipping, or final limiter. */
#include <math.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#define BANDS 7
#define PI 3.14159265358979323846
typedef struct {
    float threshold[BANDS], release_ms[BANDS];
    float ratio, output_db, auto_level, target_db, max_boost_db, level_ms, bypass;
} Params;
typedef struct {
    float input_db, output_db, auto_db, reduction[BANDS];
} Meters;
typedef struct { double b0,b1,b2,a1,a2,z1,z2; } Biquad;
typedef struct { Biquad lp[2][2], hp[2][2]; } Split;
typedef struct {
    double rate, power, auto_db, gain[BANDS], output_db, wet;
    Split split[6], phase[6][6];
} DSP;
static const double crossovers[6] = {80,200,500,1250,3150,8000};
static double db(double x) { return 20*log10(fmax(x, 1e-12)); }
static double amp(double x) { return pow(10, x/20); }
static double clamp(double x,double lo,double hi) { return fmin(hi,fmax(lo,x)); }
static void setup(Biquad *b, double rate, double hz, int high) {
    double w=2*PI*hz/rate, c=cos(w), a=sin(w)/sqrt(2), n=1+a;
    b->b0=(high ? (1+c) : (1-c))/(2*n);
    b->b1=(high ? -(1+c) : (1-c))/n;
    b->b2=b->b0; b->a1=-2*c/n; b->a2=(1-a)/n;
}
static void setup_split(Split *s,double rate,double hz) {
    for(int c=0;c<2;c++) for(int k=0;k<2;k++) {
        setup(&s->lp[c][k],rate,hz,0); setup(&s->hp[c][k],rate,hz,1);
    }
}
static double filter(Biquad *b,double x) {
    double y=b->b0*x+b->z1;
    b->z1=b->b1*x-b->a1*y+b->z2; b->z2=b->b2*x-b->a2*y;
    if(fabs(b->z1)<1e-30) b->z1=0;
    if(fabs(b->z2)<1e-30) b->z2=0;
    return y;
}
static void split(Split *s,int c,double x,double *lo,double *hi) {
    *lo=filter(&s->lp[c][1],filter(&s->lp[c][0],x));
    *hi=filter(&s->hp[c][1],filter(&s->hp[c][0],x));
}
void *ea_create(double rate) {
    if(!isfinite(rate)||rate<32000||rate>192000) return NULL;
    DSP *s=calloc(1,sizeof(*s)); if(!s) return NULL;
    s->rate=rate; s->wet=1;
    for(int k=0;k<6;k++) {
        setup_split(&s->split[k],rate,crossovers[k]);
        for(int b=0;b<k;b++) setup_split(&s->phase[k][b],rate,crossovers[k]);
    }
    return s;
}
void ea_destroy(void *ptr) { free(ptr); }
size_t ea_params_size(void) { return sizeof(Params); }
void ea_process(void *ptr,float *audio,size_t frames,const Params *p,Meters *m) {
    DSP *s=ptr; double inpeak=0,outpeak=0,gr[BANDS]={0};
    double release[BANDS],threshold[BANDS];
    double slope=1-1/clamp(p->ratio,1,1000);
    for(int b=0;b<BANDS;b++) {
        release[b]=exp(-1/(s->rate*clamp(p->release_ms[b],10,5000)*.001));
        threshold[b]=clamp(p->threshold[b],-60,0);
    }
    double rms_c=exp(-1/(s->rate*.25));
    double level_c=exp(-1/(s->rate*clamp(p->level_ms,100,10000)*.001));
    double trim_c=exp(-1/(s->rate*.02));
    for(size_t i=0;i<frames;i++) {
        double dry[2],x[2],bands[BANDS][2],sum[2]={0};
        for(int c=0;c<2;c++) {
            dry[c]=audio[2*i+c]; if(!isfinite(dry[c])) dry[c]=0;
            inpeak=fmax(inpeak,fabs(dry[c]));
        }
        double power=(dry[0]*dry[0]+dry[1]*dry[1])*.5;
        s->power=rms_c*s->power+(1-rms_c)*power;
        double level=db(sqrt(s->power));
        /* Both short-term and current signal must be above the pause floor.
           Hold gain during pauses; this is not a noise gate or speech detector. */
        double want=s->auto_db;
        if(p->auto_level<.5) want=0;
        else if(level>-50 && db(sqrt(power))>-50)
            want=clamp(p->target_db-level,-24,clamp(p->max_boost_db,0,24));
        s->auto_db=level_c*s->auto_db+(1-level_c)*want;
        for(int c=0;c<2;c++) {
            x[c]=dry[c]*amp(s->auto_db);
            double rest=x[c];
            for(int k=0;k<6;k++) {
                double hi; split(&s->split[k],c,rest,&bands[k][c],&hi); rest=hi;
                /* Match the phase of previously split bands at each crossover. */
                for(int b=0;b<k;b++) {
                    double lo2,hi2;
                    split(&s->phase[k][b],c,bands[b][c],&lo2,&hi2);
                    bands[b][c]=lo2+hi2;
                }
            }
            bands[6][c]=rest;
        }
        for(int b=0;b<BANDS;b++) {
            double peak=fmax(fabs(bands[b][0]),fabs(bands[b][1]));
            double over=db(peak)-threshold[b], knee=6, reduction;
            if(over<=-knee/2) reduction=0;
            else if(over>=knee/2) reduction=over*slope;
            else reduction=(over+knee/2)*(over+knee/2)*slope/(2*knee);
            double target=-reduction;
            /* Apply any additional reduction to this very sample. */
            if(target<s->gain[b]) s->gain[b]=target;
            else s->gain[b]=release[b]*s->gain[b]+(1-release[b])*target;
            gr[b]=fmax(gr[b],-s->gain[b]);
            double gain=amp(s->gain[b]);
            for(int c=0;c<2;c++) sum[c]+=bands[b][c]*gain;
        }
        s->output_db=trim_c*s->output_db+(1-trim_c)*clamp(p->output_db,-24,18);
        s->wet=trim_c*s->wet+(1-trim_c)*(p->bypass>.5 ? 0 : 1);
        for(int c=0;c<2;c++) {
            double y=s->wet*sum[c]*amp(s->output_db)+(1-s->wet)*dry[c];
            audio[2*i+c]=(float)y; outpeak=fmax(outpeak,fabs(y));
        }
    }
    m->input_db=(float)db(inpeak); m->output_db=(float)db(outpeak);
    m->auto_db=(float)s->auto_db;
    for(int b=0;b<BANDS;b++) m->reduction[b]=(float)gr[b];
}
