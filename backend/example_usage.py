"""
Example: Collect phonocardiogram data using PCGClient.
"""
import asyncio
import numpy as np
from pcg_ble_client import PCGClient

async def main():
    # Initialize client
    client = PCGClient(device_name="PCG_Monitor_Raw")

    try:
        # Connect to Arduino
        print("Connecting to device...")
        await client.connect()

        if not client.is_connected():
            print("Failed to connect")
            return

        print("Connected! Starting analysis...")

        # Collect 60 seconds of data at 500 Hz
        batch_count = 0
        async for batch in client.analyze(
            sample_rate=500,
            oversample_count=8,
            batch_size=6,
            patient_name="Test Patient",
            analysis_time_seconds=60
        ):
            batch_count += 1
            print(f"Batch {batch_count}: {len(batch)} samples")

        print("Analysis complete. Getting full signal...")

        # Retrieve accumulated signal
        full_signal = client.get_full_signal()
        print(f"Received {len(full_signal)} total samples")

        # Pass to analyzer (placeholder)
        print(f"Signal shape: {full_signal.shape}, dtype: {full_signal.dtype}")
        print(f"Min: {full_signal.min()}, Max: {full_signal.max()}")

    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
