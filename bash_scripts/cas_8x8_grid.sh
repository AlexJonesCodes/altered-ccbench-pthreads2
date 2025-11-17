#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
CCBENCH_BIN=${CCBENCH_BIN:-"${PROJECT_ROOT}/ccbench"}
REPS=${REPETITIONS:-1000}
TEST_NUM=${TEST_NUM:-16}
LOG_FILE=${LOG_FILE:-"${PROJECT_ROOT}/cas_grid.log"}
CSV_FILE=${CSV_FILE:-"${PROJECT_ROOT}/cas_grid.csv"}

if [[ ! -x "${CCBENCH_BIN}" ]]; then
  echo "error: ccbench binary not found at ${CCBENCH_BIN}. Build the project first (e.g. 'make')." >&2
  exit 1
fi

CORES=(0 1 2 3 4 5 6 7)

echo "Recording CAS latency grid to ${LOG_FILE} and ${CSV_FILE}" >&2
>"${LOG_FILE}"
printf "from_core,to_core,avg_latency\n" >"${CSV_FILE}"

for from_core in "${CORES[@]}"; do
  for to_core in "${CORES[@]}"; do
    if [[ "${from_core}" -eq "${to_core}" ]]; then
      continue
    fi
    printf 'Running CAS test %d -> %d...\n' "${from_core}" "${to_core}" >&2
    echo "===== LOG: core ${from_core} -> core ${to_core} =====" >>"${LOG_FILE}"
    if LOG_OUTPUT=$("${CCBENCH_BIN}" --cores 2 --cores_array "[${from_core},${to_core}]" --test "${TEST_NUM}" --repetitions "${REPS}" 2>&1); then
      status=0
    else
      status=$?
    fi
    echo "${LOG_OUTPUT}" >>"${LOG_FILE}"
    if [[ ${status} -ne 0 ]]; then
      echo "Command exited with status ${status}" >>"${LOG_FILE}"
    fi
    echo >>"${LOG_FILE}"

    avg=$(echo "${LOG_OUTPUT}" | awk '
      /Summary/ {
        if (match($0, /Summary[[:space:]]*:[[:space:]]*mean avg[[:space:]]*([0-9.]+)/, m)) {
          print m[1]
          exit
        }
      }
    ')
    if [[ -z "${avg}" ]]; then
      avg="NA"
    fi
    printf '%d,%d,%s\n' "${from_core}" "${to_core}" "${avg}" >>"${CSV_FILE}"
  done

done

echo "Done. Full log: ${LOG_FILE}; summary: ${CSV_FILE}" >&2
