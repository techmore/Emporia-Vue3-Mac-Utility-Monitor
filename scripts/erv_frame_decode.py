#!/usr/bin/env python3
"""Decode Broan/Nutone AI-series ERV RS-485 frames captured by the receive-only listener.

Frame layout and checksum follow the community write-up (https://spitko.net/2025/08/08/Reverse-Engineering-an-ERV/),
which its author calls possibly incomplete or wrong: this tool reports what it sees and never assumes.
    01 | to | from | 01 | length | payload... | checksum | 04     (length = payload bytes)
    checksum = (0 - (sum(bytes before it) - 1)) & 0xFF

Usage: erv_frame_decode.py [capture.log]   (reads stdin if no file; accepts ESPHome uart debug lines
or plain hex such as "01:10:11:01:04:40:00:50:00:4a:04")
"""
import re
import sys

KINDS = {0x02: 'ping (ERV)', 0x03: 'ping reply', 0x04: 'flow: offer/release', 0x05: 'flow: ack',
         0x20: 'register read', 0x21: 'register read reply', 0x40: 'register write', 0x41: 'register write ack'}


def checksum(body):
    return (0 - (sum(body) - 1)) & 0xFF


def frames(data):
    """Split a byte string into candidate frames starting at each 0x01 header; yield (bytes, ok, reason)."""
    i = 0
    while i < len(data):
        if data[i] != 0x01 or i + 5 > len(data):
            i += 1
            continue
        length = data[i + 4]
        end = i + 5 + length + 2
        if end > len(data):
            yield bytes(data[i:]), False, 'truncated'
            return
        frame = bytes(data[i:end])
        if frame[3] != 0x01:
            i += 1
            continue
        ok = frame[-1] == 0x04 and frame[-2] == checksum(frame[:-2])
        yield frame, ok, '' if ok else ('bad end byte' if frame[-1] != 0x04 else 'bad checksum')
        i = end


def describe(frame):
    to, sender, length = frame[1], frame[2], frame[4]
    payload = frame[5:5 + length]
    kind = KINDS.get(payload[0], 'unknown 0x%02x' % payload[0]) if payload else 'empty'
    return '%02x -> %02x  %-22s %s' % (sender, to, kind, payload.hex(' '))


def hex_bytes(text):
    found = re.findall(r'(?<![0-9A-Fa-f])((?:[0-9A-Fa-f]{2}[: ]){3,}[0-9A-Fa-f]{2})(?![0-9A-Fa-f])', text)
    out = bytearray()
    for chunk in found:
        out += bytes(int(b, 16) for b in re.findall(r'[0-9A-Fa-f]{2}', chunk))
    return bytes(out)


def main(argv):
    text = open(argv[1]).read() if len(argv) > 1 else sys.stdin.read()
    good = bad = 0
    for frame, ok, reason in frames(hex_bytes(text)):
        good, bad = good + ok, bad + (not ok)
        print(('ok   ' if ok else 'BAD  ') + (describe(frame) if ok else '%s: %s' % (reason, frame.hex(' '))))
    print('%d valid, %d invalid' % (good, bad))
    return 0 if good and not bad else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
