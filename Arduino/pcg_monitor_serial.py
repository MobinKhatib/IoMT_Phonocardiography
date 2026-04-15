import sys
import serial
import numpy as np
import pyqtgraph as pg
import queue
import sounddevice as sd
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QHBoxLayout
from PyQt5.QtCore import QThread, pyqtSignal
from scipy.io import wavfile
from datetime import datetime

# --- Configuration ---
SERIAL_PORT = '/dev/ttyACM0'  # Update this to match your Arduino's COM port
BAUD_RATE = 115200
SAMPLE_RATE = 500     

# --- Audio Configuration ---
AUDIO_SAMPLE_RATE = 8000 
AUDIO_GAIN = 2.0  # Adjusted default gain since filtering changes the amplitude

class SerialWorker(QThread):
    new_data = pyqtSignal(int)

    def __init__(self, port, baud):
        super().__init__()
        self.port = port
        self.baud = baud
        self.running = True
        self.serial_conn = None

    def run(self):
        try:
            self.serial_conn = serial.Serial(self.port, self.baud, timeout=1)
            while self.running:
                if self.serial_conn.in_waiting:
                    line = self.serial_conn.readline().decode('utf-8').strip()
                    if line:
                        try:
                            value = int(line)
                            self.new_data.emit(value)
                        except ValueError:
                            pass 
        except Exception as e:
            print(f"Serial Error: {e}")

    def stop(self):
        self.running = False
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Real-Time PCG Monitor & DSP Audio")
        self.resize(800, 400)

        # Signal Data Storage
        self.is_recording = False
        self.recorded_data = []
        
        # Display buffer
        self.display_buffer_size = 2000
        self.plot_data = np.zeros(self.display_buffer_size)

        # --- Digital Filter States ---
        self.lp_val = 2048.0        # Low-pass state
        self.dc_prev_x = 2048.0     # DC-blocker input state
        self.dc_prev_y = 0.0        # DC-blocker output state
        self.last_audio_val = 0.0   # Interpolation state

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

        # Plot Setup
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setYRange(0, 4096)
        self.plot_widget.setTitle("Phonocardiogram (PCG) Signal")
        self.curve = self.plot_widget.plot(self.plot_data, pen='y')
        layout.addWidget(self.plot_widget)

        # Buttons Setup
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("Start Recording")
        self.btn_stop = QPushButton("Stop & Save WAV")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.start_recording)
        self.btn_stop.clicked.connect(self.stop_recording)
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        layout.addLayout(btn_layout)

        # Start Serial Thread
        self.serial_thread = SerialWorker(SERIAL_PORT, BAUD_RATE)
        self.serial_thread.new_data.connect(self.update_plot)
        self.serial_thread.start()

    def audio_callback(self, outdata, frames, time, status):
        chunk = np.zeros((frames, 1), dtype=np.float32)
        for i in range(frames):
            try:
                chunk[i, 0] = self.audio_queue.get_nowait()
            except queue.Empty:
                break 
        outdata[:] = chunk

    def update_plot(self, value):
        # Update Plot Visuals (Unfiltered raw data)
        self.plot_data[:-1] = self.plot_data[1:]
        self.plot_data[-1] = value
        self.curve.setData(self.plot_data)

        if self.is_recording:
            self.recorded_data.append(value)

        # ==========================================
        # SIGNAL PROCESSING FOR AUDIO
        # ==========================================

        # 1. Low-Pass Filter (Removes high-pitch sensor hiss)
        # alpha controls smoothing. Lower = smoother. Heart sounds are very low frequency.
        alpha_lp = 0.25 
        self.lp_val = alpha_lp * value + (1 - alpha_lp) * self.lp_val

        # 2. DC-Blocking Filter (Removes baseline wander & centers at 0)
        # R is the roll-off factor. 0.99 is standard for tight centering.
        R = 0.99 
        dc_val = self.lp_val - self.dc_prev_x + R * self.dc_prev_y
        self.dc_prev_x = self.lp_val
        self.dc_prev_y = dc_val

        # Normalize the centered signal for audio (-1.0 to 1.0)
        # Assuming typical heart sound deflections are around +/- 300 ADC units
        norm_val = (dc_val / 300.0) * AUDIO_GAIN
        norm_val = np.clip(norm_val, -1.0, 1.0)

        # 3. Linear Interpolation Upsampling (Removes "Staircase" buzzing)
        upsample_factor = AUDIO_SAMPLE_RATE // SAMPLE_RATE
        step = (norm_val - self.last_audio_val) / upsample_factor
        
        for i in range(upsample_factor):
            # Connect the dots smoothly instead of repeating the same value
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
        if not self.recorded_data: return
        raw_signal = np.array(self.recorded_data, dtype=np.float32)
        centered_signal = raw_signal - np.mean(raw_signal) # Better centering for saving
        normalized_signal = np.int16(centered_signal * 15) 
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"PCG_Record_{timestamp}.wav"
        wavfile.write(filename, SAMPLE_RATE, normalized_signal)
        print(f"Saved successfully as {filename}")

    def closeEvent(self, event):
        self.audio_stream.stop()
        self.audio_stream.close()
        self.serial_thread.stop()
        self.serial_thread.wait()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())