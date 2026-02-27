import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List


class TaskLogger:
    
    def __init__(self, output_dir: str = "task_logs"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.current_task_log = None
        self.logger = logging.getLogger(__name__)
    
    def start_task(self, task_id: str, website: str, question: str, start_url: str):
        self.current_task_log = {
            "task_id": task_id,
            "website": website,
            "question": question,
            "start_url": start_url,
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "duration_seconds": None,
            "success": False,
            "final_answer": "",
            "validation_result": None,
            "steps": [],
            "errors": [],
            "total_steps": 0
        }
    
    def log_step(self, step_num: int, action: str, reasoning: str, result: str, url: str = ""):
        if not self.current_task_log:
            return
        
        step_data = {
            "step": step_num,
            "action": action,
            "reasoning": reasoning[:200],
            "result": result,
            "url": url,
            "timestamp": datetime.now().isoformat()
        }
        
        self.current_task_log["steps"].append(step_data)
        self.current_task_log["total_steps"] = step_num
    
    def log_error(self, error_message: str, step_num: int = None):
        if not self.current_task_log:
            return
        
        error_data = {
            "step": step_num,
            "error": error_message,
            "timestamp": datetime.now().isoformat()
        }
        
        self.current_task_log["errors"].append(error_data)
    
    def log_extraction(self, step_num: int, extracted_content: str):
        if not self.current_task_log:
            return
        
        for step in self.current_task_log["steps"]:
            if step["step"] == step_num:
                step["extracted_content"] = extracted_content[:500]
                break
    
    def end_task(self, success: bool, final_answer: str, validation_result: Dict = None):
        if not self.current_task_log:
            return
        
        end_time = datetime.now()
        start_time = datetime.fromisoformat(self.current_task_log["start_time"])
        duration = (end_time - start_time).total_seconds()
        
        self.current_task_log["end_time"] = end_time.isoformat()
        self.current_task_log["duration_seconds"] = round(duration, 2)
        self.current_task_log["success"] = success
        self.current_task_log["final_answer"] = final_answer
        self.current_task_log["validation_result"] = validation_result
        
        self.save_log()
    
    def save_log(self):
        if not self.current_task_log:
            return
        
        task_id = self.current_task_log["task_id"]
        website = self.current_task_log["website"]
        
        website_dir = self.output_dir / website
        website_dir.mkdir(exist_ok=True)
        
        log_file = website_dir / f"{task_id}_log.json"
        
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(self.current_task_log, f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"Task log saved: {log_file}")
    
    def get_task_summary(self) -> Dict:
        if not self.current_task_log:
            return {}
        
        return {
            "task_id": self.current_task_log["task_id"],
            "website": self.current_task_log["website"],
            "success": self.current_task_log["success"],
            "total_steps": self.current_task_log["total_steps"],
            "duration_seconds": self.current_task_log["duration_seconds"],
            "error_count": len(self.current_task_log["errors"])
        }