import subprocess
import os
import sys
from datetime import datetime

# Set up paths
project_root = r"c:\Users\nires\Side Gig\ensemble_trader"
other_repo_logs = r"C:\Users\nires\Side Gig\pixel-data-feeds\logs"
report_path = os.path.join(other_repo_logs, "E1_2017_SHADOW_REPORT.md")

os.makedirs(other_repo_logs, exist_ok=True)

print(f"Starting Full 2017 Shadow Run...")
print(f"Output will be summarized in: {report_path}")

cmd = [
    "uv", "run", "python", 
    "E1/testing/shadow_runner.py",
    "--start", "2017-01-01",
    "--end", "2017-12-31",
    "--reset"
]

start_time = datetime.now()

with open(report_path, "w") as f:
    f.write(f"# E1 2017 Full Shadow Simulation Report\n\n")
    f.write(f"- **Start Time**: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"- **Status**: RUNNING\n\n")
    f.write(f"## Live Log Output\n\n```text\n")

# Run in background and pipe to file
process = subprocess.Popen(
    cmd,
    cwd=project_root,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1
)

with open(report_path, "a") as f:
    for line in process.stdout:
        f.write(line)
        # Also print to console so I can see progress if I check
        print(line, end="")

process.wait()

end_time = datetime.now()
duration = end_time - start_time

with open(report_path, "a") as f:
    f.write(f"```\n\n## Final Summary\n\n")
    f.write(f"- **End Time**: {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"- **Duration**: {duration}\n")
    f.write(f"- **Status**: COMPLETED\n")

print(f"\nShadow run finished in {duration}")
