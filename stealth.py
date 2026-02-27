import random
import asyncio
import logging
from typing import List, Optional, Tuple
from playwright.async_api import Page, Browser

logger = logging.getLogger(__name__)



STEALTH_ARGS = [
    '--disable-blink-features=AutomationControlled',
    '--disable-dev-shm-usage',
    '--disable-web-security',
    '--disable-features=IsolateOrigins,site-per-process',
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-accelerated-2d-canvas',
    '--disable-gpu',
    '--window-size=1280,720',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
]

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
]


COOKIE_CONSENT_SELECTORS = [
    # Common cookie accept buttons
    'button:has-text("Accept")',
    'button:has-text("Accept All")',
    'button:has-text("Accept all")',
    'button:has-text("I Agree")',
    'button:has-text("I agree")',
    'button:has-text("Agree")',
    'button:has-text("Allow All")',
    'button:has-text("Allow all")',
    'button:has-text("OK")',
    'button:has-text("Got it")',
    'button:has-text("Continue")',
    
    # Common cookie banner IDs/classes
    '#onetrust-accept-btn-handler',
    '#accept-cookies',
    '#cookie-accept',
    '.cookie-accept',
    '.accept-cookies',
    '[id*="cookie"][id*="accept"]',
    '[class*="cookie"][class*="accept"]',
    '[aria-label*="Accept"]',
    '[aria-label*="accept"]',
    
    # GDPR-specific
    'button:has-text("Accept cookies")',
    'button:has-text("Accept Cookies")',
    '.gdpr-accept',
    '#gdpr-accept',
]

POPUP_CLOSE_SELECTORS = [
    # Common close buttons
    'button[aria-label="Close"]',
    'button[aria-label="close"]',
    'button[title="Close"]',
    'button[title="close"]',
    '[class*="close"]',
    '[class*="Close"]',
    '.modal-close',
    '.popup-close',
    'button:has-text("No")',
    'button:has-text("No")',
    '[aria-label*="dismiss"]',
    '[aria-label*="Dismiss"]',
]



async def configure_stealth_browser(page: Page) -> None:
    """
    Configure page with stealth settings to avoid bot detection
    
    Args:
        page: Playwright page object
    """
    try:
        # Remove webdriver property
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        # Override permissions
        await page.add_init_script("""
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
        
        # Add realistic plugins
        await page.add_init_script("""
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    {
                        0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},
                        description: "Portable Document Format",
                        filename: "internal-pdf-viewer",
                        length: 1,
                        name: "Chrome PDF Plugin"
                    },
                    {
                        0: {type: "application/x-nacl", suffixes: "", description: "Native Client Executable"},
                        description: "Native Client Executable",
                        filename: "internal-nacl-plugin",
                        length: 2,
                        name: "Native Client"
                    }
                ]
            });
        """)
        
        # Override chrome property
        await page.add_init_script("""
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
        """)
        
        # Randomize canvas fingerprint
        await page.add_init_script("""
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) {
                    return 'Intel Inc.';
                }
                if (parameter === 37446) {
                    return 'Intel Iris OpenGL Engine';
                }
                return getParameter.apply(this, [parameter]);
            };
        """)
        
        # Add more realistic navigator properties
        await page.add_init_script("""
            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            
            // Override platform
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32'
            });
            
            // Add hardwareConcurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8
            });
            
            // Add deviceMemory
            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8
            });
        """)
        
        # Override battery API to make it look like a real device
        await page.add_init_script("""
            if (navigator.getBattery) {
                const originalGetBattery = navigator.getBattery;
                navigator.getBattery = async () => {
                    const battery = await originalGetBattery();
                    Object.defineProperty(battery, 'charging', { value: true });
                    Object.defineProperty(battery, 'chargingTime', { value: 0 });
                    Object.defineProperty(battery, 'dischargingTime', { value: Infinity });
                    Object.defineProperty(battery, 'level', { value: 1.0 });
                    return battery;
                };
            }
        """)
        
        # Remove automation-related properties
        await page.add_init_script("""
            delete navigator.__proto__.webdriver;
            
            // Override toString to hide proxy
            const originalToString = Function.prototype.toString;
            Function.prototype.toString = function() {
                if (this === navigator.permissions.query) {
                    return 'function query() { [native code] }';
                }
                return originalToString.call(this);
            };
        """)
        
        # Add connection info
        await page.add_init_script("""
            Object.defineProperty(navigator, 'connection', {
                get: () => ({
                    effectiveType: '4g',
                    rtt: 100,
                    downlink: 10,
                    saveData: false
                })
            });
        """)
        
        logger.info("Enhanced stealth configuration applied")
        
    except Exception as e:
        logger.warning(f"Failed to apply stealth config: {e}")



async def detect_challenges(page: Page) -> Tuple[bool, bool, bool]:
    """
    Detect various bot challenges on the page
    
    Args:
        page: Playwright page object
        
    Returns:
        Tuple of (has_cloudflare, has_captcha, has_cookie_popup)
    """
    try:
        content = await page.content()
        text = await page.text_content('body') or ""
        current_url = page.url
        
        # Detect Cloudflare challenge
        has_cloudflare = any([
            "Checking your browser" in text,
            "Just a moment" in text,
            "DDoS protection by Cloudflare" in text,
            "cloudflare" in content.lower() and "challenge" in content.lower(),
            "cf-browser-verification" in content,
        ])
        
        # Detect CAPTCHAs and Google bot detection
        has_captcha = any([
            "recaptcha" in content.lower(),
            "hcaptcha" in content.lower(),
            "captcha" in content.lower(),
            "/sorry/index" in current_url, 
            "unusual traffic" in text.lower(),
            await page.query_selector('iframe[src*="recaptcha"]') is not None,
            await page.query_selector('iframe[src*="hcaptcha"]') is not None,
        ])
        
        has_cookie_popup = False
        for selector in COOKIE_CONSENT_SELECTORS[:5]: 
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    has_cookie_popup = True
                    break
            except:
                continue
        
        if has_cloudflare:
            logger.warning(" Cloudflare challenge detected")
        if has_captcha:
            if "/sorry/index" in current_url:
                logger.warning("Google bot detection page detected")
            else:
                logger.warning(" CAPTCHA detected")
        if has_cookie_popup:
            logger.info(" Cookie consent popup detected")
        
        return has_cloudflare, has_captcha, has_cookie_popup
        
    except Exception as e:
        logger.error(f"Error detecting challenges: {e}")
        return False, False, False



async def close_cookie_popup(page: Page, max_attempts: int = 3) -> bool:
    """
    Attempt to close cookie consent popup
    
    Args:
        page: Playwright page object
        max_attempts: Maximum number of selectors to try
        
    Returns:
        True if popup was closed, False otherwise
    """
    try:
        logger.info("Attempting to close cookie popup...")
        
        for selector in COOKIE_CONSENT_SELECTORS[:max_attempts * 3]:
            try:
                element = await page.query_selector(selector)
                if element:
                    is_visible = await element.is_visible()
                    if is_visible:
                        await element.click(timeout=2000)
                        await asyncio.sleep(0.5)
                        logger.info(f" Closed cookie popup with selector: {selector}")
                        return True
            except Exception as e:
                continue
        
        logger.warning("Could not find/close cookie popup")
        return False
        
    except Exception as e:
        logger.error(f"Error closing cookie popup: {e}")
        return False


async def close_generic_popup(page: Page) -> bool:
    """
    Attempt to close generic popup/modal
    
    Args:
        page: Playwright page object
        
    Returns:
        True if popup was closed, False otherwise
    """
    try:
        logger.info("Attempting to close generic popup...")
        
        # Try Escape key first
        await page.keyboard.press('Escape')
        await asyncio.sleep(0.3)
        
        # Try close button selectors
        for selector in POPUP_CLOSE_SELECTORS[:10]:
            try:
                element = await page.query_selector(selector)
                if element:
                    is_visible = await element.is_visible()
                    if is_visible:
                        await element.click(timeout=2000)
                        await asyncio.sleep(0.5)
                        logger.info(f"Closed popup with selector: {selector}")
                        return True
            except:
                continue
        
        logger.warning("Could not close generic popup")
        return False
        
    except Exception as e:
        logger.error(f"Error closing popup: {e}")
        return False


async def wait_for_cloudflare(page: Page, max_wait: int = 30) -> bool:
    """
    Wait for Cloudflare challenge to complete
    
    Args:
        page: Playwright page object
        max_wait: Maximum seconds to wait
        
    Returns:
        True if challenge passed, False if timed out
    """
    try:
        logger.warning("Waiting for Cloudflare challenge (manual verification may be required)...")
        
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < max_wait:
            content = await page.content()
            text = await page.text_content('body') or ""
            
            # Check if challenge is gone
            is_challenge = any([
                "Checking your browser" in text,
                "Just a moment" in text,
                "cf-browser-verification" in content,
            ])
            
            if not is_challenge:
                logger.info(" Cloudflare challenge completed")
                return True
            
            await asyncio.sleep(2)
        
        logger.error("Cloudflare challenge timeout")
        return False
        
    except Exception as e:
        logger.error(f"Error waiting for Cloudflare: {e}")
        return False


async def handle_google_bot_detection(page: Page, max_wait: int = 60) -> bool:
    """
    Handle Google's "unusual traffic" / bot detection page
    
    Args:
        page: Playwright page object
        max_wait: Maximum seconds to wait
        
    Returns:
        True if challenge was passed, False otherwise
    """
    try:
        current_url = page.url
        
        # Check if we're on Google's bot detection page
        if "/sorry/index" not in current_url and "unusual traffic" not in await page.text_content('body'):
            return True  # Not on bot detection page
        
        logger.warning(" Google bot detection triggered!")
        logger.warning("=" * 60)
        logger.warning(" MANUAL CAPTCHA SOLVING REQUIRED")
        logger.warning("=" * 60)
        logger.warning("Please solve the CAPTCHA in the browser window.")
        logger.warning("The agent will wait up to 60 seconds...")
        logger.warning("=" * 60)
        
        # Wait for URL to change (indicating solved CAPTCHA)
        start_time = asyncio.get_event_loop().time()
        check_interval = 2
        
        while asyncio.get_event_loop().time() - start_time < max_wait:
            await asyncio.sleep(check_interval)
            
            current_url = page.url
            
            # Check if we've moved away from the sorry page
            if "/sorry/index" not in current_url:
                logger.info("=" * 60)
                logger.info(" CAPTCHA SOLVED! Continuing...")
                logger.info("=" * 60)
                # Wait a bit more for page to fully load
                await asyncio.sleep(3)
                return True
            
            # Show countdown
            elapsed = int(asyncio.get_event_loop().time() - start_time)
            remaining = max_wait - elapsed
            if elapsed % 10 == 0:  # Update every 10 seconds
                logger.info(f" Waiting for CAPTCHA solve... ({remaining}s remaining)")
        
        # Timeout
        logger.warning("=" * 60)
        logger.warning(" CAPTCHA solve timeout")
        logger.warning("=" * 60)
        return False
        
    except Exception as e:
        logger.error(f"Error handling Google bot detection: {e}")
        return False


async def human_delay(min_ms: int = 300, max_ms: int = 1000) -> None:
    """
    Add random human-like delay
    
    Args:
        min_ms: Minimum delay in milliseconds
        max_ms: Maximum delay in milliseconds
    """
    delay = random.uniform(min_ms / 1000, max_ms / 1000)
    await asyncio.sleep(delay)


async def human_mouse_move(page: Page) -> None:
    """
    Simulate human-like mouse movement
    
    Args:
        page: Playwright page object
    """
    try:
        # Random mouse movement
        x = random.randint(100, 800)
        y = random.randint(100, 500)
        await page.mouse.move(x, y)
        await human_delay(50, 150)
    except:
        pass


def get_random_user_agent() -> str:
    """Get random user agent string"""
    return random.choice(USER_AGENTS)


async def auto_handle_popups(page: Page) -> None:
    """
    Automatically handle popups after page load
    
    Args:
        page: Playwright page object
    """
    try:
        # Wait a bit for popups to appear
        await asyncio.sleep(1.5)
        
        # Detect and handle challenges
        has_cloudflare, has_captcha, has_cookie = await detect_challenges(page)
        
        # Handle Google bot detection first
        if "/sorry/index" in page.url or "unusual traffic" in await page.text_content('body'):
            await handle_google_bot_detection(page)
            return
        
        # Handle cookie popup first
        if has_cookie:
            await close_cookie_popup(page)
            await asyncio.sleep(0.5)
        
        # Handle Cloudflare if present
        if has_cloudflare:
            await wait_for_cloudflare(page)
        
        # Note CAPTCHAs but don't block
        if has_captcha and "/sorry/index" not in page.url:
            logger.warning("  CAPTCHA detected - may require manual intervention")
        
    except Exception as e:
        logger.error(f"Error in auto popup handling: {e}")