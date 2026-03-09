import csv
from collections import defaultdict
import matplotlib.pyplot as plt
import numpy as np

# ----------------------------
# SETTINGS
# ----------------------------

RANDOM_MIX_MODE = True   # True = mixed instruction CSV

CSV_FILES = [
    "4kruns_1_000_000_reps2/4000_runs_1mill_reps2.csv",
    "cas_4kruns_1_000_000_reps/4000_runs_1mill_reps_cas.csv",
    "fai_4kruns_1_000_000_reps/4000_runs_1mill_reps_fai_rep.csv",
    "load_on_modified_4kruns_1_000_000_reps/load_on_modified_4000_runs_1mill_reps.csv",
    "swap_4kruns_1_000_000_reps/4000_runs_1mill_reps_swap.csv",
    "tas_4kruns_1_000_000_reps/1/4000_runs_1mill_reps_tas.csv"
]

'''
    "../Xeon_Gold_6142/6400_runs_1.6mill_reps_repeat/6400_runs_1.6mill_reps_repeat.csv",
    "../Xeon_Gold_6142/cas_6400_runs_1.6mill_reps_repeat/cas_6400_runs_1.6mill_reps.csv",
    "../Xeon_Gold_6142/fai_6400_runs_1.6mill_reps_repeat/fai_6400_runs_1.6mill_reps.csv",
    "../Xeon_Gold_6142/load_on_modified_6400_runs_1.6mill_reps_repeat/6400_runs_1.6mill_reps_load_on_modified.csv",
    "../Xeon_Gold_6142/swap_6400_runs_1.6mill_reps_repeat/swap_6400_runs_1.6mill_reps.csv",
    "../Xeon_Gold_6142/tas_6400_runs_1.6mill_reps/6400_runs_1.6mill_reps_tas.csv"
'''

is_gold_list = [
    False
]

LABEL_MAP = {
    "STORE_ON_MODIFIED": "STORE",
    "LOAD_ON_MODIFIED": "LOAD"
}

instruction_order = [
    "TAS",
    "SWAP",
    "STORE_ON_MODIFIED",
    "LOAD_FROM_MODIFIED",
    "CAS",
    "FAI"
]

# ----------------------------
# SOCKET MAPPING
# ----------------------------

def get_socket_mapping(is_gold, total_cpus):

    if is_gold:
        return {
            0: list(range(0,10)) + list(range(20,30)),
            1: list(range(10,20)) + list(range(30,40))
        }
    else:
        half = total_cpus // 2
        return {
            0: list(range(0, half)),
            1: list(range(half, total_cpus))
        }

# ----------------------------
# LOAD CSV
# ----------------------------

def load_csv(csv_file):

    core_winners = defaultdict(lambda: defaultdict(int))
    core_instr = defaultdict(dict)

    with open(csv_file) as f:
        reader = csv.DictReader(f)

        for row in reader:
            run = int(row["run"])
            cpu = int(row["cpu"])
            wins = int(row["wins"])
            instr = row["test_type"]

            core_winners[run][cpu] += wins
            core_instr[run][cpu] = instr

    return core_winners, core_instr

# ----------------------------
# FAIRNESS CALCULATION
# ----------------------------

def total_executions_per_run(core_winners):

    totals = {}

    for run in core_winners:
        totals[run] = sum(core_winners[run].values())

    return totals


def compute_socket_totals(core_winners, SOCKETS):

    socket_data = defaultdict(list)
    totals_per_run = total_executions_per_run(core_winners)

    for run in core_winners:

        total_cpus = sum(len(v) for v in SOCKETS.values())
        fair = totals_per_run[run] / total_cpus

        for socket, cpus in SOCKETS.items():

            total = sum(core_winners[run][cpu] for cpu in cpus)

            socket_data[socket].append(total / (fair * len(cpus)))

    return socket_data


def compute_socket_instruction(core_winners, core_instr, SOCKETS):

    socket_instr = defaultdict(list)
    totals_per_run = total_executions_per_run(core_winners)

    for run in core_winners:

        total_cpus = sum(len(v) for v in SOCKETS.values())
        fair = totals_per_run[run] / total_cpus

        for socket, cpus in SOCKETS.items():

            for cpu in cpus:

                instr = core_instr[run][cpu]
                wins = core_winners[run][cpu]

                socket_instr[(socket, instr)].append(wins / fair)

    return socket_instr

# ----------------------------
# BUILD TEST DATA
# ----------------------------

tests = []

for csv_file, is_gold in zip(CSV_FILES, is_gold_list):

    core_winners, core_instr = load_csv(csv_file)

    total_cpus = max(core_winners[next(iter(core_winners))].keys()) + 1
    SOCKETS = get_socket_mapping(is_gold, total_cpus)

    if RANDOM_MIX_MODE:

        mixed = compute_socket_instruction(core_winners, core_instr, SOCKETS)

        tests.append({"mixed": mixed})

    else:

        totals = compute_socket_totals(core_winners, SOCKETS)

        # determine instruction type
        first_run = next(iter(core_instr))
        test_type = list(core_instr[first_run].values())[0]

        tests.append({
            "type": test_type,
            "totals": totals
        })

# ----------------------------
# PLOTTING
# ----------------------------

def make_plot():

    fig, ax = plt.subplots()

    data = []
    positions = []

    socket_labels = []
    instr_labels = []

    pos = 1

    if RANDOM_MIX_MODE:

        mixed = tests[0]["mixed"]

        for socket in [0,1]:
            for instr in instruction_order:

                key = (socket, instr)

                if key not in mixed:
                    continue

                data.append(mixed[key])
                positions.append(pos)

                socket_labels.append(str(socket))
                instr_labels.append(LABEL_MAP.get(instr, instr))

                pos += 1

    else:

        for test in tests:

            totals = test["totals"]

            for socket in [0,1]:

                data.append(totals[socket])
                positions.append(pos)

                socket_labels.append(str(socket))
                instr_labels.append(LABEL_MAP.get(test["type"], test["type"]))

                pos += 1

    vp = ax.violinplot(data, positions=positions, showmeans=True)

    # x axis
    ax.set_xticks(positions)
    ax.set_xticklabels(instr_labels, rotation=45)

    # secondary x axis for sockets
    ax2 = ax.twiny()
    ax2.set_xlim(ax.get_xlim())
    ax2.set_xticks(positions)
    ax2.set_xticklabels(socket_labels)

    ax2.set_xlabel("Socket")

    # y axis
    ax.set_ylabel("Normalized Executions")
    ax.set_ylim(bottom=0)

    ax.legend(loc="lower right")

    plt.tight_layout()
    plt.show()

# ----------------------------
# RUN
# ----------------------------

make_plot()