#!/usr/bin/python

'''
    This software has been designed to work with Weather Station Renkforce WH2600,
    Domoticz and Weather Underground.
    It's aimed for Domoticz running on Raspberry Pi

    Copyright (C) 2017  Allan Gam.

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.

    https://github.com/allan-gam
'''

from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
#from os import curdir, sep
import json, urlparse, requests
import os, getopt, sys, socket
from datetime import datetime
import math, cmath, scipy

PROGRAM_NAME = 'WH2600 Interceptor'
VERSION = '1.0.3'
MSG_ERROR = 'Error'
MSG_INFO = 'Info'
MSG_EXEC = 'Exec info'
WU_UPDATE_URL = 'https://rtupdate.wunderground.com/weatherstation/updateweatherstation.php'
WU_SOFTWARE_TYPE = PROGRAM_NAME + ' V. ' + VERSION
UPDATE_INTERV = 10 # Expected Weather Station report Interval in seconds
UPDATE_DOMO_INTERV = 6 # Weather Station report Interval that Domoticz will by updated by

# Global (module) namespace variables
cfgFile = sys.path[0] + '/config.json'
tty = True if os.isatty(sys.stdin.fileno()) else False
isDebug = False
isVerbose = False
runs = 0

# Global variables for Wind data
average_wind_speed_2min = 0
average_wind_speed_10min = 0
max_gust_speed_2min = 0
max_gust_speed_10min = 0
wdir_2min = 0
wdir_10min = 0
arrWindDir = {}
arrWindSpeed = {}
arrGustSpeed = {}


#This class will handles any incoming request from PWS
class myHandler(BaseHTTPRequestHandler):
	
	#Handler for the GET requests
	def do_GET(self):
		#print self.path
		global runs
		runs+=1

		jsonQs = dict(urlparse.parse_qsl(urlparse.urlparse(self.path).query))
		if isDebug: print 'Received data for station ID : ', jsonQs['ID']
		# Make numbers within quotes numerical
		for key in jsonQs:
			value = jsonQs[key]
			if value.replace('-','').isdigit():
				jsonQs[key] = int(value)
			elif is_number(value):
				jsonQs[key] = float(value)

		# Cleansing and Data Processing
		jsonQs['softwaretype'] = WU_SOFTWARE_TYPE
		jsonQs['dateutc'] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
		jsonQs['rtfreq'] = 12

		if isVerbose and isDebug:
			for key, value in jsonQs.items():
				print key, value
		#print jsonQs
		# Send a reply to the WH2600
		mimetype='text/html'
		self.send_response(200)
		self.send_header('Content-type',mimetype)
		self.end_headers()
		self.wfile.write('success\n')

		saveWindData(jsonQs)
		if ((runs % UPDATE_DOMO_INTERV == 0) or (runs == 1)):
			if isVerbose: print 'Checking if Domoticz devices needs to be updated...'
			updateDomoticz(jsonQs)
			if isDebug: print jsonQs

		# Get rid of some elements that we don't need to send to WU
		del jsonQs['lowbatt']
		del jsonQs['windchillf']
		del jsonQs['monthlyrainin']
		del jsonQs['yearlyrainin']
		del jsonQs['weeklyrainin']
		del jsonQs['winddir_avg10m']
		updateWU(jsonQs)
		return

	def log_message(self, format, *args):
		return # Quiet please

def updateWU(payload):
	try:
		r = requests.get(WU_UPDATE_URL, params=payload)
	except:
		logToDomoticz(MSG_ERROR, 'The WU server couldn\'t fulfill the request.')
		sys.exit(0)
	else:
		# everything is fine
		if isVerbose: print r.text
		if isDebug: print(r.url)

def updateDomoticz(jsonQs):
	for c in cfg['domoticz']['devices']['device']:
		updateDomoDevice(c, jsonQs)

def updateDomoDevice(domoDevice, jsonQs):
	if not domoDevice['enabled']:
		return
	# Only update if the new value differs from the device value
	# or if the device has not been updated for a while
	payload = dict([('type', 'devices'), ('rid', domoDevice['domoticzIdx'])])
	r = domoticzAPI(payload)
	#print r['result'][0]['Data'] #data
	#print r['result'][0]['LastUpdate']

	if not 'result' in r.keys():
		errMess = 'Failure getting data for domoticz device idx: ' + str(domoDevice['domoticzIdx'])
		print errMess
		logToDomoticz(MSG_ERROR, errMess)
		return

	# Now, looking for a reason to update the sensor
	# Does the Domotic's sensor need an update in order not to time out?
	sensorTimedOut = False
	if 'HaveTimeout' in r['result'][0]:
		if (r['result'][0]['HaveTimeout'] and ((datetime.now() - datetime.strptime(r['result'][0]['LastUpdate'], '%Y-%m-%d %H:%M:%S')).seconds >= 3000)):
			sensorTimedOut = True

	# Checking if the 'Indoor Temp + Humidity' device needs an update

	if ('indoortempf' in jsonQs) and domoDevice['categoryName'] == 'Indoor Temp + Humidity' and domoDevice['domoticzSensorType'] == 82:
		if cfg['domoticz']['unitsOfTemperature']  == 'Celcius':
			reportedTemp = round(temp_c(jsonQs['indoortempf']), 1)
		else:
			reportedTemp = round(jsonQs['indoortempf'], 1)
		reportedHum = jsonQs['indoorhumidity']
		domoTemp = round(r['result'][0]['Temp'], 1)
		domoHum = round(r['result'][0]['Humidity'], 1)
		if reportedTemp != domoTemp or reportedHum != domoHum or sensorTimedOut:
			if isVerbose: print 'Updating the Domoticz Indoor Temp + Humidity device to', reportedTemp, ';', reportedHum
			if isVerbose and sensorTimedOut: print '<sensorTimedOut>'
			payload = dict([('type', 'command'), ('param', 'udevice'), ('idx', domoDevice['domoticzIdx']), \
                  ('nvalue', 0), ('svalue', str(reportedTemp)+';'+str(reportedHum)+';'+str(getHumStat(reportedHum)))])
			r = domoticzAPI(payload)

	# Checking if the 'Outdoor Temp + Humidity' device needs an update
	elif domoDevice['categoryName'] == 'Outdoor Temp + Humidity' and domoDevice['domoticzSensorType'] == 82:
		if cfg['domoticz']['unitsOfTemperature']  == 'Celcius':
			reportedTemp = round(temp_c(jsonQs['tempf']), 1)
		else:
			reportedTemp = round(jsonQs['tempf'], 1)
		reportedHum = int(jsonQs['humidity'])
		domoTemp = round(r['result'][0]['Temp'], 1)
		domoHum = round(r['result'][0]['Humidity'], 1)
		if reportedTemp != domoTemp or reportedHum != domoHum or sensorTimedOut:
			if isVerbose: print 'Updating the Domoticz Outdoor Temp + Humidity device to', reportedTemp, ';', reportedHum
			if isVerbose and sensorTimedOut: print '<sensorTimedOut>'
			payload = dict([('type', 'command'), ('param', 'udevice'), ('idx', domoDevice['domoticzIdx']), \
                  ('nvalue', 0), ('svalue', str(reportedTemp)+';'+str(reportedHum)+';'+str(getHumStat(reportedHum)))])
			r = domoticzAPI(payload)

	# Checking if the 'Barometer' device needs an update
	elif domoDevice['categoryName'] == 'Barometer' and domoDevice['domoticzSensorType'] == 1:
		reportedValue = round(mbar(jsonQs['baromin']), 0)
		domoValue = round(r['result'][0]['Barometer'], 0)
		if reportedValue != domoValue or sensorTimedOut:
			if isVerbose: print 'Updating the Domoticz Barometer device to', reportedValue
			if isVerbose and sensorTimedOut: print '<sensorTimedOut>'
			payload = dict([('type', 'command'), ('param', 'udevice'), ('idx', domoDevice['domoticzIdx']), \
                  ('nvalue', 0), ('svalue', str(reportedValue)+';'+str(getBaroForecast(reportedValue)))])
			r = domoticzAPI(payload)

	# Checking if the 'Rain' device needs an update
	elif domoDevice['categoryName'] == 'Rain' and domoDevice['domoticzSensorType'] == 85:
		# Convert inches of rain to mm
		reportedValue = round(mm(jsonQs['rainin']) * 100, 0)
		reportedValueYear = round(mm(jsonQs['yearlyrainin']), 0)
		try:
			domoValue = round(float(r['result'][0]['Data'].split(';')[1]), 0)
		except:
			domoValue = 0
		if reportedValueYear != domoValue or sensorTimedOut:
			if isVerbose: print 'Updating the Domoticz Rain device from ', domoValue, 'to', reportedValue, ';', reportedValueYear
			if isVerbose and sensorTimedOut: print '<sensorTimedOut>'
			payload = dict([('type', 'command'), ('param', 'udevice'), ('idx', domoDevice['domoticzIdx']), \
					('nvalue', 0), ('svalue', str(reportedValue)+';'+str(reportedValueYear))])
			r = domoticzAPI(payload)

	# Updating the 'Wind' device. No need to check the current value, it's likely to be different anyway
	elif domoDevice['categoryName'] == 'Wind' and domoDevice['domoticzSensorType'] == 86:
		# First build the data string
		dataString = str(jsonQs['winddir_avg10m'])
		dataString += ';' + str(degToCompass(jsonQs['winddir_avg10m']))
		dataString += ';' + str(round(ms(jsonQs['windspdmph_avg10m']) * 10 , 0))
		dataString += ';' + str(round(ms(jsonQs['windgustmph_10m']) * 10 , 0))
		dataString += ';' + str(round(temp_c(jsonQs['tempf']), 1))
		dataString += ';' + str(round(wind_chill(temp_c(jsonQs['tempf']), jsonQs['windspdmph_avg10m']), 1))
		if isDebug: print 'Wind data string: ', dataString # E.g. '4;N;30.0;44.0;-6.6;-14.3'
		if isVerbose: print 'Updating the Domoticz Wind device to', dataString
		payload = dict([('type', 'command'), ('param', 'udevice'), ('idx', domoDevice['domoticzIdx']), \
				('nvalue', 0), ('svalue', dataString)])
		r = domoticzAPI(payload)


	# Checking if the 'UV' device needs an update
	elif domoDevice['categoryName'] == 'UV' and domoDevice['domoticzSensorType'] == 87:
		reportedValue = jsonQs['UV']
		domoValue = int(r['result'][0]['UVI'])
		if reportedValue != domoValue or sensorTimedOut:
			if isVerbose: print 'Updating the Domoticz UV device to', reportedValue
			if isVerbose and sensorTimedOut: print '<sensorTimedOut>'
			payload = dict([('type', 'command'), ('param', 'udevice'), ('idx', domoDevice['domoticzIdx']), \
                  ('nvalue', 0), ('svalue', str(reportedValue)+';0')])
			# Don't loose the ";0" at the end - without it the database may corrupt. You don't want that.
			r = domoticzAPI(payload)

	# Checking if the 'Solar Radiation' device needs an update
	elif domoDevice['categoryName'] == 'Solar Radiation' and domoDevice['domoticzSensorType'] == 20:
		reportedValue = int(jsonQs['solarradiation'])
		domoValue = int(r['result'][0]['Radiation'])
		if reportedValue != domoValue or sensorTimedOut:
			if isVerbose: print 'Updating the Domoticz Solar Radiation device to', reportedValue
			if isVerbose and sensorTimedOut: print '<sensorTimedOut>'
			payload = dict([('type', 'command'), ('param', 'udevice'), ('idx', domoDevice['domoticzIdx']), \
                  ('nvalue', 0), ('svalue', reportedValue)])
			r = domoticzAPI(payload)

	# Checking if the 'Battery Alert' device needs an update
	elif domoDevice['categoryName'] == 'Battery Alert' and domoDevice['domoticzSensorType'] == 7:
		reportedValue = 0 if jsonQs['lowbatt'] == 0 else 4
		rText = 'Everything seems fine' if jsonQs['lowbatt'] == 0 else 'Battery Alert!'
		domoValue = r['result'][0]['Level']
		if reportedValue != domoValue or sensorTimedOut:
			if isVerbose: print 'Updating the Domoticz Battery Alert device to', reportedValue
			if isVerbose and sensorTimedOut: print '<sensorTimedOut>'
			payload = dict([('type', 'command'), ('param', 'udevice'), ('idx', domoDevice['domoticzIdx']), \
                  ('nvalue', reportedValue), ('svalue', rText)])
			r = domoticzAPI(payload)

	return

def degToCompass(num):
	val=int((num/22.5)+.5)
	arr=['N','NNE','NE','ENE','E','ESE', 'SE', 'SSE','S','SSW','SW','WSW','W','WNW','NW','NNW']
	return arr[(val % 16)]

def getHumStat(hum):
	#Humidity_status, 0=Normal, 1=Comfortable, 2=Dry, 3=Wet
	if hum <30: return 2
	elif hum >70: return 3
	elif hum >=50 and hum <=60: return 1
	else: return 0

def getBaroForecast(mbar):
	# Barometer forecast, 0 = Stable, 1 = Sunny, 2 = Cloudy, 3 = Unstable, 4 = Thunderstorm, 5 = Unknown, 6 = Cloudy/Rain
	return 5

def load_config():
	try:
		with open(cfgFile) as json_data_file:
			cfg = json.load(json_data_file)
	except:
		logMessage = 'Can not open the config file ' + cfgFile
		print logMessage, sys.exc_info()[0]
		sys.exit(0)
	return cfg

def connected_to_internet(url='http://www.google.com/', timeout=5):
	try:
		_ = requests.head(url, timeout=timeout)
		return True
	except requests.ConnectionError:
		print('No internet connection available.')
		return False

def domoticzAPI(payload):
	try:
		r = requests.get(cfg['domoticz']['protocol'] + '://' + cfg['domoticz']['hostName'] + ':' + \
				str(cfg['domoticz']['portNumber']) + '/json.htm', \
				verify=False, \
				auth=(cfg['domoticz']['httpBasicAuth']['userName'], cfg['domoticz']['httpBasicAuth']['passWord']), \
				params=payload)
	except:
		print('Can not open domoticz URL: \'' + cfg['domoticz']['protocol'] + '://' + cfg['domoticz']['hostName'] + ':' + \
					str(cfg['domoticz']['portNumber']) + '/json.htm\'', sys.exc_info()[0])
		sys.exit(0)
	if r.status_code <> 200:
		print 'Unexpected status code from Domoticz: ' + str(r.status_code)
		sys.exit(0)
	try:
		rJsonDecoded = r.json()
	except:
		print('Can\'t Json decode response from Domoticz.', sys.exc_info()[0])
		sys.exit(0)
	if rJsonDecoded['status'] <> 'OK':
		print 'Unexpected response from Domoticz: ' + rJsonDecoded['status']
		sys.exit(0)
	return rJsonDecoded

def logToDomoticz(messageType, logMessage):
	payload = dict([('type', 'command'), ('param', 'addlogmessage'), \
						('message', '(' + messageType+ ') ' + os.path.basename(sys.argv[0]) + ': ' + logMessage)])
	r = domoticzAPI(payload)
	return r

def saveWindData(jsonQs):
	global arrWindDir, arrWindSpeed, arrGustSpeed
	global average_wind_speed_2min, max_gust_speed_2min, wdir_2min
	global average_wind_speed_10min, max_gust_speed_10min, wdir_10min

	# Conversion base : 1 mph = 0.44704 mps
	windSpeed = round(jsonQs['windspeedmph'] * 0.44704, 1)
	windGustSpeed = round(jsonQs['windgustmph'] * 0.44704, 1)
	windDir = jsonQs['winddir']
	if runs == 1:
		# Array will hold approximately 10 minutes of data
		elements = 10*60/UPDATE_INTERV
		arrWindDir = [windDir] * elements
		arrWindSpeed = [windSpeed] * elements
		arrGustSpeed = [windGustSpeed] * elements
		wdir_4last = wdir_2min = wdir_10min = windDir
		average_wind_speed_2min = average_wind_speed_10min = windSpeed
		max_gust_speed_2min = max_gust_speed_10min = windGustSpeed
	else:
		# Delete the oldest reading
		arrWindDir.pop(0)
		arrWindSpeed.pop(0)
		arrGustSpeed.pop(0)
		# Push the latest reading onto the end of array
		arrWindDir.append(windDir)
		arrWindSpeed.append(windSpeed)
		arrGustSpeed.append(windGustSpeed)
	
		elements2min = 2*60/UPDATE_INTERV
		# The average wind speed is a simple average of wind velocity, regardless of direction
		average_wind_speed_2min = round(sum(arrWindSpeed[-elements2min:]) / elements2min, 1)
		average_wind_speed_10min = round(sum(arrWindSpeed) / len(arrWindSpeed), 1)
		max_gust_speed_2min = max(arrGustSpeed[-elements2min:])
		max_gust_speed_10min = max(arrGustSpeed)

		if cfg['system']['windDirSmoothenizer']: wspd_4last, wdir_4last = windvec(scipy.array(arrWindSpeed[-4:]), scipy.array(arrWindDir[-4:]))
		wspd_2min, wdir_2min = windvec(scipy.array(arrWindSpeed[-elements2min:]), scipy.array(arrWindDir[-elements2min:]))
		wspd_10min, wdir_10min = windvec(scipy.array(arrWindSpeed), scipy.array(arrWindDir))

		if isVerbose:
			print 'Past 2 minutes resultant wind direction:', wdir_2min
			print 'Past 10 minutes resultant wind direction:',  wdir_10min
			print 'Past 2 minutes average wind speed:',  average_wind_speed_2min
			print 'Past 10 minutes average wind speed:',  average_wind_speed_10min
			print 'Past 2 minutes maximum wind gust speed:',  max_gust_speed_2min
			print 'Past 10 minutes maximum wind gust speed:',  max_gust_speed_10min
		if isDebug and isVerbose:
			print arrWindDir
			print arrWindSpeed
			print arrGustSpeed

	if isDebug: print '\nLatest wind speed reading: ', jsonQs['winddir']
	if cfg['system']['windDirSmoothenizer']:
		jsonQs['winddir'] = wdir_4last
		if isDebug: print '\'windDirSmoothenizer\' is active. Resultant of the last 4 wind direction readings (Reported to WU) : ', wdir_4last
		if isDebug: print 'array', arrWindDir[-4:], "\n"
	jsonQs['windspdmph_avg2m'] = round(mph(average_wind_speed_2min), 2)
	jsonQs['windspdmph_avg10m'] = round(mph(average_wind_speed_10min), 2)
	jsonQs['windgustmph_2m'] = round(mph(max_gust_speed_2min), 2)
	jsonQs['windgustmph_10m'] = round(mph(max_gust_speed_10min), 2)
	jsonQs['winddir_avg2m'] = wdir_2min
	jsonQs['winddir_avg10m'] = wdir_10min
	return

# See http://python.hydrology-amsterdam.nl/modules/meteolib.py
def windvec(u= scipy.array([]), D=scipy.array([])):
	ve = 0.0 # define east component of wind speed
	vn = 0.0 # define north component of wind speed
	D = D * math.pi / 180.0 # convert wind direction degrees to radians
	for i in range(0, len(u)):
		ve = ve + u[i] * math.sin(D[i]) # calculate sum east speed components
		vn = vn + u[i] * math.cos(D[i]) # calculate sum north speed components
	ve = - ve / len(u) # determine average east speed component
	vn = - vn / len(u) # determine average north speed component
	uv = math.sqrt(ve * ve + vn * vn) # calculate wind speed vector magnitude
	# Calculate wind speed vector direction
	vdir = scipy.arctan2(ve, vn)
	vdir = vdir * 180.0 / math.pi # Convert radians to degrees
	if vdir < 180:
		Dv = vdir + 180.0
	else:
		if vdir > 180.0:
			Dv = vdir - 180
		else:
			Dv = vdir
	return round(uv, 1), int(round(Dv)) # uv in m/s, Dv in dgerees from North

def print_help(argv):
	print 'usage: ' + os.path.basename(__file__) + ' [option]  [-C domoticzDeviceidx|all] \nOptions and arguments'
	print '-d     : debug output (also --debug)'
	print '-h     : print this help message and exit (also --help)'
	print '-v     : verbose'
	print '-V     : print the version number and exit (also --version)'

def temp_f(c):
	"Convert temperature from Celsius to Fahrenheit"
	if c is None: return None
	return (c * 9.0 / 5.0) + 32.0

def temp_c(f):
	"Convert temperature from Fahrenheit to Celsius"
	if f is None: return None
	return (f -32) / 1.8

def ms(mph):
	"Convert mph to m/s"
	if ms is None: return None
	return mph * 0.44704

def mph(ms):
	"Convert m/s to mph"
	if ms is None: return None
	return ms * 2.2369362920544

def mbar(inHg):
	"Convert m/s to mph"
	if inHg is None: return None
	return inHg * 33.8637526

def mm(inch):
	"Convert inch to mm"
	if inch is None: return None
	return inch * 25.4

def wind_chill(temp, wind):
	# Compute wind chill, using formula from http://en.wikipedia.org/wiki/wind_chill
	if temp is None or wind is None: return None
	wind_kph = wind * 3.6
	if wind_kph <= 4.8 or temp > 10.0: return temp
	return min(13.12 + (temp * 0.6215) + (((0.3965 * temp) - 11.37) * (wind_kph ** 0.16)), temp)

def is_number(s):
	try:
		n=str(float(s))
		if n == "nan" or n=="inf" or n=="-inf" : return False
	except ValueError:
		try:
			complex(s) # for complex
		except ValueError:
			return False
	return True

def main(argv):
	global isDebug
	global isVerbose
	try:
		opts, args = getopt.getopt(argv, 'dhvV', ['help', 'debug', 'version'])
	except getopt.GetoptError:
		print_help(argv)
		sys.exit(2)
	for opt, arg in opts:
		if opt in ('-h', '--help'):
			print_help(argv)
			sys.exit(0)
		elif opt in ('-d', '--debug'):
			isDebug = True
		elif opt in ('-v'):
			isVerbose = True
		elif opt in ('-V', '--version'):
			print PROGRAMNAME + ' ' + VERSION
			sys.exit(0)

	if isDebug: print 'Debug is on'
	global cfg; cfg = load_config()

	if not connected_to_internet():
		logToDomoticz(MSG_ERROR, 'No internet connection available')
		sys.exit(0)

	try:
		#Create a web server and define the handler to manage the
		#incoming request
		if isDebug:
			listenPort = cfg['system']['listenPortDebug']
		else:
			listenPort = cfg['system']['listenPort']
		server = HTTPServer(('', listenPort), myHandler)
		msgProgInfo = PROGRAM_NAME + ' ' + VERSION + ' listening for PWS on port ' + str(listenPort) + '. '
		msgProgInfo += ' Running on TTY console...' if tty else ' Running as a CRON job...'
		logToDomoticz(MSG_EXEC, msgProgInfo)
		if isVerbose: print msgProgInfo
		#Wait forever for incoming htto requests
		server.serve_forever()
	except socket.error as msg:
		error_code = msg.args[0]
		error_string = msg.args[1]
		print "Process already running (%d:%s ). Exiting" % ( error_code, error_string)
		sys.exit(0)
	except KeyboardInterrupt:
		print '^C received, shutting down the web server'
		server.socket.close()

if __name__ == "__main__":
  main(sys.argv[1:])
