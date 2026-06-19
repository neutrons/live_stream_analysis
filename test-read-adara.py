from readadara import AdaraFileReader, AdaraLiveStreamReader
import time

#reader = AdaraLiveStreamReader('bl1b-daq1', 31415)
#g = reader.read_generator()

filename = "./adara_mount/20250201/adara_streams/NOMAD.Raw.Data.Runs.208511-208543/20250131-101613.350178410-run-208511/m00000001-f00000001-run-208511.adara"
reader = AdaraFileReader(filename)
g = reader.read_generator()

for packet in g:
	packet_type_name = type(packet).__name__
	if packet_type_name == "AdaraRunInfoPacket":
		print(packet, packet.get_events(), packet.get_key_functions())
		print(packet.get_xml_run_info())
	#time.sleep(.01)
