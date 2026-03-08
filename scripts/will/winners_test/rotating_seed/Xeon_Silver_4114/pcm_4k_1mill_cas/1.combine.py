import csv
import glob
import os

PCM_DIR = "./2/pcm"
OUT_FILE = "pcm_combined.csv"

files = sorted(
    glob.glob(os.path.join(PCM_DIR, "*.csv")),
    key=lambda x: int(os.path.basename(x).split(".")[0])
)

header_written = False

with open(OUT_FILE, "w", newline="") as out_f:
    writer = csv.writer(out_f)

    for fpath in files:
        run_index = int(os.path.basename(fpath).split(".")[0])

        with open(fpath, newline="") as f:
            reader = csv.reader(f)

            header1 = next(reader)
            header2 = next(reader)

            if not header_written:
                writer.writerow(["run", "pcm_sample"] + header1)
                writer.writerow(["run", "pcm_sample"] + header2)
                header_written = True

            pcm_sample = 0
            for row in reader:
                pcm_sample += 1
                writer.writerow([run_index, pcm_sample] + row)

print(f"Combined CSV written to {OUT_FILE}")
