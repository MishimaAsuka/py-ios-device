#!/usr/bin/env python
# -*- coding: utf-8 -*-

import runpy
import sys
import argparse
import logging
import time

from py_instrument_client.util.usbmux import USBMux
from py_instrument_client.util.lockdown import LockdownClient
from py_instrument_client.demo.installation_proxy import installation_proxy

from py_instrument_client.instrument.RPC import get_usb_rpc, pre_call


log = logging.getLogger(__name__)

SYSMON_CONFIG_MSG = {
        'ur': 1000,  # 输出频率 ms
        'bm': 0,
        'procAttrs': ['memVirtualSize', 'cpuUsage', 'procStatus', 'appSleep', 'uid', 'vmPageIns', 'memRShrd',
                      'ctxSwitch', 'memCompressed', 'intWakeups', 'cpuTotalSystem', 'responsiblePID', 'physFootprint',
                      'cpuTotalUser', 'sysCallsUnix', 'memResidentSize', 'sysCallsMach', 'memPurgeable',
                      'diskBytesRead', 'machPortCount', '__suddenTerm', '__arch', 'memRPrvt', 'msgSent', 'ppid',
                      'threadCount', 'memAnon', 'diskBytesWritten', 'pgid', 'faults', 'msgRecv', '__restricted', 'pid',
                      '__sandbox'],  # 输出所有进程信息字段，字段顺序与自定义相同（全量自字段，按需使用）
        'sysAttrs': ['diskWriteOps', 'diskBytesRead', 'diskBytesWritten', 'threadCount', 'vmCompressorPageCount',
                     'vmExtPageCount', 'vmFreeCount', 'vmIntPageCount', 'vmPurgeableCount', 'netPacketsIn',
                     'vmWireCount', 'netBytesIn', 'netPacketsOut', 'diskReadOps', 'vmUsedCount', '__vmSwapUsage',
                     'netBytesOut'],  # 系统信息字段
        'cpuUsage': True,
        'sampleInterval': 1000000000}


#---------------------------------------------------------------
# CLI stuff
#---------------------------------------------------------------

def parse_cli_options(argv=None):
    parser = argparse.ArgumentParser(description='pyPerfIOS - ios instruments client',
                                     add_help=False)
    parser.add_argument('--udid', action='store', dest="udid", default="",
                        help='udid')
    parser.add_argument('--list-targets', action='store_true', dest='list_targets', default=False,
                        help='list-devices')
    parser.add_argument('--list-apps', action='store_true', dest='list_apps', default=False,
                        help='list-apps')
    parser.add_argument('--list-processes', action='store_true', dest='list_processes', default=False,
                        help='list-processes')
    parser.add_argument('-v', '--verbose', action='store_true', dest='verbose', default=False,
                        help='Print debug information')
    parser.add_argument('extra_args', metavar="[args]", nargs=argparse.REMAINDER,
                        help="Additional command-line arguments to be passed to the script.")

    if len(sys.argv) == 2 and sys.argv[1] in ('-h', '--help'):
        parser.print_help()
        sys.exit()
    return parser.parse_known_args(argv)

def run_cli_options(args):
    if args.list_targets:
        usbmux = USBMux()
        usbmux.process()
        print("serial", "\t|", "product type", "\t|", "band version", "\t|", "phone name" )
        for device in usbmux.devices:
            device_info = LockdownClient(udid=device.serial).device_info
            print(device.serial.decode(), "|", device_info["ProductType"], "|", device_info["BasebandVersion"], "|", device_info["DeviceName"])
        return

    if args.list_apps:
        install_proxy = installation_proxy(udid=args.udid)
        appType = ["User", "System"]
        for app in install_proxy.get_apps(appType):
            print(app.get("CFBundleDisplayName"), "|", app.get("CFBundleIdentifier"), "|", app.get("Path") if app.get("Path")
                                      else app.get("Container"))
        return

    rpc = get_usb_rpc(args.udid)
    attr_names = ["pid", "name", "realAppName"]
    try: 
        rpc.start()
        processes = rpc.call("com.apple.instruments.server.services.deviceinfo", "runningProcesses").parsed
    finally:
        rpc.stop()
        rpc.deinit()

    forground = filter(lambda x: x.get("foregroundRunning"), processes)
    application = filter(lambda x: not x.get("foregroundRunning") and x.get("isApplication"), processes)


    if args.list_processes:
        headers = '\t'.join(attr_names)
        print("Forground running:", "    ", headers)
        for item in forground:
            print("    ", '\t'.join([repr(item[n]) for n in attr_names]))
        print("Running Application:", "    ", headers)
        for item in application:
            print("    ", '\t'.join([repr(item[n]) for n in attr_names]))
        return

    print("1.选择一个运行中的应用")

    available_pids = set()
    process_maps = {}
    headers = '\t'.join(attr_names)
    print("Forground running:", "    ", headers)
    for item in forground:
        print("    ", '\t'.join([repr(item[n]) for n in attr_names]))
        available_pids.add(item["pid"])
        process_maps[item["pid"]] = item["name"]
    print("Running Application:", "    ", headers)
    for item in application:
        print("    ", '\t'.join([repr(item[n]) for n in attr_names]))
        available_pids.add(item["pid"])
        process_maps[item["pid"]] = item["name"]

    pid = -1
    while not int(pid) in available_pids:
        pid = int(input("输入pid: "))

    print("2. 您选择了", process_maps[pid], "当前Matrics：[ cpu, mem, fps]")


    timeout = 200
    print("3. 开始执行")


    matrics = [ "cpu", "mem", "fps"]

    class Profiler(object):
        CPU_USAGE = 0
        PSS_MEM = 0
        VIRTUAL_MEM = 0
        FPS = 0
        def on_sysmontap_message(self, res):
            if isinstance(res.parsed, list):
                data = res.parsed
                for d in data:
                    if "Processes" in d:
                        info = d["Processes"].get(pid)
                        if info:
                            self.CPU_USAGE = info[1]
                            self.PSS_MEM = info[12] / 1024.0 / 1024.0
                            self.VIRTUAL_MEM = info[0] / 1024.0 / 1024.0/ 1024.0
                            return
                    self.CPU_USAGE = 0
                    self.PSS_MEM = 0
                    self.VIRTUAL_MEM = 0

        def on_fps_message(self, res):
            data = res.parsed
            self.FPS = data["CoreAnimationFramesPerSecond"]


    
    profiler = Profiler()

    def make_channel(channel):
        if channel == "com.apple.instruments.server.services.sysmontap":
            rpc.call(channel, "setConfig:", SYSMON_CONFIG_MSG)
            rpc.register_channel_callback(channel, profiler.on_sysmontap_message)
        if channel == "com.apple.instruments.server.services.graphics.opengl":
            rpc.register_channel_callback(channel, profiler.on_fps_message)

    def start_channel(channel):
        if channel == "com.apple.instruments.server.services.sysmontap":
            rpc.call("com.apple.instruments.server.services.sysmontap", "start")
        if channel == "com.apple.instruments.server.services.graphics.opengl":
            ret = rpc.call(channel, "startSamplingAtTimeInterval:", 10)
    
    rpc = get_usb_rpc()
    try: 
        pre_call(rpc)
        
        if "fps" in matrics:
            make_channel("com.apple.instruments.server.services.graphics.opengl")
            start_channel("com.apple.instruments.server.services.graphics.opengl")
        if "cpu" in matrics or "mem" in matrics:
            make_channel("com.apple.instruments.server.services.sysmontap")
            start_channel("com.apple.instruments.server.services.sysmontap")
        for i in range(timeout):
            time.sleep(1)
            print(profiler.CPU_USAGE, profiler.PSS_MEM, profiler.VIRTUAL_MEM, profiler.FPS)
    finally:
        rpc.stop()
        rpc.deinit()

    
    return












def main():
    args, more_args = parse_cli_options()
    if args.verbose:
        logging.basicConfig(format='%(asctime)s - %(pathname)s[line:%(lineno)d] - %(levelname)s: %(message)s', level=logging.DEBUG)
    run_cli_options(args)


if "__main__" == __name__:
    sys.exit(main())