#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
CCBENCH_BIN=${CCBENCH_BIN:-"${PROJECT_ROOT}/ccbench"}
REPS=${REPETITIONS:-1000}
TEST_NUM=${TEST_NUM:-16}
LOG_FILE_DEFAULT="${PROJECT_ROOT}/cas_grid.log"
CSV_FILE_DEFAULT="${PROJECT_ROOT}/cas_grid.csv"
LOG_FILE=${LOG_FILE:-"${LOG_FILE_DEFAULT}"}
CSV_FILE=${CSV_FILE:-"${CSV_FILE_DEFAULT}"}

prepare_output_file() {
  local requested_path="$1"
  local default_path="$2"
  local description="$3"
  local env_var_name="$4"

  local output_dir
  output_dir=$(dirname -- "${requested_path}")
  if [[ ! -d "${output_dir}" ]]; then
    if [[ "${requested_path}" == "${default_path}" ]]; then
      mkdir -p -- "${output_dir}"
    else
      echo "error: directory ${output_dir} does not exist for ${description} (${requested_path})." >&2
      echo "Create the directory or point ${env_var_name} at a writable location." >&2
      exit 1
    fi
  fi

  if : >"${requested_path}" 2>/dev/null; then
    echo "${requested_path}"
    return
  fi

  if [[ "${requested_path}" != "${default_path}" ]]; then
    echo "error: unable to write to ${description} (${requested_path})." >&2
    echo "Check permissions or override via ${env_var_name}." >&2
    exit 1
  fi

  local template fallback
  template="$(basename -- "${default_path}").XXXXXX"
  fallback=$(mktemp -p "${output_dir}" "${template}")
  echo "warning: unable to write to ${description} (${requested_path}); using ${fallback} instead." >&2
  echo "${fallback}"
}

if [[ ! -x "${CCBENCH_BIN}" ]]; then
  echo "error: ccbench binary not found at ${CCBENCH_BIN}. Build the project first (e.g. 'make')." >&2
  exit 1
fi

CORE_SET_DEFAULT="0 1 2 3 4 5 6 7"
if [[ -n "${CORE_SET:-}" ]]; then
  read -r -a CORES <<<"${CORE_SET}"
else
  read -r -a CORES <<<"${CORE_SET_DEFAULT}"
fi

LOG_FILE=$(prepare_output_file "${LOG_FILE}" "${LOG_FILE_DEFAULT}" "log file" "LOG_FILE")
CSV_FILE=$(prepare_output_file "${CSV_FILE}" "${CSV_FILE_DEFAULT}" "CSV file" "CSV_FILE")

echo "Recording CAS latency grid to ${LOG_FILE} and ${CSV_FILE} (cores: ${CORES[*]})" >&2
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

    avg=$(echo "${LOG_OUTPUT}" | awk -v core="${to_core}" '
      $2 == "Core" {
        tgt = $3
        gsub(":", "", tgt)
        if (tgt == core) {
          for (i = 1; i <= NF; ++i) {
            if ($i == "avg") {
              print $(i+1)
              exit
            }
          }
        }
      }
      $1 == "Core" {
        tgt = $2
        gsub(":", "", tgt)
        if (tgt == core) {
          for (i = 1; i <= NF; ++i) {
            if ($i == "avg") {
              print $(i+1)
              exit
            }
          }
        }
      }')
    if [[ -z "${avg}" ]]; then
      avg="NA"
    fi
    printf '%d,%d,%s\n' "${from_core}" "${to_core}" "${avg}" >>"${CSV_FILE}"
  done

done

echo "Done. Full log: ${LOG_FILE}; summary: ${CSV_FILE}" >&2
