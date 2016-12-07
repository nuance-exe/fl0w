from Highway import Route, Pipe, Client
import Logging
import Config
import Utils


import socket
import time
import os
import sys
import subprocess
from random import randint
from _thread import start_new_thread

CHANNEL = 2
IS_WALLABY = Utils.is_wallaby()
PATH = "/home/root/Documents/KISS/bin/" if IS_WALLABY else (sys.argv[1] if len(sys.argv) > 1 else None)

LIB_WALLABY = "/usr/lib/libwallaby.so"

if not PATH:
	Logging.error("No path specified. (Necessary on simulated Wallaby controllers.)")
	exit(1)

if not IS_WALLABY:
	Logging.warning("Binaries that were created for Wallaby Controllers will not run on a simulated Wallaby.")


class SensorReadout:
	ANALOG = 1
	DIGITAL = 2
	NAMED_MODES = {ANALOG : "analog", DIGITAL : "digital"}
	MODES = tuple(NAMED_MODES.keys())


	def __init__(self, handler, poll_rate=0.2):
		self.poll_rate = poll_rate
		self.handler = handler
		self.peers = {}
		self.readout_required = {SensorReadout.ANALOG : [],
			SensorReadout.DIGITAL : []}
		self.generate_random_values = False
		# Running on actual hardware?
		if Utils.is_wallaby():
			if not os.path.exists(LIB_WALLABY):
				Logging.error("The Wallaby library should normally exist on "
					"a Wallaby. You broke something, mate.")
				self.generate_random_values = True
		else:
			Logging.warning("Sensor data can not be read on a dev-system. "
				"Generating random values instead.")
			self.generate_random_values = True
		# Generate random values?
		if not self.generate_random_values:
			self.wallaby_library = cdll.LoadLibrary(LIB_WALLABY)
			self.get_sensor_value = self.__get_sensor_value
		else:
			self.get_sensor_value = self.__get_random_value
		start_new_thread(self._sensor_fetcher, ())


	def subscribe(self, port, mode, peer):
		if port not in self.readout_required[mode]:
			self.readout_required[mode].append(port)
		if not peer in self.peers:
			self.peers[peer] = {SensorReadout.ANALOG : [],
			SensorReadout.DIGITAL : []}
		self.peers[peer][mode].append(port)


	def unsubscribe(self, port, mode, peer):
		if peer in self.peers:
			if port in self.peers[peer][mode]:
				del self.peers[peer][mode][self.peers[peer][mode].index(port)]
		readout_still_required = False
		for peer in self.peers:
			if port in self.peers[peer][mode]:
				readout_required = True
				break
		if not readout_required:
			del self.readout_required[mode][self.readout_required[mode].index(port)]


	def __get_sensor_value(self, port, mode):
		if mode == SensorReadout.ANALOG:
			return self.wallaby_library.analog(port)
		elif mode == SensorReadout.DIGITAL:
			return self.wallaby_library.digital(port)


	def __get_random_value(self, port, mode):
		if mode == SensorReadout.ANALOG:
			return randint(0, 4095)
		elif mode == SensorReadout.DIGITAL:
			return randint(0, 1)



	def _sensor_fetcher(self):
		while True:
			current_values = {SensorReadout.ANALOG : {},
			SensorReadout.DIGITAL : {}}
			for mode in SensorReadout.MODES:
				for port in self.readout_required[mode]:
					current_values[mode][port] = self.get_sensor_value(port, mode)
			for peer in self.peers:
				response = {"analog" : {}, "digital" : {}}
				for mode in SensorReadout.NAMED_MODES:
					for port in self.peers[peer][mode]:
						response[SensorReadout.NAMED_MODES[mode]][port] = current_values[mode][port]
				self.handler.pipe(response, "sensor", peer)
			time.sleep(self.poll_rate)

	@staticmethod
	def valid_port(port, mode):
		if mode == SensorReadout.ANALOG:
			return port >= 0 and port <= 5
		elif mode == SensorReadout.DIGITAL:
			return port >= 0 and port <= 9
		return False



class Sensor(Pipe):
	"""
	{"subscribe" : {"analog" : [1, 2, 3], "digital" : [1, 2, 3]}}
	{"unsubscribe" : {"analog" : [1, 2, 3], "digital" : [1, 2, 3]}}
	"""
	def run(self, data, peer, handler):
		if type(data) is dict:
			for event in ("subscribe", "unsubscribe"):
				if event in data:
					for mode in ("analog", "digital"):
						if mode in data[event]:
							for port in data[event][mode]:
								if type(port) is int:
									if mode == "analog":
										mode = SensorReadout.ANALOG
									elif mode == "digital":
										mode = SensorReadout.DIGITAL
									if SensorReadout.valid_port(port, mode):
										if event == "subscribe":
											self.sensor_readout.subscribe(port, mode, peer)
										elif event == "unsubscribe":
											self.sensor_readout.unsubscribe(port, mode, peer)

	def start(self, handler):
		self.sensor_readout = SensorReadout(handler)



class WallabyControl(Route):
	def __init__(self, output_unbuffer):
		self.output_unbuffer = output_unbuffer
		self.actions_with_params = {"run" : self.run_program}
		self.actions_without_params = {"disconnect" : self.disconnect,
		"reboot" : self.reboot, "shutdown" : self.shutdown, "stop" : self.stop}
		self.currently_running_program = None

	def run(self, data, handler):
		if type(data) is str:
			if data in self.actions_without_params.keys():
				self.actions_without_params[data](handler)
		elif type(data) is dict:
			for action in data:
				if action in self.actions_with_params.keys():
					_thread.start_new_thread(self.actions_with_params[action], (handler, data[action]))


	def run_program(self, handler, program):
		command = [self.output_unbuffer, "-i0", "-o0", "-e0"]
		command.append("%s%s/botball_user_program" % (handler.sync.folder, program))
		self.currently_running_program = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

		# Poll process for new output until finished
		for line in iter(self.currently_running_program.stdout.readline, b""):
			handler.sock.send(line.decode(), "std_stream")

		self.currently_running_program.wait()
		handler.sock.send({"return_code" : self.currently_running_program.returncode}, "std_stream")
		self.currently_running_program = None


	def stop(self, handler):
		if self.currently_running_program != None:
			Logging.info("Killing currently running programm.")
			self.currently_running_program.kill()
		else:
			Logging.info("No program started by fl0w.")


	def reboot(self, handler):
		self.disconnect(handler)
		os.system("reboot")
		exit(0)

	def shutdown(self, handler):
		self.disconnect(handler)
		os.system("shutdown -h 0")

	def disconnect(self, handler):
		self.stop(handler)
		handler.sock.close()



class Subscribe(Route):
	def start(self, handler):
		handler.send({"name" : Utils.get_hostname(), "channel" : CHANNEL}, "subscribe")


class Hostname(Pipe):
	def run(self, data, peer, handler):
		if type(data) is dict:
			if "set" in data:
				set_hostname(str(data["set"]))


class Processes(Pipe):
	def run(self, data, peer, handler):
		handler.pipe(
			subprocess.check_output(["ps", "aux"]).decode().split("\n")[1:-1],
			"processes", peer)


class Handler(Client):
	def setup(self, routes, debug=False):
		super().setup(routes, piping=True, debug=debug)




CONFIG_PATH = "wallaby.cfg"

config = Config.Config()
config.add(Config.Option("server_address", "ws://127.0.0.1:3077"))
config.add(Config.Option("debug", False, validator=lambda x: True if True or False else False))
config.add(Config.Option("output_unbuffer", "stdbuf"))

try:
	config = config.read_from_file(CONFIG_PATH)
except FileNotFoundError:
	config.write_to_file(CONFIG_PATH)
	config = config.read_from_file(CONFIG_PATH)


try:
	ws = Handler(config.server_address)
	# setup has to be called before the connection is established
	ws.setup({"subscribe" : Subscribe(), "hostname" : Hostname(),
		"processes" : Processes(), "sensor" : Sensor()},
		debug=config.debug)
	ws.connect()
	ws.run_forever()
except KeyboardInterrupt:
	ws.close()