from .exceptions import NotConnected
from .tcp_stream import TCPStream
from .internal_messages import (
    AckClientDisconnect,
    NotifyClientDisconnect)
import logging
import queue
import time
import tornado.tcpclient
import tornado.ioloop
import threading

logger = logging.getLogger(__name__)


class TCPClient():
    '''A TCP client to handle client-server communication through
    :class:`TCPMessage` objects.

    Args:

        host (string):
        port (int):

            The hostname and port of the :class:`TCPServer` to connect to.
    '''

    def __init__(self, host, port):

        logger.debug("Creating new IOLoop for TCPClient")
        self.ioloop = tornado.ioloop.IOLoop()

        logger.debug("Starting io loop for TCPClient")
        self.ioloop_thread = threading.Thread(
            target=self.ioloop.start,
            daemon=True)
        self.ioloop_thread.start()

        logger.debug("Creating new TCP client...")

        self.host = host
        self.port = port
        self.client = tornado.tcpclient.TCPClient()
        self.stream = None
        self.message_queue = queue.Queue()
        self.exception_queue = queue.Queue()

        self.connect()

    def __del__(self):
        logger.debug("Garbage collection called for TCPClient")
        if self.connected():
            self.disconnect()

    def connect(self):
        '''Connect to the server and start the message receive event loop.'''

        logger.debug("Connecting to server at %s:%d...", self.host, self.port)

        self.ioloop.add_callback(self._connect)
        while not self.connected():
            self._check_for_errors()
            time.sleep(.1)

        logger.debug("...connected")

        self.ioloop.add_callback(self._receive)

    def disconnect(self):
        '''Gracefully close the connection to the server and stop the
        IOLoop.'''

        if not self.connected():
            logger.warn("Called disconnect() on disconnected client")
            return

        logger.debug("Notifying server of disconnect...")
        self.stream.send_message(NotifyClientDisconnect())

        while self.connected():
            time.sleep(.1)

        # TCPServer is no longer listening to this client, now it is safe to
        # close the stream and stop the IOLoop
        logger.debug("Stopping IOLoop")
        self.ioloop.add_callback(self.ioloop.stop)

        logger.debug("Disconnected")

    def connected(self):
        '''Check whether this client has a connection to the server.'''
        return self.stream is not None

    def send_message(self, message):
        '''Send a message to the server.

        Args:

            message (:class:`TCPMessage`):

                Message to send over to the server.
        '''

        self._check_for_errors()

        if not self.connected():
            raise NotConnected()

        self.stream.send_message(message)

    def get_message(self, timeout=None):
        '''Get a message that was sent to this client.

        Args:

            timeout (``float``, optional):

                If set, wait up to `timeout` seconds for a message to arrive.
                If no message is available after the timeout, returns ``None``.
                If not set, wait until a message arrived.
        '''

        self._check_for_errors()

        if not self.connected():
            raise NotConnected()

        try:

            return self.message_queue.get(block=True, timeout=timeout)

        except queue.Empty:

            return None

    def _check_for_errors(self):

        try:
            exception = self.exception_queue.get(block=False)
            raise exception
        except queue.Empty:
            return

    async def _connect(self):
        '''Async method to connect to the TCPServer.'''

        try:
            stream = await self.client.connect(self.host, self.port)
        except Exception as e:
            self.exception_queue.put(e)
            return

        self.stream = TCPStream(stream)

    async def _receive(self):
        '''Loop that receives messages from the server.'''

        logger.debug("Entering receive loop")

        while self.connected():

            try:

                # raises StreamClosedError
                message = await self.stream._get_message()

                if isinstance(message, AckClientDisconnect):

                    # server acknowledged disconnect, close connection on
                    # our side and break out of event loop
                    try:
                        self.stream.close()
                    finally:
                        self.stream = None
                        return

                else:

                    self.message_queue.put(message)

            except Exception as e:

                try:
                    self.exception_queue.put(e)
                    self.stream.close()
                finally:
                    # mark client as disconnected
                    self.stream = None
                    return
