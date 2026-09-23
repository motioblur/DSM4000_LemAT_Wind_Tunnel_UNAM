import sys
import socket
import struct
from PySide6.QtWidgets import (QApplication, QMainWindow, QPushButton, 
                               QVBoxLayout, QHBoxLayout, QWidget, QTextEdit, 
                               QLineEdit, QLabel)
from PySide6.QtCore import QThread, Signal, Slot

# ==========================================
# 1. THE NETWORK THREAD (Runs in background)
# ==========================================
class TCPWorker(QThread):
    data_received = Signal(tuple)
    raw_data_received = Signal(bytes) # New signal for raw bytes
    error_occurred = Signal(str)

    def __init__(self, ip, port):
        super().__init__()
        self.ip = ip
        self.port = port
        self.is_running = True
        self.sock = None # Keep a reference so the main thread can send data

    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(2.0) 
            self.sock.connect((self.ip, self.port))
            
            buffer = bytearray()
            PACKET_SIZE = 12 

            while self.is_running:
                try:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        break 
                    
                    # Emit the raw bytes immediately upon receipt
                    self.raw_data_received.emit(chunk)
                    buffer.extend(chunk)

                    while len(buffer) >= PACKET_SIZE:
                        packet = buffer[:PACKET_SIZE]
                        del buffer[:PACKET_SIZE]

                        unpacked_data = struct.unpack('<Id', packet)
                        self.data_received.emit(unpacked_data)

                except socket.timeout:
                    continue 
                
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            if self.sock:
                self.sock.close()
                self.sock = None

    def stop(self):
        self.is_running = False

    def send_command(self, cmd_bytes):
        """Called by the main thread to send data to the DAQ."""
        if self.sock:
            try:
                self.sock.sendall(cmd_bytes)
            except Exception as e:
                self.error_occurred.emit(f"Send Error: {str(e)}")

# ==========================================
# 2. THE MAIN UI (Runs on the main thread)
# ==========================================
class DAQWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scanivalve DSM4000 DAQ")
        self.resize(600, 500)

        # UI Elements
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        
        self.btn_start = QPushButton("Connect to DAQ")
        self.btn_stop = QPushButton("Disconnect")
        self.btn_stop.setEnabled(False)

        # Connection Inputs
        self.input_ip = QLineEdit("192.168.1.24")
        self.input_port = QLineEdit("24")

        # Command Inputs
        self.input_cmd = QLineEdit()
        self.input_cmd.setPlaceholderText("Enter command (e.g., SCAN)")
        self.input_cmd.setEnabled(False)
        self.btn_send = QPushButton("Send")
        self.btn_send.setEnabled(False)

        # Layouts
        conn_layout = QHBoxLayout()
        conn_layout.addWidget(QLabel("IP:"))
        conn_layout.addWidget(self.input_ip)
        conn_layout.addWidget(QLabel("Port:"))
        conn_layout.addWidget(self.input_port)

        cmd_layout = QHBoxLayout()
        cmd_layout.addWidget(self.input_cmd)
        cmd_layout.addWidget(self.btn_send)

        main_layout = QVBoxLayout()
        main_layout.addLayout(conn_layout)
        main_layout.addWidget(self.text_log)
        main_layout.addLayout(cmd_layout)
        main_layout.addWidget(self.btn_start)
        main_layout.addWidget(self.btn_stop)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        # Signals/Slots
        self.btn_start.clicked.connect(self.start_daq)
        self.btn_stop.clicked.connect(self.stop_daq)
        self.btn_send.clicked.connect(self.send_cmd)
        self.input_cmd.returnPressed.connect(self.send_cmd) # Send on 'Enter' key
        
        self.worker = None

    def start_daq(self):
        ip = self.input_ip.text().strip()
        try:
            port = int(self.input_port.text().strip())
        except ValueError:
            self.text_log.append("ERROR: Port must be a valid number.")
            return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.input_ip.setEnabled(False)
        self.input_port.setEnabled(False)
        self.input_cmd.setEnabled(True)
        self.btn_send.setEnabled(True)
        
        self.text_log.append(f"Connecting to {ip}:{port}...")

        self.worker = TCPWorker(ip, port)
        self.worker.data_received.connect(self.update_display)
        self.worker.raw_data_received.connect(self.show_raw_data)
        self.worker.error_occurred.connect(self.show_error)
        self.worker.start()

    def stop_daq(self):
        if self.worker:
            self.worker.stop()
            self.worker.wait()
        
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.input_ip.setEnabled(True)
        self.input_port.setEnabled(True)
        self.input_cmd.setEnabled(False)
        self.btn_send.setEnabled(False)
        
        self.text_log.append("Disconnected.")

    def send_cmd(self):
        if self.worker and self.input_cmd.text():
            cmd_text = self.input_cmd.text().strip()
            # Scanivalve usually requires a carriage return/line feed terminator
            cmd_bytes = f"{cmd_text}\r\n".encode('ascii') 
            self.worker.send_command(cmd_bytes)
            self.text_log.append(f"Sent: {cmd_text}")
            self.input_cmd.clear()

    @Slot(bytes)
    def show_raw_data(self, raw_bytes):
        # Convert raw bytes to a readable hex string (e.g., '0A 1F B3')
        hex_string = raw_bytes.hex(' ').upper()
        self.text_log.append(f"<font color='gray'>[RAW] {hex_string}</font>")

    @Slot(tuple)
    def update_display(self, data):
        self.text_log.append(f"<font color='blue'>[PARSED] {data}</font>")

    @Slot(str)
    def show_error(self, error_msg):
        self.text_log.append(f"<font color='red'>ERROR: {error_msg}</font>")
        self.stop_daq()

# ==========================================
# 3. APPLICATION ENTRY POINT
# ==========================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DAQWindow()
    window.show()
    sys.exit(app.exec())