"""
获取系统FPS
"""
import os
import sys

sys.path.append(os.getcwd())
import json
import time
from threading import Event
from instrument.RPC import pre_call, get_usb_rpc
from util import logging

log = logging.getLogger(__name__)


def opengl(rpc, callback=None):
    def on_opengl_message(res):
        print(json.dumps(res.parsed, indent=4))

    pre_call(rpc)
    rpc.register_channel_callback("com.apple.instruments.server.services.graphics", callback)
    #rpc.register_channel_callback("com.apple.instruments.server.services.graphics", on_opengl_message)
    var = rpc.call("com.apple.instruments.server.services.graphics.opengl", "startSamplingAtTimeInterval:", 10).parsed
    print("start" + str(var))
    time.sleep(10)
    var = rpc.call("com.apple.instruments.server.services.graphics.opengl", "stopSampling").parsed
    print("stop" + str(var))
    rpc.call("com.apple.instruments.server.services.graphics.opengl", "cleanup").parsed
    rpc.stop()


if __name__ == '__main__':
    rpc = get_usb_rpc()
    opengl(rpc)
    rpc.deinit()
