import json
import argparse
from pathlib import Path
from typing import List, Dict


class BatchProcessor:
    
    def __init__(self, input_file: str, output_dir: str = "batches"):
        self.input_file = Path(input_file)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
    
    def load_tasks(self) -> List[Dict]:
        tasks = []
        with open(self.input_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    tasks.append(json.loads(line.strip()))
        return tasks
    
    def split_by_size(self, batch_size: int):
        tasks = self.load_tasks()
        total_tasks = len(tasks)
        num_batches = (total_tasks + batch_size - 1) // batch_size
        
        print(f"Splitting {total_tasks} tasks into {num_batches} batches of ~{batch_size} tasks each")
        
        batch_files = []
        for i in range(num_batches):
            start_idx = i * batch_size
            end_idx = min((i + 1) * batch_size, total_tasks)
            batch_tasks = tasks[start_idx:end_idx]
            
            batch_file = self.output_dir / f"batch_{i+1:03d}_of_{num_batches:03d}.jsonl"
            with open(batch_file, 'w', encoding='utf-8') as f:
                for task in batch_tasks:
                    f.write(json.dumps(task, ensure_ascii=False) + '\n')
            
            batch_files.append(str(batch_file))
            print(f"Created {batch_file} ({len(batch_tasks)} tasks)")
        
        manifest_file = self.output_dir / "batch_manifest.json"
        with open(manifest_file, 'w') as f:
            json.dump({
                "source_file": str(self.input_file),
                "total_tasks": total_tasks,
                "num_batches": num_batches,
                "batch_size": batch_size,
                "batch_files": batch_files
            }, f, indent=2)
        
        print(f"\nBatch manifest saved to: {manifest_file}")
        return batch_files
    
    def split_by_website(self):
        tasks = self.load_tasks()
        
        by_website = {}
        for task in tasks:
            website = task.get('web_name', task.get('website', 'unknown'))
            if website not in by_website:
                by_website[website] = []
            by_website[website].append(task)
        
        print(f"Splitting {len(tasks)} tasks by website ({len(by_website)} websites)")
        
        batch_files = []
        for website, website_tasks in by_website.items():
            batch_file = self.output_dir / f"{website}_tasks.jsonl"
            with open(batch_file, 'w', encoding='utf-8') as f:
                for task in website_tasks:
                    f.write(json.dumps(task, ensure_ascii=False) + '\n')
            
            batch_files.append(str(batch_file))
            print(f"Created {batch_file} ({len(website_tasks)} tasks)")
        
        manifest_file = self.output_dir / "website_manifest.json"
        with open(manifest_file, 'w') as f:
            json.dump({
                "source_file": str(self.input_file),
                "total_tasks": len(tasks),
                "websites": {
                    website: len(tasks) 
                    for website, tasks in by_website.items()
                },
                "batch_files": batch_files
            }, f, indent=2)
        
        print(f"\nWebsite manifest saved to: {manifest_file}")
        return batch_files


class ResultAggregator:
    
    def __init__(self, results_dirs: List[str], output_file: str = "aggregated_results.json"):
        self.results_dirs = [Path(d) for d in results_dirs]
        self.output_file = Path(output_file)
    
    def aggregate(self):
        aggregated = {
            "total_batches": len(self.results_dirs),
            "batch_summaries": [],
            "overall_stats": {
                "total_tasks": 0,
                "completed_successfully": 0,
                "failed_validation": 0,
                "errors": 0,
                "total_duration_hours": 0.0
            },
            "website_stats": {},
            "all_tasks": []
        }
        
        for results_dir in self.results_dirs:
            summary_file = results_dir / "execution_summary.json"
            
            if not summary_file.exists():
                print(f"Warning: No summary found in {results_dir}")
                continue
            
            with open(summary_file, 'r') as f:
                summary = json.load(f)
            
            aggregated["batch_summaries"].append({
                "batch_dir": str(results_dir),
                "stats": summary["overall_stats"],
                "duration_hours": summary["execution_time"]["duration_hours"]
            })
            
            aggregated["overall_stats"]["total_tasks"] += summary["overall_stats"]["total_tasks"]
            aggregated["overall_stats"]["completed_successfully"] += summary["overall_stats"]["completed_successfully"]
            aggregated["overall_stats"]["failed_validation"] += summary["overall_stats"]["failed_validation"]
            aggregated["overall_stats"]["errors"] += summary["overall_stats"]["errors"]
            aggregated["overall_stats"]["total_duration_hours"] += summary["execution_time"]["duration_hours"]
            
            for website, stats in summary.get("website_breakdown", {}).items():
                if website not in aggregated["website_stats"]:
                    aggregated["website_stats"][website] = {
                        "total": 0,
                        "completed": 0,
                        "failed": 0,
                        "errors": 0
                    }
                aggregated["website_stats"][website]["total"] += stats["total"]
                aggregated["website_stats"][website]["completed"] += stats["completed"]
                aggregated["website_stats"][website]["failed"] += stats["failed"]
                aggregated["website_stats"][website]["errors"] += stats["errors"]
            
            aggregated["all_tasks"].extend(summary.get("task_details", []))
        
        total_tasks = aggregated["overall_stats"]["total_tasks"]
        if total_tasks > 0:
            aggregated["overall_stats"]["success_rate"] = round(
                aggregated["overall_stats"]["completed_successfully"] / total_tasks * 100, 2
            )
        
        for website in aggregated["website_stats"]:
            total = aggregated["website_stats"][website]["total"]
            if total > 0:
                aggregated["website_stats"][website]["success_rate"] = round(
                    aggregated["website_stats"][website]["completed"] / total * 100, 2
                )
        
        with open(self.output_file, 'w') as f:
            json.dump(aggregated, f, indent=2)
        
        print(f"\n{'='*80}")
        print("AGGREGATED RESULTS")
        print(f"{'='*80}")
        print(f"Total Batches: {aggregated['total_batches']}")
        print(f"Total Tasks: {total_tasks}")
        print(f"Completed Successfully: {aggregated['overall_stats']['completed_successfully']}")
        print(f"Failed Validation: {aggregated['overall_stats']['failed_validation']}")
        print(f"Errors: {aggregated['overall_stats']['errors']}")
        print(f"Success Rate: {aggregated['overall_stats']['success_rate']:.2f}%")
        print(f"Total Duration: {aggregated['overall_stats']['total_duration_hours']:.2f} hours")
        print(f"\nResults saved to: {self.output_file}")
        print(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Batch processing and result aggregation utilities'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    split_parser = subparsers.add_parser('split', help='Split tasks into batches')
    split_parser.add_argument('input_file', help='Input JSONL file')
    split_parser.add_argument(
        '--batch-size',
        type=int,
        default=100,
        help='Tasks per batch (default: 100)'
    )
    split_parser.add_argument(
        '--output-dir',
        type=str,
        default='batches',
        help='Output directory for batches (default: batches)'
    )
    split_parser.add_argument(
        '--by-website',
        action='store_true',
        help='Split by website instead of size'
    )
    
    aggregate_parser = subparsers.add_parser('aggregate', help='Aggregate batch results')
    aggregate_parser.add_argument(
        'results_dirs',
        nargs='+',
        help='Result directories to aggregate'
    )
    aggregate_parser.add_argument(
        '--output',
        type=str,
        default='aggregated_results.json',
        help='Output file (default: aggregated_results.json)'
    )
    
    args = parser.parse_args()
    
    if args.command == 'split':
        processor = BatchProcessor(
            input_file=args.input_file,
            output_dir=args.output_dir
        )
        
        if args.by_website:
            processor.split_by_website()
        else:
            processor.split_by_size(batch_size=args.batch_size)
    
    elif args.command == 'aggregate':
        aggregator = ResultAggregator(
            results_dirs=args.results_dirs,
            output_file=args.output
        )
        aggregator.aggregate()
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()