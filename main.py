import os
import json
import argparse
import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from playwright._impl._errors import TargetClosedError, Error as PlaywrightError

from agent import WebAutomationAgent
from llm import GeminiLLM
from stealth import configure_stealth_browser, get_random_user_agent, STEALTH_ARGS
from browserbase import create_browserbase_browser, cleanup_browserbase_session
from proxy_manager import get_proxy_manager, ProxyManager

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def run_agent(
    task: str,
    start_url: str,
    max_steps: int = 30,
    headless: bool = False,
    output_file: str = "final_output.json",
    model: str = "gemini-2.0-flash-exp",
    viewport_width: int = 1280,
    viewport_height: int = 720,
    use_browserbase: bool = False,
    browserbase_api_key: str = None,
    browserbase_project_id: str = None,
    browserbase_timeout: int = 600,
    use_proxy: bool = True,
    proxy_country: str = None,
    enable_proxy_health_check: bool = True
) -> dict:
    
    logger.info("="*80)
    logger.info("GEMINI WEB AUTOMATION AGENT")
    logger.info("="*80)
    logger.info(f"Task: {task}")
    logger.info(f"Start URL: {start_url}")
    logger.info(f"Max steps: {max_steps}")
    logger.info(f"Model: {model}")
    logger.info(f"Browserbase: {use_browserbase}")
    logger.info(f"Proxy: {use_proxy}")
    logger.info(f"Headless: {headless}")
    logger.info("="*80)
    
    session_id = None
    proxy_manager = None
    proxy_config = None
    task_session_id = f"task_{hash(task)}"
    
    if use_proxy:
        proxy_manager = get_proxy_manager()
        
        if not proxy_manager.has_proxies():
            logger.warning("Proxy enabled but no proxies configured. Running without proxy.")
            use_proxy = False
        else:
            if enable_proxy_health_check:
                proxy_manager.start_health_monitoring()
            
            proxy_config = await proxy_manager.get_proxy_for_session(task_session_id, proxy_country)
            
            if proxy_config:
                logger.info(f"Using proxy: {proxy_config.host}:{proxy_config.port} ({proxy_config.provider.value})")
            else:
                logger.warning("Failed to get proxy from pool. Running without proxy.")
                use_proxy = False
    
    async with async_playwright() as p:
        
        if use_browserbase:
            try:
                browserbase_proxy = None
                use_builtin_proxy = True
                
                if use_proxy and proxy_config:
                    browserbase_proxy = proxy_config.to_browserbase_dict()
                    use_builtin_proxy = False
                    logger.info("Using custom external proxy with Browserbase")
                elif use_proxy:
                    use_builtin_proxy = True
                    logger.info("Using Browserbase built-in residential proxies (requires Developer plan)")
                else:
                    use_builtin_proxy = False
                    logger.info("Browserbase without proxies")
                
                browser, context, session_id = await create_browserbase_browser(
                    api_key=browserbase_api_key,
                    project_id=browserbase_project_id,
                    viewport_width=viewport_width,
                    viewport_height=viewport_height,
                    proxy=browserbase_proxy,
                    session_timeout=browserbase_timeout,
                    playwright_instance=p,
                    enable_proxy=use_proxy,
                    enable_stealth=True,
                    use_builtin_proxy=use_builtin_proxy
                )
                logger.info(f"Using Browserbase session: {session_id}")
                
                pages = context.pages
                if pages:
                    page = pages[0]
                    logger.info("Using existing Browserbase page")
                else:
                    page = await context.new_page()
                    logger.info("Created new page in Browserbase context")
                
            except Exception as e:
                logger.error(f"Failed to create Browserbase session: {e}")
                logger.info("Falling back to local browser")
                use_browserbase = False
                
                if proxy_manager and proxy_config:
                    await proxy_manager.mark_session_failure(task_session_id, f"Browserbase creation failed: {str(e)}")
        
        if not use_browserbase:
            launch_args = STEALTH_ARGS.copy()
            launch_args.extend([
                '--disable-geolocation',
                '--disable-sensors',
                '--deny-permission-prompts',
            ])
            
            if use_proxy and proxy_config:
                proxy_url = proxy_config.to_url()
                launch_args.append(f'--proxy-server={proxy_url}')
                logger.info(f"Configuring local browser with proxy: {proxy_url}")
            
            browser = await p.chromium.launch(
                headless=headless,
                args=launch_args,
                chromium_sandbox=False
            )
            
            context_options = {
                'viewport': {'width': viewport_width, 'height': viewport_height},
                'user_agent': get_random_user_agent(),
                'locale': 'en-US',
                'timezone_id': 'America/New_York',
                'permissions': [],
                'color_scheme': 'light',
                'accept_downloads': True,
                'has_touch': False,
                'is_mobile': False,
                'device_scale_factor': 1,
                'extra_http_headers': {
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-User': '?1',
                    'Sec-Fetch-Dest': 'document',
                    'Upgrade-Insecure-Requests': '1',
                }
            }
            
            if use_proxy and proxy_config and proxy_config.username and proxy_config.password:
                context_options['proxy'] = proxy_config.to_playwright_dict()
            
            context = await browser.new_context(**context_options)
            
            page = await context.new_page()
            
            await configure_stealth_browser(page)
        
        try:
            llm = GeminiLLM(model_name=model)
            
            agent = WebAutomationAgent(
                page=page,
                llm=llm,
                max_steps=max_steps
            )
            
            try:
                history = await agent.run(task, start_url)
                
                if proxy_manager and use_proxy:
                    await proxy_manager.mark_session_success(task_session_id, 5.0)
                    
            except (TargetClosedError, PlaywrightError) as e:
                if "closed" in str(e).lower():
                    logger.error(f"Browser session closed prematurely: {e}")
                    if use_browserbase:
                        logger.error("Browserbase session timed out or was closed remotely")
                    
                    if proxy_manager and use_proxy:
                        await proxy_manager.mark_session_failure(task_session_id, f"Session closed: {str(e)}")
                    
                    history = agent.history
                else:
                    raise
            
            result = history.model_dump()
            
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            logger.info(f"\n{'='*80}")
            logger.info(f"TASK COMPLETED")
            logger.info(f"{'='*80}")
            logger.info(f"Success: {result['success']}")
            logger.info(f"Total steps: {result['total_steps']}")
            logger.info(f"Final answer: {result['final']['final_answer'][:100]}...")
            logger.info(f"Output saved to: {output_path}")
            logger.info(f"{'='*80}")
            
            return result
            
        finally:
            try:
                if not use_browserbase:
                    await context.close()
                    await browser.close()
                else:
                    try:
                        await browser.close()
                    except:
                        pass
                    if session_id:
                        await cleanup_browserbase_session(session_id, browserbase_api_key)
                
                if proxy_manager:
                    proxy_manager.release_session(task_session_id)
                    
                    if enable_proxy_health_check:
                        proxy_manager.stop_health_monitoring()
                    
                    stats = proxy_manager.get_stats()
                    logger.info(f"Proxy stats: {stats['healthy_proxies']}/{stats['total_proxies']} healthy")
                    
            except Exception as e:
                logger.warning(f"Cleanup error: {e}")


def main():
    
    parser = argparse.ArgumentParser(
        description='Gemini Web Automation Agent - WebBench/WebVoyager Compatible',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py "Find the price of iPhone 15 Pro" --start-url "https://www.apple.com"
  python main.py "Search for latest AI news" --start-url "https://www.google.com" --max-steps 20 --headless
  python main.py "Book a flight from NYC to SF" --start-url "https://www.google.com/flights" --use-browserbase
        """
    )
    
    parser.add_argument(
        'task',
        type=str,
        help='Task description'
    )
    
    parser.add_argument(
        '--start-url',
        type=str,
        required=True,
        help='Starting URL'
    )
    
    parser.add_argument(
        '--max-steps',
        type=int,
        default=30,
        help='Maximum steps (default: 30)'
    )
    
    parser.add_argument(
        '--headless',
        action='store_true',
        help='Run in headless mode'
    )
    
    parser.add_argument(
        '--output',
        type=str,
        default='final_output.json',
        help='Output file (default: final_output.json)'
    )
    
    parser.add_argument(
        '--model',
        type=str,
        default='gemini-2.0-flash-exp',
        help='Gemini model (default: gemini-2.0-flash-exp)'
    )
    
    parser.add_argument(
        '--viewport-width',
        type=int,
        default=1280,
        help='Browser width (default: 1280)'
    )
    
    parser.add_argument(
        '--viewport-height',
        type=int,
        default=720,
        help='Browser height (default: 720)'
    )
    
    parser.add_argument(
        '--use-browserbase',
        action='store_true',
        help='Use Browserbase for CAPTCHA avoidance'
    )
    
    parser.add_argument(
        '--browserbase-api-key',
        type=str,
        default=None,
        help='Browserbase API key (overrides BROWSERBASE_API_KEY env var)'
    )
    
    parser.add_argument(
        '--browserbase-project-id',
        type=str,
        default=None,
        help='Browserbase project ID (overrides BROWSERBASE_PROJECT_ID env var)'
    )
    
    parser.add_argument(
        '--browserbase-timeout',
        type=int,
        default=600,
        help='Browserbase session timeout in seconds (default: 600 = 10 min, max: 21600 = 6 hours)'
    )
    
    parser.add_argument(
        '--use-proxy',
        action='store_true',
        default=False,
        help='Enable proxy rotation for CAPTCHA avoidance'
    )
    
    parser.add_argument(
        '--proxy-country',
        type=str,
        default=None,
        help='Preferred proxy country code (e.g., US, GB, DE)'
    )
    
    parser.add_argument(
        '--enable-proxy-health-check',
        action='store_true',
        default=False,
        help='Enable background proxy health monitoring'
    )
    
    args = parser.parse_args()
    
    if not os.getenv('GOOGLE_API_KEY'):
        logger.error("GOOGLE_API_KEY not found in environment!")
        logger.error("Please set it in .env file or export it:")
        logger.error("  export GOOGLE_API_KEY='your-api-key'")
        return
    
    if args.use_browserbase:
        bb_key = args.browserbase_api_key or os.getenv('BROWSERBASE_API_KEY')
        if not bb_key:
            logger.error("BROWSERBASE_API_KEY not found!")
            logger.error("Please set it in .env file or use --browserbase-api-key")
            return
    
    try:
        result = asyncio.run(run_agent(
            task=args.task,
            start_url=args.start_url,
            max_steps=args.max_steps,
            headless=args.headless,
            output_file=args.output,
            model=args.model,
            viewport_width=args.viewport_width,
            viewport_height=args.viewport_height,
            use_browserbase=args.use_browserbase,
            browserbase_api_key=args.browserbase_api_key,
            browserbase_project_id=args.browserbase_project_id,
            browserbase_timeout=args.browserbase_timeout,
            use_proxy=args.use_proxy,
            proxy_country=args.proxy_country,
            enable_proxy_health_check=args.enable_proxy_health_check
        ))
        
        print("\n" + "="*80)
        print("FINAL RESULT")
        print("="*80)
        print(f"Success: {result['success']}")
        print(f"Final Answer: {result['final']['final_answer']}")
        print(f"Total Steps: {result['total_steps']}")
        print("="*80)
        
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)


if __name__ == "__main__":
    main()