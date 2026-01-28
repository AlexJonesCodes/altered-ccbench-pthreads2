/*   
 *   File: ccbench.c
 *   Author: Vasileios Trigonakis <vasileios.trigonakis@epfl.ch>
 *   Description: the main functionality of ccbench
 *   ccbench.c is part of ccbench
 *
 * The MIT License (MIT)
 *
 * Copyright (C) 2013  Vasileios Trigonakis
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy of
 * this software and associated documentation files (the "Software"), to deal in
 * the Software without restriction, including without limitation the rights to
 * use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
 * the Software, and to permit persons to whom the Software is furnished to do so,
 * subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
 * FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
 * COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
 * IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
 * CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 *
 */

#include "ccbench.h"
#include <ctype.h>
#include <limits.h>  
#include <sys/mman.h>
#include <numa.h>
#include <numaif.h>
#include <float.h>   /* for DBL_MAX */
#include <sched.h>   /* for CPU_SET, sched_getcpu */

__thread uint8_t ID;
__thread unsigned long* seeds;

#if defined(__tile__)
cpu_set_t cpus;
#endif

moesi_type_t test_test = DEFAULT_TEST;
moesi_type_t test_test2 = -1;
size_t test_reps = DEFAULT_REPS;
size_t test_rows;             // number of rows
size_t *test_cols;            // array of column counts per row
size_t **test_num_array;
size_t core_rows;             // number of rows
size_t *core_cols;            // array of column counts per row
size_t **test_cores_array;
uint32_t test_cores = DEFAULT_CORES;
size_t *core_for_rank = NULL;    /* flattened physical core id per rank */
size_t *test_for_rank = NULL;    /* test id per rank */
size_t *role_for_rank = NULL;    /* role index within group per rank */
size_t *group_for_rank = NULL;   /* group index per rank */
static int seed_core = -1;     /* physical core id that primes the line */
static int seed_rank = -1;     /* flattened rank (computed after -x mapping) */
static int have_seeder_thread = 0;  /* seed core not in -x => spawn helper */
static pthread_t seeder_pth;        /* helper thread handle */
static int opt_mlock = 0;
int opt_numa = 1;
static int test_backoff = 0;
static uint32_t test_backoff_max = 1024;
static size_t **backoff_max_array = NULL;
static size_t backoff_rows = 0;
static size_t *backoff_cols = NULL;
static uint32_t *backoff_max_per_rank = NULL;
uint32_t test_core_others = DEFAULT_CORE_OTHERS;
uint32_t test_flush = DEFAULT_FLUSH;
uint32_t test_verbose = DEFAULT_VERBOSE;
uint32_t test_print = DEFAULT_PRINT;
uint32_t test_stride = DEFAULT_STRIDE;
uint32_t test_fence = DEFAULT_FENCE;
uint32_t test_ao_success = DEFAULT_AO_SUCCESS;
size_t   test_mem_size = CACHE_LINE_NUM * sizeof(cache_line_t);
uint32_t test_cache_line_num = CACHE_LINE_NUM;
uint32_t test_lfence = DEFAULT_LFENCE;
uint32_t test_sfence = DEFAULT_SFENCE;

/* NUMA placement: selected node for the cache line and allocation origin */
static int seed_node = -1;
static int cache_line_from_numa = 0;

/* Per-thread and per-repetition winner tracking (generalised for all tests) */
static uint32_t* win_counts_per_rank = NULL;   /* size: test_cores */
static uint32_t* first_winner_per_rep = NULL;  /* size: test_reps, UINT32_MAX means unclaimed */
/* Round start (common t0 per repetition) and per-thread, per-rep latency from t0 to success */
static ticks* round_start = NULL;                 /* size: test_reps */
static uint64_t* common_latency_cycles = NULL;    /* size: test_cores * test_reps */
static uint64_t* cas_attempts_per_rank = NULL;
static uint64_t* cas_failures_per_rank = NULL;
static uint64_t* cas_successes_per_rank = NULL;
/* Record B4->success for this thread and repetition (only once). */
static inline void rec_success(uint64_t rep)
{
  if (!common_latency_cycles || !round_start) return;
  size_t idx = (size_t) ID * test_reps + (size_t) rep;
  if (common_latency_cycles[idx] == 0) {
    ticks t_end = getticks();
    common_latency_cycles[idx] = (uint64_t)(t_end - round_start[rep]);
  }
}


/* Thread-local current repetition index for ops that don't take 'reps' param */
__thread uint64_t current_rep_idx = 0;

/* Attempt to claim victory for this repetition; first thread to claim wins */
static inline void race_try_win(uint64_t rep_idx)
{
  if (!first_winner_per_rep) return;
  if (rep_idx >= test_reps) return;

  /* Atomically set from UINT32_MAX (unclaimed) to our thread ID */
  uint32_t expected = UINT32_MAX;
  if (__sync_bool_compare_and_swap(&first_winner_per_rep[rep_idx], expected, (uint32_t) ID))
    {
      /* We won this repetition */
      if (win_counts_per_rank)
        {
          __sync_fetch_and_add(&win_counts_per_rank[ID], 1);
        }
    }
}

/* Convenience macro: pick reps param if available, else use current_rep_idx */
#define RACE_TRY_WITH_REP(rep_expr) race_try_win((uint64_t)(rep_expr))
#define RACE_TRY() race_try_win(current_rep_idx)

typedef struct
{
  abs_deviation_t store[PFD_NUM_STORES];
  uint8_t store_valid[PFD_NUM_STORES];
} core_summary_t;

static core_summary_t* core_summaries;

typedef struct
{
  volatile cache_line_t* cache_line;
  uint32_t rank;
} worker_args_t;

static void* run_benchmark(void* arg);
typedef struct {
  volatile cache_line_t* cache_line;
} seeder_args_t;

static void* seeder_main(void* arg);

static void store_0(volatile cache_line_t* cache_line, volatile uint64_t reps);
static void store_0_no_pf(volatile cache_line_t* cache_line, volatile uint64_t reps);
static void store_0_eventually(volatile cache_line_t* cl, volatile uint64_t reps);
static void store_0_eventually_pfd1(volatile cache_line_t* cl, volatile uint64_t reps);

static uint64_t load_0(volatile cache_line_t* cache_line, volatile uint64_t reps);
static uint64_t load_next(volatile uint64_t* cl, volatile uint64_t reps);
static uint64_t load_0_eventually(volatile cache_line_t* cl, volatile uint64_t reps);
static uint64_t load_0_eventually_no_pf(volatile cache_line_t* cl);

static void invalidate(volatile cache_line_t* cache_line, uint64_t index, volatile uint64_t reps);
static uint32_t cas(volatile cache_line_t* cache_line, volatile uint64_t reps);
static uint32_t cas_0_eventually(volatile cache_line_t* cache_line, volatile uint64_t reps);
static uint32_t cas_no_pf(volatile cache_line_t* cache_line, volatile uint64_t reps);
static uint32_t fai(volatile cache_line_t* cache_line, volatile uint64_t reps);
static uint8_t tas(volatile cache_line_t* cl, volatile uint64_t reps);
static uint32_t swap(volatile cache_line_t* cl, volatile uint64_t reps);
static uint32_t cas_until_success(volatile cache_line_t* cache_line, volatile uint64_t reps);

static size_t parse_size(char* optarg);
static void create_rand_list_cl(volatile uint64_t* list, size_t n);
static void collect_core_stats(uint32_t store, uint32_t num_vals, uint32_t num_print);
static void free_jagged(size_t **a, size_t *cols, size_t rows);

/* command-line long options */
static struct option long_options[] = {
	{"help",                     no_argument,       NULL, 'h'},
	{"repetitions",              required_argument, NULL, 'r'},
	{"test",                     required_argument, NULL, 't'},
	{"stride",                   required_argument, NULL, 's'},
	{"cores",                    required_argument, NULL, 'c'},
	{"cores_array",              required_argument, NULL, 'x'},
	{"seed",                     required_argument, NULL, 'b'},
	{"mem-size",                 required_argument, NULL, 'm'},
	{"backoff",                  no_argument,       NULL, 'B'},
	{"backoff-max",              required_argument, NULL, 'M'},
	{"backoff-array",            required_argument, NULL, 'A'},
	{"flush",                    no_argument,       NULL, 'f'},
	{"success",                  no_argument,       NULL, 'u'},
	{"verbose",                  no_argument,       NULL, 'v'},
	{"mlock",                    no_argument,       NULL, 'K'},
	{"no-numa",                  no_argument,       NULL, 'n'},
	{"print",                    required_argument, NULL, 'p'},
	{NULL, 0, NULL, 0}
};

int main(int argc, char** argv)
{
	int i;
	char c;
	while (1)
		{
			i = 0;
			c = getopt_long(argc, argv, "r:t:c:x:s:b:fe:m:uvp:Kno:BM:A:", long_options, &i);

			if (c == -1)
				break;

			if (c == 0 && long_options[i].flag == 0)
				c = long_options[i].val;

			switch (c)
				{
				case 0:
					/* Flag is automatically set */
					break;
				case 'h':
	  printf("ccbench  Copyright (C) 2013  Vasileios Trigonakis <vasileios.trigonakis@epfl.ch>\n"
		 "This program comes with ABSOLUTELY NO WARRANTY.\n"
		 "This is free software, and you are welcome to redistribute it under certain conditions.\n\n"
		 "ccbecnh is an application for measuring the cache-coherence latencies, i.e., the latencies of\n"
		 "of loads, stores, CAS, FAI, TAS, and SWAP\n"
		 "\n"
		 "Usage:\n"
		 "  ./ccbench [options...]\n"
		 "\n"
		 "Options:\n"
		 "  -h, --help\n"
		 "        Print this message\n"
		 "  -c, --cores <int>\n"
		 "        Number of cores to run the test on (default=" XSTR(DEFAULT_CORES) ")\n"
		 "  -r, --repetitions <int>\n"
		 "        Repetitions of the test case (default=" XSTR(DEFAULT_REPS) ")\n"
		 "  -t, --test <int>\n"
		 "        Test case to run (default=" XSTR(DEFAULT_TEST) "). See below for supported events\n"
		 "  -x, --cores_array <int>\n"
		 "        supply an array of cores to use. eg [1,2,3,4]"
		 "  -f, --flush\n"
		 "        Perform a cache line flush before the test (default=" XSTR(DEFAULT_FLUSH) ")\n"
		 "  -s, --stride <int>\n"
		 "        What stride size to use when accessing the cache line(s) (default=" XSTR(DEFAULT_STRIDE) ")\n"
		 "        The application draws a random number X in the [0..(stride-1)] range and applies the target\n"
		 "        operation on this random cache line. The operation is completed when X=0. The stride is used\n"
		 "        in order to fool the hardware prefetchers that could hide the latency we want to measure.\n"
		 "  -e, --fence <int>\n"
		 "        What memory barrier (fence) lvl to use (default=" XSTR(DEFAULT_FENCE) ")\n"
		 "        0 = no fences / 1 = load-store fences / 2 = full fences / 3 = load-none fences / 4 = none-store fences\n"
		 "        5 = full-none fences / 6 = none-full fences / 7 = full-store fences / 8 = load-full fences \n"
		 "  -m, --mem-size <int>\n"
		 "        What memory size to use (in cache lines) (default=" XSTR(CACHE_LINE_NUM) ")\n"
		 "  -B, --backoff\n"
		 "        Enable exponential backoff after CAS_UNTIL_SUCCESS failures\n"
		 "  -M, --backoff-max <int>\n"
		 "        Max pause iterations for backoff (default=1024)\n"
		 "  -A, --backoff-array <array>\n"
		 "        Per-thread backoff max array, e.g. [1,2,4,8] (length must match threads)\n"
		 "  -u, --success\n"
		 "        Make all atomic operations be successfull (e.g, TAS_ON_SHARED)\n"
		 "  -n, --no-numa\n"
		 "        Disable NUMA placement/binding (enabled by default if libnuma is present)\n"
		 "  -v, --verbose\n"
		 "        Verbose printing of results (default=" XSTR(DEFAULT_VERBOSE) ")\n"
		 "  -p, --print <int>\n"
		 "        If verbose, how many results to print (default=" XSTR(DEFAULT_PRINT) ")\n"
		 );
	  printf("Supported events: \n");
	  int ar;
	  for (ar = 0; ar < NUM_EVENTS; ar++)
	    {
	      printf("      %2d - %s\n", ar, moesi_type_des[ar]);
	    }

	  exit(0);
	  break;
	case 'r':
	  test_reps = atoi(optarg);
	  break;
	case 'K':
		opt_mlock = 1;
		break;
	case 'n': /* --no-numa: disable NUMA placement/binding at runtime */
		opt_numa = 0;
	break;
	case 'b': /* --seed: physical core id where the line is primed each repetition */
		seed_core = atoi(optarg);
		break;
	case 'B': /* --backoff: enable exponential backoff for CAS_UNTIL_SUCCESS */
		test_backoff = 1;
		break;
	case 'M': /* --backoff-max: cap for backoff pause iterations */
		test_backoff_max = (uint32_t) atoi(optarg);
		if (test_backoff_max < 1) test_backoff_max = 1;
		break;
	case 'A': /* --backoff-array: per-thread backoff max array */
		if (parse_jagged_array(optarg, &backoff_max_array, &backoff_rows, &backoff_cols) != 0) {
			fprintf(stderr, "Invalid format for -A\n");
			exit(EXIT_FAILURE);
		}
		test_backoff = 1;
		break;
	case 't':
		if ((parse_jagged_array(optarg, &test_num_array, &test_rows, &test_cols) != 0) || test_rows != 1){
			fprintf(stderr, "Invalid format for -t\n");
			exit(EXIT_FAILURE);
		}
		break;
	case 'x': // user provided a core array
		if (parse_jagged_array(optarg, &test_cores_array, &core_rows, &core_cols) != 0) {
			fprintf(stderr, "Invalid format for -x\n");
			exit(EXIT_FAILURE);
		}
		break;
	case 'o':
	  test_core_others = atoi(optarg);
	  break;
	case 'f':
	  test_flush = 1;
	  break;
	case 's':
	  test_stride = pow2roundup(atoi(optarg));
	  break;
	case 'e':
	  test_fence = atoi(optarg);
	  break;
	case 'm':
	  test_mem_size = parse_size(optarg);
	  printf("Data size : %zu KiB\n", test_mem_size / 1024);
	  break;
	case 'u':
	  test_ao_success = 1;
	  break;
	case 'v':
	  test_verbose = 1;
	  break;
	case 'p':
	  test_verbose = 1;
	  test_print = atoi(optarg);
	  break;
	case '?':
	  printf("Use -h or --help for help\n");
	  exit(0);
	default:
	  exit(1);
	}
    }

	if (test_rows == 1 && core_rows == 1 && test_cols[0] == core_cols[0]) {
		printf("Per-thread ops in group 0:\n");
		for (size_t j = 0; j < core_cols[0]; j++) {
			printf("  Test %zu on core %zu\n", test_num_array[0][j], test_cores_array[0][j]);
		}
		printf("\n");
	} else {
		for (i = 0; i < (int)core_rows; i++) {
			size_t t_for_group = test_num_array[0][i];
			printf("Test %zu runs on cores: ", t_for_group);
			for (size_t j = 0; j < core_cols[i]; j++) {
				printf("%zu", test_cores_array[i][j]);
				if (j + 1 < core_cols[i]) printf(", ");
			}
			printf("\n");
		}
		printf("\n");
	}

  test_cache_line_num = test_mem_size / sizeof(cache_line_t);

  if ((test_test == STORE_ON_EXCLUSIVE || test_test == STORE_ON_INVALID || test_test == LOAD_FROM_INVALID
       || test_test == LOAD_FROM_EXCLUSIVE || test_test == LOAD_FROM_SHARED) && !test_flush)
    {
      assert((test_reps * test_stride) <= test_cache_line_num);
    }

  if (test_test != LOAD_FROM_MEM_SIZE)
    {
      assert(test_stride < test_cache_line_num);
    }


  ID = 0;
  // printf("test: %20s  / #cores: %d / #repetitions: %d / stride: %d (%u kiB)", moesi_type_des[test_test], test_cores, test_reps, test_stride, (64 * test_stride) / 1024);
  if (test_flush)
    {
      printf(" / flush");
    }

  printf("  / fence: ");

  switch (test_fence)
    {
    case 1:
      printf(" load & store");
      test_lfence = test_sfence = 1;
      break;
    case 2:
      printf(" full");
      test_lfence = test_sfence = 2;
      break;
    case 3:
      printf(" load");
      test_lfence = 1;
      test_sfence = 0;
      break;
    case 4:
      printf(" store");
      test_lfence = 0;
      test_sfence = 1;
      break;
    case 5:
      printf(" full/none");
      test_lfence = 2;
      test_sfence = 0;
      break;
    case 6:
      printf(" none/full");
      test_lfence = 0;
      test_sfence = 2;
      break;
    case 7:
      printf(" full/store");
      test_lfence = 2;
      test_sfence = 1;
      break;
    case 8:
      printf(" load/full");
      test_lfence = 1;
      test_sfence = 2;
      break;    
    case 9:
      printf(" double write");
      test_lfence = 0;
      test_sfence = 3;
      break;
    default:
      printf(" none");
      test_lfence = test_sfence = 0;
      break;
    }
	printf("\n");

	/* Build per-rank mappings from jagged core arrays and test ids. */
	if (test_cores_array == NULL) {
		test_cores = DEFAULT_CORES;
		core_for_rank = (size_t*) malloc(sizeof(size_t) * test_cores);
		test_for_rank = (size_t*) malloc(sizeof(size_t) * test_cores);
		role_for_rank = (size_t*) malloc(sizeof(size_t) * test_cores);
		group_for_rank = (size_t*) malloc(sizeof(size_t) * test_cores);
		if (!core_for_rank || !test_for_rank || !role_for_rank || !group_for_rank) {
			perror("malloc");
			exit(1);
		}
		for (size_t rr = 0; rr < test_cores; rr++) {
			core_for_rank[rr] = rr;
			test_for_rank[rr] = test_test;
			role_for_rank[rr] = 0;
			group_for_rank[rr] = 0;
		}
	} else {
		/* compute total number of ranks (flatten all groups) */
		size_t total = 0;
		for (size_t g = 0; g < core_rows; g++) {
			total += core_cols[g];
		}
		test_cores = (uint32_t) total;

		core_for_rank = (size_t*) malloc(sizeof(size_t) * test_cores);
		test_for_rank = (size_t*) malloc(sizeof(size_t) * test_cores);
		role_for_rank = (size_t*) malloc(sizeof(size_t) * test_cores);
		group_for_rank = (size_t*) malloc(sizeof(size_t) * test_cores);
		if (!core_for_rank || !test_for_rank || !role_for_rank || !group_for_rank) {
			perror("malloc");
			exit(1);
		}

		size_t idx = 0;
		for (size_t g = 0; g < core_rows; g++) {
			size_t assigned_test = (size_t) test_test;
			if (test_num_array != NULL) {
				if (test_rows == 1 && core_rows == 1 && test_cols[0] == core_cols[0]) {
				/* Per-thread ops: one group, tests list length equals group size */
				assigned_test = (size_t) test_test; /* placeholder, overridden per j below */
				} else if (test_rows == 1) {
				/* One test per group (by position g) */
				if (g < test_cols[0]) {
					assigned_test = test_num_array[0][g];
				} else {
					fprintf(stderr, "Mismatch between -t and -x shapes\n");
					exit(EXIT_FAILURE);
				}
				} else if (test_rows == core_rows) {
				if (test_cols[g] >= 1) {
					assigned_test = test_num_array[g][0];
				} else {
					fprintf(stderr, "Invalid -t content\n");
					exit(EXIT_FAILURE);
				}
				} else {
				fprintf(stderr, "Invalid -t shape\n");
				exit(EXIT_FAILURE);
				}
			}

			for (size_t j = 0; j < core_cols[g]; j++) {
				core_for_rank[idx] = test_cores_array[g][j];
				if (test_num_array != NULL && test_rows == 1 && core_rows == 1 && test_cols[0] == core_cols[0]) {
				/* per-thread ops list */
				test_for_rank[idx] = (size_t) test_num_array[0][j];
				} else {
				test_for_rank[idx] = assigned_test;
				}
				role_for_rank[idx] = j;
				group_for_rank[idx] = g;
				idx++;
			}

		}
	}

	int uses_cas_until_success = 0;
	for (size_t r = 0; r < test_cores; r++) {
		if ((moesi_type_t) test_for_rank[r] == CAS_UNTIL_SUCCESS) {
			uses_cas_until_success = 1;
			break;
		}
	}

	if (backoff_max_array) {
		if (backoff_rows != 1 || backoff_cols[0] != test_cores) {
			fprintf(stderr, "Mismatch between --backoff-array and thread count\n");
			exit(EXIT_FAILURE);
		}
		backoff_max_per_rank = (uint32_t*) malloc(sizeof(uint32_t) * test_cores);
		if (!backoff_max_per_rank) { perror("malloc"); exit(1); }
		for (size_t r = 0; r < test_cores; r++) {
			size_t v = backoff_max_array[0][r];
			if (v < 1) v = 1;
			backoff_max_per_rank[r] = (uint32_t) v;
		}
		test_backoff = 1;
	}

	/* Allocate winner tracking arrays */
	win_counts_per_rank = (uint32_t*) calloc(test_cores, sizeof(uint32_t));
	if (!win_counts_per_rank) { perror("calloc"); exit(1); }

	first_winner_per_rep = (uint32_t*) malloc(sizeof(uint32_t) * test_reps);
	if (!first_winner_per_rep) { perror("malloc"); exit(1); }
	round_start = (ticks*) calloc(test_reps, sizeof(ticks));
	if (!round_start) { perror("calloc"); exit(1); }

	common_latency_cycles = (uint64_t*) calloc((size_t)test_cores * test_reps, sizeof(uint64_t));
	if (!common_latency_cycles) { perror("calloc"); exit(1); }

	if (uses_cas_until_success) {
		cas_attempts_per_rank = (uint64_t*) calloc(test_cores, sizeof(uint64_t));
		cas_failures_per_rank = (uint64_t*) calloc(test_cores, sizeof(uint64_t));
		cas_successes_per_rank = (uint64_t*) calloc(test_cores, sizeof(uint64_t));
		if (!cas_attempts_per_rank || !cas_failures_per_rank || !cas_successes_per_rank) {
			perror("calloc");
			exit(1);
		}
	}

	for (size_t i_init = 0; i_init < test_reps; i_init++)
	{
		first_winner_per_rep[i_init] = UINT32_MAX; /* unclaimed */
	}

	if (seed_core >= 0) {
		seed_rank = -1;
		for (size_t r = 0; r < test_cores; r++) {
			if ((int)core_for_rank[r] == seed_core) { seed_rank = (int) r; break; }
		}
		if (seed_rank < 0) {
			have_seeder_thread = 1; /* prime outside -x */
		}
	}

	#ifdef PLATFORM_NUMA
		/* Resolve seed NUMA node from seed_core (libnuma, user-space) */
		if (opt_numa && seed_core >= 0 && numa_available() != -1) {
			seed_node = numa_node_of_cpu(seed_core);
			if (seed_node >= 0) {
				printf("Seed core %d is on NUMA node %d\n", seed_core, seed_node);
			}
		}
	#endif

	barriers_init(test_cores);

	/* Reconfigure per-group barriers so each per-group barrier expects only
	 * the number of participants in that group (core_cols[g]). This prevents
	 * deadlock where a barrier initialized for all threads would wait for
	 * threads that never call it.
	 */
	for (size_t g = 0; g < core_rows; g++) {
		for (size_t k = 0; k < PER_GROUP_SLOTS; k++) {
			uint32_t bar_idx = (uint32_t)(PER_GROUP_BASE + g * PER_GROUP_SLOTS + k);
			if (bar_idx < NUM_BARRIERS) {
				barrier_set_participants(bar_idx, (uint64_t)core_cols[g], test_cores);
			}
		}
	}

	if (have_seeder_thread) {
		barrier_set_participants(5, (uint64_t)(test_cores + 1), test_cores);
	}

	/* First-touch on seed's NUMA node: pin main to seed_core before cache_line_open() */
	if (seed_core >= 0) {
		set_cpu(seed_core);
		printf("Main pinned to seed core %d for first-touch placement\n", seed_core);
		}

		/* Now allocate the test buffer */
		volatile cache_line_t* cache_line = cache_line_open();

		#ifdef PLATFORM_NUMA
		/* Diagnostic: print the current NUMA node of the first page of cache_line */
		if (opt_numa && numa_available() != -1) {
			int status = -1;
			void* pages[1] = { (void*) cache_line };
			if (move_pages(0, 1, pages, NULL, &status, 0) == 0) {
				printf("Initial page node for cache_line: %d\n", status);
			} else {
				perror("move_pages");
			}
		}
		#endif


  core_summaries = (core_summary_t*) calloc(test_cores, sizeof(core_summary_t));
  if (core_summaries == NULL)
    {
      perror("calloc");
      exit(1);
    }

  worker_args_t* args = (worker_args_t*) calloc(test_cores, sizeof(worker_args_t));
  if (args == NULL)
    {
      perror("calloc");
      exit(1);
    }

  pthread_t* threads = NULL;
  if (test_cores > 1)
    {
      threads = (pthread_t*) calloc(test_cores - 1, sizeof(pthread_t));
      if (threads == NULL)
        {
          perror("calloc");
          exit(1);
        }
    }

	seeder_args_t* sargs = NULL;
	if (have_seeder_thread) {
	sargs = (seeder_args_t*) malloc(sizeof(seeder_args_t));
	if (!sargs) { perror("malloc"); exit(1); }
	sargs->cache_line = cache_line;
	int rc = pthread_create(&seeder_pth, NULL, seeder_main, sargs);
	if (rc != 0) { errno = rc; perror("pthread_create seeder"); exit(1); }
	}

  int rank;
  for (rank = 1; rank < test_cores; rank++)
    {
      args[rank].cache_line = cache_line;
      args[rank].rank = rank;
      int rc = pthread_create(&threads[rank - 1], NULL, run_benchmark, &args[rank]);
      if (rc != 0)
        {
          errno = rc;
          perror("pthread_create");
          exit(1);
        }
    }

  args[0].cache_line = cache_line;
  args[0].rank = 0;
  run_benchmark(&args[0]);

  for (rank = 1; rank < test_cores; rank++)
    {
      int rc = pthread_join(threads[rank - 1], NULL);
      if (rc != 0)
        {
          errno = rc;
          perror("pthread_join");
          exit(1);
        }
    }
	if (have_seeder_thread) {
		int rc = pthread_join(seeder_pth, NULL);
		if (rc != 0) {
			errno = rc; perror("pthread_join seeder"); 
		}
	}
	if (sargs) {
		free(sargs); sargs = NULL; 
	}

  cache_line_close(cache_line);
  barriers_term();
  free(core_summaries);
  free(args);
  free(threads);
  if (common_latency_cycles) { free(common_latency_cycles); common_latency_cycles = NULL; }
  if (round_start) { free(round_start); round_start = NULL; }
  if (first_winner_per_rep) {
	free(first_winner_per_rep);
	first_winner_per_rep = NULL;
	}
	if (win_counts_per_rank) {
	free(win_counts_per_rank);
	win_counts_per_rank = NULL;
	}
	if (cas_attempts_per_rank) { free(cas_attempts_per_rank); cas_attempts_per_rank = NULL; }
	if (cas_failures_per_rank) { free(cas_failures_per_rank); cas_failures_per_rank = NULL; }
	if (cas_successes_per_rank) { free(cas_successes_per_rank); cas_successes_per_rank = NULL; }

	if (core_for_rank) {
		free(core_for_rank);
		core_for_rank = NULL;
	}
	if (test_for_rank) {
		free(test_for_rank);
		test_for_rank = NULL;
	}
	if (role_for_rank) {
		free(role_for_rank);
		role_for_rank = NULL;
	}
	if (group_for_rank) {
		free(group_for_rank);
		group_for_rank = NULL;
	}
	if (backoff_max_per_rank) {
		free(backoff_max_per_rank);
		backoff_max_per_rank = NULL;
	}

	/* free parsed jagged arrays if they were allocated */
	if (test_num_array) {
		free_jagged(test_num_array, test_cols, test_rows);
		test_num_array = NULL;
		test_cols = NULL;
		test_rows = 0;
	}
	if (test_cores_array) {
		free_jagged(test_cores_array, core_cols, core_rows);
		test_cores_array = NULL;
		core_cols = NULL;
		core_rows = 0;
	}
	if (backoff_max_array) {
		free_jagged(backoff_max_array, backoff_cols, backoff_rows);
		backoff_max_array = NULL;
		backoff_cols = NULL;
		backoff_rows = 0;
	}
  return 0;

}

static void* seeder_main(void* arg)
{
  seeder_args_t* a = (seeder_args_t*) arg;
  volatile cache_line_t* cache_line = a->cache_line;

  /* Pin this helper thread to the seed core */
  set_cpu(seed_core);

	for (uint64_t reps = 0; reps < test_reps; reps++) {
		uint8_t o = (uint8_t)(reps & 0x1);
		cache_line->word[0] = o;
		_mm_mfence();

		if (first_winner_per_rep) {
			first_winner_per_rep[reps] = UINT32_MAX;
			_mm_mfence();
		}

		if (round_start) {
			round_start[reps] = getticks();
			_mm_mfence();
		}

		B4; /* release contenders */
	}

  return NULL;
}

static void*
run_benchmark(void* arg)
{
  worker_args_t* worker = (worker_args_t*) arg;
  uint32_t rank = worker->rank;
  volatile cache_line_t* cache_line = worker->cache_line;

	ID = rank;
	seeds = seed_rand();
	size_t core = 0;
	size_t role = 0;
	moesi_type_t my_test = test_test;
	if (core_for_rank) {
		core = core_for_rank[rank];
		role = role_for_rank[rank];
		my_test = (moesi_type_t) test_for_rank[rank];
	} else {
		core = rank;
		role = 0;
		my_test = test_test;
	}

	/* lightweight startup info removed (cleanup) */

#if defined(NIAGARA)
  if (test_cores <= 8 && test_cores > 3)
    {
	if (role == 0)
	{
	  PRINT(" ** spreading the 8 threads on the 8 real cores");
	}
      core = ID * 8;
    }
#endif

  set_cpu(core);
  { const char* tname = (my_test >= 0 && my_test < NUM_EVENTS) ? moesi_type_des[my_test] : "UNKNOWN"; 
	printf("Requested core: %zu, now running on cpu: %d, test is: %d (%s)\n", core, sched_getcpu(), (int) my_test, tname); }
  
#if defined(__tile__)
  tmc_cmem_init(0);		/*   initialize shared memory */
#endif  /* TILERA */

  volatile uint64_t* cl = (volatile uint64_t*) cache_line;

  B0;
  if (ID < test_cores)
    {
      PFDINIT(test_reps);
    }
  B0;

  /* Local warmup: touch the target line a few times to prime TLB/L1 */
  for (int w = 0; w < 1024; w++) {
    (void) cache_line->word[0];
    _mm_pause();
  }
  _mm_mfence();


  /* /\********************************************************************************* */
  /*  *  main functionality */
  /*  *********************************************************************************\/ */

  uint64_t sum = 0;

  volatile uint64_t reps;
  for (reps = 0; reps < test_reps; reps++)
    {
      if (test_flush)
	{
	  _mm_mfence();
	  _mm_clflush((void*) cache_line);
	  _mm_mfence();
	}

	B0;            /* BARRIER 0 */
	/* Seed mode: either seed is inside -x (seed_rank >= 0) or we have a helper seeder thread */
	if (seed_rank >= 0 || have_seeder_thread) {
		int i_am_seeder = (seed_rank >= 0 && (int)ID == seed_rank);

		if (i_am_seeder) {
			/* In-thread priming when seed is part of -x: leave value = o */
			uint8_t o = (uint8_t)(reps & 0x1);
			cache_line->word[0] = o;
			_mm_mfence();
			if (first_winner_per_rep) {
			first_winner_per_rep[reps] = UINT32_MAX;
			_mm_mfence();
			}
			if (round_start) {
			round_start[reps] = getticks();
			_mm_mfence();
			}
		}

		/* Start contention phase: release all contenders (including the seeder) */
		B4;

		/* Dispatch this thread's assigned test (seeder joins the race) */
		switch (my_test) {
			case CAS:        sum += cas_0_eventually(cache_line, reps); break;  /* 12 */
			case FAI:        sum += fai(cache_line, reps); break;                /* 13 */
			case TAS:        sum += tas(cache_line, reps);
							_mm_mfence(); cache_line->word[0] = 0; break;       /* keep TAS re-entrant */
			case SWAP:       sum += swap(cache_line, reps); break;               /* 15 */
			case CAS_UNTIL_SUCCESS:
							sum += cas_until_success(cache_line, reps); break;  /* 34 */

			case STORE_ON_MODIFIED:
			case STORE_ON_MODIFIED_NO_SYNC:
			case STORE_ON_EXCLUSIVE:
			case STORE_ON_SHARED:
			case STORE_ON_OWNED_MINE:
			case STORE_ON_OWNED:
			case STORE_ON_INVALID:
			store_0_eventually(cache_line, reps);
			break;

			case LOAD_FROM_MODIFIED:
			case LOAD_FROM_EXCLUSIVE:
			case LOAD_FROM_SHARED:
			case LOAD_FROM_OWNED:
			case LOAD_FROM_INVALID:
			case LOAD_FROM_L1:
			sum += load_0_eventually(cache_line, reps);
			break;

			default:
			/* keep counts aligned */
			PFDI(0); asm volatile(""); PFDO(0, reps);
			break;
		}

		/* Optional per-group sync to keep loop structure */
		B1;
	continue; /* skip the normal test switch for this repetition */
	}


	  current_rep_idx = reps;

	switch (my_test)
	{
	case STORE_ON_MODIFIED: /* 0 */
	  {
		if (role == 0)
	      {
		store_0_eventually(cache_line, reps);
		B1;    /* BARRIER 1 */
	      }
		else if (role == 1)
	      {
		B1;    /* BARRIER 1 */
		store_0_eventually(cache_line, reps);
	      }
	    else
	      {
		B1;    /* BARRIER 1 */
	      }
	    break;
	  }
	case STORE_ON_MODIFIED_NO_SYNC: // 1
	  {
		if (role == 0 || role == 1 || role == 2)
	      {
		store_0(cache_line, reps);
	      }
	    else
	      {
		store_0_no_pf(cache_line, reps);
	      }
	    break;
	  }
	case STORE_ON_EXCLUSIVE: /* 2 */
	  {
		if (role == 0)
	      {
		sum += load_0_eventually(cache_line, reps);
		B1;    /* BARRIER 1 */
	      }
		else if (role == 1)
	      {
		B1;    /* BARRIER 1 */
		store_0_eventually(cache_line, reps);
	      }
	    else
	      {
		B1;    /* BARRIER 1 */
	      }

	    if (!test_flush)
	      {
		cache_line += test_stride;
	      }
	    break;
	  }
	case STORE_ON_SHARED:	/* 3 */
	  {
		if (role == 0)
	      {
		sum += load_0_eventually(cache_line, reps);
		B1;            /* BARRIER 1 */
		B2;            /* BARRIER 2 */
	      }
		else if (role == 1)
	      {
		B1;            /* BARRIER 1 */
		B2;            /* BARRIER 2 */
		store_0_eventually(cache_line, reps);
	      }
		else if (role == 2)
	      {
		B1;            /* BARRIER 1 */
		sum += load_0_eventually(cache_line, reps);
		B2;            /* BARRIER 2 */
	      }
	    else
	      {
		B1;            /* BARRIER 1 */
		sum += load_0_eventually_no_pf(cache_line);
		B2;            /* BARRIER 2 */
	      }
	    break;
	  }
	case STORE_ON_OWNED_MINE: /* 4 */
	{
		if (role == 0)
		{
			B1;            /* BARRIER 1 */
			sum += load_0_eventually(cache_line, reps);
			B2;            /* BARRIER 2 */
		}
		else if (role == 1)
		{
			store_0_eventually(cache_line, reps);
			B1;            /* BARRIER 1 */
			B2;            /* BARRIER 2 */
			store_0_eventually_pfd1(cache_line, reps);
		}
		else
		{
			B1;            /* BARRIER 1 */
			sum += load_0_eventually_no_pf(cache_line);
			B2;            /* BARRIER 2 */
		}
		break;
	}

	case STORE_ON_OWNED:	/* 5 */
	  {
		if (role == 0)
	      {
		store_0_eventually(cache_line, reps);
		B1;            /* BARRIER 1 */
		B2;            /* BARRIER 2 */
	      }
		else if (role == 1)
	      {
		B1;            /* BARRIER 1 */
		sum += load_0_eventually(cache_line, reps);
		B2;            /* BARRIER 2 */
		store_0_eventually_pfd1(cache_line, reps);
	      }
	    else
	      {
		B1;            /* BARRIER 1 */
		sum += load_0_eventually_no_pf(cache_line);
		B2;            /* BARRIER 2 */
	      }
	    break;
	  }
	case STORE_ON_INVALID:	/* 6 */
	  {
		if (role == 0)
	      {
		B1;
		/* store_0_eventually(cache_line, reps); */
		store_0(cache_line, reps);
		if (!test_flush)
		  {
		    cache_line += test_stride;
		  }
	      }
		else if (role == 1)
	      {
		invalidate(cache_line, 0, reps);
		if (!test_flush)
		  {
		    cache_line += test_stride;
		  }
		B1;
	      }
	    else
	      {
		B1;
	      }
	    break;
	  }
	case LOAD_FROM_MODIFIED: /* 7 */
	  {
		if (role == 0)
	      {
		store_0_eventually(cache_line, reps);
		B1;        
	      }
		else if (role == 1)
	      {
		B1;            /* BARRIER 1 */
		sum += load_0_eventually(cache_line, reps);
	      }
	    else
	      {
		B1;
	      }
	    break;
	  }
	case LOAD_FROM_EXCLUSIVE: /* 8 */
	  {
		if (role == 0)
	      {
		sum += load_0_eventually(cache_line, reps);
		B1;            /* BARRIER 1 */

		if (!test_flush)
		  {
		    cache_line += test_stride;
		  }
	      }
		else if (role == 1)
	      {
		B1;            /* BARRIER 1 */
		sum += load_0_eventually(cache_line, reps);

		if (!test_flush)
		  {
		    cache_line += test_stride;
		  }
	      }
	    else
	      {
		B1;            /* BARRIER 1 */
	      }
	    break;
	  }

	case LOAD_FROM_OWNED:	/* 10 */
	  {
		if (role == 0)
	      {
		store_0_eventually(cache_line, reps);
		B1;            /* BARRIER 1 */
		B2;            /* BARRIER 2 */
	      }
		else if (role == 1)
	      {
		B1;            /* BARRIER 1 */
		sum += load_0_eventually(cache_line, reps);
		B2;            /* BARRIER 2 */
	      }
		else if (role == 2)
	      {
		B1;            /* BARRIER 1 */
		B2;            /* BARRIER 2 */
		sum += load_0_eventually(cache_line, reps);
	      }
	    else
	      {
		B1;            /* BARRIER 1 */
		B2;            /* BARRIER 2 */
	      }
	    break;
	  }
	case LOAD_FROM_INVALID:	/* 11 */
	  {
		if (role == 0)
	      {
		B1;            /* BARRIER 1 */
		sum += load_0_eventually(cache_line, reps); 		/* sum += load_0(cache_line, reps); */
	      }
		else if (role == 1)
	      {
		invalidate(cache_line, 0, reps);
		B1;            /* BARRIER 1 */
	      }
	    else
	      {
		B1;            /* BARRIER 1 */
	      }

	    if (!test_flush)
	      {
		cache_line += test_stride;
	      }
	    break;
	  }
	case CAS: /* 12 */
	  {
		if (role == 0){
			sum += cas_0_eventually(cache_line, reps);
			B1;			/* BARRIER 1 */
		}
		else if (role == 1){
			B1;			/* BARRIER 1 */
			sum += cas_0_eventually(cache_line, reps);
		}
		else {
			B1;		/* BARRIER 1 */
		}
		break;
	}
	case FAI: /* 13 */
	  {
		if (role == 0)
	      {
		sum += fai(cache_line, reps);
		B1;    /* BARRIER 1 */
	      }
		else if (role == 1)
	      {
		B1;    /* BARRIER 1 */
		sum += fai(cache_line, reps);
	      }
	    else
	      {
		B1;    /* BARRIER 1 */
	      }
	    break;
	  }
	case TAS:		/* 14 */
	  {
		if (role == 0)
	      {
		sum += tas(cache_line, reps);
		B1;    /* BARRIER 1 */
		B2;    /* BARRIER 2 */
	      }
		else if (role == 1)
	      {
		B1;    /* BARRIER 1 */
		sum += tas(cache_line, reps);
		_mm_mfence();
		cache_line->word[0] = 0;
		B2;    /* BARRIER 2 */
	      }
	    else
	      {
		B1;    /* BARRIER 1 */
		B2;    /* BARRIER 2 */
	      }
	    break;
	  }
	case SWAP: /* 15 */
	  {
		if (role == 0)
	      {
		sum += swap(cache_line, reps);
		B1;    /* BARRIER 1 */
	      }
		else if (role == 1)
	      {
		B1;    /* BARRIER 1 */
		sum += swap(cache_line, reps);
	      }
	    else
	      {
		B1;    /* BARRIER 1 */
	      }
	    break;
	  }
	case CAS_ON_MODIFIED: /* 16 */
	  {
		if (role == 0){
			store_0_eventually(cache_line, reps);
			if (test_ao_success)
			{
				cache_line->word[0] = reps & 0x01;
			}
			B1;		/* BARRIER 1 */
		}
		else if (role == 1){
			B1;		/* BARRIER 1 */
			sum += cas_0_eventually(cache_line, reps);
		}
		else {
			B1;
		}
            break;
          }
	case FAI_ON_MODIFIED: /* 17 */
	  {
		if (role == 0)
	      {
		store_0_eventually(cache_line, reps);
		B1;		/* BARRIER 1 */
	      }
		else if (role == 1)
	      {
		B1;		/* BARRIER 1 */
		sum += fai(cache_line, reps);
	      }
	    else
	      {
		B1;		/* BARRIER 1 */
	      }
	    break;
	  }
	case TAS_ON_MODIFIED: /* 18 */
	  {
	    if (role == 0)
	      {
	    	store_0_eventually(cache_line, reps);
	    	if (!test_ao_success)
	    	  {
	    	    cache_line->word[0] = 0xFFFFFFFF;
	    	    _mm_mfence();
	    	  }
	    	B1;		/* BARRIER 1 */
	      }
	    else if (role == 1)
	      {
	    	B1;		/* BARRIER 1 */
	    	sum += tas(cache_line, reps);
	      }
	    else
	      {
		B1;		/* BARRIER 1 */
	      }
	    break;
	  }
	case SWAP_ON_MODIFIED: /* 19 */
	  {
		if (role == 0)
			{
			store_0_eventually(cache_line, reps);
			B1;		/* BARRIER 1 */
			}
		else if (role == 1)
			{
			B1;		/* BARRIER 1 */
			sum += swap(cache_line, reps);
			}
	    else
	      {
		B1;		/* BARRIER 1 */
	      }
	    break;
	  }
	case CAS_ON_SHARED: /* 20 */
		{
			if (role == 0)
				{
					sum += load_0_eventually(cache_line, reps);
					B1;        /* BARRIER 1 */
					B2;        /* BARRIER 2 */
				}
			else if (role == 1)
				{
					B1;        /* BARRIER 1 */
					sum += cas_0_eventually(cache_line, reps);
					B2;        /* BARRIER 2 */
				}
			else if (role == 2)
				{
					B1;        /* BARRIER 1 */
					sum += load_0_eventually(cache_line, reps);
					B2;        /* BARRIER 2 */
				}
			else
				{
					B1;        /* BARRIER 1 */
					sum += load_0_eventually_no_pf(cache_line);
					B2;        /* BARRIER 2 */
				}
			break;
		}
	case FAI_ON_SHARED: /* 21 */
	  {
		if (role == 0)
	      {
		sum += load_0_eventually(cache_line, reps);
		B1;		/* BARRIER 1 */
		B2;		/* BARRIER 2 */
	      }
		else if (role == 1)
	      {
		B1;		/* BARRIER 1 */
		B2;		/* BARRIER 2 */
		sum += fai(cache_line, reps);
	      }
	    else if (role == 2)
	      {
		B1;		/* BARRIER 1 */
		sum += load_0_eventually(cache_line, reps);
		B2;		/* BARRIER 2 */
	      }
	    else
	      {
		B1;		/* BARRIER 1 */
		sum += load_0_eventually_no_pf(cache_line);
		B2;			/* BARRIER 2 */
	      }
	    break;
	  }
	case TAS_ON_SHARED: /* 22 */
	  {
		if (role == 0)
	      {
		if (test_ao_success)
		  {
		    cache_line->word[0] = 0;
		  }
		else
		  {
		    cache_line->word[0] = 0xFFFFFFFF;
		  }
		sum += load_0_eventually(cache_line, reps);
		B1;		/* BARRIER 1 */
		B2;		/* BARRIER 2 */
	      }
	    else if (role == 1)
	      {
		B1;		/* BARRIER 1 */
		B2;		/* BARRIER 2 */
		sum += tas(cache_line, reps);
	      }
			else if (role == 2)
	      {
		B1;		/* BARRIER 1 */
		sum += load_0_eventually(cache_line, reps);
		B2;		/* BARRIER 2 */
	      }
	    else
	      {
		B1;		/* BARRIER 1 */
		sum += load_0_eventually_no_pf(cache_line);
		B2;			/* BARRIER 2 */
	      }
	    break;
	  }
	case SWAP_ON_SHARED: /* 23 */
	  {
	    if (role == 0)
	      {
		sum += load_0_eventually(cache_line, reps);
		B1; 		/* BARRIER 1 */
		B2; 		/* BARRIER 2 */
	      }
	    else if (role == 1)
	      {
		B1; 		/* BARRIER 1 */
		B2; 		/* BARRIER 2 */
		sum += swap(cache_line, reps);
	      }
	    else if (role == 2)
	      {
		B1; 		/* BARRIER 1 */
		sum += load_0_eventually(cache_line, reps);
		B2; 		/* BARRIER 2 */
	      }
	    else
	      {
		B1; 		/* BARRIER 1 */
		sum += load_0_eventually_no_pf(cache_line);
		B2; 			/* BARRIER 2 */
	      }
	    break;
	  }
        case CAS_CONCURRENT: /* 24 */
          {
            if (ID < test_cores)
              {
                sum += cas(cache_line, reps);
              }
            else
              {
                sum += cas_no_pf(cache_line, reps);
              }
            break;
          }
	case FAI_ON_INVALID:	/* 25 */
	  {
		if (role == 0)
			{
			B1; 		/* BARRIER 1 */
			sum += fai(cache_line, reps);
			}
		else if (role == 1)
			{
			invalidate(cache_line, 0, reps);
			B1; 		/* BARRIER 1 */
			}
		else
			{
			B1; 		/* BARRIER 1 */
			}

	    if (!test_flush)
	      {
		cache_line += test_stride;
	      }
	    break;
	  }
	case LOAD_FROM_L1:	/* 26 */
	{
		if (role == 0)
			{
			sum += load_0(cache_line, reps);
			sum += load_0(cache_line, reps);
			sum += load_0(cache_line, reps);
			}
	    break;
	  }
	case LOAD_FROM_MEM_SIZE: /* 27 */
	  {
	    if (ID < test_cores)
	      {
		sum += load_next(cl, reps);
	      }
	  }
	  break;
	case LFENCE:		/* 28 */
	  if (ID < 2)
	    {
	      PFDI(0);
	      _mm_lfence();
	      PFDO(0, reps);
	    }
	  break;
	case SFENCE:		/* 29 */
	  if (ID < 2)
	    {
	      PFDI(0);
	      _mm_sfence();
	      PFDO(0, reps);
	    }
	  break;
	case MFENCE:		/* 30 */
	  if (ID < 2)
	    {
	      PFDI(0);
	      _mm_mfence();
	      PFDO(0, reps);
	    }
	  break;
	case PAUSE:		/* 32 */
	  if (ID < 2)
	    {
	      PFDI(0);
	      _mm_pause();
	      PFDO(0, reps);
	    }
	  break;
	case NOP:		/* 33 */
	  if (ID < 2)
	    {
	      PFDI(0);
	      asm volatile ("nop");
	      PFDO(0, reps);
	    }
	  break;
	case CAS_UNTIL_SUCCESS:  /* 34 */
		{
		if (role == 0) { 
			sum += cas_until_success(cache_line, reps); 
			B1; 
		} 
		else if (role == 1) { 
			B1; 
			sum += cas_until_success(cache_line, reps); 
		} 
		else { 
			B1; 
		} 
		break;
	}

	case PROFILER:		/* 31 */
	default:
	  PFDI(0);
	  asm volatile ("");
	  PFDO(0, reps);
	  break;
	}

      B3;			/* BARRIER 3 */
    }

  if (!test_verbose)
    {
      test_print = 0;
    }

  uint32_t id;
  for (id = 0; id < test_cores; id++)
    {
      if (ID == id && ID < test_cores)
	{
	  switch (test_test)
	    {
	    case STORE_ON_OWNED_MINE:
	    case STORE_ON_OWNED:
	      if (ID < 2)
		{
                  // PRINT(" *** Core %zu ************************************************************************************", core);
		  collect_core_stats(0, test_reps, test_print);
		  if (ID == 1)
		    {
		      collect_core_stats(1, test_reps, test_print);
		    }
		}
	      break;
            case CAS_CONCURRENT:
              // PRINT(" *** Core %zu ************************************************************************************", core);
              collect_core_stats(0, test_reps, test_print);
              break;
	    case LOAD_FROM_L1:
	      if (ID < 1)
		{
                  // PRINT(" *** Core %zu ************************************************************************************", core);
		  collect_core_stats(0, test_reps, test_print);
		}
	      break;
	    case LOAD_FROM_MEM_SIZE:
	      if (ID < test_cores)
		{
                  // PRINT(" *** Core %zu ************************************************************************************", core);
		  collect_core_stats(0, test_reps, test_print);
		}
	      break;
	    default:
              // PRINT(" *** Core %zu ************************************************************************************", core);
	      collect_core_stats(0, test_reps, test_print);
	    }
	}
      B0;
    }
  B10;


	if (rank == 0)    {
	  printf("\n\n");
      printf("---- Cross-core summary ------------------------------------------------------------\n");
      double min_avg = DBL_MAX;
      double max_avg = 0.0;
      double sum_avg = 0.0;
      uint32_t min_core = 0;
      uint32_t max_core = 0;
      uint32_t cores_with_stats = 0;

      uint32_t core_idx;
      for (core_idx = 0; core_idx < test_cores; core_idx++)
        {
          const core_summary_t* summary = &core_summaries[core_idx];
          const abs_deviation_t* stats = NULL;
          uint32_t store_idx;
          for (store_idx = 0; store_idx < PFD_NUM_STORES; store_idx++)
            {
              if (summary->store_valid[store_idx])
                {
                  stats = &summary->store[store_idx];
                  break;
                }
            }
			if (role_for_rank[core_idx] == 0){
				printf("Test number %u uses test ID %u\n", (uint32_t) group_for_rank[core_idx], (uint32_t) test_for_rank[core_idx]);
			}

					if (stats == NULL)
						{
							printf("Thread %u : no samples recorded\n", (uint32_t) core_for_rank[core_idx]);
							continue;
						}

					double avg = stats->avg;
					double std_dev = stats->std_dev;
					double abs_dev = stats->abs_dev;
					printf("Core number %u is using thread: %u. with: avg %5.1f cycles (min %5.1f | max %5.1f), std dev: %5.1f, abs dev: %5.1f\n",
								(uint32_t) role_for_rank[core_idx], (uint32_t) core_for_rank[core_idx], avg, stats->min_val, stats->max_val, std_dev, abs_dev);
				
					if (core_idx == (test_cores - 1) || role_for_rank[core_idx + 1] == 0) {
						printf("End test %u results for ID %u\n",
							(uint32_t) group_for_rank[core_idx],
							(uint32_t) test_for_rank[core_idx]);
					}

          sum_avg += avg;
          cores_with_stats++;
          if (avg < min_avg)
            {
              min_avg = avg;
							min_core = (uint32_t) core_for_rank[core_idx];
            }
          if (avg > max_avg)
            {
              max_avg = avg;
							max_core = (uint32_t) core_for_rank[core_idx];
            }
        }
	  printf("\n\n");

	  /* Aggregate by socket (simple heuristic: even CPUs -> socket 0, odd CPUs -> socket 1) */
      double sum_avg_sock[2] = {0.0, 0.0};
      uint32_t cnt_sock[2] = {0, 0};
      uint32_t wins_sock[2] = {0, 0};
      for (uint32_t r = 0; r < test_cores; r++)
        {
          int sock = (core_for_rank[r] % 2 == 0) ? 0 : 1;
          const core_summary_t* summary = &core_summaries[r];
          const abs_deviation_t* stats = NULL;
          for (uint32_t s = 0; s < PFD_NUM_STORES; s++) {
            if (summary->store_valid[s]) { stats = &summary->store[s]; break; }
          }
          if (stats) {
            sum_avg_sock[sock] += stats->avg;
            cnt_sock[sock]++;
          }
          if (win_counts_per_rank) {
            wins_sock[sock] += win_counts_per_rank[r];
          }
        }
	  #if defined(XeonGold6142) 
      if (cnt_sock[0] || cnt_sock[1]) {
        printf("Per-socket summary:\n");
        if (cnt_sock[0]) printf("  Socket 0: mean avg %6.1f cycles, total wins %u, avg wins for socket %6.1f\n",
								sum_avg_sock[0]/cnt_sock[0], wins_sock[0],
								(double)wins_sock[0]/(double)cnt_sock[0]);
        if (cnt_sock[1]) printf("  Socket 1: mean avg %6.1f cycles, total wins %u, avg wins for socket %6.1f\n",
								sum_avg_sock[1]/cnt_sock[1], wins_sock[1],
								(double)wins_sock[1]/(double)cnt_sock[1]);
        printf("\n");
      }
	  #endif

      if (cores_with_stats > 0)
        {
          double mean_avg = sum_avg / cores_with_stats;
          PRINT(" Summary : mean avg %8.1f cycles | min avg %8.1f (core %u) | max avg %8.1f (core %u)",
                mean_avg, min_avg, min_core, max_avg, max_core);
        }
      else
        {
          PRINT(" Summary : no statistics captured");
        }
	  
	  /* Mean common-start latency per thread (from B4 to this thread’s success) */
		if (common_latency_cycles) {
		printf("\nCommon-start latency (B4 -> success), per thread:\n");
		for (uint32_t r = 0; r < test_cores; r++) {
			double sum = 0.0, minv = DBL_MAX, maxv = 0.0;
			for (size_t k = 0; k < test_reps; k++) {
			double v = (double) common_latency_cycles[(size_t)r * test_reps + k];
			sum += v;
			if (v < minv) minv = v;
			if (v > maxv) maxv = v;
			}
			double mean = sum / (double)test_reps;
			printf("  thread ID %u (core %zu): mean %6.1f cycles, min %6.1f, max %6.1f\n",
				r, core_for_rank ? core_for_rank[r] : (size_t)r, mean, minv, maxv);
		}
		printf("\n");

		/* Optional: check how often the winner is the fastest (argmin) for that rep */
		if (first_winner_per_rep) {
			size_t matches = 0, valid = 0;
			for (size_t rep = 0; rep < test_reps; rep++) {
			uint32_t win = first_winner_per_rep[rep];
			if (win == UINT32_MAX) continue;
			valid++;
			uint32_t best = 0;
			uint64_t bestv = UINT64_MAX;
			for (uint32_t r = 0; r < test_cores; r++) {
				uint64_t v = common_latency_cycles[(size_t)r * test_reps + rep];
				if (v < bestv) { bestv = v; best = r; }
			}
			if (best == win) matches++;
			}
			if (valid) {
			printf("Winner==argmin(B4->success) in %zu/%zu reps (%.1f%%)\n",
					matches, valid, 100.0 * (double)matches / (double)valid);
			}
			printf("\n");
		}
		}

	        /* Report first-op winners across all repetitions (generalised) */
      if (win_counts_per_rank)
        {
          printf("\nFirst-success winners per thread (out of %zu reps):\n", test_reps);
          for (uint32_t r = 0; r < test_cores; r++)
            {
              printf("  Group %u role %u on thread %u (thread ID %u): %u wins\n",
                     (unsigned) (group_for_rank ? group_for_rank[r] : 0),
                     (unsigned) (role_for_rank ? role_for_rank[r] : 0),
                     (unsigned) (core_for_rank ? core_for_rank[r] : r),
                     r,
                     win_counts_per_rank[r]);
            }
          printf("\n");
        }

      if (cas_attempts_per_rank && cas_failures_per_rank && cas_successes_per_rank)
        {
          printf("CAS_UNTIL_SUCCESS retry stats per thread:\n");
          for (uint32_t r = 0; r < test_cores; r++)
            {
              printf("  thread ID %u (core %zu): attempts %llu failures %llu successes %llu\n",
                     r,
                     (size_t) (core_for_rank ? core_for_rank[r] : r),
                     (unsigned long long) cas_attempts_per_rank[r],
                     (unsigned long long) cas_failures_per_rank[r],
                     (unsigned long long) cas_successes_per_rank[r]);
            }
          printf("\n");
        }


      switch (test_test)
        {
        case STORE_ON_MODIFIED:
          {
	    if (test_flush)
	      {
		PRINT(" ** Results from Core 0 : store on invalid");
		PRINT(" ** Results from Core 1 : store on modified");
	      }
	    else
	      {
		PRINT(" ** Results from Core 0 and 1 : store on modified");
	      }
	    break;
	  }
	case STORE_ON_MODIFIED_NO_SYNC:
	  {
	    if (test_flush)
	      {
		PRINT(" ** Results do not make sense");
	      }
	    else
	      {
		PRINT(" ** Results from Core 0 and 1 : store on modified while another core is "
		      "also trying to do the same");
	      }
	    break;
	  }
	case STORE_ON_EXCLUSIVE:
	  {
	    if (test_flush)
	      {
		PRINT(" ** Results from Core 0 : load from invalid");
	      }
	    else
	      {
		PRINT(" ** Results from Core 0 : load from invalid, BUT could have prefetching");
	      }
	    PRINT(" ** Results from Core 1 : store on exclusive");
	    break;
	  }
	case STORE_ON_SHARED:
	  {
	    PRINT(" ** Results from Core 0 & 2: load from modified and exclusive or shared, respectively");
	    PRINT(" ** Results from Core 1 : store on shared");
	    if (test_cores < 3)
	      {
		PRINT(" ** Need >=3 processes to achieve STORE_ON_SHARED");
	      }
	    break;
	  }
	case STORE_ON_OWNED_MINE:
	  {
	    PRINT(" ** Results from Core 0 : load from modified (makes it owned, if owned state is supported)");
	    if (test_flush)
	      {
		PRINT(" ** Results 1 from Core 1 : store to invalid");
	      }
	    else
	      {
		PRINT(" ** Results 1 from Core 1 : store to modified mine");
	      }

	    PRINT(" ** Results 2 from Core 1 : store to owned mine (if owned is supported, else exclusive)");
	    break;
	  }
	case STORE_ON_OWNED:
	  {
	    if (test_flush)
	      {
		PRINT(" ** Results from Core 0 : store to modified");
	      }
	    else
	      {
		PRINT(" ** Results from Core 0 : store to invalid");
	      }
	    PRINT(" ** Results 1 from Core 1 : load from modified (makes it owned, if owned state is supported)");
	    PRINT(" ** Results 2 from Core 1 : store to owned (if owned is supported, else exclusive mine)");
	    break;
	  }
	case LOAD_FROM_MODIFIED:
	  {
	    if (test_flush)
	      {
		PRINT(" ** Results from Core 0 : store to invalid");
	      }
	    else
	      {
		PRINT(" ** Results from Core 0 : store to owned mine (if owned state supported, else exclusive)");
	      }

	    PRINT(" ** Results from Core 1 : load from modified (makes it owned, if owned state supported)");

	    break;
	  }
	case LOAD_FROM_EXCLUSIVE:
	  {
	    if (test_flush)
	      {
		PRINT(" ** Results from Core 0 : load from invalid");
	      }
	    else
	      {
		PRINT(" ** Results from Core 0 : load from invalid, BUT could have prefetching");
	      }
	    PRINT(" ** Results from Core 1 : load from exclusive");

	    break;
	  }
	case STORE_ON_INVALID:
	  {
	    PRINT(" ** Results from Core 0 : store on invalid");
	    PRINT(" ** Results from Core 1 : cache line flush");
	    break;
	  }
	case LOAD_FROM_INVALID:
	  {
	    PRINT(" ** Results from Core 0 : load from invalid");
	    PRINT(" ** Results from Core 1 : cache line flush");
	    break;
	  }
	case LOAD_FROM_SHARED:
	  {
	    if (test_flush)
	      {
		PRINT(" ** Results from Core 0 : load from invalid");
	      }
	    else
	      {
		PRINT(" ** Results from Core 0 : load from invalid, BUT could have prefetching");
	      }
	    PRINT(" ** Results from Core 1 : load from exclusive");
	    if (test_cores >= 3)
	      {
		PRINT(" ** Results from Core 2 : load from shared");
	      }
	    else
	      {
		PRINT(" ** Need >=3 processes to achieve LOAD_FROM_SHARED");
	      }
	    break;
	  }
	case LOAD_FROM_OWNED:
	  {
	    if (test_flush)
	      {
		PRINT(" ** Results from Core 0 : store to invalid");
	      }
	    else
	      {
		PRINT(" ** Results from Core 0 : store to owned mine (if owned is supported, else shared)");
	      }
	    PRINT(" ** Results from Core 1 : load from modified");
	    if (test_cores == 3)
	      {
		PRINT(" ** Results from Core 2 : load from owned");
	      }
	    else
	      {
		PRINT(" ** Need 3 processes to achieve LOAD_FROM_OWNED");
	      }
	    break;
	  }
	case CAS:
	  {
	    PRINT(" ** Results from Core 0 : CAS successfull");
	    PRINT(" ** Results from Core 1 : CAS unsuccessfull");
	    break;
	  }
	case FAI:
	  {
	    PRINT(" ** Results from Cores 0 & 1: FAI");
	    break;
	  }
	case TAS:
	  {
	    PRINT(" ** Results from Core 0 : TAS successfull");
	    PRINT(" ** Results from Core 1 : TAS unsuccessfull");
	    break;
	  }
	case SWAP:
	  {
	    PRINT(" ** Results from Cores 0 & 1: SWAP");
	    break;
	  }
	case CAS_ON_MODIFIED:
	  {
	    PRINT(" ** Results from Core 0 : store on modified");
	    uint32_t succ = 50 + test_ao_success * 50;
	    PRINT(" ** Results from Core 1 : CAS on modified (%d%% successfull)", succ);
	    break;
	  }
	case FAI_ON_MODIFIED:
	  {
	    PRINT(" ** Results from Core 0 : store on modified");
	    PRINT(" ** Results from Core 1 : FAI on modified");
	    break;
	  }
	case TAS_ON_MODIFIED:
	  {
	    PRINT(" ** Results from Core 0 : store on modified");
	    uint32_t succ = test_ao_success * 100;
	    PRINT(" ** Results from Core 1 : TAS on modified (%d%% successfull)", succ);
	    break;
	  }
	case SWAP_ON_MODIFIED:
	  {
	    PRINT(" ** Results from Core 0 : store on modified");
	    PRINT(" ** Results from Core 1 : SWAP on modified");
	    break;
	  }
	case CAS_ON_SHARED:
	  {
	    PRINT(" ** Results from Core 0 : load from modified");
	    PRINT(" ** Results from Core 1 : CAS on shared (100%% successfull)");
	    PRINT(" ** Results from Core 2 : load from exlusive or shared");
	    if (test_cores < 3)
	      {
		PRINT(" ** Need >=3 processes to achieve CAS_ON_SHARED");
	      }
	    break;
	  }
	case FAI_ON_SHARED:
	  {
	    PRINT(" ** Results from Core 0 : load from modified");
	    PRINT(" ** Results from Core 1 : FAI on shared");
	    PRINT(" ** Results from Core 2 : load from exlusive or shared");
	    if (test_cores < 3)
	      {
		PRINT(" ** Need >=3 processes to achieve FAI_ON_SHARED");
	      }
	    break;
	  }
	case TAS_ON_SHARED:
	  {
	    PRINT(" ** Results from Core 0 : load from L1");
	    uint32_t succ = test_ao_success * 100;
	    PRINT(" ** Results from Core 1 : TAS on shared (%d%% successfull)", succ);
	    PRINT(" ** Results from Core 2 : load from exlusive or shared");
	    if (test_cores < 3)
	      {
		PRINT(" ** Need >=3 processes to achieve TAS_ON_SHARED");
	      }
	    break;
	  }
	case SWAP_ON_SHARED:
	  {
	    PRINT(" ** Results from Core 0 : load from modified");
	    PRINT(" ** Results from Core 1 : SWAP on shared");
	    PRINT(" ** Results from Core 2 : load from exlusive or shared");
	    if (test_cores < 3)
	      {
		PRINT(" ** Need >=3 processes to achieve SWAP_ON_SHARED");
	      }
	    break;
	  }
        case CAS_CONCURRENT:
          {
            PRINT(" ** Results from %u cores: CAS concurrent", test_cores);
            break;
          }
	case FAI_ON_INVALID:
	  {
	    PRINT(" ** Results from Core 0 : FAI on invalid");
	    PRINT(" ** Results from Core 1 : cache line flush");
	    break;
	  }
	case LOAD_FROM_L1:
	  {
	    PRINT(" ** Results from Core 0: load from L1");
	    break;
	  }
	case LOAD_FROM_MEM_SIZE:
	  {
	    PRINT(" ** Results from Corees 0 & 1 & 2: load from random %zu KiB", test_mem_size / 1024);
	    break;
	  }
	case LFENCE:
	  {
	    PRINT(" ** Results from Cores 0 & 1: load fence");
	    break;
	  }
	case SFENCE:
	  {
	    PRINT(" ** Results from Cores 0 & 1: store fence");
	    break;
	  }
	case MFENCE:
	  {
	    PRINT(" ** Results from Cores 0 & 1: full fence");
	    break;
	  }
	case PROFILER:
	  {
	    PRINT(" ** Results from Cores 0 & 1: empty profiler region (start_prof - empty - stop_prof");
	    break;
	  }

	default:
	  break;
	}
    }

  B0;


  if (ID < test_cores)
    {
      PRINT(" value of cl is %-10u / sum is %llu", cache_line->word[0], (LLU) sum);
    }

  return NULL;
}

uint32_t
cas(volatile cache_line_t* cl, volatile uint64_t reps)
{
  uint8_t o = reps & 0x1;
  uint8_t no = !o; 
  volatile uint32_t r;

  RACE_TRY_WITH_REP(reps);
  PFDI(0);
  r = CAS_U32(cl->word, o, no);
  PFDO(0, reps);

  return (r == o);
}

uint32_t
cas_no_pf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  uint8_t o = reps & 0x1;
  uint8_t no = !o; 
  volatile uint32_t r;
  RACE_TRY_WITH_REP(reps);
  r = CAS_U32(cl->word, o, no);

  return (r == o);
}

static uint32_t
cas_until_success(volatile cache_line_t* cl, volatile uint64_t reps)
{
  /* Random-walk until we reach the target line (cln==0) without timing. */
  uint32_t cln;
  do {
    cln = clrand();
  } while (cln > 0);

  volatile uint32_t* w = &cl[0].word[0];

  uint32_t attempts = 0;
  uint32_t backoff = 1;
  uint32_t max_backoff = test_backoff_max;
  if (backoff_max_per_rank) {
    max_backoff = backoff_max_per_rank[ID];
  }

  /* We keep the original PFD “attempt->success” timing as-is */
  PFDI(0);
  for (;;) {
    attempts++;
    if (cas_attempts_per_rank) {
      cas_attempts_per_rank[ID]++;
    }
    uint32_t expect = *w;           /* read current value */
    uint32_t desired = expect ^ 1;  /* flip LSB */
    uint32_t old = CAS_U32(w, expect, desired);
    if (old == expect) {
      /* First successful CAS may claim the win for this rep */
      race_try_win((uint64_t) reps);
      if (cas_successes_per_rank) {
        cas_successes_per_rank[ID]++;
      }
      break;
    }
    if (cas_failures_per_rank) {
      cas_failures_per_rank[ID]++;
    }
    if (test_backoff) {
      for (uint32_t i = 0; i < backoff; i++) {
        _mm_pause();
      }
      if (backoff < max_backoff) {
        backoff = backoff << 1;
        if (backoff > max_backoff) backoff = max_backoff;
      }
    } else {
      _mm_pause();
    }
  }
  PFDO(0, reps);

  /* Also store the common-start latency (B4->this success) */
  if (common_latency_cycles && round_start) {
    ticks t_end = getticks();
    common_latency_cycles[(size_t)ID * test_reps + (size_t)reps] =
      (uint64_t)(t_end - round_start[reps]);
  }

  return 1; /* indicates success */
}


uint32_t
cas_0_eventually(volatile cache_line_t* cl, volatile uint64_t reps)
{
  uint8_t o = reps & 0x1;
  uint8_t no = !o; 
  volatile uint32_t r;

  uint32_t cln = 0;

  do
    {
      cln = clrand();
      volatile cache_line_t* cl1 = cl + cln;

	  if (cln == 0) {           // reached the target line
		RACE_TRY_WITH_REP(reps);  // claim winner here
	  }

      PFDI(0);
      r = CAS_U32(cl1->word, o, no);
      PFDO(0, reps);
    }
  while (cln > 0);

  return (r == o);
}

uint32_t
fai(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t t = 0;
  uint32_t cln = 0;
  do
  {
    cln = clrand();
    volatile cache_line_t* cl1 = cl + cln;
    if (cln == 0) {
      RACE_TRY_WITH_REP(reps);
    }
    PFDI(0);
    t = FAI_U32(cl1->word);
    PFDO(0, reps);
    if (cln == 0) {
      rec_success(reps);               /* B4 -> FAI completion (always success) */
    }
  }
  while (cln > 0);

  return t;
}

uint8_t
tas(volatile cache_line_t* cl, volatile uint64_t reps)
{
  uint32_t cln = 0;
  do
  {
    cln = clrand();
    volatile cache_line_t* cl1 = cl + cln;
#if defined(TILERA)
    volatile uint32_t* b = (volatile uint32_t*) cl1->word;
#else
    volatile uint8_t*  b = (volatile uint8_t*)  cl1->word;
#endif
    if (cln == 0)
    {
      /* We reached the target line: first arrival marker (keeps existing fairness winner semantics) */
      RACE_TRY_WITH_REP(reps);

      /* Time the "attempts until TAS succeeds" region and record B4->success on success */
      PFDI(0);
      for (;;)
      {
        uint8_t r = TAS_U8(b);
        if (r != 255) {
          PFDO(0, reps);
          rec_success(reps);          /* B4 -> TAS success */
          break;
        }
        _mm_pause();
      }
    }
  }
  while (cln > 0);

  /* We always succeed before leaving the cln==0 iteration */
  return 1;
}

uint32_t
swap(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t res;
  uint32_t cln = 0;
  do
  {
    cln = clrand();
    volatile cache_line_t* cl1 = cl + cln;
    if (cln == 0) {
      RACE_TRY_WITH_REP(reps);
    }
    PFDI(0);
    res = SWAP_U32(cl1->word, ID);
    PFDO(0, reps);
    if (cln == 0) {
      rec_success(reps);               /* B4 -> SWAP completion (always success) */
    }
  }
  while (cln > 0);

  _mm_mfence();
  return res;
}

void
store_0(volatile cache_line_t* cl, volatile uint64_t reps)
{
	  RACE_TRY_WITH_REP(reps);
  if (test_sfence == 0)
    {
      PFDI(0);
      cl->word[0] = reps;
      PFDO(0, reps);
    }
  else if (test_sfence == 1)
    {
      PFDI(0);
      cl->word[0] = reps;
      _mm_sfence();
      PFDO(0, reps);
    }
  else if (test_sfence == 2)
    {
      PFDI(0);
      cl->word[0] = reps;
      _mm_mfence();
      PFDO(0, reps);
    }
}

void
store_0_no_pf(volatile cache_line_t* cl, volatile uint64_t reps)
{  
	RACE_TRY();
  cl->word[0] = reps;
  if (test_sfence == 1)
    {
      _mm_sfence();
    }
  else if (test_sfence == 2)
    {
      _mm_mfence();
    }
}

static void
store_0_eventually_sf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t cln = 0;
  RACE_TRY_WITH_REP(reps);
  do
    {
      cln = clrand();
      volatile uint32_t *w = &cl[cln].word[0];
      PFDI(0);
      w[0] = cln;
      _mm_sfence();
      PFDO(0, reps);
    }
  while (cln > 0);
}

static void
store_0_eventually_mf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t cln = 0;
    RACE_TRY_WITH_REP(reps);
  do
    {
      cln = clrand();
      volatile uint32_t *w = &cl[cln].word[0];
      PFDI(0);
      w[0] = cln;
      _mm_mfence();
      PFDO(0, reps);
    }
  while (cln > 0);
}

static void
store_0_eventually_nf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t cln = 0;
    RACE_TRY_WITH_REP(reps);
  do
    {
      cln = clrand();
      volatile uint32_t *w = &cl[cln].word[0];
      PFDI(0);
      w[0] = cln;
      PFDO(0, reps);
    }
  while (cln > 0);
}

static void
store_0_eventually_dw(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t cln = 0;
    RACE_TRY_WITH_REP(reps);
  do
    {
      cln = clrand();
      volatile uint32_t *w = &cl[cln].word[0];
      PFDI(0);
      w[0]  = cln;
	  w[15] = cln;  /* last uint32_t in a 64B cache line */
      PFDO(0, reps);
    }
  while (cln > 0);
}

void
store_0_eventually(volatile cache_line_t* cl, volatile uint64_t reps)
{
  if (test_sfence == 0)
    {
      store_0_eventually_nf(cl, reps);
    }
  else if (test_sfence == 1)
    {
      store_0_eventually_sf(cl, reps);
    }
  else if (test_sfence == 2)
    {
      store_0_eventually_mf(cl, reps);
    }
  else if (test_sfence == 3)
    {
      store_0_eventually_dw(cl, reps);
    }
  /* _mm_mfence(); */
}


static void
store_0_eventually_pfd1_sf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t cln = 0;
    RACE_TRY_WITH_REP(reps);
  do
    {
      cln = clrand();
      volatile uint32_t *w = &cl[cln].word[0];
      PFDI(1);
      w[0] = cln;
      _mm_sfence();
      PFDO(1, reps);
    }
  while (cln > 0);
}

static void
store_0_eventually_pfd1_mf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t cln = 0;
    RACE_TRY_WITH_REP(reps);
  do
    {
      cln = clrand();
      volatile uint32_t *w = &cl[cln].word[0];
      PFDI(1);
      w[0] = cln;
      _mm_mfence();
      PFDO(1, reps);
    }
  while (cln > 0);
}

static void
store_0_eventually_pfd1_nf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t cln = 0;
    RACE_TRY_WITH_REP(reps);
  do
    {
      cln = clrand();
      volatile uint32_t *w = &cl[cln].word[0];
      PFDI(1);
      w[0] = cln;
      PFDO(1, reps);
    }
  while (cln > 0);
}

void
store_0_eventually_pfd1(volatile cache_line_t* cl, volatile uint64_t reps)
{
  if (test_sfence == 0)
    {
      store_0_eventually_pfd1_nf(cl, reps);
    }
  else if (test_sfence == 1)
    {
      store_0_eventually_pfd1_sf(cl, reps);
    }
  else if (test_sfence == 2)
    {
      store_0_eventually_pfd1_mf(cl, reps);
    }
  /* _mm_mfence(); */
}

static uint64_t
load_0_eventually_lf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t cln = 0;
  volatile uint64_t val = 0;
  RACE_TRY_WITH_REP(reps);

  do
    {
      cln = clrand();
      volatile uint32_t* w = &cl[cln].word[0];
      PFDI(0);
      val = w[0];
      _mm_lfence();
      PFDO(0, reps);
    }
  while (cln > 0);
  return val;
}

static uint64_t
load_0_eventually_mf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t cln = 0;
  volatile uint64_t val = 0;
  RACE_TRY_WITH_REP(reps);

  do
    {
      cln = clrand();
      volatile uint32_t* w = &cl[cln].word[0];
      PFDI(0);
      val = w[0];
      _mm_mfence();
      PFDO(0, reps);
    }
  while (cln > 0);
  return val;
}

static uint64_t
load_0_eventually_nf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t cln = 0;
  volatile uint64_t val = 0;
  RACE_TRY_WITH_REP(reps);

  do
    {
      cln = clrand();
      volatile uint32_t* w = &cl[cln].word[0];
      PFDI(0);
      val = w[0];
      PFDO(0, reps);
    }
  while (cln > 0);
  return val;
}


uint64_t
load_0_eventually(volatile cache_line_t* cl, volatile uint64_t reps)
{
  uint64_t val = 0;
  if (test_lfence == 0)
    {
      val = load_0_eventually_nf(cl, reps);
    }
  else if (test_lfence == 1)
    {
      val = load_0_eventually_lf(cl, reps);
    }
  else if (test_lfence == 2)
    {
      val = load_0_eventually_mf(cl, reps);
    }
  _mm_mfence();
  return val;
}

uint64_t
load_0_eventually_no_pf(volatile cache_line_t* cl)
{
  uint32_t cln = 0;
  uint64_t sum = 0;  
  RACE_TRY();

  do
    {
      cln = clrand();
      volatile uint32_t *w = &cl[cln].word[0];
      sum = w[0];
    }
  while (cln > 0);

  _mm_mfence();
  return sum;
}

static uint64_t
load_0_lf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t val = 0;
  volatile uint32_t* p = (volatile uint32_t*) &cl->word[0];
    RACE_TRY_WITH_REP(reps);
  PFDI(0);
  val = p[0];
  _mm_lfence();
  PFDO(0, reps);
  return val;
}

static uint64_t
load_0_mf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t val = 0;
  volatile uint32_t* p = (volatile uint32_t*) &cl->word[0];
    RACE_TRY_WITH_REP(reps);
  PFDI(0);
  val = p[0];
  _mm_mfence();
  PFDO(0, reps);
  return val;
}

static uint64_t
load_0_nf(volatile cache_line_t* cl, volatile uint64_t reps)
{
  volatile uint32_t val = 0;
  volatile uint32_t* p = (volatile uint32_t*) &cl->word[0];
    RACE_TRY_WITH_REP(reps);
  PFDI(0);
  val = p[0];
  PFDO(0, reps);
  return val;
}


uint64_t
load_0(volatile cache_line_t* cl, volatile uint64_t reps)
{
  uint64_t val = 0;
  if (test_lfence == 0)
    {
      val = load_0_nf(cl, reps);
    }
  else if (test_lfence == 1)
    {
      val = load_0_lf(cl, reps);
    }
  else if (test_lfence == 2)
    {
      val = load_0_mf(cl, reps);
    }
  _mm_mfence();
  return val;
}

static uint64_t
load_next_lf(volatile uint64_t* cl, volatile uint64_t reps)
{
  const size_t do_reps = test_cache_line_num;
    RACE_TRY_WITH_REP(reps);
  PFDI(0);
  int i;
  for (i = 0; i < do_reps; i++)
    {
      cl = (uint64_t*) *cl;
      _mm_lfence();
    }
  PFDOR(0, reps, do_reps);
  return *cl;

}

static uint64_t
load_next_mf(volatile uint64_t* cl, volatile uint64_t reps)
{
  const size_t do_reps = test_cache_line_num;
    RACE_TRY_WITH_REP(reps);
  PFDI(0);
  int i;
  for (i = 0; i < do_reps; i++)
    {
      cl = (uint64_t*) *cl;
      _mm_mfence();
    }
  PFDOR(0, reps, do_reps);
  return *cl;

}

static uint64_t
load_next_nf(volatile uint64_t* cl, volatile uint64_t reps)
{
  const size_t do_reps = test_cache_line_num;
    RACE_TRY_WITH_REP(reps);
  PFDI(0);
  int i;
  for (i = 0; i < do_reps; i++)
    {
      cl = (uint64_t*) *cl;
    }
  PFDOR(0, reps, do_reps);
  return *cl;
}

uint64_t
load_next(volatile uint64_t* cl, volatile uint64_t reps)
{
  uint64_t val = 0;
  if (test_lfence == 0)
    {
      val = load_next_nf(cl, reps);
    }
  else if (test_lfence == 1)
    {
      val = load_next_lf(cl, reps);
    }
  else if (test_lfence == 2)
    {
      val = load_next_mf(cl, reps);
    }
  return val;
}

void
invalidate(volatile cache_line_t* cl, uint64_t index, volatile uint64_t reps)
{
	  RACE_TRY_WITH_REP(reps);
  PFDI(0);
  _mm_clflush((void*) (cl + index));
  PFDO(0, reps);
  _mm_mfence();
}

static size_t
parse_size(char* optarg)
{
  size_t test_mem_size_multi = 1;
  char multi = optarg[strlen(optarg) - 1];
  if (multi == 'b' || multi == 'B')
    {
      optarg[strlen(optarg) - 1] = optarg[strlen(optarg)];
      multi = optarg[strlen(optarg) - 1];
    }

  if (multi == 'k' || multi == 'K')
    {
      test_mem_size_multi = 1024;
      optarg[strlen(optarg) - 1] = optarg[strlen(optarg)];
    }
  else if (multi == 'm' || multi == 'M')
    {
      test_mem_size_multi = 1024 * 1024LL;
      optarg[strlen(optarg) - 1] = optarg[strlen(optarg)];
    }
  else if (multi == 'g' || multi == 'G')
    {
      test_mem_size_multi = 1024 * 1024 * 1024LL;
      optarg[strlen(optarg) - 1] = optarg[strlen(optarg)];
    }

  return test_mem_size_multi * atoi(optarg);
}

static void
collect_core_stats(uint32_t store, uint32_t num_vals, uint32_t num_print)
{
  abs_deviation_t stats;
  pfd_collect_abs_deviation(store, num_vals, num_print, &stats);

  if (core_summaries == NULL)
    {
      return;
    }

  if (ID < test_cores && store < PFD_NUM_STORES)
    {
      core_summaries[ID].store[store] = stats;
      core_summaries[ID].store_valid[store] = 1;
    }
}

volatile cache_line_t*
cache_line_open()
{
  uint64_t size = test_cache_line_num * sizeof(cache_line_t);

#if defined(__tile__)
  tmc_alloc_t alloc = TMC_ALLOC_INIT;
  tmc_alloc_set_shared(&alloc);
  /*   tmc_alloc_set_home(&alloc, TMC_ALLOC_HOME_HASH); */
  /*   tmc_alloc_set_home(&alloc, MAP_CACHE_NO_LOCAL); */
  tmc_alloc_set_home(&alloc, TMC_ALLOC_HOME_HERE);
  /*   tmc_alloc_set_home(&alloc, TMC_ALLOC_HOME_TASK); */
  
  volatile cache_line_t* cache_line = (volatile cache_line_t*) tmc_alloc_map(&alloc, size);
  if (cache_line == NULL)
    {
      tmc_task_die("Failed to allocate memory.");
    }

  tmc_cmem_init(0);		/*   initialize shared memory */


  cache_line->word[0] = 0;

#else    /* !__tile__ ****************************************************************************************/
  void* mem = NULL;

  #ifdef PLATFORM_NUMA
	/* Prefer allocating the buffer on the seed’s NUMA node (page-aligned, OK for 64B alignment) */
	if (seed_node >= 0 && numa_available() != -1) {
		mem = numa_alloc_onnode(size, seed_node);
		if (mem != NULL) {
		cache_line_from_numa = 1;
		}
	}
  #endif

  /* Fallback to regular allocation if libnuma unavailable or allocation failed */
  if (mem == NULL) {
    int rc = posix_memalign(&mem, 64, size);
    if (rc != 0)
      {
        errno = rc;
        perror("posix_memalign");
        exit(1);
      }
  }

  volatile cache_line_t* cache_line = (volatile cache_line_t*) mem;
  
  /* Best-effort lock to reduce paging jitter (may fail due to RLIMIT_MEMLOCK) */
  if (opt_mlock) {
    if (mlock((const void*) cache_line, size) != 0) {
      perror("mlock (best-effort)");
    }
  }


#endif  /* __tile ********************************************************************************************/
    /* Only memset when we need the whole region (LOAD_FROM_MEM_SIZE).
     For single-line/stride tests, avoid touching all pages to reduce noise. */
	if (test_test == LOAD_FROM_MEM_SIZE) {
		memset((void*) cache_line, '1', size);
	}


	  if (ID == 0) {
		if (test_test == LOAD_FROM_MEM_SIZE)
			{
			/* Touch all pages and build the random list */
			uint32_t cl;
			for (cl = 0; cl < test_cache_line_num; cl++)
				{
				cache_line[cl].word[0] = 0;
				_mm_clflush((void*) (cache_line + cl));
				}
			create_rand_list_cl((volatile uint64_t*) cache_line, test_mem_size / sizeof(uint64_t));
			}
		else
			{
			/* Minimal first-touch: only the first line we actually use */
			cache_line[0].word[0] = 0;
			_mm_clflush((void*) (cache_line + 0));
			}
    }


  _mm_mfence();
  return cache_line;
}

static void
create_rand_list_cl(volatile uint64_t* list, size_t n)
{
  size_t per_cl = sizeof(cache_line_t) / sizeof(uint64_t);
  n /= per_cl;

  unsigned long* s = seed_rand();
  s[0] = 0xB9E4E2F1F1E2E3D5ULL;
  s[1] = 0xF1E2E3D5B9E4E2F1ULL;
  s[2] = 0x9B3A0FA212342345ULL;

  uint8_t* used = calloc(n * per_cl, sizeof(uint8_t));
  assert (used != NULL);

  size_t idx = 0;
  size_t used_num = 0;
  while (used_num < n - 1)
    {
      used[idx] = 1;
      used_num++;
      
      size_t nxt;
      do 
	{
	  nxt = (my_random(s, s+1, s+2) % n) * per_cl;
	}
      while (used[nxt]);

      list[idx] = (uint64_t) (list + nxt);
      idx = nxt;
    }
  list[idx] = (uint64_t) (list); /* close the loop! */

  free(s);
  free(used);
} 

void
cache_line_close(volatile cache_line_t* cache_line)
{
#if !defined(__tile__)
  size_t size = test_cache_line_num * sizeof(cache_line_t);
  #ifdef PLATFORM_NUMA
	if (opt_numa && cache_line_from_numa && numa_available() != -1) {
	numa_free((void*) cache_line, size);
	} else
	#endif
	{
	free((void*) cache_line);
	}

#else
  (void) cache_line;
  tmc_cmem_close();
#endif
}

static void free_jagged(size_t **a, size_t *cols, size_t rows) {
    if (!a) return;
    for (size_t i = 0; i < rows; i++)
        free(a[i]);
    free(a);
    free(cols);
}

int parse_jagged_array(
  const char *s,
  size_t ***out,
  size_t *rows,
  size_t **cols
) {
  const char *p = s;
  size_t r = 0;
  size_t **data = NULL;
  size_t *col_counts = NULL;

  while (*p) {
    while (*p && *p != '[') p++;
    if (!*p) break;
    p++; /* enter row */

    /* dynamic vector for this row */
    size_t cap = 16, len = 0;
    size_t *row = (size_t*) malloc(cap * sizeof *row);
    if (!row) goto fail;

    while (*p && *p != ']') {
      /* skip until number or '-' */
      while (*p && !isdigit((unsigned char)*p) && *p != '-' && *p != ']') p++;
      if (!*p || *p == ']') break;

      /* parse first integer (start or single) */
      char *endptr = NULL;
      long long a = strtoll(p, &endptr, 10);
      if (p == endptr) { free(row); goto fail; }
      p = endptr;

      /* look ahead for ellipsis */
      const char *save = p;
      while (*p == ' ' || *p == '\t' || *p == ',') p++;
      int has_ellipsis = (p[0]=='.' && p[1]=='.' && p[2]=='.');

      if (has_ellipsis) {
        p += 3;
        while (*p == ' ' || *p == '\t' || *p == ',') p++;
        long long b = strtoll(p, &endptr, 10);
        if (p == endptr) { free(row); goto fail; }
        p = endptr;

        long long step = (b >= a) ? 1 : -1;
        for (long long v = a;; v += step) {
          if (len == cap) {
            cap *= 2;
            size_t *tmp = (size_t*) realloc(row, cap * sizeof *row);
            if (!tmp) { free(row); goto fail; }
            row = tmp;
          }
          row[len++] = (size_t) v;
          if (v == b) break;
        }
      } else {
        /* single value */
        p = save; /* rewind to after the number; separators are skipped below */
        if (len == cap) {
          cap *= 2;
          size_t *tmp = (size_t*) realloc(row, cap * sizeof *row);
          if (!tmp) { free(row); goto fail; }
          row = tmp;
        }
        row[len++] = (size_t) a;
      }

      /* advance to next number or ']' */
      while (*p && *p != ']' && !(isdigit((unsigned char)*p) || *p=='-')) p++;
    }

    if (*p != ']') { free(row); goto fail; }
    p++; /* leave row */

    /* shrink row to size and append to outputs */
    size_t *final = len ? (size_t*) realloc(row, len * sizeof *row) : row;
    if (!final && len) { free(row); goto fail; }

    size_t **tmpd = (size_t**) realloc(data, (r + 1) * sizeof *data);
    size_t *tmpc = (size_t*) realloc(col_counts, (r + 1) * sizeof *col_counts);
    if (!tmpd || !tmpc) { free(final); goto fail; }
    data = tmpd; col_counts = tmpc;

    data[r] = final;
    col_counts[r] = len;
    r++;
  }

  if (r == 0) goto fail;
  *out = data;
  *rows = r;
  *cols = col_counts;
  return 0;

fail:
  free_jagged(data, col_counts, r);
  return -1;
}
