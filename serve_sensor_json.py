# TODO: air quality estimate evidently requires persisting state, perhaps
#  staying connected to the sensor? Dunno if it does it on-board. When I
#  restarted the script it had been low quality but went back to not ready.

# TODO: twisted serve JSON
import Raspberry_Pi.sensor_functions as functions
import Raspberry_Pi.sensor_constants as constants
from twisted.web import server, resource
from twisted.internet import reactor, endpoints
import json
import time
import threading
import signal

# TODO: Accept particle sensor and cycle period with argparse?
# Which particle sensor, if any, is attached
# (PARTICLE_SENSOR_X with X = PPD42, SDS011, or OFF)
particleSensor = constants.PARTICLE_SENSOR_OFF

# How often to read data (every 3, 100, 300 seconds)
cycle_period = constants.CYCLE_PERIOD_3_S


def read_sensor(current_data: dict, data_lock: threading.Lock, quit_event: threading.Event):
    # Set up the GPIO and I2C communications bus
    gpio, i2c = functions.SensorHardwareSetup()

    # Apply the chosen particle sensor and cycle period settings.
    if particleSensor != constants.PARTICLE_SENSOR_OFF:
        i2c.write_i2c_block_data(functions.i2c_7bit_address,
                                 constants.PARTICLE_SENSOR_SELECT_REG,
                                 [particleSensor])

    i2c.write_i2c_block_data(functions.i2c_7bit_address,
                             constants.CYCLE_TIME_PERIOD_REG,
                             [cycle_period])

    print("Sensor thread entering cycle mode and waiting for data.")
    i2c.write_byte(functions.i2c_7bit_address, constants.CYCLE_MODE_CMD)

    # TODO: How to ensure this thread can exit gracefully?
    while not quit_event.is_set():
        # Wait for the next new data release, indicated by a falling edge on
        # READY.
        while not gpio.event_detected(functions.READY_pin):
            # TODO: Is it safe for this time to be 1 instead of 0.05 as in cycle_readout?
            time.sleep(1)

        if quit_event.is_set():
            return

        # Air data
        raw_data = i2c.read_i2c_block_data(functions.i2c_7bit_address,
                                           constants.AIR_DATA_READ,
                                           constants.AIR_DATA_BYTES)
        air_data = functions.extractAirData(raw_data)

        # Air quality data
        # The initial self-calibration of the air quality data may take several
        # minutes to complete. During this time the accuracy parameter is zero
        # and the data values are not valid.
        raw_data = i2c.read_i2c_block_data(functions.i2c_7bit_address,
                                           constants.AIR_QUALITY_DATA_READ,
                                           constants.AIR_QUALITY_DATA_BYTES)
        air_quality_data = functions.extractAirQualityData(raw_data)

        # Light data
        raw_data = i2c.read_i2c_block_data(functions.i2c_7bit_address,
                                           constants.LIGHT_DATA_READ,
                                           constants.LIGHT_DATA_BYTES)
        light_data = functions.extractLightData(raw_data)

        # Sound data
        raw_data = i2c.read_i2c_block_data(functions.i2c_7bit_address,
                                           constants.SOUND_DATA_READ,
                                           constants.SOUND_DATA_BYTES)
        sound_data = functions.extractSoundData(raw_data)

        # Particle data
        # This requires the connection of a particulate sensor (invalid
        # values will be obtained if this sensor is not present).
        # Also note that, due to the low pass filtering used, the
        # particle data become valid after an initial initialization
        # period of approximately one minute.
        if particleSensor != constants.PARTICLE_SENSOR_OFF:
            raw_data = i2c.read_i2c_block_data(functions.i2c_7bit_address,
                                               constants.PARTICLE_DATA_READ,
                                               constants.PARTICLE_DATA_BYTES)
            particle_data = functions.extractParticleData(raw_data, particleSensor)

        result = {
            "air": {
                "temperature_celsius": air_data['T_C'],
                "pressure_pascals": air_data['P_Pa'],
                "humidity_percentage": air_data['H_pc'],
                "gas_sensor_ohms": air_data['G_ohm'],
            },
            "air_quality": {
                "aqi_accuracy": air_quality_data['AQI_accuracy'],
                "aqi_accuracy_str": functions.interpret_AQI_accuracy(
                    air_quality_data['AQI_accuracy']),
            },
            # TODO: add light, sound, and particle data
        }

        # Provide air quality information only when it's available.
        if air_quality_data['AQI_accuracy'] > 0:
            result["air_quality"].update({
                "aqi": air_quality_data['AQI'],
                "aqi_str": functions.interpret_AQI_accuracy(air_quality_data['AQI']),
                "co2_estimate": air_quality_data['CO2e'],
                "equivalent_breath_voc": air_quality_data['bVOC'],
            })

        with data_lock:
            current_data.clear()
            current_data.update(result)


class SensorData(resource.Resource):
    isLeaf = True

    def __init__(self, current_data: dict, data_lock: threading.Lock):
        super().__init__()
        self.current_data = current_data
        self.data_lock = data_lock

    def render_GET(self, request):
        request.setHeader(b"content-type", b"application/JSON")
        with self.data_lock:
            # TODO: return an error if the sensor data hasn't been filled out yet.
            return json.dumps(self.current_data).encode("utf8")


def main():
    current_data = dict()
    data_lock = threading.Lock()
    quit_event = threading.Event()

    def handler(signum, frame):
        print("got SIGINT; quitting")
        quit_event.set()

    signal.signal(signal.SIGINT, handler)

    reactor.callInThread(read_sensor, current_data, data_lock, quit_event)

    endpoints.serverFromString(reactor, "tcp:8080").listen(server.Site(SensorData(
        current_data,
        data_lock,
    )))
    reactor.run()

    quit_event.set()


if __name__ == '__main__':
    main()
