#!/bin/sh

if pgrep -x "interceptWH2600" > /dev/null
then
	echo "Running"
else
	echo "Stopped, trying to restart it"
	/etc/init.d/interceptWH2600.sh stop
	sleep 3
	/etc/init.d/interceptWH2600.sh start
fi