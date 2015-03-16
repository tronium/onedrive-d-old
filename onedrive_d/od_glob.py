#!/usr/bin/python3

"""
Global variables for onedrive_d.
"""

import os
import sys
import logging
import atexit
import json
from dateutil.parser import parse as str_to_time
from calendar import timegm
from datetime import datetime, timedelta, timezone
from pwd import getpwnam
from . import od_ignore_list

config_instance = None
logger_instance = None

APP_CLIENT_ID = '000000004010C916'
APP_CLIENT_SECRET = 'PimIrUibJfsKsMcd0SqwPBwMTV7NDgYi'
APP_VERSION = '1.1.0dev'


def get_config_instance(force=False, setup_mode=False):
	global config_instance
	# callingframe = sys._getframe(1)
	# print('My caller is the %r function in a %r class' % (
	# 	callingframe.f_code.co_name,
	# 	callingframe.f_locals['self'].__class__.__name__))
	if force or config_instance is None:
		config_instance = ConfigSet(setup_mode)
		atexit.register(dump_config)
	return config_instance


def get_logger(level=logging.DEBUG, file_path=None):
	global logger_instance
	if logger_instance is None:
		logging.basicConfig(format='[%(asctime)-15s] %(levelname)s: %(threadName)s: %(message)s')
		logger_instance = logging.getLogger(__name__)
		logger_instance.setLevel(level)
		if file_path is not None:
			logger_instance.propagate = False
			logger_fh = logging.FileHandler(file_path, 'a')
			logger_fh.setLevel(level)
			logger_instance.addHandler(logger_fh)
		atexit.register(flush_log_at_shutdown)
	return logger_instance


def now():
	return datetime.now(timezone.utc)


def time_to_str(t):
	s = t.strftime('%Y-%m-%dT%H:%M:%S.%f%Z')
	return s


def str_to_timestamp(s):
	return timegm(str_to_time(s).timetuple())


def timestamp_to_time(t):
	return datetime.fromtimestamp(t, tz=timezone.utc)


def mkdir(path, uid):
	"""
	Create a path and set up owner uid.
	"""
	os.mkdir(path)
	os.chown(path, uid, -1)


def flush_log_at_shutdown():
	global logger_instance
	if logger_instance is not None:
		logging.shutdown()


def dump_config():
	if config_instance is not None and ConfigSet.is_dirty:
		config_instance.dump()


class ConfigSet:

	params = {
		'NETWORK_ERROR_RETRY_INTERVAL': 10,  # in seconds
		'DEEP_SCAN_INTERVAL': 60,  # in seconds
		'NUM_OF_WORKERS': 4,
		# files > 4 MiB will be uploaded with BITS API
		'BITS_FILE_MIN_SIZE': 4194304,
		# 512 KiB per block for BITS API
		'BITS_BLOCK_SIZE': 524288,
		'ONEDRIVE_ROOT_PATH': None,
		'ONEDRIVE_TOKENS': None,
		'ONEDRIVE_TOKENS_EXP': None,
		'USE_GUI': False,
		'MIN_LOG_LEVEL': logging.DEBUG,
		'LOG_FILE_PATH': '/var/log/onedrive_d.log',
		'LAST_RUN_TIMESTAMP': '1970-01-01T00:00:00+0000'
	}

	tokens = None

	OS_HOSTNAME = os.uname()[1]
	OS_USERNAME = os.getenv('SUDO_USER')

	initialized = False
	is_dirty = False

	def __init__(self, setup_mode=False):
		# no locking is necessary because the code is run way before multithreading
		if not ConfigSet.initialized:
			if ConfigSet.OS_USERNAME is None or ConfigSet.OS_USERNAME == '':
				ConfigSet.OS_USERNAME = os.getenv('USER')
			if ConfigSet.OS_USERNAME is None or ConfigSet.OS_USERNAME == '':
				get_logger().critical('cannot find current logged-in user.')
				sys.exit(1)
			ConfigSet.OS_USER_ID = getpwnam(ConfigSet.OS_USERNAME).pw_uid
			ConfigSet.OS_HOME_PATH = os.path.expanduser('~' + ConfigSet.OS_USERNAME)
			ConfigSet.APP_CONF_PATH = ConfigSet.OS_HOME_PATH + '/.onedrive'
			if not os.path.exists(ConfigSet.APP_CONF_PATH):
				get_logger().critical('onedrive-d may not be installed properly. Exit.')
				sys.exit(1)
			ConfigSet.APP_CONF_FILE = ConfigSet.APP_CONF_PATH + '/config_v2.json'
			ConfigSet.APP_TOKEN_FILE = ConfigSet.APP_CONF_PATH + '/session.json'
			if os.path.exists(ConfigSet.APP_CONF_FILE):
				try:
					with open(ConfigSet.APP_CONF_FILE, 'r') as f:
						saved_params = json.load(f)
						for key in saved_params:
							ConfigSet.params[key] = saved_params[key]
				except:
					get_logger().info(
						'fail to read config file "' + ConfigSet.APP_CONF_FILE + '". Use default.')
			elif not setup_mode:
				get_logger().critical('onedrive-d config file does not exist. Exit.')
				sys.exit(1)
			if ConfigSet.params['ONEDRIVE_ROOT_PATH'] is None and not setup_mode:
				get_logger().critical('path to local OneDrive repo is not set.')
				sys.exit(1)
			ConfigSet.LAST_RUN_TIMESTAMP = str_to_time(ConfigSet.params['LAST_RUN_TIMESTAMP'])
			ConfigSet.APP_IGNORE_FILE = ConfigSet.APP_CONF_PATH + '/ignore_v2.ini'
			ConfigSet.initialized = True
			print('Loading configuration ... OK')

		if not setup_mode:
			if os.path.exists(ConfigSet.APP_IGNORE_FILE):
				self.ignore_list = od_ignore_list.IgnoreList(
					ConfigSet.APP_IGNORE_FILE, ConfigSet.params['ONEDRIVE_ROOT_PATH'])
			else:
				ConfigSet.logger.info('ignore list file was not found.')
				ConfigSet.ignore_list = None

	def set_root_path(self, path):
		ConfigSet.params['ONEDRIVE_ROOT_PATH'] = path
		ConfigSet.is_dirty = True

	def set_last_run_timestamp(self):
		ConfigSet.params['LAST_RUN_TIMESTAMP'] = time_to_str(now())
		ConfigSet.is_dirty = True

	def get_access_token(self):
		if ConfigSet.tokens is None:
			try:
				with open(ConfigSet.APP_TOKEN_FILE, 'r') as f:
					ConfigSet.tokens = json.load(f)
			except:
				pass
		return ConfigSet.tokens

	def is_token_expired(self):
		return str_to_time(ConfigSet.tokens['expiration']) < now()

	def set_access_token(self, tokens):
		exp = now() + timedelta(seconds=tokens['expires_in'])
		tokens['expiration'] = time_to_str(exp)
		ConfigSet.tokens = tokens
		ConfigSet.is_dirty = True

	def dump(self):
		try:
			with open(ConfigSet.APP_CONF_FILE, 'w') as f:
				json.dump(ConfigSet.params, f)
			with open(ConfigSet.APP_TOKEN_FILE, 'w') as f:
				json.dump(ConfigSet.tokens, f)
			os.chown(ConfigSet.APP_CONF_FILE, ConfigSet.OS_USER_ID, -1)
			os.chown(ConfigSet.APP_TOKEN_FILE, ConfigSet.OS_USER_ID, -1)
			get_logger().debug('config saved.')
		except:
			get_logger().warning('failed to save config.')