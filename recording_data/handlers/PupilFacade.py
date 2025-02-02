from collections import OrderedDict
import time
from typing import Callable

import cv2
import msgpack
import numpy as np
import zmq

# Most of the startup code adapted from https://docs.pupil-labs.com/developer/core/network-api/#communicating-with-pupil-service
class PupilFacade:
  def __init__(self,
               pupil_capture_ip: str,
               pupil_capture_port: str,
               video_image_format: str,
               gaze_estimate_stale_s: float,
               stream_video_world: bool,
               stream_video_worldGaze: bool,
               stream_video_eye: bool,
               is_binocular: bool) -> None:
    
    self._stream_video_world = stream_video_world
    self._stream_video_worldGaze = stream_video_worldGaze
    self._stream_video_eye = stream_video_eye
    self._is_binocular = is_binocular
    self._pupil_capture_ip = pupil_capture_ip
    self._pupil_capture_port = pupil_capture_port
    self._video_image_format = video_image_format
    self._gaze_estimate_stale_s = gaze_estimate_stale_s

    # Connect to the Pupil Capture socket.
    self._zmq_context = zmq.Context.instance()
    self._zmq_requester: zmq.SyncSocket = self._zmq_context.socket(zmq.REQ)
    self._zmq_requester.RCVTIMEO = 2000 # receive timeout in milliseconds
    self._zmq_requester.connect('tcp://%s:%s' % (self._pupil_capture_ip, self._pupil_capture_port))
    # Get the port that will be used for data.
    self._zmq_requester.send_string('SUB_PORT')
    self._ipc_sub_port = self._zmq_requester.recv_string()

    # Sync the Pupil Core clock with the system clock.
    self._sync_pupil_time()

    # Subscribe to the desired topics.
    topics = ['notify.', 'gaze.3d.%s'%('01.' if self._is_binocular else '0.')]
    if self._stream_video_world or self._stream_video_worldGaze:
      topics.append('frame.world')
    if self._stream_video_eye:
      topics.append('frame.eye.')

    self._receiver: zmq.SyncSocket = self._zmq_context.socket(zmq.SUB)
    self._receiver.connect('tcp://%s:%s' % (self._pupil_capture_ip, self._ipc_sub_port))
    for t in topics: self._receiver.subscribe(t)
    # self._log_debug('Subscribed to eye tracking topics')

  # Receive data and return a parsed dictionary.
  # The data dict will have keys 'gaze', 'pupil', 'video-world', 'video-worldGaze', and 'video-eye'
  #  where each will map to a dict or to None if it was not applicable.
  # The dict keys correspond to device names after the 'eye-tracking-' prefix.
  #   Each sub-dict has keys that are stream names.
  def process_pupil_data(self):
    data = self._receiver.recv_multipart()
    time_s = time.time()
    pupilCore_time_s = self._get_pupil_time()

    gaze_items = None
    pupil_items = None
    video_world_items = None
    video_worldGaze_items = None
    video_eye_items = None
    time_items = [
      ('pupilCore_time_s', pupilCore_time_s)
    ]

    topic = data[0].decode('utf-8')

    # Process gaze/pupil data
    # Note it works for both, mono- and binocular gaze data
    if topic in ['gaze.2d.0.', 'gaze.3d.0.','gaze.2d.01.', 'gaze.3d.01.']: # former two - monocular, latter two - binocular 
      payload = msgpack.loads(data[1])
      pupil_data = payload['base_data'] # pupil detection on which the gaze detection was based (just use the first one for now if there were multiple)
      # Record data common to both 2D and 3D formats
      gaze_items = [
        ('timestamp'  , payload['timestamp']),  # seconds from an arbitrary reference time, but should be synced with the video timestamps
        ('position'   , payload['norm_pos']),   # normalized units [0-1]
        ('confidence' , payload['confidence']), # gaze confidence [0-1]
      ]
      pupil_items = [
        ('timestamp'  , [pupil['timestamp'] for pupil in pupil_data]),  # seconds from an arbitrary reference time, but should be synced with the video timestamps
        ('position'   , [pupil['norm_pos'] for pupil in pupil_data]),   # normalized units [0-1]
        ('confidence' , [pupil['confidence'] for pupil in pupil_data]), # [0-1]
        ('diameter'   , [pupil['diameter'] for pupil in pupil_data]),   # 2D image space, unit: pixel
      ]
      # Add extra data available for 3D formats
      if topic in ['gaze.3d.0.', 'gaze.3d.01.']:
        gaze_items.extend([
          ('normal_3d' , list(payload['gaze_normal%s_3d' % ('s' if self._is_binocular else '')].values())),    # [(x,y,z),]
          ('point_3d'  , payload['gaze_point_3d']),     # x,y,z
          ('eye_center_3d' , list(payload['eye_center%s_3d' % ('s' if self._is_binocular else '')].values())), # [(x,y,z),]
        ])
        pupil_items.extend([
          ('polar_theta' , [pupil['theta'] for pupil in pupil_data]),
          ('polar_phi'   , [pupil['phi'] for pupil in pupil_data]),
          ('circle3d_radius' , [pupil['circle_3d']['radius'] for pupil in pupil_data]), # mm in 3D space
          ('circle3d_center' , [pupil['circle_3d']['center'] for pupil in pupil_data]), # mm in 3D space
          ('circle3d_normal' , [pupil['circle_3d']['normal'] for pupil in pupil_data]), # mm in 3D space
          ('diameter3d'      , [pupil['diameter_3d'] for pupil in pupil_data]), # mm in 3D space
          ('sphere_center' , [pupil['sphere']['center'] for pupil in pupil_data]), # mm in 3D space
          ('sphere_radius' , [pupil['sphere']['radius'] for pupil in pupil_data]), # mm in 3D space
          ('projected_sphere_center' , [pupil['projected_sphere']['center'] for pupil in pupil_data]), # pixels in image space
          ('projected_sphere_axes'   , [pupil['projected_sphere']['axes'] for pupil in pupil_data]),   # pixels in image space
          ('projected_sphere_angle'  , [pupil['projected_sphere']['angle'] for pupil in pupil_data]),
        ])
      # Add extra data available for 2D formats
      else:
        pupil_items.extend([
          ('ellipse_center'   , [pupil['ellipse']['center'] for pupil in pupil_data]), # pixels, in image space
          ('ellipse_axes'     , [pupil['ellipse']['axes'] for pupil in pupil_data]),   # pixels, in image space
          ('ellipse_angle_deg', [pupil['ellipse']['angle'] for pupil in pupil_data]),  # degrees
        ])

    # Process world video data
    elif topic == 'frame.world':
      metadata = msgpack.loads(data[1])
      payload = data[2]
      frame_timestamp = float(metadata['timestamp'])
      img_data = np.frombuffer(payload, dtype=np.uint8)
      if self._video_image_format == 'bgr':
        img = img_data.reshape(metadata['height'], metadata['width'], 3)
      else:
        img_data = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
        img = img_data.reshape(metadata['height'], metadata['width'], 3)

      if self._stream_video_world: # we might be here because we want 'worldGaze' and not 'world'
        video_world_items = [
          ('frame_timestamp', frame_timestamp),
          ('frame_index', metadata['index']), # world view frame index used for annotation
          ('frame', img),
        ]

      # Synthesize a stream with the gaze superimposed on the world video.
      try:
        world_img = img
        gaze_norm_pos = self._get_latest_stream_data_fn(device_name='eye-tracking-gaze', stream_name='position', starting_index=-1)
        gaze_timestamp = self._get_latest_stream_data_fn(device_name='eye-tracking-gaze', stream_name='timestamp', starting_index=-1)
        if gaze_norm_pos is not None and gaze_timestamp is not None:
          gaze_norm_pos = gaze_norm_pos['data'][0]
          gaze_timestamp = gaze_timestamp['data'][0]
          world_gaze_time_diff_s = frame_timestamp - gaze_timestamp
          gaze_radius = 10
          gaze_color_outer = (255, 255, 255) # BGR format
          if abs(world_gaze_time_diff_s) < self._gaze_estimate_stale_s: # check if the gaze prediction is recent
            gaze_color_inner = (0, 255, 0) # BGR format
          else: # gaze prediction is stale
            gaze_color_inner = (0, 0, 0) # BGR format
          gaze_norm_pos = np.array(gaze_norm_pos)
          world_with_gaze = world_img.copy()
          gaze_norm_pos[1] = 1 - gaze_norm_pos[1]
          gaze_norm_pos = tuple((gaze_norm_pos * [world_with_gaze.shape[1], world_with_gaze.shape[0]]).astype(int))
          cv2.circle(world_with_gaze, gaze_norm_pos, gaze_radius, gaze_color_outer, -1, lineType=cv2.LINE_AA)
          cv2.circle(world_with_gaze, gaze_norm_pos, round(gaze_radius*0.7), gaze_color_inner, -1, lineType=cv2.LINE_AA)
          video_worldGaze_items = [
            ('frame_timestamp', frame_timestamp), 
            ('frame_index', metadata['index']), # world view frame index used for annotation
            ('frame', world_with_gaze),
          ]
        else:
          video_worldGaze_items = [
            ('frame_timestamp', frame_timestamp), 
            ('frame_index', metadata['index']), # world view frame index used for annotation
            ('frame', world_img),
          ]
      except KeyError: # Streams haven't been configured yet
        pass
      except IndexError: # Data hasn't been received yet
        pass

    # Process eye video data
    elif topic in ['frame.eye.0', 'frame.eye.1']:
      metadata = msgpack.loads(data[1])
      payload = data[2]
      frame_timestamp = float(metadata['timestamp'])
      img_data = np.frombuffer(payload, dtype=np.uint8)
      if self._video_image_format == 'bgr':
        img = img_data.reshape(metadata['height'], metadata['width'], 3)
      else:
        img_data = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
        img = img_data.reshape(metadata['height'], metadata['width'], 3)
      video_eye_items = [
        ('frame_timestamp', frame_timestamp),
        ('frame_index', metadata['index']), # world view frame index used for annotation
        ('frame', img)
      ]
      eye_id = int(topic.split('.')[2])

    # Create a data dictionary.
    # The keys should correspond to device names after the 'eye-tracking-' prefix.
    data = OrderedDict([
      ('gaze',  OrderedDict(gaze_items) if gaze_items  is not None else None),
      ('pupil', OrderedDict(pupil_items) if pupil_items is not None else None),
      ('video-world', OrderedDict(video_world_items) if video_world_items is not None else None),
      ('video-worldGaze', OrderedDict(video_worldGaze_items) if video_worldGaze_items is not None else None),
      ('video-eye0', OrderedDict(video_eye_items) if video_eye_items is not None and not eye_id else None),
      ('video-eye1', OrderedDict(video_eye_items) if video_eye_items is not None and eye_id else None),
      ('time', OrderedDict(time_items) if time_items is not None else None),
    ])

    return time_s, data

  def set_stream_data_getter(self, fn: Callable) -> None:
    self._get_latest_stream_data_fn = fn

  # Get some sample data to determine what gaze/pupil streams are present.
  def get_available_info(self) -> dict:
    # Will validate that all expected streams are present, and will also
    #  determine whether 2D or 3D processing is being used.
    # self._log_status('Waiting for initial eye tracking data to determine streams')
    # Temporarily set self._stream_video_world to True if we want self._stream_video_worldGaze.
    #   Gaze data is not stored yet, so video_worldGaze won't be created yet.
    #   So instead, we can at least check that the world is streamed.
    stream_video_world_original = self._stream_video_world
    if self._stream_video_worldGaze:
      self._stream_video_world = True
    gaze_data = None
    pupil_data = None
    video_world_data = None
    video_eye0_data = None
    video_eye1_data = None
    wait_start_time_s = time.time()
    wait_timeout_s = 5
    while (gaze_data is None
            or pupil_data is None
            or (video_world_data is None and self._stream_video_world)
            or (video_world_data is None and self._stream_video_worldGaze) # see above note - gaze data is not stored yet, so video_worldGaze won't be created yet, but can check if the world is streamed at least
            or (video_eye0_data is None and self._stream_video_eye)
            or (video_eye1_data is None and self._stream_video_eye and self._is_binocular)) \
          and (time.time() - wait_start_time_s < wait_timeout_s):
      time_s, data = self.process_pupil_data()
      gaze_data = gaze_data or data['gaze']
      pupil_data = pupil_data or data['pupil']
      video_world_data = video_world_data or data['video-world']
      video_eye0_data = video_eye0_data or data['video-eye0']
      video_eye1_data = video_eye1_data or data['video-eye1']
    if (time.time() - wait_start_time_s >= wait_timeout_s):
      msg = 'ERROR: Eye tracking did not detect all expected streams as active'
      msg+= '\n Gaze  data  is streaming? %s' % (gaze_data is not None)
      msg+= '\n Pupil data  is streaming? %s' % (pupil_data is not None)
      msg+= '\n Video world is streaming? %s' % (video_world_data is not None)
      msg+= '\n Video eye0   is streaming? %s' % (video_eye0_data is not None)
      msg+= '\n Video eye1   is streaming? %s' % (video_eye1_data is not None)
      # self._log_error(msg)
      raise AssertionError(msg)

    # Estimate the video frame rates
    fps_video_world = None
    fps_video_eye0 = None
    fps_video_eye1 = None
    def _get_fps(data_key, duration_s=0.1):
      # self._log_status('Estimating the eye-tracking frame rate for %s... ' % data_key, end='')
      # Wait for a new data entry, so we start timing close to a frame boundary.
      data = {data_key: None}
      while data[data_key] is not None:
        time_s, data = self.process_pupil_data()
      # Receive/process messages for the desired duration.
      time_start_s = time.time()
      frame_count = 0
      while time.time() - time_start_s < duration_s:
        time_s, data = self.process_pupil_data()
        if data[data_key] is not None:
          frame_count = frame_count+1
      # Since we both started and ended timing right after a sample, no need to do (frame_count-1).
      frame_rate = frame_count/(time.time() - time_start_s)
      # self._log_status('estimated frame rate as %0.2f for %s' % (frame_rate, data_key))
      return frame_rate
    if self._stream_video_world or self._stream_video_worldGaze:
      fps_video_world = _get_fps('video-world')
    if self._stream_video_eye:
      fps_video_eye0 = _get_fps('video-eye0')
      fps_video_eye1 = _get_fps('video-eye1') if self._is_binocular else None

    # Restore the desired stream world setting, now that world-gaze data has been obtained if needed.
    self._stream_video_world = stream_video_world_original

    # self._log_status('Started eye tracking streamer')

    data = {
      "gaze_data": gaze_data,
      "pupil_data": pupil_data,
      "video_world_data": video_world_data,
      "video_eye0_data": video_eye0_data,
      "video_eye1_data": video_eye1_data,
      "fps_video_world": fps_video_world,
      "fps_video_eye0": fps_video_eye0,
      "fps_video_eye1": fps_video_eye1
    }

    return data

  # Close sockets used by the Facade, destroy the ZeroMQ context in the SensorStreamer
  def close(self) -> None:
    self._zmq_requester.close()
    self._receiver.close()

  # A helper to clear the Pupil socket receive buffer.
  def _flush_pupilCapture_input_buffer(self) -> None:
    flush_completed = False
    while not flush_completed:
      try:
        self._zmq_requester.recv(flags=zmq.NOBLOCK)
        flush_completed = False
      except:
        flush_completed = True

  # A helper method to send data to the pupil system.
  # Payload can be a dict or a simple string.
  # Strings will be sent as-is, while a dict will be sent after a topic message.
  # Returns the response message received.
  def _send_to_pupilCapture(self, payload, topic=None) -> str:
    # Try to receive any outstanding messages, since sending
    #  will fail if there are any waiting.
    self._flush_pupilCapture_input_buffer()
    # Send the desired data as a dict or string.
    if isinstance(payload, dict):
      # Send the topic, using a default if needed.
      if topic is None:
        topic = 'notify.%s' % payload['subject']
      # Pack and send the payload.
      payload = msgpack.dumps(payload)
      self._zmq_requester.send_string(topic, flags=zmq.SNDMORE)
      self._zmq_requester.send(payload)
    else:
      # Send the topic if there is one.
      if topic is not None:
        self._zmq_requester.send_string(topic, flags=zmq.SNDMORE)
      # Send the payload as a string.
      self._zmq_requester.send_string(payload)
    # Receive the response.
    return self._zmq_requester.recv_string()

  # Get the time of the Pupil Core clock.
  # Data exported from the Pupil Capture software uses timestamps
  #  that are relative to a random epoch time.
  def _get_pupil_time(self):
    pupil_time_str = self._send_to_pupilCapture('t')
    return float(pupil_time_str)

  # Set the time of the Pupil Core clock to the system time.
  def _sync_pupil_time(self):
    # self._log_status('Syncing the Pupil Core clock with the system clock')
    # Note that the same number of decimals will always be used,
    #  so the length of the message is always the same
    #  (this can help make delay estimates more accurate).
    def set_pupil_time(time_s):
      self._send_to_pupilCapture('T %0.8f' % time_s)
    # Estimate the network delay when sending the set-time command.
    num_samples = 100
    transmit_delays_s = []
    for i in range(num_samples):
      local_time_before = time.time()
      set_pupil_time(time.time())
      local_time_after = time.time()
      # Assume symmetric delays.
      transmit_delays_s.append((local_time_after - local_time_before)/2.0)
    # Set the time!
    transmit_delay_s = np.mean(transmit_delays_s)
    set_pupil_time(time.time() + transmit_delay_s)
    # self._log_debug('Estimated Pupil Core set clock transmit delay [ms]: mean %0.3f | std %0.3f | min %0.3f | max %0.3f' % \
    #                 (np.mean(transmit_delay_s)*1000.0, np.std(transmit_delay_s)*1000.0,
    #                  np.min(transmit_delay_s)*1000.0, np.max(transmit_delay_s)*1000.0))
    # Check that the sync was successful.
    clock_offset_ms = self._measure_pupil_clock_offset_s()/1000.0
    if abs(clock_offset_ms) > 5:
      # self._log_warn('WARNING: Pupil Core clock sync may not have been successful. Offset is still %0.3f ms.' % clock_offset_ms)
      return False
    return True

  # Measure the offset between the Pupil Core clock (relative to a random epoch)
  #  and the system clock (relative to the standard epoch).
  # See the following for more information:
  #  https://docs.pupil-labs.com/core/terminology/#timestamps
  #  https://github.com/pupil-labs/pupil-helpers/blob/6e2cd2fc28c8aa954bfba068441dfb582846f773/python/simple_realtime_time_sync.py#L119
  def _measure_pupil_clock_offset_s(self, num_samples=100):
    assert num_samples > 0, 'Measuring the Pupil Capture clock offset requires at least one sample'
    clock_offsets_s = []
    for i in range(num_samples):
      # Account for network delays by recording the local time
      #  before and after the call to fetch the pupil time
      #  and then assuming that the clock was measured at the midpoint
      #  (assume symmetric network delays).
      # Note that in practice, this delay is small when
      #  using Pupil Capture via USB (typically 0-1 ms, rarely 5-10 ms).
      local_time_before = time.time()
      pupil_time = self._get_pupil_time()
      local_time_after = time.time()
      local_time = (local_time_before + local_time_after) / 2.0
      clock_offsets_s.append(pupil_time - local_time)
    # Average multiple readings to account for variable network delays.
    # self._log_debug('Estimated Pupil Core clock offset [ms]: mean %0.3f | std %0.3f | min %0.3f | max %0.3f' % \
    #                 (np.mean(clock_offsets_s)*1000.0, np.std(clock_offsets_s)*1000.0,
    #                  np.min(clock_offsets_s)*1000.0, np.max(clock_offsets_s)*1000.0))
    return np.mean(clock_offsets_s)

  # #####################################
  # ###### EXTERNAL DATA RECORDING ######
  # #####################################

  # # Whether recording via the sensor's dedicated software will require user action.
  # def external_data_recording_requires_user(self):
  #   return False

  # # Start the Pupil Capture software recording functionality.
  # def start_external_data_recording(self, recording_dir):
  #   recording_dir = os.path.join(recording_dir, 'pupil_capture')
  #   os.makedirs(recording_dir, exist_ok=True)
  #   self._send_to_pupilCapture('R %s' % recording_dir)
  #   self._log_status('Started Pupil Capture recording to %s' % recording_dir)

  # # Stop the Pupil Capture software recording funtionality.
  # def stop_external_data_recording(self):
  #   self._send_to_pupilCapture('r')
  #   self._log_status('Stopped Pupil Capture recording')

  # # Update a streamed data log with data recorded from Pupil Capture.
  # def merge_external_data_with_streamed_data(self,
  #                                             # Final post-processed outputs
  #                                             hdf5_file_toUpdate,
  #                                             data_dir_toUpdate,
  #                                             # Original streamed and external data
  #                                             data_dir_streamed,
  #                                             data_dir_external_original,
  #                                             # Archives for data no longer needed
  #                                             data_dir_archived,
  #                                             hdf5_file_archived):

  #   self._log_status('EyeStreamer merging streamed data with Pupil Capture data')
  #   self._define_data_notes()
  #   # Will save some state that will be useful later for creating a world-gaze video.
  #   world_video_filepath = None

  #   # Find the Pupil Capture recording subfolder within the main external data folder.
  #   # Use the most recent Pupil Capture subfolder in case the folder was used more than once.
  #   #   Pupil Capture will always create a folder named 000, 001, etc.
  #   data_dir_external_original = os.path.join(data_dir_external_original, 'pupil_capture')
  #   data_dir_external_subdirs = next(os.walk(data_dir_external_original))[1]
  #   numeric_subdirs = [subdir for subdir in data_dir_external_subdirs if subdir.isdigit()]
  #   if len(numeric_subdirs) == 0:
  #     self._log_error('\n\nAborting data merge for eye tracking - no externally recorded Pupil Capture folder found in %s\n' % data_dir_external_original)
  #     return
  #   recent_subdir = sorted(numeric_subdirs)[-1]
  #   data_dir_external_original = os.path.join(data_dir_external_original, recent_subdir)

  #   # Move files and deal with HDF5 timestamps.
  #   video_device_names = [
  #     'eye-tracking-video-world',
  #     'eye-tracking-video-worldGaze',
  #     'eye-tracking-video-eye0',
  #     'eye-tracking-video-eye1',
  #   ]
  #   for video_device_name in video_device_names:
  #     # Move the streamed video to the archive folder.
  #     #  (From data_dir_streamed to data_dir_archived)
  #     # Also save the streamed video filename, so the Pupil recording can assume it later.
  #     filepaths = glob.glob(os.path.join(data_dir_streamed, '*%s_frame.*' % (video_device_name)))
  #     if len(filepaths) > 0:
  #       filepath = filepaths[0]
  #       streamed_video_filename = os.path.basename(filepath)
  #       self._log_debug(' Moving streamed video %s to %s' % (filepath, data_dir_archived))
  #       shutil.move(filepath, os.path.join(data_dir_archived, streamed_video_filename))
  #     else:
  #       streamed_video_filename = None
      
  #     # Move the Pupil Capture video to the final data folder.
  #     #  (from data_dir_external_original to data_dir_toUpdate)
  #     filepath = None
  #     if video_device_name == 'eye-tracking-video-world':
  #       filepath = os.path.join(data_dir_external_original, 'world.mp4')
  #     elif video_device_name == 'eye-tracking-video-eye':
  #       filepaths = glob.glob(os.path.join(data_dir_external_original, 'eye*.mp4'))
  #       if len(filepaths) > 0:
  #         filepath = filepaths[0]
  #     if isinstance(filepath, str) and os.path.exists(filepath):
  #       if streamed_video_filename is not None:
  #         filename = '%s.mp4' % os.path.splitext(streamed_video_filename)[0]
  #       else:
  #         filename = '%s_frame.mp4' % video_device_name
  #       self._log_debug(' Moving Pupil Capture video %s to %s' % (filepath, data_dir_toUpdate))
  #       shutil.move(filepath, os.path.join(data_dir_toUpdate, filename))
  #       if video_device_name == 'eye-tracking-video-world':
  #         world_video_filepath = os.path.join(data_dir_toUpdate, filename)
      
  #     # Move streamed timestamps, and frame data if applicable, to the archive HDF5 file.
  #     #  (from hdf5_file_toUpdate to hdf5_file_archived)
  #     device_group_metadata = {}
  #     if video_device_name in hdf5_file_toUpdate:
  #       self._log_debug(' Moving streamed timestamps and frames for %s to archived HDF5' % video_device_name)
  #       device_group_old = hdf5_file_toUpdate[video_device_name]
  #       hdf5_file_toUpdate.copy(device_group_old, hdf5_file_archived,
  #                               name=None, shallow=False,
  #                               expand_soft=True, expand_external=True, expand_refs=True,
  #                               without_attrs=False)
  #       device_group_metadata = dict(device_group_old.attrs.items())
  #       hdf5_file_archived[video_device_name].attrs.update(device_group_metadata)
  #       del hdf5_file_toUpdate[video_device_name]

  #     # Add the Pupil Capture timestamps to the final HDF5 file.
  #     #  (from files in data_dir_external_original to hdf5_file_toUpdate)
  #     timestamps_filepath = None
  #     if video_device_name in ['eye-tracking-video-world', 'eye-tracking-video-worldGaze']:
  #       timestamps_filepath = os.path.join(data_dir_external_original, 'world_timestamps.npy')
  #     elif video_device_name == 'eye-tracking-video-eye':
  #       for eye_index in range(2):
  #         timestamps_filepath = os.path.join(data_dir_external_original, 'eye%d_timestamps.npy' % eye_index)
  #         if os.path.exists(timestamps_filepath):
  #           break
  #     if isinstance(timestamps_filepath, str) and os.path.exists(timestamps_filepath):
  #       self._log_debug(' Adding recorded timestamps for %s to updated HDF5' % video_device_name)
  #       # Load the timestamps, and create user-friendly strings from them.
  #       timestamps_s = np.load(timestamps_filepath)
  #       timestamps_str = [get_time_str(t, '%Y-%m-%d %H:%M:%S.%f') for t in timestamps_s]
  #       num_timestamps = timestamps_s.shape[0]
  #       # Delete any existing timestamp data in the HDF5 and prepare a new group.
  #       if video_device_name not in hdf5_file_toUpdate:
  #         hdf5_file_toUpdate.create_group(video_device_name)
  #         hdf5_file_toUpdate[video_device_name].attrs.update(device_group_metadata)
  #       if 'frame_timestamp' in hdf5_file_toUpdate[video_device_name]:
  #         del hdf5_file_toUpdate[video_device_name]['frame_timestamp']
  #       hdf5_file_toUpdate[video_device_name].create_group('frame_timestamp')
  #       stream_group = hdf5_file_toUpdate[video_device_name]['frame_timestamp']
  #       # Add the new timestamps.
  #       # Note that for now, the Pupil Capture timestamps are assumed to
  #       #  be the same as the system time at which they would have been received (time_s).
  #       # This assumes that the Pupil Core clock was synced with the system time.
  #       # If further adjustment is desired, to estimate when each frame would have been
  #       #  received by the streaming system, then the information in
  #       #  the device eye-tracking-time can be used to interpolate new arrival times,
  #       #  since it maps from system arrival time to Pupil Core time.
  #       stream_group.create_dataset('data', [num_timestamps, 1], dtype='float64',
  #                                   data=timestamps_s)
  #       stream_group.create_dataset('time_s', [num_timestamps, 1], dtype='float64',
  #                                   data=timestamps_s)
  #       stream_group.create_dataset('time_str', [num_timestamps, 1], dtype='S26',
  #                                   data=timestamps_str)
  #       stream_group.attrs.update(convert_dict_values_to_str(
  #           self._data_notes_postprocessed[video_device_name]['frame_timestamp'],
  #           preserve_nested_dicts=False))

  #   # Create a world video with a gaze indicator overlaid.
  #   #  Note that timestamps for it will be added to the HDF5 file below.
  #   world_video_timestamps_filepath = os.path.join(data_dir_external_original, 'world_timestamps.npy')
  #   if (world_video_filepath is not None and os.path.exists(world_video_filepath)) \
  #       and os.path.exists(world_video_timestamps_filepath) \
  #       and 'eye-tracking-gaze' in hdf5_file_toUpdate:
  #     video_reader = cv2.VideoCapture(world_video_filepath)
  #     video_times_s = np.load(world_video_timestamps_filepath)
  #     gaze_positions = hdf5_file_toUpdate['eye-tracking-gaze']['position']['data']
  #     gaze_times_s = np.asarray(hdf5_file_toUpdate['eye-tracking-gaze']['timestamp']['data']) # use Pupil Core time instead of system time (['eye-tracking-gaze']['position']['time_s']) to align more precisely with the video frame timestamps copied above
  #     # Base the new filename on the streamed world video filename if there was one.
  #     # Note that the streamed videos were moved to the arvhive folder above.
  #     streamed_video_filepaths = glob.glob(os.path.join(data_dir_archived, '*eye-tracking-video-world_frame.*'))
  #     if len(streamed_video_filepaths) > 0:
  #       worldGaze_filename = streamed_video_filepaths[0].replace('eye-tracking-video-world', 'eye-tracking-video-worldGaze')
  #       worldGaze_filename = os.path.basename(worldGaze_filename)
  #     else:
  #       worldGaze_filename = 'eye-tracking-video-worldGaze_frame.avi'
  #     worldGaze_filepath = os.path.join(data_dir_toUpdate, worldGaze_filename)
  #     self._log_debug(' Creating world-gaze video based on %s' % world_video_filepath)
  #     self._log_debug(' Will output synthesized video to   %s' % worldGaze_filepath)
  #     # Extract information about the video input and create a video writer for the output.
  #     success, video_frame = video_reader.read()
  #     video_reader.set(cv2.CAP_PROP_POS_FRAMES, 0) # go back to the beginning
  #     frame_height = video_frame.shape[0]
  #     frame_width = video_frame.shape[1]
  #     data_type = str(video_frame.dtype)
  #     sampling_rate_hz = video_reader.get(cv2.CAP_PROP_FPS)
  #     frame_count = int(video_reader.get(cv2.CAP_PROP_FRAME_COUNT))
  #     fourcc = 'MJPG'
  #     video_writer = cv2.VideoWriter(worldGaze_filepath,
  #                                    cv2.VideoWriter_fourcc(*fourcc),
  #                                    sampling_rate_hz, (frame_width, frame_height))
  #     # Loop through each input frame and overlay the gaze indicator.
  #     previous_video_frame = None
  #     previous_video_frame_noGaze = None
  #     for i in range(frame_count):
  #       # Get the next video frame, and the gaze estimate closest to it in time.
  #       try:
  #         success, video_frame = video_reader.read()
  #         previous_video_frame_noGaze = video_frame
  #         video_time_s = video_times_s[i]
  #         gaze_index = (np.abs(gaze_times_s - video_time_s)).argmin()
  #         gaze_time_s = gaze_times_s[gaze_index][0]
  #         gaze_position = gaze_positions[gaze_index, :]
  #         # Draw the gaze indicator on the frame.
  #         world_gaze_time_diff_s = video_time_s - gaze_time_s
  #         gaze_radius = 10
  #         gaze_color_outer = (255, 255, 255) # BGR format
  #         if abs(world_gaze_time_diff_s) < self._gaze_estimate_stale_s: # check if the gaze prediction is recent
  #           gaze_color_inner = (0, 255, 0) # BGR format
  #         else: # gaze prediction is stale
  #           gaze_color_inner = (0, 0, 0) # BGR format
  #         gaze_position[1] = 1 - gaze_position[1]
  #         gaze_position = tuple((gaze_position * [video_frame.shape[1], video_frame.shape[0]]).astype(int))
  #         cv2.circle(video_frame, gaze_position, gaze_radius, gaze_color_outer, -1, lineType=cv2.LINE_AA)
  #         cv2.circle(video_frame, gaze_position, round(gaze_radius*0.7), gaze_color_inner, -1, lineType=cv2.LINE_AA)
  #         # Write the frame.
  #         video_writer.write(video_frame)
  #         previous_video_frame = video_frame
  #       except:
  #         # Add a dummy frame to stay aligned with the frame timestamps.
  #         self._log_debug('  Error processing frame %6d/%d. Copying the previous frame instead.' % ((i+1), frame_count))
  #         if previous_video_frame_noGaze is not None:
  #           video_frame = previous_video_frame_noGaze
  #         else:
  #           video_frame = np.zeros((frame_height, frame_width, 3), dtype=np.uint8)
  #         video_writer.write(video_frame)
  #       # Print some status updates.
  #       if self._print_debug and (((i+1) % int(frame_count/10)) == 0 or (i+1) == frame_count):
  #         self._log_debug('  Processed %6d/%d frames (%0.1f%%)' % ((i+1), frame_count, 100*(i+1)/frame_count))
  #     video_writer.release()
  #     video_reader.release()
