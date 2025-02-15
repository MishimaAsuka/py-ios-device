import logging
import sys

gettrace = getattr(sys, 'gettrace', None)


def hexdump(d):
    for i in range(0, len(d), 16):
        data = d[i:i + 16]
        print("%08X | %s | %s" % (i, hex(data).ljust(47), ascii(data)))


def read_file(filename):
    f = open(filename, "rb")
    data = f.read()
    f.close()
    return data
