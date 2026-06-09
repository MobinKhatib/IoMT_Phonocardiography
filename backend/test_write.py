import asyncio
import struct
from bleak import BleakClient, BleakScanner

async def test_write():
    scanner = BleakScanner()
    devices = await scanner.discover()
    
    pcg_device = None
    for device in devices:
        if device.name == "PCG_Monitor_Raw":
            pcg_device = device
            break
    
    if not pcg_device:
        print("Device not found!")
        return
    
    async with BleakClient(pcg_device.address) as client:
        print("Connected!")
        
        # Create a test packet (29 bytes)
        packet = bytearray(29)
        packet[0] = 0x01  # START command
        struct.pack_into('<I', packet, 1, 500)      # sample_rate
        struct.pack_into('<H', packet, 5, 8)        # oversample
        struct.pack_into('<H', packet, 7, 6)        # batch_size
        struct.pack_into('<I', packet, 9, 10)       # time (10 sec for quick test)
        packet[13:18] = b"Test\0"  # patient name
        
        print(f"Packet size: {len(packet)} bytes")
        print(f"Packet: {packet.hex()}")
        
        try:
            print("\nTrying write with response=True...")
            await client.write_gatt_char(
                "abcd1234-ab12-cd34-ef56-123456789abc",
                packet,
                response=True
            )
            print("✓ Write succeeded!")
        except Exception as e:
            print(f"✗ Write failed: {e}")
            
            # Try without response
            try:
                print("\nTrying write with response=False...")
                await client.write_gatt_char(
                    "abcd1234-ab12-cd34-ef56-123456789abc",
                    packet,
                    response=False
                )
                print("✓ Write succeeded (no response)!")
            except Exception as e2:
                print(f"✗ Write also failed: {e2}")

asyncio.run(test_write())
