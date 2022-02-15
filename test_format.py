import json

from main import BatteryMonitor

with open("test_data.json") as myfile:
    battery_info = json.load(myfile)

print(BatteryMonitor.format_overview_request(battery_info))
print(BatteryMonitor.format_cells_request(battery_info))
print(BatteryMonitor.format_temperature_request(battery_info))
