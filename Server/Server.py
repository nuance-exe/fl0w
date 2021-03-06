import Logging
import Config

from .Broadcast import Broadcast

import json
import os
import subprocess
import re
import pwd
import platform
import struct
from subprocess import Popen, PIPE

from wsgiref.simple_server import make_server
from ws4py.server.wsgirefserver import WSGIServer, WebSocketWSGIRequestHandler
from ws4py.server.wsgiutils import WebSocketWSGIApplication

from Highway import Server, Route, DummyPipe


class Info(Route):
	def run(self, data, handler):
		handler.send({"routes" : list(handler.routes.keys())}, "info")


class Compile:
	HAS_MAIN = re.compile(r"\w*\s*main\(\)\s*(\{|.*)$")

	@staticmethod
	def is_valid_c_program(path):
		for line in open(path, "r").read().split("\n"):
			if Compile.HAS_MAIN.match(line):
				return True
		return False


	def __init__(self, source_path, binary_path):
		self.source_path = os.path.abspath(source_path) + "/"
		self.binary_path = os.path.abspath(binary_path) + "/"
		self.wallaby_library_avaliable = os.path.isfile("/usr/local/lib/libaurora.so") and os.path.isfile("/usr/local/lib/libdaylite.so")
		if not self.wallaby_library_avaliable:
			Logging.warning("Wallaby library not found. All Wallaby functions are unavaliable.")
		if platform.machine() != "armv7l":
			Logging.warning("Wrong processor architecture! Generated binaries will not run on Wallaby Controllers.")


	def compile(self, path, relpath, handler=None):
		if relpath.endswith(".c") and Compile.is_valid_c_program(path + relpath):
			name = "-".join(relpath.split("/")).rstrip(".c")
			full_path = self.binary_path + name
			if not os.path.exists(full_path):
				os.mkdir(full_path)
			error = True
			command = ["gcc", "-pipe", "-O0", "-lwallaby", "-I%s" % self.source_path, "-o", "%s" % full_path + "/botball_user_program", path + relpath]
			if not self.wallaby_library_avaliable:
				del command[command.index("-lwallaby")]
			p = Popen(command, stdout=PIPE, stderr=PIPE)
			error = False if p.wait() == 0 else True
			result = ""
			for line in p.communicate():
				result += line.decode()
			if handler != None:
				handler.send({"failed" : error, "returned" : result, "relpath" : relpath}, self.handler.reverse_routes[self])



class Subscribe(Route):
	EDITOR = 1
	WALLABY = 2
	WEB = 3
	CHANNELS = [EDITOR, WALLABY, WEB]

	def run(self, data, handler):
		if type(data) is dict:
			if "channel" in data:
				if data["channel"] in Subscribe.CHANNELS:
					handler.channel = data["channel"]
					handler.broadcast.add(handler, handler.channel)
				if handler.debug:
					Logging.info("'%s:%i' has identified as a %s client." % (handler.address, handler.port,
						"Editor" if handler.channel == Subscribe.EDITOR else
						"Controller" if handler.channel == Subscribe.WALLABY else
						"Web" if handler.channel == Subscribe.WEB else
						"Unknown (will not subscribe to broadcast)"))
			if "name" in data:
				handler.name = data["name"]
			handler.routes["peers"].push_changes(handler)


class WhoAmI(Route):
	def run(self, data, handler):
		handler.send({"id" : handler.id_, 
			"user" : pwd.getpwuid(os.getuid()).pw_name}, 
			handler.reverse_routes[self])


class Peers(Route):
	"""
	{"subscribe" : [1, 2]}
	{"unsubscribe" : [1, 2]}
	{"channels" : [1, 2]}
	"""
	def __init__(self):
		self.subscriptions = {}

	def run(self, data, handler):
		for event in ("subscribe", "unsubscribe", "channels"):
			if event in data:
				channels = []
				for channel in data[event]:
					if channel in Subscribe.CHANNELS:
						channels.append(channel)
				if event == "unsubscribe":
					for channel in channels:
						self.unsubscribe(handler, channel)
				else:
					if event == "subscribe":
						for channel in channels:
							self.subscribe(handler, channel)
					# Send on channels and on subscribe
					self.send_connected_peers(handler, channels)						


	def send_connected_peers(self, handler, channels):
		out = {}
		peers = handler.peers
		for peer_id in peers:
			# Only check for type inclusion if check_type is True
			peer = peers[peer_id]
			if peer.channel in channels:
				if peer is not handler:
					out[peer_id] = {"name" : peer.name,
					"address" : peer.address, "port" : peer.port,
					"channel" : peer.channel}
		handler.send(out, handler.reverse_routes[self])


	def subscribe(self, handler, channel):
		if handler not in self.subscriptions:
			self.subscriptions[handler] = [channel]
		else:
			if channel not in self.subscriptions[handler]:
				self.subscriptions[handler].append(channel)


	def unsubscribe(self, handler, channel):
		if handler in self.subscriptions:
			if channel in self.subscriptions[handler]:
				del self.subscriptions[handler][self.subscriptions[handler].index(channel)]


	def unsubscribe_all(self, handler):
		if handler in self.subscriptions:
			del self.subscriptions[handler]


	def push_changes(self, handler):
		out = {}
		to_unsubscribe = []
		peers = handler.peers
		for handler_ in self.subscriptions:
			try:
				self.send_connected_peers(handler_, self.subscriptions[handler_])
			except RuntimeError:
				to_unsubscribe.append(handler_)
		for handler in to_unsubscribe:
			self.unsubscribe_all(handler)


class Handler(Server):
	def setup(self, routes, broadcast, websockets, debug=False):
		super().setup(routes, websockets, debug=debug)
		self.broadcast = broadcast
		self.channel = None
		self.name = "Unknown"


	def ready(self):
		if self.debug:
			Logging.info("Handler for '%s:%d' ready." % (self.address, self.port))
		

	def closed(self, code, reason):
		if self.channel != None:
			self.broadcast.remove(self, self.channel)
		if self.debug:
			Logging.info("'%s:%d' disconnected." % (self.address, self.port))
		self.routes["peers"].push_changes(self)


def folder_validator(folder):
	if not os.path.isdir(folder):
		try:
			os.mkdir(folder)
		except OSError:
			return False
	return True


CONFIG_PATH = "server.cfg"

config = Config.Config()
config.add(Config.Option("server_address", ("127.0.0.1", 3077)))
config.add(Config.Option("debug", True, validator=lambda x: True if True or False else False))
config.add(Config.Option("binary_path", "Binaries", validator=folder_validator))
config.add(Config.Option("source_path", "Source", validator=folder_validator))

try:
	config = config.read_from_file(CONFIG_PATH)
except FileNotFoundError:
	config.write_to_file(CONFIG_PATH)
	config = config.read_from_file(CONFIG_PATH)


broadcast = Broadcast()
# Populating broadcast channels with all channels defined in Subscribe.Channels
for channel in Subscribe.CHANNELS:
	broadcast.add_channel(channel)

compile = Compile(config.source_path, config.binary_path)


server = make_server(config.server_address[0], config.server_address[1],
	server_class=WSGIServer, handler_class=WebSocketWSGIRequestHandler,
	app=None)
server.initialize_websockets_manager()

server.set_app(WebSocketWSGIApplication(handler_cls=Handler,
		handler_args={"debug" : config.debug, "broadcast" : broadcast,
		"websockets" : server.manager.websockets,
		"routes" : {"info" : Info(),
		"whoami" : WhoAmI(),
		"subscribe" : Subscribe(),
		"hostname" : DummyPipe(),
		"processes" : DummyPipe(),
		"peers" : Peers(),
		"sensor" : DummyPipe(),
		"identify" : DummyPipe(),
		"list_programs" : DummyPipe(),
		"run_program" : DummyPipe(),
		"std_stream" : DummyPipe(),
		"stop_programs" : DummyPipe(),
		"shutdown" : DummyPipe(),
		"reboot" : DummyPipe()}}))


try:
	Logging.header("Server loop starting.")
	server.serve_forever()
except KeyboardInterrupt:
	Logging.header("Gracefully shutting down server.")
	server.server_close()
	Logging.success("Server shutdown successful.")
