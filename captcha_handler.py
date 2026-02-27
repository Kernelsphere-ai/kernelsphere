import asyncio
import logging
from typing import Optional, List, Tuple
from playwright.async_api import Page

logger = logging.getLogger(__name__)


class CaptchaHandler:
    """
    Handles detection and solving of various CAPTCHA types
    
    """
    
    def __init__(self, api_key: Optional[str] = None, manual_mode: bool = True):
        """
        Initialize CAPTCHA handler
        
        Args:
            api_key: 2Captcha API key (optional)
            manual_mode: Whether to pause for manual solving
        """
        self.api_key = api_key
        self.manual_mode = manual_mode
        self.solver_2captcha = None
        self.cloudflare_completed = False  # Track if Cloudflare was already solved
        
        if api_key:
            try:
                from twocaptcha import TwoCaptcha
                self.solver_2captcha = TwoCaptcha(api_key)
                logger.info("2Captcha API solver initialized")
            except ImportError:
                logger.warning(" 2captcha-python not installed. Install with: pip install 2captcha-python")
    
    def mark_cloudflare_completed(self):
        """Mark that Cloudflare challenge was already solved by stealth.py"""
        self.cloudflare_completed = True
        logger.info(" Cloudflare challenge marked as completed - will skip redundant checks")
    
    async def detect_captcha_type(self, page: Page, skip_if_cloudflare_done: bool = True) -> List[str]:
        """
        Detect what types of CAPTCHAs are present on the page
        
        Args:
            page: Playwright page object
            skip_if_cloudflare_done: Skip detection if Cloudflare was already solved
            
        Returns:
            List of detected CAPTCHA types (empty if false positive)
        """
        captcha_types = []
        
        try:
            # Skip if Cloudflare was already completed
            if skip_if_cloudflare_done and self.cloudflare_completed:
                logger.info("Skipping CAPTCHA check - Cloudflare challenge already completed")
                return []
            
            # Get page content
            try:
                body_text = (await page.text_content("body") or "").lower()
                page_title = (await page.title() or "").lower()
                full_text = body_text + " " + page_title
            except:
                full_text = ""
                body_text = ""
            
            logger.debug(f" Scanning page for CAPTCHAs: {page.url}")

            iframes = await page.query_selector_all("iframe")
            has_captcha_iframe = False
            
            for i, iframe in enumerate(iframes):
                try:
                    src = await iframe.get_attribute("src") or ""
                    title = await iframe.get_attribute("title") or ""
                    name = await iframe.get_attribute("name") or ""
                    
                    # Check if iframe is visible
                    is_visible = await iframe.is_visible()
                    
                    iframe_info = (src + " " + title + " " + name).lower()
                    
                    if is_visible and "recaptcha" in iframe_info:
                        captcha_types.append("recaptcha_v2")
                        logger.info(f"Detected reCAPTCHA v2 (visible iframe #{i})")
                        has_captcha_iframe = True
                        break
                    elif is_visible and "hcaptcha" in iframe_info:
                        captcha_types.append("hcaptcha")
                        logger.info(f"Detected hCaptcha (visible iframe #{i})")
                        has_captcha_iframe = True
                        break
                    elif is_visible and ("turnstile" in iframe_info or "cloudflare" in iframe_info):
                        captcha_types.append("cloudflare_turnstile")
                        logger.info(f"Detected Cloudflare Turnstile (visible iframe #{i})")
                        has_captcha_iframe = True
                        break
                except Exception as e:
                    logger.debug(f"Error checking iframe {i}: {e}")
                    continue
            
            # Check for visible CAPTCHA elements (not hidden)
            if not has_captcha_iframe and "recaptcha_v2" not in captcha_types:
                recaptcha_selectors = [
                    ".g-recaptcha",
                    "[class*='g-recaptcha']",
                    "[id*='recaptcha']"
                ]
                
                for selector in recaptcha_selectors:
                    element = await page.query_selector(selector)
                    if element:
                        is_visible = await element.is_visible()
                        if is_visible:
                            captcha_types.append("recaptcha_v2")
                            logger.info(f"Detected reCAPTCHA v2 (visible element: {selector})")
                            break
            
            # Check for hCaptcha elements
            if "hcaptcha" not in captcha_types:
                hcaptcha_selectors = [".h-captcha", "[class*='h-captcha']"]
                for selector in hcaptcha_selectors:
                    element = await page.query_selector(selector)
                    if element:
                        is_visible = await element.is_visible()
                        if is_visible:
                            captcha_types.append("hcaptcha")
                            logger.info(f"Detected hCaptcha (visible element)")
                            break
            
            # Check for Cloudflare challenge page
            cloudflare_specific_texts = [
                "just a moment",
                "checking your browser before accessing"
            ]
            
            has_cloudflare_text = any(text in full_text for text in cloudflare_specific_texts)
            
            # Only report Cloudflare if BOTH text AND visual indicators present
            if has_cloudflare_text:
                # Check for Cloudflare-specific elements
                cf_elements = await page.query_selector_all(
                    "[class*='cloudflare'], [id*='challenge'], [class*='challenge']"
                )
                
                visible_cf_elements = []
                for el in cf_elements:
                    if await el.is_visible():
                        visible_cf_elements.append(el)
                
                if visible_cf_elements:
                    captcha_types.append("cloudflare_challenge")
                    logger.info("Detected Cloudflare challenge page (visible elements + text)")
            
            # Check for image CAPTCHAs
            if not captcha_types:
                captcha_inputs = await page.query_selector_all(
                    "input[name*='captcha' i], input[id*='captcha' i]"
                )
                captcha_images = await page.query_selector_all(
                    "img[src*='captcha' i], img[id*='captcha' i]"
                )
                
                visible_inputs = []
                visible_images = []
                
                for inp in captcha_inputs:
                    if await inp.is_visible():
                        visible_inputs.append(inp)
                
                for img in captcha_images:
                    if await img.is_visible():
                        visible_images.append(img)
                
                if visible_inputs or visible_images:
                    captcha_types.append("image_captcha")
                    logger.info(f"Detected image CAPTCHA (visible elements)")
            

            if not captcha_types:
                # Check for keywords but REQUIRE visible challenge elements
                captcha_keywords = ["verify you are human", "are you a robot", "security check"]
                
                has_specific_keywords = any(keyword in full_text for keyword in captcha_keywords)
                
                if has_specific_keywords:
                    # Look for any visible challenge container
                    challenge_containers = await page.query_selector_all(
                        "[class*='challenge'], [id*='challenge'], [class*='verify'], [id*='verify']"
                    )
                    
                    for container in challenge_containers:
                        if await container.is_visible():
                            captcha_types.append("unknown_captcha")
                            logger.warning(" Detected unknown CAPTCHA type (visible challenge + keywords)")
                            break
            
            # Final result
            if captcha_types:
                logger.warning(f" CAPTCHA DETECTION: {', '.join(captcha_types)}")
            else:
                logger.info("No active CAPTCHAs detected")
            
        except Exception as e:
            logger.error(f" Error detecting CAPTCHAs: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        return captcha_types
    
    async def solve_captcha(
        self,
        page: Page,
        captcha_type: str,
        max_wait: int = 120
    ) -> Tuple[bool, str]:
        """
        Solve detected CAPTCHA
        
        Args:
            page: Playwright page object
            captcha_type: Type of CAPTCHA to solve
            max_wait: Maximum seconds to wait for solving
            
        Returns:
            Tuple of (success, message)
        """
        
        logger.info(f" Attempting to solve: {captcha_type}")
        
        if captcha_type == "recaptcha_v2":
            return await self._solve_recaptcha_v2(page, max_wait)
        
        elif captcha_type == "cloudflare_challenge":
            return await self._solve_cloudflare(page, max_wait)
        
        elif captcha_type == "image_captcha":
            return await self._solve_image_captcha(page, max_wait)
        
        elif captcha_type == "unknown_captcha":
            logger.warning(" Unknown CAPTCHA type - will try all available methods")
            
            if self.manual_mode:
                logger.info("Attempting manual solve for unknown CAPTCHA...")
                return await self._manual_solve(page, "Unknown CAPTCHA", max_wait)
            else:
                logger.warning("Manual mode disabled - waiting to see if CAPTCHA auto-resolves...")
                await asyncio.sleep(10)
                
                # Check if CAPTCHA is still present
                remaining_captchas = await self.detect_captcha_type(page, skip_if_cloudflare_done=False)
                if not remaining_captchas:
                    logger.info("CAPTCHA appears to have auto-resolved")
                    return True, "Auto-resolved"
                else:
                    return False, "Unknown CAPTCHA type - manual intervention required"
        
        elif captcha_type in ["hcaptcha", "cloudflare_turnstile", "recaptcha_v3"]:

            if self.manual_mode:
                return await self._manual_solve(page, captcha_type, max_wait)
            else:
                return False, f"{captcha_type} requires manual solving or specialized API"
        
        else:
            return False, f"Unsupported CAPTCHA type: {captcha_type}"
    
    async def _solve_recaptcha_v2(self, page: Page, max_wait: int) -> Tuple[bool, str]:
        """Solve reCAPTCHA v2"""
        
        # Try API solving if available
        if self.solver_2captcha:
            try:
                logger.info("=" * 70)
                logger.info(" Attempting to solve reCAPTCHA v2 with 2Captcha API...")
                logger.info("=" * 70)
                
                # Extract sitekey
                sitekey = None
                
                # Try to get from data attribute
                recaptcha_element = await page.query_selector(".g-recaptcha")
                if recaptcha_element:
                    sitekey = await recaptcha_element.get_attribute("data-sitekey")
                    logger.info(f" Found sitekey from .g-recaptcha: {sitekey}")
                
                # Try to get from iframe src
                if not sitekey:
                    logger.info("Checking iframes for sitekey...")
                    iframes = await page.query_selector_all("iframe")
                    
                    for i, iframe in enumerate(iframes):
                        try:
                            src = await iframe.get_attribute("src") or ""
                            
                            if "recaptcha" in src and "k=" in src:
                                sitekey = src.split("k=")[-1].split("&")[0]
                                logger.info(f"Found sitekey from iframe {i}: {sitekey}")
                                break
                        except Exception as e:
                            logger.debug(f"Error checking iframe {i}: {e}")
                            continue
                
                if not sitekey:
                    logger.error(" Could not extract reCAPTCHA sitekey")
                    raise Exception("Sitekey extraction failed")
                
                logger.info(f" Submitting to 2Captcha API...")
                logger.info(f"   Sitekey: {sitekey}")
                logger.info(f"   URL: {page.url}")
                
                # Solve with API
                result = self.solver_2captcha.recaptcha(
                    sitekey=sitekey,
                    url=page.url
                )
                
                token = result['code']
                logger.info(f"Received solution from 2Captcha ({len(token)} chars)")
                logger.info(" Injecting token into page...")
                
                # Inject token into page
                inject_result = await page.evaluate(f'''
                    () => {{
                        try {{
                            const textarea = document.getElementById("g-recaptcha-response");
                            if (!textarea) {{
                                return "ERROR: g-recaptcha-response element not found";
                            }}
                            
                            textarea.innerHTML = "{token}";
                            textarea.value = "{token}";
                            textarea.style.display = "block";
                            
                            // Trigger callback
                            if (typeof ___grecaptcha_cfg !== 'undefined') {{
                                const clients = ___grecaptcha_cfg.clients;
                                if (clients) {{
                                    Object.keys(clients).forEach(key => {{
                                        const client = clients[key];
                                        if (client && client.callback) {{
                                            try {{
                                                client.callback("{token}");
                                            }} catch(e) {{
                                                console.log("Callback error:", e);
                                            }}
                                        }}
                                    }});
                                }}
                            }}
                            
                            return "SUCCESS";
                        }} catch(e) {{
                            return "ERROR: " + e.message;
                        }}
                    }}
                ''')
                
                logger.info(f"Injection result: {inject_result}")
                
                if "SUCCESS" in inject_result:
                    logger.info("=" * 70)
                    logger.info(" reCAPTCHA solved successfully with 2Captcha API!")
                    logger.info("=" * 70)
                    await asyncio.sleep(3)
                    return True, "Solved with 2Captcha API"
                else:
                    logger.error(f"Token injection failed: {inject_result}")
                    raise Exception("Token injection failed")
                
            except Exception as e:
                logger.error("=" * 70)
                logger.error(f" 2Captcha API failed: {e}")
                logger.error("=" * 70)
                import traceback
                logger.error(traceback.format_exc())
                # Fall through to manual mode
        
        # Manual solving
        if self.manual_mode:
            logger.info("Falling back to manual CAPTCHA solving...")
            return await self._manual_solve(page, "reCAPTCHA v2", max_wait)
        
        logger.error("No solving method available for reCAPTCHA v2")
        return False, "No solving method available"
    
    async def _solve_cloudflare(self, page: Page, max_wait: int) -> Tuple[bool, str]:
        """Wait for Cloudflare challenge to complete"""
        
        logger.info(" Waiting for Cloudflare challenge to resolve...")
        
        start_url = page.url
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < max_wait:
            await asyncio.sleep(2)
            
            try:
                current_url = page.url
                body_text = await page.text_content("body")
                
                # Check if challenge is gone
                if "Just a moment" not in body_text and \
                   "Checking your browser" not in body_text:
                    
                    if current_url != start_url or \
                       not await page.query_selector("iframe[src*='challenges.cloudflare.com']"):
                        logger.info("Cloudflare challenge resolved!")
                        self.cloudflare_completed = True
                        await asyncio.sleep(2)
                        return True, "Cloudflare challenge passed"
                
                # Log progress
                elapsed = int(asyncio.get_event_loop().time() - start_time)
                if elapsed % 10 == 0 and elapsed > 0:
                    logger.info(f"Still waiting... ({max_wait - elapsed}s remaining)")
                    
            except Exception as e:
                logger.error(f"Error checking Cloudflare status: {e}")
                break
        
        return False, "Cloudflare challenge timeout"
    
    async def _solve_image_captcha(self, page: Page, max_wait: int) -> Tuple[bool, str]:
        """Solve generic image CAPTCHA"""
        
        if self.solver_2captcha:
            try:
                logger.info(" Attempting to solve image CAPTCHA with 2Captcha API...")
                
                # Find CAPTCHA image
                captcha_img = await page.query_selector("img[src*='captcha' i], img[id*='captcha' i]")
                if not captcha_img:
                    return False, "Could not find CAPTCHA image"
                
                # Get image source
                img_src = await captcha_img.get_attribute("src")
                
                # Solve with API
                result = self.solver_2captcha.normal(img_src)
                captcha_text = result['code']
                
                logger.info(f"Received solution: {captcha_text}")
                
                # Find input field
                captcha_input = await page.query_selector(
                    "input[name*='captcha' i], input[id*='captcha' i]"
                )
                
                if captcha_input:
                    await captcha_input.fill(captcha_text)
                    logger.info("CAPTCHA solution entered")
                    return True, f"Solved image CAPTCHA: {captcha_text}"
                else:
                    return False, "Could not find CAPTCHA input field"
                    
            except Exception as e:
                logger.error(f"Image CAPTCHA solving failed: {e}")
        
        # Manual fallback
        if self.manual_mode:
            return await self._manual_solve(page, "Image CAPTCHA", max_wait)
        
        return False, "No solving method available"
    
    async def _manual_solve(
        self,
        page: Page,
        captcha_type: str,
        max_wait: int
    ) -> Tuple[bool, str]:
        """
        Pause and wait for user to manually solve CAPTCHA
        """
        
        logger.warning("=" * 70)
        logger.warning(f"MANUAL CAPTCHA SOLVING REQUIRED: {captcha_type}")
        logger.warning("=" * 70)
        logger.warning("Please solve the CAPTCHA in the browser window.")
        logger.warning(f" The agent will wait up to {max_wait} seconds...")
        logger.warning("=" * 70)
        
        start_time = asyncio.get_event_loop().time()
        initial_url = page.url
        
        while asyncio.get_event_loop().time() - start_time < max_wait:
            await asyncio.sleep(3)
            
            try:
                current_url = page.url
                
                # Check if CAPTCHA is gone
                captcha_still_present = await self._check_captcha_presence(page, captcha_type)
                
                if not captcha_still_present or current_url != initial_url:
                    logger.info("=" * 70)
                    logger.info("CAPTCHA SOLVED! Continuing...")
                    logger.info("=" * 70)
                    await asyncio.sleep(3)
                    return True, "Manual CAPTCHA solve successful"
                
                # Progress update
                elapsed = int(asyncio.get_event_loop().time() - start_time)
                if elapsed % 15 == 0 and elapsed > 0:
                    remaining = max_wait - elapsed
                    logger.info(f" Waiting for CAPTCHA solve... ({remaining}s remaining)")
                    
            except Exception as e:
                logger.error(f"Error during manual solving: {e}")
                break
        
        logger.warning("=" * 70)
        logger.warning("  CAPTCHA solve timeout")
        logger.warning("=" * 70)
        return False, "Manual solve timeout"
    
    async def _check_captcha_presence(self, page: Page, captcha_type: str) -> bool:
        """Check if specific CAPTCHA type is still present"""
        
        try:
            if "recaptcha" in captcha_type.lower():
                return bool(await page.query_selector(".g-recaptcha:visible, iframe[src*='recaptcha']:visible"))
            
            elif "cloudflare" in captcha_type.lower():
                body = await page.text_content("body")
                return "Just a moment" in body or "Checking your browser" in body
            
            elif "hcaptcha" in captcha_type.lower():
                return bool(await page.query_selector(".h-captcha:visible"))
            
            elif "image" in captcha_type.lower():
                return bool(await page.query_selector("img[src*='captcha' i]:visible"))
            
            else:
                # Generic check
                captcha_types = await self.detect_captcha_type(page, skip_if_cloudflare_done=False)
                return len(captcha_types) > 0
                
        except:
            return False
    
    async def handle_captchas(self, page: Page, max_wait: int = 120) -> Tuple[bool, List[str]]:
        """
        Detect and solve all CAPTCHAs on current page
        
        Args:
            page: Playwright page
            max_wait: Maximum wait time per CAPTCHA
            
        Returns:
            Tuple of (all_solved, messages)
        """
        
        # Detect CAPTCHAs
        captcha_types = await self.detect_captcha_type(page)
        
        if not captcha_types:
            return True, ["No CAPTCHAs detected"]
        
        logger.info(f" Detected {len(captcha_types)} CAPTCHA type(s): {', '.join(captcha_types)}")
        
        # Solve each type
        messages = []
        all_solved = True
        
        for captcha_type in captcha_types:
            success, message = await self.solve_captcha(page, captcha_type, max_wait)
            messages.append(f"{captcha_type}: {message}")
            
            if not success:
                all_solved = False
                logger.error(f" Failed to solve {captcha_type}: {message}")
            else:
                logger.info(f"Successfully solved {captcha_type}")
        
        return all_solved, messages


# Convenience function
async def detect_and_handle_captcha(
    page: Page,
    api_key: Optional[str] = None,
    manual_mode: bool = True,
    max_wait: int = 120
) -> Tuple[bool, List[str]]:
    """Quick function to detect and handle CAPTCHAs"""
    handler = CaptchaHandler(api_key=api_key, manual_mode=manual_mode)
    return await handler.handle_captchas(page, max_wait)