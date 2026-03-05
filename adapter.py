import json
from pathlib import Path
from typing import Dict, List


def load_task(task_file: str) -> Dict:
    with open(task_file, 'r', encoding='utf-8') as f:
        if task_file.endswith('.jsonl'):
            line = f.readline().strip()
            data = json.loads(line)
        else:
            data = json.load(f)
    
    return normalize_task_data(data)


def load_all_tasks(tasks_file: str) -> List[Dict]:
    tasks = []
    with open(tasks_file, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                data = json.loads(line.strip())
                tasks.append(normalize_task_data(data))
    return tasks


def normalize_task_data(data: Dict) -> Dict:
    normalized = {}
    
    normalized['web_name'] = data.get('web_name', data.get('website', 'Unknown'))
    
    if 'task_id' in data:
        normalized['task_id'] = data['task_id']
    elif 'id' in data:
        normalized['task_id'] = data['id']
    else:
        normalized['task_id'] = 'unknown'
    
    normalized['question'] = data.get('question', data.get('ques', ''))
    
    normalized['start_url'] = data.get('start_url', data.get('web', ''))
    
    return normalized


class Adapter:
    
    def __init__(self, output_dir: str = "results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
    
    def format_output(self, agent_history: Dict, task_data: Dict) -> Dict:
        interact_messages = self._build_interact_messages(agent_history)
        
        task_name = f"task{task_data['web_name']}--{task_data['task_id']}"
        task_dir = self.output_dir / task_name
        task_dir.mkdir(exist_ok=True)
        
        return {
            "directory": str(task_dir),
            "interact_messages": interact_messages,
            "final_answer": agent_history.get("final_answer", ""),
            "completed": agent_history.get("completed", False),
            "total_steps": len(agent_history.get("steps", []))
        }
    
    def save_output(self, output_data: Dict, task_data: Dict):
        task_dir = Path(output_data["directory"])
        
        interact_file = task_dir / "interact_messages.json"
        with open(interact_file, 'w', encoding='utf-8') as f:
            json.dump(output_data["interact_messages"], f, indent=2, ensure_ascii=False)
        
        summary_file = task_dir / "summary.json"
        summary = {
            "web_name": task_data['web_name'],
            "task_id": task_data['task_id'],
            "question": task_data['question'],
            "start_url": task_data['start_url'],
            "final_answer": output_data["final_answer"],
            "completed": output_data["completed"],
            "total_steps": output_data["total_steps"]
        }
        
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
    
    def _build_interact_messages(self, agent_history: Dict) -> List[Dict]:
        messages = [
            {
                "role": "system",
                "content": "You are an autonomous web browsing agent."
            }
        ]
        
        if "task" in agent_history:
            messages.append({
                "role": "user",
                "content": f"Now given a task: {agent_history['task']} Please interact with the web page to complete the task. The URL is {agent_history.get('start_url', '')}"
            })
        
        for step in agent_history.get("steps", []):
            step_num = step.get("step", 0)
            url = step.get("url", "")
            
            messages.append({
                "role": "assistant",
                "content": f"Step {step_num}: Actions taken on {url}"
            })
        
        final_answer = agent_history.get("final_answer", "")
        if final_answer:
            messages.append({
                "role": "assistant",
                "content": f"Action: ANSWER; {final_answer}"
            })
        
        return messages

