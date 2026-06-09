import asyncio
import struct
import time
import numpy as np
from bleak import BleakClient, BleakScanner
from typing import Generator

class BLEConnectionError(Exception):
    """Raised when BLE connection fails or drops."""
    pass

class PCGClient:
    """
    Client for controlling Arduino PCG data collection via BLE.
    Sends analysis requests and receives phonocardiogram signal batches.
    """

    SERVICE_UUID = "12345678-1234-1234-1234-123456789abc"
    CHARACTERISTIC_UUID = "abcd1234-ab12-cd34-ef56-123456789abc"

    def __init__(self, device_name="PCG_Monitor_Raw"):
        self.device_name = device_name
        self.client = None
        self._sample_rate = 0
        self._analysis_time_seconds = 0
        self._accumulated_data = []
        self._batch_queue = asyncio.Queue()

    async def connect(self):
        """Establish BLE connection to Arduino."""
        pass

    async def disconnect(self):
        """Close BLE connection."""
        pass

    def is_connected(self) -> bool:
        """Return True if BLE connection is active."""
        pass

    async def analyze(self, sample_rate: int, oversample_count: int, batch_size: int,
                     patient_name: str, analysis_time_seconds: int) -> Generator:
        """
        Send analysis request and yield batches as they arrive.
        Generator exits when Arduino finishes collection.
        """
        pass

    def get_full_signal(self) -> np.ndarray:
        """Return all accumulated samples, validated to expected count."""
        pass

    def _encode_start_packet(self, sample_rate: int, oversample_count: int, batch_size: int,
                            analysis_time_seconds: int, patient_name: str) -> bytes:
        """
        Encode binary START packet:
        Byte 0:        Command type (0x01)
        Bytes 1-4:     SAMPLE_RATE (uint32_t, little-endian)
        Bytes 5-6:     OVERSAMPLE_COUNT (uint16_t, little-endian)
        Bytes 7-8:     BATCH_SIZE (uint16_t, little-endian)
        Bytes 9-12:    ANALYSIS_TIME_SECONDS (uint32_t, little-endian)
        Bytes 13-28:   Patient name (null-terminated, max 16 bytes)
        """
        # Truncate patient name to 15 chars (16 bytes with null terminator)
        truncated_name = patient_name[:15].encode('utf-8')

        # Build packet
        packet = bytearray(29)  # Fixed size: 1 + 4 + 2 + 2 + 4 + 16

        packet[0] = 0x01  # START command
        struct.pack_into('<I', packet, 1, sample_rate)
        struct.pack_into('<H', packet, 5, oversample_count)
        struct.pack_into('<H', packet, 7, batch_size)
        struct.pack_into('<I', packet, 9, analysis_time_seconds)

        # Copy patient name (null-padded)
        packet[13:13+len(truncated_name)] = truncated_name
        # Rest is zeros (null padding)

        return bytes(packet)

    async def _notification_handler(self, sender, data: bytearray):
        """BLE notification callback: parse batch and queue it."""
        pass
