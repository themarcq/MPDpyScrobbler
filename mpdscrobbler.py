#!/usr/bin/env python3.4
from pathlib import Path
from datetime import date
from os.path import expanduser
import os
import subprocess 
import configparser
import time
import re
import multiprocessing
import queue
import threading
import webbrowser
import urllib.request
import json
import hashlib
import signal
import sys
#API Key: a303cdfc5d6f204bf2ed0806d0f634d5
#Secret: is 4c4b8f6208181f043fa5b2b2a1ea75d6

home = expanduser("~")
path = os.path.join(home,".pyscrobble")
config_file = os.path.join(path,"config")
journal_file = os.path.join(path,"journal")
DEBUG = True

def debug(*args, debug = False):
    if debug == True: 
        if DEBUG == False:
            return None
    print(*args)

def signal_term_handler(signal, frame):
    debug('Got SIGTERM')
    sys.exit(0)

def sign(data):
    api_sig = ""
    data["api_key"] = "a303cdfc5d6f204bf2ed0806d0f634d5"
    for key in sorted(data):
        api_sig = api_sig + key + data[key]
    api_sig = api_sig + "4c4b8f6208181f043fa5b2b2a1ea75d6"
    data['api_sig'] = hashlib.md5(api_sig.encode('utf-8')).hexdigest()
    data['format'] = 'json'
    return urllib.parse.urlencode(data).encode('utf-8')

def write_config(config, config_file):
    with open(config_file, 'w') as configfile:
        config.write(configfile)

class Song():
    def __init__(self):
        self.title = ""
        self.album = ""
        self.artist = ""
        self.length = ""
        self.date = ""

    def __str__(self):
        return str(self.__dict__)
    
    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def items(self):
        return {'title': self.title, 'album': self.album, 'artist': self.artist, 'length': self.length, 'date': self.date}

class Scrobble():
    pass

class WaiterThread(threading.Thread):
    def __init__(self, scrobble_queue, return_queue, mpd_host, wait_for):
        super(WaiterThread, self).__init__()
        self.scrobble_queue = scrobble_queue
        self.return_queue   = return_queue
        self.mpd_host       = mpd_host
        self.wait_for       = wait_for
        self.stop_request   = threading.Event()
        self.time           = 0

    def run(self):
        time_played = 0
        duration_minutes = int(re.search('([0-9]{1,})(:)([0-9]{1,})',self.wait_for.length).group(1))
        duration_seconds = int(re.search('([0-9]{1,})(:)([0-9]{1,})',self.wait_for.length).group(3))
        duration = duration_minutes * 60 + duration_seconds
        scrobble = Scrobble()
        scrobble.kind = "now_playing"
        scrobble.song=self.wait_for
        self.scrobble_queue.put(scrobble)
        while not self.stop_request.isSet():
            now_playing_output = subprocess.check_output(["mpc", "-f", "artist=%artist%\nalbum=%album%\ntitle=%title%\nlength=%time%", "-h", self.mpd_host, "status", "--wait"]).decode("utf-8")
            tmp_playing = Song()
            tmp_playing.title = re.search('\ntitle=(.*)',now_playing_output).group(1)
            tmp_playing.artist = re.search('^artist=(.*)',now_playing_output).group(1)
            tmp_playing.album = re.search('\nalbum=(.*)',now_playing_output).group(1)
            tmp_playing.length = re.search('\nlength=(.*)',now_playing_output).group(1)
            if tmp_playing == self.wait_for:
                if re.search('[playing]',now_playing_output) != None:
                    time_played += 1
            else:
                debug("Song changed")
                self.join()
            if time_played > (0.5 * duration) or time_played > 240:
                debug("Scrobbling")
                scrobble.kind = "played"
                self.scrobble_queue.put(scrobble)
                self.join()
            time.sleep(1)
            pass

    def join(self, timeout=None):
        self.stop_request.set()


class ScrobblerThread(threading.Thread):

    def __init__(self, scrobble_queue, return_queue, config):
        super(ScrobblerThread, self).__init__()
        self.scrobble_queue     = scrobble_queue
        self.return_queue       = return_queue
        self.config             = config
        self.stop_request       = threading.Event()
        self.has_token          = False
        self.has_session        = False
        self.browser            = False
        self.reload_token       = False
        self.journal            = None
        self.session            = None

    def _get_token(self):
        debug("Getting new token.")
        try:
            response = urllib.request.urlopen(self.config['lastfm']['host'] + "?method=auth.gettoken&api_key=a303cdfc5d6f204bf2ed0806d0f634d5&format=json")
            token = json.loads(response.read().decode("utf-8"))['token']
            debug("New token recieved, authenticating...")
            webbrowser.open('http://www.last.fm/api/auth/?api_key=a303cdfc5d6f204bf2ed0806d0f634d5&token='+token)
        except urllib.error.URLError as e:
            debug("Can not get new token")
            debug("error({0}): {1}".format(e.errno, e.strerror))
            return "ERR"
        self.config['lastfm']['token']=token
        write_config(self.config, config_file)
        self.has_token = True
        return token

    def _get_session(self):
        data = {
            'method': 'auth.getSession',
            'token': self.config['lastfm']['token']
        }
        data = sign(data)
        try:
            response = urllib.request.urlopen(
                self.config['lastfm']['host'],
                data)
        except urllib.error.URLError as e:
            debug("error({0}): {1}".format(e.errno, e.strerror))
            return "ERR"
        response = json.loads(response.read().decode('utf-8'))
        debug(response)
        if 'error' in response:
            if response['error'] == 14:
                debug(response['message'])
                if self.browser == False:
                   webbrowser.open('http://www.last.fm/api/auth/?api_key=a303cdfc5d6f204bf2ed0806d0f634d5&token='+self.config['lastfm']['token'])
                   self.browser = True
                   return "ERR"
            if response['error'] == 4 or response['error'] == 15:
                debug(response['message'])
                debug('Getting new token')
                self.has_token = False
                self.reload_token = True
                return "ERR"
        self.has_session = True
        return response['session']['key']

#TODO

    def _add_scrobble_to_journal(self,scrobble):
        if scrobble.kind != "now_playing":
            print("Adding to journal")
            scrobble.song.date=str(int(time.time()))
            print(scrobble.song)
            self.journal[str(time.time())]=scrobble.song.items()
            write_config(self.journal, journal_file)

    def _scrobble_from_journal(self):
        edited = False
        for song in self.journal:
            if song == "DEFAULT":
                continue
            print("Scrobbling from journal")
            print(self.journal[song])
            data = {
                    "timestamp": self.journal[song]['date'],
                    "method": "track.scrobble",
                    "token": self.config['lastfm']['token'],
                    "artist": self.journal.get(song,'artist'),
                    "track": self.journal[song]['title'],
                    "album": self.journal[song]['album'],
                    "sk": self.session
                        }
            data = sign(data)
            try:
                response = urllib.request.urlopen(self.config['lastfm']['host'], data)
            except urllib.error.URLError as e:
                debug("error({0}): {1}".format(e.errno, e.strerror))
                break
            else:
                self.journal.remove_section(song)
                edited = True
        if edited:
            write_config(self.journal, journal_file)
#!TODO

    def run(self): 
        self.journal = configparser.ConfigParser()
        self.journal.read(journal_file)
        while not self.stop_request.isSet():
            try:
                scrobble = self.scrobble_queue.get(True, 0.1)
            except queue.Empty:
                continue
            else :
                
                # load token and session if we have none or it needs renewal

                if self.has_token == False:
                    debug("loading token")
                    if not self.config.has_option('lastfm','token') or self.reload_token == True:
                        token = self._get_token()
                    else:
                        token = self.config['lastfm']['token']
                        self.has_token = True
                    if token == "ERR":
                        self._add_scrobble_to_journal(scrobble)
                        continue
                if self.has_session == False:
                    debug("loading session")
                    self.session = self._get_session() 
                    if self.session == "ERR":
                        self._add_scrobble_to_journal(scrobble)
                        continue
                if scrobble.kind == "now_playing":
                    data = { 
                        "method": "track.updateNowPlaying", 
                        "token": self.config['lastfm']['token'],
                        "artist": scrobble.song.artist,
                        "track": scrobble.song.title,
                        "album": scrobble.song.album,
                        "sk": self.session
                            }
                    data = sign(data)
                    try:
                        response = urllib.request.urlopen(self.config['lastfm']['host'], data)
                    except urllib.error.URLError:
                        debug("Could not update \"now playing\"")
                        debug("Skipping")
                    else:
                        debug(response.read().decode('utf-8'), debug=True)
                else:
                    #normal scrobble
                    data = { 
                        "timestamp": str(int(time.time())),
                        "method": "track.scrobble", 
                        "token": self.config['lastfm']['token'],
                        "artist": scrobble.song.artist,
                        "track": scrobble.song.title,
                        "album": scrobble.song.album,
                        "sk": self.session
                            }
                    data = sign(data)
                    try:
                        response = urllib.request.urlopen(self.config['lastfm']['host'], data)
                    except urllib.error.URLError as e:
                        debug("error({0}): {1}".format(e.errno, e.strerror))
                        self._add_scrobble_to_journal(scrobble)
                    else:
                        response = response.read().decode('utf-8')
                        debug(response, debug=True)
                        if 'error' in response:
                            debug("Something went wrong")
                            debug(response)
                            self._add_scrobble_to_journal(scrobble)
                        else:
                            self._scrobble_from_journal()
                    self.scrobble_queue.task_done()

    def join(self, timeout=None):
        self.stop_request.set()
        super(WaiterThread, self).join(timeout)

def main():
    """
        main function
        loads configuration and starts threads
    """

    # loading config from ~/.pyscrobble/config 
    config = configparser.ConfigParser()
    config.read(config_file)
    
    # queues with scrobbles and outputs for threads
    scrobble_queue = queue.Queue()
    return_queue = queue.Queue()
    
    # creating thread which scrobbles tracks     
    scrobbler = ScrobblerThread(scrobble_queue  = scrobble_queue, \
                                return_queue    = return_queue, \
                                config          = config)
    scrobbler.start()

    # main loop checking if song changes, then spawn 
    now_playing = Song()
    host=config['mpd']['hostname']
    if not config['mpd']['password'] == "":
        host= config['mpd']['password'] + "@" + host
    while True:
        now_playing_output = subprocess.check_output(["mpc", "-f", "artist=%artist%\nalbum=%album%\ntitle=%title%\nlength=%time%", "-h", host, "current", "--wait"]).decode("utf-8")
        if now_playing_output == "error: Connection closed by the server":
            debug("Error: can not connect to server. Waiting")
            time.sleep(1)
        else :
            debug(now_playing_output)
            if not re.search('\ntitle=',now_playing_output):
                debug('MPD Stopped')
            else:
                now_playing = Song()
                now_playing.title = re.search('\ntitle=(.*)',now_playing_output).group(1)
                now_playing.artist = re.search('^artist=(.*)',now_playing_output).group(1) 
                now_playing.album = re.search('\nalbum=(.*)',now_playing_output).group(1)
                now_playing.length = re.search('\nlength=(.*)',now_playing_output).group(1)
                waiter = WaiterThread(  scrobble_queue  = scrobble_queue, \
                                        return_queue    = return_queue, \
                                        mpd_host        = host, \
                                        wait_for        = now_playing)
                waiter.start()

signal.signal(signal.SIGTERM, signal_term_handler)
try:
    main()
except KeyboardIterrupt:
    debug('Bye')
