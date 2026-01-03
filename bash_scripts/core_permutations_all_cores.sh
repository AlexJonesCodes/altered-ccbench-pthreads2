#!/bin/bash
set -u

source test_nums_to_name.sh

########################################
# Configuration
########################################
CORES=(0 1 2 3)
TEST_NUMS=(12,13,14,15,0,2,3,5,6,7,8,9,10,11,16,17,18,19,20,21,22,23,24)
#TEST_NUMS=(20)
REPS=1000
INTENSE_LOGGING=true
CCBENCH=../ccbench

OUTDIR="results_2"
LOGDIR="$OUTDIR/spam_logs"
CSVOUT="$OUTDIR/ccbench_summary.csv"

mkdir -p "$LOGDIR"

echo "test,cores,avg_latency" > "$CSVOUT"

########################################
# Generate permutations of exact length
# Usage: permute LEN items...
########################################
permute() {
    local length=$1
    shift
    local items=("$@")

    _permute() {
        local prefix="$1"
        shift
        local remaining=("$@")

        # Count elements in prefix
        local prefix_len=0
        [ -n "$prefix" ] && prefix_len=$(wc -w <<< "$prefix")

        if (( prefix_len == length )); then
            echo "$prefix"
            return
        fi

        for i in "${!remaining[@]}"; do
            local new_prefix
            if [ -z "$prefix" ]; then
                new_prefix="${remaining[i]}"
            else
                new_prefix="$prefix ${remaining[i]}"
            fi

            local new_remaining=(
                "${remaining[@]:0:i}"
                "${remaining[@]:i+1}"
            )

            _permute "$new_prefix" "${new_remaining[@]}"
        done
    }

    _permute "" "${items[@]}"
}


########################################
# Main loop
########################################
TOTAL_START=$SECONDS

for k in ${TEST_NUMS//,/ }; do
    TEST_NAME=${NUM_TO_TEST[$k]}
    CORE_TO_CHOOSE=${TARGET_CORE[$k]}

    echo "Running test $k: $TEST_NAME"

    if [ "$INTENSE_LOGGING" = true ]; then
        CURRENT_LOG="$LOGDIR/test_${TEST_NAME}_log.txt"
        echo "CURRENT_TEST: $TEST_NAME" > "$CURRENT_LOG"
    fi

    TEST_START=$(date +%s.%N)

    ########################################
    # Run all permutations with LEN >= 2
    ########################################
    for (( LEN=2; LEN<=${#CORES[@]}; LEN++ )); do
        while read -r perm; do
            echo "  Testing cores permutation: $perm"
            CORES_ARRAY=($perm)

            ARRAY_STR="[${CORES_ARRAY[*]}]"
            ARRAY_STR="${ARRAY_STR// /,}"

            LOG=$(
                $CCBENCH \
                    --cores "$LEN" \
                    --cores_array "$ARRAY_STR" \
                    --test "$k" \
                    --repetitions "$REPS" 2>&1
            )

            if [ "$INTENSE_LOGGING" = true ]; then
                echo "===== LOG: cores $ARRAY_STR =====" >> "$CURRENT_LOG"
                echo "$LOG" >> "$CURRENT_LOG"
                echo "" >> "$CURRENT_LOG"
            fi

            CORE=${CORES_ARRAY[$CORE_TO_CHOOSE]}
            avg=$(echo "$LOG" | grep "Core $CORE :" | \
                  awk '{for(i=1;i<=NF;i++) if ($i=="avg") print $(i+1)}' | head -n1)

            echo "$TEST_NAME,$ARRAY_STR,$avg" >> "$CSVOUT"
        done < <(permute "$LEN" "${CORES[@]}")
    done

    TEST_END=$(date +%s.%N)
    TEST_S=$(awk -v s="$TEST_START" -v e="$TEST_END" 'BEGIN { printf "%.6f", e-s }')

    echo "$TEST_NAME took ${TEST_S}s"
    [ "$INTENSE_LOGGING" = true ] && echo "TIME_TEST_SEC: $TEST_S" >> "$CURRENT_LOG"
done

TOTAL_END=$SECONDS
echo "Total runtime: $((TOTAL_END - TOTAL_START)) seconds"
