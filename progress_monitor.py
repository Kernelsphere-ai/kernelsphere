import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta


class ProgressMonitor:
    
    def __init__(self, results_dir: str = "parallel_results"):
        self.results_dir = Path(results_dir)
        self.progress_file = self.results_dir / "progress.json"
        self.summary_file = self.results_dir / "execution_summary.json"
    
    def read_progress(self):
        if not self.progress_file.exists():
            return None
        
        with open(self.progress_file, 'r') as f:
            return json.load(f)
    
    def read_summary(self):
        if not self.summary_file.exists():
            return None
        
        with open(self.summary_file, 'r') as f:
            return json.load(f)
    
    def print_progress(self):
        progress = self.read_progress()
        
        if not progress:
            print("No progress data found. Execution may not have started yet.")
            return
        
        print("\n" + "="*80)
        print("PARALLEL EXECUTION PROGRESS")
        print("="*80)
        print(f"Last Update: {progress['timestamp']}")
        print(f"\nTotal Tasks: {progress['total_tasks']}")
        print(f"Completed: {progress['completed']} ({progress['completion_rate']:.1f}%)")
        print(f"Failed: {progress['failed']}")
        print(f"In Progress: {progress['in_progress']}")
        print(f"Pending: {progress['pending']}")
        print(f"Success Rate: {progress['success_rate']:.1f}%")
        print("="*80 + "\n")
    
    def print_summary(self):
        summary = self.read_summary()
        
        if not summary:
            print("No summary data found. Execution may not be complete yet.")
            return
        
        exec_time = summary['execution_time']
        overall = summary['overall_stats']
        
        print("\n" + "="*80)
        print("FINAL EXECUTION SUMMARY")
        print("="*80)
        
        print(f"\nExecution Time:")
        print(f"  Start: {exec_time['start']}")
        print(f"  End: {exec_time['end']}")
        print(f"  Duration: {exec_time['duration_hours']:.2f} hours ({exec_time['duration_minutes']:.1f} minutes)")
        
        print(f"\nOverall Results:")
        print(f"  Total Tasks: {overall['total_tasks']}")
        print(f"  Completed Successfully: {overall['completed_successfully']}")
        print(f"  Failed Validation: {overall['failed_validation']}")
        print(f"  Errors: {overall['errors']}")
        print(f"  Success Rate: {overall['success_rate']:.2f}%")
        
        print(f"\nWebsite Breakdown:")
        for website, stats in summary['website_breakdown'].items():
            success_rate = (stats['completed'] / stats['total'] * 100) if stats['total'] > 0 else 0
            print(f"  {website}:")
            print(f"    Total: {stats['total']}")
            print(f"    Success: {stats['completed']} ({success_rate:.1f}%)")
            print(f"    Failed: {stats['failed']}")
            print(f"    Errors: {stats['errors']}")
        
        print("="*80 + "\n")
    
    def monitor_continuous(self, interval: int = 10):
        print(f"Starting continuous monitoring (refresh every {interval}s)")
        print("Press Ctrl+C to stop\n")
        
        try:
            while True:
                self.print_progress()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")
            
            summary = self.read_summary()
            if summary:
                print("\nExecution appears to be complete. Printing final summary:")
                self.print_summary()
    
    def analyze_failures(self):
        summary = self.read_summary()
        
        if not summary:
            print("No summary data found.")
            return
        
        failed_tasks = [
            task for task in summary['task_details']
            if task['status'] in ['failed', 'error']
        ]
        
        if not failed_tasks:
            print("No failed tasks found.")
            return
        
        print("\n" + "="*80)
        print(f"FAILED TASKS ANALYSIS ({len(failed_tasks)} tasks)")
        print("="*80)
        
        by_website = {}
        for task in failed_tasks:
            website = task['website']
            if website not in by_website:
                by_website[website] = []
            by_website[website].append(task)
        
        for website, tasks in by_website.items():
            print(f"\n{website} ({len(tasks)} failures):")
            for task in tasks:
                print(f"  Task ID: {task['task_id']}")
                print(f"    Status: {task['status']}")
                print(f"    Retries: {task['retry_count']}")
                if task['error']:
                    print(f"    Error: {task['error'][:100]}")
        
        print("\n" + "="*80 + "\n")
    
    def calculate_eta(self):
        progress = self.read_progress()
        
        if not progress:
            print("No progress data available.")
            return
        
        completed = progress['completed'] + progress['failed']
        total = progress['total_tasks']
        
        if completed == 0:
            print("Not enough data to calculate ETA.")
            return
        
        tracker_file = self.results_dir / "task_tracker.json"
        if not tracker_file.exists():
            print("No task tracker data available.")
            return
        
        with open(tracker_file, 'r') as f:
            tracker_data = json.load(f)
        
        session_file = self.results_dir / "current_session.json"
        if not session_file.exists():
            print("No session data available.")
            return
        
        with open(session_file, 'r') as f:
            session_data = json.load(f)
        
        start_time = datetime.fromisoformat(session_data['start_time'])
        current_time = datetime.now()
        elapsed = (current_time - start_time).total_seconds()
        
        avg_time_per_task = elapsed / completed if completed > 0 else 0
        remaining_tasks = total - completed
        estimated_remaining_seconds = avg_time_per_task * remaining_tasks
        
        eta = current_time + timedelta(seconds=estimated_remaining_seconds)
        
        print("\n" + "="*80)
        print("TIME ESTIMATION")
        print("="*80)
        print(f"Elapsed Time: {elapsed / 3600:.2f} hours")
        print(f"Completed Tasks: {completed}/{total}")
        print(f"Average Time per Task: {avg_time_per_task:.1f} seconds")
        print(f"Estimated Remaining Time: {estimated_remaining_seconds / 3600:.2f} hours")
        print(f"Estimated Completion: {eta.strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Monitor parallel execution progress',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '--results-dir',
        type=str,
        default='parallel_results',
        help='Results directory to monitor (default: parallel_results)'
    )
    
    parser.add_argument(
        '--mode',
        type=str,
        choices=['progress', 'summary', 'monitor', 'failures', 'eta'],
        default='progress',
        help='Monitor mode (default: progress)'
    )
    
    parser.add_argument(
        '--interval',
        type=int,
        default=10,
        help='Refresh interval for continuous monitoring in seconds (default: 10)'
    )
    
    args = parser.parse_args()
    
    monitor = ProgressMonitor(results_dir=args.results_dir)
    
    if args.mode == 'progress':
        monitor.print_progress()
    elif args.mode == 'summary':
        monitor.print_summary()
    elif args.mode == 'monitor':
        monitor.monitor_continuous(interval=args.interval)
    elif args.mode == 'failures':
        monitor.analyze_failures()
    elif args.mode == 'eta':
        monitor.calculate_eta()


if __name__ == "__main__":
    main()