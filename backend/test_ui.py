"""
Test UI for PCGClient - Full system testing with real-time plotting.
"""
import sys
import asyncio
import numpy as np
from PyQt6 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg
from pcg_ble_client import PCGClient, BLEConnectionError

class PCGTestUI(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PCG BLE Test UI")
        self.setGeometry(100, 100, 1400, 700)

        self.client = None
        self.full_signal = None
        self.is_analyzing = False

        self.initUI()

    def initUI(self):
        """Initialize UI components."""
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QHBoxLayout(central_widget)

        # --- LEFT SIDEBAR: CONTROLS ---
        left_panel = QtWidgets.QVBoxLayout()

        # Status indicator
        self.status_label = QtWidgets.QLabel("Status: Disconnected")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        left_panel.addWidget(self.status_label)

        # Device connection
        left_panel.addWidget(QtWidgets.QLabel("<b>Connection</b>"))
        device_layout = QtWidgets.QHBoxLayout()
        self.device_input = QtWidgets.QLineEdit("PCG_Monitor_Raw")
        device_layout.addWidget(QtWidgets.QLabel("Device:"))
        device_layout.addWidget(self.device_input)
        left_panel.addLayout(device_layout)

        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.clicked.connect(self.on_connect)
        left_panel.addWidget(self.connect_btn)

        left_panel.addSpacing(20)

        # Analysis parameters
        left_panel.addWidget(QtWidgets.QLabel("<b>Analysis Parameters</b>"))

        self.patient_input = QtWidgets.QLineEdit("Test Patient")
        left_panel.addWidget(QtWidgets.QLabel("Patient Name:"))
        left_panel.addWidget(self.patient_input)

        sample_rate_layout = QtWidgets.QHBoxLayout()
        self.sample_rate_input = QtWidgets.QSpinBox()
        self.sample_rate_input.setValue(500)
        self.sample_rate_input.setRange(100, 5000)
        sample_rate_layout.addWidget(QtWidgets.QLabel("Sample Rate (Hz):"))
        sample_rate_layout.addWidget(self.sample_rate_input)
        left_panel.addLayout(sample_rate_layout)

        oversample_layout = QtWidgets.QHBoxLayout()
        self.oversample_input = QtWidgets.QSpinBox()
        self.oversample_input.setValue(8)
        self.oversample_input.setRange(1, 32)
        oversample_layout.addWidget(QtWidgets.QLabel("Oversample Count:"))
        oversample_layout.addWidget(self.oversample_input)
        left_panel.addLayout(oversample_layout)

        batch_layout = QtWidgets.QHBoxLayout()
        self.batch_input = QtWidgets.QSpinBox()
        self.batch_input.setValue(6)
        self.batch_input.setRange(1, 64)
        batch_layout.addWidget(QtWidgets.QLabel("Batch Size:"))
        batch_layout.addWidget(self.batch_input)
        left_panel.addLayout(batch_layout)

        time_layout = QtWidgets.QHBoxLayout()
        self.time_input = QtWidgets.QSpinBox()
        self.time_input.setValue(60)
        self.time_input.setRange(1, 600)
        time_layout.addWidget(QtWidgets.QLabel("Analysis Time (s):"))
        time_layout.addWidget(self.time_input)
        left_panel.addLayout(time_layout)

        left_panel.addSpacing(20)

        # Control buttons
        self.start_btn = QtWidgets.QPushButton("Start Analysis")
        self.start_btn.clicked.connect(self.on_start_analysis)
        self.start_btn.setEnabled(False)
        left_panel.addWidget(self.start_btn)

        left_panel.addSpacing(10)

        # Progress
        self.progress_label = QtWidgets.QLabel("Progress: 0%")
        left_panel.addWidget(self.progress_label)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setValue(0)
        left_panel.addWidget(self.progress_bar)

        left_panel.addSpacing(20)

        # Stats
        left_panel.addWidget(QtWidgets.QLabel("<b>Statistics</b>"))
        self.stats_label = QtWidgets.QLabel(
            "Samples: 0\nMin: 0\nMax: 0\nExpected: 0"
        )
        self.stats_label.setStyleSheet("font-family: monospace; font-size: 10px;")
        left_panel.addWidget(self.stats_label)

        left_panel.addStretch()

        # --- CENTER: PLOT ---
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setLabel('left', 'Amplitude')
        self.plot_widget.setLabel('bottom', 'Sample')
        self.plot_widget.setTitle('Real-time Signal')
        self.plot_widget.setBackground('w')
        self.curve_live = self.plot_widget.plot(pen=pg.mkPen(color='b', width=1))
        self.curve_final = self.plot_widget.plot(pen=pg.mkPen(color='g', width=1.5))

        # --- LAYOUT ---
        container = QtWidgets.QWidget()
        container_layout = QtWidgets.QHBoxLayout(container)

        left_container = QtWidgets.QWidget()
        left_container.setLayout(left_panel)
        left_container.setMaximumWidth(350)

        container_layout.addWidget(left_container)
        container_layout.addWidget(self.plot_widget, 1)

        main_layout.addWidget(container)

    def on_connect(self):
        """Connect to Arduino."""
        device_name = self.device_input.text()
        self.client = PCGClient(device_name=device_name)

        async def connect_async():
            try:
                await self.client.connect()
                self.status_label.setText("Status: Connected")
                self.status_label.setStyleSheet("color: green; font-weight: bold;")
                self.connect_btn.setEnabled(False)
                self.start_btn.setEnabled(True)
                self.device_input.setEnabled(False)
                print("Connected to Arduino")
            except BLEConnectionError as e:
                self.status_label.setText(f"Status: Failed - {e}")
                self.status_label.setStyleSheet("color: red; font-weight: bold;")
                print(f"Connection failed: {e}")

        asyncio.create_task(connect_async())

    def on_start_analysis(self):
        """Start analysis."""
        if not self.client or not self.client.is_connected():
            self.status_label.setText("Status: Not connected")
            return

        self.is_analyzing = True
        self.start_btn.setEnabled(False)
        self.curve_live.clear()
        self.curve_final.clear()
        self.full_signal = None

        async def analyze_async():
            try:
                sample_rate = self.sample_rate_input.value()
                oversample = self.oversample_input.value()
                batch_size = self.batch_input.value()
                patient_name = self.patient_input.text()
                analysis_time = self.time_input.value()

                expected_samples = sample_rate * analysis_time
                samples_received = 0
                batch_count = 0

                self.status_label.setText("Status: Analyzing...")

                async for batch in self.client.analyze(
                    sample_rate=sample_rate,
                    oversample_count=oversample,
                    batch_size=batch_size,
                    patient_name=patient_name,
                    analysis_time_seconds=analysis_time
                ):
                    if not self.is_analyzing:
                        break

                    samples_received += len(batch)
                    batch_count += 1

                    # Update plot (show only last 1000 samples for performance)
                    display_samples = self.client._accumulated_data[-1000:]
                    self.curve_live.setData(display_samples)

                    # Update progress
                    progress = min(100, int((samples_received / expected_samples) * 100))
                    self.progress_bar.setValue(progress)
                    self.progress_label.setText(f"Progress: {progress}%")

                    # Update stats
                    self.update_stats(samples_received, expected_samples)

                    QtCore.QCoreApplication.processEvents()

                # Get full signal
                self.full_signal = self.client.get_full_signal()
                self.curve_final.setData(self.full_signal)
                self.curve_live.clear()

                self.progress_bar.setValue(100)
                self.progress_label.setText("Progress: 100% (Complete)")
                self.status_label.setText("Status: Analysis Complete")
                self.status_label.setStyleSheet("color: blue; font-weight: bold;")

                self.update_stats(len(self.full_signal), expected_samples)

                print(f"Analysis complete: {len(self.full_signal)} samples collected")

            except BLEConnectionError as e:
                self.status_label.setText(f"Status: Connection Lost - {e}")
                self.status_label.setStyleSheet("color: red; font-weight: bold;")
                print(f"Analysis failed: {e}")
            finally:
                self.is_analyzing = False
                self.start_btn.setEnabled(self.client and self.client.is_connected())

        asyncio.create_task(analyze_async())

    def update_stats(self, samples_received, expected_samples):
        """Update statistics display."""
        if len(self.client._accumulated_data) > 0:
            data = np.array(self.client._accumulated_data)
            min_val = int(data.min())
            max_val = int(data.max())
        else:
            min_val = max_val = 0

        stats_text = (
            f"Samples: {samples_received}/{expected_samples}\n"
            f"Min: {min_val}\n"
            f"Max: {max_val}\n"
            f"Expected: {expected_samples}"
        )
        self.stats_label.setText(stats_text)

    def closeEvent(self, event):
        """Handle window close."""
        if self.client and self.client.is_connected():
            async def disconnect():
                await self.client.disconnect()

            asyncio.create_task(disconnect())
        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = PCGTestUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
