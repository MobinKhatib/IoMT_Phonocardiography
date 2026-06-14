import sys
import serial
import numpy as np
from PyQt6 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg
from scipy.signal import butter, lfilter, find_peaks

class KalmanFilter:
    def __init__(self, q=0.001, r=0.1):
        self.q = q  # Process noise
        self.r = r  # Measurement noise
        self.p = 1.0
        self.x = 0
    def update(self, measurement):
        self.p = self.p + self.q
        k = self.p / (self.p + self.r)
        self.x = self.x + k * (measurement - self.x)
        self.p = (1 - k) * self.p
        return self.x

class AdvancedHeartMonitor(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.fs = 250  
        self.raw_data = np.zeros(1000)
        self.filter_mode = "Bandpass"
        self.kf = KalmanFilter(q=0.01, r=0.5)

        self.initUI()
        
        try:
            self.ser = serial.Serial('COM7', 115200, timeout=0.01)
        except:
            self.ser = serial.Serial('COM7', 115200, timeout=0.01)

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_all)
        self.timer.start(20) 

    def initUI(self):
        self.setWindowTitle("Ubuntu PCG Analyzer - Nano ESP32")
        self.central_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QtWidgets.QHBoxLayout(self.central_widget)

        # Control Sidebar
        self.sidebar = QtWidgets.QVBoxLayout()
        self.sidebar.addWidget(QtWidgets.QLabel("<b>SIGNAL PROCESSING</b>"))
        
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(["Bandpass (20-100Hz)", "Kalman Filter", "Raw"])
        self.filter_combo.currentTextChanged.connect(self.set_filter)
        self.sidebar.addWidget(self.filter_combo)

        self.sidebar.addStretch()

        # BPM Display
        self.bpm_label = QtWidgets.QLabel("00")
        self.bpm_label.setStyleSheet("font-size: 60px; color: #00FF00; font-family: monospace;")
        self.sidebar.addWidget(QtWidgets.QLabel("HEART RATE (BPM)"))
        self.sidebar.addWidget(self.bpm_label)
        
        self.main_layout.addLayout(self.sidebar, 1)

        # Plot
        self.plot_widget = pg.PlotWidget()
        self.curve = self.plot_widget.plot(pen=pg.mkPen(color='#00ff00', width=2))
        self.main_layout.addWidget(self.plot_widget, 4)

    def set_filter(self, text):
        self.filter_mode = text

    def apply_filters(self, data):
        centered = data - np.mean(data)
        if "Bandpass" in self.filter_mode:
            nyq = 0.5 * self.fs
            b, a = butter(4, [20/nyq, 100/nyq], btype='band')
            return lfilter(b, a, centered)
        elif "Kalman" in self.filter_mode:
            return np.array([self.kf.update(x) for x in centered])
        return centered

    def update_all(self):
        try:
            while self.ser.in_waiting > 0:
                line = self.ser.readline().decode('utf-8').strip()
                if line:
                    self.raw_data = np.roll(self.raw_data, -1)
                    self.raw_data[-1] = float(line)
        except: pass

        processed = self.apply_filters(self.raw_data)
        self.curve.setData(processed)
        
        # Simple Peak Detection for BPM
        peaks, _ = find_peaks(np.abs(processed), height=np.max(np.abs(processed))*0.6, distance=self.fs/2)
        if len(peaks) >= 2:
            bpm = 60 / (np.mean(np.diff(peaks)) / self.fs)
            self.bpm_label.setText(f"{int(bpm)}")

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = AdvancedHeartMonitor()
    window.show()
    sys.exit(app.exec())