from abc import ABC, abstractmethod

import copy
from collections import OrderedDict
import cv2
import numpy as np

from utils.time_utils import *
from utils.dict_utils import *
from utils.print_utils import *


################################################
################################################
# An abstract class to store data of a SensorStreamer.
#   May contain multiple streams from the sensor, such as acceleration/gyroscope from the DOTs.
# Structure of data and streams_info:
#   Dict with device names as keys, each of which maps to:
#     Dict with name of streams, each of which maps to:
#       for data: 'data', 'time_s', 'time_str', and others if desired
#       for streams_info: 'data_type', 'sample_size', 'sampling_rate_hz', 'timesteps_before_solidified', 'extra_data_info'
################################################
################################################
class Streamer(ABC):
  def __init__(self) -> None:
    self._data = OrderedDict()
    self._metadata = OrderedDict()

  # Define all devices/streams at once.
  # @param streams_info should be a dict mapping device names to dicts.
  #   Each nested dict should map a stream name to a dict of stream info.
  #   See add_stream() for information about the parameters that should be contained in each stream info.
  def set_streams_info(self, streams_info: dict[str, dict[str, dict]] | None = None) -> None:
    if streams_info is None:
      self._streams_info = OrderedDict()
      self._data = OrderedDict()
      return
    # Validate the format
    required_info_keys = ['data_type', 'sample_size', 'sampling_rate_hz']
    try:
      for (device_name, device_info) in streams_info.items():
        for (stream_name, stream_info) in device_info.items():
          if False in [info_key in stream_info for info_key in required_info_keys]:
            raise AssertionError('Provided stream information for %s.%s does not contain all required keys' % (device_name, stream_name))
    except AssertionError:
      raise
    except:
      raise ValueError('Provided stream information does not have the correct format')
    # Set the new streams
    for (device_name, device_info) in streams_info.items():
      for (stream_name, stream_info) in device_info.items():
        stream_info.setdefault('timesteps_before_solidified', 0)
        stream_info.setdefault('extra_data_info', {})
        stream_info.setdefault('data_notes', None)
        stream_info.setdefault('is_video', False)
        stream_info.setdefault('is_audio', False)
        if not isinstance(stream_info['sample_size'], (list, tuple)):
          stream_info['sample_size'] = [stream_info['sample_size']]
        self.add_stream(device_name=device_name,
                        stream_name=stream_name,
                        data_type=stream_info['data_type'],
                        sample_size=stream_info['sample_size'],
                        data_notes=stream_info['data_notes'],
                        is_video=stream_info['is_video'],
                        is_audio=stream_info['is_audio'],
                        sampling_rate_hz=stream_info['sampling_rate_hz'],
                        timesteps_before_solidified=stream_info['timesteps_before_solidified'],
                        extra_data_info=stream_info['extra_data_info'])

  # Add a new device stream.
  # @param timesteps_before_solidified allows indication that data/timestamps for
  #   previous timesteps may be altered when a new sample is received.
  #   For example, some sensors may send two samples at the same time, and the
  #     streamer class may infer a timestamp for the previous reading when
  #     a new pair of readings arrive.  A timestamp is thus not 'final' until the next timestep.
  # @param extra_data_info is used to specify additional data keys that will be streamed
  #   along with the default 'data', 'time_s', and 'time_str'.
  #   It should be a dict, where each extra data key maps to a dict with at least 'data_type' and 'sample_size'.
  # @param data_notes can be a string or a dict of relevant info.
  #   If it's a dict, the key 'Data headings' is recommended and will be used by DataLogger for headers.
  #     In that case, 'Data headings' should map to a list of strings of length sample_size.
  def add_stream(self, device_name: str, stream_name: str, data_type: type, 
                 sample_size: list[int] | tuple[int], sampling_rate_hz, 
                 data_notes: str | dict = None, 
                 is_video: bool = False, is_audio: bool = False, 
                 timesteps_before_solidified: int = 0,
                 extra_data_info: dict[str, dict] = None):
    self._streams_info.setdefault(device_name, OrderedDict())
    if not isinstance(sample_size, (list, tuple)):
      sample_size = [sample_size]
    self._streams_info[device_name][stream_name] = OrderedDict([
                                                    ('data_type', data_type),
                                                    ('sample_size', sample_size),
                                                    ('data_notes', data_notes or {}),
                                                    ('sampling_rate_hz', sampling_rate_hz),
                                                    ('is_video', is_video),
                                                    ('is_audio', is_audio),
                                                    ('timesteps_before_solidified', timesteps_before_solidified),
                                                    ('extra_data_info', extra_data_info or {}),
                                                    ])
    self.clear_data(device_name, stream_name)

  # Clear data for a stream (and add the stream if it doesn't exist).
  # Optionally, can clear only data before a specified index.
  def clear_data(self, device_name, stream_name, first_index_to_keep=None):
    # Create the device/stream entry if it doesn't exist
    self._data.setdefault(device_name, OrderedDict())
    self._data[device_name].setdefault(stream_name, OrderedDict())

    # Get the data keys that have been populated so far.
    data_keys = list(self._data[device_name][stream_name].keys())
    # Get the expected data keys in the stream,
    #  in case data hasn't been received for some of them yet.
    data_keys.extend(['time_s', 'time_str', 'data'])
    data_keys.extend(list(self._streams_info[device_name][stream_name]['extra_data_info'].keys()))
    data_keys = list(set(data_keys))
    # Clear data for each known or expected data key.
    for data_key in data_keys:
      if first_index_to_keep is None:
        self._data[device_name][stream_name][data_key] = []
      else:
        self._data[device_name][stream_name][data_key] = \
          self._data[device_name][stream_name][data_key][first_index_to_keep:]

  def clear_data_all(self):
    for (device_name, device_info) in self._streams_info.items():
      for (stream_name, stream_info) in device_info.items():
        self.clear_data(device_name, stream_name)

    # Add a single timestep of data to the data log.
  # @param time_s and @param data should each be a single value.
  # @param extra_data should be a dict mapping each extra data key to a single value.
  #   The extra data keys should match what was specified in add_stream() or set_streams_info().
  def append_data(self, device_name, stream_name, time_s, data, extra_data=None):
    # self._log_debug('Appending data to %s.%s: %f | %s' % (device_name, stream_name, time_s, str(data)))
    with self._data_lock:
      time_str = get_time_str(time_s, '%Y-%m-%d %H:%M:%S.%f')
      self._data[device_name][stream_name]['time_s'].append(time_s)
      self._data[device_name][stream_name]['time_str'].append(time_str)
      self._data[device_name][stream_name]['data'].append(data)
      if extra_data is not None:
        for (extra_key, extra_value) in extra_data.items():
          if extra_key not in self._data[device_name][stream_name]:
            self._log_error('ERROR: Unknown extra data key: %s' % extra_key)
          self._data[device_name][stream_name][extra_key].append(extra_value)

  # Get the number of timesteps recorded so far.
  def get_num_timesteps(self, device_name, stream_name):
    times_s = self._get_times_s(device_name, stream_name)
    try: # If reading from a stored HDF5 file, avoid loading the array into memory
      return times_s.shape[0]
    except:
      return len(times_s)

  # Get the start time of data recorded so far.
  def get_start_time_s(self, device_name, stream_name):
    try:
      start_time_s = self._get_times_s(device_name, stream_name)[0]
      if isinstance(start_time_s, np.ndarray):
        start_time_s = start_time_s[0]
      return start_time_s
    except IndexError:
      return None

  # Get the end time of data recorded so far.
  def get_end_time_s(self, device_name, stream_name):
    try:
      end_time_s = self._get_times_s(device_name, stream_name)[-1]
      if isinstance(end_time_s, np.ndarray):
        end_time_s = end_time_s[0]
      return end_time_s
    except IndexError:
      return None
  
  # Get the duration of data recorded so far.
  def get_duration_s(self, device_name, stream_name):
    start_time_s = self.get_start_time_s(device_name, stream_name)
    end_time_s = self.get_end_time_s(device_name, stream_name)
    if end_time_s is not None and start_time_s is not None:
      return end_time_s - start_time_s
    return None

  # Get data.
  # Can get all data, or only data to/from a specified timestep.
  # @starting_index the desired first index to return, or None to start at the beginning.
  # @ending_index the desired ending index to return, or None to go until the end.
  #   If provided, will be used with standard Python list indexing,
  #    so the provided index will be *excluded* from the returned data.
  #   Note that it can also be negative, in accordance with standard Python list indexing.
  # @starting_time_s and @ending_time_s are alternatives to starting_index and ending_index.
  #   If not None, will be used instead of the corresponding index argument.
  # If no data exists based on the specified timesteps, will return None.
  # @param return_deepcopy Whether to return a deep copy of the data (safer)
  #                         or to potentially include pointers to the data record (faster).
  def get_data(self, device_name, stream_name,
                    starting_index=None, ending_index=None,
                    starting_time_s=None, ending_time_s=None,
                    return_deepcopy=True):
    with self._data_lock:
      # Convert desired times to indexes if applicable.
      if starting_time_s is not None:
        starting_index = self.get_index_for_time_s(device_name, stream_name, starting_time_s, 'after')
        if starting_index is None:
          return None
      if ending_time_s is not None:
        ending_index = self.get_index_for_time_s(device_name, stream_name, ending_time_s, 'before')
        if ending_index is None:
          return None
        else:
          ending_index += 1 # since will use as a list index and thus exclude the specified index
      # Update default starting/ending indexes.
      if starting_index is None:
        starting_index = 0
      if ending_index is None:
        ending_index = self.get_num_timesteps(device_name, stream_name)
      
      # Use streaming logs or existing logs as needed.
      if not self._replaying_data_logs:
        # Get the desired subset of data.
        # Note that this also creates a copy of each array, so the returned data remains static as new data is collected.
        res = dict([(key, values[starting_index:ending_index]) for (key, values) in self._data[device_name][stream_name].items()])
      else:
        # Use existing logs.
        # Create a data array that mimics a streaming log,
        #  but that is only populated with the desired subset of data.
        # print('Getting saved data for', device_name, stream_name, 'indexes', starting_index, ending_index)
        if device_name in self._hdf5_stream_groups and stream_name in self._hdf5_stream_groups[device_name]:
          # Get data from the HDF5 file.
          # print('Getting HDF5 group')
          res = {}
          # Extract the data subset and convert to a list (possibly a list of lists if N-D)
          #  instead of numpy arrays that come from the HDF5.
          for (key, value) in self._hdf5_stream_groups[device_name][stream_name].items():
            res[key] = value[starting_index:ending_index]
            res[key] = [x.squeeze().tolist() for x in res[key]]
            # if not isinstance(res[key], list): # was probably a single element
            #   res[key] = [res[key]]
        else:
          # Get data from the video file.
          # print('Getting video data', starting_index, ending_index)
          res = {'data':[], 'time_s':[], 'time_str':[]}
          video_reader = self._video_readers[device_name][stream_name]
          video_time_s_group = self._video_time_s_stream_groups[device_name][stream_name]
          for index in range(starting_index, ending_index):
            video_reader.set(cv2.CAP_PROP_POS_FRAMES, index)
            success, frame = video_reader.read()
            time_s = video_time_s_group['time_s'][index][0]
            res['data'].append(frame)
            res['time_s'].append(time_s)
            res['time_str'].append(get_time_str(time_s, format='%Y-%m-%d %H:%M:%S.%f'))
        
      # Return the result
      if len(list(res.values())[0]) == 0:
        return None
      elif return_deepcopy:
        return copy.deepcopy(res)
      else:
        return res
        
  # Helper to get the time array for a specified stream.
  # Will use the streaming data or the loaded log data as applicable.
  def _get_times_s(self, device_name, stream_name):
    if not self._replaying_data_logs:
      return self._data[device_name][stream_name]['time_s']
    else:
      try:
        return self._hdf5_stream_groups[device_name][stream_name]['time_s']
      except KeyError:
        return self._video_time_s_stream_groups[device_name][stream_name]['time_s']
    
  # Helper to get the sample index that best corresponds to the specified time.
  # Will return the index of the last sample that is <= the specified time
  #  or the index of the first sample that is >= the specified time.
  def get_index_for_time_s(self, device_name, stream_name, target_time_s, target_type='before'):
    # Get the sample times streamed so far, or loaded from existing logs.
    times_s = np.array(self._get_times_s(device_name, stream_name))
    # Get the last sample before the specified time.
    if 'before' == target_type.lower().strip():
      indexes = np.argwhere(times_s <= target_time_s)
      return np.max(indexes) if indexes.size > 0 else None
    # Get the first sample after the specified time.
    elif 'after' == target_type.lower().strip():
      indexes = np.argwhere(times_s >= target_time_s)
      return np.min(indexes) if indexes.size > 0 else None
    else:
      raise ValueError
    
  # Get/set metadata
  def get_metadata(self, device_name=None, only_str_values=False):
    # Get metadata for all devices or for the specified device.
    if device_name is None:
      metadata = self._metadata
    elif device_name in self._metadata:
      metadata = self._metadata[device_name]
    else:
      metadata = OrderedDict()

    # Add the class name.
    class_name = type(self).__name__
    if device_name is None:
      for device_name_toUpdate in metadata:
        metadata[device_name_toUpdate][SensorStreamer.metadata_class_name_key] = class_name
    else:
      metadata[SensorStreamer.metadata_class_name_key] = class_name

    # Convert values to strings if desired.
    if only_str_values:
      metadata = convert_dict_values_to_str(metadata)
    return metadata

  def set_metadata(self, new_metadata):
    self._metadata = new_metadata

  def get_num_devices(self):
    return len(self._streams_info)

  # Get the names of streaming devices
  def get_device_names(self):
    return list(self._streams_info.keys())

  # Rename a device (and keep the device indexing order the same).
  # If the name already exists, will not do anything.
  def rename_device(self, old_device_name, new_device_name):
    self._log_debug('Renaming device "%s" to "%s"' % (old_device_name, new_device_name))
    with self._data_lock:
      self._streams_info = rename_dict_key(self._streams_info, old_device_name, new_device_name)
      self._metadata = rename_dict_key(self._metadata, old_device_name, new_device_name)
      self._data = rename_dict_key(self._data, old_device_name, new_device_name)

  # Get the names of streams.
  # If device_name is None, will assume streams are the same for every device.
  def get_stream_names(self, device_name=None):
    if device_name is None:
      device_name = self.get_device_names()[0]
    return list(self._streams_info[device_name].keys())

  # Get information about a stream.
  # Returned dict will have keys data_type, sample_size, sampling_rate_hz, timesteps_before_solidified, extra_data_info
  def get_stream_info(self, device_name, stream_name):
    return self._streams_info[device_name][stream_name]

  # Get all information about all streams.
  def get_all_stream_infos(self):
    return copy.deepcopy(self._streams_info)

  # Get a list of values for a particular type of stream info (decoupled from device/stream names).
  # info_key can be data_type, sample_size, sampling_rate_hz, timesteps_before_solidified, extra_data_info
  def get_stream_attribute_allStreams(self, stream_attribute):
    infos = []
    for (device_name, device_info) in self._streams_info.items():
      for (stream_name, stream_info) in device_info.items():
        infos.append(stream_info[stream_attribute])

  # Get a list of data keys associated with a stream.
  # Will include 'data', 'time_s', 'time_str', and any extra ones defined.
  def get_stream_data_keys(self, device_name, stream_name):
    return list(self._data[device_name][stream_name].keys())
  
  ###########################
  ###### VISUALIZATION ######
  ###########################

  # Get how each stream should be visualized.
  # Should be overridden by subclasses if desired.
  # Returns a dictionary mapping [device_name][stream_name] to a dict of visualizer options.
  # See DataVisualizer for options of default visualizers, such as line plots and videos.
  def get_default_visualization_options(self, visualization_options=None):
    # Do not show any visualization by default. Subclasses can override this.
    processed_options = {}
    for (device_name, device_info) in self._streams_info.items():
      processed_options[device_name] = {}
      for (stream_name, stream_info) in device_info.items():
        processed_options[device_name][stream_name] = {
          'class': None
        }
    return processed_options

  # Specify how each stream should be visualized.
  # Should be overridden by subclasses if desired.
  # See DataVisualizer for options of default visualizers, such as line plots and videos.
  def get_visualization_options(self, device_name, stream_name):
    return self._visualization_options[device_name][stream_name]

  # Override default visualization options if desired.
  def set_visualization_options(self, device_name, stream_name, options):
    for (k, v) in options.items():
      self._visualization_options[device_name][stream_name][k] = v