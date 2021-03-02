import binascii
import logging
import pygatt
import time
import queue

log = logging.getLogger('pygatt')
log.setLevel(logging.INFO)

PACKET_SIZE = 20
DEVICE_ADDR = 'EC:FE:7E:1D:09:2F'
INFO_CHAR_RD = "99564A02-DC01-4D3C-B04E-3BB1EF0571B2"
MODE_CHAR_RW = "A87988B9-694C-479C-900E-95DFA6C00A24"
RX_CHAR_WO = "BF03260C-7205-4C25-AF43-93B1C299D159"
TX_CHAR_RD = "18CDA784-4BD3-4370-85BB-BFED91EC86AF"
RTS_CHAR_RD = "FDD6B4D3-046D-4330-BDEC-1FD0C90CB43B"

class SmartBMS(object):
    """Class for 123SmartBMS"""
    def __init__(self):
        self.adapter = pygatt.GATTToolBackend()
        self.adapter.start()
        self.device = None
        self.recv_queue = queue.Queue()

    def __del__(self):
        if self.device is not None:
            self.device.disconnect()
        self.adapter.stop()

    def initialize(self):
        self._connect()
        # Observes the given characteristics for indications.
        # When a response is available, calls data_handle_cb
        #self.device.subscribe(
        #    RTS_CHAR_RD,
        #    callback=self._data_recv_callback,
        #    indication=True,
        #    wait_for_response=True)
        self.device.subscribe(
            TX_CHAR_RD,
            callback=self._data_recv_callback,
            indication=True,
            wait_for_response=True)
        # Enable data mode
        self.device.char_write(MODE_CHAR_RW, bytearray([0x01]))
        for i in range(10):
            self.device.char_write(RX_CHAR_WO, bytearray([0x24]))
        # Disable cell data
        self.send_command("D!\r")
        # Get version
        print("Version", self.send_command("V@\r"))
        self.get_cell_info()

    def get_cell_info(self):
        # Enable cell data
        self.send_command("E!\r")
        for i in range(90):
            self.device.char_write(RX_CHAR_WO, bytearray([0x24]))
            data = self.wait_for_data()
            data_arr = data.split("_".encode())
            pkt_type = data_arr[0].decode()
            pkt_len = len(data_arr)
            if pkt_type == 'U' and pkt_len == 5:
                # Overview
                print("Battery Voltage:", self._parse_int(data_arr[1]) * 0.005,
                      "Solar Amps:", self._parse_int(data_arr[2]) * 0.05,
                      "Battery Amps:", self._parse_int(data_arr[3]) * 0.05,
                      "Load Amps:", self._parse_int(data_arr[4]) * 0.05)
            elif pkt_type == 'T' and pkt_len == 5:
                # Min / max temperatures
                print("Min Temp, Cell #%i @ %fC" % (
                    self._parse_int(data_arr[2]),
                    self._parse_int(data_arr[1])*0.857 - 232.1),
                      "Max Temp, Cell #%i @ %fC" % (
                    self._parse_int(data_arr[4]),
                    self._parse_int(data_arr[3])*0.857 - 232.1))
            elif pkt_type == 'E' and pkt_len == 5:
                # Energy counters, useless
                pass
            elif pkt_type == 'V' and pkt_len == 6:
                # Min / max cell voltages
                print("Min Voltage, Cell #%i @ %fV" % (
                    self._parse_int(data_arr[2]),
                    self._parse_int(data_arr[1]) * 0.005),
                      "Max Voltage, Cell #%i @ %fV" % (
                    self._parse_int(data_arr[4]),
                    self._parse_int(data_arr[3]) * 0.005),
                      "Balance Voltage: %fV" % (
                    self._parse_int(data_arr[5]) * 0.005))
            elif pkt_type == 'C' and pkt_len == 6:
                # Cell voltages
                print("Cell Voltage", data_arr)
                print("Cell #%i Voltage: %fV" % (
                    self._parse_int(data_arr[1]),
                    self._parse_int(data_arr[3]) * 0.005))
            else:
                print("Unknown", data_arr)
        self.send_command("D!\r")

    def send_command(self, cmd_str):
        # Try to send a command
        command = [0] * len(cmd_str)
        for i in range(len(cmd_str)):
            command[i] = ord(cmd_str[i])
        #print("Sending :", bytearray(command))
        self.device.char_write(RX_CHAR_WO, bytearray(command))
        recv = bytearray()
        while not self._endswith(recv, bytearray(command)):
            self.device.char_write(RX_CHAR_WO, bytearray([0x24]))
            recv = self.wait_for_data()
            #print("Received:", recv)
        return self.wait_for_data()

    def wait_for_data(self):
        last_char = ''
        data = []
        while True:
            while not self.recv_queue.empty():
                last_char = self.recv_queue.get()
                data.append(last_char)
                if last_char == ord('\r'):
                    return bytearray(data)
            #print("Waiting for queue", data)
            time.sleep(0.1)

    @staticmethod
    def _parse_int(input_bytes):
        if input_bytes.decode()[0] == 'X':
            return 0
        return int(input_bytes, 16)

    @staticmethod
    def _endswith(input_bytes, suffix_bytes):
        inp_len = len(input_bytes)
        suf_len = len(suffix_bytes)
        if inp_len < suf_len:
            return False
        for i in range(suf_len):
            if input_bytes[inp_len - suf_len + i] != suffix_bytes[i]:
                return False
        return True

    def _connect(self):
        while True:
            try:
                print("Connecting...")
                self.device = self.adapter.connect(DEVICE_ADDR)
                return True
            except pygatt.exceptions.NotConnectedError:
                time.sleep(1.0)

    def _data_recv_callback(self, handle, value):
        for character in value:
            self.recv_queue.put(character)

bms = SmartBMS()
bms.initialize()
