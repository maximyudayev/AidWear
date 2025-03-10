from nodes.producers.Producer import Producer
from streams import ViconStream
from handlers.vicon_dssdk import ViconDataStream
from utils.print_utils import *
from utils.zmq_utils import *


###############################################
###############################################
# A class for streaming data from Vicon system.
###############################################
###############################################
class ViconStreamer(Producer):
  @classmethod
  def _log_source_tag(cls) -> str:
    return 'vicon'


  def __init__(self,
               logging_spec: dict,
               port_pub: str = PORT_BACKEND,
               port_sync: str = PORT_SYNC,
               port_killsig: str = PORT_KILL,
               print_status: bool = True, 
               print_debug: bool = False):

    stream_info = {
    }

    super().__init__(stream_info=stream_info,
                     logging_spec=logging_spec,
                     port_pub=port_pub,
                     port_sync=port_sync,
                     port_killsig=port_killsig,
                     print_status=print_status, 
                     print_debug=print_debug)


  def create_stream(cls, stream_info: dict) -> ViconStream:  
    return ViconStream(**stream_info)


  def _ping_device(self) -> None:
    return None


  def _connect(self) -> bool:
    self._client = ViconDataStream.Client()
    print('Connecting')
    while not self._client.IsConnected():
      self._client.Connect('%s:%s'%(DNS_LOCALHOST, PORT_VICON))

    # Check setting the buffer size works
    self._client.SetBufferSize(1)

    # Enable all the data types
    self._client.EnableSegmentData()
    self._client.EnableMarkerData()
    self._client.EnableUnlabeledMarkerData()
    self._client.EnableMarkerRayData()
    self._client.EnableDeviceData()
    self._client.EnableCentroidData()

    # Set server push mode,
    #   server pushes frames to client buffer, TCP/IP buffer, then server buffer.
    # Code must keep up to ensure no overflow.
    self._client.SetStreamMode(ViconDataStream.Client.StreamMode.EServerPush)
    print('Get Frame Push', self._client.GetFrame(), self._client.GetFrameNumber())
    
    time.sleep(1) # wait for the setup
    is_has_frame = False
    timeout = 50
    while not is_has_frame:
      print('.')
      try:
        if self._client.GetFrame():
          is_has_frame = True
        timeout -= 1
        if timeout < 0:
          print('Failed to get frame')
          return False
      except ViconDataStream.DataStreamException as e:
        pass
    
    devices = self._client.GetDeviceNames()
    # Keep only EMG. This device was renamed in the Nexus SDK
    self._devices = [d for d in devices if d[0] == "Cometa EMG"]
    return True


  # Acquire data from the sensors until signalled externally to quit
  def _process_data(self) -> None:
    if self._is_continue_capture or self._client.GetFrame():
      time_s = time.time()
      frame_number = self._client.GetFrameNumber()

      for deviceName, deviceType in self._devices:
        # handle Cometa EMG
        deviceOutputDetails = self._client.GetDeviceOutputDetails(deviceName)
        all_results = []
        for outputName, componentName, unit in deviceOutputDetails:
          # NOTE: must set this ID in the Vicon software first.
          if outputName != "EMG Channels": continue # only record EMG
          values, occluded = self._client.GetDeviceOutputValues(deviceName, outputName, componentName)
          all_results.append(values)
          # Store the captured data into the data structure.
        result_array = np.array(all_results)
        for sample in result_array.T:
          tag: str = "%s.data" % self._log_source_tag()
          data = {
            'EMG': sample,
            'mocap': None,
            'frame_number': frame_number,
            'latency': None,
          }
          self._publish(tag=tag, time_s=time_s, data={'vicon-data': data})
    elif not self._is_continue_capture:
      # If triggered to stop and no more available data, send empty 'END' packet and join.
      self._send_end_packet()


  def _stop_new_data(self):
    # Disable all the data types
    self._client.DisableSegmentData()
    self._client.DisableMarkerData()
    self._client.DisableUnlabeledMarkerData()
    self._client.DisableMarkerRayData()
    self._client.DisableDeviceData()
    self._client.DisableCentroidData()


  def _cleanup(self) -> None:
    # Clean up the SDK
    self._client.Disconnect()
    super()._cleanup()
