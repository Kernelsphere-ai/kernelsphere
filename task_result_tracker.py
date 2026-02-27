import json
import os
from pathlib import Path
from typing import Dict, List, Set
from datetime import datetime


class TaskResultTracker:
    
    def __init__(self, results_dir: str = "results"):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(exist_ok=True)
        self.tracker_file = self.results_dir / "task_tracker.json"
        self.failed_tasks_file = self.results_dir / "failed_tasks.json"
        self.session_file = self.results_dir / "current_session.json"
        
        self.recorded_tasks: Set[str] = set()
        
        self.load_tracker()
        self.start_new_session()
    
    def load_tracker(self):
        if self.tracker_file.exists():
            with open(self.tracker_file, 'r') as f:
                self.data = json.load(f)
        else:
            self.data = {}
        
        if self.failed_tasks_file.exists():
            with open(self.failed_tasks_file, 'r') as f:
                self.failed_tasks = json.load(f)
        else:
            self.failed_tasks = {}
    
    def start_new_session(self):
        self.session_data = {
            "session_id": datetime.now().isoformat(),
            "tasks_executed": [],
            "start_time": datetime.now().isoformat()
        }
        self.recorded_tasks.clear()
    
    def record_task_result(self, website: str, task_id: str, success: bool, task_description: str = ""):
        if task_id in self.recorded_tasks:
            return
        
        self.recorded_tasks.add(task_id)
        
        if website not in self.data:
            self.data[website] = {
                "total": 0,
                "successful": 0,
                "failed": 0,
                "success_rate": 0.0
            }
        
        self.data[website]["total"] += 1
        
        if success:
            self.data[website]["successful"] += 1
        else:
            self.data[website]["failed"] += 1
            
            if website not in self.failed_tasks:
                self.failed_tasks[website] = []
            
            self.failed_tasks[website].append({
                "task_id": task_id,
                "description": task_description,
                "timestamp": datetime.now().isoformat()
            })
        
        total = self.data[website]["total"]
        successful = self.data[website]["successful"]
        self.data[website]["success_rate"] = round((successful / total) * 100, 2) if total > 0 else 0.0
        
        self.session_data["tasks_executed"].append({
            "task_id": task_id,
            "website": website,
            "success": success,
            "timestamp": datetime.now().isoformat()
        })
        
        self.save_tracker()
        self.save_session()
    
    def save_tracker(self):
        with open(self.tracker_file, 'w') as f:
            json.dump(self.data, f, indent=2)
        
        with open(self.failed_tasks_file, 'w') as f:
            json.dump(self.failed_tasks, f, indent=2)
    
    def save_session(self):
        self.session_data["end_time"] = datetime.now().isoformat()
        with open(self.session_file, 'w') as f:
            json.dump(self.session_data, f, indent=2)
    
    def get_website_stats(self, website: str) -> Dict:
        return self.data.get(website, {
            "total": 0,
            "successful": 0,
            "failed": 0,
            "success_rate": 0.0
        })
    
    def get_all_stats(self) -> Dict:
        return self.data
    
    def get_failed_tasks(self, website: str = None) -> List:
        if website:
            return self.failed_tasks.get(website, [])
        return self.failed_tasks
    
    def reset_tracker(self):
        self.data = {}
        self.failed_tasks = {}
        self.recorded_tasks.clear()
        self.save_tracker()
    
    def print_summary(self):
        print("\n" + "="*80)
        print("TASK EXECUTION SUMMARY")
        print("="*80)
        
        session_tasks = len(self.session_data["tasks_executed"])
        if session_tasks > 0:
            print(f"\nCurrent Session: {session_tasks} task(s) executed")
        
        for website, stats in self.data.items():
            print(f"\n{website}:")
            print(f"  Total Tasks: {stats['total']}")
            print(f"  Successful: {stats['successful']}")
            print(f"  Failed: {stats['failed']}")
            print(f"  Success Rate: {stats['success_rate']}%")
            
            if website in self.failed_tasks and self.failed_tasks[website]:
                print(f"  Failed Task IDs: {[t['task_id'] for t in self.failed_tasks[website]]}")
        
        print("\n" + "="*80)