import os
import hashlib
import time
import base64

import Routing

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


def relative_recursive_ls(path, relative_to, exclude=[]):
	if path[-1] != "/":
		path += "/"
	files = []
	for item in os.listdir(path):
		if item not in exclude:
			if os.path.isdir(path + item):
				files += relative_recursive_ls(path + item, relative_to, exclude)
			else:
				files.append(os.path.relpath(path + item, relative_to))
	return files


def md5(path):
	return hashlib.md5(open(path, "rb").read()).hexdigest()


def get_name_from_path(path):
	name = os.path.basename(path)
	if name:
		return name
	else:
		return None


def get_file_content(path):
	return open(path, "rb+").read()


class ReloadHandler(FileSystemEventHandler):
	def __init__(self, sync):
		self.sync = sync

	def on_modified(self, event):
		if get_name_from_path(event.src_path) not in self.sync.exclude and not event.is_directory:
			self.sync.modified(event)


	def on_created(self, event):
		if get_name_from_path(event.src_path) not in self.sync.exclude and not event.is_directory:
			self.sync.created(event)


	def on_deleted(self, event):
		if get_name_from_path(event.src_path) not in self.sync.exclude and not event.is_directory:
			self.sync.deleted(event)


class SyncClient(Routing.ClientRoute):
	def __init__(self, sock, folder, route, exclude=[".DS_Store", ".git"]):
		self.sock = sock
		self.folder = folder if folder[-1] == "/" else folder + "/"
		self.started = False
		self.route = route
		self.exclude = exclude
		self.files = relative_recursive_ls(folder, folder, exclude=self.exclude)
		self.surpressed_fs_events = []
		observer = Observer()
		observer.schedule(ReloadHandler(self), path=self.folder, recursive=True)
		observer.start()


	def start(self):
		out = {"list" : {}}
		for file in self.files:
			out["list"].update({file : {"mtime" : os.path.getmtime(self.folder + file), "md5" : md5(self.folder + file)}})
		print(out)
		self.sock.send(out, self.route)
		self.started = True

	def stop(self):
		self.started = False


	def surpress_fs_event(self, file):
		if not file in self.surpressed_fs_events:
			self.surpressed_fs_events.append(file)


	def unsurpess_fs_event(self, file):
		if file in self.surpressed_fs_events:
			del self.surpressed_fs_events[self.surpressed_fs_events.index(file)]


	def run(self, data, handler):
		if type(data) is dict:
			if "add" in data:
				for file in data["add"]:
					self.surpress_fs_event(file)
					open(file, "wb+").write(base64.b64decode(data["add"][file]["content"]))
					os.utime(file, data["add"][file]["mtime"])
					self.unsupress_fs_event(file)
			elif "del" in data:
				self.surpress_fs_event(file)
				os.remove(file)
				self.unsupress_fs_event(file)
			elif "req" in data:
				for file in data["req"]:
					if file in self.files:
						self.sock.send({"add" : {"content" : base64.b64encode(get_file_content(file)).encode(), "mtime" : os.path.getmtime(file)}}, self.route)


	def modified(self, event):
		if self.started:
			relpath = os.path.relpath(event.src_path, self.folder)
			if relpath not in self.files:
				self.files.append(relpath)
			if not relpath in self.surpressed_fs_events:
				self.sock.send({"add" : {"content" : base64.b64encode(get_file_content(event.src_path)).encode(), "mtime" : os.path.getmtime(event.src_path)}}, self.route)


	def created(self, event):
		self.modified(event)


	def deleted(self, event):
		if self.started:
			relpath = os.path.relpath(event.src_path, self.folder)
			if relpath in self.files:
				del self.files[self.files.index(relpath)]
			if not relpath in self.surpressed_fs_events:
				self.sock.send({"del" : [relpath]}, self.route)


class SyncServer:
	def __init__(self, folder, exclude=[".DS_Store", ".git"]):
		self.folder = folder if folder[-1] == "/" else folder + "/"
		self.exclude = exclude
		self.files = relative_recursive_ls(folder, folder, exclude=self.exclude)
		self.surpressed_fs_events = []



	def start(self):
		self.files = relative_recursive_ls(folder, folder, exclude=self.exclude)
		observer = Observer()
		observer.schedule(ReloadHandler(self), path=self.folder, recursive=True)
		observer.start()


	def surpress_fs_event(self, file):
		if not file in self.surpressed_fs_events:
			self.surpressed_fs_events.append(file)


	def unsurpess_fs_event(self, file):
		if file in self.surpressed_fs_events:
			del self.surpressed_fs_events[self.surpressed_fs_events.index(file)]


	def run(self, data, handler):
		if type(data) is dict:
			if "list" in data:
				for file in data["list"]:
					print(file)
					if data["list"][file]["mtime"] < handler.last_stop_time:
						if file in self.files:
							if data["list"][file]["md5"] != md5(file):
								handler.sock.send({"del" : [file]}, handler.get_route(self))
						else:
							handler.sock.send({"del" : [file]}, handler.get_route(self))
					else:
						handler.sock.send({"req" : [file]}, handler.get_route(self))
			elif "add" in data:
				for file in data["add"]:
					self.surpress_fs_event(file)
					open(file, "wb+").write(base64.b64decode(data["add"][file]["content"].decode()))
					os.utime(file, data["add"][file]["mtime"])
					self.unsupress_fs_event(file)
			elif "del" in data:
				self.surpress_fs_event(file)
				os.remove(file)
				self.unsupress_fs_event(file)


	def modified(self, event):
		relpath = os.path.relpath(event.src_path, self.folder)
		if relpath not in self.files:
			self.files.append(relpath)
		if not relpath in self.surpressed_fs_events:
			handler.broadcast({"add" : {
				"content" : base64.b64encode(get_file_content(event.src_path)).encode(), 
				"mtime" : os.path.getmtime(event.src_path)}}, handler.get_route(self), channel)


	def created(self, event):
		self.modified(event)


	def deleted(self, event):
		relpath = os.path.relpath(event.src_path, self.folder)
		if relpath in self.files:
			del self.files[self.files.index(relpath)]
		if not relpath in self.surpressed_fs_events:
			handler.broadcast({"del" : [relpath]}, handler.get_route(self), channel)