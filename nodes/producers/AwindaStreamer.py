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

from nodes.producers.Producer import Producer
from streams import AwindaStream

from handlers.XsensHandler import XsensFacade
from utils.zmq_utils import *

import numpy as np
import time
from collections import OrderedDict


########################################
########################################
# A class for streaming Awinda IMU data.
########################################
########################################
class AwindaStreamer(Producer):
  @classmethod
  def _log_source_tag(cls) -> str:
    return 'awinda'


  def __init__(self, 
               logging_spec: dict,
               device_mapping: dict[str, str],
               sampling_rate_hz: int = 100,
               num_joints: int = 7,
               radio_channel: int = 15,
               port_pub: str = PORT_BACKEND,
               port_sync: str = PORT_SYNC,
               port_killsig: str = PORT_KILL,
               transmit_delay_sample_period_s: float = None,
               print_status: bool = True, 
               print_debug: bool = False,
               **_):

    self._num_joints = num_joints
    self._sampling_rate_hz = sampling_rate_hz
    self._radio_channel = radio_channel
    self._device_mapping = device_mapping

    joint_names, device_ids = tuple(zip(*(self._device_mapping.items())))
    self._packet = OrderedDict(zip(device_ids, [{}]*len(joint_names)))

    stream_info = {
      "num_joints": self._num_joints,
      "sampling_rate_hz": self._sampling_rate_hz,
      "device_mapping": self._device_mapping
    }

    super().__init__(stream_info=stream_info,
                     logging_spec=logging_spec,
                     port_pub=port_pub,
                     port_sync=port_sync,
                     port_killsig=port_killsig,
                     transmit_delay_sample_period_s=transmit_delay_sample_period_s,
                     print_status=print_status, 
                     print_debug=print_debug)


  def create_stream(self, stream_info: dict) -> AwindaStream:  
    return AwindaStream(**stream_info)


  def _ping_device(self) -> None:
    return None


  def _connect(self) -> bool:
    self._handler = XsensFacade(device_mapping=self._device_mapping,
                                radio_channel=self._radio_channel,
                                sampling_rate_hz=self._sampling_rate_hz)
    # Keep reconnecting until success
    while not self._handler.initialize(): 
      self._handler.cleanup()
    return True


  def _process_data(self) -> None:
    process_time_s = time.time()
    snapshot = self._handler.get_snapshot()
    if snapshot:
      for device, packet in snapshot.items():
        if packet:
          acc = packet["acc"]
          gyr = packet["gyr"]
          mag = packet["mag"]
          quaternion = packet["quaternion"]
          timestamp_fine: np.uint32 = packet["timestamp_fine"]
          counter: np.uint16 = packet["counter"]
          toa_s: float = packet["toa_s"] # TODO: use the average clock of the valid samples in a snapshot
        else:
          acc = (None, None, None)
          gyr = (None, None, None)
          mag = (None, None, None)
          quaternion = (None, None, None, None)
          timestamp_fine = 0
          counter = 0

        self._packet[device] = {
          "acceleration": acc,
          "gyroscope": gyr,
          "magnetometer": mag,
          "orientation": quaternion,
          "timestamp": timestamp_fine,
          "counter": counter,
        }

      acceleration = np.array([v['acceleration'] for v in self._packet.values()])
      gyroscope = np.array([v['gyroscope'] for v in self._packet.values()])
      magnetometer = np.array([v['magnetometer'] for v in self._packet.values()])
      orientation = np.array([v['orientation'] for v in self._packet.values()])
      timestamp = np.array([v['timestamp'] for v in self._packet.values()])
      counter = np.array([v['counter'] for v in self._packet.values()])

      data = {
        'acceleration-x': acceleration[:,0],
        'acceleration-y': acceleration[:,1],
        'acceleration-z': acceleration[:,2],
        'gyroscope-x': gyroscope[:,0],
        'gyroscope-y': gyroscope[:,1],
        'gyroscope-z': gyroscope[:,2],
        'magnetometer-x': magnetometer[:,0],
        'magnetometer-y': magnetometer[:,1],
        'magnetometer-z': magnetometer[:,2],
        'orientation': orientation,
        'timestamp': timestamp,
        'counter': counter
      }

      tag: str = "%s.data" % self._log_source_tag()
      self._publish(tag, time_s=process_time_s, data={'awinda-imu': data})
    elif not self._is_continue_capture:
      # If triggered to stop and no more available data, send empty 'END' packet and join.
      self._send_end_packet()


  def _stop_new_data(self):
    self._handler.cleanup()


  def _cleanup(self) -> None:
    super()._cleanup()
