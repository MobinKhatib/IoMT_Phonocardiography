# PCG BLE Client

Python client for collecting phonocardiogram (PCG) data from Arduino via BLE.

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

```python
import asyncio
from pcg_ble_client import PCGClient

async def main():
    client = PCGClient(device_name="PCG_Monitor_Raw")
    await client.connect()

    # Collect 60 seconds of data
    async for batch in client.analyze(sample_rate=500, oversample_count=8, batch_size=6,
                               patient_name="John", analysis_time_seconds=60):
        print(f"Got batch: {batch}")

    # Get full accumulated signal
    signal = client.get_full_signal()

    await client.disconnect()

asyncio.run(main())
```

## API

### `PCGClient`

#### `connect()`
Establish BLE connection to Arduino.

#### `disconnect()`
Close BLE connection.

#### `is_connected() -> bool`
Check connection status.

#### `analyze(...) -> Generator[np.ndarray]`
Send analysis request and yield batches.

Parameters:
- `sample_rate` (int): Hz
- `oversample_count` (int): ADC reads per sample
- `batch_size` (int): Samples per BLE packet
- `patient_name` (str): Patient ID (truncated to 15 chars)
- `analysis_time_seconds` (int): Collection duration

Yields: `np.ndarray` of samples (uint16)

#### `get_full_signal() -> np.ndarray`
Return all accumulated samples (validated).

## Running Tests

```bash
python3 -m pytest tests/ -v
```

## Integration Tests

Integration tests require Arduino with `ble_lcd_analyzer.ino` flashed and powered.

```bash
pytest tests/test_integration.py -v -m "asyncio"
```

Note: Tests will skip if Arduino is not available.
