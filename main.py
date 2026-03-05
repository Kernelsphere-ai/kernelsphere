import asyncio
import json
import argparse
import logging
import os
from pathlib import Path
from playwright.async_api import async_playwright
from dotenv import load_dotenv

from agent import WebAutomationAgent
from llm import GeminiLLM
from stealth import configure_stealth_browser, get_random_user_agent, STEALTH_ARGS
from adapter import Adapter, normalize_task_data, load_all_tasks
from task_result_tracker import TaskResultTracker
from task_logger import TaskLogger
from answer_validator import AnswerValidator
from proxy_manager import get_proxy_manager
from email_otp_handler import EmailOTPHandler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('web_run.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)


async def run_task(
    task_data: dict,
    adapter: Adapter,
    task_tracker: TaskResultTracker,
    task_logger: TaskLogger,
    model: str = "gemini-2.0-flash",
    max_steps: int = 30,
    headless: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    captcha_api_key: str = None,
    manual_captcha: bool = True,
    captcha_max_wait: int = 120,
    use_proxy: bool = False,
    proxy_country: str = None
) -> dict:

    logger.info("="*80)
    logger.info(f"Running Task: {task_data['web_name']}--{task_data['task_id']}")
    logger.info(f"Question: {task_data['question']}")
    logger.info(f"Start URL: {task_data['start_url']}")
    logger.info(f"Proxy enabled: {use_proxy}")
    logger.info("="*80)

    task_logger.start_task(
        task_id=f"{task_data['web_name']}--{task_data['task_id']}",
        website=task_data['web_name'],
        question=task_data['question'],
        start_url=task_data['start_url']
    )

    task_session_id = f"task_{task_data['web_name']}_{task_data['task_id']}"
    proxy_manager = None
    proxy_config = None

    if use_proxy:
        logger.info(f"Initializing proxy manager for task: {task_session_id}")
        proxy_manager = get_proxy_manager()

        if not proxy_manager.has_proxies():
            logger.warning("Proxy enabled but no proxies configured - running without proxy")
            use_proxy = False
        else:
            logger.info(f"Getting proxy for session: {task_session_id}")
            proxy_config = await proxy_manager.get_proxy_for_session(task_session_id, proxy_country)

            if proxy_config:
                logger.info(f"Using proxy: {proxy_config.host}:{proxy_config.port} ({proxy_config.provider.value})")
                logger.info(f"Username: {proxy_config.username}")
            else:
                logger.warning("Failed to get proxy from pool - running without proxy")
                use_proxy = False

    async with async_playwright() as p:
        browser = None
        context = None

        try:
            logger.info("Preparing browser launch options")
            launch_options = {
                'headless': headless,
                'args': STEALTH_ARGS.copy()
            }

            if use_proxy and proxy_config:
                launch_options['proxy'] = {'server': 'http://per-context'}
                logger.info("Browser launched with per-context proxy mode")

            logger.info(f"Launching browser (headless={headless})")
            browser = await p.chromium.launch(**launch_options)
            logger.info("Browser launched successfully")

            user_agent = get_random_user_agent()
            logger.info(f"Using user agent: {user_agent[:50]}...")

            logger.info("Preparing context options")
            context_options = {
                'user_agent': user_agent,
                'viewport': {'width': viewport_width, 'height': viewport_height},
                'locale': 'en-US',
                'timezone_id': 'America/New_York',
                'permissions': ['geolocation'],
                'geolocation': {'latitude': 40.7128, 'longitude': -74.0060},
                'java_script_enabled': True,
                'bypass_csp': True,
                'ignore_https_errors': True
            }

            if use_proxy and proxy_config:
                context_options['proxy'] = proxy_config.to_playwright_dict()
                logger.info(f"Context configured with proxy: {proxy_config.host}:{proxy_config.port}")

            logger.info("Creating browser context")
            context = await browser.new_context(**context_options)
            logger.info("Browser context created")

            logger.info("Configuring stealth browser")
            await configure_stealth_browser(context)
            logger.info("Stealth configuration applied")

            logger.info("Creating new page")
            page = await context.new_page()
            logger.info("Page created")

            logger.info(f"Initializing LLM with model: {model}")
            llm = GeminiLLM(model_name=model)
            logger.info("LLM initialized")

            email_handler = None
            email_address = os.getenv("EMAIL_ADDRESS")
            email_password = os.getenv("EMAIL_PASSWORD")

            if email_address and email_password:
                logger.info("Initializing email OTP handler")
                email_handler = EmailOTPHandler(email_address, email_password)
                logger.info("Email OTP handler initialized")
            else:
                logger.warning("EMAIL_ADDRESS or EMAIL_PASSWORD not found in environment - OTP login will not work")

            logger.info("Creating WebAutomationAgent")
            agent = WebAutomationAgent(
                page=page,
                llm=llm,
                max_steps=max_steps,
                captcha_api_key=captcha_api_key,
                manual_captcha=manual_captcha,
                captcha_max_wait=captcha_max_wait,
                task_logger=task_logger,
                email_handler=email_handler
            )
            logger.info(f"Agent created with max_steps={max_steps}")

            logger.info(f"Starting agent run - Question: {task_data['question'][:100]}...")
            logger.info(f"Navigating to start URL: {task_data['start_url']}")
            history = await agent.run(task_data['question'], task_data['start_url'])
            logger.info(f"Agent run completed - Total steps: {history.total_steps}, Success: {history.success}")

            final_answer = history.final.get('final_answer', '')
            logger.info(f"Final answer extracted (length: {len(final_answer)} chars)")

            task_logger.log_step(
                step_num=history.total_steps + 1,
                action="done",
                reasoning="Task completed",
                result=final_answer[:500] if final_answer else "No answer",
                url=page.url
            )

            logger.info("Validating answer")
            validator = AnswerValidator()
            is_valid, validation = validator.validate_answer(
                answer=final_answer,
                task_question=task_data['question']
            )
            logger.info(f"Answer validation result: is_valid={is_valid}")

            is_success = is_valid and history.success
            logger.info(f"Final task status: is_success={is_success}")

            task_logger.end_task(
                success=is_success,
                final_answer=final_answer,
                validation_result=validation
            )

            logger.info(f"Task completed: Success={is_success}")
            logger.info(f"Final answer: {final_answer[:200]}...")

            if use_proxy and proxy_config and proxy_manager:
                if is_success:
                    logger.info("Marking proxy as successful")
                    await proxy_manager.pool.mark_proxy_success(proxy_config, 1.0)
                else:
                    logger.info("Marking proxy as failed")
                    await proxy_manager.pool.mark_proxy_failure(proxy_config, "Task failed")

            return {
                'task_id': f"{task_data['web_name']}--{task_data['task_id']}",
                'success': is_success,
                'final_answer': final_answer,
                'validation': validation,
                'total_steps': history.total_steps,
                'duration': 0
            }

        except Exception as e:
            logger.error(f"Error running task: {e}", exc_info=True)

            task_logger.end_task(
                success=False,
                final_answer="",
                validation_result={'is_valid': False, 'reason': str(e)}
            )

            if use_proxy and proxy_config and proxy_manager:
                logger.info("Marking proxy as failed due to exception")
                await proxy_manager.pool.mark_proxy_failure(proxy_config, str(e))

            return {
                'task_id': f"{task_data['web_name']}--{task_data['task_id']}",
                'success': False,
                'final_answer': '',
                'error': str(e),
                'total_steps': 0,
                'duration': 0
            }

        finally:
            logger.info("Cleaning up browser resources")
            if context:
                try:
                    logger.info("Closing browser context")
                    await context.close()
                    logger.info("Browser context closed")
                except Exception as e:
                    logger.warning(f"Error closing context: {e}")
            if browser:
                try:
                    logger.info("Closing browser")
                    await browser.close()
                    logger.info("Browser closed")
                except Exception as e:
                    logger.warning(f"Error closing browser: {e}")


async def run_single_task(
    task: str,
    start_url: str,
    output_dir: str = "results",
    model: str = "gemini-2.0-flash",
    max_steps: int = 30,
    headless: bool = True,
    captcha_api_key: str = None,
    manual_captcha: bool = True,
    use_proxy: bool = False,
    proxy_country: str = None
):
    logger.info("="*80)
    logger.info("Starting Single Task")
    logger.info(f"Task: {task}")
    logger.info(f"Start URL: {start_url}")
    logger.info(f"Model: {model}")
    logger.info(f"Max steps: {max_steps}")
    logger.info(f"Headless: {headless}")
    logger.info(f"Proxy enabled: {use_proxy}")
    logger.info("="*80)

    os.makedirs(output_dir, exist_ok=True)

    task_data = {
        'web_name': 'single',
        'task_id': '0',
        'question': task,
        'start_url': start_url
    }

    adapter = Adapter()
    task_tracker = TaskResultTracker(output_dir)
    task_logger = TaskLogger(output_dir)

    result = await run_task(
        task_data=task_data,
        adapter=adapter,
        task_tracker=task_tracker,
        task_logger=task_logger,
        model=model,
        max_steps=max_steps,
        headless=headless,
        captcha_api_key=captcha_api_key,
        manual_captcha=manual_captcha,
        use_proxy=use_proxy,
        proxy_country=proxy_country
    )

    output_file = Path(output_dir) / 'result.json'
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)

    logger.info("="*80)
    logger.info("SINGLE TASK COMPLETE")
    logger.info(f"Success: {result['success']}")
    logger.info(f"Final answer: {result.get('final_answer', '')[:200]}")
    logger.info(f"Result saved to: {output_file}")
    logger.info("="*80)


async def run_benchmark(
    tasks_file: str,
    output_dir: str = "webbench_results",
    model: str = "gemini-2.0-flash",
    max_steps: int = 30,
    headless: bool = True,
    limit: int = None,
    captcha_api_key: str = None,
    manual_captcha: bool = True,
    use_proxy: bool = False,
    proxy_country: str = None
):

    logger.info("="*80)
    logger.info("Starting Benchmark")
    logger.info(f"Tasks file: {tasks_file}")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Model: {model}")
    logger.info(f"Max steps: {max_steps}")
    logger.info(f"Headless: {headless}")
    logger.info(f"Limit: {limit}")
    logger.info(f"Proxy enabled: {use_proxy}")
    logger.info("="*80)

    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"Created output directory: {output_dir}")

    adapter = Adapter()
    logger.info("Adapter initialized")

    task_tracker = TaskResultTracker(output_dir)
    logger.info("TaskResultTracker initialized")

    logger.info(f"Loading tasks from: {tasks_file}")
    tasks = load_all_tasks(tasks_file)

    if limit:
        logger.info(f"Applying limit: {limit}")
        tasks = tasks[:limit]

    logger.info(f"Loaded {len(tasks)} tasks from {tasks_file}")
    logger.info(f"Proxy enabled: {use_proxy}")

    if use_proxy:
        logger.info("Initializing proxy manager")
        proxy_manager = get_proxy_manager()
        if proxy_manager.has_proxies():
            logger.info(f"Proxy manager initialized with {len(proxy_manager.pool.proxies)} proxies")
        else:
            logger.warning("Proxy enabled but no proxies configured")
            logger.warning("Add to .env: PROXYEMPIRE_USERNAME and PROXYEMPIRE_PASSWORD")

    results = []

    for i, task in enumerate(tasks, 1):
        logger.info(f"\n{'='*80}")
        logger.info(f"Task {i}/{len(tasks)}")
        logger.info(f"{'='*80}")

        task_logger = TaskLogger(output_dir)
        logger.info(f"TaskLogger initialized for task {i}")

        logger.info(f"Starting task: {task['web_name']}--{task['task_id']}")
        result = await run_task(
            task_data=task,
            adapter=adapter,
            task_tracker=task_tracker,
            task_logger=task_logger,
            model=model,
            max_steps=max_steps,
            headless=headless,
            captcha_api_key=captcha_api_key,
            manual_captcha=manual_captcha,
            use_proxy=use_proxy,
            proxy_country=proxy_country
        )
        logger.info(f"Task {i}/{len(tasks)} completed with result: {result['success']}")

        results.append(result)

        logger.info("Recording task result in tracker")
        task_tracker.record_task_result(
            website=task['web_name'],
            task_id=f"{task['web_name']}--{task['task_id']}",
            success=result['success'],
            task_description=task['question']
        )

        logger.info(f"Waiting 2 seconds before next task")
        await asyncio.sleep(2)

    passed = sum(1 for r in results if r['success'])
    total = len(results)
    success_rate = (100 * passed / total) if total > 0 else 0

    logger.info("="*80)
    logger.info("Printing summary")
    logger.info("="*80)
    task_tracker.print_summary()

    summary_file = Path(output_dir) / 'benchmark_summary.json'
    logger.info(f"Saving summary to: {summary_file}")
    with open(summary_file, 'w') as f:
        json.dump({
            'total_tasks': total,
            'passed': passed,
            'failed': total - passed,
            'success_rate': success_rate,
            'results': results
        }, f, indent=2)
    logger.info("Summary saved")

    logger.info(f"\n{'='*80}")
    logger.info(f"BENCHMARK COMPLETE")
    logger.info(f"{'='*80}")
    logger.info(f"Passed: {passed}/{total} ({int(success_rate)}%)")
    logger.info(f"Results saved to: {output_dir}")
    logger.info(f"Summary saved to: {summary_file}")
    logger.info(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(description=' Benchmark Runner')
    subparsers = parser.add_subparsers(dest='mode', help='Run mode')

    single_parser = subparsers.add_parser('run', help='Run a single task')
    single_parser.add_argument('--task', type=str, required=True, help='Task description in plain English')
    single_parser.add_argument('--start-url', type=str, required=True, help='Starting URL for the task')
    single_parser.add_argument('--output', type=str, default='results', help='Output directory')
    single_parser.add_argument('--model', type=str, default='gemini-2.0-flash', help='Gemini model')
    single_parser.add_argument('--max-steps', type=int, default=30, help='Maximum steps per task')
    single_parser.add_argument('--headless', action='store_true', default=True, help='Run in headless mode')
    single_parser.add_argument('--headed', action='store_true', help='Run in headed mode')
    single_parser.add_argument('--captcha-api-key', type=str, default=None, help='2Captcha API key')
    single_parser.add_argument('--no-manual-captcha', action='store_true', help='Disable manual CAPTCHA')
    single_parser.add_argument('--use-proxy', action='store_true', help='Enable proxy usage')
    single_parser.add_argument('--proxy-country', type=str, default=None, help='Proxy country code')

    bench_parser = subparsers.add_parser('benchmark', help='Run benchmark from tasks file')
    bench_parser.add_argument('--tasks', type=str, required=True, help='Path to tasks JSONL file')
    bench_parser.add_argument('--output', type=str, default='results', help='Output directory')
    bench_parser.add_argument('--model', type=str, default='gemini-2.0-flash', help='Gemini model')
    bench_parser.add_argument('--max-steps', type=int, default=30, help='Maximum steps per task')
    bench_parser.add_argument('--headless', action='store_true', default=True, help='Run in headless mode')
    bench_parser.add_argument('--headed', action='store_true', help='Run in headed mode')
    bench_parser.add_argument('--limit', type=int, default=None, help='Limit number of tasks')
    bench_parser.add_argument('--captcha-api-key', type=str, default=None, help='2Captcha API key')
    bench_parser.add_argument('--no-manual-captcha', action='store_true', help='Disable manual CAPTCHA')
    bench_parser.add_argument('--use-proxy', action='store_true', help='Enable proxy usage')
    bench_parser.add_argument('--proxy-country', type=str, default=None, help='Proxy country code')

    args = parser.parse_args()

    if args.mode is None:
        parser.print_help()
        return

    logger.info("="*80)
    logger.info("Benchmark Runner - Starting")
    logger.info(f"Arguments: {args}")
    logger.info("="*80)

    if args.mode == 'run':
        headless = not args.headed if args.headed else args.headless
        manual_captcha = not args.no_manual_captcha

        logger.info(f"Computed headless: {headless}")
        logger.info(f"Computed manual_captcha: {manual_captcha}")

        asyncio.run(run_single_task(
            task=args.task,
            start_url=args.start_url,
            output_dir=args.output,
            model=args.model,
            max_steps=args.max_steps,
            headless=headless,
            captcha_api_key=args.captcha_api_key,
            manual_captcha=manual_captcha,
            use_proxy=args.use_proxy,
            proxy_country=args.proxy_country
        ))

    elif args.mode == 'benchmark':
        headless = not args.headed if args.headed else args.headless
        manual_captcha = not args.no_manual_captcha

        logger.info(f"Computed headless: {headless}")
        logger.info(f"Computed manual_captcha: {manual_captcha}")

        asyncio.run(run_benchmark(
            tasks_file=args.tasks,
            output_dir=args.output,
            model=args.model,
            max_steps=args.max_steps,
            headless=headless,
            limit=args.limit,
            captcha_api_key=args.captcha_api_key,
            manual_captcha=manual_captcha,
            use_proxy=args.use_proxy,
            proxy_country=args.proxy_country
        ))


if __name__ == '__main__':
    main()
