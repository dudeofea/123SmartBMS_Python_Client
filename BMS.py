import binascii
import logging
import pygatt
import time
import queue

ble_log = logging.getLogger('pygatt')
ble_log.setLevel(logging.WARN)

LOG = logging.getLogger('SmartBMS')
LOG.setLevel(logging.INFO)

PACKET_SIZE = 20
DEVICE_ADDR = 'EC:FE:7E:1D:09:2F'
INFO_CHAR_RD = "99564A02-DC01-4D3C-B04E-3BB1EF0571B2"
MODE_CHAR_RW = "A87988B9-694C-479C-900E-95DFA6C00A24"
RX_CHAR_WO = "BF03260C-7205-4C25-AF43-93B1C299D159"
TX_CHAR_RD = "18CDA784-4BD3-4370-85BB-BFED91EC86AF"
RTS_CHAR_RD = "FDD6B4D3-046D-4330-BDEC-1FD0C90CB43B"

class SmartBMS(object):
    def __init__(self):
        self.adapter = pygatt.GATTToolBackend()
        self.adapter.start()
        self.device = None
        self.recv_queue = queue.Queue()

    def __del__(self):
        try:
            self.device.unsubscribe(TX_CHAR_RD)
        except Exception as ex:
            LOG.error("Could not unsubscribe %s", ex)
        try:
            self.device.disconnect()
        except Exception as ex:
            LOG.error("Could not disconnect %s", ex)
        self.adapter.stop()

    def _connect(self, timeout):
        while True:
            try:
                LOG.info("Connecting to BMS")
                self.device = self.adapter.connect(DEVICE_ADDR, timeout=timeout)
                return True
            except pygatt.exceptions.NotConnectedError as ex:
                LOG.info("Could not connect to BMS: %s", str(ex))
                time.sleep(1.0)

    def _data_recv_callback(self, handle, value):
        for character in value:
            self.recv_queue.put(character)

    def _send_bytes(self, ble_uuid, send_bytes, timeout):
        try:
            handle = self.device.get_handle(ble_uuid)
            self.device.char_write_handle(handle, send_bytes, timeout=timeout)
            return True
        except pygatt.exceptions.NotificationTimeout:
            return False
        except pygatt.exceptions.NotConnectedError:
            return False

    def _send_command(self, cmd_str, timeout):
        # package and send the command
        command = [0] * len(cmd_str)
        for i in range(len(cmd_str)):
            command[i] = ord(cmd_str[i])
        if not self._send_bytes(RX_CHAR_WO, bytearray(command), timeout):
            LOG.error("Could not send command '%s'" % (cmd_str))
            return None

        # get the response
        recv = bytearray()
        while not self._endswith(recv, bytearray(command)):
            # flush write buffer
            self._send_bytes(RX_CHAR_WO, bytearray([0x24]), timeout)
            recv = self._wait_for_data(timeout=timeout)
            if recv is None:
                LOG.info("Timed-out waiting for response to %s", cmd_str)
                return None

        return self._wait_for_data(timeout=timeout)

    def _wait_for_data(self, timeout=0):
        last_char = ''
        data = []
        total_time = 0
        while True:
            while not self.recv_queue.empty():
                last_char = self.recv_queue.get()
                data.append(last_char)
                if last_char == ord('\r'):
                    return bytearray(data)
            if timeout > 0 and total_time > timeout:
                return None
            time.sleep(0.1)
            total_time += 0.1

    @staticmethod
    def _parse_int(input_bytes):
        if isinstance(input_bytes, bytearray):
            input_bytes = input_bytes.decode()
        if input_bytes[0] == 'X':
            return 0
        return int(input_bytes, 16)

    @staticmethod
    def _parse_tmp(input_bytes):
        return SmartBMS._parse_int(input_bytes) * 0.857 - 232.1

    @staticmethod
    def _parse_packet(input_bytes):
        data_arr = input_bytes.split("_".encode())
        pkt_type = data_arr[0].decode()
        pkt_len = len(data_arr)

        if pkt_type == 'U' and pkt_len == 5:
            # Overview
            return {
                'type': 'overview',
                'contents': {
                    "pack_voltage": SmartBMS._parse_int(data_arr[1]) * 0.005,
                    "input_amps": SmartBMS._parse_int(data_arr[2]) * 0.05,
                    "pack_amps": SmartBMS._parse_int(data_arr[3]) * 0.05,
                    "output_amps": SmartBMS._parse_int(data_arr[4]) * 0.05
                }
            }
        elif pkt_type == 'T' and pkt_len == 5:
            # Min / max temperatures
            return {
                'type': 'min_max_temp',
                'contents': {
                    'min_temp_cell': {
                        'index': SmartBMS._parse_int(data_arr[2]),
                        'temp': SmartBMS._parse_tmp(data_arr[1])
                    },
                    'max_temp_cell': {
                        'index': SmartBMS._parse_int(data_arr[4]),
                        'temp': SmartBMS._parse_tmp(data_arr[3])
                    }
                }
            }
        elif pkt_type == 'E' and pkt_len == 5:
            # Energy counters / battery SOC
            return {
                'type': 'energy',
                'contents': {
                    'input_kwh': SmartBMS._parse_int(data_arr[1]) / 1000,
                    'pack_kwh': SmartBMS._parse_int(data_arr[2]) / 1000,
                    'output_kwh': SmartBMS._parse_int(data_arr[3]) / 1000,
                    'pack_soc': SmartBMS._parse_int(data_arr[4])
                }
            }
        elif pkt_type == 'M' and pkt_len == 4:
            time_str = data_arr[3].decode().split(":")
            return {
                'type': 'power',
                'contents': {
                    'input_watts': SmartBMS._parse_int(data_arr[1]),
                    'output_watts': SmartBMS._parse_int(data_arr[2]),
                    'time_hours': SmartBMS._parse_int(time_str[0]),
                    'time_minutes': SmartBMS._parse_int(time_str[1])
                }
            }
        elif pkt_type == 'V' and pkt_len == 6:
            # Min / max cell voltages
            return {
                'type': 'min_max_volts',
                'contents': {
                    'min_volt_cell': {
                        'index': SmartBMS._parse_int(data_arr[2]),
                        'volts': SmartBMS._parse_int(data_arr[1]) * 0.005
                    },
                    'max_volt_cell': {
                        'index': SmartBMS._parse_int(data_arr[4]),
                        'volts': SmartBMS._parse_int(data_arr[3]) * 0.005
                    },
                    'balance_volts': SmartBMS._parse_int(data_arr[5]) * 0.005
                }
            }
        elif pkt_type == 'C' and pkt_len == 6:
            # Cell voltages
            cell_indx = SmartBMS._parse_int(data_arr[1])
            cells_len = SmartBMS._parse_int(data_arr[2])
            if cell_indx <= cells_len:
                cell_volts = SmartBMS._parse_int(data_arr[3]) * 0.005
                cell_temp = SmartBMS._parse_tmp(data_arr[4])
                return {
                    'type': 'cell_info',
                    'contents': {
                        'cell_index': cell_indx,
                        'total_cells': cells_len,
                        'cell_volts': cell_volts,
                        'cell_temp': cell_temp
                    }
                }
            return {
                'type': 'invalid',
                'contents': {
                    'message': "Cell %i out of bounds" % (cell_indx)
                }
            }
        else:
            return {
                'type': 'invalid',
                'contents': {
                    'message': "Unknown packet " + str(data_arr)
                }
            }

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

    def _all_cells_measured(self):
        # check that all cell measurements are present
        if self.cells_len is None:
            return False
        print(self.cells)
        for i in range(self.cells_len):
            if i not in self.cells:
                return False
        return True

    def initialize(self, timeout):
        # Observes the given characteristics for indications.
        # When a response is available, calls data_recv_callback
        try:
            #self.device = self.adapter.connect(DEVICE_ADDR, timeout=timeout)
            #LOG.info("Connection Established")
            self._connect(timeout)
            LOG.info("Connected")

            self.device.subscribe(
                TX_CHAR_RD,
                callback=self._data_recv_callback,
                indication=True,
                wait_for_response=True)
            LOG.info("Subscribed")
        except pygatt.exceptions.NotificationTimeout as ex:
            LOG.info("Timed out in connecting %s", str(ex))
            time.sleep(0.5)
            return None
        except pygatt.exceptions.NotConnectedError as ex:
            LOG.info("Not connected %s", str(ex))
            time.sleep(0.5)
            return None

        # Enable data mode
        if not self._send_bytes(MODE_CHAR_RW, bytearray([0x01]), timeout):
            return None
        for i in range(10):
            self._send_bytes(RX_CHAR_WO, bytearray([0x24]), timeout)
        # Disable cell data
        return self._send_command("D!\r", timeout)

    def get_battery_info(self, timeout):
        try:
            # Enable cell data
            if not self._send_command("E!\r", timeout):
                LOG.info("Could not send start data command")
                return False

            while True:
                LOG.info("Waiting for packet")
                # get another packet
                if not self._send_bytes(RX_CHAR_WO, bytearray([0x24]), timeout):
                    break
                data = self._wait_for_data(timeout=timeout)
                yield self._parse_packet(data)
        finally:
            # Stop cell data
            return self._send_command("D!\r", timeout)
