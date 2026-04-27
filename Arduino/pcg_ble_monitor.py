import sys
import struct
import asyncio
import numpy as np
import pyqtgraph as pg
import queue
import collections
import sounddevice as sd
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QHBoxLayout, QLabel
from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal
from scipy.io import wavfile
from scipy.signal import butter, lfilter_zi, lfilter
from datetime import datetime
from bleak import BleakClient, BleakScanner

# --- Configuration ---
BLE_DEVICE_NAME = "PCG_Monitor"
SERVICE_UUID = "12345678-1234-1234-1234-123456789abc"
CHARACTERISTIC_UUID = "abcd1234-ab12-cd34-ef56-123456789abc"
SAMPLE_RATE = 500
BATCH_SIZE = 20

# --- Audio Configuration ---
AUDIO_SAMPLE_RATE = 8000
AUDIO_GAIN = 2.0

# --- Re-clock timer ---
# We fire the timer at ~50 Hz (every 20ms) and drain up to 10 samples per tick.
# This is much more reliable than a 2ms timer which many OS schedulers can't hit.
TIMER_INTERVAL_MS = 20
SAMPLES_PER_TICK = SAMPLE_RATE * TIMER_INTERVAL_MS // 1000  # 10 samples


class BLEWorker(QThread):
    new_batch = pyqtSignal(list)
    connection_status = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
        asyncio.run(self._ble_loop())

    async def _ble_loop(self):
        while self.running:
            try:
                self.connection_status.emit("scanning")
                print("[BLE] Scanning for device...")

                target = None
                while target is None and self.running:
                    devices = await BleakScanner.discover(timeout=5.0)
                    target = next((d for d in devices if d.name == BLE_DEVICE_NAME), None)
                    if target is None:
                        print(f"[BLE] '{BLE_DEVICE_NAME}' not found, retrying...")
                        await asyncio.sleep(1)

                if not self.running:
                    return

                print(f"[BLE] Found device: {target.name} [{target.address}]")

                async with BleakClient(target.address) as client:
                    self.connection_status.emit("connected")
                    print(f"[BLE] Connected: {client.is_connected}")

                    self._notify_count = 0

                    def on_notify(_handle, data: bytearray):
                        n_samples = len(data) // 2
                        values = list(struct.unpack(f"<{n_samples}H", data))
                        self._notify_count += 1
                        if self._notify_count <= 5 or self._notify_count % 100 == 0:
                            print(f"[BLE] Notification #{self._notify_count}: {n_samples} samples, first={values[0]}")
                        self.new_batch.emit(values)

                    await client.start_notify(CHARACTERISTIC_UUID, on_notify)
                    print("[BLE] Receiving notifications...")

                    while self.running and client.is_connected:
                        await asyncio.sleep(1)

                    try:
                        await client.stop_notify(CHARACTERISTIC_UUID)
                    except Exception:
                        pass

                self.connection_status.emit("disconnected")
                print("[BLE] Disconnected")

            except Exception as e:
                self.connection_status.emit(f"error: {e}")
                print(f"[BLE] Error: {e}")
                await asyncio.sleep(2)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Real-Time PCG Monitor (BLE v3)")
        self.resize(800, 450)

        self.is_recording = False
        self.recorded_data = []

        self.display_buffer_size = 2000
        self.plot_data = np.zeros(self.display_buffer_size)

        # ==============================================
        # RE-CLOCKING BUFFER
        # ==============================================
        # BLE batches arrive with jitter. We park samples in a FIFO
        # and drain at a steady rate to eliminate batch-boundary noise.
        self.sample_fifo = collections.deque(maxlen=SAMPLE_RATE * 4)
        self._total_received = 0
        self._total_processed = 0

        # Timer fires every 20ms, drains 10 samples per tick (= 500 Hz effective)
        self.reclock_timer = QTimer()
        self.reclock_timer.setTimerType(Qt.PreciseTimer)
        self.reclock_timer.timeout.connect(self._drain_samples)
        self.reclock_timer.start(TIMER_INTERVAL_MS)

        # ==============================================
        # BANDPASS FILTER
        # ==============================================
        nyq = SAMPLE_RATE / 2.0
        low = 20.0 / nyq
        high = 200.0 / nyq
        self.bp_b, self.bp_a = butter(4, [low, high], btype='band')
        self.bp_zi = lfilter_zi(self.bp_b, self.bp_a) * 2048.0

        self.last_audio_val = 0.0

        # --- Audio Setup ---
        self.audio_queue = queue.Queue(maxsize=AUDIO_SAMPLE_RATE * 2)
        self.audio_stream = sd.OutputStream(
            samplerate=AUDIO_SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=self.audio_callback
        )
        self.audio_stream.start()

        # UI Layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        self.status_label = QLabel("Status: starting...")
        self.status_label.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(self.status_label)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setYRange(0, 4096)
        self.plot_widget.setTitle("Phonocardiogram (PCG) Signal")
        self.curve = self.plot_widget.plot(self.plot_data, pen='y')
        layout.addWidget(self.plot_widget)

        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("Start Recording")
        self.btn_stop = QPushButton("Stop & Save WAV")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.start_recording)
        self.btn_stop.clicked.connect(self.stop_recording)
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        layout.addLayout(btn_layout)

        # Start BLE Thread
        self.ble_thread = BLEWorker()
        self.ble_thread.new_batch.connect(self._enqueue_batch)
        self.ble_thread.connection_status.connect(self.update_status)
        self.ble_thread.start()
        print("[APP] Started. Waiting for BLE data...")

    def _enqueue_batch(self, values):
        """Park BLE samples in the FIFO (don't process yet)."""
        for v in values:
            self.sample_fifo.append(v)
        self._total_received += len(values)

    def _drain_samples(self):
        """Called every 20ms. Drain a fixed number of samples from the FIFO."""
        count = min(SAMPLES_PER_TICK, len(self.sample_fifo))
        for _ in range(count):
            value = self.sample_fifo.popleft()
            self.process_sample(value)
            self._total_processed += 1

        # Periodic debug
        if self._total_processed > 0 and self._total_processed % 2500 == 0:
            fifo = len(self.sample_fifo)
            print(f"[DRAIN] processed={self._total_processed}, received={self._total_received}, fifo={fifo}")

    def update_status(self, status):
        colors = {
            "connected": "color: green;",
            "disconnected": "color: orange;",
            "scanning": "color: blue;",
        }
        style = "font-weight: bold; padding: 4px; "
        if status.startswith("error"):
            style += "color: red;"
        else:
            style += colors.get(status, "")
        self.status_label.setStyleSheet(style)
        fifo_len = len(self.sample_fifo)
        self.status_label.setText(f"BLE: {status}  |  buffer: {fifo_len}")

    def audio_callback(self, outdata, frames, time, status):
        chunk = np.zeros((frames, 1), dtype=np.float32)
        for i in range(frames):
            try:
                chunk[i, 0] = self.audio_queue.get_nowait()
            except queue.Empty:
                break
        outdata[:] = chunk

    def process_sample(self, value):
        self.plot_data[:-1] = self.plot_data[1:]
        self.plot_data[-1] = value
        self.curve.setData(self.plot_data)

        if self.is_recording:
            self.recorded_data.append(value)

        # --- Bandpass filter (20-200 Hz) ---
        filtered, self.bp_zi = lfilter(
            self.bp_b, self.bp_a, [float(value)], zi=self.bp_zi
        )
        bp_val = filtered[0]

        # Normalize
        norm_val = (bp_val / 300.0) * AUDIO_GAIN
        norm_val = np.clip(norm_val, -1.0, 1.0)

        # Upsample with linear interpolation
        upsample_factor = AUDIO_SAMPLE_RATE // SAMPLE_RATE
        step = (norm_val - self.last_audio_val) / upsample_factor
        for i in range(upsample_factor):
            interp_val = self.last_audio_val + step * (i + 1)
            if not self.audio_queue.full():
                self.audio_queue.put_nowait(interp_val)
        self.last_audio_val = norm_val

    def start_recording(self):
        self.is_recording = True
        self.recorded_data = []
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def stop_recording(self):
        self.is_recording = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.save_to_wav()

    def save_to_wav(self):
        if not self.recorded_data:
            return
        raw_signal = np.array(self.recorded_data, dtype=np.float32)
        centered_signal = raw_signal - np.mean(raw_signal)
        normalized_signal = np.int16(centered_signal * 15)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"PCG_Record_{timestamp}.wav"
        wavfile.write(filename, SAMPLE_RATE, normalized_signal)
        print(f"[SAVE] {filename}")

    def closeEvent(self, event):
        self.reclock_timer.stop()
        self.audio_stream.stop()
        self.audio_stream.close()
        self.ble_thread.stop()
        self.ble_thread.wait(timeout=5000)
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
