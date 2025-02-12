import os
import cv2
from utils.time_utils import get_time_str

# Configure printing and logging.
print_status: bool = True
print_debug: bool = True

# Configure trial.
subject_id: int = 1 # UID of the subject
trial_id: int = 1 # UID of the trial
is_real: bool = False # Data collection from actual trials

# Configure settings for each streamer.
producer_specs = [
  # Allow the experimenter to label data and enter notes.
  {'class': 'ExperimentControlStreamer',
    'activities': [ # Cybathlon activities that you want to label
      'Balance beam',
      'Stairs',
      'Step over',
      'Slopes',
      'Bench and table',
      'Wobbly steps',
      'High step',
      'Ladder',
      'Cross country',
      'Hurdles',
    ],
    'print_debug': print_debug, 'print_status': print_status
    },
  # Stream from the Awinda body tracking and Manus gloves.
  {'class': 'AwindaStreamer',
    'device_mapping': {
      'pelvis'         : '00B4D3E4',
      'upper_leg_right': '00B4D3D7',
      'lower_leg_right': '00B4D3E2',
      'foot_right'     : '00B4D3DD',
      'upper_leg_left' : '00B4D3E7',
      'lower_leg_left' : '00B4D3D4',
      'foot_left'      : '00B4D3D8',
    },
    'num_joints'        : 7,
    'sampling_rate_hz'  : 100,
    'radio_channel'     : 15,
    'print_debug': print_debug, 'print_status': print_status
    },
  # Stream from the DOTs lower limb tracking.
  {'class': 'DotsStreamer',
    'device_mapping': {
      'knee_right'  : '40195BFC800B01F2',
      'foot_right'  : '40195BFC800B003B',
      'pelvis'      : '40195BFD80C20052',
      'knee_left'   : '40195BFC800B017A',
      'foot_left'   : '40195BFD80C200D1',
    },
    'master_device'   : 'pelvis', # wireless dot relaying messages, must match a key in the `device_mapping`
    'num_joints'      : 5,
    'sampling_rate_hz': 20,
    'print_debug': print_debug, 'print_status': print_status
    },
  # Stream from the Pupil Labs eye tracker, including gaze and video data.
  {'class': 'EyeStreamer',
    'pupil_capture_ip'      : 'localhost',
    'pupil_capture_port'    : '50020',
    'video_image_format'    : 'bgr',
    'gaze_estimate_stale_s' : 0.2,
    'stream_video_world'    : False, # the world video
    'stream_video_worldGaze': True, # the world video with gaze indication overlayed
    'stream_video_eye'      : False, # video of the eye
    'is_binocular'          : True, # uses both eyes for gaze data and for video
    'shape_video_world'     : (720,1280,3),
    'shape_video_eye0'      : (400,400,3),
    'shape_video_eye1'      : (400,400,3),
    'fps_video_world'       : 30.0,
    'fps_video_eye0'        : 120.0,
    'fps_video_eye1'        : 120.0,
    'print_debug': print_debug, 'print_status': print_status
    },
  # Stream from one or more cameras.
  {'class': 'CameraStreamer',
    'camera_mapping': { # map camera names (usable as device names in the HDF5 file) to capture device indexes
      'basler_north' : '40478064',
      'basler_east'  : '40549960',
      'basler_south' : '40549975',
      'basler_west'  : '40549976',
    },
    'fps': 20,
    'resolution': (1944, 2592),
    'color_format': cv2.COLOR_BAYER_RG2RGB,
    'camera_config_filepath': 'resources/pylon_20fps_maxres.pfs',
    'print_debug': print_debug, 'print_status': print_status
    },
  # Moticon insole pressure.
  {'class': 'InsoleStreamer',
    'sampling_rate_hz': 100,
    'print_debug': print_debug, 'print_status': print_status
    },
  # Moxy blood oxygenation.
  {'class': 'MoxyStreamer',
    'devices': ["8523",
                "8524",
                "8525"],
    'sampling_rate_hz': 0.5,
    'print_debug': print_debug, 'print_status': print_status
    },
  # TMSi SAGA device.
  {'class': 'TmsiStreamer',
    'sampling_rate_hz': 20,
    'print_debug': print_debug, 'print_status': print_status
    },
  # Vicon capture system.
  {'class': 'ViconStreamer',
    'print_debug': print_debug, 'print_status': print_status
    },
  # Stream from one or more microphones.
  {'class': 'MicrophoneStreamer',
    'device_names_withAudioKeywords': {'microphone_conference': 'USB audio CODEC'},
    'print_debug': print_debug, 'print_status': print_status
    }
]

# Find the camera names for future use.
camera_streamer_index: int = ['CameraStreamer' in spec['class'] for spec in producer_specs].index(True)
camera_ids: list[str] = list(producer_specs[camera_streamer_index]['camera_mapping'].values())

# Configure where and how to save sensor data.
#   Adjust log_tag, and log_dir_root as desired.
trial_type: str = 'real' if is_real else 'test' # recommend 'tests' and 'experiments' for testing vs "real" data
log_tag: str = 'aidWear-wearables'

script_dir: str = os.path.dirname(os.path.realpath(__file__))
(log_time_str, log_time_s) = get_time_str(return_time_s=True)
log_dir_root: str = os.path.join(script_dir, '..', '..', 'data',
                            trial_type,
                            '{0}_S{1}_{2}'.format(get_time_str(format='%Y-%m-%d'), 
                                                  str(subject_id).zfill(3), 
                                                  str(trial_id).zfill(2)))
log_subdir: str = '%s_%s' % (log_time_str, log_tag)
log_dir: str = os.path.join(log_dir_root, log_subdir)
# Initialize a file for writing the log history of all printouts/messages.
log_history_filepath: str = os.path.join(log_dir, '%s_log_history.txt' % (log_time_str))
os.makedirs(log_dir, exist_ok=True)

logging_spec = {
  'log_dir': log_dir,
  'log_tag': log_tag,
  # Choose whether to periodically write data to files.
  'stream_hdf5' : True, # recommended over CSV since it creates a single file
  'stream_csv'  : False, # will create a CSV per stream
  'stream_video': True,
  'stream_audio': False,
  'stream_period_s': 10, # how often to save streamed data to disk
  'clear_logged_data_from_memory': True, # NOTE: IGNORED if dumping is also enabled below
  # Choose whether to write all data at the end.
  #   Seems to be mutually exclusive with `stream_` options.
  'dump_csv'  : False,
  'dump_hdf5' : False,
  'dump_video': False,
  'dump_audio': False,
  # Additional configuration.
  #   Choose whether to save media separately or as part of HDF5/CSV files.
  'videos_format' : 'avi', # mp4 occasionally gets openCV errors about a tag not being supported?
  'videos_in_hdf5': False,
  'videos_in_csv' : False,
  'audio_format'  : 'wav', # currently only supports WAV
  'audio_in_hdf5' : False,
  'audio_in_csv'  : False,
}

# Add logging spec to each producer.
for spec in producer_specs:
  spec['logging_spec'] = logging_spec

# Configure visualization.
composite_frame_size = (1280, 720) # screen resolution
composite_col_width_quarter = int(composite_frame_size[0]/4)
composite_row_height = int(composite_frame_size[1]/5)
visualization_spec = {
  'is_visualize_streaming'       : True,
  'is_visualize_all_when_stopped': True,
  'is_wait_while_windows_open': False,
  'update_period_s': 0.1,
  'use_composite_video': True,
  'composite_video_filepath': os.path.join(log_dir, 'composite_visualization') if log_dir is not None else None,
  'composite_video_layout': [ # first 3 rows of IMU data, next 2 of video data with Pupil Core spanning 2x2 cell and 4 PoE cameras around it
    [ # row  0
      {'device_name':'dots-imu', 'stream_name':'acceleration-x', 'rowspan':1, 'colspan':2, 'width':composite_col_width_quarter, 'height':composite_row_height},
      {'device_name':None, 'stream_name':None, 'rowspan':0, 'colspan':0, 'width':0, 'height':0},
      {'device_name':'dots-imu', 'stream_name':'orientation-x', 'rowspan':1, 'colspan':2, 'width':composite_col_width_quarter, 'height':composite_row_height},
      {'device_name':None, 'stream_name':None, 'rowspan':0, 'colspan':0, 'width':0, 'height':0},
      # {'device_name':'awinda-imu', 'stream_name':'acceleration-x', 'rowspan':1, 'colspan':1, 'width':composite_col_width_quarter, 'height':composite_row_height},
      # {'device_name':'awinda-imu', 'stream_name':'orientation-x', 'rowspan':1, 'colspan':1, 'width':composite_col_width_quarter, 'height':composite_row_height},
    ],
    [ # row  1
      {'device_name':'dots-imu', 'stream_name':'acceleration-y', 'rowspan':1, 'colspan':2, 'width':composite_col_width_quarter, 'height':composite_row_height},
      {'device_name':None, 'stream_name':None, 'rowspan':0, 'colspan':0, 'width':0, 'height':0},
      {'device_name':'dots-imu', 'stream_name':'orientation-y', 'rowspan':1, 'colspan':2, 'width':composite_col_width_quarter, 'height':composite_row_height},
      {'device_name':None, 'stream_name':None, 'rowspan':0, 'colspan':0, 'width':0, 'height':0},
      # {'device_name':'awinda-imu', 'stream_name':'acceleration-y', 'rowspan':1, 'colspan':1, 'width':composite_col_width_quarter, 'height':composite_row_height},
      # {'device_name':'awinda-imu', 'stream_name':'orientation-y', 'rowspan':1, 'colspan':1, 'width':composite_col_width_quarter, 'height':composite_row_height},
    ],
    [ # row  2
      {'device_name':'dots-imu', 'stream_name':'acceleration-z', 'rowspan':1, 'colspan':2, 'width':composite_col_width_quarter, 'height':composite_row_height},
      {'device_name':None, 'stream_name':None, 'rowspan':0, 'colspan':0, 'width':0, 'height':0},
      {'device_name':'dots-imu', 'stream_name':'orientation-z', 'rowspan':1, 'colspan':2, 'width':composite_col_width_quarter, 'height':composite_row_height},
      {'device_name':None, 'stream_name':None, 'rowspan':0, 'colspan':0, 'width':0, 'height':0},
      # {'device_name':'awinda-imu', 'stream_name':'acceleration-z', 'rowspan':1, 'colspan':1, 'width':composite_col_width_quarter, 'height':composite_row_height},
      # {'device_name':'awinda-imu', 'stream_name':'orientation-z', 'rowspan':1, 'colspan':1, 'width':composite_col_width_quarter, 'height':composite_row_height},
    ],
    [ # row  3 
      {'device_name':camera_ids[0], 'stream_name':'frame', 'rowspan':1, 'colspan':1, 'width':composite_col_width_quarter, 'height':composite_row_height},
      {'device_name':'eye-tracking-video-worldGaze', 'stream_name':'frame', 'rowspan':2, 'colspan':2, 'width':composite_col_width_quarter, 'height':composite_row_height},
      {'device_name':None, 'stream_name':None, 'rowspan':0, 'colspan':0, 'width':0, 'height':0},
      {'device_name':camera_ids[1], 'stream_name':'frame', 'rowspan':1, 'colspan':1, 'width':composite_col_width_quarter, 'height':composite_row_height},
    ],
    [ # row  4
      {'device_name':camera_ids[2], 'stream_name':'frame', 'rowspan':1, 'colspan':1, 'width':composite_col_width_quarter, 'height':composite_row_height},
      {'device_name':None, 'stream_name':None, 'rowspan':0, 'colspan':0, 'width':0, 'height':0},
      {'device_name':None, 'stream_name':None, 'rowspan':0, 'colspan':0, 'width':0, 'height':0},
      {'device_name':camera_ids[3], 'stream_name':'frame', 'rowspan':1, 'colspan':1, 'width':composite_col_width_quarter, 'height':composite_row_height},
    ],
  ]
}
