import sys
import socket
import struct
import numpy as np
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton
from PyQt6.QtCore import QThread, pyqtSignal
import pyqtgraph as pg

# Background thread for handling the TCP binary stream
class DAQThread(QThread):
    # Signal to send unpacked data to the GUI (e.g., timestamp, pressure_value)
    data_received = pyqtSignal(float, float)

    def __init__(self):
        super().__init__()
        self.running = True

    def run(self):
        # Connect to DSM 4000 Data Port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # sock.connect(('192.168.1.100', 7000)) # Replace with real IP/Port

        while self.running:
            # Simulate receiving binary data 
            # In reality: raw_data = sock.recv(PACKET_SIZE)
            QThread.msleep(10) # Simulating acquisition delay
            
            # Example parsing (you will need the exact structure from the manual)
            # unpacked = struct.unpack('<ff', raw_data)
            simulated_time = QThread.currentTime()
            simulated_val = np.random.normal() 
            
            # Emit data to the GUI
            self.data_received.emit(simulated_time, simulated_val)

    def stop(self):
        self.running = False
        self.wait()

# Main GUI Window
class ScanivalveApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scanivalve DSM4000 Interface")
        self.resize(800, 600)

        # Setup UI
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Setup Plot
        self.plot_widget = pg.PlotWidget(title="Live Measurement Data")
        self.plot_curve = self.plot_widget.plot(pen='y')
        layout.addWidget(self.plot_widget)

        # Setup Controls
        self.start_btn = QPushButton("Start Acquisition")
        self.start_btn.clicked.connect(self.start_daq)
        layout.addWidget(self.start_btn)

        # Data storage for plotting
        self.x_data = []
        self.y_data = []

        # Initialize DAQ Thread
        self.daq_thread = DAQThread()
        self.daq_thread.data_received.connect(self.update_plot)

    def start_daq(self):
        if not self.daq_thread.isRunning():
            self.daq_thread.start()
            self.start_btn.setText("Stop Acquisition")
        else:
            self.daq_thread.stop()
            self.start_btn.setText("Start Acquisition")

    def update_plot(self, t, val):
        self.x_data.append(t)
        self.y_data.append(val)
        
        # Keep plot length manageable for performance
        if len(self.x_data) > 1000:
            self.x_data = self.x_data[-1000:]
            self.y_data = self.y_data[-1000:]

        self.plot_curve.setData(self.x_data, self.y_data)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ScanivalveApp()
    window.show()
    sys.exit(app.exec())