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
DISCOVERY_PORT = 4211
SAMPLE_RATE = 500
BATCH_SIZE = 20
MAGIC = 0x5043
PACKET_SIZE = 8 + BATCH_SIZE * 2  # 48 bytes

# --- Audio Configuration ---
AUDIO_SAMPLE_RATE = 8000
AUDIO_GAIN = 2.0

# --- Re-clock timer ---
TIMER_INTERVAL_MS = 20
SAMPLES_PER_TICK = SAMPLE_RATE * TIMER_INTERVAL_MS // 1000


class TCPWorker(QThread):
    new_batch = pyqtSignal(list)
    connection_status = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.last_seq = None
        self.lost_packets = 0

    def run(self):
        while self.running:
            # 1. Discover ESP32 via UDP beacon
            esp_ip, esp_port = self._discover()
            if esp_ip is None:
                continue  # stopped or timed out — outer loop handles retry

            # 2. Connect via TCP and stream
            self._stream(esp_ip, esp_port)

    def _discover(self):
        """Listen for the ESP32's UDP beacon. Returns (ip, port) or (None, None)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError as e:
            self.connection_status.emit(f"error: {e}")
            print(f"[DISC] Bind failed: {e}")
            sock.close()
            self._sleep_interruptible(2.0)
            return None, None

        self.connection_status.emit("searching for ESP32...")
        print(f"[DISC] Listening for beacon on UDP {DISCOVERY_PORT}")

        try:
            while self.running:
                try:
                    data, addr = sock.recvfrom(128)
                except socket.timeout:
                    continue

                try:
                    text = data.decode("ascii", errors="ignore")
                except Exception:
                    continue

                if not text.startswith("PCG_Monitor:"):
                    continue

                try:
                    port = int(text.split(":", 1)[1])
                except ValueError:
                    continue

                print(f"[DISC] Found ESP32 at {addr[0]}:{port}")
                return addr[0], port
        finally:
            sock.close()

        return None, None

    def _stream(self, esp_ip, esp_port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)

        try:
            self.connection_status.emit(f"connecting to {esp_ip}")
            print(f"[TCP] Connecting to {esp_ip}:{esp_port}...")
            sock.connect((esp_ip, esp_port))
        except (socket.timeout, OSError) as e:
            self.connection_status.emit(f"error: {e}")
            print(f"[TCP] Connect failed: {e}")
            sock.close()
            self._sleep_interruptible(2.0)
            return

        sock.settimeout(2.0)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection_status.emit(f"connected ({esp_ip})")
        print(f"[TCP] Connected to {esp_ip}")

        packet_count = 0
        try:
            while self.running:
                data = self._recv_exact(sock, PACKET_SIZE)
                if data is None:
                    break

                magic, seq, count = struct.unpack("<HIH", data[:8])
                if magic != MAGIC or count != BATCH_SIZE:
                    print(f"[TCP] Bad header: magic={magic:#x} count={count} — resyncing")
                    if not self._resync(sock):
                        break
                    continue

                if self.last_seq is not None:
                    expected = (self.last_seq + 1) & 0xFFFFFFFF
                    if seq != expected:
                        gap = (seq - expected) & 0xFFFFFFFF
                        if gap < 1000:
                            self.lost_packets += gap
                self.last_seq = seq

                samples = list(struct.unpack(f"<{BATCH_SIZE}H", data[8:]))
                packet_count += 1
                if packet_count <= 5 or packet_count % 100 == 0:
                    print(f"[TCP] Packet #{packet_count} seq={seq} first={samples[0]}")

                self.new_batch.emit(samples)
        except Exception as e:
            print(f"[TCP] Error during recv: {e}")

        sock.close()
        self.connection_status.emit("disconnected")
        print("[TCP] Disconnected, will rediscover...")
        self.last_seq = None
        self._sleep_interruptible(1.0)

    def _recv_exact(self, sock, n):
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
            except socket.timeout:
                if not self.running:
                    return None
                continue
            except OSError:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _resync(self, sock):
        magic_lo = MAGIC & 0xFF
        magic_hi = (MAGIC >> 8) & 0xFF
        prev = 0
        for _ in range(PACKET_SIZE * 4):
            byte = self._recv_exact(sock, 1)
            if byte is None:
                return False
            if prev == magic_lo and byte[0] == magic_hi:
                rest = self._recv_exact(sock, PACKET_SIZE - 2)
                return rest is not None
            prev = byte[0]
        return False

    def _sleep_interruptible(self, seconds):
        slept = 0.0
        while slept < seconds and self.running:
            self.msleep(100)
            slept += 0.1

    def stop(self):
        self.running = False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Real-Time PCG Monitor (Wi-Fi / TCP)")
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

        # TCP thread
        self.tcp_thread = TCPWorker()
        self.tcp_thread.new_batch.connect(self._enqueue_batch)
        self.tcp_thread.connection_status.connect(self.update_status)
        self.tcp_thread.start()
        print("[APP] Started. Searching for ESP32...")

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
            print(f"[DRAIN] processed={self._total_processed}, received={self._total_received}, fifo={fifo}")

    def update_status(self, status):
        style = "font-weight: bold; padding: 4px; "
        if status.startswith("error"):
            style += "color: red;"
        elif status.startswith("connected"):
            style += "color: green;"
        elif status.startswith("connecting") or status.startswith("searching"):
            style += "color: blue;"
        else:
            style += "color: orange;"
        self.status_label.setStyleSheet(style)
        fifo_len = len(self.sample_fifo)
        self.status_label.setText(f"TCP: {status}  |  buffer: {fifo_len}")

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
        self.tcp_thread.stop()
        self.tcp_thread.wait(5000)
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())