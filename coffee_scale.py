#!/usr/bin/env python
import os
import fcntl
import struct
from datetime import datetime, timedelta
from time import sleep, strftime
import logging
from logging.handlers import TimedRotatingFileHandler
import glob
import shutil
from ISStreamer.Streamer import Streamer
import hipchat
import math
import requests
import json

logger = logging.getLogger("coffee_log")
logger.setLevel(logging.INFO)

_currentWeight = 0
_weightChangedThreshold = 5
_emptyPotThreshold = 10
_loopCount = 0
_logToHipChatLoopCount = 40
_mugAmounts = [1200, 1466, 1732, 1998, 2264, 2530]
_mugFluidCapacity = 266

_initialStateKey = os.environ.get('INITIAL_STATE_ACCESS_KEY')
if not _initialStateKey:
    logger.error("### Initial State Key not set in environment variable")

_environment = os.environ.get("ENVIRONMENT")
if not _environment:
    _environment = "prod"

_hipchatKey = os.environ.get('HIPCHAT_KEY')
if not _hipchatKey:
    logger.error('### Hipchat API Key missing from environment variable HIPCHAT_KEY')

_ledServiceUrl = os.environ.get('LED_SERVICE_URL')
if not _ledServiceUrl:
   logger.error('### LED_SERVICE_URL environment variable has not been set') 

def getWeightInGrams(dev="/dev/usb/hiddev0"):
    """
    This device normally appears on /dev/usb/hiddev0, assume
    device still appears on this file handle.
    """
    # If we cannot find the USB device, return -1

    grams = -1
    try:
        fd = os.open(dev, os.O_RDONLY)

        # Read 4 unsigned integers from USB device
        hiddev_event_fmt = "IIII"
        usb_binary_read = struct.unpack(hiddev_event_fmt, os.read(fd, struct.calcsize(hiddev_event_fmt)))
        grams = usb_binary_read[3]
        os.close(fd)
    except OSError as e:
        print("{0} - Failed to read from USB device".format(datetime.utcnow()))
    return grams

def moveLogsToArchive(tempFilePath, archiveDir):
    """
    Using shutil.move here since the raspberry pi will 
    be moving the files from a tempfs file system to the
    ext3 file system on the SD card. 
    """
    tempFileName = os.path.basename(tempFilePath)
    tempFileDir = os.path.dirname(tempFilePath)
    logFiles = glob.glob("{0}.*".format(tempFilePath))

    for fileName in logFiles:
        shutil.move(fileName, os.path.join(archiveDir, os.path.basename(fileName)))

def shouldLogWeight(newReading):
    return abs(_currentWeight - newReading) > _weightChangedThreshold

def potIsLifted():
    return _currentWeight <= _emptyPotThreshold

def shouldPostToHipChat():
    return _loopCount == _logToHipChatLoopCount

def shouldPostToLed():
    return _loopCount == _logToHipChatLoopCount

def postToLed():
    displayJson = {}
    totalAvailableMugs = len(_mugAmounts)
    displayJson['text'] = "{0} / {1} - {2} / {3}".format(getAvailableMugs(), totalAvailableMugs,
            _currentWeight, _mugAmounts[totalAvailableMugs - 1])

    url = "{0}/display".format(_ledServiceUrl)
    payload = json.dumps(displayJson)
    headers = {'content-type': 'application/json'}

    # TODO: Identify why the this code is throwing the following exception:
    # requests.exceptions.ConnectionError: ('Connection aborted.', BadStatusLine('HTTP/1.1 1 \r\n',))
    try:
        response = requests.post(url, data=payload, headers=headers)
    except:
        pass

def logToInitialState():
    utcnow = datetime.utcnow()
    bucketKey = "{0} - coffee_scale_data".format(_environment)

    streamer = Streamer(bucket_name="{0} - Coffee Scale Data".format(_environment), 
            bucket_key=bucketKey, access_key=_initialStateKey)

    if potIsLifted():
        streamer.log("Coffee Pot Lifted", True)
    streamer.log("Coffee Weight", _currentWeight)
    streamer.close()

def getHipchatParameters():
    parameters = {}
    # Fridge Room
    parameters['room_id'] = 926556
    totalAvailableMugs = len(_mugAmounts)
    parameters['from'] = "{0} / {1}".format(getAvailableMugs(), totalAvailableMugs)
    parameters['message'] = "{0} / {1}".format(_currentWeight, _mugAmounts[totalAvailableMugs - 1]) 
    parameters['color'] = 'random'

    return parameters

def writeToHipChat():
    hipster = hipchat.HipChat(token=_hipchatKey)
    params = getHipchatParameters()
    hipster.method('rooms/message', method='POST', parameters=params)

def getAvailableMugs():
    availableMugs = 0
    for mugAmount in _mugAmounts:
        minimumWeightForMug = math.floor(mugAmount - (_mugFluidCapacity * .1)) - 10
        if _currentWeight <= minimumWeightForMug:
            break
        availableMugs += 1

    return availableMugs
        
def main(args):
    rotateMinutes = timedelta(minutes = args.logRotateTimeMinutes)
    rotateTime = datetime.utcnow() + rotateMinutes
    global _currentWeight 
    global _loopCount
    _currentWeight = getWeightInGrams()

    while True:
        _loopCount += 1
        tmpWeight = getWeightInGrams()
        if datetime.utcnow() > rotateTime:
            moveLogsToArchive(args.tempFile, args.permanentDirectory)
            rotateTime = datetime.utcnow() + rotateMinutes

        if shouldLogWeight(tmpWeight):
            logger.info("{0},{1}".format(datetime.utcnow().strftime("%Y-%m-%dT%X"), tmpWeight))
            _currentWeight = tmpWeight
            logToInitialState()

        if shouldPostToLed():
            _loopCount = 0
            postToLed()

        # if shouldPostToHipChat():
        #     _loopCount = 0
        #     writeToHipChat()

        sleep(1)

def getParser():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('tempFile', help='Temporary output location file to write', 
            default='/var/tmp/coffee_scale')
    parser.add_argument('permanentDirectory', help='Permanent storage location for scale data')
    parser.add_argument('logRotateTimeMinutes', 
            help='Number of minutes to capture data in the temp-file before writing to the permanent directory',
            type=int)

    return parser

if __name__ == "__main__":
    parser = getParser()
    args = parser.parse_args()
    handler = TimedRotatingFileHandler(args.tempFile,
            when="m", interval=args.logRotateTimeMinutes, utc=True)

    logger.addHandler(handler)
    main(args)

