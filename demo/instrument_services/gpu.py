"""
获取系统opengl
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


def gpu(rpc, callback=None):
    def on_gpu_message(res):
        print(res.plist)
        #print(json.dumps(res.parsed, indent=4))

    pre_call(rpc)
    rpc.register_channel_callback("com.apple.instruments.server.services.gpu", on_gpu_message)
    #var = rpc.call("com.apple.instruments.server.services.gpu", "stopCollectingCounters").parsed
    #print("stop", str(var))
    print(rpc.call("com.apple.instruments.server.services.gpu", "requestDeviceGPUInfo").parsed)
    var = rpc.call("com.apple.instruments.server.services.gpu", "enableShaderProfiler").parsed
    #print("enable" + str(var))
    var = rpc.call("com.apple.instruments.server.services.gpu", "startCollectingCounters").parsed
    print("start" + str(var))
    time.sleep(10)
    #var = rpc.call("com.apple.instruments.server.services.gpu", "flushRemainingData").parsed
    #print("flush" + str(var))
    time.sleep(10)
    var = rpc.call("com.apple.instruments.server.services.gpu", "stopCollectingCounters").parsed
    print("stop" + str(var))
    rpc.call("com.apple.instruments.server.services.gpu", "cleanup").parsed
    rpc.stop()


if __name__ == '__main__':
    rpc = get_usb_rpc()
    gpu(rpc)
    rpc.deinit()
