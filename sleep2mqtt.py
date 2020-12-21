import gc
import json
import network
import ntptime
import uos
import utime
from copy import deepcopy
from machine import Pin, ADC
from simple import MQTTClient
from ubinascii import hexlify
from m5ui import *

# this program requires 4 additional python libraries to be manually loaded
#
# npttime.py 
#    https://github.com/micropython/micropython/blob/master/ports/esp8266/modules/ntptime.py
# simple.py
#    https://github.com/micropython/micropython-lib/blob/master/umqtt.simple/umqtt/simple.py
# copy.py
#    https://github.com/micropython/micropython-lib/blob/master/copy/copy.py
# types.py (needed by copy.py)
#    https://github.com/micropython/micropython-lib/blob/master/types/types.py
#
# I recommend compiling all libraies and sleep2mqtt.py with mypcross to save memory:
#    https://github.com/micropython/micropython/tree/master/mpy-cross

class BedSensor():
    '''
    only tested with MPXV7002GP analog differential pressure sensors on ESP32
        available analog pins on M5Stack Core: 35 & 36
        Parameters:
            name = name of the bed side
            pin = analog pin on ESP32 connected to the MPXV7002GP sensor
            ideal_pressure = the perfect pressure to maintain at all times
            delta = the amount of pressure increase for a person
    '''
    all_sensors = []
    sensitivity = 5
    def __init__(self, name, pin, ideal_pressure, delta):
        
        BedSensor.all_sensors.append(self)

        self.name = name
        self.ideal_pressure = ideal_pressure
        self.value = 0
        self.p_value = 0
        self.pin = ADC(Pin(pin))
        self.pin.atten(ADC.ATTN_11DB)
        self.pin.width(ADC.WIDTH_12BIT)

        # keep and track a pressure history that adapts to a delta
        self.history = {}
        # the % of difference between on/off
        self.delta = delta
        # timestamps
        self.ts = None
        self.timestamp(update=True)
        self.saved_timestamp = utime.time()
        self.ideal_pressure_ts = utime.time()
        self.warmed_up = False
        # load sensor data from state file on disk
        self.restore_state()


    def quiet_read(self):
        # read sensor, but don't process it
        smooth = []
        for x in range(10):
            smooth.append(self.pin.read())
        value = sum(smooth) / len(smooth)
        return value


    def read(self):

        # new value is taken from the average of 10 readings
        smooth = []
        for x in range(10):
            smooth.append(self.pin.read())
        
        value = sum(smooth) / len(smooth)

        # scale voltage to 0-1 range based on sensor min/max
        scaled = float(value - 142) / float(3150 - 142)
        
        # convert the scaled value to percentage of total
        self.value = 100 - (scaled * 100)

        # create a "print" value - 100 should have no decimal, otherwise pad to 2 decimal points
        if self.value == 100:
            self.p_value = "100"
        else:
            self.p_value = "{:0.2f}".format(self.value) # 2 decimal points with trailing 0

        # determine if the state has changed using self-updating adptive data
        state_changed = self.adaptive_state()
        return state_changed


    def set_sensitivity(sensivity):
        # sets how sensitive the sensor is to state change
        # 1 is least sensitive, 10 is most sensitive
        BedSensor.sensitivity = float((10 - sensivity)/10)


    def adaptive_state(self):
        # Goal: compensate for atmospheric pressure and temp variation, 
        # and continually adapt the sensor on/off baselines
        # 
        # This function tracks sensor history for both on/off and compares new readings 
        # against a sliding average of the last 10 readings to determine state change 
        # 

        state_changed = False

        # store history data (by on/off name) based on state
        if self.state():
            state = "on"
            anti_state = "off"
        else:
            state = "off"
            anti_state = "on"

        history = self.history[state]
        history_avg = self.history["{}_avg".format(state)]

        # average the history when fully populated
        if len(history) == 10:
            avg = sum(history) / len(history)
        else:
            avg = history_avg

        # calc the difference between history average and current value
        change = abs(avg - float(self.value))

        # check for state change
        if change > (self.delta * BedSensor.sensitivity):
            if float(self.value) > avg:  # if the pressure is rising...
                if self.state(): # who cares if it's already on
                    pass 
                else: # somebody got in bed
                    self.state(state=True)
                    state_changed = True
            else: # the pressure is dropping...
                if self.state(): # somebody got out of bed
                    self.state(state=False)
                    state_changed = True
                else: 
                    pass # nobody was in bed anyway

        # direct self.value and a delta of self.value to the correct histories
        if state_changed:
            self.ideal_pressure_ts = utime.time()
            self.warmed_up = False
            value_target = anti_state
            delta_target = state
        else:
            value_target = state
            delta_target = anti_state

        # save the current self.value to the correct history
        self.history[value_target].append(float(self.value))

        # save a delta of self.value to the opposite history
        if self.state(): # if it's on, delta is removed
            self.history[delta_target].append(float(self.value) - float(self.delta))
        else: # if it's off, delta is added
            self.history[delta_target].append(float(self.value) + float(self.delta))
            
        # keep only last 10 values of each history
        while len(self.history[state]) > 10:
            self.history[state].pop(0)

        while len(self.history[anti_state]) > 10:
            self.history[anti_state].pop(0)

        # update averages
        if len(self.history[state]) == 10:
            self.history["{}_avg".format(state)] = sum(self.history[state]) / len(self.history[state])

        if len(self.history[anti_state]) == 10:
            self.history["{}_avg".format(anti_state)] = sum(self.history[anti_state]) / len(self.history[anti_state])

        # update state file every 5m
        if utime.time() - self.saved_timestamp > 300:
            self.save_state()

        return state_changed


    def state(self, state=None):
        # if no state is passed, return current state
        # otherwise, update internal state and write to disk
        if state is not None:
            self.current_state = state
            self.save_state()
        else:
            return self.current_state


    def reset(self):
        # reset the history and reset state
        print('resetting sensor: {}'.format(self.name))
        
        try:
            self.create_history()
        except Exception as e:
            print('error creating history: {}'.format(e))
        
        try:
            self.state(state=False)
        except Exception as e:
            print('error setting state: {}'.format(e))
        
        try:
            self.read()
        except Exception as e:
            print('error reading: {}'.format(e))


    def save_state(self):
        # read last state file from sd card
        try:
            with open('/sd/state.json', 'r') as f:
                state = json.load(f)
        except Exception as e:
            print('error loading state {}'.format(e))
            state = {}

        # udpate state for self
        state[self.name] = {}
        state[self.name]['state'] = self.state()
        state[self.name]['history'] = self.history

        # write state to back to file
        try:
            with open('/sd/state.json', 'w+') as f: 
                json.dump(state, f)
            self.saved_timestamp = utime.time()
        except Exception as e:
            print('error saving state: {}'.format(e))
            mount_sd()


    def restore_state(self):
        # load previous state from sd card
        loaded = False
        try:
            with open('/sd/state.json', 'r') as f: 
                state = json.load(f)
            loaded = True
        except Exception as e:
            print('Error loading state json for {}: {}'.format(self.name, e))
            self.current_state = False
            self.create_history()

        if loaded:
            try:
                self.current_state = state[self.name]['state']
            except Exception as e:
                print('Error restoring state for {}: {}'.format(self.name, e))
                self.current_state = False

            try:
                self.history = state[self.name]['history']
            except Exception as e:
                print('Error restoring history for {}: {}'.format(self.name, e))
                self.create_history()


    def create_history(self):
        # seed history data with reasonable assumptions
        self.history["on"] = []
        self.history["off"] = []
        for i in range(10):
            self.history["off"].append(float(self.ideal_pressure - self.delta))
            self.history["on"].append(float(self.ideal_pressure))
        self.history["off_avg"] = float(float(self.ideal_pressure - self.delta))
        self.history["on_avg"] = float(self.ideal_pressure)


    def timestamp(self, update=False):
        if update:
            self.ts = utime.time()
        else:
            return self.ts


    def sensors():
        return BedSensor.all_sensors


    def sensor_name(name):
        for sensor in BedSensor.sensors():
            if sensor.name == name:
                return sensor
        return None


##################################
###
###     UTILITY AND SETUP FUNCTIONS
###
##################################


def save_config():
    try:
        with open(config_file, 'w+') as f: 
            json.dump(config, f)
        log('saved config')
        publish_config_mqtt()
    except Exception as e:
        log('error saving config: {}'.format(e))
        mount_sd()


def load_config():
    try:
        uos.stat(config_file)
        log('reading {}'.format(config_file))
        try:
            global config
            with open(config_file, 'r') as f:
                config = json.load(f)
            return config
        except Exception as e:
            log('error loading config file {}'.format(e))
            machine.reset()
    except Exception as e:
        log('cannot find {}'.format(config_file))
        log('Error: {}'.format(e))
        machine.reset()


def fib(n):
    # return the nth number in the Fibonacci sequence
    # this is used for retry backoffs
    if n == 0: return 0
    elif n == 1: return 1
    else: return fib(n-1)+fib(n-2)


def buttonA_wasPressed():
    # decrease brightness 
    global brightness
    if brightness >= 10:
        brightness = brightness - 10
    else:
        brightness = 0
    lcd.setBrightness(brightness)
    print('brightness to {}'.format(brightness))


def buttonB_wasPressed():
    pass


def buttonC_wasPressed():
    # increase brightness 
    global brightness
    if brightness <= 90:
        brightness = brightness + 10
    else:
        brightness = 100
    lcd.setBrightness(brightness)
    print('brightness to {}'.format(brightness))

def mount_sd():
    uos.sdconfig(uos.SDMODE_SPI,clk=18,mosi=23,miso=19,cs=4, maxspeed=16)
    try:
        uos.mountsd()
    except:
        pass


def log(thing):
    # print to console for live logging over usb serial
    print('{} - {}'.format(current_time(), thing))
    if config:
        if config['settings']['logging']:
            try:
                with open('/sd/log.txt', 'a+') as f: 
                    if current_time().startswith('1999'):
                        f.write('{}\n'.format(thing))
                    else:
                        f.write('{} - {}\n'.format(current_time(), thing))

            except Exception as e:
                print('ERROR writing log file: {}'.format(e))
                mount_sd()


def current_time(gmt_offset=-5):
    # get current local time in for mqtt time stamp
    (year, month, day, hours, minutes, seconds, weekday, yearday) = utime.localtime(
        utime.mktime(utime.localtime()) + gmt_offset*3600)

    return '{}-{:0>2}-{:0>2}T{:0>2}:{:0>2}:{:0>2}{:0>2}:00'.format(
        year, month, day, hours, minutes, seconds, gmt_offset)


def restart_and_reconnect(sec=10):
    log('Restarting device in {} sec...'.format(sec))
    utime.sleep(sec)
    machine.reset()


def connect_wifi():
    # setup WiFi network
    station = network.WLAN(network.STA_IF)
    station.active(True)
    station.connect(config['settings']['wifi_ssid'], config['settings']['wifi_pass'])
    
    log('connecting to wifi, restarting...')
    
    r = 1
    while not station.isconnected():
        time.sleep_ms(500)
        r = r + 1
        # status dots
        print('.', end=' ')
        if r > 30:
            print('')
            log('cannot connect to wifi, restarting...')
            machine.reset()

    print('')
    log('WiFi connection successful')
    log(station.ifconfig())


def network_setup():
    # setup WiFi network
    connect_wifi()

    # sync clock with ntp
    try:
        ntptime.settime()
        log('Clock synced with ntp server')
    except Exception as e:
        log('Error during clock sync: {}'.format(e))
        restart_and_reconnect(1)

    # setup mqtt
    try:
        mqtt_connect()
        publish_config_mqtt()
    except OSError as e:
        log('Error creating MQTT client: {}'.format(e))
        restart_and_reconnect()


##################################
###
###     HOME ASSISTANT INTEGRATION FUNCTIONS
###
##################################


def create_ha_configs():
    # create home assistant mqtt config topics from config data
    # and publush them to mqtt
    i = 0
    for sensor, values in config['sensors'].items():
        # create/update home assistant occupancy config topics
        publish_ha_config(
            name='{} Bed Occupancy'.format(sensor),
            state_topic="sleep2mqtt/{} Bed Occupancy".format(sensor),
            number=i,
            device_class='occupancy',
            model='bed pressure',
            payload_on=True
            )

        # increment device number
        i+=1

        # create/update home assistant pressure config topics
        publish_ha_config(
            name='{} Bed Pressure'.format(sensor),
            state_topic="sleep2mqtt/{} Bed Occupancy".format(sensor),
            number=i,
            # there is no 0-100 value device class in HA for pressure. while not ideal, humidity works
            device_class='humidity',
            model='bed pressure',
            template='pressure'
            )

        # increment device number
        i+=1    


def publish_ha_config(name, state_topic, number, device_class, model, template=None, payload_on=None):
    # create mqtt config topics for "homeassistant/" topic
    # run this function this after creating the "sleep2mqtt/" topics
    if payload_on is not None:
        sensor_type = "binary_sensor"
    else:
        sensor_type = "sensor"

    template_format = '{{ value_json.device_class }}'

    if template is None:
        value_template = template_format.replace('device_class', device_class)
    else:
        value_template = template_format.replace('device_class', template)

    topic = "homeassistant/{}/sleep2mqtt_{}_{}/{}/config".format(
        sensor_type, config['settings']['mqtt_clientid'], number, device_class)

    ha_conf = {
        "device_class": device_class,
        "device": {
            "manufacturer": "sleep2mqtt",
            "identifiers": ["sleep2mqtt_{}_{}".format(config['settings']['mqtt_clientid'], number)],
            "name": name,
            "model": model
        },
        "name": name,
        "unique_id": "{}_{}_{}_sleep2mqtt".format(config['settings']['mqtt_clientid'], number, device_class),
        "json_attributes_topic": state_topic,
        "state_topic": state_topic,
        "value_template": value_template
    }
    
    if payload_on is not None:
        ha_conf['payload_on'] = payload_on
        ha_conf['payload_off'] = not payload_on
    else:
        ha_conf["unit_of_measurement"] = "%"

    publish_mqtt(message=ha_conf, topic=topic)


##################################
###
###     MQTT INTEGRATION FUNCTIONS
###
##################################


def mqtt_callback(top, msg):
    # call back function for receiving messages on subscribed topics
    #
    #   examples for sending commands to topic sleep2mqtt/control:
    #       {"command": "reset", "sensor_name": "Dan Bed Occupancy"}
    #       {"command": "calibrate", "sensor_name": "Dan Bed Occupancy"}
    #       {"command": "ideal_pressure", "sensor_name": "Dan Bed Occupancy", "value": 42}
    #       {"command": "settings", "variable": "max_drift", "value": 6}
    #       {"command": "air_exchange", "variable": "cycles", "value": 3}
    #   
    global config

    topic = top.decode()
    try:
        message = json.loads(msg.decode())
    except:
        log('mqtt message not json, reading string')
        message = msg.decode()

    log('mqtt callback topic: {}, message: {}'.format(topic, message))
    if topic == "sleep2mqtt/control":
        try:
            if message['command'] == 'reset':
                for sensor in BedSensor.sensors():
                    if sensor.name == message['sensor_name']:
                        sensor.reset()
                        update_mqtt_attributes(sensor)

            if message['command'] == 'ideal_pressure':
                for sensor in BedSensor.sensors():
                    if sensor.name == message['sensor_name']:
                        # update sensor
                        sensor.ideal_pressure = message['value']
                        # save to config file
                        name = sensor.name.split(' ')[0]
                        config['sensors'][name]['ideal_pressure'] = message['value']
                        save_config()

            if message['command'] == 'delta':
                for sensor in BedSensor.sensors():
                    if sensor.name == message['sensor_name']:
                        # update sensor
                        sensor.delta = message['value']
                        # save to config file
                        name = sensor.name.split(' ')[0]
                        config['sensors'][name]['delta'] = message['value']
                        save_config()

            if message['command'] == 'settings':
                try:
                    config['settings'][message['setting']] = message['value']
                    save_config()
                    machine.reset()
                except Exception as e:
                    log('error ({}) setting config with: {}'.format(e, message))

        except Exception as e:
            log('message "{}" not recognized: {}'.format(message, e))

    if topic == "hass/status":
        # when home assistant reboots, push latest data to mqtt
        log('homeassistant online, publishing configs')
        create_ha_configs()
        for sensor in BedSensor.sensors():
            update_sensor(sensor, push=True)


def publish_config_mqtt():
    # copy config and remove passwords first
    message = deepcopy(config)
    message['published'] = current_time()
    for item, value in message['settings'].items():
        if item.endswith('_pass'):
            message['settings'][item] = '***'
    # publish to config topic
    publish_mqtt(message, topic='sleep2mqtt/config')


def check_mqtt():
    # check for new messages to any subscribed topics, new messages to go callback
    for retry in range(10):
        try:
            client.check_msg()
            return
        except OSError as e:
            log("Error checking MQTT messages: {}".format(e))
            utime.sleep(fib(retry))

    # should only get here after 10 failed backed off retries
    restart_and_reconnect()


def mqtt_connect():
    global client

    client = MQTTClient(
        config['settings']['mqtt_clientid'].encode(),
        config['settings']['mqtt_server'],
        1883,
        config['settings']['mqtt_user'].encode(),
        config['settings']['mqtt_pass'].encode())
    
    client.set_callback(mqtt_callback)
    client.connect()
    client.subscribe('sleep2mqtt/control'.encode())
    client.subscribe('hass/status'.encode())


def publish_mqtt(message, sensor=None, topic=None, raw=False):
    if topic is None:
        topic = "sleep2mqtt/{}".format(sensor.name)
    # retry logic range() times with fib backoff
    if raw:
        msg = message
    else:
        msg = json.dumps(message)

    for retry in range(2):
        try:
            client.publish(topic.encode(), msg.encode(), retain=True)
            if sensor is not None:
                sensor.timestamp(True)
            return True
        except Exception as e:
            log('Exception trying to publish update: {}'.format(e))
            utime.sleep(2)

    # only get here after retries
    # attempt to disconnect and reconnect to MQTT
    for retry in range(2):
        try:
            mqtt_connect()
            success = publish_mqtt(message, sensor, topic)
            if success:
                log('Successful reconnecting to MQTT')
                return True
            else:
                log('NOT successful reconnecting to MQTT')
                network_setup()
        except Exception as e:
            log('Exception trying reconnect to mqtt: {}'.format(e))
            utime.sleep(fib(retry))

    # if all else fails
    log('Failed attempts to reconnect, rebooting...')
    restart_and_reconnect()


def update_mqtt_attributes(sensor):
    message = {
        "occupancy": sensor.state(),
        "pressure": "{:0.2f}".format(sensor.value),
        "avg_off": "{:0.2f}".format(sensor.history["off_avg"]),
        "avg_on": "{:0.2f}".format(sensor.history["on_avg"]),
        "ideal_pressure": sensor.ideal_pressure,
        "delta": sensor.delta,
        "last_seen": current_time()
        }
    result = publish_mqtt(message, sensor=sensor)
    return result


##################################
###
###     MAIN LOGIC LOOP FUNCTIONS
###     note to self... be very careful with below here
###
##################################


def update_screen():
    # update screen with useful data
    if brightness > 0:
        i = 50 # increment
        h = 20 # line height
        for sensor in BedSensor.sensors():
            # sensor name label
            M5TextBox(0, i, "{}:".format(
                sensor.name.split(' ')[0]),
                lcd.FONT_DejaVu18,
                0xFFFFFF,
                rotate=0)

            # move down 1 line
            i = i+h
            # erase previous pressure value
            M5Rect(0, i, 320, h, 0x000000, 0x000000)
            # format and write pressure reading
            line = '  p: {}%'.format(sensor.p_value)
            M5TextBox(0, i, line, lcd.FONT_DejaVu18,0xFFFFFF, rotate=0)
            # move down 1 line
            i = i+h
            # erase previous occupancy
            M5Rect(0, i, 320, h, 0x000000, 0x000000)
            # format and write occupancy value
            if sensor.state():
                M5TextBox(0, i, '  occupied', lcd.FONT_DejaVu18,0xFFFFFF, rotate=0)
            else:
                M5TextBox(0, i, '  vacant', lcd.FONT_DejaVu18,0xFFFFFF, rotate=0)
            # move to next line
            i = i+h
            # erase previous data
            M5Rect(0, i, 320, h, 0x000000, 0x000000)
            M5TextBox(0, i, '  on: {:0.2f} / off: {:0.2f}'.format(
                sensor.history['on_avg'],
                sensor.history['off_avg']),
                lcd.FONT_DejaVu18,0xFFFFFF,
                rotate=0)
            i = i+h


def update_sensor(sensor, push=False):
    # the sensor.read() method determines state and stores sensor values
    state_changed = sensor.read()

    # always push to mqtt if it's been more than 30s
    if utime.time() - sensor.timestamp() > 30:
        push = True

    # update mqtt state/attributes topic for HA
    if state_changed or push:
        update_mqtt_attributes(sensor)


def bed_sensor_loop():
    for sensor in BedSensor.sensors():
        update_sensor(sensor, push=True)

    log('Running infinite sensor loop')

    while True:
        for sensor in BedSensor.sensors():
            update_sensor(sensor)

        if config['settings']['m5stack']:
            update_screen()

        # look for control topic messages
        check_mqtt()

        # garbage collect
        gc.collect()
        gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())

        # take a nap, you worked hard
        utime.sleep(1)


def main():

    print('booting sleep2mqtt bed sensor')
    global config
    global config_file
    global client

    # global config vars
    config_file='/sd/config.json'
    config = {}

    # global mqtt client object
    client = None

    # for config, state, and data logging
    mount_sd()

    # load configuration from SD card into global config dictonary
    load_config()

    # wifi setup and creation of mqtt client
    network_setup()

    # draw sleep2mqtt header to screen
    if config['settings']['m5stack']:    
        # from m5ui import M5TextBox, M5Rect, btnA, btnB, btnC,lcd
        from m5ui import *

        global brightness
        brightness = config['settings']['brightness']
        lcd.setBrightness(brightness)
        
        lcd.setRotation(1)
        
        M5TextBox(0, 02, 'sleep2mqtt by hobbysprawl', lcd.FONT_DejaVu18,0xFFFFFF, rotate=0)
        M5TextBox(0, 22, 'Adaptive Bed Sensor', lcd.FONT_DejaVu18,0xFFFFFF, rotate=0)
        
        for i in range(42, 46):
            lcd.drawLine(0,i,320,i)
        
        # register button callbacks
        btnA.wasPressed(buttonA_wasPressed)
        btnB.wasPressed(buttonB_wasPressed)
        btnC.wasPressed(buttonC_wasPressed)

    # create bed sensors from config
    for sensor, value in config['sensors'].items():
        s = BedSensor(
            name = '{} Bed Occupancy'.format(sensor),
            pin = value['pin'],
            ideal_pressure= value['ideal_pressure'],
            delta = value['delta'])

    # set sensitivity, 1-10 to trigger state change
    BedSensor.set_sensitivity(config['settings']['state_sensitivity'])

    # create and publish home assistant configs to mqtt
    create_ha_configs()

    # subscribe to control topic
    try:
        client.subscribe('sleep2mqtt/control'.encode())
    except Exception as e:
        log('error subscribing to mqtt: {}'.format(e))
        restart_and_reconnect()

    # run the infinite bed controller loop
    bed_sensor_loop()


if __name__ == '__main__':
    main()

