import sys
import socket
import struct
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLineEdit, QPlainTextEdit, 
                             QLabel, QGroupBox, QSplitter)
from PyQt6.QtCore import QThread, pyqtSignal, Qt

# ==========================================
# UNIVERSAL BUFFER PARSER
# ==========================================
def parse_dsm4000_stream(buffer):
    if len(buffer) < 2:
        return None, buffer # Wait for more data
        
    # Check for BIN=4 Header (Starts with a 2-byte short evaluating to 136)
    first_two_bytes = struct.unpack('>H', buffer[:2])[0]
    if first_two_bytes == 136:
        if len(buffer) < 136:
            return None, buffer 
        return {"type": "config_header", "data": "BIN 4 Config Header Received"}, buffer[136:]
        
    binary_id = buffer[0]
    
    # Parse standard headers depending on the format
    if binary_id in (0x01, 0x02):  # BIN 1 (12-byte header)
        header_size = 12
        if len(buffer) < header_size:
            return None, buffer
        _, group_id, num_channels, frame_num, time_val = struct.unpack('>BBHII', buffer[:header_size])
        timestamp = time_val
        
    elif binary_id in (0x05, 0x06): # BIN 3 (24-byte header)
        header_size = 24
        if len(buffer) < header_size:
            return None, buffer
        _, group_id, num_channels, frame_num, start_sec, start_nano, frame_sec, frame_nano = struct.unpack('>BBHIIIII', buffer[:header_size])
        timestamp = frame_sec + (frame_nano * 1e-9)
        
    else:
        # Unrecognized byte, flush 1 byte to resynchronize the TCP stream
        return None, buffer[1:]
        
    # Calculate payload size (4 bytes per channel)
    payload_size = num_channels * 4
    total_size = header_size + payload_size
    
    if len(buffer) < total_size:
        return None, buffer # Wait for the rest of the payload
        
    payload = buffer[header_size:total_size]
    
    # Determine data type: 0x01 and 0x05 are Floats, 0x02 and 0x06 are Integers
    is_float = binary_id in (0x01, 0x05)
    fmt = f">{num_channels}{'f' if is_float else 'i'}"
    pressures = struct.unpack(fmt, payload)
    
    parsed_data = {
        "type": "scan_data",
        "group_id": group_id,
        "num_channels": num_channels,
        "frame_number": frame_num,
        "timestamp": timestamp,
        "pressures": [round(p, 4) if is_float else p for p in pressures]
    }
    
    return parsed_data, buffer[total_size:]


# ==========================================
# NETWORK THREADS
# ==========================================
class CommandThread(QThread):
    response_received = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.sock = None
        self.running = False

    def connect_to_dsm(self, ip, port):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(2.0)
            self.sock.connect((ip, int(port)))
            self.running = True
            self.start()
            self.response_received.emit(f"Connected to DSM Telnet at {ip}:{port}")
        except Exception as e:
            self.response_received.emit(f"Telnet Connection failed: {e}")

    def send_command(self, cmd_str):
        if self.sock and self.running:
            if not cmd_str.endswith('\r\n'):
                cmd_str += '\r\n'
            self.sock.sendall(cmd_str.encode('ascii'))
            self.response_received.emit(f"> {cmd_str.strip()}")
        else:
            self.response_received.emit("Error: Telnet not connected.")

    def run(self):
        while self.running:
            try:
                data = self.sock.recv(1024)
                if data:
                    self.response_received.emit(data.decode('ascii').strip())
                else:
                    self.running = False
            except socket.timeout:
                continue 
            except Exception as e:
                self.response_received.emit(f"Telnet Socket error: {e}")
                self.running = False

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        self.wait()

class DAQThread(QThread):
    data_parsed = pyqtSignal(str)
    status_update = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.listen_ip = ""
        self.listen_port = 7000

    def start_listening(self, ip, port):
        self.listen_ip = ip
        self.listen_port = int(port)
        self.running = True
        self.start()

    def run(self):
        # Set up a TCP Server to listen for incoming DSM connections
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            server_sock.bind((self.listen_ip, self.listen_port))
            server_sock.listen(1)
            server_sock.settimeout(2.0)
            self.status_update.emit(f"DAQ Listening on {self.listen_ip}:{self.listen_port}...")
        except Exception as e:
            self.status_update.emit(f"DAQ Bind Error: {e}")
            return

        conn = None
        while self.running and conn is None:
            try:
                conn, addr = server_sock.accept()
                self.status_update.emit(f"DAQ Connected by DSM at {addr}")
                conn.settimeout(2.0)
            except socket.timeout:
                continue

        if not self.running:
            server_sock.close()
            return

        buffer = bytearray()
        while self.running:
            try:
                chunk = conn.recv(4096)
                if not chunk:
                    self.status_update.emit("DAQ Connection closed by DSM.")
                    break
                buffer.extend(chunk)
                
                # Parse as much data as possible from the buffer
                while True:
                    parsed_data, remaining = parse_dsm4000_stream(buffer)
                    buffer = bytearray(remaining) # Keep remaining bytes for next loop
                    
                    if parsed_data:
                        # Convert dictionary to string for ASCII visualization
                        if parsed_data["type"] == "scan_data":
                            msg = f"Grp: {parsed_data['group_id']} | Frame: {parsed_data['frame_number']} | Data: {parsed_data['pressures']}"
                            self.data_parsed.emit(msg)
                        else:
                            self.data_parsed.emit(str(parsed_data))
                    else:
                        break # Not enough data for a full packet, wait for next chunk
                        
            except socket.timeout:
                continue
            except Exception as e:
                self.status_update.emit(f"DAQ Stream error: {e}")
                break

        if conn:
            conn.close()
        server_sock.close()

    def stop(self):
        self.running = False
        self.wait()


# ==========================================
# MAIN GUI
# ==========================================
class DSMControlInterface(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DSM 4000 Command & Data Interface")
        self.resize(1000, 700)
        
        # Initialize Threads
        self.cmd_thread = CommandThread()
        self.cmd_thread.response_received.connect(self.log_to_telnet_console)
        
        self.daq_thread = DAQThread()
        self.daq_thread.status_update.connect(self.log_to_data_console)
        self.daq_thread.data_parsed.connect(self.log_to_data_console)

        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # --- Left Side: Controls ---
        control_layout = QVBoxLayout()
        
        # 1. Telnet Connection Group
        telnet_group = QGroupBox("1. DSM Telnet Connection")
        telnet_layout = QVBoxLayout()
        self.dsm_ip_input = QLineEdit("192.168.1.100")
        self.dsm_port_input = QLineEdit("23")
        self.connect_telnet_btn = QPushButton("Connect Telnet")
        self.connect_telnet_btn.clicked.connect(self.connect_telnet)
        
        telnet_layout.addWidget(QLabel("DSM IP Address:"))
        telnet_layout.addWidget(self.dsm_ip_input)
        telnet_layout.addWidget(QLabel("DSM Telnet Port:"))
        telnet_layout.addWidget(self.dsm_port_input)
        telnet_layout.addWidget(self.connect_telnet_btn)
        telnet_group.setLayout(telnet_layout)
        control_layout.addWidget(telnet_group)

        # 2. Data Listener Group
        data_listen_group = QGroupBox("2. Local Data Listener")
        data_listen_layout = QVBoxLayout()
        # 0.0.0.0 listens on all interfaces (good for Linux/Windows cross-platform)
        self.local_ip_input = QLineEdit("0.0.0.0") 
        self.local_port_input = QLineEdit("7000")
        self.start_listen_btn = QPushButton("Start Listening for Data")
        self.start_listen_btn.clicked.connect(self.start_daq_listener)
        
        data_listen_layout.addWidget(QLabel("Local Bind IP (0.0.0.0 for all):"))
        data_listen_layout.addWidget(self.local_ip_input)
        data_listen_layout.addWidget(QLabel("Local Data Port:"))
        data_listen_layout.addWidget(self.local_port_input)
        data_listen_layout.addWidget(self.start_listen_btn)
        data_listen_group.setLayout(data_listen_layout)
        control_layout.addWidget(data_listen_group)

        # 3. Configuration Commands
        config_group = QGroupBox("3. DSM Configuration")
        config_layout = QVBoxLayout()
        
        self.binaddr_btn = QPushButton("Send SET BINADDR (Using Local IPs)")
        self.binaddr_btn.clicked.connect(self.send_binaddr)
        self.bin1_btn = QPushButton("Set Format: BIN 1 (Standard)")
        self.bin1_btn.clicked.connect(lambda: self.cmd_thread.send_command("SET BIN 1"))
        self.bin3_btn = QPushButton("Set Format: BIN 3 (PTP Time)")
        self.bin3_btn.clicked.connect(lambda: self.cmd_thread.send_command("SET BIN 3"))
        
        config_layout.addWidget(self.binaddr_btn)
        config_layout.addWidget(self.bin1_btn)
        config_layout.addWidget(self.bin3_btn)
        config_group.setLayout(config_layout)
        control_layout.addWidget(config_group)

        # 4. Action Commands
        action_group = QGroupBox("4. Scanning Actions")
        action_layout = QHBoxLayout()
        self.scan_btn = QPushButton("START SCAN")
        self.scan_btn.clicked.connect(lambda: self.cmd_thread.send_command("SCAN"))
        self.stop_btn = QPushButton("STOP SCAN")
        self.stop_btn.clicked.connect(lambda: self.cmd_thread.send_command("STOP"))
        action_layout.addWidget(self.scan_btn)
        action_layout.addWidget(self.stop_btn)
        action_group.setLayout(action_layout)
        control_layout.addWidget(action_group)
        
        control_layout.addStretch()
        main_layout.addLayout(control_layout, stretch=1)

        # --- Right Side: Consoles ---
        # Using a QSplitter so you can drag to resize the consoles
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Telnet Console
        telnet_console_widget = QWidget()
        telnet_console_layout = QVBoxLayout(telnet_console_widget)
        telnet_console_layout.setContentsMargins(0,0,0,0)
        telnet_console_layout.addWidget(QLabel("Telnet Command & Response Console:"))
        
        self.telnet_output = QPlainTextEdit()
        self.telnet_output.setReadOnly(True)
        self.telnet_output.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: monospace;")
        telnet_console_layout.addWidget(self.telnet_output)
        
        self.raw_input = QLineEdit()
        self.raw_input.setPlaceholderText("Enter raw ASCII command here and press Enter...")
        self.raw_input.returnPressed.connect(self.send_raw_command)
        telnet_console_layout.addWidget(self.raw_input)
        
        splitter.addWidget(telnet_console_widget)

        # Data Console
        data_console_widget = QWidget()
        data_console_layout = QVBoxLayout(data_console_widget)
        data_console_layout.setContentsMargins(0,0,0,0)
        data_console_layout.addWidget(QLabel("Binary Data Output (ASCII Visualizer):"))
        
        self.data_output = QPlainTextEdit()
        self.data_output.setReadOnly(True)
        self.data_output.setStyleSheet("background-color: #1e1e1e; color: #00bfff; font-family: monospace;")
        data_console_layout.addWidget(self.data_output)
        
        self.clear_data_btn = QPushButton("Clear Data Console")
        self.clear_data_btn.clicked.connect(self.data_output.clear)
        data_console_layout.addWidget(self.clear_data_btn)

        splitter.addWidget(data_console_widget)
        
        main_layout.addWidget(splitter, stretch=2)

    def connect_telnet(self):
        ip = self.dsm_ip_input.text()
        port = self.dsm_port_input.text()
        self.cmd_thread.connect_to_dsm(ip, port)

    def start_daq_listener(self):
        ip = self.local_ip_input.text()
        port = self.local_port_input.text()
        if not self.daq_thread.isRunning():
            self.daq_thread.start_listening(ip, port)
            self.start_listen_btn.setText("Stop Listening")
        else:
            self.daq_thread.stop()
            self.start_listen_btn.setText("Start Listening for Data")
            self.log_to_data_console("Stopped listening for data.")

    def send_binaddr(self):
        # We assume the PC's actual network IP is needed here instead of 0.0.0.0
        # If the user typed 0.0.0.0 in the box, we will try to resolve the hostname
        target_ip = self.local_ip_input.text()
        if target_ip == "0.0.0.0":
            target_ip = socket.gethostbyname(socket.gethostname())
            self.log_to_telnet_console(f"Auto-resolved local IP to {target_ip} for BINADDR.")
            
        target_port = self.local_port_input.text()
        self.cmd_thread.send_command(f"SET BINADDR {target_ip} {target_port}")

    def send_raw_command(self):
        cmd = self.raw_input.text()
        if cmd:
            self.cmd_thread.send_command(cmd)
            self.raw_input.clear()

    def log_to_telnet_console(self, text):
        self.telnet_output.appendPlainText(text)
        
    def log_to_data_console(self, text):
        self.data_output.appendPlainText(text)
        # Limit lines to prevent memory issues during high-speed scanning
        if self.data_output.document().blockCount() > 1000:
            cursor = self.data_output.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def closeEvent(self, event):
        self.cmd_thread.stop()
        self.daq_thread.stop()
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DSMControlInterface()
    window.show()
    sys.exit(app.exec())