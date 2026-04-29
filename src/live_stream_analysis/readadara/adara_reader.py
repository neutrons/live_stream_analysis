import os
import numpy as np
import struct
import time
import xml.etree.ElementTree as ET
import threading

def packet_bytes_to_format_int(packet_bytes):
	return int.from_bytes(packet_bytes[4:8], "little")

def bytes_to_events(events_b, num_events):
	#L = struct.unpack("<"+"II"*num_events, events_b)
	#L = np.frombuffer(events_b, dtype=np.uint32)
	L = list(struct.iter_unpack("<II", events_b))
	#L = [(L[2*i], L[2*i+1]) for i in range(len(L)//2)]
	#L = [{0:L[2*i], 1:L[2*i+1]} for i in range(len(L)//2)]
	return L

def select_bits(data, bits_start, bits_end):
	#bin_str = bin(data)[2:]
	#bin_str = (32-len(bin_str))*'0' + bin_str
	#bin_str_sliced = bin_str[len(bin_str)-bits_end:len(bin_str)-bits_start]
	#return int(bin_str_sliced,2)
	#int(bin_str[len(bin_str)-1-i]))
	######
	#mask = ((1 << (bits_end-(bits_start//8)*8)) - 1)
	#byte_offset = 0*(bits_start//8)
	#mask = ((1 << (bits_end-byte_offset*8)) - 1)
	mask = ((1 << bits_end) - 1)
	data = data & mask
	#data = data >> (bits_start%8)
	#data = data >> (bits_start - byte_offset*8)
	data = data >> bits_start
	return data
	
class AdaraRawPacket:
	def test_packet_bits(self):
		d = 34
		assert select_bits(d, 0, 8) == d
		#print(select_bits(d, 0, 1))
		#assert select_bits(d, 1, 2) == 1
		#for i in range(8):
		#	print("test bit", i, select_bits(d, i, i+1))
		d = 33560954
		print(d, bin(d))
		assert type(d) == int
		#assert 33560954 & 2 == 2
		#assert 7==1
		for i in range(32):
			bit = select_bits(d, i, i+1)
			print("test bitg", i, bit)
			bin_str = bin(d)[2:]
			#print(bit, int(bin_str[len(bin_str)-1-i]))
			#assert bit == int(bin_str[len(bin_str)-1-i])
		print(select_bits(d, 22, 32))
		#assert select_bits(d, 22, 32) == 0
		#assert select_bits(d, 0, 32) == d
		print(select_bits(d, 22, 32))
		#assert select_bits(d, 22, 32) == d
		#assert select_bits(d, 22, 32) == 0
		#assert 1==0


	def get_packet_bits(self, bits_start, bits_end):
		bytes_start = bits_start // 8
		bytes_end = bits_end // 8 + 1
		#bytes_end = (bits_end - 1) // 8 + 1
		#bytes_end = bits_end // 8
		packet_bytes = self.get_packet_b()[bytes_start:bytes_end]
		data = int.from_bytes(packet_bytes, "little") 
		return select_bits(data, bits_start - bytes_start*8, bits_end-bytes_start*8)
	def get_payload_length(self):
		packet_bytes = int.from_bytes(self.get_packet_b()[:4], "little")
		#packet_bits = self.get_packet_bits(0,8*4)
		#assert packet_bytes == packet_bits
		return packet_bytes
	def get_format_int(self):
		return int.from_bytes(self.get_packet_b()[4:8], "little")
	def get_timestamp_s(self):
		return int.from_bytes(self.get_packet_b()[8:12], "little")
	def get_timestamp_ns(self):
		return int.from_bytes(self.get_packet_b()[12:16], "little")
	def get_timestamp(self):
		return self.get_timestamp_s() + self.get_timestamp_ns()*10**(-9)
	def get_length(self):
		return len(self.get_packet_b())
	def get_events_b(self):
		return self.get_packet_b()[16+24:]
	def get_charge_b(self):
		return self.get_packet_b()[16+8:16+11]
	def get_tof_offset_b(self):
		return self.get_packet_b()[16+20:16+24]
	def get_charge(self):
		row = int.from_bytes(self.get_charge_b(), "little")
		pc_10 = row
		return pc_10*10
		
	def get_events(self):
		return self.get_events_()
	def get_events_(self):
		events_b = self.get_events_b()
		num_events = len(events_b)//8
		L = bytes_to_events(events_b, num_events)
		return L
	def get_tof_offset(self):
		return int.from_bytes(self.get_tof_offset_b(), "little")
		
	def __init__(self, packet_b):
		self.packet_b = packet_b
	def get_accelerator_cycle_b(self):
		return self.get_packet_b()[16+12:16+13]
	def get_accelerator_cycle(self):
		return int.from_bytes(self.get_accelerator_cycle_b(), "little")
	def get_key_functions(self):
		return {"format_int": self.get_format_int, "timestamp_s": self.get_timestamp_s, "timestamp_ns": self.get_timestamp_ns, "charge": self.get_charge, "events": self.get_events, "payload_length": self.get_payload_length}
	def to_dict(self):
		d = {}
		#keys_functions = [("format_int", self.get_format_int), ("timestamp", self.get_timestamp), ("charge", self.get_charge), ("events", self.get_events)]
		keys_functions = self.get_key_functions()
		for k,f in keys_functions.items():
			d[k] = f()
		#return {"format_int": self.get_format_int(), "timestamp": self.get_timestamp(), "charge": self.get_charge(), "events": self.get_events()}
		return d
	def get_packet_b(self):
		return self.packet_b

class AdaraNullPacket(AdaraRawPacket):
	def get_events(self):
		return []
	def get_key_functions(self):
		d = super().get_key_functions()
		d.pop("events")
		d.pop("charge")
		return d


class AdaraSyncPacket(AdaraNullPacket):
	def get_signature(self):
		return self.get_packet_b()[16:16+16]
	def get_file_offset(self):
		return int.from_bytes(self.get_packet_b()[32:40], "little")
	def get_comment_length(self):
		return int.from_bytes(self.get_packet_b()[40:44], "little")
	def get_comment(self):
		return self.get_packet_b()[44:]
	def get_key_functions(self):
		d = super().get_key_functions()
		additional_fields = {"signature": self.get_signature, "file_offset": self.get_file_offset, "comment_length": self.get_comment_length, "comment": self.get_comment}
		d.update(additional_fields)
		return d

class AdaraRunStatusPacket(AdaraNullPacket):
	def get_run_number(self):
		return int.from_bytes(self.get_packet_b()[16:20], "little")
	def get_run_start(self):
		return int.from_bytes(self.get_packet_b()[20:24], "little")
	def get_file_number(self):
		return self.get_packet_bits(24*8, 24*8+24)
	def get_status(self):
		return self.get_packet_bits(24*8+24, 24*8+32)
	def get_pause_file_number(self):
		return self.get_packet_bits(28*8, 28*8+24)
	def get_paused(self):
		return self.get_packet_bits(28*8+24, 28*8+32)
	def get_addendum_file_number(self):
		return self.get_packet_bits(32*8, 32*8+24)
	def get_addendum(self):
		return self.get_packet_bits(32*8+24, 32*8+32)
	def get_key_functions(self):
		d = super().get_key_functions()
		additional_fields = {"run_number": self.get_run_number, "run_start": self.get_run_start, "file_number": self.get_file_number, "status": self.get_status, "pause_file_number": self.get_pause_file_number, "paused": self.get_paused, "addendum_file_number": self.get_addendum_file_number, "addendum": self.get_addendum}
		d.update(additional_fields)
		return d


class AdaraStreamAnnotationPacket(AdaraNullPacket):
	def get_comment_length(self):
		return self.get_packet_bits(16*8, 16*8+16)
	def get_type(self):
		return self.get_packet_bits(16*8+16, 16*8+31)
	def get_rst(self):
		return self.get_packet_bits(16*8+31, 16*8+32)
	def get_scan_index(self):
		return self.get_packet_bits(20*8, 20*8+32)
	def get_comment(self):
		#return self.get_packet_b()[24:24+self.get_comment_length()]
		return self.get_packet_b()[24:]
	def get_key_functions(self):
		d = super().get_key_functions()
		additional_fields = {"comment_length": self.get_comment_length, "type": self.get_type, "rst": self.get_rst, "scan_index": self.get_scan_index, "comment": self.get_comment}
		d.update(additional_fields)
		return d



class AdaraBeamlineInfoPacket(AdaraNullPacket):
	def get_long_length(self):
		return self.get_packet_bits(16*8, 16*8+8)
	def get_short_length(self):
		return self.get_packet_bits(16*8+8, 16*8+16)
	def get_id_length(self):
		return self.get_packet_bits(16*8+16, 16*8+24)
	def get_target_station_num(self):
		return self.get_packet_bits(16*8+24, 16*8+32)
	def get_beamline_data(self):
		return self.get_packet_b()[20:]
	def get_key_functions(self):
		d = super().get_key_functions()
		additional_fields = {"long_length": self.get_long_length, "short_length": self.get_short_length, "id_length": self.get_id_length, "target_station_num": self.get_target_station_num, "beamline_data": self.get_beamline_data}
		d.update(additional_fields)
		return d


	
def parse_mapping_data_shorthand(b):
	d = {}
	d["base_physical_id"] = int.from_bytes(b[0:4], "little")
	two_bytes = int.from_bytes(b[4:6], "little")
	d["count"] = two_bytes & 0x7FFF
	d["is_shorthand"] = two_bytes & 0x8000 == 0x8000
	d["bank_id"] = int.from_bytes(b[6:8], "little")
	d["stopping_physical_id"] = int.from_bytes(b[8:12], "little")
	d["base_logical_id"] = int.from_bytes(b[12:16], "little")
	d["logical_step"] = int.from_bytes(b[16:18], "little")
	d["physical_step"] = int.from_bytes(b[18:20], "little")
	d["stopping_logical_id"] = int.from_bytes(b[20:24], "little")
	return d

def pixid_in_range(pixid, pixid_start, pixid_end):
	if pixid >= pixid_start and pixid <= pixid_end:
		return True
	return False

class AdaraPixelMappingTablePacket(AdaraNullPacket):
	def get_num_banks(self):
		return int.from_bytes(self.get_packet_b()[16:20], "little")
	def get_mapping_data(self):
		num_banks = self.get_num_banks()
		payload_length = self.get_payload_length()
		mapping_data_list = []
		bytes_read = 4
		while bytes_read < payload_length:
			b = self.get_packet_b()[16+bytes_read:20+bytes_read+24]
			mapping_data = parse_mapping_data_shorthand(b)
			mapping_data_list.append(mapping_data)
			bytes_read += 24
		return mapping_data_list
	def get_banks_in_pixel_range(self, pixel_start, pixel_end):
		pixel_start = pixel_start & 0x7FFFFFFF
		pixel_end = pixel_end & 0x7FFFFFFF
		bank_ids = set([])
		mapping_data = self.get_mapping_data()
		for entry in mapping_data:
			if entry["bank_id"] not in bank_ids:
				if pixid_in_range(entry["base_logical_id"], pixel_start, pixel_end):
					print("adding bank because base", entry["bank_id"])
					bank_ids.add(entry["bank_id"])
				if pixid_in_range(entry["stopping_logical_id"], pixel_start, pixel_end):
					print("adding bank because stop", entry["bank_id"])
					print("pm_base pm_stop pixel_start pixel_end", entry["base_logical_id"], entry["stopping_logical_id"], pixel_start, pixel_end)
					bank_ids.add(entry["bank_id"])
		return bank_ids
	#def get_events(self):
	#	return []
	def get_key_functions(self):
		d = super().get_key_functions()
		additional_fields = {"num_banks": self.get_num_banks, "mapping_data": self.get_mapping_data}
		d.update(additional_fields)
		return d



		
		
	

class AdaraEventPacket(AdaraRawPacket):
	pass

class PacketBank:
	def __init__(self, b):
		self.b = b
		#self.b = b[:8+8*self.get_event_count()]
	def get_bank_id(self):
		return int.from_bytes(self.get_b()[:4], "little")
	def get_event_count(self):
		return int.from_bytes(self.get_b()[4:8], "little")
	def get_banked_events(self):
		event_count = self.get_event_count()
		return bytes_to_events(self.get_b()[8:8+2*4*event_count], event_count)
	def get_np_events(self):
		event_count = self.get_event_count()
		events_b = self.get_b()[8:8+2*4*event_count]
		events = np.frombuffer(events_b, dtype='<u4').reshape(-1, 2)
		return events
	def get_b(self):
		return self.b
	def to_dict(self):
		return {"bank_id": self.get_bank_id(), "event_count": self.get_event_count(), "banked_events": self.get_banked_events()}
		


class PacketBankLazy(PacketBank):
	def __init__(self, f, position, size):
		self.f = f
		self.position = position	
		self.size = size
	def get_b(self):
		f_lock.acquire()
		self.f.seek(self.position)
		packet_b = self.f.read(self.size)
		f_lock.release()
		return packet_b

def parse_bank_section_old(b):
	bank_id = int.from_bytes(b[:4], "little")
	event_count = int.from_bytes(b[4:8], "little")
	L = bytes_to_events(b[8:8+2*4*event_count], event_count)
	assert len(L) == event_count
	r = {}
	r["bank_id"] = bank_id
	r["event_count"] = event_count
	r["banked_events"] = L
	return r
	
def parse_source_section_lazy(f, start, b):
	source_id = int.from_bytes(b[:4], "little")
	intra_pulse_time = int.from_bytes(b[4:8], "little")
	tof_offset_cor = int.from_bytes(b[8:12], "little")
	tof_offset = select_bits(tof_offset_cor, 0, 31)
	cor = select_bits(tof_offset_cor, 31, 32)
	bank_count = int.from_bytes(b[12:16], "little")
	bank_sections = []
	pointer = 0
	for i in range(bank_count):
		#print(i, bank_count)
		#assert pointer < len(b)
		bank_section_b = b[pointer+16:]
		pb = PacketBank(bank_section_b)
		if f is None:
			bank_section = PacketBank(bank_section_b)
		else:
			bank_section = PacketBankLazy(f, start+pointer+16, len(bank_section_b))
		bank_sections.append(bank_section)
		bss = calc_bank_section_size(bank_section)
		pointer += bss
	r = {}
	r["source_id"] = source_id
	r["intra_pulse_time"] = intra_pulse_time
	r["tof_offset"] = tof_offset
	r["cor"] = cor
	r["bank_count"] = bank_count
	r["bank_sections"] = bank_sections
	return r

def parse_source_section(b):
	return parse_source_section_lazy(None, None, b)

class BeamMonitorEvent:
	def __init__(self, b):
		self.b = b
	def get_b(self):
		return self.b[:4]
	def get_tof(self):
		#print("GETB IS", self.get_b()[:4])
		return select_bits(int.from_bytes(self.get_b(), "little"), 0, 21)
	def get_cycle(self):
		return select_bits(int.from_bytes(self.get_b(), "little"), 21, 31)
	def get_ris(self):
		return select_bits(int.from_bytes(self.get_b(), "little"), 31, 32)
	def to_dict(self):
		return {"tof": self.get_tof(), "cycle": self.get_cycle(), "ris": self.get_ris()}
	

def parse_beam_monitor_section_lazy(f, start, b):
	event_count_monitor_id = int.from_bytes(b[:4], "little")
	event_count = select_bits(event_count_monitor_id, 0, 22)
	monitor_id = select_bits(event_count_monitor_id, 22, 32)
	source_id = int.from_bytes(b[4:8], "little")
	tof_offset_cor = int.from_bytes(b[8:12], "little")
	tof_offset = select_bits(tof_offset_cor, 0, 31)
	cor = select_bits(tof_offset_cor, 31, 32)
	bm_events = []
	pointer = 0
	for i in range(event_count):
		beam_monitor_section_b = b[pointer+12:]
		#pb = PacketBank(bank_section_b)
		bm_event = BeamMonitorEvent(beam_monitor_section_b)
		if f is None:
			bm_event = BeamMonitorEvent(beam_monitor_section_b)
		else:
			bm_event = BeamMonitorEvent(beam_monitor_section_b)
		bm_events.append(bm_event)
		#bss = calc_bank_section_size(bank_section)
		#bss = 12 + len(bm_events)*4
		bss = 4
		#print("lll",beam_monitor_section_b[:bss])
		pointer += bss
	r = {}
	r["monitor_id"] = monitor_id
	r["event_count"] = event_count
	r["source_id"] = source_id
	r["tof_offset"] = tof_offset
	r["cor"] = cor
	#r["bank_count"] = bank_count
	r["monitor_events"] = bm_events
	return r

def parse_beam_monitor_section(b):
	return parse_beam_monitor_section_lazy(None, None, b)


def calc_bank_section_size(bank_section):
	#return 8 + bank_section["event_count"]*8
	return 8 + bank_section.get_event_count()*8

def calc_source_section_size(source_section):
	bank_sections_size = sum([calc_bank_section_size(bank_section) for bank_section in source_section["bank_sections"]])
	return bank_sections_size + 16
	
class AdaraBankedEventPacket(AdaraRawPacket):
	def get_source_sections_b(self):
		return self.get_packet_b()[16+16:]
	def get_source_sections(self):
		pointer = 0
		source_sections = []
		while pointer < len(self.get_source_sections_b()):
			source_section = parse_source_section(self.get_source_sections_b()[pointer:])
			source_sections.append(source_section)
			source_section_size = calc_source_section_size(source_section)
			pointer += source_section_size
		return source_sections
	def get_source_sections_dict(self):
		source_sections = self.get_source_sections()
		for i in range(len(source_sections)):
			e = source_sections[i]
			for j in range(len(e["bank_sections"])):
				source_sections[i]["bank_sections"][j] = source_sections[i]["bank_sections"][j].to_dict()
		return source_sections
	def get_banks(self):
		banks = []
		source_sections = self.get_source_sections()
		for source_section in source_sections:
			bank_sections = source_section["bank_sections"]
			banks += bank_sections
		return banks
		

	def get_events_(self):
		source_sections = self.get_source_sections()
		events = []
		for source_section in source_sections:
			bank_sections = source_section["bank_sections"]
			for bank_section in bank_sections:
				#events += bank_section["banked_events"]
				events += bank_section.get_banked_events()

		return events
	
	def get_accelerator_cycle_b(self):
		return self.get_packet_b()[16+8:16+12]
	def get_charge(self):
		return self.get_packet_bits(16*8, 20*8)
	def get_energy(self):
		return self.get_packet_bits(20*8, 24*8)
	def get_flags(self):
		return self.get_packet_bits(28*8, 28*8+20)
	def get_veto_flags(self):
		return self.get_packet_bits(28*8+20, 28*8+32)
	def get_key_functions(self):
		d = super().get_key_functions()
		d.pop("events")
		additional_fields = {"charge": self.get_charge, "energy": self.get_energy, "cycle": self.get_accelerator_cycle, "flags": self.get_flags, "veto_flags": self.get_veto_flags, "source_sections": self.get_source_sections_dict}
		d.update(additional_fields)
		return d

def calc_beam_monitor_section_size(beam_monitor_section):
	return 4*3 + 4*sum([len(beam_monitor_section["monitor_events"])])
	

class AdaraBeamMonitorEventPacket(AdaraBankedEventPacket):
	def get_beam_monitor_sections_b(self):
		return self.get_packet_b()[16+16:]
	def get_beam_monitor_sections(self):
		pointer = 0
		beam_monitor_sections = []
		while pointer < len(self.get_beam_monitor_sections_b()):
			beam_monitor_section = parse_beam_monitor_section(self.get_beam_monitor_sections_b()[pointer:])
			beam_monitor_sections.append(beam_monitor_section)
			beam_monitor_section_size = calc_beam_monitor_section_size(beam_monitor_section)
			pointer += beam_monitor_section_size
		return beam_monitor_sections
	def get_beam_monitor_sections_dict(self):
		beam_monitor_sections = self.get_beam_monitor_sections()
		for i in range(len(beam_monitor_sections)):
			for j in range(len(beam_monitor_sections[i]["monitor_events"])):
				beam_monitor_sections[i]["monitor_events"][j] = beam_monitor_sections[i]["monitor_events"][j].to_dict()
		return beam_monitor_sections
	def get_key_functions(self):
		d = super().get_key_functions()
		d.pop("source_sections")
		additional_fields = {"beam_monitor_sections": self.get_beam_monitor_sections_dict}
		d.update(additional_fields)
		return d
	def get_events_(self):
		beam_monitor_sections = self.get_beam_monitor_sections()
		events = []
		for beam_monitor_section in beam_monitor_sections:
			monitor_events = beam_monitor_section["monitor_events"]
			for monitor_event in monitor_events:
				#events += bank_section["banked_events"]
				events += [(monitor_event.get_tof(), beam_monitor_section["monitor_id"])]

		return events
	


	
f_lock = threading.Lock()		
class AdaraBankedEventPacketLazy(AdaraBankedEventPacket):
	def __init__(self, f, position):
		self.f = f
		self.position = position
	def get_packet_b(self):
		f_lock.acquire()
		self.f.seek(self.position)
		payload_length_b = self.f.read(4)
		payload_length = int.from_bytes(payload_length_b, "little")
		self.f.seek(self.position)
		packet_b = self.f.read(payload_length + 16)
		f_lock.release()
		return packet_b
	def get_source_sections(self):
		pointer = 0
		source_sections = []
		while pointer < len(self.get_source_sections_b()):
			#source_section = parse_source_section(self.get_source_sections_b()[pointer:])
			source_section = parse_source_section_lazy(self.f, self.position + 32 + pointer, self.get_source_sections_b()[pointer:])
			source_sections.append(source_section)
			source_section_size = calc_source_section_size(source_section)
			pointer += source_section_size
		return source_sections
	
	
class AdaraRTDLPacket(AdaraRawPacket):
	def get_charge_b(self):
		return self.get_packet_b()[16:16+3]
	#def get_flavor_b(self):
	#	return self.get_packet_b()[16+3:16+4]
	def get_flavor(self):
		return self.get_packet_bits(19*8, 20*8)
	def get_cycle(self):
		return self.get_packet_bits(20*8, 20*8+10)
	def get_veto_flags(self):
		return self.get_packet_bits(20*8+10, 20*8+22)
	def get_tstat(self):
		return self.get_packet_bits(20*8+22, 20*8+30)
	def get_bcy(self):
		return self.get_packet_bits(20*8+30, 20*8+31)
	def get_bvt(self):
		return self.get_packet_bits(20*8+31, 20*8+32)
	def get_intra_pulse_time(self):
		return self.get_packet_bits(24*8, 24*8 + 32)
	def get_tof_offset(self):
		return self.get_packet_bits(28*8, 28*8 + 31)
	def get_cor(self):
		return self.get_packet_bits(28*8 + 31, 28*8 + 32)
	def get_ring_period(self):
		return self.get_packet_bits(32*8, 32*8 + 24)
	def get_frames(self):
		num_frames = 25
		L = []
		for i in range(num_frames):
			frame_data = self.get_packet_bits(36*8 + i*8*4, 36*8 + i*8*4 + 24)
			fna = self.get_packet_bits(36*8 + i*8*4 + 24, 36*8 + i*8*4 + 32)
			L.append({"frame_data": frame_data, "fna": fna})
		return L
		

	def get_key_functions(self):
		d = super().get_key_functions()
		d.pop("events")
		additional_fields = {"flavor": self.get_flavor, "cycle": self.get_cycle, "veto_flags": self.get_veto_flags, "tstat": self.get_tstat, "bcy": self.get_bcy, "bvt": self.get_bvt, "intra_pulse_time": self.get_intra_pulse_time, "tof_offset": self.get_tof_offset, "ring_period": self.get_ring_period, "frames": self.get_frames, "cor": self.get_cor}
		d.update(additional_fields)
		return d

class AdaraDoublePacket(AdaraNullPacket):
	def get_device_id(self):
		return int.from_bytes(self.packet_b[16:20], "little")
	def get_variable_id(self):
		return int.from_bytes(self.packet_b[20:24], "little")
	def get_severity(self):
		return int.from_bytes(self.packet_b[24:26], "little")
	def get_status(self):
		return int.from_bytes(self.packet_b[26:28], "little")
	def get_value(self):
		return struct.unpack("<d", self.packet_b[28:36])[0]
	def get_key_functions(self):
		d = super().get_key_functions()
		additional_fields = {"device_id": self.get_device_id, "variable_id": self.get_variable_id, "severity": self.get_severity, "status": self.get_status, "value": self.get_value}
		d.update(additional_fields)
		return d



class AdaraIntPacket(AdaraDoublePacket):
	def get_value(self):
		#return struct.unpack("<I", self.get_packet_b()[28:32])
		return int.from_bytes(self.get_packet_b()[28:32], "little")


class AdaraStringPacket(AdaraDoublePacket):
	def get_string_length(self):
		return int.from_bytes(self.get_packet_b()[28:32], "little")
	def get_value(self):
		return self.get_packet_b()[32:]
	def get_key_functions(self):
		d = super().get_key_functions()
		additional_fields = {"string_length": self.get_string_length}
		d.update(additional_fields)
		return d

class AdaraDoubleArrayPacket(AdaraDoublePacket):
	def get_element_count(self):
		return int.from_bytes(self.get_packet_b()[28:32], "little")
	def get_value(self):
		return struct.unpack("<"+"d"*self.get_element_count(), self.packet_b[32:])
	def get_key_functions(self):
		d = super().get_key_functions()
		additional_fields = {"element_count": self.get_element_count}
		d.update(additional_fields)
		return d


class AdaraBeamMonitorConfigPacket(AdaraNullPacket):
	def get_beam_monitor_count(self):
		return int.from_bytes(self.get_packet_b()[16:20], "little")
	def get_config_sections(self):
		config_sections = []
		for i in range(self.get_beam_monitor_count()):
			section_size = 24
			section_base = 20+i*section_size
			d = {}
			d["monitor_id"] = int.from_bytes(self.get_packet_b()[section_base:section_base+4], "little")
			d["tof_offset"] = int.from_bytes(self.get_packet_b()[section_base+4:section_base+8], "little")
			d["max_tof"] = int.from_bytes(self.get_packet_b()[section_base+8:section_base+12], "little")
			d["histogram_bin_size"] = int.from_bytes(self.get_packet_b()[section_base+12:section_base+16], "little")
			d["monitor_distance"] = struct.unpack("<d", self.packet_b[section_base+16:section_base+24])[0]
			config_sections.append(d)
		for i in range(self.get_beam_monitor_count()):
			section_base = 20+24*self.get_beam_monitor_count() + i*4
			config_sections[i]["monitor_format"] = int.from_bytes(self.get_packet_b()[section_base:section_base+4], "little")
		return config_sections
	def get_key_functions(self):
		d = super().get_key_functions()
		additional_fields = {"beam_monitor_count": self.get_beam_monitor_count, "beam_monitor_config_sections": self.get_config_sections}
		d.update(additional_fields)
		return d


	
class AdaraRunInfoPacket(AdaraNullPacket):
	def get_xml_length(self):
		return int.from_bytes(self.get_packet_b()[16:20], "little")
	def get_xml_run_info(self):
		return self.get_packet_b()[20:]
	def get_key_functions(self):
		d = super().get_key_functions()
		additional_fields = {"xml_length": self.get_xml_length, "xml_run_info": self.get_xml_run_info}
		d.update(additional_fields)
		return d

class AdaraGeometryPacket(AdaraRunInfoPacket):
	def get_xml_geometry_info(self):
		return self.get_xml_run_info()
	def get_key_functions(self):
		d = super().get_key_functions()
		d.pop("xml_run_info")
		additional_fields = {"xml_geometry_info": self.get_xml_geometry_info}
		d.update(additional_fields)
		return d


class AdaraDeviceDescriptorPacket(AdaraDoublePacket):
	def get_descriptor_length(self):
		return int.from_bytes(self.packet_b[20:24], "little")
	def get_xml(self):
		return self.packet_b[24:].decode("utf-8").rstrip('\x00')
	def get_xml_tree(self):
		return ET.fromstring(self.get_xml())
	def get_pv_dict(self):
		pvs_tag = self.get_xml_tree().find("./{http://public.sns.gov/schema/device.xsd}process_variables")
		pvs = pvs_tag.findall("./{http://public.sns.gov/schema/device.xsd}process_variable")
		L = {}
		for pv in pvs:
			d = {}
			pv_name = pv.find('./{http://public.sns.gov/schema/device.xsd}pv_name').text
			pv_id = int(pv.find('./{http://public.sns.gov/schema/device.xsd}pv_id').text)
			pv_connection = pv.find('./{http://public.sns.gov/schema/device.xsd}pv_connection')
			if pv_connection is not None:
				pv_connection = pv_connection.text
			pv_type = pv.find('./{http://public.sns.gov/schema/device.xsd}pv_type').text
			d["pv_name"] = pv_name
			d["pv_id"] = pv_id
			d["pv_connection"] = pv_connection
			d["pv_type"] = pv_type
			d["device_id"] = self.get_device_id()
			assert pv_id not in L.keys()
			L[(self.get_device_id(), pv_id)] = d
		return L
	def get_events(self):
		return []
	def get_key_functions(self):
		d = super().get_key_functions()
		d.pop("variable_id")
		d.pop("severity")
		d.pop("status")
		d.pop("value")
		additional_fields = {"descriptor_length": self.get_descriptor_length, "xml": self.get_xml}
		d.update(additional_fields)
		return d



class Descriptors:
	def __init__(self):
		self.pv_dict = {}
	def handle_descriptor(self, p):
		if p.get_format_int() == 0x800000:
			self.pv_dict = {**self.pv_dict, **p.get_pv_dict()}
	def id_to_pv_name(self, identifier):
		return self.pv_dict[identifier]["pv_name"]
	def pv_name_to_id(self, pv_name):
		for k,v in self.pv_dict.items():
			if v["pv_name"] == pv_name:
				return k
	def get_pv_names(self):
		return [e["pv_name"] for e in self.pv_dict.values()]
	def packet_to_pv_name(self, p):
		did = p.get_device_id()
		pid = p.get_variable_id()
		return self.id_to_pv_name((did, pid))
		
def search_linear(data, t, method):
	pprev = data[0]
	for i in range(len(data)):
		p = data[i]
		assert p.get_timestamp() >= pprev.get_timestamp()
		pprev = p
		if t<=p.get_timestamp():
			if method=="before":
				if i<1:
					return None
				return data[i-1]
			return p
	
	if method == "before":
		return data[-1]

def search_binary_closest_index(data, t, start, end):
    if end == start + 1:
        return start
    middle_i = start + (end - start)//2
    if data[middle_i].get_timestamp() < t:
        return search_binary_closest_index(data, t, middle_i, end)
    return search_binary_closest_index(data, t, start, middle_i)

def search_binary(data, t, method):
	p_i = search_binary_closest_index(data, t, 0, len(data))
	if method =="before" and data[p_i].get_timestamp() > t:
		if p_i < 1:
			#return data[0]
			return None
		if not (data[p_i - 1].get_timestamp() <= t):
			print(data[p_i - 1].get_timestamp(), t, data[p_i].get_timestamp())
		assert data[p_i - 1].get_timestamp() <= data[p_i].get_timestamp()
		assert data[p_i - 1].get_timestamp() <= t
		return data[p_i - 1]
	if method =="after" and data[p_i].get_timestamp() < t:
		if p_i == len(data) - 1:
			return None
		assert data[p_i + 1].get_timestamp() >= t
		return data[p_i + 1]
	return data[p_i]

		
	
class DoubleArchiver:
	def __init__(self):
		self.descriptors = Descriptors()
		self.data_dict = {}
	def archive_packet(self, p):
		self.descriptors.handle_descriptor(p)
		if p.get_format_int() not in [0x800100, 0x800200]:
			return
		did = p.get_device_id()
		pid = p.get_variable_id()
		if (did, pid) not in self.data_dict.keys():
			self.data_dict[(did, pid)] = []
		self.data_dict[(did, pid)].append(p)
	def get_data_by_id(self, identifier):
		if identifier not in self.data_dict.keys():
			#print(identifier, "not in:", self.data_dict.keys())
			return None
		return self.data_dict[identifier]
	def get_data_by_pv_name(self, pv_name):
		identifier = self.descriptors.pv_name_to_id(pv_name)
		return self.get_data_by_id(identifier)
	def get_double_by_pv_name(self, pv_name, t, method="after"):
		data = self.get_data_by_pv_name(pv_name)
		if data is None:
			return None
		#return search_linear(data, t, method)
		#assert search_linear(data, t, method).get_timestamp() == search_binary(data, t, method).get_timestamp()
		return search_binary(data, t, method)
class AdaraFileReader:
	def __init__(self, filename=None):
		self.set_filename(filename)
	def get_filename(self):
		return self.filename
	def set_filename(self, filename):
		self.filename = filename
	def get_size(self):
		return os.path.getsize(self.get_filename())
	def read(self):
		f = open(self.get_filename(), "rb")
		self.packets = read_all_packets(f)
	def read_generator(self):
		f = open(self.get_filename(), "rb")
		self.f = f
		return read_packets_generator(f)
	
	def get_all_events(self):
		all_events_b_L = []
		event_lengths = []
		timestamps = []
		count = 0
		for p in self.packets:
			count += 1
			if p.get_format_int()!=0x300:
				continue
			timestamps.append(p.get_timestamp())
			all_events_b_L.append(p.get_events_b())
		all_events_b = b''.join(all_events_b_L)
		num_events = len(all_events_b)//8
		L = struct.unpack("<"+"ii"*num_events, all_events_b)

class AdaraMultiFileReader:
	def __init__(self, filename_list=[]):
		self.set_filename_list(filename_list)
	def set_filename_list(self, filename_list):
		self.filename_list = filename_list
	def read_generator(self):
		for fn in self.filename_list:
			reader = AdaraFileReader(fn)
			g = reader.read_generator()
			for packet in g:
				yield packet
			#reader.f.close()
	def get_size(self):
		return sum([AdaraFileReader(fn).get_size() for fn in self.filename_list])


def get_all_files(base_path):
	try:
		walk = os.walk(base_path)
	except:
		walk = os.walk("./")
	paths = []
	for e in walk:
		for f in e[2]:
			if "ds" not in f and "m000" not in f:
				continue
			if "adara" not in f:
				continue
			full_path = e[0]+"/"+f
			paths.append(full_path)
	return paths

class AdaraRunReader:
	def __init__(self, run_number):
		self.run_number = int(run_number)
		self.fr = AdaraMultiFileReader(self.get_run_files())
	def get_all_files(self):
		base_path = "/SNSlocal/sms/data/"
		return get_all_files(base_path)
	def get_run_files(self):
		paths = self.get_all_files()
		paths = [p for p in paths if "run-"+str(self.run_number)+".adara" in p]
		#paths = [p for p in paths if "ds00000001" in p]
		paths = [p for p in paths if "/m00000001" in p]
		paths = sorted(paths)
		return paths
	def read_generator(self):
		return self.fr.read_generator()
	def get_size(self):
		return self.fr.get_size()

import socket


CLIENT_HELLO_TYPE = 0x4006
ADARA_HELLO_PACKET = struct.pack('IIIII', 4, CLIENT_HELLO_TYPE<<8, 0 , 0, 0)



class BufferedSocket:
	def __init__(self, ip, port):
		self.position = 0
		self.first_position = 0
		self.running_buffer = b''
		self.sd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.sd.connect((ip, port))
		self.sd.send(ADARA_HELLO_PACKET)
		self.max_buffer_size = 100000
	def shrink_buffer(self):
		while len(self.running_buffer) > self.max_buffer_size:
			diff = len(self.running_buffer)-self.max_buffer_size
			self.running_buffer = self.running_buffer[diff:]
			self.first_position += diff
	def read(self, num_bytes):
		self.shrink_buffer()
		start_pos = self.get_buffer_position()
		end_pos = start_pos + num_bytes
		while end_pos > len(self.running_buffer):
			self.running_buffer += self.sd.recv(num_bytes)
		r = self.running_buffer[start_pos:end_pos]
		self.seek(self.tell() + num_bytes)
		return r
	def tell(self):
		return self.position
	def get_buffer_position(self):
		return self.tell() - self.first_position
	def seek(self, pos):
		self.position = pos
	


class AdaraLiveStreamReader(AdaraFileReader):
	def __init__(self, ip, port):
		self.buffered_stream = BufferedSocket(ip, port)
	def read_generator(self):
		self.f = self.buffered_stream
		return read_packets_generator(self.f, lazy=False)
	def get_size(self):
		return 1
	

def read_packet(f, shared_file):
	p = f.tell()
	payload_length_b = f.read(4)
	payload_length = int.from_bytes(payload_length_b, "little")
	f.seek(p)
	packet_b = f.read(payload_length + 16)
	if not packet_b:
		return None
	format_int = packet_bytes_to_format_int(packet_b)
	if format_int == 0x300:
		return AdaraEventPacket(packet_b)
	if format_int == 0x100:
		return AdaraRTDLPacket(packet_b)
	if format_int == 0:
		return AdaraRawPacket(packet_b)
	if format_int == 0x400301:
		return AdaraRunStatusPacket(packet_b)
	if format_int == 0x400400:
		return AdaraRunInfoPacket(packet_b)
	if format_int == 0x400700:
		return AdaraStreamAnnotationPacket(packet_b)
	if format_int == 0x400A00:
		return AdaraGeometryPacket(packet_b)
	if format_int == 0x400800:
		return AdaraSyncPacket(packet_b)
	if format_int == 0x400B01:
		return AdaraBeamlineInfoPacket(packet_b)
	if format_int == 0x400D01:
		return AdaraBeamMonitorConfigPacket(packet_b)
	if format_int == 0x400001:
		if shared_file is not None:
			return AdaraBankedEventPacketLazy(shared_file, p)
		return AdaraBankedEventPacket(packet_b)
	if format_int == 0x400101:
		return AdaraBeamMonitorEventPacket(packet_b)
	if format_int == 0x800000:
		return AdaraDeviceDescriptorPacket(packet_b)
	if format_int == 0x800200:
		return AdaraDoublePacket(packet_b)
	if format_int == 0x800300:
		return AdaraStringPacket(packet_b)
	if format_int == 0x800500:
		return AdaraDoubleArrayPacket(packet_b)
	if format_int == 0x800100:
		return AdaraIntPacket(packet_b)
	if format_int == 0x410201:
		return AdaraPixelMappingTablePacket(packet_b)
		
	return AdaraNullPacket(packet_b)

class ManagedFile:
	file_name_list = []
	file_dict = {}
	queue_size = 100
	def __init__(self, name, rw):
		self.name = name
		self.rw = rw
	def get_file(self):
		#if self.name not in ManagedFile.file_name_list:
		if self.name not in ManagedFile.file_dict.keys():
			ManagedFile.file_name_list.append(self.name)
			ManagedFile.file_dict[self.name] = open(self.name, self.rw)
			if len(ManagedFile.file_name_list) > ManagedFile.queue_size:
				ManagedFile.file_dict[ManagedFile.file_name_list[0]].close()
				del ManagedFile.file_dict[ManagedFile.file_name_list[0]]
				ManagedFile.file_name_list = ManagedFile.file_name_list[1:]
		return ManagedFile.file_dict[self.name]
	def read(self, n):
		return self.get_file().read(n)
	def seek(self, n):
		return self.get_file().seek(n)
	def tell(self):
		return self.get_file().tell()

			

#shared_file = None
def read_packets_generator(f, lazy=True):
	#global shared_file
	#if lazy and shared_file is None:
	if lazy:
		#shared_file = open(f.name, "rb")
		shared_file = ManagedFile(f.name, "rb")
		#shared_file = f
	else:
		shared_file = None
	p = read_packet(f, shared_file)
	while p is not None:
		yield p
		p = read_packet(f, shared_file)
	#if shared_file is not None:
	#	shared_file.close()

def read_all_packets(f):
	return list(read_packets_generator(f))
	

