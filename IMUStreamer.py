import sys
from abc import ABC, abstractmethod
import AwindaHelper as AwH
from xdpchandler import *
from time import sleep
from pynput import keyboard as pyKey
import keyboard
import queue
from datetime import datetime
import xsensdeviceapi as xda


class IMUManager(ABC):
    def __init__(self):
        super().__init__()
        self.buffer = queue.Queue(50000)

    def put_in_queue(self, item):
        if self.buffer.full():
            print("IMU data buffer is full, future data will be overwritten...")
            self.buffer.get()  # remove 1 item from queue
        self.buffer.put(item)

    def pop_from_queue(self):
        if self.buffer.empty():
            print("No data has been buffered currently...")
        else:
            return self.buffer.get()

    @abstractmethod
    def init_sensors(self):
        pass

    @abstractmethod
    def start_streaming(self):
        pass

    @abstractmethod
    def stop_streaming(self):
        pass


class DotPacket:
    def __init__(self, sensor_id, timestamp, arrival_time, data):
        self.sensor_id = sensor_id
        self.timestamp = timestamp
        self.arrival_time = arrival_time
        self.data = data


class DotManager(IMUManager):
    """
    Some notes: if the dots are not shutdown properly they stay in sync mode (2 fast green blinks)
    which messes up the timing when rerun. Fix is to rerun and quit a recording with "q".
    To guarantee heading accuracy and, therefore, to avoid drift in orientation data, the
    sensors must be kept still at the beginning of the measurement for 2-3 seconds.
    """

    def __init__(self):
        super().__init__()
        self.xdpcHandler = XdpcHandler()
        if not self.xdpcHandler.initialize():
            self.xdpcHandler.cleanup()
            exit(-1)

    def start_streaming(self):
        # Start live data output. Make sure root node is last to go to measurement.
        print("Putting devices into measurement mode.")
        for device in self.xdpcHandler.connectedDots():
            if not device.startMeasurement(movelladot_pc_sdk.XsPayloadMode_ExtendedEuler):
                print(f"Could not put device into measurement mode. Reason: {device.lastResultText()}")
                continue

        print("Resetting device headings")
        for device in self.xdpcHandler.connectedDots():
            print(f"\nResetting heading for device {device.portInfo().bluetoothAddress()}: ", end="", flush=True)
            if device.resetOrientation(movelladot_pc_sdk.XRM_Heading):
                print("OK", end="", flush=True)
            else:
                print(f"NOK: {device.lastResultText()}", end="", flush=True)
        print("\n", end="", flush=True)

        record_time = 1200

        print(f"\nMain loop. Recording data for {record_time} seconds. Quit recording by pressing 'Q'.")
        print("-----------------------------------------")

        # First printing some headers, so we see which data belongs to which device
        ''''s = ""
        for device in self.xdpcHandler.connectedDots():
            s += f"{device.portInfo().bluetoothAddress():27}"
        print("%s" % s, flush=True)'''

        startTime = movelladot_pc_sdk.XsTimeStamp_nowMs()
        while movelladot_pc_sdk.XsTimeStamp_nowMs() - startTime <= 1000 * record_time:
            if keyboard.is_pressed('q'):
                print('Q pressed, exiting recording loop...')
                self.stop_streaming()
                return

            if self.xdpcHandler.packetsAvailable():
                for device in self.xdpcHandler.connectedDots():
                    # Retrieve a packet
                    packet = self.xdpcHandler.getNextPacket(device.portInfo().bluetoothAddress())
                    if packet.containsOrientation():
                        euler = packet.orientationEuler()
                        self.put_in_queue(DotPacket(device.bluetoothAddress(), packet.sampleTimeFine(), datetime.now(),
                                                    (euler.x(), euler.y(), euler.z(), euler.pitch(), euler.yaw(),
                                                     euler.roll())))

                # print("%s" % s, flush=True)
        self.stop_streaming()

    def stop_streaming(self):
        print("\n-----------------------------------------", end="", flush=True)

        print("\nStopping measurement...")
        for device in self.xdpcHandler.connectedDots():
            if not device.stopMeasurement():
                print("Failed to stop measurement.")

        print("Stopping sync...")
        if not self.manager.stopSync():
            print("Failed to stop sync.")

        print("Closing ports...")
        self.manager.close()

        print("Successful exit.")

    def init_sensors(self):
        output_rate = 20
        self.xdpcHandler.scanForDots()
        if len(self.xdpcHandler.detectedDots()) == 0:
            print("No Movella DOT device(s) found. Aborting.")
            self.xdpcHandler.cleanup()
            exit(-1)

        self.xdpcHandler.connectDots()

        if len(self.xdpcHandler.connectedDots()) == 0:
            print("Could not connect to any Movella DOT device(s). Aborting.")
            self.xdpcHandler.cleanup()
            exit(-1)

        for device in self.xdpcHandler.connectedDots():
            # Make sure all connected devices have the same filter profile and output rate
            if device.setOnboardFilterProfile("General"):
                print("Successfully set profile to General")
            else:
                print("Setting filter profile failed!")

            if device.setOutputRate(output_rate):
                print(f"Successfully set output rate to {output_rate} Hz")
            else:
                print("Setting output rate failed!")

        self.manager = self.xdpcHandler.manager()
        self.deviceList = self.xdpcHandler.connectedDots()

    def sensor_sync(self):
        if len(self.deviceList) == 1:
            print("Only 1 device connected, sync not needed...")
        else:
            print(f"\nStarting sync for connected devices... Root node: {self.deviceList[-1].bluetoothAddress()}")
            print("This takes at least 14 seconds")
            if not self.manager.startSync(self.deviceList[-1].bluetoothAddress()):
                print(f"Could not start sync. Reason: {self.manager.lastResultText()}")
                if self.manager.lastResult() != movelladot_pc_sdk.XRV_SYNC_COULD_NOT_START:
                    print("Sync could not be started. Aborting.")
                    self.xdpcHandler.cleanup()
                    exit(-1)

                # If (some) devices are already in sync mode.Disable sync on all devices first.
                self.manager.stopSync()
                print(f"Retrying start sync after stopping sync")
                if not self.manager.startSync(self.deviceList[-1].bluetoothAddress()):
                    print(f"Could not start sync. Reason: {self.manager.lastResultText()}. Aborting.")
                    self.xdpcHandler.cleanup()
                    exit(-1)


class AwindaManager(IMUManager):
    def __init__(self):
        super().__init__()
        self.desired_update_rate = 75
        self.desired_radio_channel = 15

        self.wireless_master_callback = AwH.WirelessMasterCallback()
        self.mtw_callbacks = []

        print("Constructing XsControl...")
        self.control = xda.XsControl.construct()
        if self.control is None:
            print("Failed to construct XsControl instance.")
            sys.exit(1)

    def init_sensors(self):
        print("Scanning ports...")

        detected_devices = xda.XsScanner_scanPorts()

        print("Finding wireless master...")
        wireless_master_port = next((port for port in detected_devices if port.deviceId().isWirelessMaster()), None)
        if wireless_master_port is None:
            raise RuntimeError("No wireless masters found")

        print(f"Wireless master found @ {wireless_master_port}")

        print("Opening port...")
        if not self.control.openPort(wireless_master_port.portName(), wireless_master_port.baudrate()):
            raise RuntimeError(f"Failed to open port {wireless_master_port}")

        print("Getting XsDevice instance for wireless master...")
        self.wireless_master_device = self.control.device(wireless_master_port.deviceId())
        if self.wireless_master_device is None:
            raise RuntimeError(f"Failed to construct XsDevice instance: {wireless_master_port}")

        print(f"XsDevice instance created @ {self.wireless_master_device}")

        print("Setting config mode...")
        if not self.wireless_master_device.gotoConfig():
            raise RuntimeError(f"Failed to goto config mode: {self.wireless_master_device}")

        print("Attaching callback handler...")
        self.wireless_master_device.addCallbackHandler(self.wireless_master_callback)

        print("Getting the list of the supported update rates...")
        supportUpdateRates = xda.XsDevice.supportedUpdateRates(self.wireless_master_device, xda.XDI_None)

        print("Supported update rates: ", end="")
        for rate in supportUpdateRates:
            print(rate, end=" ")
        print()

        new_update_rate = AwH.find_closest_update_rate(supportUpdateRates, self.desired_update_rate)

        print(f"Setting update rate to {new_update_rate} Hz...")

        if not self.wireless_master_device.setUpdateRate(new_update_rate):
            raise RuntimeError(f"Failed to set update rate: {self.wireless_master_device}")

        print("Disabling radio channel if previously enabled...")

        if self.wireless_master_device.isRadioEnabled():
            if not self.wireless_master_device.disableRadio():
                raise RuntimeError(f"Failed to disable radio channel: {self.wireless_master_device}")

        print(f"Setting radio channel to {self.desired_radio_channel} and enabling radio...")
        if not self.wireless_master_device.enableRadio(self.desired_radio_channel):
            raise RuntimeError(f"Failed to set radio channel: {self.wireless_master_device}")

        print("Waiting for MTW to wirelessly connect...\n")


        wait_for_connections = True
        connected_mtw_count = len(self.wireless_master_callback.getWirelessMTWs())
        while wait_for_connections:
            time.sleep(0.1)
            next_count = len(self.wireless_master_callback.getWirelessMTWs())
            if next_count != connected_mtw_count:
                print(f"Number of connected MTWs: {next_count}. Press Y to stop searching for devices.")
                connected_mtw_count = next_count
            wait_for_connections = not keyboard.is_pressed('y')

    def start_streaming(self):
        print("Starting measurement...")
        if not self.wireless_master_device.gotoMeasurement():
            raise RuntimeError(f"Failed to goto measurement mode: {self.wireless_master_device}")

        print("Getting XsDevice instances for all MTWs...")
        all_device_ids = self.control.deviceIds()
        mtw_device_ids = [device_id for device_id in all_device_ids if device_id.isMtw()]
        mtw_devices = []
        for device_id in mtw_device_ids:
            mtw_device = self.control.device(device_id)
            if mtw_device is not None:
                mtw_devices.append(mtw_device)
            else:
                raise RuntimeError("Failed to create an MTW XsDevice instance")

        print("Attaching callback handlers to MTWs...")
        mtw_callbacks = [AwH.MtwCallback(i, mtw_devices[i]) for i in range(len(mtw_devices))]
        for i in range(len(mtw_devices)):
            mtw_devices[i].addCallbackHandler(mtw_callbacks[i])

        print("Creating a log file...")
        logFileName = "logfile.mtb"
        if self.wireless_master_device.createLogFile(logFileName) != xda.XRV_OK:
            raise RuntimeError("Failed to create a log file. Aborting.")
        else:
            print("Created a log file: %s" % logFileName)

        print("Starting recording...")
        ready_to_record = False

        while not ready_to_record:
            ready_to_record = all([mtw_callbacks[i].dataAvailable() for i in range(len(mtw_callbacks))])
            if not ready_to_record:
                print("Waiting for data available...")
                time.sleep(0.5)
            # optional, enable heading reset before recording data, make sure all sensors have aligned physically the
            # same heading!! else: print("Do heading reset before recording data, make sure all sensors have aligned
            # physically the same heading!!") all([mtw_devices[i].resetOrientation(xda.XRM_Heading) for i in range(
            # len(mtw_callbacks))])

        if not self.wireless_master_device.startRecording():
            raise RuntimeError("Failed to start recording. Aborting.")

        print("\nMain loop. Press any key to quit\n")
        print("Waiting for data available...")

        euler_data = [xda.XsEuler()] * len(mtw_callbacks)
        print_counter = 0
        while True:
            time.sleep(0)
            if keyboard.is_pressed('q'):
                self.stop_streaming()

            new_data_available = False
            for i in range(len(mtw_callbacks)):
                if mtw_callbacks[i].dataAvailable():
                    new_data_available = True
                    packet = mtw_callbacks[i].getOldestPacket()
                    euler_data[i] = packet.orientationEuler()
                    mtw_callbacks[i].deleteOldestPacket()

            if new_data_available:
                # print only 1/x of the data in the screen.
                if print_counter % 1 == 0:
                    for i in range(len(mtw_callbacks)):
                        print(f"[{i}]: ID: {mtw_callbacks[i].device().deviceId()}, "
                              f"Roll: {euler_data[i].x():7.2f}, "
                              f"Pitch: {euler_data[i].y():7.2f}, "
                              f"Yaw: {euler_data[i].z():7.2f}")

                print_counter += 1

    def stop_streaming(self):
        print("Setting config mode...")
        if not self.wireless_master_device.gotoConfig():
            raise RuntimeError(f"Failed to goto config mode: {self.wireless_master_device}")

        print("Disabling radio...")
        if not self.wireless_master_device.disableRadio():
            raise RuntimeError(f"Failed to disable radio: {self.wireless_master_device}")

imu_manager = AwindaManager()
imu_manager.init_sensors()
imu_manager.start_streaming()