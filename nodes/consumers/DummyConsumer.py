from nodes.consumers.Consumer import Consumer
from handlers.LoggingHandler import Logger

import threading
from utils.zmq_utils import *


class DummyConsumer(Consumer):
  @classmethod
  def _log_source_tag(cls) -> str:
    return 'dummy-consumer'


  def __init__(self,
               stream_specs: list[dict],
               logging_spec: dict,
               port_sub: str = PORT_FRONTEND,
               port_sync: str = PORT_SYNC,
               port_killsig: str = PORT_KILL,
               log_history_filepath: str = None,
               print_status: bool = True,
               print_debug: bool = False,
               **_):

    # Inherits FSM and Consumer ZeroMQ functionality.
    super().__init__(stream_specs=stream_specs,
                     port_sub=port_sub,
                     port_sync=port_sync,
                     port_killsig=port_killsig,
                     log_history_filepath=log_history_filepath,
                     print_status=print_status,
                     print_debug=print_debug)

    # Inherits the datalogging functionality.
    self._logger = Logger(**logging_spec)

    # Launch datalogging thread with reference to the Stream object.
    self._logger_thread = threading.Thread(target=self._logger, args=(self._streams,))
    self._logger_thread.start()


  # Stop all the data logging.
  # Will stop stream-logging if it is active.
  # Will dump all data if desired.
  def _cleanup(self):
    # Finish up the file saving before exitting.
    self._logger.cleanup()
    self._logger_thread.join()
    super()._cleanup()
