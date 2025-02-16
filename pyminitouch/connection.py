import subprocess
import socket
import time
import os
import random
from contextlib import contextmanager

from pyminitouch import config
from pyminitouch.utils import (
    str2byte,
    download_file,
    is_port_using,
    is_device_connected,
)

_ADB = config.ADB_EXECUTOR


class MNTInstaller(object):
    """ install minitouch for android devices """

    def __init__(self, device_id, logger):
        self.logger = logger
        self.device_id = device_id
        self.abi = self.get_abi()
        self.download_target_mnt()

    def get_abi(self):
        abi = subprocess.check_output(
            "{} -s {} shell getprop ro.product.cpu.abi".format(_ADB, self.device_id),
            errors='strict',
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).strip()
        self.logger.info("{}.abi = {}".format(self.device_id, abi))
        return abi

    def download_target_mnt(self):
        abi = self.abi
        target_url = "{}/{}/bin/minitouch".format(config.MNT_PREBUILT_URL, abi)
        self.logger.info("downloading minitouch binary")
        mnt_path = download_file(target_url)

        # push and grant
        subprocess.check_call(
            [_ADB, "-s", self.device_id, "push", mnt_path, config.MNT_HOME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        subprocess.check_call(
            [_ADB, "-s", self.device_id, "shell", "chmod", "777", config.MNT_HOME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        self.logger.info("{}.binary_path = {}".format(self.device_id, config.MNT_HOME))

        subprocess.run(
            [_ADB, "-s", self.device_id, "shell", "killall", "minitouch"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

        self.logger.info("killed all instances of minitouch")

        # remove temp
        os.remove(mnt_path)

class MNTServer(object):
    """
    manage connection to minitouch.
    before connection, you should execute minitouch with adb shell.

    command eg::

        adb forward tcp:{some_port} localabstract:minitouch
        adb shell /data/local/tmp/minitouch

    you would better use it via safe_connection ::

        _DEVICE_ID = '123456F'

        with safe_connection(_DEVICE_ID) as conn:
            conn.send('d 0 500 500 50\nc\nd 1 500 600 50\nw 5000\nc\nu 0\nu 1\nc\n')
    """

    _PORT_SET = config.PORT_SET

    def __init__(self, device_id, logger):
        assert is_device_connected(device_id)

        self.logger = logger
        self.device_id = device_id
        self.port = self._get_port()
        self.logger.info("{}.bound_port = {}".format(device_id, self.port))

        # check minitouch
        self.installer = MNTInstaller(device_id, self.logger)

        # keep minitouch alive
        self._forward_port()
        self.mnt_process = None
        self._start_mnt()

        # make sure it's up
        time.sleep(1)
        assert (
            self.heartbeat()
        ), "minitouch did not work. see https://github.com/williamfzc/pyminitouch/issues/11"

    def stop(self):
        self.mnt_process and self.mnt_process.kill()
        self._PORT_SET.add(self.port)
        self.logger.info("device {} unbound from port {}".format(self.device_id, self.port))

    @classmethod
    def _get_port(cls):
        """ get a random port from port set """
        new_port = random.choice(list(cls._PORT_SET))
        if is_port_using(new_port):
            return cls._get_port()
        return new_port

    def _forward_port(self):
        """ allow pc access minitouch with port """
        command_list = [
            _ADB,
            "-s",
            self.device_id,
            "forward",
            "tcp:{}".format(self.port),
            "localabstract:minitouch",
        ]
        self.logger.debug("forward command: {}".format(" ".join(command_list)))
        output = subprocess.check_output(
            command_list,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        self.logger.debug("output: {}".format(output))

    def _start_mnt(self):
        """ fork a process to start minitouch on android """
        command_list = [
            _ADB,
            "-s",
            self.device_id,
            "shell",
            "/data/local/tmp/minitouch",
        ]
        self.logger.info("{}.start_command = {}".format(self.device_id, " ".join(command_list)))
        self.mnt_process = subprocess.Popen(
            command_list,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

    def heartbeat(self):
        """ check if minitouch process alive """
        return self.mnt_process.poll() is None


class MNTConnection(object):
    """ manage socket connection between pc and android """

    _DEFAULT_HOST = config.DEFAULT_HOST
    _DEFAULT_BUFFER_SIZE = config.DEFAULT_BUFFER_SIZE

    def __init__(self, port, logger):
        self.logger = logger
        self.port = port

        # build connection
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect((self._DEFAULT_HOST, self.port))
        self.client = client

        # get minitouch server info
        socket_out = client.makefile()

        # v <version>
        # protocol version, usually it is 1. needn't use this
        socket_out.readline()

        # ^ <max-contacts> <max-x> <max-y> <max-pressure>
        _, max_contacts, max_x, max_y, max_pressure, *_ = (
            socket_out.readline().replace("\n", "").replace("\r", "").split(" ")
        )
        self.max_contacts = max_contacts
        self.max_x = max_x
        self.max_y = max_y
        self.max_pressure = max_pressure

        # $ <pid>
        _, pid = socket_out.readline().replace("\n", "").replace("\r", "").split(" ")
        self.pid = pid

        self.logger.info(
            "minitouch running on port: {}, pid: {}".format(self.port, self.pid)
        )

    def disconnect(self):
        self.client and self.client.close()
        self.client = None
        self.logger.info("minitouch disconnected")

    def send(self, content):
        """ send message and get its response """
        byte_content = str2byte(content)
        self.client.sendall(byte_content)
        return self.client.recv(self._DEFAULT_BUFFER_SIZE)


@contextmanager
def safe_connection(device_id):
    """ safe connection runtime to use """

    # prepare for connection
    server = MNTServer(device_id)
    # real connection
    connection = MNTConnection(server.port)
    try:
        yield connection
    finally:
        # disconnect
        connection.disconnect()
        server.stop()


if __name__ == "__main__":
    _DEVICE_ID = "123456F"

    with safe_connection(_DEVICE_ID) as conn:
        # conn.send('d 0 150 150 50\nc\nu 0\nc\n')
        conn.send("d 0 500 500 50\nc\nd 1 500 600 50\nw 5000\nc\nu 0\nu 1\nc\n")
