from sys import path
import os
import _thread

fl0w_path = os.path.dirname(os.path.realpath(__file__))
shared_path = os.path.dirname(os.path.realpath(__file__)) + "/Shared/"
if fl0w_path not in path:
	path.append(fl0w_path)
if shared_path not in path:
	path.append(shared_path)


from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import sublime 
import sublime_plugin

import socket
from ESock import ESock
from Utils import capture_trace
from Sync import SyncClient
import Routing
from SublimeMenu import *
import Logging

import webbrowser

CHANNEL = "s"

def plugin_unloaded():
	for window in windows:
		if hasattr(window, "fl0w"):
			window.fl0w.invoke_disconnect()



def sock_handler(sock, routes, handler):
	while 1:
		try:
			data = sock.recv()
			if data[1] in routes:
				routes[data[1]].run(data[0], handler)
		except (OSError, socket.error, Exception) as e:
			if str(e) != "[Errno 9] Bad file descriptor":
				capture_trace()
			handler.invoke_disconnect()
			break


class ReloadHandler(FileSystemEventHandler):
		def on_modified(self, event):
			print("Modified: %s" % event)

		def on_created(self, event):
			print("Created: %s" % event)

		def on_deleted(self, event):
			print("Deleted: %s" % event)   


class Fl0w:
	def __init__(self, window):
		self.connected = False
		self.window = window
		self.folder = window.folders()[0]

		self.start_menu = Menu()
		self.start_menu.add(Entry("Connect", "Connect to a fl0w server", action=self.invoke_connect))
		self.start_menu.add(Entry("About", "Information about fl0w", action=self.invoke_about))

		self.main_menu = Menu()
		self.main_menu.add(Entry("Wallaby Control", "Control a connected Wallaby", action=self.invoke_wallaby_control))
		self.main_menu.add(Entry("Info", "Server info", action=self.invoke_info))
		self.main_menu.add(Entry("Debug", "Debug options", action=self.invoke_debug_options))
		self.main_menu.add(Entry("Disconnect", "Disconnect from server", action=self.invoke_disconnect))


	def invoke_about(self):
		if sublime.ok_cancel_dialog("fl0w by @robot0nfire", "robot0nfire.com"):	
			webbrowser.open("http://robot0nfire.com")


	def invoke_info(self):
		self.sock.send("", "info")


	class Info(Routing.ClientRoute):
		def run(self, data, handler):
			sublime.message_dialog(data)
			handler.main_menu.invoke(handler.window)


	class GetInfo(Routing.ClientRoute):
		def run(self, data, handler):
			if data == "":
				handler.sock.send({"type" : CHANNEL}, "get_info")

	
	class ErrorReport(Routing.ClientRoute):
		def run(self, data, handler):
			sublime.error_message(data)


	class Compile(Routing.ClientRoute):
		def run(self, data, handler):
			status = ""
			if data["failed"]:
				status = "Compile failed (%s)." % data["relpath"]
			else:
				status = "Compile completed successfully. (%s)." % data["relpath"]
			view = handler.window.create_output_panel('compile_panel')
			view.set_syntax_file("Packages/fl0w/CompileHighlight.sublime-syntax")
			view.settings().set("draw_white_space", "none")
			view.settings().set("draw_indent_guides", False)
			view.settings().set("gutter", False)
			view.settings().set("line_numbers", False)
			view.set_read_only(False)
			view.run_command("append", {"characters": data["returned"] + status})
			view.set_read_only(True)
			handler.window.run_command("show_panel", {"panel": "output.compile_panel"})


	def connect(self, connect_details):
		connect_details_raw = connect_details
		connect_details = connect_details.split(":")
		if len(connect_details) == 2:
			try:
				# Establish connection to the server
				self.sock = ESock(socket.create_connection((connect_details[0], int(connect_details[1]))), disconnect_callback=self.invoke_disconnect, debug=False)
				sublime.status_message("Connected to %s:%s." % (connect_details[0], connect_details[1]))
				self.connected = True
				# Initialize all routes
				error_report = Fl0w.ErrorReport()
				info = Fl0w.Info()
				wallaby_control = Fl0w.WallabyControl()
				get_info = Fl0w.GetInfo()
				self.s_sync = SyncClient(self.sock, self.folder, "s_sync")
				compile = Fl0w.Compile()
				_thread.start_new_thread(sock_handler, (self.sock, {"error_report": error_report, 
					"info" : info, "wallaby_control" : wallaby_control, "get_info" : get_info, 
					"s_sync" : self.s_sync, "compile" : compile}, self))
				self.s_sync.start()
				# Saving last server address
				sublime.load_settings("fl0w.sublime-setting").set("server_address", connect_details_raw)
				sublime.save_settings("fl0w.sublime-setting")
			except OSError as e:
				sublime.error_message("Error during connection creation:\n %s" % str(e))
		else:
			sublime.error_message("Invalid input.")


	# Input invokers
	def invoke_connect(self):
		address = sublime.load_settings("fl0w.sublime-setting").get("server_address")
		address = "" if type(address) is not str else address
		Input("Address:Port", initial_text=address, on_done=self.connect).invoke(self.window)


	def invoke_disconnect(self):
		if self.connected:
			sublime.message_dialog("Connection closed ('%s')" % ", ".join(self.folder))
		if hasattr(self, "s_sync"):
			self.s_sync.observer.stop()
		self.connected = False
		self.sock.close()


	def invoke_wallaby_control(self):
		self.sock.send("list", "wallaby_control")


	class WallabyControl(Routing.ClientRoute):
		def run(self, data, handler):
			wallaby_menu = Menu()
			entry_count = 0
			for wallaby in data:
				wallaby_menu.add(Entry(wallaby, str(data[wallaby]), action=handler.wallaby_control_submenu, kwargs={"wallaby" : wallaby}))
				entry_count += 1
			if entry_count != 0:
				wallaby_menu.invoke(handler.window, back=handler.main_menu)
			else:
				sublime.error_message("No Wallaby Controllers connected.")


	def wallaby_control_submenu(self, wallaby):
		menu = Menu(subtitles=False)
		for action in ("Run", "Stop"):
			menu.add(Entry(action, action=self.sock.send, kwargs={"data" : {wallaby : action.lower()}, "route" : "wallaby_control"}))
		for action in ("Restart", "Shutdown", "Reboot", "Disconnect"):
			menu.add(Entry(action, action=self.sock.send, kwargs={"data" : {wallaby : action.lower()}, "route" : "wallaby_control"}))
		

		menu.invoke(self.window)


	def invoke_debug_options(self):
		debug_menu = Menu(subtitles=False)
		debug_menu.add(Entry("On", action=self.set_debug, kwargs={"debug" : True}))
		debug_menu.add(Entry("Off", action=self.set_debug, kwargs={"debug" : False}))
		debug_menu.invoke(self.window, back=self.main_menu)


	def set_debug(self, debug):
		sublime.status_message("fl0w: Debug now '%s'" % debug)
		self.sock.debug = debug


class Fl0wCommand(sublime_plugin.WindowCommand):
	def run(self): 
		valid_window_setup = True
		folder_count = len(self.window.folders())
		if folder_count > 1:
			sublime.error_message("Only one open folder per window is allowed.")
			valid_window_setup = False
		elif folder_count == 0:
			sublime.error_message("No folder open in window.")
			valid_window_setup = False			
		if valid_window_setup:
			if not hasattr(self.window, "fl0w"):
				folder = self.window.folders()[0]
				files = os.listdir(folder)
				if not ".no-fl0w" in files:
					if not ".fl0w" in files:
						if sublime.ok_cancel_dialog("""We've detected that this is your first time using fl0w in your current directory.
												We don't want to be responsible for any lost work so please backup your files before proceding. 
												(An empty project directory is recommended but not necessary.)""", "Yes"):
							open(folder + "/.fl0w", 'a').close()
							self.window.fl0w = Fl0w(self.window)
							windows.append(self.window)
							self.window.fl0w.start_menu.invoke(self.window)
						else:
							sublime.error_message("fl0w can only be run once you've allowed it to operate in your current directory.")
					else:
						self.window.fl0w = Fl0w(self.window)
						windows.append(self.window)
						self.window.fl0w.start_menu.invoke(self.window)
				else:
					sublime.error_message("fl0w can't be opened in your current directory (.no-fl0w file exists)")		
			else:
				if not self.window.fl0w.connected:
					self.window.fl0w.start_menu.invoke(self.window)
				else:
					self.window.fl0w.main_menu.invoke(self.window)
		else:
			if hasattr(self.window, "fl0w"):
				sublime.error_message("Window setup was invalidated (Don't close or open any additional folders in a fl0w window)")
				if self.window.fl0w.connected:
					self.window.fl0w.invoke_disconnect()

	def description(self):
		print("description accessed")
		if hasattr(self.windows, "fl0w"):
			return "Connected."
		return None


windows = []