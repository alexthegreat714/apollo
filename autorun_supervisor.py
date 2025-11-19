import os, json, time, subprocess, psutil
from datetime import datetime
from mss import mss

try:
    from common.log_tools import summarize_log
except Exception:
    def summarize_log(text):
        return (text or "")[:2000]


class AutoRunSupervisor:
    TASK_DIR = r"C:\Users\blyth\Desktop\Engineering\rag_data\Aegis\tasks"
    LOG_DIR = r"C:\Users\blyth\Desktop\Engineering\rag_data\Aegis\logs\autorun_evidence"
    TRACK_FILE = os.path.join(LOG_DIR, "autorun_tracker.json")

    def __init__(self):
        os.makedirs(self.TASK_DIR, exist_ok=True)
        os.makedirs(self.LOG_DIR, exist_ok=True)
        self.state = self._load_tracker()

    def _load_tracker(self):
        if os.path.exists(self.TRACK_FILE):
            with open(self.TRACK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"completed": [], "failed": [], "last_run": None}

    def _save_tracker(self):
        with open(self.TRACK_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def detect_unfinished(self):
        all_tasks = [f for f in os.listdir(self.TASK_DIR) if f.endswith(".json")]
        completed = set(self.state.get("completed", []))
        return [t for t in all_tasks if t not in completed]

    def _log_summary(self, log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                data = f.read()
            summary = summarize_log(data)
            summary_path = log_path.replace(".txt", "_summary.txt")
            with open(summary_path, "w", encoding="utf-8") as out:
                if isinstance(summary, str):
                    out.write(summary)
                else:
                    out.write(json.dumps(summary, indent=2))
        except Exception:
            pass

    def run_task_silent(self, task_file):
        task_path = os.path.join(self.TASK_DIR, task_file)
        with open(task_path, "r", encoding="utf-8") as f:
            task = json.load(f)

        cmd = task["command"]
        cwd = task.get("cwd") or os.path.dirname(task_path)
        logfile = os.path.join(self.LOG_DIR, f"{task_file}_{int(time.time())}.txt")
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            if isinstance(cmd, str):
                result = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    creationflags=creation,
                )
            else:
                result = subprocess.run(
                    cmd,
                    shell=False,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    creationflags=creation,
                )
            with open(logfile, "w", encoding="utf-8") as out:
                out.write(result.stdout or "")
                out.write("\n")
                out.write(result.stderr or "")

            if result.returncode == 0:
                if task_file not in self.state["completed"]:
                    self.state["completed"].append(task_file)
                if task_file in self.state.get("failed", []):
                    self.state["failed"].remove(task_file)
                self._capture_screenshot(task_file)
            else:
                if task_file not in self.state["failed"]:
                    self.state["failed"].append(task_file)

            self._log_summary(logfile)
        except Exception as e:
            if task_file not in self.state["failed"]:
                self.state["failed"].append(task_file)
            with open(logfile, "w", encoding="utf-8") as out:
                out.write(str(e))
        finally:
            self._save_tracker()

    def _capture_screenshot(self, label):
        with mss() as sct:
            shot = os.path.join(self.LOG_DIR, f"{label}_proof.png")
            sct.shot(output=shot)

    def _process_guard(self):
        if os.environ.get("AEGIS_AUTORUN_KILL_PROCS", "0") != "1":
            return
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if "code.exe" in name:
                    proc.kill()
            except Exception:
                continue

    def nightly_demo(self):
        self._process_guard()
        pending = list(dict.fromkeys(self.detect_unfinished() + self.state.get("failed", [])))
        for task_file in pending:
            self.run_task_silent(task_file)
        self.state["last_run"] = datetime.now().isoformat()
        self._save_tracker()
        return {
            "run_count": len(pending),
            "last_run": self.state["last_run"],
            "completed": self.state["completed"],
            "failed": self.state["failed"],
        }
