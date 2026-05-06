import subprocess
import csv
import json

def get_algo_trader_tasks():
    cmd = ['schtasks', '/query', '/fo', 'CSV', '/v']
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("Error running schtasks")
        return

    # Skip first line if it's "Folder: ..."
    lines = res.stdout.strip().split('\n')
    # find the header line (usually starts with "HostName")
    header_idx = 0
    for i, line in enumerate(lines):
        if "HostName" in line:
            header_idx = i
            break
            
    reader = csv.DictReader(lines[header_idx:])
    tasks = []
    for row in reader:
        name = row.get('TaskName', '')
        if name.startswith('\\AlgoTrader\\'):
            tasks.append({
                'TaskName': name,
                'TaskToRun': row.get('Task To Run', ''),
                'NextRunTime': row.get('Next Run Time', ''),
                'Status': row.get('Status', ''),
                'ScheduleType': row.get('Schedule Type', '')
            })
    
    # Sort by task name
    tasks.sort(key=lambda x: x['TaskName'])
    
    print(f"| {'Task Name':<35} | {'Schedule':<12} | {'Task To Run':<60} |")
    print("|" + "-"*37 + "|" + "-"*14 + "|" + "-"*62 + "|")
    for t in tasks:
        print(f"| {t['TaskName']:<35} | {t['ScheduleType']:<12} | {t['TaskToRun'][:60]:<60} |")

if __name__ == "__main__":
    get_algo_trader_tasks()
