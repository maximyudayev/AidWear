from streams.Stream import Stream


###############################################
###############################################
# A structure to store Cyberleg FSM state data.
###############################################
###############################################
class CyberlegStream(Stream):
  def __init__(self, 
               sampling_rate_hz: int = 0,
               **_) -> None:
    super().__init__()

    self.add_stream(device_name='cyberleg-data',
                    stream_name='activity',
                    data_type='float32',
                    sample_size=[1],
                    sampling_rate_hz=sampling_rate_hz,
                    is_measure_rate_hz=True)
    self.add_stream(device_name='cyberleg-data',
                    stream_name='timestamp',
                    data_type='float32',
                    sample_size=[1],
                    sampling_rate_hz=sampling_rate_hz)


  def get_fps(self) -> dict[str, float]:
    return {'cyberleg-data': super()._get_fps('cyberleg-data', 'activity')}


  def get_default_visualization_options(self) -> dict:
    return super().get_default_visualization_options()
