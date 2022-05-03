import logging
import re
import serial

LOG = logging.getLogger("SixFabSMS")
LOG.setLevel(logging.INFO)

class SixFabSMS(object):
    def __init__(self, whitelist, disable_sending=False, timeout=15):
        with open(whitelist) as myfile:
            self.whitelist = myfile.read().split('\n')
        self.whitelist = [w for w in self.whitelist if len(w) > 0]
        LOG.info("Whitelist: %s", str(self.whitelist))
        self.sim_serial = serial.Serial(
            "/dev/ttyUSB2", baudrate=115200, timeout=timeout)

    def _clear_buffer(self):
        self.sim_serial.write((chr(26) + "\rAT\r").encode())
        self.sim_serial.flush()
        self.sim_serial.read(self.sim_serial.in_waiting)

    def _communicate(self, input_str):
        self.sim_serial.write((input_str + "\r").encode())
        self.sim_serial.flush()
        # disregard command echo
        self.sim_serial.readline()
        # get response as string
        return self.sim_serial.read(self.sim_serial.in_waiting).decode()

    def _check_success(self, at_cmd):
        resp = self._communicate(at_cmd)
        success = resp.endswith("\r\nOK\r\n") or resp == "OK\r\n"
        if not success:
            LOG.error('Failed command "%s" -> %s', at_cmd, str(resp.encode()))
        return success

    @staticmethod
    def _phone_match(phone_number, phone_list):
        return phone_number in phone_list

    def initialize(self):
        self._clear_buffer()
        if not self._check_success("AT&F"):
            LOG.error("Could not factory reset SMS")
            return False
        if not self._check_success('AT+CPMS="SM","SM","SM"'):
            LOG.error("Could not set SIM card storage")
            return False
        if not self._check_success("AT+CMGF=1"):
            LOG.error("Could not set SMS to text mode")
            return False
        if not self._check_success("ATI"):
            LOG.error("Could not get SMS info")
            return False
        return True

    def get_rssi(self):
        """get_rssi
        Return the RSSI or signal strength in dBm, normally negative
        """
        resp = self._communicate('AT+CSQ')
        if not resp.endswith("\r\nOK\r\n"):
            LOG.error("Could not get RSSI: %s", resp)
            return None
        rssi_regex = re.compile(
            r'\+CSQ: (\d+),(\d+)')
        match = rssi_regex.match(resp)
        if not match:
            LOG.error("Could not match RSSI regex %s", resp)
            return None
        rssi_code = int(match.group(1))
        error_rate = match.group(2)
        #LOG.info("RSSI error rate code: %s", error_rate)

        if rssi_code == 0:
            return -113
        if rssi_code == 1:
            return -111
        if rssi_code >= 2 and rssi_code <= 30:
            return -109 + (rssi_code - 2) * 2
        if rssi_code == 31:
            return -51
        if rssi_code == 100:
            return -116
        if rssi_code == 101:
            return -115
        if rssi_code >= 102 and rssi_code <= 190:
            return -114 + (rssi_code - 102)
        if rssi_code == 191:
            return -25

        return None

    def get_messages(self):
        """get_messages
        Return all messages with number and id. delete messages from
        non-whitelisted ids and don't return them
        """
        resp = self._communicate('AT+CMGL="ALL"')
        if resp == "OK\r\n":
            return []
        if not resp.endswith("\r\nOK\r\n"):
            LOG.error("Error in getting messages %s", resp)
            return None
        split = resp[:-6].split("+CMGL: ")
        msg_regex = re.compile(
            r'(\d+),"([^"]*)","([^"]*)",,"([^"]*)"\r\n(.*)\r\n')
        messages = []
        for msg in split:
            match = msg_regex.match(msg)
            if match:
                messages.append({
                    'index': match.group(1),
                    'status': match.group(2),
                    'number': match.group(3),
                    'timestamp': match.group(4),
                    'message': match.group(5)
                })
        ok_list, ban_list = self.whitelist_messages(messages)
        for msg in ban_list:
            LOG.info("Deleting message from number %s: %s",
                     msg['number'], msg['message'])
            self.delete_message(msg['index'])
        return ok_list

    def whitelist_messages(self, all_messages):
        ok_list = []
        ban_list = []
        for msg in all_messages:
            if self._phone_match(msg['number'], self.whitelist):
                LOG.info("Matched message from number %s: %s",
                         msg['number'], msg['message'])
                ok_list.append(msg)
            else:
                ban_list.append(msg)
        return ok_list, ban_list

    def delete_message(self, msg_index):
        LOG.info("Deleting message %s", msg_index)
        return self._check_success('AT+CMGD=' + msg_index)

    def send_message(self, number, message):
        LOG.info("Sending message to %s: %s", number, message)
        LOG.info("Message length %s chars", len(message))
        resp = self._communicate('AT+CMGS="' + number + '"\r')
        return self._check_success(message + chr(26))
