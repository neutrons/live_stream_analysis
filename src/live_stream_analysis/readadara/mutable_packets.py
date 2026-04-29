import struct
#int_fmt = "<i"
int_fmt = "<I"
uint64_fmt = "<Q"
class AdaraRawMutablePacket:
	def __init__(self, data_):
		self.data = data_
	def pack_payload(self):
		return struct.pack(int_fmt, self.data["payload_length"])
	def pack_format_int(self):
		return struct.pack(int_fmt, self.data["format_int"])
	def pack_timestamp_s(self):
		#timestamp_s = int(self.data["timestamp"])
		timestamp_s = self.data["timestamp_s"]
		return struct.pack(int_fmt, timestamp_s)
	def pack_timestamp_ns(self):
		#timestamp_ns = int((self.data["timestamp"]%1)*10**9)
		timestamp_ns = self.data["timestamp_ns"]
		return struct.pack(int_fmt, timestamp_ns)
	def pack_header(self):
		return self.pack_payload() + self.pack_format_int() + self.pack_timestamp_s() + self.pack_timestamp_ns()
	def assemble_bytes(self):
		return self.pack_payload() + self.pack_format_int() + self.pack_timestamp_s() + self.pack_timestamp_ns()

class AdaraRTDLMutablePacket(AdaraRawMutablePacket):
	def pack_pulse_charge_flavor(self):
		pulse_charge = self.data["charge"]//10
		flavor = self.data["flavor"]
		d = pulse_charge | (flavor << 24)
		return struct.pack(int_fmt, d)
	def pack_cycle_veto_tstat(self):
		cycle = self.data["cycle"]
		veto = self.data["veto_flags"]
		tstat = self.data["tstat"]
		bcy = self.data["bcy"]
		bvt = self.data["bvt"]
		d = cycle | (veto << 10) | (tstat << 22)
		return struct.pack(int_fmt, d)
	def pack_intra_pulse_time(self):
		d = self.data["intra_pulse_time"]
		return struct.pack(int_fmt, d)
	def pack_tof_offset_cor(self):
		tof_offset = self.data["tof_offset"]
		cor = self.data["cor"]
		d = tof_offset | (cor << 31)
		return struct.pack(int_fmt, d)
	def pack_ring_period(self):
		ring_period = self.data["ring_period"]
		d = ring_period | (4 << 24)
		return struct.pack(int_fmt, d)
	def pack_frames(self):
		frames = self.data["frames"]
		frames_bytes = b''
		for frame in frames:
			frame_data = frame["frame_data"]
			fna = frame["fna"]
			d = frame_data | (fna << 24)
			frames_bytes += struct.pack(int_fmt, d)
		return frames_bytes
	def assemble_bytes(self):
		return self.pack_header() + self.pack_pulse_charge_flavor() + self.pack_cycle_veto_tstat() + self.pack_intra_pulse_time() + self.pack_tof_offset_cor() + self.pack_ring_period() + self.pack_frames()

class AdaraSyncMutablePacket(AdaraRawMutablePacket):
	def pack_signature(self):
		return self.data["signature"]
	def pack_file_offset(self):
		return struct.pack(uint64_fmt, self.data["file_offset"])
	def pack_comment_length(self):
		return struct.pack(int_fmt, self.data["comment_length"])
	def pack_comment(self):
		return self.data["comment"]
	def assemble_bytes(self):
		return self.pack_header() + self.pack_signature() + self.pack_file_offset() + self.pack_comment_length() + self.pack_comment()

class AdaraRunStatusMutablePacket(AdaraRawMutablePacket):
	def pack_run_number(self):
		return struct.pack(int_fmt, self.data["run_number"])
	def pack_run_start(self):
		return struct.pack(int_fmt, self.data["run_start"])
	def pack_file_number_status(self):
		d = self.data["file_number"] | (self.data["status"]<<24)
		return struct.pack(int_fmt, d)
	def pack_pause_file_number_paused(self):
		d = self.data["pause_file_number"] | (self.data["paused"]<<24)
		return struct.pack(int_fmt, d)
	def pack_addendum_file_number_addendum(self):
		d = self.data["addendum_file_number"] | (self.data["addendum"]<<24)
		return struct.pack(int_fmt, d)
	def assemble_bytes(self):
		return self.pack_header() + self.pack_run_number() + self.pack_run_start() + self.pack_file_number_status() + self.pack_pause_file_number_paused() + self.pack_addendum_file_number_addendum()

class AdaraBeamlineInfoMutablePacket(AdaraRawMutablePacket):
	def pack_long_short_id_target(self):
		d = self.data["long_length"] | (self.data["short_length"]<<8) | (self.data["id_length"]<<16) | (self.data["target_station_num"]<<24)
		return struct.pack(int_fmt, d)
	def pack_beamline_data(self):
		return self.data["beamline_data"]
	def assemble_bytes(self):
		return self.pack_header() + self.pack_long_short_id_target() + self.pack_beamline_data()


class AdaraPixelMappingTableMutablePacket(AdaraRawMutablePacket):
	def pack_num_banks(self):
		return struct.pack(int_fmt, self.data["num_banks"])
	def pack_mapping_data(self):
		mapping_data = self.data["mapping_data"]
		mapping_bytes = b''
		for mapping_entry in mapping_data:
			mapping_bytes += struct.pack(int_fmt, mapping_entry["base_physical_id"])
			count = mapping_entry["count"]
			is_shorthand = 1 if mapping_entry["is_shorthand"] else 0
			bank_id = mapping_entry["bank_id"]
			mapping_bytes += struct.pack(int_fmt, count | (is_shorthand << 15) | (bank_id << 16))
			if mapping_entry["is_shorthand"]:
				mapping_bytes += struct.pack(int_fmt, mapping_entry["stopping_physical_id"])
				mapping_bytes += struct.pack(int_fmt, mapping_entry["base_logical_id"])
				mapping_bytes += struct.pack(int_fmt, mapping_entry["logical_step"] | (mapping_entry["physical_step"] << 16))
				mapping_bytes += struct.pack(int_fmt, mapping_entry["stopping_logical_id"])
		return mapping_bytes
	def assemble_bytes(self):
		return self.pack_header() + self.pack_num_banks() + self.pack_mapping_data()
		

class AdaraRunInfoMutablePacket(AdaraRawMutablePacket):
	def pack_xml_length(self):
		d = self.data["xml_length"]
		return struct.pack(int_fmt, d)
	def pack_xml_run_info(self):
		return self.data["xml_run_info"]
	def assemble_bytes(self):
		return self.pack_header() + self.pack_xml_length() + self.pack_xml_run_info()


class AdaraStreamAnnotationMutablePacket(AdaraRawMutablePacket):
	def pack_comment_length_type_rst(self):
		d = self.data["comment_length"] | (self.data["type"] << 16) | (self.data["rst"] << 31)
		return struct.pack(int_fmt, d)
	def pack_scan_index(self):
		return struct.pack(int_fmt, self.data["scan_index"])
	def pack_comment(self):
		return self.data["comment"]
	def assemble_bytes(self):
		return self.pack_header() + self.pack_comment_length_type_rst() + self.pack_scan_index() + self.pack_comment()

class AdaraGeometryMutablePacket(AdaraRunInfoMutablePacket):
	def pack_xml_geometry_info(self):
		return self.data["xml_geometry_info"]
	def assemble_bytes(self):
		return self.pack_header() + self.pack_xml_length() + self.pack_xml_geometry_info()

class AdaraDeviceDescriptorMutablePacket(AdaraRawMutablePacket):
	def pack_device_id(self):
		return struct.pack(int_fmt, self.data["device_id"])
	def pack_descriptor_length(self):
		return struct.pack(int_fmt, self.data["descriptor_length"])
	def pack_xml(self):
		xml_data = self.data["xml"].encode('utf-8')
		return xml_data + b'\x00'*((4-len(xml_data)%4)%4)
	def assemble_bytes(self):
		return self.pack_header() + self.pack_device_id() + self.pack_descriptor_length() + self.pack_xml()

class AdaraDoubleMutablePacket(AdaraDeviceDescriptorMutablePacket):
	def pack_variable_id(self):
		return struct.pack(int_fmt, self.data["variable_id"])
	def pack_severity_status(self):
		d = self.data["severity"] | (self.data["status"] << 16)
		return struct.pack(int_fmt, d)
	def pack_value(self):
		return struct.pack("<d", self.data["value"])
	def assemble_bytes(self):
		return self.pack_header() + self.pack_device_id() + self.pack_variable_id() + self.pack_severity_status() + self.pack_value()
		
class AdaraIntMutablePacket(AdaraDoubleMutablePacket):
	def pack_value(self):
		return struct.pack(int_fmt, self.data["value"])
		
class AdaraStringMutablePacket(AdaraDoubleMutablePacket):
	def pack_string_length(self):
		return struct.pack(int_fmt, self.data["string_length"])
	def pack_value(self):
		return self.data["value"]
	def assemble_bytes(self):
		return self.pack_header() + self.pack_device_id() + self.pack_variable_id() + self.pack_severity_status() + self.pack_string_length() + self.pack_value()
		
class AdaraDoubleArrayMutablePacket(AdaraDoubleMutablePacket):
	def pack_element_count(self):
		return struct.pack(int_fmt, self.data["element_count"])
	def pack_value(self):
		value_b = b''
		for e in self.data["value"]:
			value_b += struct.pack("<d", self.data["value"])
		return value_b
	def assemble_bytes(self):
		return self.pack_header() + self.pack_device_id() + self.pack_variable_id() + self.pack_severity_status() + self.pack_element_count() + self.pack_value()

class AdaraBankedEventMutablePacket(AdaraRawMutablePacket):
	def pack_pulse_charge(self):
		pulse_charge = self.data["charge"]
		return struct.pack(int_fmt, pulse_charge)
	def pack_pulse_energy(self):
		pulse_energy = self.data["energy"]
		return struct.pack(int_fmt, pulse_energy)
	def pack_cycle(self):
		cycle = self.data["cycle"]
		return struct.pack(int_fmt, cycle)
	def pack_flags_veto_flags(self):
		flags = self.data["flags"]
		veto_flags = self.data["veto_flags"]
		d = flags | (veto_flags << 20)
		return struct.pack(int_fmt, d)
	def pack_source_sections(self):
		source_sections = self.data["source_sections"]
		source_sections_b = b''
		for source_section in source_sections:
			source_id = source_section["source_id"]
			intra_pulse_time = source_section["intra_pulse_time"]
			tof_offset = source_section["tof_offset"]
			cor = source_section["cor"]
			bank_count = source_section["bank_count"]
			source_header = struct.pack(int_fmt, source_id) + struct.pack(int_fmt, intra_pulse_time) + struct.pack(int_fmt, tof_offset | (cor<<31)) + struct.pack(int_fmt, bank_count)
			bank_sections = source_section["bank_sections"]
			bank_sections_b = b''
			for bank_section in bank_sections:
				bank_id = bank_section["bank_id"]
				event_count = bank_section["event_count"]
				banked_events = bank_section["banked_events"]
				banked_events_b = b''
				for event in banked_events:
					event_b = struct.pack(int_fmt, event[0]) + struct.pack(int_fmt, event[1])
					banked_events_b += event_b
				bank_sections_b += struct.pack(int_fmt, bank_id) + struct.pack(int_fmt, event_count) + banked_events_b
			source_sections_b += source_header + bank_sections_b
		return source_sections_b

	def assemble_bytes(self):
		return self.pack_header() + self.pack_pulse_charge() + self.pack_pulse_energy() + self.pack_cycle() + self.pack_flags_veto_flags() + self.pack_source_sections()

class AdaraBeamMonitorEventMutablePacket(AdaraBankedEventMutablePacket):
	def pack_beam_monitor_sections(self):
		beam_monitor_sections = self.data["beam_monitor_sections"]
		beam_monitor_sections_b = b''
		for beam_monitor_section in beam_monitor_sections:
			monitor_id = beam_monitor_section["monitor_id"]
			source_id = beam_monitor_section["source_id"]
			event_count = beam_monitor_section["event_count"]
			tof_offset = beam_monitor_section["tof_offset"]
			cor = beam_monitor_section["cor"]
			#print(event_count, monitor_id, event_count | (monitor_id << 22))
			#assert event_count == -1
			section_header = struct.pack(int_fmt, event_count | (monitor_id << 22)) + struct.pack(int_fmt, source_id) + struct.pack(int_fmt, tof_offset | (cor<<31))
			monitor_events = beam_monitor_section["monitor_events"]
			monitor_events_b = b''
			for monitor_event in monitor_events:
				tof = monitor_event["tof"]
				cycle = monitor_event["cycle"]
				ris = monitor_event["ris"]
				monitor_events_b += struct.pack(int_fmt, tof | (cycle << 21) | (ris << 31))
			beam_monitor_sections_b += section_header + monitor_events_b
		return beam_monitor_sections_b

	def assemble_bytes(self):
		return self.pack_header() + self.pack_pulse_charge() + self.pack_pulse_energy() + self.pack_cycle() + self.pack_flags_veto_flags() + self.pack_beam_monitor_sections()
	
class AdaraBeamMonitorConfigMutablePacket(AdaraRawMutablePacket):
	def pack_beam_monitor_count(self):
		return struct.pack(int_fmt, self.data["beam_monitor_count"])
	def pack_beam_monitor_config_sections(self):
		config_sections_b = b''
		for section in self.data["beam_monitor_config_sections"]:
			config_sections_b += struct.pack(int_fmt, section["monitor_id"])
			config_sections_b += struct.pack(int_fmt, section["tof_offset"])
			config_sections_b += struct.pack(int_fmt, section["max_tof"])
			config_sections_b += struct.pack(int_fmt, section["histogram_bin_size"])
			config_sections_b += struct.pack("<d", section["monitor_distance"])
			
		for section in self.data["beam_monitor_config_sections"]:
			config_sections_b += struct.pack(int_fmt, section["monitor_format"])
		return config_sections_b

	def assemble_bytes(self):
		return self.pack_header() + self.pack_beam_monitor_count() + self.pack_beam_monitor_config_sections()
