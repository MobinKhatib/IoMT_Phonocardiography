import argparse
import asyncio
import struct

from bleak import BleakClient, BleakScanner

from receiver_common import BATCH_SIZE, PacketStats, validate_packet

DEVICE_NAME = "PCG_Monitor"
CHAR_UUID = "abcd1234-ab12-cd34-ef56-123456789abc"

PACKET_FORMAT = "<BBIH" + ("H" * BATCH_SIZE)
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)

stats = PacketStats()


def handle_notification(sender, data: bytearray):
    del sender

    if len(data) != PACKET_SIZE:
        print(f"Wrong packet size: got {len(data)}, expected {PACKET_SIZE}")
        return

    header1, header2, seq, count, *samples = struct.unpack(PACKET_FORMAT, data)

    packet_error = validate_packet(header1, header2, count)
    if packet_error is not None:
        print(packet_error)
        return

    lost_here = stats.register(seq)
    if lost_here:
        expected_seq = seq - lost_here
        print(f"Missing {lost_here} packet(s): expected {expected_seq}, got {seq}")

    report = stats.build_report(seq, samples)
    if report is not None:
        print(report)


async def main(device_name: str, char_uuid: str, scan_timeout: float):
    print("Scanning for BLE device...")
    devices = await BleakScanner.discover(timeout=scan_timeout)

    target = next((device for device in devices if device.name == device_name), None)
    if target is None:
        print(f"Device '{device_name}' not found.")
        return

    print(f"Found device: {target.name} [{target.address}]")

    async with BleakClient(target.address) as client:
        print("Connected:", client.is_connected)

        await client.start_notify(char_uuid, handle_notification)
        print("Receiving notifications... Press Ctrl+C to stop.")

        while True:
            await asyncio.sleep(1)


def parse_args():
    parser = argparse.ArgumentParser(description="Receive PCG samples over BLE.")
    parser.add_argument("--device-name", default=DEVICE_NAME, help="BLE advertised device name.")
    parser.add_argument("--char-uuid", default=CHAR_UUID, help="BLE characteristic UUID.")
    parser.add_argument("--scan-timeout", type=float, default=5.0, help="BLE scan duration in seconds.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(main(args.device_name, args.char_uuid, args.scan_timeout))
    except KeyboardInterrupt:
        print("\nStopped.")
