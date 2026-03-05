import argparse
import asyncio
import json
import logging
import os
import sys
import re
from pathlib import Path
from typing import Dict, List, Any


MAX_PARALLEL = 3

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('parallel_runner.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

CHAR_REPLACEMENTS = {
    '\x80': 'EUR', '\x82': ',', '\x83': 'f', '\x84': '"', '\x85': '...',
    '\x86': '+', '\x87': '++', '\x88': '^', '\x89': 'per-mille', '\x8a': 'S',
    '\x8b': '<', '\x8c': 'OE', '\x8e': 'Z', '\x91': "'", '\x92': "'",
    '\x93': '"', '\x94': '"', '\x95': '*', '\x96': '-', '\x97': '--',
    '\x98': '~', '\x99': 'TM', '\x9a': 's', '\x9b': '>', '\x9c': 'oe',
    '\x9e': 'z', '\x9f': 'Y',
}


def normalize_text(text: str) -> str:
    if not text:
        return text
    
    for bad, good in CHAR_REPLACEMENTS.items():
        text = text.replace(bad, good)
    
    try:
        text = text.encode('utf-8', errors='replace').decode('utf-8')
    except Exception:
        try:
            text = text.encode('latin-1', errors='replace').decode('utf-8', errors='replace')
        except Exception:
            text = ''.join(char for char in text if ord(char) < 128)
    
    text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    
    return text


def normalize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return data
    
    result = {}
    for key, value in data.items():
        if isinstance(key, str):
            key = normalize_text(key)
        
        if isinstance(value, str):
            result[key] = normalize_text(value)
        elif isinstance(value, dict):
            result[key] = normalize_dict(value)
        elif isinstance(value, list):
            result[key] = normalize_list(value)
        else:
            result[key] = value
    
    return result


def normalize_list(data: list) -> list:
    if not isinstance(data, list):
        return data
    
    result = []
    for item in data:
        if isinstance(item, str):
            result.append(normalize_text(item))
        elif isinstance(item, dict):
            result.append(normalize_dict(item))
        elif isinstance(item, list):
            result.append(normalize_list(item))
        else:
            result.append(item)
    
    return result


def prepare_subprocess_result(result: Dict[str, Any]) -> str:
    result = normalize_dict(result)
    
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        try:
            return json.dumps(result, ensure_ascii=True)
        except Exception:
            return json.dumps({
                'task_id': result.get('task_id', 'unknown'),
                'success': False,
                'final_answer': '',
                'error': 'JSON serialization failed',
                'total_steps': 0
            }, ensure_ascii=True)


def safe_json_loads(text: str) -> Any:
    text = normalize_text(text)
    
    try:
        data = json.loads(text)
        
        if isinstance(data, dict):
            return normalize_dict(data)
        elif isinstance(data, list):
            return normalize_list(data)
        else:
            return data
            
    except json.JSONDecodeError as e:
        text = text.lstrip('\ufeff\xef\xbb\xbf')
        
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return normalize_dict(data)
            elif isinstance(data, list):
                return normalize_list(data)
            else:
                return data
        except:
            raise e


def safe_print(message: str, file=sys.stderr):
    """Safely print messages, handling Unicode encoding errors on Windows"""
    try:
        print(message, file=file)
    except UnicodeEncodeError:
        safe_message = message.encode('ascii', errors='replace').decode('ascii')
        print(safe_message, file=file)


async def run_single_task(task_data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        safe_print(f'[DEBUG] Starting task: {task_data["task_id"]}')
        
        
        from dotenv import load_dotenv
        load_dotenv()
        
        from playwright.async_api import async_playwright
        from agent import WebAutomationAgent
        from llm import GeminiLLM
        from stealth import configure_stealth_browser, get_random_user_agent, STEALTH_ARGS
        
        task_id = task_data['task_id']
        question = task_data['question']
        start_url = task_data['start_url']
        max_steps = task_data.get('max_steps', 30)
        use_proxy = task_data.get('use_proxy', False)
        proxy_country = task_data.get('proxy_country', None) 
        
        safe_print(f'[DEBUG] Task: {question[:100]}...')
        safe_print(f'[DEBUG] Start URL: {start_url}')
        safe_print(f'[DEBUG] Proxy enabled: {use_proxy}')
        
        api_key = os.getenv('GOOGLE_API_KEY')
        if not api_key:
            safe_print('[ERROR] GOOGLE_API_KEY not set')
            return {
                'task_id': task_id,
                'success': False,
                'final_answer': '',
                'error': 'GOOGLE_API_KEY not set',
                'total_steps': 0
            }
        
        task_session_id = f"task_{task_id}"
        proxy_manager = None
        proxy_config = None
        
        if use_proxy:
            try:
                safe_print(f'[DEBUG] Initializing proxy manager for task: {task_session_id}')
                from proxy_manager import get_proxy_manager
                proxy_manager = get_proxy_manager()
                
                if not proxy_manager.has_proxies():
                    safe_print('[WARNING] Proxy enabled but no proxies configured - running without proxy')
                    use_proxy = False
                else:
                    safe_print(f'[DEBUG] Getting proxy for session: {task_session_id}')
                    proxy_config = await proxy_manager.get_proxy_for_session(task_session_id, proxy_country)
                    
                    if proxy_config:
                        safe_print(f'[DEBUG] Using proxy: {proxy_config.host}:{proxy_config.port}')
                    else:
                        safe_print('[WARNING] Failed to get proxy from pool - running without proxy')
                        use_proxy = False
            except Exception as proxy_error:
                safe_print(f'[ERROR] Proxy initialization failed: {str(proxy_error)} - running without proxy')
                use_proxy = False
                proxy_manager = None
                proxy_config = None
        
        async with async_playwright() as p:
            browser = None
            context = None
            
            try:
                safe_print('[DEBUG] Preparing browser launch options')
                launch_options = {
                    'headless': False,
                    'args': STEALTH_ARGS.copy()
                }
                
                if use_proxy and proxy_config:
                    launch_options['proxy'] = {'server': 'http://per-context'}
                    safe_print('[DEBUG] Browser will launch with per-context proxy mode')
                
                safe_print('[DEBUG] Launching browser')
                browser = await p.chromium.launch(**launch_options)
                safe_print('[DEBUG] Browser launched successfully')
                
                user_agent = get_random_user_agent()
                safe_print(f'[DEBUG] Using user agent: {user_agent[:50]}...')
                
                safe_print('[DEBUG] Preparing context options')
                context_options = {
                    'user_agent': user_agent,
                    'viewport': {'width': 1280, 'height': 720},
                    'locale': 'en-US',
                    'timezone_id': 'America/New_York',
                    'permissions': ['geolocation'],
                    'geolocation': {'latitude': 40.7128, 'longitude': -74.0060},
                    'java_script_enabled': True,
                    'bypass_csp': True,
                    'ignore_https_errors': True
                }
                
                if use_proxy and proxy_config:
                    try:
                        proxy_dict = proxy_config.to_playwright_dict()
                        context_options['proxy'] = proxy_dict
                        safe_print(f'[DEBUG] Context configured with proxy: {proxy_config.host}:{proxy_config.port}')
                    except Exception as proxy_error:
                        safe_print(f'[ERROR] Failed to configure proxy in context: {str(proxy_error)} - continuing without proxy')
                        use_proxy = False
                        proxy_config = None
                        if browser:
                            await browser.close()
                        # Relaunch without proxy
                        launch_options = {
                            'headless': False,
                            'args': STEALTH_ARGS.copy()  # Use stealth args for relaunch too
                        }
                        browser = await p.chromium.launch(**launch_options)
                        safe_print('[DEBUG] Browser relaunched without proxy')
                
                safe_print('[DEBUG] Creating browser context')
                context = await browser.new_context(**context_options)
                safe_print('[DEBUG] Browser context created')
                
                safe_print('[DEBUG] Configuring stealth browser')
                await configure_stealth_browser(context)  # Add stealth configuration
                safe_print('[DEBUG] Stealth configuration applied')
                
                page = await context.new_page()
                safe_print('[DEBUG] Page created')
                
                if use_proxy and proxy_config:
                    safe_print('[DEBUG] Testing proxy connection...')
                    try:
                        test_response = await page.goto('https://api.ipify.org?format=json', timeout=15000, wait_until='domcontentloaded')
                        if test_response and test_response.ok:
                            ip_data = await page.content()
                            safe_print(f'[DEBUG] Proxy connection successful - IP response: {ip_data[:100]}')
                        else:
                            safe_print(f'[WARNING] Proxy test failed - Status: {test_response.status if test_response else "No response"}')
                            safe_print('[WARNING] Disabling proxy and retrying without it')
                            # Disable proxy and recreate everything
                            await page.close()
                            await context.close()
                            await browser.close()
                            
                            use_proxy = False
                            proxy_config = None
                            
                            launch_options = {'headless': False, 'args': STEALTH_ARGS.copy()}
                            browser = await p.chromium.launch(**launch_options)
                            context_options_no_proxy = {
                                'user_agent': user_agent,
                                'viewport': {'width': 1280, 'height': 720},
                                'locale': 'en-US',
                                'timezone_id': 'America/New_York',
                                'permissions': ['geolocation'],
                                'geolocation': {'latitude': 40.7128, 'longitude': -74.0060},
                                'java_script_enabled': True,
                                'bypass_csp': True,
                                'ignore_https_errors': True
                            }
                            context = await browser.new_context(**context_options_no_proxy)
                            await configure_stealth_browser(context)
                            page = await context.new_page()
                            safe_print('[DEBUG] Browser restarted without proxy')
                    except Exception as proxy_test_error:
                        safe_print(f'[ERROR] Proxy connection test failed: {str(proxy_test_error)}')
                        safe_print('[WARNING] Disabling proxy due to connection failure')
                        # Disable proxy and recreate everything
                        await page.close()
                        await context.close()
                        await browser.close()
                        
                        use_proxy = False
                        proxy_config = None
                        
                        launch_options = {'headless': False, 'args': STEALTH_ARGS.copy()}
                        browser = await p.chromium.launch(**launch_options)
                        context_options_no_proxy = {
                            'user_agent': user_agent,
                            'viewport': {'width': 1280, 'height': 720},
                            'locale': 'en-US',
                            'timezone_id': 'America/New_York',
                            'permissions': ['geolocation'],
                            'geolocation': {'latitude': 40.7128, 'longitude': -74.0060},
                            'java_script_enabled': True,
                            'bypass_csp': True,
                            'ignore_https_errors': True
                        }
                        context = await browser.new_context(**context_options_no_proxy)
                        await configure_stealth_browser(context)
                        page = await context.new_page()
                        safe_print('[DEBUG] Browser restarted without proxy after test failure')
                
                llm = GeminiLLM()
                agent = WebAutomationAgent(page=page, llm=llm, max_steps=max_steps)
                
                safe_print('[DEBUG] Running agent...')
                try:
                    history = await agent.run(question, start_url)
                    safe_print(f'[DEBUG] Agent completed - success: {history.success}, steps: {history.total_steps}')
                    
                    # Check if there's an error in the history
                    if hasattr(history, 'error') and history.error:
                        safe_print(f'[ERROR] Agent reported error: {history.error}')
                    
                    # Check final answer for error messages
                    final_answer_text = history.final.get('final_answer', '')
                    if 'failed' in final_answer_text.lower() or 'error' in final_answer_text.lower():
                        safe_print(f'[WARNING] Suspicious final answer: {final_answer_text[:100]}')
                    
                except Exception as agent_error:
                    safe_print(f'[ERROR] Agent.run() raised exception: {str(agent_error)}')
                    import traceback
                    traceback.print_exc(file=sys.stderr)
                    raise
                
                result = {
                    'task_id': task_id,
                    'success': history.success,
                    'final_answer': normalize_text(history.final.get('final_answer', '')),
                    'error': None,
                    'total_steps': history.total_steps,
                    'url': normalize_text(history.final.get('url', '')),
                    'title': normalize_text(history.final.get('title', ''))
                }
                
                safe_print(f'[DEBUG] Task completed - Success: {result["success"]}, Steps: {result["total_steps"]}')
                if not result['success']:
                    safe_print(f'[DEBUG] Failure reason - Final answer: {result["final_answer"][:200]}')
                
                if use_proxy and proxy_config and proxy_manager:
                    try:
                        if result['success']:
                            safe_print('[DEBUG] Marking proxy as successful')
                            await proxy_manager.pool.mark_proxy_success(proxy_config)
                        else:
                            safe_print('[DEBUG] Marking proxy as failed')
                            await proxy_manager.pool.mark_proxy_failure(proxy_config, "Task failed")
                    except Exception as e:
                        safe_print(f'[WARNING] Failed to update proxy stats: {str(e)}')
                
                return result
                
            except Exception as e:
                safe_print(f'[ERROR] Agent execution error: {str(e)}')
                import traceback
                traceback.print_exc(file=sys.stderr)
                
                if use_proxy and proxy_config and proxy_manager:
                    try:
                        safe_print('[DEBUG] Marking proxy as failed due to exception')
                        await proxy_manager.pool.mark_proxy_failure(proxy_config, str(e))
                    except Exception as e2:
                        safe_print(f'[WARNING] Failed to mark proxy failure: {str(e2)}')
                
                result = {
                    'task_id': task_id,
                    'success': False,
                    'final_answer': '',
                    'error': str(e)[:200],
                    'total_steps': 0
                }
                return result
            finally:
                safe_print('[DEBUG] Cleaning up browser resources')
                if context:
                    try:
                        await context.close()
                        safe_print('[DEBUG] Browser context closed')
                    except Exception as e:
                        safe_print(f'[WARNING] Error closing context: {e}')
                if browser:
                    try:
                        await browser.close()
                        safe_print('[DEBUG] Browser closed')
                    except Exception as e:
                        safe_print(f'[WARNING] Error closing browser: {e}')
        
    except Exception as e:
        safe_print(f'[ERROR] Task failed with exception: {str(e)}')
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {
            'task_id': task_data.get('task_id', 'unknown'),
            'success': False,
            'final_answer': '',
            'error': str(e)[:200],
            'total_steps': 0
        }


async def run_task_subprocess(task_data: Dict[str, Any], semaphore: asyncio.Semaphore) -> Dict[str, Any]:
    async with semaphore:
        try:
            env = os.environ.copy()
            env['PYTHONPATH'] = os.pathsep.join(sys.path)
            env['PYTHONIOENCODING'] = 'utf-8'
            
            task_json = json.dumps(task_data, ensure_ascii=True)
            
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                __file__,
                '--task-json',
                task_json,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=600
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except:
                    pass
                safe_print(f'[PARENT] Task {task_data["task_id"]} timeout after 10 minutes')
                return {
                    'task_id': task_data['task_id'],
                    'success': False,
                    'final_answer': '',
                    'error': 'Task timeout after 10 minutes',
                    'total_steps': 0
                }
            
            if proc.returncode == 0:
                try:
                    stdout_text = stdout.decode('utf-8', errors='replace').strip()
                    stderr_text = stderr.decode('utf-8', errors='replace').strip()
                    
                    if stderr_text:
                        debug_lines = [line for line in stderr_text.split('\n') if '[DEBUG]' in line or '[ERROR]' in line or '[WARNING]' in line]
                        if debug_lines:
                            for line in debug_lines[-20:]: 
                                safe_print(f'  {line}')
                    
                    lines = stdout_text.split('\n')
                    json_line = None
                    for line in reversed(lines):
                        line = line.strip()
                        if line.startswith('{') and line.endswith('}'):
                            json_line = line
                            break
                    
                    if json_line:
                        result = safe_json_loads(json_line)
                        print(f'[PARENT] Task {task_data["task_id"]} completed: {result["success"]}')
                        return result
                    else:
                        safe_print(f'[PARENT] No JSON in output for {task_data["task_id"]}')
                        return {
                            'task_id': task_data['task_id'],
                            'success': False,
                            'final_answer': '',
                            'error': 'No JSON output found',
                            'total_steps': 0
                        }
                        
                except Exception as e:
                    safe_print(f'[PARENT] Parse error for {task_data["task_id"]}: {e}')
                    return {
                        'task_id': task_data['task_id'],
                        'success': False,
                        'final_answer': '',
                        'error': f'Failed to parse result: {str(e)[:100]}',
                        'total_steps': 0
                    }
            else:
                stderr_text = stderr.decode('utf-8', errors='replace').strip()
                error_msg = stderr_text[-500:] if len(stderr_text) > 500 else stderr_text
                safe_print(f'[PARENT] Subprocess failed for {task_data["task_id"]}: code {proc.returncode}')
                safe_print(f'[PARENT] Error details: {error_msg}')
                return {
                    'task_id': task_data['task_id'],
                    'success': False,
                    'final_answer': '',
                    'error': f'Subprocess failed (code {proc.returncode}): {error_msg[:100]}',
                    'total_steps': 0
                }
                
        except Exception as e:
            safe_print(f'[PARENT] Exception for {task_data["task_id"]}: {e}')
            return {
                'task_id': task_data['task_id'],
                'success': False,
                'final_answer': '',
                'error': f'Failed to start subprocess: {str(e)[:100]}',
                'total_steps': 0
            }


async def run_parallel_tasks(tasks: List[Dict[str, Any]], max_parallel: int = MAX_PARALLEL) -> List[Dict[str, Any]]:
    semaphore = asyncio.Semaphore(max_parallel)
    
    logger.info("="*80)
    logger.info("PARALLEL RUNNER")
    logger.info(f"Total tasks: {len(tasks)}")
    logger.info(f"Max parallel: {max_parallel}")
    logger.info("="*80)
    
    print(f'\n{"="*80}')
    print(f'PARALLEL RUNNER')
    print(f'{"="*80}')
    print(f'Total tasks: {len(tasks)}')
    print(f'Max parallel: {max_parallel}')
    print(f'{"="*80}\n')
    
    task_coroutines = [run_task_subprocess(task, semaphore) for task in tasks]
    results = await asyncio.gather(*task_coroutines)
    
    passed = sum(1 for r in results if r['success'])
    total = len(results)
    
    logger.info("="*80)
    logger.info("RESULTS")
    logger.info(f"Passed: {passed}/{total}")
    logger.info("="*80)
    
    print(f'\n{"="*80}')
    print(f'RESULTS')
    print(f'{"="*80}\n')
    
    headers = ['Task ID', 'Success', 'Steps', 'Answer Preview']
    rows = []
    for r in results:
        status = 'PASS' if r['success'] else 'FAIL'
        answer_preview = r['final_answer'][:50] + '...' if len(r['final_answer']) > 50 else r['final_answer']
        if r.get('error'):
            answer_preview = f"ERROR: {r['error'][:40]}"
        rows.append([r['task_id'], status, str(r['total_steps']), answer_preview])
    
    col_widths = [max(len(str(row[i])) for row in ([headers] + rows)) for i in range(4)]
    
    header_row = ' | '.join(headers[i].ljust(col_widths[i]) for i in range(4))
    print(header_row)
    print('-+-'.join('-' * w for w in col_widths))
    
    for row in rows:
        print(' | '.join(str(row[i]).ljust(col_widths[i]) for i in range(4)))
    
    print(f'\n{"="*80}')
    print(f'SCORE: {passed}/{total} PASSED ({100*passed//total if total > 0 else 0}%)')
    print(f'{"="*80}\n')
    
    logger.info(f"SCORE: {passed}/{total} PASSED ({100*passed//total if total > 0 else 0}%)")
    
    return results


def load_tasks_from_jsonl(filepath: str, limit: int = None, use_proxy: bool = False, proxy_country: str = None) -> List[Dict[str, Any]]:
    """Load tasks from JSONL file with added proxy parameters"""
    tasks = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            if line.strip():
                try:
                    data = json.loads(line.strip())
                    task = {
                        'task_id': data.get('id', data.get('task_id', f'task_{i}')),
                        'website': data.get('web_name', data.get('website', 'Unknown')),
                        'question': normalize_text(data.get('ques', data.get('question', ''))),
                        'start_url': data.get('web', data.get('start_url', '')),
                        'max_steps': 30,
                        'use_proxy': use_proxy, 
                        'proxy_country': proxy_country 
                    }
                    tasks.append(task)
                except Exception as e:
                    print(f'Error parsing line {i}: {e}', file=sys.stderr)
                    continue
    
    logger.info(f"Loaded {len(tasks)} tasks from {filepath}")
    if use_proxy:
        logger.info(f"Proxy enabled for all tasks (country: {proxy_country or 'any'})")
    
    return tasks


def save_results(results: List[Dict[str, Any]], output_file: str):
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    normalized_results = [normalize_dict(r) for r in results]
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(normalized_results, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Results saved to: {output_path}")
    print(f'\nResults saved to: {output_path}')


async def main():
    parser = argparse.ArgumentParser(description='Parallel WebVoyager Task Runner')
    parser.add_argument('--tasks', type=str, help='Path to tasks JSONL file')
    parser.add_argument('--limit', type=int, default=None, help='Limit number of tasks to run')
    parser.add_argument('--max-parallel', type=int, default=MAX_PARALLEL, help='Max parallel tasks')
    parser.add_argument('--output', type=str, default='parallel_results.json', help='Output file')
    parser.add_argument('--task-json', type=str, help='Single task JSON (subprocess mode)')
    parser.add_argument('--use-proxy', action='store_true', help='Enable proxy usage')
    parser.add_argument('--proxy-country', type=str, default=None, help='Proxy country code')  
    
    args = parser.parse_args()
    
    logger.info("="*80)
    logger.info("Parallel WebVoyager Runner - Starting")
    logger.info(f"Arguments: {args}")
    logger.info("="*80)
    
    if args.task_json:
        task_data = json.loads(args.task_json)
        result = await run_single_task(task_data)
        clean_output = prepare_subprocess_result(result)
        print(clean_output)
    else:
        if not args.tasks:
            parser.error('--tasks is required when not in subprocess mode')
        
        tasks = load_tasks_from_jsonl(args.tasks, args.limit, args.use_proxy, args.proxy_country)
        
        if not tasks:
            logger.info("No tasks found!")
            print('No tasks found!')
            return
        
        # Log proxy configuration
        if args.use_proxy:
            logger.info("Proxy enabled for parallel runner")
            from proxy_manager import get_proxy_manager
            proxy_manager = get_proxy_manager()
            if proxy_manager.has_proxies():
                logger.info(f"Proxy manager initialized with {len(proxy_manager.pool.proxies)} proxies")
            else:
                logger.warning("Proxy enabled but no proxies configured")
                logger.warning("Add to .env: PROXYEMPIRE_USERNAME and PROXYEMPIRE_PASSWORD")
        
        results = await run_parallel_tasks(tasks, args.max_parallel)
        
        save_results(results, args.output)
        
        passed = sum(1 for r in results if r['success'])
        total = len(results)
        
        if total > 0 and passed == 0:
            logger.critical("0% pass rate - all tasks failed!")
            print('\nCRITICAL: 0% pass rate - all tasks failed!')
            sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
