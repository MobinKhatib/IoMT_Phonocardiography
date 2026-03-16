import sys
import numpy as np
import asyncio
from PyQt6 import QtWidgets, QtCore
import pyqtgraph as pg
from scipy.signal import butter, lfilter, find_peaks
from bleak import BleakClient, BleakScanner
import qasync
from qasync import asyncSlot

# --- UPDATE THESE WITH YOUR ESP32'S BLE DETAILS ---
BLE_DEVICE_NAME = "Nano_ESP32_Heart" # The name your ESP32 advertises
CHARACTERISTIC_UUID = "12345678-1234-5678-1234-56789abcdef1" # The UUID from your Arduino code

class KalmanFilter:
    def __init__(self, q=0.001, r=0.1):
        self.q = q  
        self.r = r  
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
        self.client = None

        self.initUI()

        # Timer just for updating the UI/Plotting now, NOT reading data
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(40) # 25fps UI update is plenty

        # Start the BLE connection process automatically
        asyncio.ensure_future(self.connect_ble())

    def initUI(self):
        self.setWindowTitle("Ubuntu PCG Analyzer - Nano ESP32 (BLE)")
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

        # Connection Status
        self.status_label = QtWidgets.QLabel("Status: Disconnected")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.sidebar.addWidget(self.status_label)

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

    async def connect_ble(self):
        self.status_label.setText("Status: Scanning...")
        self.status_label.setStyleSheet("color: orange; font-weight: bold;")
        
        # Find the device by name
        device = await BleakScanner.find_device_by_name(BLE_DEVICE_NAME)
        
        if not device:
            self.status_label.setText("Status: Device not found")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            return

        self.status_label.setText("Status: Connecting...")
        
        try:
            self.client = BleakClient(device)
            await self.client.connect()
            
            # Start receiving notifications
            await self.client.start_notify(CHARACTERISTIC_UUID, self.ble_notification_handler)
            
            self.status_label.setText("Status: Connected")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
        except Exception as e:
            self.status_label.setText(f"Status: Error connecting")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            print(f"BLE Error: {e}")

    def ble_notification_handler(self, sender, data):
        """ This is called automatically every time the ESP32 sends a new BLE value """
        try:
            # Assuming the ESP32 sends the float as a string encoded in utf-8, similar to serial
            value = float(data.decode('utf-8').strip())
            
            # If the ESP32 sends raw bytes (like a 4-byte float) instead of a string, 
            # you would use struct.unpack('f', data)[0] instead.
            
            self.raw_data = np.roll(self.raw_data, -1)
            self.raw_data[-1] = value
        except Exception as e:
            pass

    def apply_filters(self, data):
        centered = data - np.mean(data)
        if "Bandpass" in self.filter_mode:
            nyq = 0.5 * self.fs
            b, a = butter(4, [20/nyq, 100/nyq], btype='band')
            return lfilter(b, a, centered)
        elif "Kalman" in self.filter_mode:
            return np.array([self.kf.update(x) for x in centered])
        return centered

    def update_plot(self):
        """ This function now ONLY handles the math and the GUI, not data fetching """
        processed = self.apply_filters(self.raw_data)
        self.curve.setData(processed)
        
        # Simple Peak Detection for BPM
        peaks, _ = find_peaks(np.abs(processed), height=np.max(np.abs(processed))*0.6, distance=self.fs/2)
        if len(peaks) >= 2:
            bpm = 60 / (np.mean(np.diff(peaks)) / self.fs)
            self.bpm_label.setText(f"{int(bpm)}")

if __name__ == "__main__":
    # Required setup for qasync to merge PyQt6 and asyncio
    app = QtWidgets.QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    window = AdvancedHeartMonitor()
    window.show()
    
    with loop:
        loop.run_forever()
