import pytest
import struct
import asyncio
import numpy as np
from pcg_ble_client import PCGClient

def test_encode_start_packet():
    """Test binary packet encoding."""
    client = PCGClient()

    packet = client._encode_start_packet(
        sample_rate=500,
        oversample_count=8,
        batch_size=6,
        analysis_time_seconds=60,
        patient_name="Alice"
    )

    assert len(packet) == 29
    assert packet[0] == 0x01  # Command type

    # Verify sample rate (little-endian)
    sr = struct.unpack('<I', packet[1:5])[0]
    assert sr == 500

    # Verify oversample count
    os = struct.unpack('<H', packet[5:7])[0]
    assert os == 8

    # Verify batch size
    bs = struct.unpack('<H', packet[7:9])[0]
    assert bs == 6

    # Verify time
    t = struct.unpack('<I', packet[9:13])[0]
    assert t == 60

    # Verify patient name
    name = packet[13:13+5].decode('utf-8')
    assert name == "Alice"

def test_encode_truncates_long_name():
    """Test that patient name is truncated to 15 chars."""
    client = PCGClient()

    long_name = "A" * 20
    packet = client._encode_start_packet(500, 8, 6, 60, long_name)

    # Extract name part
    name_section = packet[13:29]
    name_str = name_section.split(b'\x00')[0].decode('utf-8')
    assert len(name_str) == 15
    assert name_str == "A" * 15

def test_notification_handler_parsing():
    """Test that notification handler correctly parses batches."""
    async def test_coro():
        client = PCGClient()

        # Simulate 6-sample batch as bytearray
        samples = np.array([100, 200, 300, 400, 500, 600], dtype=np.uint16)
        data = samples.tobytes()

        await client._notification_handler(None, bytearray(data))

        assert len(client._accumulated_data) == 6
        assert client._accumulated_data == [100, 200, 300, 400, 500, 600]

    asyncio.run(test_coro())

def test_get_full_signal_validation():
    """Test that get_full_signal returns correct sample count."""
    client = PCGClient()

    # Simulate collected data
    client._sample_rate = 500
    client._analysis_time_seconds = 10
    client._accumulated_data = list(range(5000))  # Exactly 500*10 samples

    signal = client.get_full_signal()

    assert len(signal) == 5000
    assert signal.dtype == np.uint16
    assert signal[0] == 0
    assert signal[4999] == 4999

def test_get_full_signal_trims_excess():
    """Test trimming when more samples than expected."""
    client = PCGClient()

    client._sample_rate = 500
    client._analysis_time_seconds = 10
    client._accumulated_data = list(range(5100))  # 100 extra

    signal = client.get_full_signal()

    assert len(signal) == 5000  # Trimmed
