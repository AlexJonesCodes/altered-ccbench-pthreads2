import subprocess
import csv
import re

NUM_RUNS = 24 # total runs of ccbench invoked
CSV_FILE_NAME = "4000_runs_1mill_reps_repeat.csv"
# for 39 cpu sys, do 40 since list doesnt include final val
x_array = list(range(0,40))   # change list if you want to use different cpus, range not needed, eg [0,4,6,8,9]

print(f"-x array cpus is: {x_array}")
num_cpus = len(x_array)

# TODO: CHANGE FROM 1 MILLION TO 1.6 IF USING GOLD CPU, 1M IS FOR SILVER
command = [
    "../../../../ccbench",
    "-r", "1" + "000" + "000" ,
    "-x", str(x_array),
    "-t", "[0]",
    "-R",
]

with open(CSV_FILE_NAME, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "run",
        "cpu",
        "b_value",
        "test_type",
        "wins",
        "attempts",
        "successes",
        "failures"
    ])

    for run in range(NUM_RUNS):
        b = x_array[run % num_cpus]
        full_command = command + ["-b", str(b)] # b val increments run to run so is appended here
        print(f"starting run {run + 1} -b is {b}")
        result = subprocess.run(full_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)

        for line in result.stdout.splitlines():
            regex = re.compile(
                r"CPU\s+(\d+)\s+ran\s+(\S+)\s+\|\s+"
                r"wins:\s+(\d+)\s+\|\s+"
                r"attempts:\s+(\d+)\s+\|\s+"
                r"successes:\s+(\d+)\s+\|\s+"
                r"failures:\s+(\d+)"
            )
            match = regex.search(line)
            if match:
                cpu, test_type, wins, attempts, successes, failures = match.groups()
                writer.writerow([ run + 1, cpu, b, test_type, wins, attempts, successes, failures])

        print(f"run completed: {run + 1}")
        
print(f"all runs are complete, no error")
