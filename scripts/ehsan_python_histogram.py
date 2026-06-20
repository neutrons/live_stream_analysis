import argparse
import csv
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import readadara


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Legacy Ehsan-style ADARA histogrammer")
    parser.add_argument("mode", nargs="?", default="w", help="File write mode for legacy text output")
    parser.add_argument(
        "--geometry-csv",
        default="input_files/pixel_geometry.csv",
        help="Pixel geometry CSV containing 'pixel id' and 'TOF-to-Q matrix element'",
    )
    parser.add_argument("--live-stream", nargs=2, metavar=("HOST", "PORT"), help="Read from a live ADARA stream")
    parser.add_argument("--folder-path", help="Read .adara files from a folder")
    parser.add_argument(
        "--max-banked-packets",
        type=int,
        default=0,
        help="Stop after this many banked event packets; 0 means no limit",
    )
    parser.add_argument(
        "--output-csv",
        default="artifacts/ehsan_live_stream_hist.csv",
        help="Output CSV path for Q value, I(Q), Error I(Q)",
    )
    parser.add_argument(
        "--output-plot",
        default="artifacts/ehsan_live_stream_hist.png",
        help="Output plot path",
    )
    return parser.parse_args()


def load_q_matrix_constants(geometry_csv: str) -> list[float]:
    by_pixel_id: dict[int, float] = {}
    max_pixel_id = -1
    with Path(geometry_csv).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            pixel_id = int(row["pixel id"])
            by_pixel_id[pixel_id] = float(row["TOF-to-Q matrix element"])
            max_pixel_id = max(max_pixel_id, pixel_id)

    q_matrix = [0.0] * (max_pixel_id + 1)
    for pixel_id, value in by_pixel_id.items():
        q_matrix[pixel_id] = value
    return q_matrix


args = parse_args()
mode = args.mode
q_matrix_constant = load_q_matrix_constants(args.geometry_csv)

## print(f"len(q_matrix_constant): {len(q_matrix_constant)}")
q_array = []

hist = np.zeros(5000, dtype=np.uint32)

all_events = []

event_counter = 0

np.set_printoptions(threshold=np.inf)

if args.live_stream is not None:
    host, port = args.live_stream
    reader = readadara.AdaraLiveStreamReader(host, int(port))
else:
    folder_path = args.folder_path or "/SNS/users/y8y/NOMAD.Raw.Data.Runs.208511-208543/20250131-125313.244482428-run-208543"
    adara_files = sorted(Path(folder_path).glob("*.adara"))
    reader = readadara.AdaraMultiFileReader(adara_files[0:20])

itr = 0

g = reader.read_generator()
packet_bytes = b""

BANKED_EVENT_TYPE = 0x400001

start = time.time()
for packet in g:
    if packet.get_format_int() == BANKED_EVENT_TYPE:
        # print(f"packet no: {itr}")
        # packet_bytes = packet.get_packet_b()
        for k, (tof, pixel_id) in enumerate(packet.get_events()):
            merged = (tof << 32) | pixel_id
            # print(f"pixel_id: {pixel_id}")
            ##print(f"q_matrix_constant[{pixel_id}]: {q_matrix_constant[pixel_id]}")
            if pixel_id >= len(q_matrix_constant):
                # print(f"Warning: pixel_id {pixel_id} is out of bounds for q_matrix_constant (length {len(q_matrix_constant)})")
                continue
            c1 = q_matrix_constant[pixel_id]
            if tof == 0:
                # print(f"Warning: TOF is zero for pixel_id {pixel_id}, skipping Q calculation to avoid division by zero")
                continue
            Q = c1 * 50
            Q = Q / tof
            # Qx50=(int)(Q*1024)
            # q_array.append(Q)

            event_counter = event_counter + 1

            bram_index = int(Q)
            if 0 <= bram_index < 5000:
                hist[bram_index] += 1

            ## if k >= 4:
            ##	break
            ## print(f"source {i} | bank {j} | event {k}: tof = {tof}, pixel_id = {pixel_id} | 0x{merged:016X}")
            ##with open("printed_values_live2.txt", mode) as file:
            ##	print(f"Event:{k} | Q:{Q} | bin:{Qx50:06x}| tof:{tof} | pid:{pixel_id}| pid_tof:{merged:#018X}", file=file)
            ##print(f"Event:{k} | Q:{Q} | bin:{Qx50:#08X} | tof:{tof} | pid:{pixel_id} | pid_tof:{merged:#018X}")
            # print(f"0x{merged:016X}")
        # all_events += packet.get_events()

        ##if event_counter >= 500000:
        ##	break

        itr = itr + 1
        if args.max_banked_packets > 0 and itr >= args.max_banked_packets:
            break

end = time.time()

print(f"\nTotal Events: {event_counter}\n")
print(f"\nTotal Packets: {itr}\n")
print(f"\nTotal runtime of the program is {end - start} seconds")


bins = np.arange(5000)

for bram_index in range(5000):
    with open("bram_values_python_all.txt", mode, encoding="utf-8") as file:
        print(f"Index:{bram_index} - Counts:{hist[bram_index]}", file=file)

output_csv = Path(args.output_csv)
output_csv.parent.mkdir(parents=True, exist_ok=True)
with output_csv.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["Q value", "I(Q)", "Error I(Q)"])
    for bram_index, count in enumerate(hist):
        q_value = bram_index / 50.0
        writer.writerow([f"{q_value:.8f}", f"{float(count):.8f}", f"{np.sqrt(float(count)):.8f}"])


plt.figure(figsize=(12, 6))
# plt.bar(bins[0:200], hist[0:200], width=1)
plt.plot(bins[0:500], hist[0:500])
plt.title("Python Histogram")
plt.xlabel("Bin")
plt.ylabel("Counts")
plt.grid(True)
output_plot = Path(args.output_plot)
output_plot.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(output_plot, dpi=600, bbox_inches="tight")

plt.show(block=False)

print(f"Total Python counts = {np.sum(hist)}", flush=True)


plt.show(block=True)
