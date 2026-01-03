#!/bin/bash
# this script contains the mappings from test numbers to test names
source test_nums_to_name.sh

CORES=({0..3})
#CORES=(0 1 2 3)
#TEST_NUMS=(0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33)
TEST_NUMS=(12,13,14,15,0,2,3,5,6,7,8,9,10,11,16,17,18,19,20,21,22,23,24)
#TEST_NUMS=(12)
REPS=500
INTENSE_LOGGING= true # do we want vigorous logging for each test? speed unefected for ryzen 5
CCBENCH=../ccbench

[ -d "results" ] || mkdir "results"
[ -d "./results/spam_logs" ] || mkdir "./results/spam_logs"
CSVOUT="results/ccbench_summary.csv"

TOTAL_START=$SECONDS
echo "test,from_core,to_core,avg_latency" > "$CSVOUT"
for k in ${TEST_NUMS//,/ }; do
    # Get test name from number
    TEST_NAME=${NUM_TO_TEST[$k]}
    if [ "$INTENSE_LOGGING" = true ] ; then
        CURRENT_LOG="results/spam_logs/test_${TEST_NAME}_log.txt"
        echo "CURRENT_TEST: $TEST_NAME" >> "$CURRENT_LOG"
    fi
    echo "Running test $k: $TEST_NAME"
    TEST_START=$(date +%s.%N)
    CORE_TO_CHOOSE=${TARGET_CORE[$k]} 
    for i in "${CORES[@]}"; do
        for j in "${CORES[@]}"; do
            LOG=$( $CCBENCH --cores 2 --cores_array "[$i,$j]" --test $k --repetitions $REPS 2>&1 )
            if [ "$INTENSE_LOGGING" = true ] ; then
                echo "===== LOG: core $i -> core $j =====" >> "$CURRENT_LOG"
                # Run and tee to ALLLOG, but capture output for parsing
                echo "$LOG" >> "$CURRENT_LOG"
                echo "" >> "$CURRENT_LOG"
            fi
            if (( CORE_TO_CHOOSE == 0 )); then
                CORE=$i
            elif (( CORE_TO_CHOOSE == 1 )); then
                CORE=$j
            fi
            # Extract 'avg' for Core $j from the log
            avg=$(echo "$LOG" | grep "Core $CORE :" | awk '{for(i=1;i<=NF;i++) if ($i=="avg") print $(i+1)}' | head -n1)
            echo "$TEST_NAME,$i,$j,$avg" >> "$CSVOUT"
        done
    done
    TEST_END=$(date +%s.%N)
    TEST_S=$(awk -v s="$TEST_START" -v e="$TEST_END" 'BEGIN { printf "%.6f", e-s }')

    echo "$TEST_NAME took ${TEST_S}s"
    if [ "$INTENSE_LOGGING" = true ] ; then
        echo "TIME_TEST_SEC: $TEST_S" >> "$CURRENT_LOG"
    fi
done
TOTAL_END=$SECONDS
RUNTIME=$((TOTAL_END - TOTAL_START))
echo "Total runtime: $RUNTIME seconds"
