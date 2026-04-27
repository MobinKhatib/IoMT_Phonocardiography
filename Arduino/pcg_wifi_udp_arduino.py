import sys
import struct
import socket
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

# --- Configuration ---
UDP_PORT = 4210
SAMPLE_RATE = 500
BATCH_SIZE = 20
MAGIC = 0x5043  # must match Arduino
PACKET_SIZE = 8 + BATCH_SIZE * 2  # header (8 bytes) + samples (40 bytes) = 48

# --- Audio Configuration ---
AUDIO_SAMPLE_RATE = 8000
AUDIO_GAIN = 2.0

# --- Re-clock timer ---
TIMER_INTERVAL_MS = 20
SAMPLES_PER_TICK = SAMPLE_RATE * TIMER_INTERVAL_MS // 1000  # 10 samples


class UDPWorker(QThread):
    new_batch = pyqtSignal(list)
    connection_status = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.last_seq = None
        self.lost_packets = 0

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1.0)

        try:
            sock.bind(("", UDP_PORT))
        except OSError as e:
            self.connection_status.emit(f"error: {e}")
            print(f"[UDP] Bind failed: {e}")
            return

        self.connection_status.emit("listening")
        print(f"[UDP] Listening on port {UDP_PORT}")

        packet_count = 0
        got_first = False

        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                if got_first:
                    self.connection_status.emit("waiting (no data)")
                continue
            except Exception as e:
                print(f"[UDP] recv error: {e}")
                continue

            if len(data) != PACKET_SIZE:
                continue  # ignore stray packets

            magic, seq, count = struct.unpack("<HIH", data[:8])
            if magic != MAGIC or count != BATCH_SIZE:
                continue

            if not got_first:
                got_first = True
                self.connection_status.emit(f"connected ({addr[0]})")
                print(f"[UDP] First packet from {addr[0]}")

            # Track packet loss
            if self.last_seq is not None:
                expected = (self.last_seq + 1) & 0xFFFFFFFF
                if seq != expected:
                    gap = (seq - expected) & 0xFFFFFFFF
                    if gap < 1000:  # ignore wrap-around / restart
                        self.lost_packets += gap
            self.last_seq = seq

            samples = list(struct.unpack(f"<{BATCH_SIZE}H", data[8:]))
            packet_count += 1
            if packet_count <= 5 or packet_count % 100 == 0:
                print(f"[UDP] Packet #{packet_count} seq={seq} first={samples[0]} lost_total={self.lost_packets}")

            self.new_batch.emit(samples)

        sock.close()

    def stop(self):
        self.running = False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Real-Time PCG Monitor (Wi-Fi)")
        self.resize(800, 450)

        self.is_recording = False
        self.recorded_data = []

        self.display_buffer_size = 2000
        self.plot_data = np.zeros(self.display_buffer_size)

        # Re-clocking FIFO
        self.sample_fifo = collections.deque(maxlen=SAMPLE_RATE * 4)
        self._total_received = 0
        self._total_processed = 0

        self.reclock_timer = QTimer()
        self.reclock_timer.setTimerType(Qt.PreciseTimer)
        self.reclock_timer.timeout.connect(self._drain_samples)
        self.reclock_timer.start(TIMER_INTERVAL_MS)

        # Bandpass filter (20–200 Hz)
        nyq = SAMPLE_RATE / 2.0
        low = 20.0 / nyq
        high = 200.0 / nyq
        self.bp_b, self.bp_a = butter(4, [low, high], btype='band')
        self.bp_zi = lfilter_zi(self.bp_b, self.bp_a) * 2048.0

        self.last_audio_val = 0.0

        # Audio
        self.audio_queue = queue.Queue(maxsize=AUDIO_SAMPLE_RATE * 2)
        self.audio_stream = sd.OutputStream(
            samplerate=AUDIO_SAMPLE_RATE,
            channels=1,
            dtype='float32',
            callback=self.audio_callback
        )
        self.audio_stream.start()

        # UI
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

        # UDP thread
        self.udp_thread = UDPWorker()
        self.udp_thread.new_batch.connect(self._enqueue_batch)
        self.udp_thread.connection_status.connect(self.update_status)
        self.udp_thread.start()
        print("[APP] Started. Waiting for UDP data...")

    def _enqueue_batch(self, values):
        for v in values:
            self.sample_fifo.append(v)
        self._total_received += len(values)

    def _drain_samples(self):
        count = min(SAMPLES_PER_TICK, len(self.sample_fifo))
        for _ in range(count):
            value = self.sample_fifo.popleft()
            self.process_sample(value)
            self._total_processed += 1

        if self._total_processed > 0 and self._total_processed % 2500 == 0:
            fifo = len(self.sample_fifo)
            print(f"[DRAIN] processed={self._total_processed}, received={self._total_received}, fifo={fifo}, lost={self.udp_thread.lost_packets}")

    def update_status(self, status):
        style = "font-weight: bold; padding: 4px; "
        if status.startswith("error"):
            style += "color: red;"
        elif status.startswith("connected"):
            style += "color: green;"
        elif status.startswith("listening"):
            style += "color: blue;"
        else:
            style += "color: orange;"
        self.status_label.setStyleSheet(style)
        fifo_len = len(self.sample_fifo)
        self.status_label.setText(f"Wi-Fi: {status}  |  buffer: {fifo_len}  |  lost: {self.udp_thread.lost_packets}")

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

        # Bandpass 20–200 Hz
        filtered, self.bp_zi = lfilter(
            self.bp_b, self.bp_a, [float(value)], zi=self.bp_zi
        )
        bp_val = filtered[0]

        # Normalize
        norm_val = (bp_val / 300.0) * AUDIO_GAIN
        norm_val = np.clip(norm_val, -1.0, 1.0)

        # Linear-interpolation upsample to audio rate
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
        self.udp_thread.stop()
        self.udp_thread.wait(5000)
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())