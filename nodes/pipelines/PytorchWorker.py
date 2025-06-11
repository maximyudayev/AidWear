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

from nodes.pipelines.Pipeline import Pipeline
from streams import PytorchStream

from utils.time_utils import get_time
from utils.zmq_utils import *

import numpy as np
import torch
from torch import nn
from collections import deque
from pytorch_tcn import TCN


######################################################
######################################################
# A class for processing sensor data with an AI model.
# TODO: Keep the module fixed, instantiate PyTorch
#       model as an object from user parameters.
######################################################
######################################################
class PytorchWorker(Pipeline):
  @classmethod
  def _log_source_tag(cls) -> str:
    return 'ai'


  def __init__(self,
               host_ip: str,
               model_path: str,
               input_size: tuple[int, int],
               output_classes: list[str],
               sampling_rate_hz: int,
               logging_spec: dict,
               stream_specs: list[dict],
               port_pub: str = PORT_BACKEND,
               port_sub: str = PORT_FRONTEND,
               port_sync: str = PORT_SYNC_HOST,
               port_killsig: str = PORT_KILL,
               **_):
    self._model: nn.Module = TCN(
      num_inputs=30,
      num_channels=[16, 32, 32, 32, 16],
      kernel_size=3,
      dropout=0.1,
      output_projection=2,
      output_activation=None,
      causal=True
    )
    self._model.load_state_dict(torch.load(model_path, map_location='cpu', weights_only=True))
    self._model.eval()
    # to keep the latest valid IMU sample (because at some time frames a single IMU sample can be None).
    self._buffer: list[deque[np.ndarray]] = [deque([np.zeros(input_size[1])], maxlen=1) for _ in range(input_size[0])]
    # Globally turn off gradient calculation. Inference-only mode.
    torch.set_grad_enabled(False)

    # Initialize any state that the sensor needs.
    stream_info = {
      "classes": output_classes,
      "sampling_rate_hz": sampling_rate_hz
    }

    super().__init__(host_ip=host_ip,
                     stream_info=stream_info,
                     logging_spec=logging_spec,
                     stream_specs=stream_specs,
                     port_pub=port_pub,
                     port_sub=port_sub,
                     port_sync=port_sync,
                     port_killsig=port_killsig)


  @classmethod
  def create_stream(cls, stream_info: dict) -> PytorchStream:
    return PytorchStream(**stream_info)


  def _generate_prediction(self) -> tuple[list[float], int]:
    input_tensor = torch.tensor(np.concatenate([buf[0] for buf in self._buffer]), dtype=torch.float32)[None,:,None]
    output = self._model(input_tensor, inference=True)
    return output.squeeze().numpy(), output.squeeze().argmax().item()


  def _process_data(self, topic: str, msg: dict) -> None:
    acc = msg['data']['dots-imu']['acceleration']
    gyr = msg['data']['dots-imu']['gyroscope']
    toa_s = msg['data']['dots-imu']['toa_s']

    for i, sensor_sample in enumerate(np.concatenate((acc, gyr), axis=1)):
      # Replace the circular buffer's values for valid newly arrivied sensor samples.
      #   NOTE: shape (6,), contents may be NaN for missed packets.
      if all(map(lambda el: not np.isnan(el), sensor_sample)):
        self._buffer[i].append(sensor_sample)

    start_time_s: float = get_time()
    logits, prediction = self._generate_prediction()
    end_time_s: float = get_time()

    data = {
      'logits': logits,
      'prediction': prediction,
      'inference_latency_s': end_time_s-start_time_s,
      'delay_since_first_sensor_s': start_time_s-np.min(toa_s),
      'delay_since_snapshot_ready_s': start_time_s-msg['process_time_s']
    }

    tag: str = "%s.data" % self._log_source_tag()
    self._publish(tag, process_time_s=end_time_s, data={'pytorch-worker': data})


  def _stop_new_data(self):
    pass


  def _cleanup(self) -> None:
    super()._cleanup()
