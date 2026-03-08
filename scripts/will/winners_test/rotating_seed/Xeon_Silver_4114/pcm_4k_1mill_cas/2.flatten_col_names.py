import csv

INPUT_FILE = "pcm_combined.csv"
OUTPUT_FILE = "pcm_flattened.csv"

with open(INPUT_FILE, newline="") as f:
    reader = csv.reader(f)

    header1 = next(reader)
    header2 = next(reader)

    flattened = []

    for h1, h2 in zip(header1, header2):
        h1 = h1.strip()
        h2 = h2.strip()

        if h1 and h2 and h1 != h2:
            flattened.append(f"{h1}_{h2}")
        elif h2:
            flattened.append(h2)
        else:
            flattened.append(h1)

    with open(OUTPUT_FILE, "w", newline="") as out:
        writer = csv.writer(out)

        writer.writerow(flattened)

        for row in reader:
            writer.writerow(row)

print(f"Flattened CSV written to {OUTPUT_FILE}")
