import argparse
import json
import logging
import queue
import sys
import threading
import time

from BMS import SmartBMS
from SMS import SixFabSMS

LOG = logging.getLogger("BatteryMonitor")
LOG.setLevel(logging.INFO)

class BatteryMonitor(object):
    def __init__(self, disable_sms, test_request, phone_filter_list):
        self.sms = SixFabSMS(whitelist=phone_filter_list,
                             disable_sending=disable_sms)
        self.info_queue = queue.Queue()

        if test_request:
            self.sms.add_test_message(test_request)

    def start(self):
        bms_thread = threading.Thread(target=self.task_get_battery_info)
        sms_thread = threading.Thread(target=self.task_respond_to_sms)
        bms_thread.start()
        sms_thread.start()
        bms_thread.join()
        sms_thread.join()

    @staticmethod
    def has_all_battery_info(battery_info):
        required_keys = [
            'pack_voltage', 'total_cells', 'time_hours', 'time_minutes',
            'input_amps', 'input_watts', 'output_amps', 'output_watts',
            'pack_soc']
        for req in required_keys:
            if req not in battery_info:
                return False
        for ind in range(1, battery_info['total_cells']):
            if ind not in battery_info['cells']:
                LOG.info("Missing cell %i", ind)
                return False
        return True

    def task_get_battery_info(self):
        bms = SmartBMS()
        battery_info = {
            'cells': {}
        }
        running = True

        while running:
            # initialize
            LOG.info("Initializing BMS")
            if bms.initialize(timeout=10) is None:
                LOG.info("Trying BMS initialization again")
                continue

            # try to get info on cells
            LOG.info("Getting info from BMS")
            for packet in bms.get_battery_info(timeout=20):
                if packet['type'] != 'invalid':
                    if packet['type'] == 'cell_info':
                        idx = packet['contents']['cell_index']
                        battery_info['cells'][idx] = {
                            'temp': packet['contents']['cell_temp'],
                            'volts': packet['contents']['cell_volts']
                        }
                        battery_info['total_cells'] = \
                            packet['contents']['total_cells']
                    else:
                        for key, val in packet['contents'].items():
                            battery_info[key] = val
                if self.has_all_battery_info(battery_info):
                    LOG.info("Got all info from battery")
                    LOG.info(self.format_overview_request(battery_info))
                    LOG.info(self.format_cells_request(battery_info))
                    LOG.info(self.format_temperature_request(battery_info))
                    running = False
                    break

        self.info_queue.put(battery_info)

    def task_respond_to_sms(self):
        battery_info = None
        wait_time = 1.0
        while True:
            LOG.info("Initializing SMS")
            while not self.sms.initialize():
                time.sleep(0.5)

            no_error = True
            while no_error:
                LOG.info("Getting messages from SMS")
                sms_requests = self.sms.get_messages()
                if sms_requests is None:
                    time.sleep(10.0)
                    continue

                # wait for BMS info
                if battery_info is None:
                    battery_info = self.info_queue.get()

                for message in sms_requests:
                    LOG.info("Replying to %s", message['number'])
                    message['message'] = message['message'].lower()

                    if message['message'] == "overview":
                        reply = self.format_overview_request(battery_info)
                    elif message['message'] == "cells":
                        reply = self.format_cells_request(battery_info)
                    elif message['message'] == "temperature":
                        reply = self.format_temperature_request(battery_info)
                    else:
                        LOG.info('Got message "%s", replying with overview',
                                 message['message'])
                        reply = self.format_overview_request(battery_info)
                        reply += ". Commands: overview, cells, temperature"

                    if not self.sms.send_message(message['number'], reply):
                        no_error = False
                        break
                    if not self.sms.delete_message(message['index']):
                        no_error = False
                        break

                time.sleep(wait_time)
                wait_time *= 2

    @staticmethod
    def format_overview_request(info):
        f_str = "{:.1f}V, Input: {:.1f}A/{:d}W, Output: {:.1f}A/{:d}W ({:d}%)"
        return f_str.format(info['pack_voltage'],
                            info['input_amps'], info['input_watts'],
                            info['output_amps'], info['output_watts'],
                            info['pack_soc'])

    @staticmethod
    def format_cells_request(info):
        out = "Cell Volts: ({:.3f}V - {:.3f}V)".format(
            info['min_volt_cell']['volts'], info['max_volt_cell']['volts'])
        for ind in sorted(info['cells'].keys()):
            out += " {:.3f}".format(info['cells'][ind]['volts'], ind)
        return out

    @staticmethod
    def format_temperature_request(info):
        out = "Temp. Celcius: ({:.1f}C - {:.1f}C)".format(
            info['min_temp_cell']['temp'], info['max_temp_cell']['temp'])
        for ind in sorted(info['cells'].keys()):
            out += " {:.1f}".format(info['cells'][ind]['temp'])
        return out

def main():
    parser = argparse.ArgumentParser(description='Run SMS Battery Monitor')
    parser.add_argument('--no-sms-send',  action='store_true', default=False,
                        help="Don't send any SMS, only receive (to avoid cost)")
    parser.add_argument('--request',
                        help="A request to process as if it were an SMS")
    parser.add_argument('--phone-list', required=True,
                        help="Path to a list of allowed phone numbers")
    args = parser.parse_args()

    logFormatter = logging.Formatter(
        "%(asctime)s [%(name)-14.14s] [%(levelname)-5.5s]  %(message)s")
    consoleHandler = logging.StreamHandler(sys.stdout)
    consoleHandler.setFormatter(logFormatter)
    logging.getLogger().addHandler(consoleHandler)

    LOG.info("### Starting Battery Monitor ###")
    monitor = BatteryMonitor(args.no_sms_send, args.request, args.phone_list)
    monitor.start()

if __name__ == "__main__":
    main()
