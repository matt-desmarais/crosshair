import os, sys, inspect
# realpath() will make the script run, even if you symlink it
cmd_folder = os.path.realpath(os.path.abspath(os.path.split(inspect.getfile( inspect.currentframe() ))[0]))
if cmd_folder not in sys.path:
    sys.path.insert(0, cmd_folder)
# to include modules from a subfolder
cmd_subfolder = os.path.realpath(os.path.abspath(os.path.join(os.path.split(inspect.getfile( inspect.currentframe() ))[0],"include")))
if cmd_subfolder not in sys.path:
    sys.path.insert(0, cmd_subfolder)


import datetime
import time
import picamera
import numpy as np
import cv2
import RPi.GPIO as GPIO
import patterns
import ConfigParser

def get_file_name():  # new
    return datetime.datetime.now().strftime("%Y-%m-%d_%H.%M.%S.h264")


# subclass for ConfigParser to add comments for settings
# (adapted from jcollado's solution on stackoverflow)
class ConfigParserWithComments(ConfigParser.ConfigParser):
    def add_comment(self, section, comment):
        self.set(section, '; %s' % (comment,), None)

    def write(self, fp):
        """Write an .ini-format representation of the configuration state."""
        for section in self._sections:
            fp.write("[%s]\n" % section)
            for (key, value) in self._sections[section].items():
                self._write_item(fp, key, value)
            fp.write("\n")

    def _write_item(self, fp, key, value):
        if key.startswith(';') and value is None:
            fp.write("%s\n" % (key,))
        else:
            fp.write("%s = %s\n" % (key, str(value).replace('\n', '\n\t')))

# settings from config file:
configfile = '/boot/crosshair.cfg'
cdefaults = {
            'width': '800',
            'height': '600',
            'color': 'white',
            'pattern': '1',
            'radius': '100',
            'xcenter': '400',
            'ycenter': '300',
            'stream': 'false',
            'upload': 'false'
            }

# if config file is missing, recreate it with default values:
def CreateConfigFromDef(fileloc,defaults):
    print "Config file not found."
    print "Recreating " + fileloc + " using default settings."
    config.add_section('main')
    config.add_section('overlay')
    config.set('overlay', 'xcenter', cdefaults.get('xcenter'))
    config.set('overlay', 'ycenter', cdefaults.get('ycenter'))
    config.add_comment('overlay', 'color options: white (default), red, green, blue, yellow')
    config.set('overlay', 'color', cdefaults.get('color'))
    config.add_comment('overlay', 'pattern options:')
    config.add_comment('overlay', '1: Bruker style with circles and ticks')
    config.add_comment('overlay', '2: simple crosshair with ticks')
    config.add_comment('overlay', '3: simple crosshair without ticks')
    config.add_comment('overlay', '4: crosshair with circles, no ticks')
    config.add_comment('overlay', '5: crosshair with one circle, no ticks')
    config.add_comment('overlay', '6: only one circle')
    config.add_comment('overlay', '7: small crosshair')
    config.add_comment('overlay', '8: small crosshair without intersection')
    config.add_comment('overlay', '9: only a dot')
    config.add_comment('overlay', '10: grid')
    config.set('overlay', 'pattern', cdefaults.get('pattern'))
    config.add_comment('overlay', 'set radius (in px) for all circles,')
    config.add_comment('overlay', 'also controls grid spacing in Pattern 10')
    config.set('overlay', 'radius', cdefaults.get('radius'))
    config.set('main', 'width', cdefaults.get('width'))
    config.set('main', 'height', cdefaults.get('height'))
    config.add_comment('main', 'uploading and streaming not implemented yet')
    config.set('main', 'upload', cdefaults.get('upload'))
    config.set('main', 'stream', cdefaults.get('stream'))
    # write default settings to new config file:
    with open(fileloc, 'wb') as f:
        config.write(f)

# try to read settings from config file; if it doesn't exist
# create one from defaults & use same defaults for this run:
try:
    with open(configfile) as f:
        config = ConfigParserWithComments(cdefaults)
        config.readfp(f)
except IOError:
    config = ConfigParserWithComments(cdefaults)
    CreateConfigFromDef(configfile,cdefaults)

# retrieve settings from config parser:
width = int(config.get('main', 'width'))
height = int(config.get('main', 'height'))
print "Set resolution: " + str(width) + "x" + str(height)
# make sure width is a multiple of 32 and height
# is a multiple of 16:
if (width%32) > 0 or (height%16) > 0:
    print "Rounding down set resolution to match camera block size:"
    width = width-(width%32)
    height = height-(height%16)
    print "New resolution: " + str(width) + "x" + str(height)
curcol = config.get('overlay', 'color')
curpat = int(config.get('overlay', 'pattern'))
xcenter = int(config.get('overlay', 'xcenter'))
ycenter = int(config.get('overlay', 'ycenter'))
radius = int(config.get('overlay', 'radius'))

# map colors:
colors = {
        'white': (255,255,255),
        'red': (255,0,0),
        'green': (0,255,0),
        'blue': (0,0,255),
        'yellow': (255,255,0),
        }

# initialize toggle for on/off button and gui state:
togsw = 1
guivisible = 1
gunRange = 30

# initialize GPIO and assign buttons:
GPIO.setmode(GPIO.BCM)
# GPIO 24, 23 & 18 set up as inputs, pulled up to avoid false detection.
# Both ports are wired to connect to GND on button press.
GPIO.setup(24, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(14, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(18, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# threaded callbacks to run in new thread when button events are detected
# function to call when top button is pressed (GPIO 24):
def toggleonoff(channel):
    global togsw,o
    if togsw == 1:
        print "Toggle Crosshair OFF"
        camera.remove_overlay(o)
        togsw = 0
    else:
        print "Toggle Crosshair ON"
        if guivisible == 0:
            o = camera.add_overlay(np.getbuffer(ovl), layer=3, alpha=160)
        else:
            o = camera.add_overlay(np.getbuffer(gui), layer=3, alpha=160)
        togsw = 1
    return

# function to call when middle button is pressed (GPIO 23):
def togglepattern(channel):
    global togsw,o,curpat,col,ovl,gui
    # if overlay is inactive, ignore button:
    if togsw == 0:
        print "Pattern button pressed, but ignored --- Crosshair not visible."
    # if overlay is active, drop it, change pattern, then show it again
    else:
        curpat += 1
        print "Set new pattern: " + str(curpat) 
        if curpat > patterns.maxpat:     # this number must be adjusted to number of available patterns!
            curpat = 1
        if guivisible == 0:
            # reinitialize array:
            ovl = np.zeros((height, width, 3), dtype=np.uint8)
            patternswitch(ovl,0)
            if 'o' in globals():
                camera.remove_overlay(o)
            o = camera.add_overlay(np.getbuffer(ovl), layer=3, alpha=160)
        else:
            # reinitialize array
            gui = np.zeros((height, width, 3), dtype=np.uint8)
            creategui(gui)
            patternswitch(gui,1)
            if 'o' in globals():
                camera.remove_overlay(o)
            o = camera.add_overlay(np.getbuffer(gui), layer=3, alpha=160)
    return

# function to call when low button is pressed (GPIO 18):
def togglecolor(channel):
    global togsw,o,curcol,col,ovl,gui
    # step up the color to next in list
    curcol = colorcycle(colors,curcol)
    # map colorname to RGB value for new color
    col = colormap(curcol)
    # if overlay is inactive, ignore button:
    if togsw == 0:
        print "Color button pressed, but ignored --- Crosshair not visible."
    # if overlay is active, drop it, change color, then show it again
    else:
        print "Set new color: " + str(curcol) + "  RGB: " + str(col) 
        if guivisible == 0:
            # reinitialize array:
            ovl = np.zeros((height, width, 3), dtype=np.uint8)
            patternswitch(ovl,0)
            if 'o' in globals():
                camera.remove_overlay(o)
            o = camera.add_overlay(np.getbuffer(ovl), layer=3, alpha=160)
        else:
            # reinitialize array
            gui = np.zeros((height, width, 3), dtype=np.uint8)
            creategui(gui)
            patternswitch(gui,1)
            if 'o' in globals():
                camera.remove_overlay(o)
            o = camera.add_overlay(np.getbuffer(gui), layer=3, alpha=160)
    return

GPIO.add_event_detect(24, GPIO.FALLING, callback=toggleonoff, bouncetime=300)
GPIO.add_event_detect(14, GPIO.FALLING, callback=togglepattern, bouncetime=300)
GPIO.add_event_detect(18, GPIO.FALLING, callback=togglecolor, bouncetime=300)

# map text color names to RGB:
def colormap(col):
    return colors.get(col, (255,255,255))    # white is default

# cycle through color list starting from current color:
def colorcycle(self, value, default='white'):
    # create an enumerator for the entries and step it up
    for i, item in enumerate(self):
        if item == value:
            i += 1
            # if end of color list is reached, jump to first in list
            if i >= len(self):
                i = 0
            return self.keys()[i]
    # if function fails for some reason, return white
    return default

# function to construct/draw the GUI
def creategui(target):
    cv2.putText(target, gui1, (10,height-138), font, 2, col, 2)
    cv2.putText(target, gui2, (10,height-108), font, 2, col, 2)
    cv2.putText(target, gui3, (10,height-78), font, 2, col, 2)
    cv2.putText(target, gui4, (10,height-48), font, 2, col, 2)
    #cv2.putText(target, gui5, (10,height-18), font, 2, col, 2)
    #cv2.putText(target, 'GUI will vanish after 10s', (10,30), font, 2, col, 2)
    return

# function to construct and draw the overlay, options are "gui" or "ovl" and 0 or 1
def patternswitch(target,guitoggle):
    global o
    # first remove existing overlay:
    if 'o' in globals():
        camera.remove_overlay(o)
    if guitoggle == 1:
        creategui(gui)
    # cycle through possible patterns:
    if curpat == 1:
        patterns.pattern5(target, width, height, xcenter, ycenter-10, radius, col)
        gunRange = 25
    if curpat == 2:
        patterns.pattern5(target, width, height, xcenter, ycenter-20, radius, col)
        gunRange = 20
    if curpat == 3:
        patterns.pattern5(target, width, height, xcenter, ycenter-30, radius, col)
        gunRange = 15
    if curpat == 4:
        patterns.pattern5(target, width, height, xcenter, ycenter-40, radius, col)
        gunRange = 10
    if curpat == 5:
        patterns.pattern5(target, width, height, xcenter, ycenter, radius, col)
        gunRange = 30
    if curpat == 6:
        patterns.pattern5(target, width, height, xcenter, ycenter+10, radius, col)
        gunRange = 40
    if curpat == 7:
        patterns.pattern5(target, width, height, xcenter, ycenter+20, radius, col)
        gunRange = 50
    if curpat == 8:
        patterns.pattern5(target, width, height, xcenter, ycenter+30, radius, col)
        gunRange = 60
    if curpat == 9:
        patterns.pattern5(target, width, height, xcenter, ycenter+40, radius, col)
        gunRange = 70
    if curpat == 10:
        patterns.pattern5(target, width, height, xcenter, ycenter, radius, col)
        gunRange = 30
    
    # Add the overlay directly into layer 3 with transparency;
    # we can omit the size parameter of add_overlay as the
    # size is the same as the camera's resolution
    o = camera.add_overlay(np.getbuffer(target), layer=3, alpha=160)
    return

# create array for the overlay:
ovl = np.zeros((height, width, 3), dtype=np.uint8)
font = cv2.FONT_HERSHEY_PLAIN
col = colormap(curcol)
# create array for a bare metal gui and text:
gui = np.zeros((height, width, 3), dtype=np.uint8)
gui1 = 'Dont be a bitch'
gui2 = 'Go get some kills'
gui3 = 'button  = cycle distance'
gui4 = 'range: '+str(gunRange)
gui5 = 's/r     = save/revert settings'

with picamera.PiCamera() as camera:
    camera.resolution = (width, height)
    camera.framerate = 24
    filename = get_file_name()
    camera.iso = 800
    camera.start_recording(filename)
    # set this to 1 when switching to fullscreen output
    camera.preview_fullscreen = 1
    #camera.preview_window = (0,0,width,height)
    camera.start_preview()
    #camera.annotate_background = 'black'
    try:
        # show gui fot 10 seconds:
        patternswitch(gui,1)
        time.sleep(10)
        guivisible = 1
        # cycle through possible patterns:
        patternswitch(ovl,0)
        while True:
            time.sleep(1)
    finally:
        camera.close()               # clean up camera
        GPIO.cleanup()               # clean up GPIO
