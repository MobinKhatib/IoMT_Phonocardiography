import asyncio
from bleak import BleakClient, BleakScanner

async def scan_device():
    print("Scanning for PCG_Monitor_Raw...")
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
    
    print(f"\nConnecting to {pcg_device.name} ({pcg_device.address})...")
    async with BleakClient(pcg_device.address) as client:
        print("Connected!")
        print("\nServices and Characteristics:")
        for service in client.services:
            print(f"\nService: {service.uuid}")
            for char in service.characteristics:
                props = char.properties
                print(f"  Characteristic: {char.uuid}")
                print(f"    Properties: {props}")
                print(f"    Can write: {'write' in props}")
                print(f"    Can notify: {'notify' in props}")

asyncio.run(scan_device())
