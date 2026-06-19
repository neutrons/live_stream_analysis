from readadara import AdaraLiveStreamReader
import time

reader = AdaraLiveStreamReader('bl1b-daq1', 31415)
g = reader.read_generator()
for packet in g:
	packet_type_name = type(packet).__name__
	if packet_type_name == "AdaraRunInfoPacket":
		print(packet, packet.get_events())
	time.sleep(.01)
