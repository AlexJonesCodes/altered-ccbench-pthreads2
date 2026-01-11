#!/bin/bash

CORES=(0 1 2 3)
TEST_NUM=16
REPS=10000
CCBENCH=../ccbench

ALLLOG="ccbench_all.log"
CSVOUT="ccbench_summary.csv"

> "$ALLLOG"
echo "from_core,to_core,avg_latency" > "$CSVOUT"

for i in "${CORES[@]}"; do
    for j in "${CORES[@]}"; do
        if [[ "$i" -ne "$j" ]]; then
            echo "===== LOG: core $i -> core $j =====" >> "$ALLLOG"
            # Run and tee to ALLLOG, but capture output for parsing
            LOG=$( $CCBENCH --cores 2 --cores_array "[$i,$j]" --test $TEST_NUM --repetitions $REPS 2>&1 )
            echo "$LOG" >> "$ALLLOG"
            echo "" >> "$ALLLOG"
            # Extract 'avg' for Core $j from the log
            avg=$(echo "$LOG" | grep "Core $j :" | awk '{for(i=1;i<=NF;i++) if ($i=="avg") print $(i+1)}')
            echo "$i,$j,$avg" >> "$CSVOUT"
        fi
    done
done
