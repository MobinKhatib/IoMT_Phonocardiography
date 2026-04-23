import argparse
import struct

import serial

from receiver_common import BATCH_SIZE, PacketStats, validate_packet

PORT = "/dev/cu.usbmodemE4B063ADCF0C2"
BAUD = 460800

PACKET_FORMAT = "<BBIH" + ("H" * BATCH_SIZE)
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)


def parse_args():
    parser = argparse.ArgumentParser(description="Receive PCG samples over serial.")
    parser.add_argument("--port", default=PORT, help="Serial device path.")
    parser.add_argument("--baud", type=int, default=BAUD, help="Serial baud rate.")
    parser.add_argument("--timeout", type=float, default=1.0, help="Read timeout in seconds.")
    return parser.parse_args()


def main():
    args = parse_args()
    stats = PacketStats()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=args.timeout)
    except serial.SerialException as exc:
        raise SystemExit(f"Failed to open serial port {args.port}: {exc}") from exc

    try:
        while True:
            b1 = ser.read(1)
            if not b1 or b1[0] != 0xAA:
                continue

            b2 = ser.read(1)
            if not b2 or b2[0] != 0x55:
                continue

            rest = ser.read(PACKET_SIZE - 2)
            if len(rest) != PACKET_SIZE - 2:
                continue

            packet = bytes([0xAA, 0x55]) + rest
            header1, header2, seq, count, *samples = struct.unpack(PACKET_FORMAT, packet)

            packet_error = validate_packet(header1, header2, count)
            if packet_error is not None:
                print(packet_error)
                continue

            lost_here = stats.register(seq)
            if lost_here:
                expected_seq = seq - lost_here
                print(f"Missing {lost_here} packet(s): expected {expected_seq}, got {seq}")

            report = stats.build_report(seq, samples)
            if report is not None:
                print(report)

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
