"""
Integration tests with real Arduino.
Requires Arduino flashed with ble_lcd_analyzer.ino and powered on.
"""
import pytest
import asyncio
import numpy as np
from pcg_ble_client import PCGClient, BLEConnectionError

class TestPCGClientIntegration:

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        """Test basic connect/disconnect."""
        client = PCGClient(device_name="PCG_Monitor_Raw")

        try:
            await client.connect()
            assert client.is_connected()

            await client.disconnect()
            assert not client.is_connected()
        except BLEConnectionError as e:
            pytest.skip(f"Arduino not available: {e}")

    @pytest.mark.asyncio
    async def test_analyze_collect_data(self):
        """Test data collection for 5 seconds."""
        client = PCGClient(device_name="PCG_Monitor_Raw")

        try:
            await client.connect()

            batch_count = 0
            async for batch in client.analyze(
                sample_rate=500,
                oversample_count=8,
                batch_size=6,
                patient_name="TestUser",
                analysis_time_seconds=5
            ):
                batch_count += 1
                assert len(batch) == 6
                assert batch.dtype == np.uint16

            signal = client.get_full_signal()

            # Expect ~2500 samples (500 Hz * 5 sec)
            assert 2400 <= len(signal) <= 2600, f"Got {len(signal)} samples"

            await client.disconnect()
        except BLEConnectionError as e:
            pytest.skip(f"Arduino not available: {e}")
