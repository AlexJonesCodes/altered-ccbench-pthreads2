/*   
 *   File: barrier.c
 *   Author: Vasileios Trigonakis <vasileios.trigonakis@epfl.ch>
 *   Description: implementation of process barriers
 *   barrier.c is part of ccbench
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

#include "barrier.h"
#include <pthread.h>
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>

#ifdef __sparc__
#  include <sys/types.h>
#  include <sys/processor.h>
#  include <sys/procset.h>
#endif	/* __sparc__ */

barrier_t* barriers;


int color_all(int id)
{
  return 1;
}

void
barriers_init(const uint32_t num_procs)
{
  barriers = (barrier_t*) calloc(NUM_BARRIERS, sizeof(barrier_t));
  if (barriers == NULL)
    {
      perror("calloc");
      exit(1);
    }

  uint32_t bar;
  for (bar = 0; bar < NUM_BARRIERS; bar++)
    {
      barrier_init(bar, 0, color_all, num_procs);
    }
}

void
barrier_init(const uint32_t barrier_num, const uint64_t participants, int (*color)(int),
             const uint32_t total_cores)
{
  if (barrier_num >= NUM_BARRIERS) 
    {
      return;
    }

  barriers[barrier_num].color = color;
  uint32_t ue, num_parts = 0;
  for (ue = 0; ue < total_cores; ue++)
    {
      num_parts += color(ue);
    }
  if (num_parts == 0)
    {
      num_parts = 1;
    }
  barriers[barrier_num].num_participants = num_parts;

  int rc = pthread_barrier_init(&barriers[barrier_num].barrier, NULL, num_parts);
  if (rc != 0)
    {
      errno = rc;
      perror("pthread_barrier_init");
      exit(1);
    }

}


void 
barrier_wait(const uint32_t barrier_num, const uint32_t id, const uint32_t total_cores) 
{
  _mm_mfence();
  if (barrier_num >= NUM_BARRIERS) 
    {
      return;
    }

  //  printf("enter: %d : %d\n", barrier_num, id);

  barrier_t *b = &barriers[barrier_num];

  int (*col)(int);
  col = b->color;

  if (col(id) == 0)
    {
      return;
    }

  int rc = pthread_barrier_wait(&b->barrier);
  if (rc != 0 && rc != PTHREAD_BARRIER_SERIAL_THREAD)
    {
      errno = rc;
      perror("pthread_barrier_wait");
      exit(1);
    }

}

void
barriers_term(void)
{
  if (barriers == NULL)
    {
      return;
    }

  for (uint32_t bar = 0; bar < NUM_BARRIERS; bar++)
    {
      pthread_barrier_destroy(&barriers[bar].barrier);
    }

  free(barriers);
  barriers = NULL;
}
