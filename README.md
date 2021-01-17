# sleep2mqtt - Bed Occupancy Sensor

sleep2mqtt is a prototype occupancy sensor I created for my Sleep Number bed to integrate with Home Assistant. The hardware cost me about $80. It's written in mircopython and runs on ESP32. The sensor pushes instant state changes in addition to pressure data every 30s to MQTT for both sides of the bed. I wrote sleep2mqtt to use a configuration file so it could be easily shared.

Sleep Number beds (and other air beds) generally operate at under 1 psi. The air pressure inside the bed increases when you get in, and it decreases when you get out. That pressure data is used to determine occupancy.

The internal pressure in air beds fluctuate from changes in barometic pressure and temperature. This sensor adapts to those pressure changes by continually adjusting the on and off occupancy pressure thresholds up and down in reaction to those pressure changes. This prevents those fluctuations from triggering stage changes.

The pressure data is not reported as the PSI of the bed. Pressure is reported as the range of the sensor. 100 is the maximum the sensor can read, and 0 is the least. All pressure is reported this way.

## Hardware
- M5Stack [ESP32 Basic Core IoT Development Kit](https://m5stack.com/collections/m5-core/products/basic-core-iot-development-kit?variant=16804801937498)
- 2x MPXV7002DP on breakout boards (analog differential pressure sensors)
- small micro SD card
- silicon tubing and hose barb adapters

### **ESP32**
I got the M5 stack because it has a built-in 320x240 LCD screen, SD card, buttons, and other features are handy when prototyping. It's also in a nice finished case and only costs $28. The 320x240 display shows realtime pressure and occupancy data, and the buttons just adjust brightness. Any ESP32 should work for sleep2mqtt as long as 2 analog pins are available for the sensors and it has an SD card reader, but the display libraries will only work on the M5. If you don't use an M5, that can be disabled in the settings file. 

### **MPXV7002DP**
The MPXV7002DP sensor is the first sensor I tried, and I got lucky that it worked for me. I replaced the Sleep Number padding with 4.5" of memory foam. This adds a lot of firmness to the bed, so my sleep number is only 35. Wired directly to the ESP32, the MPXV7002DP sensors povide a useful range for a 200 pound person with a Sleep Number up to 65. This this was just a prototype for myself, I did not research other possible sensors since this one worked. If you need to read higher pressures you'll need to do some research on a different sensor. The MPXV7002DP has also gone up in price. When I got them they were only $16 each.

### **micro SD card (any size)**
The SD card is needed to store the config file and a small state cache file with a moving average of historical pressure readings. The storage on the SD card does not increase with time.

The pressure in air beds is affected by barometric pressure and temperature. To adapt to those changes, and to prevent tossing and turning from triggering occupancy, this sensor determines state based on a floating average pressure over time, and a deviation from that average. To preserve that data if the ESP32 restarts (network or mqtt errors), it keeps the state and floating averages cached to the SD card in a small json file, updating every 5 minutes.

### **Silicon Tubing and Hose Adapters**

To connect the pressure sensor to the bed, it's easiest to cut the pump hoses and install a Tee on each one. The pressure sensors have tiny hose barbs on them, and the bed pump has 3/8" ID tubing. To get these connected I used two 3/8 x 3/8 x 1/8 Barbed Fitting Reducing Tees from here: [CEC36-PR0](https://ark-plas.com/item.php?i=CEC36-PR0). They only sell in bulk, but I ordered "two samples" from that site and paid $7.95 shipping.

### Hardware Assembly

For hadware assembly and wiring, [read here](HARDWARE.md).

## Installation

sleep2mqtt requires 4 micropython libraies (copy, ntptime, simple, types). They have all been copied to this repo in the [micropython_libs](micropython_libs) directory. To reduce memory overhead when importing, you should compile all 4 libraries with [mpy-cross](https://github.com/micropython/micropython/tree/master/mpy-cross). It will create compiled .mpy files that you load instead of the .py files in this repo. I do not compile the sleep2mqtt.py file.

I don't have a lot of experience with ESP32s. This was my first project with one. M5Stack provides a nice web UI (https://flow.m5stack.com) for loading python to the chip and testing testing your code. That is how I initially loaded the program the first time I made it. Now I save the code to the M5Stack using the [M5Stack VS Code python extension](https://marketplace.visualstudio.com/items?itemName=curdeveryday.vscode-m5stack-mpy). There are many tools and guides out there to get this done.

sleep2mqtt requires the [config.json](config.json) file to be loaded to the root of an SD card. Editing this file is covered below.

sleep2mqtt prints messages the serial interface that may be helpful if you are having issues.

## Configuration

At boot, the sensor is configured by reading the [config.json](config.json) file that gets loaded onto the SD card. After that, most of the settings can be changed remotely through mqtt. Any settings changes made via mqtt will get written back to the config file. 

```
{
  "settings": {
    "wifi_ssid": "my-network",
    "wifi_pass": "my-network-password",
    "mqtt_server": "0.0.0.0",
    "mqtt_user": "mqtt_user",
    "mqtt_pass": "mqtt_pass",
    "mqtt_clientid": "bed001",
    "log": true,
    "m5stack": true,
    "brightness": 10
    "state_sensitivity": 2,
  },
  "sensors": {
    "Bert" : {
      "pin": 36,
      "ideal_pressure": 70,
      "occupancy_delta": 30
    },
    "Ernie": {
      "pin": 35,
      "ideal_pressure": 65,
      "occupancy_delta": 25,
    }
  }
}
```

The wifi and mqtt settings should be self-explanitory. The `mqtt_clientid` can be anything, as long as it's unique on the mqtt broker.

`log`: [`true|false`] whether or not to log messages to the SD card

`m5stack`: [`true|false`] if you are running this on the M5Stack, it'll display live pressure data on the screen. This code imports M5's m5ui library for the display code, so set to `false` if not on an M5Stack and it won't try to load the library.

`brightness`: [`0-100`] brightness can be turned up and down with the buttons on the front of the M5.

`state_sensitivity`: [`1-10`] sets how sensitive the sensor is to state change. 1 is least sensitive, 10 is most sensitive. This is further explained in the `delta` setting below. I recommend starting with the default value of 2.

`sensors`: has the configuration for 1 or 2 sensors. The name of each sensor (i.e. Bert/Ernie in the config example) will be used in the naming of the sensors in Home Assistant. The friendly name for each of those sensors in Home Assistant would be `Bert Bed Occupancy` and `Ernie Bed Occupancy`.

`pin`: [`integer`] the analog pin on the ESP32 where the sensor is connected.

`ideal_pressure`: [`0-100`] the pressure value that's reported when your bed is adjusted to it's Sleep Number and it's occupied. To determine this value, adjust your bed to it's Sleep Number when you are laying in it. This value is combind with the `delta` below to determine occupancy. If you change your sleep number, you should to update this value.

`delta`: [`0-100`] the difference in pressure between occupied and not occupied. To caclulate this value: after you determine the `ideal_pressure` setting above, get out of bed, wait a minute or two, and note the pressure reading. Calculate the difference from ideal_pressure to get the `delta` value. Example:  If you got a reading of 65 when occupied, and 35 when not occupied, then your delta is 30. This `delta` correlates to the weight of the person, so cacluate each `delta` with the corret person on the correct side of the bed. The `delta` is weighed with `state_sensitivity` to determine occupancy. If a change in pressure occurs that is greater than (`state_sensitivity` / `10`) x `delta`, then a state change is triggered. This allows you to tune the sensivity to your liking.

## MQTT State topics 

sleep2mqtt publishes state to an MQTT topic with the friendly sensor name. Using the Bert example, the state topic would be:
```
sleep2mqtt/Bert Bed Occupancy
```
The state is published in json with the following structure:

``` javascript
{
  "delta": 22,
  "last_seen": "2020-12-20T22:47:02-5:00",
  "avg_off": "55.23",
  "occupancy": false,
  "pressure": "55.22",
  "ideal_pressure": 70,
  "avg_on": "77.23"
}
```
State in Home Assistant is determined by `occupancy` being `true` or `false`. The `delta` and `ideal_pressure` values are your current settings for that sensor. The `pressure` value is the current pressure reading of the sensor (within 30s). The `avg_on` and `avg_off` values are informational. They are what the sensor has adapted the pressure values to for the bed being occupied or not. In the example above, if you figured out your `ideal_pressure` was 70, then this bed is slightly over inflated. It knows that it's not occupied and the current pressure is 55. If you put in a `delta` of 22, then it knows that on should be around 77.

sleep2mqtt also pushes Home Assistant discovery topics to the `homeassistant/sensor/sleep2mqtt_name_1` topic, where `name` is the `mqtt_clientid` in your config file and the number is 1 for one sensor and 2 for the other. These messages are discoverd by Home Assistant, and the sensors will show up under the MQTT integration in HA.
The only quirk with the Home Assistant integration is they come in as Humidity sensors. I needed a 0-100% sensor type, and humidity worked. So, the pressure data shows up with humidity icon by default. They don't have a sensor type for this project.

## Sensor config via MQTT

One sleep2mqtt is connected to MQTT and working, it publishes the config file contents to topic below, masking out passwords to keep them private. This lets you see your current settings without physically accessing the device.
```
sleep2mqtt/config
```
You can change any config value in `settings` by sending a json message formatted with following json structure example to the `sleep2mqtt/control` MQTT topic:
```javascript
{"command": "settings", "setting": "state_sensitivity", "value": 6}
```
I would strongly recommend against sending password changes this way. If you need to change your passwords, remove the SD card and edit the file.

You can also change the `ideal_pressure` and `delta` values, or reset the sensor for each side of the bed through MQTT by sending a json message formatted with json structure example below to the `sleep2mqtt/control` MQTT topic. Below are examples of all 3. Notice that you need to use the friendly sensor name in the json message.
```javascript
{"command": "ideal_pressure", "sensor_name": "Bert Bed Occupancy", "value": 42}
{"command": "delta", "sensor_name": "Bert Bed Occupancy", "value": 42}
{"command": "reset", "sensor_name": "Bert Bed Occupancy"}
```
Resetting the sensor will clear the adaptive sensor data (the `avg_on` and `avg_off` data) and then have it recheck for occupancy. Sometimes this needs to be done after you recylce the air in the bed (or perhaps engage in some extra curricular activity). Slowly running the pressure up with the pump, then draining it back out again can sometimes confuse the sensor.
