import readadara
import sys
import os
import subprocess
import numpy as np
import time
from pathlib import Path
import matplotlib.pyplot as plt
from itertools import islice


mode = sys.argv[1]
# Open the text file and read comma-separated numbers into a Python list

with open("/SNS/users/tev/readadara-main/pixel_geometry.txt", "r") as file:
    content = file.read()

# Convert the comma-separated values into a list of numbers
q_matrix_constant = [float(x) for x in content.split(",")]

## print(f"len(q_matrix_constant): {len(q_matrix_constant)}")
q_array = []

hist = np.zeros(5000, dtype=np.uint32)

all_events = []

event_counter = 0

np.set_printoptions(threshold=np.inf)

## reader = readadara.AdaraLiveStreamReader("bl1B-daq1", 31415)
## reader = readadara.AdaraFileReader("/Users/tev/Library/CloudStorage/OneDrive-OakRidgeNationalLaboratory/datas/m00000001-f00000001-run-235002.adara") #C:\\Users\\tev\\Downloads\\datas\\m00000001-f00000044-run-235002.adara")

## reader = readadara.AdaraFileReader("/SNS/users/tev/parser/datas/m00000001-f00000048-run-235002.adara") #m00000001-f00000004-run-208616.adara") # parser/datas/m00000001-f00000044-run-235002.adara")

folder_path = "/SNS/users/y8y/NOMAD.Raw.Data.Runs.208511-208543/20250131-125313.244482428-run-208543" ##"/SNS/users/tev/parser/datas/"

# Get a list of all .adara files in the folder
adara_files = sorted(Path(folder_path).glob("*.adara"))

reader = readadara.AdaraMultiFileReader(adara_files[0:20])

itr = 0

g = reader.read_generator()
packet_bytes = b''

BANKED_EVENT_TYPE = 0x400001

start = time.time()
for packet in g:
	if packet.get_format_int() == BANKED_EVENT_TYPE:
		#print(f"packet no: {itr}")
		#packet_bytes = packet.get_packet_b()
		for k, (tof, pixel_id) in enumerate(packet.get_events()):
			merged = (tof << 32) | pixel_id
			#print(f"pixel_id: {pixel_id}")
			##print(f"q_matrix_constant[{pixel_id}]: {q_matrix_constant[pixel_id]}")
			if pixel_id >= len(q_matrix_constant):
				#print(f"Warning: pixel_id {pixel_id} is out of bounds for q_matrix_constant (length {len(q_matrix_constant)})")
				continue
			c1 = q_matrix_constant[pixel_id]
			if tof == 0:
				#print(f"Warning: TOF is zero for pixel_id {pixel_id}, skipping Q calculation to avoid division by zero")
				continue
			Q = (c1*50)
			Q = Q/tof
			#Qx50=(int)(Q*1024)
			#q_array.append(Q)
			
			## event_counter = event_counter + 1
			##print(f"event counter: {event_counter}")
			
			bram_index = int(Q)
			if 0 <= bram_index < 5000:
                    		hist[bram_index] += 1
                    		
			## if k >= 4:
			##	break
			## print(f"source {i} | bank {j} | event {k}: tof = {tof}, pixel_id = {pixel_id} | 0x{merged:016X}")
			##with open("printed_values_live2.txt", mode) as file:
			##	print(f"Event:{k} | Q:{Q} | bin:{Qx50:06x}| tof:{tof} | pid:{pixel_id}| pid_tof:{merged:#018X}", file=file)
			##print(f"Event:{k} | Q:{Q} | bin:{Qx50:#08X} | tof:{tof} | pid:{pixel_id} | pid_tof:{merged:#018X}")
			#print(f"0x{merged:016X}")
		#all_events += packet.get_events()
		
		##if event_counter >= 500000:
		##	break
		
		itr = itr+1
		##if itr == 500:
		##	break

end = time.time()

print(f"\nTotal Events: {event_counter}\n")
print(f"\nTotal Packets: {itr}\n")
print(f"\nTotal runtime of the program is {end-start} seconds")


bins = np.arange(5000)

for bram_index in range(5000):
	with open("bram_values_python_all.txt", mode) as file:
		print(f"Index:{bram_index} - Counts:{hist[bram_index]}", file=file)


plt.figure(figsize=(12, 6))
#plt.bar(bins[0:200], hist[0:200], width=1)
plt.plot(bins[0:500], hist[0:500])
plt.title("Python Histogram")
plt.xlabel("Bin")
plt.ylabel("Counts")
plt.grid(True)
plt.savefig("python_plot_cpu_all_3.png", dpi=600, bbox_inches="tight")

plt.show(block=False)

print(f"Total Python counts = {np.sum(hist)}", flush=True)


plt.show(block=True)
