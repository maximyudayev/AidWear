############
#
# Copyright (c) 2024 Maxim Yudayev and KU Leuven eMedia Lab
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Created 2024-2025 for the KU Leuven AidWear, AidFOG, and RevalExo projects
# by Maxim Yudayev [https://yudayev.com].
#
# ############

import queue
from typing import Callable
import movelladot_pc_sdk as mdda
from collections import OrderedDict

from utils.datastructures import CircularBuffer
from utils.user_settings import *
import time


class DotCallback(mdda.XsDotCallback):
  def __init__(self,
               on_advertisement_found: Callable,
               on_packet_received: Callable,
               on_device_disconnected: Callable):
    super().__init__()
    self._on_advertisement_found = on_advertisement_found
    self._on_device_disconnected = on_device_disconnected
    self._on_packet_received = on_packet_received


  def onAdvertisementFound(self, portInfo):
    self._on_advertisement_found(portInfo)


  def onLiveDataAvailable(self, device, packet):
    toa_s: float = time.time()
    self._on_packet_received(toa_s, device, packet)


  def onDeviceStateChanged(self, device, newState, oldState):
    if newState == mdda.XDS_Destructing:
      self._on_device_disconnected(device)


  def onError(self, result, error):
    print(error)


class MovellaFacade:
  def __init__(self, 
               device_mapping: dict[str, str], 
               master_device: str,
               sampling_rate_hz: int,
               is_sync_devices: bool,
               buffer_size: int = 10) -> None:
    self._is_all_discovered_queue = queue.Queue(maxsize=1)
    self._device_mapping = device_mapping
    self._discovered_devices = list()
    self._connected_devices = OrderedDict([(v, None) for v in device_mapping.values()])
    self._buffer = CircularBuffer(size=buffer_size,
                                  keys=device_mapping.values())

    self._master_device_id = device_mapping[master_device]
    self._sampling_rate_hz = sampling_rate_hz
    self._is_sync_devices = is_sync_devices


  def initialize(self) -> bool:
    self._is_measuring = True
    # Create connection manager
    self._manager = mdda.XsDotConnectionManager()
    if self._manager is None:
      return False

    def on_advertisement_found(portInfo) -> None:
      if not portInfo.isBluetooth(): return
      self._discovered_devices.append(portInfo)
      if len(self._discovered_devices) == len(self._device_mapping): self._is_all_discovered_queue.put(True)
      print("discovered %s"%portInfo.bluetoothAddress())

    def on_packet_received(toa_s, device, packet):
      device_id: str = str(device.deviceId())
      acc = packet.calibratedAcceleration()
      gyr = packet.calibratedGyroscopeData()
      mag = packet.calibratedMagneticField()
      quaternion = packet.orientationQuaternion()
      timestamp_fine = packet.sampleTimeFine()
      counter = packet.packetCounter()
      data = {
        "device_id":            device_id,                          # str
        "acc":                  acc,
        "gyr":                  gyr,
        "mag":                  mag,
        "quaternion":           quaternion, 
        "toa_s":                toa_s,                              # float
        "timestamp_fine":       timestamp_fine,                     # uint32
        "counter":              counter,                            # uint16
      }
      self._buffer.plop(key=device_id, data=data, counter=counter)

    def on_device_disconnected(device):
      device_id: str = str(device.deviceId())
      self._connected_devices[device_id] = None

    # Attach callback handler to connection manager
    self._callback = DotCallback(on_advertisement_found=on_advertisement_found,
                                 on_packet_received=on_packet_received,
                                 on_device_disconnected=on_device_disconnected)
    self._manager.addXsDotCallbackHandler(self._callback)

    # Start a scan and wait until we have found all devices
    self._manager.enableDeviceDetection()
    self._is_all_discovered_queue.get()
    self._manager.disableDeviceDetection()

    for portInfo in self._discovered_devices:
      if not self._manager.openPort(portInfo): 
        print("failed to connect to %s"%portInfo.bluetoothAddress())
        return False
      device = self._manager.device(portInfo.deviceId())
      device_id: str = str(portInfo.deviceId())
      self._connected_devices[device_id] = device

    # Make sure all connected devices have the same filter profile and output rate
    for device_id, device in self._connected_devices.items():
      # NOTE: getAvailableFilterProfiles suggests different low-pass setup for different activities:
      #         'General' - general human daily activities.
      #         'Dynamic' - high-pace activities (e.g. sprints).
      if not device.setOnboardFilterProfile("General"):
        return False
      if not device.setOutputRate(self._sampling_rate_hz):
        return False

    # Call facade sync function, not directly the backend manager proxy
    if self._is_sync_devices:
      if not self._sync(attempts=3):
        return False

    # Set dots to streaming mode and break out of the loop if successful
    return self._stream()


  def _sync(self, attempts=1) -> bool:
    # NOTE: Syncing may not work on some devices due to poor BT drivers 
    while attempts > 0:
      if self._manager.startSync(self._connected_devices[self._master_device_id].bluetoothAddress()):
        return True
      else:
        attempts -= 1
        self._manager.stopSync()
    return False


  def _stream(self) -> bool:
    # Start live data output. Make sure root node is last to go to measurement.
    ordered_device_list: list[tuple[str, object]] = [*[(device_id, device) for device_id, device in self._connected_devices.items() 
                                                        if device_id != self._master_device_id], 
                                                    (self._master_device_id, self._connected_devices[self._master_device_id])]

    for (joint, device) in ordered_device_list:
      # XsPayloadMode_CustomMode5         - Quaternion, Acceleration, Angular velocity, Timestamp
      # XsPayloadMode_CustomMode4         - Quaternion, 9DOF IMU data, Status, Timestamp
      # XsPayloadMode_CompleteQuaternion  - Quaternion, Free acceleration, Timestamp
      if not device.startMeasurement(mdda.XsPayloadMode_CustomMode4):
        return False
    # NOTE: orientation reset works only in 'yaw' direction on DOTs -> no reason to use, turn on flat on the table, then attach to body and start program.
    # for (joint, device) in ordered_device_list:
    #   if not device.resetOrientation(mdda.XRM_Heading):
    #     return False
    return True


  def get_snapshot(self) -> dict[str, dict | None] | None:
    return self._buffer.yeet(is_running=self._is_measuring)


  def cleanup(self) -> None:
    for device_id, device in self._connected_devices.items():
      if device is not None:
        device.stopMeasurement()
        self._connected_devices[device_id] = None
    self._discovered_devices = list()
    self._manager.stopSync()
    self._manager.close()
    self._is_measuring = False
