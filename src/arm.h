extern int arm_cpu_type;

extern int arm_cpu_speed, arm_mem_speed;
extern int arm_has_swp;
extern int arm_has_cp15;

extern void cache_flush();

extern void arm_clock_i(int cycles);

extern void cache_read_timing(uint32_t addr, int is_n_cycle, int is_merged_fetch);
extern void cache_write_timing(uint32_t addr, int is_n_cycle);

#define ARM_USER_MODE (!(armregs[15] & 3))

extern uint32_t rotatelookup[4096];

void recalc_min_timer(void);

/*Abandon the rest of the current execarm() slice - see arm.c*/
void arm_break_execution(void);

/*Execution profiler - see arm.c. Counts and cycle costs are accumulated per
  word over [arm_profile_base, arm_profile_base + arm_profile_words*4).
  Profiling is off while arm_profile_counts is NULL.*/
extern uint32_t *arm_profile_counts;
extern uint64_t *arm_profile_cycles;
extern uint32_t arm_profile_base;
extern uint32_t arm_profile_words;
extern uint64_t arm_profile_total_cycles;
extern uint64_t arm_profile_total_instructions;
