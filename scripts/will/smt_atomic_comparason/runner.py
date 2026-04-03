import subprocess
import csv
import re

CCBENCH_LOCATON = "../../../ccbench"
CSV_OUT = "./result/ccbench_results.csv"
REPEATS = 4
TEST_INDEXS = [0, 7, 12, 13, 14, 15, 34] # 34 is the cas repeat operation

def find_cpu_pairs():
    out = subprocess.check_output(
        ["lscpu", "-p=CPU,CORE"],
        text=True
    )

    core_translations = {}
    for row in out.splitlines():
        if row.startswith("#"):
            continue
        cpu, core = map(int, row.split(","))
        core_translations.setdefault(core, []).append(cpu)

    cpu_pairs = []

    for core in sorted(core_translations.keys()):
        cpus = sorted(core_translations[core])
        if len(cpus) >= 2:
            cpu_pairs.append((cpus[0], cpus[1]))
    return cpu_pairs

def find_all():
    output = subprocess.check_output(
        ["lscpu", "-p=CPU"], # -p gives better parsable out than normal
        text=True
    )
    cpus = []
    for line in output.splitlines():
        if line.startswith("#"):
            continue
        cpus.append(int(line.strip()))
    return sorted(cpus)
    
# main
print("pairs:")
for p in find_cpu_pairs():
    print(p)

# find all test pairs
tests = []
for a in TEST_INDEXS:
    for b in TEST_INDEXS:
        tests.append((a, b))

with open(CSV_OUT, "w", newline="") as csv_file:
    csv_write_obj = csv.writer(csv_file)
    csv_write_obj.writerow([
        "repeat",
        "cpu1",
        "cpu2",
        "test1",
        "test2",
        "core0_avg_cycles",
        "core1_avg_cycles"
    ])
    for repeat in range(REPEATS):
        print(f"\nRepeat {repeat}\n")

        # pair cpu runs
        for cpu1, cpu2 in find_cpu_pairs():
            for test1, test2 in tests:
                # run the test
                cmd = [
                    CCBENCH_LOCATON,
                    "-x", f"[{cpu1},{cpu2}]",
                    "-t", f"[{test1},{test2}]",
                    "-b", str(cpu1)
                ]
                print("Running:", " ".join(cmd))
                process = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )

                summary = re.compile(r"Core number 0.*avg\s+([0-9.]+).*?\n"r"Core number 1.*avg\s+([0-9.]+)", re.MULTILINE)
                match = summary.search(process.stdout)
                cpu_pin0 = float(match.group(1))
                cpu_pin1 = float(match.group(2))

                #write to csv
                csv_write_obj.writerow([ repeat, cpu1, cpu2, test1, test2, cpu_pin0, cpu_pin1])
                csv_file.flush()

        # single cpuy runs
        for cpu in find_all():
            for test in TEST_INDEXS:
                # run test
                cmd = [
                    CCBENCH_LOCATON,
                    "-x", f"[{cpu}]",
                    "-t", f"[{test}]",
                    "-b", str(cpu)
                ]
                print("Running:", " ".join(cmd))
                process = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )

                # parse out
                single = re.compile(r"Core number 0.*avg\s+([0-9.]+)")
                match = single.search(process.stdout)
                cpu_pin0 = float(match.group(1))
                csv_write_obj.writerow([repeat, cpu, -1, test, -1, cpu_pin0, -1])
                csv_file.flush()
