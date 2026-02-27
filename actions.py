import asyncio
import random
import logging
import hashlib
import re
import json
from typing import Optional, Any, Dict, Tuple
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from models import ActionResult
from dom_service import DOMService
from stealth import human_delay, close_cookie_popup, close_generic_popup
from constraint_parser import ConstraintParser

logger = logging.getLogger(__name__)


class ActionExecutor:
    
    def __init__(self, page: Page, dom_service: DOMService, llm=None, vision_locator=None, vision_input_handler=None, google_flights_handler=None, google_maps_handler=None):
        self.page = page
        self.dom_service = dom_service
        self.llm = llm
        self.vision_locator = vision_locator
        self.vision_input_handler = vision_input_handler
        self.google_flights_handler = google_flights_handler
        self.google_maps_handler = google_maps_handler
        self.current_task = None
        self.last_extraction_content = None
        self.constraints = []
        self.logger = logging.getLogger(__name__)
    
    def set_task(self, task: str):
        self.current_task = task
    
    def set_constraints(self, constraints):
        """
        Set constraints for filtering
        
        Args:
            constraints: List of parsed constraints from ConstraintParser
        """
        self.constraints = constraints
        if hasattr(self, 'logger') and self.logger:
            self.logger.info(f" Constraints set in ActionExecutor: {len(constraints) if constraints else 0} constraint(s)")
    
    async def execute(self, action: str, **kwargs) -> ActionResult:
        try:
            self.logger.info(f"Executing action: {action}")
            
            state_before = await self._capture_state()
            
            if action == "navigate":
                result = await self._navigate(kwargs.get("url"))
            elif action == "click_element":
                result = await self._click_element(kwargs.get("index"))
            elif action == "input_text":
                result = await self._input_text(kwargs.get("index"), kwargs.get("text"))
            elif action == "input_otp":
                result = await self._input_otp(kwargs.get("index"), kwargs.get("email_handler"))
            elif action == "wait_for_manual_otp":
                result = await self._wait_for_manual_otp(kwargs.get("initial_url"), kwargs.get("timeout", 300))
            elif action == "select_dropdown":
                result = await self._select_dropdown(kwargs.get("index"), kwargs.get("option"))
            elif action == "set_price_range":
                result = await self._set_price_range(
                    min_price=kwargs.get("min_price"),
                    max_price=kwargs.get("max_price")
                )

            elif action == "select_date":
                result = await self._select_date(kwargs.get("date"))

            elif action == "google_flights_origin":
                result = await self._google_flights_origin(kwargs.get("city"))
            elif action == "google_flights_destination":
                result = await self._google_flights_destination(kwargs.get("city"))
            elif action == "google_flights_search":
                result = await self._google_flights_search()
            elif action == "google_flights_class":
                result = await self._google_flights_class(kwargs.get("class_type"))
            elif action == "google_maps_search":
                result = await self._google_maps_search(kwargs.get("query"))
            elif action == "google_maps_directions":
                result = await self._google_maps_directions(kwargs.get("from_location"), kwargs.get("to_location"))
            elif action == "extract":
                goal = kwargs.get("extraction_goal", "") or ""

                url = self.page.url or ""
                if "allrecipes.com/recipe/" in url and any(k in goal.lower() for k in ["recipe", "ingredient", "time", "cook", "prep", "rating"]):
                    try:
                        content = await ExtractAllrecipesRecipeAction().run(self.page)
                        
                        if not content or not isinstance(content, dict):
                            self.logger.warning("Allrecipes extractor returned invalid data, falling back")
                            result = await self._extract(goal, task=self.current_task)
                        else:
                            has_ingredients = content.get("ingredients") and len(content.get("ingredients", [])) > 2
                            has_rating = content.get("rating") is not None
                            has_any_time = content.get("total_time") or content.get("prep_time") or content.get("cook_time")
                            
                            missing_critical = []
                            if not has_rating and ("rating" in goal.lower() or "star" in goal.lower()):
                                missing_critical.append("rating")
                            if not has_any_time and ("time" in goal.lower() or "cook" in goal.lower() or "prep" in goal.lower()):
                                missing_critical.append("time")
                            if not has_ingredients and "ingredient" in goal.lower():
                                missing_critical.append("ingredients")
                            
                            if missing_critical:
                                self.logger.warning(f"Allrecipes extractor missing critical fields {missing_critical}, falling back")
                                result = await self._extract(goal, task=self.current_task)
                            else:
                                import json
                                populated = sum([
                                    1 if content.get("recipe_name") else 0,
                                    1 if has_rating else 0,
                                    1 if content.get("number_of_reviews") else 0,
                                    1 if has_any_time else 0,
                                    1 if has_ingredients else 0,
                                ])
                                self.logger.info(f"Allrecipes extractor populated {populated}/5 fields")
                                result = ActionResult(
                                    action="extract_allrecipes_recipe",
                                    success=True,
                                    extracted_content=json.dumps(content, ensure_ascii=False)
                                )
                    except Exception as e:
                        self.logger.error(f"Allrecipes extractor failed: {e}, falling back to general extraction")
                        result = await self._extract(goal, task=self.current_task)
                elif "google.com/maps" in url:
                    result = await self._extract_google_maps_info()
                else:
                    result = await self._extract(goal, task=self.current_task)

            elif action == "search":
                result = await self._search(kwargs.get("query"))
            elif action == "scroll":
                result = await self._scroll(kwargs.get("direction", "down"), kwargs.get("amount", 500))
            elif action == "go_back":
                result = await self._go_back()
            elif action == "send_keys":
                result = await self._send_keys(kwargs.get("keys"))
            elif action == "wait":
                result = await self._wait(kwargs.get("duration", 2.0))
            elif action == "close_cookie_popup":
                result = await self._close_cookie_popup()
            elif action == "close_popup":
                result = await self._close_popup()
            elif action == "done":
                extracted = kwargs.get("extracted_content", "")
                if not extracted and self.last_extraction_content:
                    try:
                        import json
                        data = json.loads(self.last_extraction_content)
                        
                        if isinstance(data, dict) and "items" in data:
                            items = data.get("items", [])
                            quality = data.get("match_quality", "unknown")
                            
                            if items:
                                formatted_output = []
                                for i, item in enumerate(items[:5], 1):
                                    if isinstance(item, dict):
                                        name = item.get("name") or item.get("title") or item.get("recipe_name") or "Item"
                                        rating = item.get("rating", "")
                                        reviews = item.get("reviews", "")
                                        match_reason = item.get("match_reason", "")
                                        
                                        item_line = f"{i}. {name}"
                                        if rating:
                                            item_line += f" ({rating}"
                                            if reviews:
                                                item_line += f", {reviews} reviews"
                                            item_line += ")"
                                        
                                        if match_reason:
                                            item_line += f"\n   {match_reason}"
                                        
                                        formatted_output.append(item_line)
                                
                                if quality == "perfect":
                                    extracted = f"Found {len(items)} items matching all requirements:\n\n" + "\n\n".join(formatted_output)
                                else:
                                    extracted = f"Found {len(items)} items:\n\n" + "\n\n".join(formatted_output)
                                
                                extracted += f"\n\nFull details:\n{json.dumps(items[:2], indent=2)}"
                            else:
                                extracted = self.last_extraction_content
                        else:
                            extracted = self.last_extraction_content
                    except:
                        extracted = self.last_extraction_content
                    
                    if extracted:
                        self.logger.info("Done action using last extraction result")
                
                return ActionResult(
                    action="done",
                    success=kwargs.get("success", True),
                    extracted_content=extracted
                )
            else:
                return ActionResult(
                    action=action,
                    success=False,
                    error=f"Unknown action: {action}"
                )
            
            state_after = await self._capture_state()
            
            result.url_changed = (state_after["url"] != state_before["url"])
            result.dom_changed = (state_after["dom_hash"] != state_before["dom_hash"])
            result.new_dialog = state_after["has_dialog"] and not state_before["has_dialog"]
            
            if result.url_changed:
                self.logger.info(f"URL changed: {state_before['url']} -> {state_after['url']}")
            if result.dom_changed:
                self.logger.info(f"DOM changed: {state_before['dom_hash']} -> {state_after['dom_hash']}")
            elif not result.url_changed:
                self.logger.warning(" No evidence of change detected")
            
            self.last_extraction_content = result.extracted_content if action == "extract" else self.last_extraction_content
            
            return result
            
        except Exception as e:
            self.logger.error(f"Action execution error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return ActionResult(
                action=action,
                success=False,
                error=str(e)
            )
    
    async def _capture_state(self) -> Dict[str, Any]:
        try:
            url = self.page.url
            
            dom_hash = await self.page.evaluate("""
                () => {
                    const text = document.body.innerText || '';
                    const elemCount = document.querySelectorAll('*').length;
                    const title = document.title || '';
                    return `${title}|${elemCount}|${text.substring(0, 200)}`;
                }
            """)
            
            has_dialog = await self.page.evaluate("""
                () => {
                    const dialogs = document.querySelectorAll('[role="dialog"], .modal, [class*="modal"], [class*="overlay"]');
                    return Array.from(dialogs).some(d => {
                        const style = window.getComputedStyle(d);
                        return style.display !== 'none' && style.visibility !== 'hidden';
                    });
                }
            """)
            
            return {
                "url": url,
                "dom_hash": hashlib.md5(dom_hash.encode()).hexdigest()[:12],
                "has_dialog": has_dialog
            }
        except Exception as e:
            self.logger.debug(f"State capture error: {e}")
            return {"url": "", "dom_hash": "", "has_dialog": False}
    
    async def _navigate(self, url: str) -> ActionResult:
        try:
            if not url:
                return ActionResult(action="navigate", success=False, error="No URL provided")
            
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            
            self.logger.info(f"Navigating to: {url}")
            
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeout:
                self.logger.error(f"Hard navigation failure: page never reached domcontentloaded")
                return ActionResult(
                    action="navigate",
                    success=False,
                    error=f"Page failed to load: {url}"
                )
            
            try:
                await self.page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeout:
                self.logger.info("Network idle timeout (page still usable)")
            
            await human_delay(500, 1000)
            
            final_url = self.page.url
            title = await self.page.title()
            
            return ActionResult(
                action="navigate",
                success=True,
                url=final_url,
                title=title
            )
            
        except Exception as e:
            self.logger.error(f"Navigation error: {e}")
            return ActionResult(action="navigate", success=False, error=str(e))
    
    async def _go_back(self) -> ActionResult:
        try:
            await self.page.go_back(wait_until="domcontentloaded", timeout=15000)
            await human_delay(500, 1000)
            url = self.page.url
            title = await self.page.title()
            return ActionResult(action="go_back", success=True, url=url, title=title)
        except Exception as e:
            return ActionResult(action="go_back", success=False, error=str(e))
    
    async def _search(self, query: str) -> ActionResult:
        try:
            if not query:
                return ActionResult(action="search", success=False, error="No query provided")
            
            search_selectors = [
                'input[type="search"]',
                'input[name*="search" i]',
                'input[placeholder*="search" i]',
                'input[aria-label*="search" i]',
                '[role="searchbox"]'
            ]
            
            for selector in search_selectors:
                try:
                    search_input = await self.page.query_selector(selector)
                    if search_input and await search_input.is_visible():
                        self.logger.info("Using site search")
                        await search_input.fill(query)
                        await search_input.press("Enter")
                        await asyncio.sleep(2)
                        return ActionResult(
                            action="search",
                            success=True,
                            url=self.page.url,
                            title=await self.page.title()
                        )
                except:
                    continue
            
            return ActionResult(
                action="search",
                success=False,
                error="No search input found on current page"
            )
            
        except Exception as e:
            return ActionResult(action="search", success=False, error=str(e))
    
    async def _click_element(self, index: int) -> ActionResult:
        try:
            if index is None:
                return ActionResult(action="click_element", success=False, error="No index provided")
            
            element = await self.dom_service.get_element_by_index(self.page, index)
            
            if not element:
                return ActionResult(
                    action="click_element",
                    success=False,
                    error=f"Element {index} not found"
                )
            
            try:
                is_visible = await element.is_visible()
                if not is_visible:
                    return ActionResult(
                        action="click_element",
                        success=False,
                        error=f"Element {index} is not visible"
                    )
            except:
                pass
            
            try:
                tag = await element.evaluate("el => el.tagName.toLowerCase()")
                text = (await element.text_content() or "")[:50]
                self.logger.info(f"Clicking <{tag}>{text}...")
            except:
                tag = "unknown"
            
            href = None
            try:
                href = await element.evaluate("""
                    el => {
                        if (el.tagName.toLowerCase() === 'a' && el.href) {
                            return el.href;
                        }
                        const link = el.querySelector('a');
                        if (link && link.href) {
                            return link.href;
                        }
                        const parentLink = el.closest('a');
                        if (parentLink && parentLink.href) {
                            return parentLink.href;
                        }
                        const onclick = el.getAttribute('onclick') || '';
                        const match = onclick.match(/location\.href\s*=\s*['"]([^'"]+)['"]/);
                        if (match) {
                            return match[1];
                        }
                        return null;
                    }
                """)
            except:
                pass
            
            is_link = href is not None
            
            await element.scroll_into_view_if_needed()
            await human_delay(200, 500)
            
            url_before = self.page.url
            dom_before = ""
            try:
                dom_before = await self.page.evaluate("() => document.body.innerHTML.substring(0, 1000)")
            except:
                pass
            
            try:
                await element.click(timeout=5000)
            except PlaywrightTimeout:
                self.logger.warning(f"Click timeout on element {index}, trying force click")
                try:
                    await element.click(force=True, timeout=3000)
                except:
                    return ActionResult(
                        action="click_element",
                        success=False,
                        error=f"Element {index} click timed out"
                    )
            
            await human_delay(500, 1000)
            
            if is_link:
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                except:
                    pass
            
            url_after = self.page.url
            dom_after = ""
            try:
                dom_after = await self.page.evaluate("() => document.body.innerHTML.substring(0, 1000)")
            except:
                pass
            
            url_changed = url_after != url_before
            dom_changed = dom_after != dom_before
            
            if url_changed or dom_changed:
                self.logger.info(f"Click caused change (URL: {url_changed}, DOM: {dom_changed})")
            else:
                self.logger.warning("Click did not cause visible changes")
            
            return ActionResult(
                action="click_element",
                success=True,
                url=url_after if url_changed else None
            )
            
        except Exception as e:
            return ActionResult(action="click_element", success=False, error=str(e))
    
    async def _input_text(self, index: int, text: str) -> ActionResult:
        try:
            if index is None:
                return ActionResult(action="input_text", success=False, error="No index provided")
            
            if not text:
                return ActionResult(action="input_text", success=False, error="No text provided")
            
            element = await self.dom_service.get_element_by_index(self.page, index)
            
            if not element:
                return ActionResult(
                    action="input_text",
                    success=False,
                    error=f"Element {index} not found"
                )
            
            try:
                tag = await element.evaluate("el => el.tagName.toLowerCase()")
                if tag not in ['input', 'textarea']:
                    return ActionResult(
                        action="input_text",
                        success=False,
                        error=f"Element {index} is not an input field (found: {tag})"
                    )
            except Exception as e:
                self.logger.warning(f"Could not verify element type: {e}")
            
            await element.scroll_into_view_if_needed()
            await human_delay(200, 400)
            
            await element.click()
            await human_delay(300, 500)
            
            try:
                current_value = await element.input_value()
                if current_value:
                    await element.fill('')
                    await asyncio.sleep(0.2)
            except:
                pass
            
            input_success = False
            method_used = None
            
            strategies = [
                ("fill", self._input_strategy_fill),
                ("press_sequentially", self._input_strategy_press_sequentially),
                ("javascript", self._input_strategy_javascript),
                ("type", self._input_strategy_type)
            ]
            
            for strategy_name, strategy_func in strategies:
                try:
                    self.logger.info(f"Trying {strategy_name} for text input")
                    if await strategy_func(element, text):
                        input_success = True
                        method_used = strategy_name
                        self.logger.info(f"Successfully entered text using {strategy_name}")
                        break
                    else:
                        self.logger.debug(f"{strategy_name} verification failed")
                except Exception as e:
                    self.logger.debug(f"{strategy_name} method failed: {e}")
                    continue
            
            if not input_success:
                try:
                    final_value = await element.input_value()
                    if final_value and len(final_value) > 0:
                        self.logger.warning(f"Text partially entered: got '{final_value}' (expected '{text}')")
                        input_success = True
                        method_used = "partial"
                except:
                    pass
            
            if not input_success:
                return ActionResult(
                    action="input_text",
                    success=False,
                    error="All input methods failed"
                )
            
            await human_delay(200, 400)
            
            is_in_form = False
            is_login_form = False
            is_search = False
            
            try:
                form_check = await element.evaluate("""
                    el => {
                        const form = el.closest('form');
                        if (!form) return { inForm: false, isLogin: false };
                        
                        const formText = (form.textContent || '').toLowerCase();
                        const formHTML = (form.innerHTML || '').toLowerCase();
                        const combined = formText + formHTML;
                        
                        const loginKeywords = ['password', 'login', 'sign in', 'signin', 'log in', 'auth', 'credentials', 'email code', 'verification'];
                        const isLogin = loginKeywords.some(kw => combined.includes(kw));
                        
                        return { inForm: true, isLogin: isLogin };
                    }
                """)
                is_in_form = form_check.get('inForm', False)
                is_login_form = form_check.get('isLogin', False)
            except:
                pass
            
            try:
                input_type = await element.get_attribute("type")
                is_search = (input_type == "search")
                
                if not is_search:
                    placeholder = await element.get_attribute("placeholder")
                    aria_label = await element.get_attribute("aria-label")
                    name = await element.get_attribute("name")
                    is_search = any(
                        "search" in str(attr).lower()
                        for attr in [placeholder, aria_label, name]
                        if attr
                    )
            except:
                pass
            
            should_submit = is_in_form and is_search and not is_login_form
            
            if should_submit:
                self.logger.info("Input is in search form - submitting with Enter")
                try:
                    await element.press("Enter")
                    await asyncio.sleep(2)
                except Exception as e:
                    self.logger.warning(f"Form submit failed: {e}")
            elif is_login_form:
                self.logger.info("Input is in login form - NOT auto-submitting")
            
            url = self.page.url
            title = await self.page.title()
            
            return ActionResult(
                action="input_text",
                success=True,
                url=url,
                title=title
            )
            
        except Exception as e:
            return ActionResult(action="input_text", success=False, error=str(e))
    
    async def _input_strategy_press_sequentially(self, element, text: str) -> bool:
        try:
            await element.press_sequentially(text, delay=random.randint(80, 120))
            await asyncio.sleep(0.5)
            
            final_value = await element.input_value()
            return final_value == text
        except Exception as e:
            self.logger.debug(f"Press sequentially error: {e}")
            return False
    
    async def _input_strategy_fill(self, element, text: str) -> bool:
        try:
            await element.fill(text)
            await asyncio.sleep(0.4)
            
            final_value = await element.input_value()
            return final_value == text
        except Exception as e:
            self.logger.debug(f"Fill strategy error: {e}")
            return False 
    
    async def _input_strategy_javascript(self, element, text: str) -> bool:
        try:
            import json
            
            await element.evaluate("el => el.value = ''")
            await asyncio.sleep(0.1)
            
            await element.evaluate(f"el => {{ el.value = {json.dumps(text)}; }}")
            await asyncio.sleep(0.1)
            
            await element.evaluate("el => el.dispatchEvent(new Event('input', { bubbles: true }))")
            await element.evaluate("el => el.dispatchEvent(new Event('change', { bubbles: true }))")
            await element.evaluate("el => el.dispatchEvent(new Event('keyup', { bubbles: true }))")
            await asyncio.sleep(0.4)
            
            final_value = await element.input_value()
            return final_value == text
        except Exception as e:
            self.logger.debug(f"JavaScript strategy error: {e}")
            return False
    
    async def _input_strategy_type(self, element, text: str) -> bool:
        try:
            try:
                await element.press("Control+A")
                await asyncio.sleep(0.05)
                await element.press("Backspace")
                await asyncio.sleep(0.1)
            except:
                pass
            
            await element.type(text, delay=random.randint(50, 100))
            await asyncio.sleep(0.4)
            
            final_value = await element.input_value()
            return final_value == text
        except Exception as e:
            self.logger.debug(f"Type strategy error: {e}")
            return False
    
    async def _input_otp(self, index: int, email_handler) -> ActionResult:
        try:
            if index is None:
                return ActionResult(action="input_otp", success=False, error="No index provided")
            
            if not email_handler:
                return ActionResult(action="input_otp", success=False, error="No email handler provided")
            
            element = await self.dom_service.get_element_by_index(self.page, index)
            
            if not element:
                return ActionResult(
                    action="input_otp",
                    success=False,
                    error=f"Element {index} not found"
                )
            
            self.logger.info("Waiting for OTP from email...")
            
            otp_code = email_handler.get_latest_otp(sender_email="no-reply@auth.allrecipes.com", timeout=60)
            
            if not otp_code:
                return ActionResult(
                    action="input_otp",
                    success=False,
                    error="Failed to retrieve OTP code from email"
                )
            
            self.logger.info(f"Retrieved OTP code, entering into field...")
            
            await element.scroll_into_view_if_needed()
            await human_delay(300, 500)
            
            await element.click()
            await human_delay(200, 300)
            
            try:
                await element.fill("")
            except:
                pass
            
            await human_delay(100, 200)
            
            input_success = False
            
            try:
                await element.type(otp_code, delay=random.randint(80, 120))
                input_success = True
                self.logger.info("OTP entered using type method")
            except:
                pass
            
            if not input_success:
                try:
                    await element.fill(otp_code)
                    input_success = True
                    self.logger.info("OTP entered using fill method")
                except:
                    pass
            
            if not input_success:
                try:
                    for char in otp_code:
                        await element.type(char)
                        await asyncio.sleep(random.uniform(0.08, 0.12))
                    input_success = True
                    self.logger.info("OTP entered character by character")
                except:
                    pass
            
            await asyncio.sleep(0.5)
            
            try:
                final_value = await element.input_value()
                if final_value == otp_code:
                    self.logger.info(f"OTP verified successfully: {otp_code}")
                elif final_value:
                    self.logger.warning(f"OTP value mismatch: expected '{otp_code}', got '{final_value}'")
                else:
                    self.logger.warning("Could not verify OTP value in field")
            except:
                self.logger.info("OTP entered (verification skipped)")
            
            await human_delay(200, 400)
            
            url = self.page.url
            title = await self.page.title()
            
            return ActionResult(
                action="input_otp",
                success=True,
                url=url,
                title=title
            )
            
        except Exception as e:
            return ActionResult(action="input_otp", success=False, error=str(e))

    async def _wait_for_manual_otp(self, initial_url: str, timeout: int = 300) -> ActionResult:
        try:
            self.logger.info(f"Waiting for manual OTP entry (timeout: {timeout}s)")
            self.logger.info("User should manually enter OTP in the browser window")
            
            start_time = asyncio.get_event_loop().time()
            last_url = initial_url
            url_stable_count = 0
            
            while asyncio.get_event_loop().time() - start_time < timeout:
                await asyncio.sleep(2)
                
                current_url = self.page.url
                
                if current_url != initial_url:
                    if current_url == last_url:
                        url_stable_count += 1
                    else:
                        url_stable_count = 0
                        last_url = current_url
                    
                    if url_stable_count >= 2:
                        self.logger.info(f"Navigation detected and stable: {current_url}")
                        
                        await asyncio.sleep(2)
                        
                        page_text = ""
                        try:
                            page_text = await self.page.evaluate("() => document.body.innerText.toLowerCase()")
                        except:
                            pass
                        
                        error_keywords = ["invalid", "incorrect", "wrong code", "expired", "try again", "error"]
                        has_error = any(keyword in page_text for keyword in error_keywords)
                        
                        if has_error:
                            self.logger.warning("Possible OTP error detected on page")
                            return ActionResult(
                                action="wait_for_manual_otp",
                                success=False,
                                error="OTP verification failed - please check the code"
                            )
                        
                        otp_keywords = ["verification code", "enter code", "otp", "email code"]
                        still_on_otp = any(keyword in page_text for keyword in otp_keywords)
                        
                        if not still_on_otp:
                            self.logger.info("Successfully moved past OTP screen")
                            return ActionResult(
                                action="wait_for_manual_otp",
                                success=True,
                                url=current_url
                            )
                
                elapsed = int(asyncio.get_event_loop().time() - start_time)
                remaining = timeout - elapsed
                
                if remaining > 0 and remaining % 30 == 0:
                    self.logger.info(f"Still waiting for manual OTP entry ({remaining}s remaining)...")
            
            self.logger.error("Manual OTP entry timeout")
            return ActionResult(
                action="wait_for_manual_otp",
                success=False,
                error=f"Timeout after {timeout}s waiting for manual OTP entry"
            )
            
        except Exception as e:
            self.logger.error(f"Error waiting for manual OTP: {e}")
            return ActionResult(action="wait_for_manual_otp", success=False, error=str(e))

    

    
    async def _select_dropdown(self, index: int, option: str) -> ActionResult:
        try:
            if index is None:
                return ActionResult(action="select_dropdown", success=False, error="No index provided")
            
            if not option:
                return ActionResult(action="select_dropdown", success=False, error="No option provided")
            
            element = await self.dom_service.get_element_by_index(self.page, index)
            
            if not element:
                return ActionResult(
                    action="select_dropdown",
                    success=False,
                    error=f"Element {index} not found"
                )
            
            await element.scroll_into_view_if_needed()
            await human_delay(200, 400)
            
            try:
                await element.select_option(label=option)
            except:
                try:
                    await element.select_option(value=option)
                except:
                    try:
                        await element.select_option(index=int(option))
                    except:
                        return ActionResult(
                            action="select_dropdown",
                            success=False,
                            error=f"Could not select option '{option}'"
                        )
            
            await human_delay(300, 500)
            
            return ActionResult(action="select_dropdown", success=True)
            
        except Exception as e:
            return ActionResult(action="select_dropdown", success=False, error=str(e))
    
    async def _set_price_range(
        self,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None
    ) -> ActionResult:
        try:
            self.logger.info(f"Setting price range: {min_price} – {max_price}")

            inputs = await self.page.query_selector_all(
                'input[type="number"], input[placeholder*="Min" i], input[placeholder*="Max" i]'
            )

            if not inputs:
                return ActionResult(
                    action="set_price_range",
                    success=False,
                    error="No price inputs found"
                )

            for inp in inputs:
                placeholder = (await inp.get_attribute("placeholder") or "").lower()
                name = (await inp.get_attribute("name") or "").lower()

                if min_price is not None and ("min" in placeholder or "min" in name):
                    await inp.fill(str(min_price))

                if max_price is not None and ("max" in placeholder or "max" in name):
                    await inp.fill(str(max_price))

            apply_btn = await self.page.query_selector(
                'button:has-text("Apply"), button:has-text("Go")'
            )
            if apply_btn:
                await apply_btn.click()

            await human_delay(500, 1000)

            return ActionResult(action="set_price_range", success=True)

        except Exception as e:
            return ActionResult(
                action="set_price_range",
                success=False,
                error=str(e)
            )
        
    async def _select_date(self, date: str) -> ActionResult:
        try:
            self.logger.info(f"Selecting date: {date}")

            for _ in range(12):
                day = await self.page.query_selector(f'[data-date="{date}"], [aria-label*="{date}"]')
                if day:
                    await day.scroll_into_view_if_needed()
                    disabled = await day.get_attribute("aria-disabled")
                    if disabled == "true":
                        return ActionResult(action="select_date", success=False, error=f"Date {date} is disabled")
                    await day.click()
                    await human_delay(300, 600)
                    return ActionResult(action="select_date", success=True)

                next_btn = await self.page.query_selector(
                    'button[aria-label*="Next" i], button[aria-label*="next month" i], .bui-calendar__control--next'
                )
                if not next_btn:
                    break
                await next_btn.click()
                await human_delay(200, 400)

            return ActionResult(action="select_date", success=False, error=f"Date {date} not found after scanning months")

        except Exception as e:
            return ActionResult(action="select_date", success=False, error=str(e))

    async def _wait_for_dynamic_content(self, timeout: int = 15) -> bool:
        try:
            start_time = asyncio.get_event_loop().time()
            last_content_hash = None
            stable_count = 0
            required_stable = 3
            
            while asyncio.get_event_loop().time() - start_time < timeout:
                await asyncio.sleep(1)
                
                try:
                    current_content = await self.page.evaluate("""
                        () => {
                            const allText = document.body.innerText || '';
                            const interactiveCount = document.querySelectorAll('button, a, input, select').length;
                            return allText.substring(0, 5000) + '|' + interactiveCount;
                        }
                    """)
                    
                    content_hash = hashlib.md5(current_content.encode()).hexdigest()
                    
                    if content_hash == last_content_hash:
                        stable_count += 1
                        if stable_count >= required_stable:
                            self.logger.info(f"Content stabilized after {int(asyncio.get_event_loop().time() - start_time)}s")
                            return True
                    else:
                        stable_count = 0
                        last_content_hash = content_hash
                        
                except Exception as e:
                    self.logger.debug(f"Content check error: {e}")
                    continue
            
            self.logger.warning(f"Content did not stabilize within {timeout}s")
            return False
            
        except Exception as e:
            self.logger.error(f"Wait for dynamic content failed: {e}")
            return False

    async def _scroll_page_for_extraction(self):
        try:
            await self.page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)
            
            viewport_height = await self.page.evaluate("window.innerHeight")
            page_height = await self.page.evaluate("document.body.scrollHeight")
            
            if page_height > viewport_height * 1.5:
                scroll_positions = []
                current = 0
                step = viewport_height * 0.7
                
                while current < page_height:
                    scroll_positions.append(int(current))
                    current += step
                
                for pos in scroll_positions[:10]:
                    await self.page.evaluate(f"window.scrollTo(0, {pos})")
                    await asyncio.sleep(0.8)
                
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.0)
                
                await self.page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(0.3)
                
                self.logger.info(f"Scrolled page to load content (height: {page_height}px)")
        except Exception as e:
            self.logger.warning(f"Scroll failed: {e}")
    
    async def _extract(self, goal: str, task: str = None) -> ActionResult:
        try:
            self.logger.info(f"Extracting: {goal}")
            
            await self._wait_for_dynamic_content(timeout=10)
            
            await self._scroll_page_for_extraction()
            
            constraints = ConstraintParser.parse_task(task or goal)
            has_constraints = len(constraints) > 0
            
            if has_constraints:
                self.logger.info(f"Detected {len(constraints)} constraints: {[c.original_text for c in constraints]}")
            
            url = self.page.url or ""
            goal_lower = goal.lower()
            
            if "wolframalpha.com" in url or any(kw in goal_lower for kw in ["angle", "length", "value", "result", "compute", "calculate"]):
                raw_content = await self._extract_numerical_with_graphs()
            elif any(keyword in goal_lower for keyword in ["price", "cost", "recipe", "ingredient", "specification", "detail"]):
                raw_content = await self._extract_structured_readonly()
            else:
                raw_content = await self._extract_full_page_readonly()
            
            if not self.llm:
                self.logger.warning("No LLM available, returning raw content")
                return ActionResult(
                    action="extract",
                    success=True,
                    extracted_content=raw_content[:1000]
                )
            
            constraint_text = ""
            if has_constraints:
                constraint_text = "\n\n" + ConstraintParser.format_constraints_for_prompt(constraints)
            
            full_task_context = f"Original task: {task}" if task and task != goal else ""
            
            if "wolframalpha.com" in url or any(kw in goal_lower for kw in ["angle", "length", "value", "result", "compute", "calculate"]):
                enhanced_prompt = f"""You are an expert at extracting numerical results from computational outputs.

EXTRACTION GOAL: {goal}
{full_task_context}

CRITICAL INSTRUCTIONS:
1. Look for ACTUAL NUMERICAL VALUES, not descriptions like "can be inferred from"
2. Extract specific numbers with units (e.g., "45 degrees", "0.15 meters", "3.5 seconds")
3. If the goal asks for multiple values (e.g., "angle and length"), extract ALL requested values
4. If data is in a table or labeled output, extract the values directly
5. Return in this exact format:
{{
    "final_angle": "actual numerical value with unit",
    "final_length": "actual numerical value with unit",
    "additional_data": {{"key": "value"}}
}}

If you see text like "Not directly provided" or "can be inferred from graph", that means extraction FAILED.
You must find the actual numerical values or return {{"error": "Numerical values not found in text"}}"""
            else:
                enhanced_prompt = f"""You are an expert at extracting and filtering information from web pages.

EXTRACTION GOAL: {goal}
{full_task_context}
{constraint_text}

CRITICAL INSTRUCTIONS:
1. Extract items as a structured JSON array with ALL visible attributes (name, price, rating, reviews, time, ingredients, etc.)
2. For EACH item, evaluate if it fully matches the EXTRACTION GOAL considering:
   - Semantic equivalents (e.g., "spinach" or "kale" count as "leaves", "banana" in ingredients counts as "includes bananas")
   - All numeric constraints (rating, reviews, price, time)
   - All qualitative requirements (dietary restrictions, ingredients, categories)
3. ONLY include items that genuinely match the FULL extraction goal, not just some constraints
4. For each item, add a "match_reason" field explaining why it matches the goal
5. Return in this exact format:
{{
    "items": [
        {{"name": "Item Name", "rating": "4.5", "reviews": "120", "ingredients": [...], "match_reason": "Matches because: vegan (no animal products), includes banana and spinach (leafy greens), 120 reviews > 20, rating 4.5 >= 4"}},
        ...
    ],
    "total_found_on_page": 10,
    "matching_items": 1
}}

If NO items fully match the goal, return {{"items": [], "total_found_on_page": N, "note": "Found N items on page but none matched the full criteria because..."}}

IMPORTANT: Use semantic understanding. "Leaves" includes spinach, kale, lettuce, etc. "Bananas" matches "banana" in ingredients."""

            extraction_result = self.llm.extract_content(
                system_prompt=enhanced_prompt,
                extraction_goal=goal,
                page_content=raw_content
            )
            
            extracted_text = extraction_result.extracted_content
            
            if has_constraints and isinstance(extracted_text, str):
                try:
                    import json
                    data = json.loads(extracted_text)
                    
                    if isinstance(data, dict) and "items" in data:
                        items = data.get("items", [])
                        total_found = data.get("total_found_on_page", len(items))
                        note = data.get("note", "")
                        
                        if items:
                            for item in items:
                                match_reason = item.get("match_reason", "")
                                if match_reason:
                                    self.logger.info(f"Item '{item.get('name', 'Unknown')}' matched: {match_reason[:100]}")
                            
                            result_data = {
                                "items": items,
                                "match_quality": "perfect",
                                "total_extracted": total_found,
                                "note": f"LLM validated {len(items)} items match the full extraction goal including semantic requirements."
                            }
                            self.logger.info(f"LLM extracted and validated {len(items)} items matching full goal out of {total_found} found on page")
                        else:
                            result_data = {
                                "items": [],
                                "match_quality": "none",
                                "total_extracted": total_found,
                                "note": note or f"Found {total_found} items on page but none matched the full criteria.",
                                "suggestion": "Try relaxing requirements or different search terms."
                            }
                            self.logger.info(f"LLM found {total_found} items but none matched full goal. Reason: {note}")
                        
                        extracted_text = json.dumps(result_data, indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    self.logger.warning("Could not parse extraction as JSON for constraint filtering")
            
            if isinstance(extracted_text, str):
                self.logger.info(f"Extracted {len(extracted_text)} chars: {extracted_text[:150]}...")
            else:
                import json
                extracted_text = json.dumps(extracted_text, ensure_ascii=False)
                self.logger.info(f"Extracted structured data: {extracted_text[:150]}...")
            
            return ActionResult(
                action="extract",
                success=True,
                extracted_content=extracted_text
            )
            
        except Exception as e:
            self.logger.error(f"Extraction error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return ActionResult(
                action="extract",
                success=False,
                error=str(e),
                extracted_content="Extraction failed: " + str(e)
            )
    async def _extract_wolfram_results(self) -> str:
        try:
            content = await self.page.evaluate("""
            () => {
            const results = [];
            const safeText = (el) => {
                try {
                if (!el) return "";
                const t = (el.innerText || el.textContent || "");
                return (t || "").trim();
                } catch (e) {
                return "";
                }
            };
            const pods = document.querySelectorAll('[class*="pod"], section, article');
            for (const pod of pods) {
                const podText = safeText(pod);
                if (!podText || podText.length < 20) continue;
                const podTitle = pod.querySelector('h2, h3, h4, [class*="title"], [class*="header"]');
                const title = podTitle ? safeText(podTitle) : "";
                const titleLower = title.toLowerCase();
                const isResult = titleLower.includes('result') || titleLower.includes('solution') || titleLower.includes('output') || titleLower.includes('plot') || titleLower.includes('graph');
                if (isResult || podText.length > 100) {
                    const prefix = title ? `SECTION[${title}]: ` : "SECTION: ";
                    results.push(prefix + podText);
                }
            }
            const allTables = document.querySelectorAll('table');
            for (const table of allTables) {
                const tableText = safeText(table);
                if (tableText && tableText.length > 20) {
                    results.push("TABLE: " + tableText);
                }
            }
            const allDL = document.querySelectorAll('dl');
            for (const dl of allDL) {
                const dlText = safeText(dl);
                if (dlText && dlText.length > 10) {
                    results.push("DATA: " + dlText);
                }
            }
            const valueElements = document.querySelectorAll('[data-value], [data-result], [class*="value"], [class*="result"], [class*="output"]');
            for (const el of valueElements) {
                const dataValue = el.getAttribute('data-value') || el.getAttribute('data-result');
                if (dataValue) {
                    results.push("VALUE_ATTR: " + dataValue);
                }
                const text = safeText(el);
                if (text && text.length > 5 && text.length < 500 && /\\d/.test(text)) {
                    results.push("VALUE_TEXT: " + text);
                }
            }
            if (results.length === 0) {
                const main = document.querySelector('main, [role="main"], article, body');
                return safeText(main);
            }
            return results.join("\\\\n\\\\n");
            }
            """)
            if not content or len(content) < 20:
                content = await self._extract_full_page_readonly()
            return content[:25000]
        except Exception as e:
            self.logger.error(f"Wolfram extraction failed: {e}")
            return await self._extract_full_page_readonly()
    async def _extract_numerical_with_graphs(self) -> str:
        try:
            content = await self.page.evaluate("""
            () => {
            const results = [];

            const safeText = (el) => {
                try {
                if (!el) return "";
                const t = (el.innerText || el.textContent || "");
                return (t || "").trim();
                } catch (e) {
                return "";
                }
            };

            const pushIfGood = (label, text) => {
                const t = (text || "").trim();
                if (!t) return;
                if (t.length < 5) return;
                results.push(label + ": " + t);
            };

            try {
                const scripts = document.querySelectorAll('script[type="application/json"], script[type="application/ld+json"]');
                scripts.forEach(script => {
                    const text = safeText(script);
                    if (text && text.length > 20) {
                        try {
                            const data = JSON.parse(text);
                            if (data && typeof data === 'object') {
                                pushIfGood("JSON_DATA", JSON.stringify(data));
                            }
                        } catch (e) {}
                    }
                });
            } catch (e) {}

            try {
                const dataElements = document.querySelectorAll('[data-value], [data-result], [data-output], [data-answer]');
                dataElements.forEach(el => {
                    const value = el.getAttribute('data-value') || el.getAttribute('data-result') || el.getAttribute('data-output') || el.getAttribute('data-answer');
                    if (value) pushIfGood("DATA_ATTR", value);
                    const text = safeText(el);
                    if (text && text.length > 5) pushIfGood("DATA_ELEM", text);
                });
            } catch (e) {}

            try {
                document.querySelectorAll("table").forEach((table) => {
                const t = safeText(table);
                if (t && t.length > 20) pushIfGood("TABLE", t);
                });
            } catch (e) {}

            try {
                document.querySelectorAll("dl").forEach((dl) => {
                const t = safeText(dl);
                if (t && t.length > 10) pushIfGood("DETAILS", t);
                });
            } catch (e) {}

            try {
                const resultDivs = document.querySelectorAll(
                '[class*="result"], [class*="output"], [class*="answer"], [class*="solution"],' +
                ' [data-testid*="result"], [data-qa*="result"], [id*="result"], [id*="output"]'
                );

                resultDivs.forEach((div) => {
                const t = safeText(div);
                if (t && t.length > 5) pushIfGood("RESULT", t);
                });
            } catch (e) {}

            try {
                const graphTexts = document.querySelectorAll('[class*="graph"], [class*="chart"], [class*="plot"], canvas, svg');
                graphTexts.forEach(el => {
                    const ariaLabel = el.getAttribute('aria-label');
                    if (ariaLabel && ariaLabel.length > 10) pushIfGood("GRAPH_LABEL", ariaLabel);
                    
                    const title = el.querySelector('title');
                    if (title) pushIfGood("GRAPH_TITLE", safeText(title));
                    
                    const parent = el.closest('[class*="pod"], [class*="result"], [class*="output"]');
                    if (parent) {
                        const parentText = safeText(parent);
                        if (parentText && parentText.length > 20 && parentText.length < 2000) {
                            pushIfGood("GRAPH_CONTEXT", parentText);
                        }
                    }
                });
            } catch (e) {}

            try {
                const headings = document.querySelectorAll('h1, h2, h3, h4, h5, h6');
                headings.forEach(h => {
                    const text = safeText(h);
                    if (text && text.length > 5) {
                        const nextEl = h.nextElementSibling;
                        if (nextEl) {
                            const nextText = safeText(nextEl);
                            if (nextText && nextText.length > 5 && nextText.length < 500) {
                                pushIfGood("HEADING_CONTENT", text + " -> " + nextText);
                            }
                        }
                    }
                });
            } catch (e) {}

            try {
                const valueElements = document.querySelectorAll('[class*="value"], [class*="number"], [class*="metric"]');
                valueElements.forEach(el => {
                    const text = safeText(el);
                    if (text && /\d/.test(text) && text.length < 200) {
                        pushIfGood("VALUE", text);
                    }
                });
            } catch (e) {}

            if (results.length > 0) {
                const seen = new Set();
                const deduped = [];
                for (const item of results) {
                const key = item.slice(0, 300);
                if (!seen.has(key)) {
                    seen.add(key);
                    deduped.push(item);
                }
                }
                return deduped.join("\\n\\n");
            }

            try {
                const main = document.querySelector("main, [role='main'], article, .content, #content");
                const t = safeText(main || document.body);
                return t || "";
            } catch (e) {
                return safeText(document.body) || "";
            }
            }
            """)

            if not content:
                return "No content found"

            return content[:20000]

        except Exception as e:
            self.logger.error(f"Numerical extraction failed: {e}")
            return await self._extract_full_page_readonly()

    async def _extract_structured_readonly(self) -> str:
        try:
            content = await self.page.evaluate("""
            () => {
            const results = [];

            const safeText = (el) => {
                try {
                if (!el) return "";
                const t = (el.innerText || el.textContent || "");
                return (t || "").trim();
                } catch (e) {
                return "";
                }
            };

            const pushIfGood = (label, text) => {
                const t = (text || "").trim();
                if (!t) return;
                if (t.length < 10) return;
                results.push(label + ": " + t);
            };

            try {
                document.querySelectorAll("table").forEach((table) => {
                const t = safeText(table);
                if (t && t.length > 20) pushIfGood("TABLE", t);
                });
            } catch (e) {}

            try {
                document.querySelectorAll("dl").forEach((dl) => {
                const t = safeText(dl);
                if (t && t.length > 10) pushIfGood("DETAILS", t);
                });
            } catch (e) {}

            try {
                const contentDivs = document.querySelectorAll(
                '[class*="ingredient"], [class*="ingredients"], [class*="recipe"], [class*="nutrition"],' +
                ' [class*="spec"], [class*="feature"], [class*="detail"], [class*="product"], [class*="description"],' +
                ' [data-testid*="ingredient"], [data-qa*="ingredient"], [data-testid*="recipe"], [data-qa*="recipe"]'
                );

                contentDivs.forEach((div) => {
                const t = safeText(div);
                if (t && t.length > 10) pushIfGood("CONTENT", t);
                });
            } catch (e) {}

            if (results.length > 0) {
                const seen = new Set();
                const deduped = [];
                for (const item of results) {
                const key = item.slice(0, 300);
                if (!seen.has(key)) {
                    seen.add(key);
                    deduped.push(item);
                }
                }
                return deduped.join("\\n\\n");
            }

            try {
                const main = document.querySelector("main, [role='main'], article, .content, #content");
                const t = safeText(main || document.body);
                return t || "";
            } catch (e) {
                return safeText(document.body) || "";
            }
            }
            """)

            if not content:
                return "No structured content found"

            return content[:15000]

        except Exception as e:
            self.logger.error(f"Structured extraction failed: {e}")
            return await self._extract_clean_text_readonly()

    
    async def _extract_full_page_readonly(self) -> str:
        try:
            content = await self.page.evaluate("""
                () => {
                    const unwantedSelectors = 'script, style, nav, header, footer, aside, .ad, .advertisement';
                    const unwantedElements = document.querySelectorAll(unwantedSelectors);
                    const unwantedSet = new Set(unwantedElements);
                    
                    function getTextRecursive(node) {
                        let text = '';
                        
                        if (node.nodeType === Node.TEXT_NODE) {
                            text = node.textContent.trim();
                        } else if (node.nodeType === Node.ELEMENT_NODE) {
                            if (unwantedSet.has(node)) {
                                return '';
                            }
                            
                            for (const child of node.childNodes) {
                                text += getTextRecursive(child) + ' ';
                            }
                        }
                        
                        return text;
                    }
                    
                    const text = getTextRecursive(document.body);
                    return text.replace(/\\s+/g, ' ').trim();
                }
            """)
            return content[:15000] if content else "No content found"
        except Exception as e:
            return await self._extract_clean_text_readonly()
    
    async def _extract_clean_text_readonly(self) -> str:
        try:
            clean_text = await self.page.evaluate("""
                () => {
                    const bodyText = document.body.innerText || document.body.textContent || '';
                    return bodyText.replace(/\\s+/g, ' ').trim();
                }
            """)
            
            return clean_text[:15000] if clean_text else "No content found"
                
        except Exception as e:
            raise e
    
    async def _scroll(self, direction: str, amount: int) -> ActionResult:
        try:
            if direction == "up":
                amount = -abs(amount)
            else:
                amount = abs(amount)
            
            await self.page.evaluate(f"window.scrollBy(0, {amount})")
            await human_delay(300, 600)
            
            return ActionResult(action="scroll", success=True)
            
        except Exception as e:
            return ActionResult(action="scroll", success=False, error=str(e))
    
    async def _send_keys(self, keys: str) -> ActionResult:
        try:
            if not keys:
                return ActionResult(action="send_keys", success=False, error="No keys provided")
            
            await self.page.keyboard.press(keys)
            await human_delay(200, 400)
            
            return ActionResult(action="send_keys", success=True)
            
        except Exception as e:
            return ActionResult(action="send_keys", success=False, error=str(e))
    
    async def _wait(self, duration: float) -> ActionResult:
        try:
            if duration is None or duration <= 0:
                self.logger.info("Waiting for content...")
                
                try:
                    await self.page.wait_for_selector(
                        '[class*="loading"], [class*="spinner"], [aria-busy="true"]',
                        state="hidden",
                        timeout=5000
                    )
                    self.logger.info("Loading indicators cleared")
                except:
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=5000)
                        self.logger.info("Network idle")
                    except:
                        await asyncio.sleep(2)
                        self.logger.info("Waited 2 seconds (fallback)")
                
                return ActionResult(action="wait", success=True)
            else:
                await asyncio.sleep(duration)
                return ActionResult(action="wait", success=True)
                
        except Exception as e:
            return ActionResult(action="wait", success=False, error=str(e))
    
    async def _close_cookie_popup(self) -> ActionResult:
        try:
            success = await close_cookie_popup(self.page)
            return ActionResult(
                action="close_cookie_popup",
                success=success,
                error=None if success else "Could not find/close cookie popup"
            )
        except Exception as e:
            return ActionResult(action="close_cookie_popup", success=False, error=str(e))
    
    async def _close_popup(self) -> ActionResult:
        try:
            success = await close_generic_popup(self.page)
            return ActionResult(
                action="close_popup",
                success=success,
                error=None if success else "Could not find/close popup"
            )
        except Exception as e:
            return ActionResult(action="close_popup", success=False, error=str(e))


class ExtractAllrecipesRecipeAction:
    name = "extract_allrecipes_recipe"
    description = "Extract recipe name, rating, review count, ingredients, and total time from an Allrecipes recipe page."

    async def run(self, page, **kwargs):
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(1000)
        
        viewport_height = await page.evaluate("window.innerHeight")
        page_height = await page.evaluate("document.body.scrollHeight")
        
        scroll_steps = min(6, int(page_height / (viewport_height * 0.8)))
        for i in range(scroll_steps):
            scroll_pos = int(i * viewport_height * 0.8)
            await page.evaluate(f"window.scrollTo(0, {scroll_pos})")
            await page.wait_for_timeout(1000)
        
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)
        
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)
        
        result = await page.evaluate("""
        () => {
        const txt = (el) => {
            if (!el) return null;
            try {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') {
                    return null;
                }
            } catch (e) {}
            const text = (el.innerText || el.textContent || '').trim();
            return text || null;
        };
        
        const first = (sels) => {
            for (const sel of sels) {
                try {
                    const el = document.querySelector(sel);
                    if (el) {
                        const t = txt(el);
                        if (t) return t;
                    }
                } catch (e) {}
            }
            return null;
        };
        
        const fullText = document.body.innerText || document.body.textContent || '';

        const recipe_name = first([
            'h1.article-heading',
            'h1[class*="headline"]', 
            'h1',
            '[data-testid="headline"]',
            '.headline-wrapper h1'
        ]);

        let rating = first([
            '#mntl-recipe-review-bar__rating_1-0',
            '[id*="mntl-recipe-review-bar__rating"]',
            '.mntl-recipe-review-bar__rating',
            '[class*="rating"]',
            '[data-qa="rating"]'
        ]);
        
        if (!rating) {
            const match = fullText.match(/(\d+(?:\.\d+)?)\s*(?:out of 5|stars|star rating)/i);
            if (match) rating = match[1];
        }

        let number_of_reviews = first([
            '#mntl-recipe-review-bar__rating-count_1-0',
            '[id*="rating-count"]',
            '[data-qa="review-count"]',
            '[class*="rating-count"]'
        ]);
        
        if (!number_of_reviews) {
            const match = fullText.match(/(\d+)\s*(?:rating|review)s?/i);
            if (match) number_of_reviews = match[1];
        }

        let total_time = null;
        let prep_time = null;
        let cook_time = null;
        
        const timePatterns = [
            {label: 'total', regex: /total(?:\s+time)?[:\s]+(\d+)\s*(min|mins|minutes|hour|hours|hr|hrs)/i},
            {label: 'prep', regex: /prep(?:\s+time)?[:\s]+(\d+)\s*(min|mins|minutes|hour|hours|hr|hrs)/i},
            {label: 'cook', regex: /cook(?:\s+time)?[:\s]+(\d+)\s*(min|mins|minutes|hour|hours|hr|hrs)/i}
        ];
        
        for (const {label, regex} of timePatterns) {
            const match = fullText.match(regex);
            if (match) {
                let time = parseInt(match[1]);
                if (match[2].toLowerCase().includes('hour') || match[2].toLowerCase().includes('hr')) {
                    time *= 60;
                }
                const timeStr = time + ' mins';
                if (label === 'total') total_time = timeStr;
                if (label === 'prep') prep_time = timeStr;
                if (label === 'cook') cook_time = timeStr;
            }
        }

        let ingredients = [];
        const selectors = [
            'li[data-ingredient-name="true"]',
            'li[data-ingredient-name]',
            '[data-ingredient]',
            'li.mntl-structured-ingredients__list-item',
            'ul[class*="ingredient"] li',
            'li.ingredient'
        ];
        
        for (const sel of selectors) {
            try {
                const items = document.querySelectorAll(sel);
                if (items.length > 0) {
                    for (const item of items) {
                        const t = txt(item);
                        if (t && t.length > 2) {
                            ingredients.push(t);
                        }
                    }
                    if (ingredients.length > 0) break;
                }
            } catch (e) {}
        }
        
        if (ingredients.length === 0) {
            const allLists = document.querySelectorAll('ul, ol');
            for (const list of allLists) {
                const parent = list.closest('section, div, article');
                if (parent) {
                    const parentText = (parent.textContent || '').toLowerCase();
                    if (parentText.includes('ingredient')) {
                        const items = list.querySelectorAll('li');
                        for (const item of items) {
                            const t = txt(item);
                            if (t && t.length > 2 && !t.toLowerCase().includes('ingredient')) {
                                ingredients.push(t);
                            }
                        }
                        if (ingredients.length > 0) break;
                    }
                }
            }
        }

        return { 
            recipe_name, 
            rating, 
            number_of_reviews, 
            total_time,
            prep_time,
            cook_time,
            ingredients 
        };
        }
        """)
        
        return result    
    async def _google_flights_origin(self, city: str) -> ActionResult:
        try:
            if not self.google_flights_handler:
                return ActionResult(action="google_flights_origin", success=False, error="Google Flights handler not initialized")
            
            success = await self.google_flights_handler.fill_origin(city)
            
            if success:
                return ActionResult(action="google_flights_origin", success=True)
            else:
                return ActionResult(action="google_flights_origin", success=False, error="Failed to fill origin")
        except Exception as e:
            return ActionResult(action="google_flights_origin", success=False, error=str(e))
    
    async def _google_flights_destination(self, city: str) -> ActionResult:
        try:
            if not self.google_flights_handler:
                return ActionResult(action="google_flights_destination", success=False, error="Google Flights handler not initialized")
            
            success = await self.google_flights_handler.fill_destination(city)
            
            if success:
                return ActionResult(action="google_flights_destination", success=True)
            else:
                return ActionResult(action="google_flights_destination", success=False, error="Failed to fill destination")
        except Exception as e:
            return ActionResult(action="google_flights_destination", success=False, error=str(e))
    
    async def _google_flights_search(self) -> ActionResult:
        try:
            if not self.google_flights_handler:
                return ActionResult(action="google_flights_search", success=False, error="Google Flights handler not initialized")
            
            success = await self.google_flights_handler.search_flights()
            
            if success:
                return ActionResult(action="google_flights_search", success=True)
            else:
                return ActionResult(action="google_flights_search", success=False, error="Failed to search flights")
        except Exception as e:
            return ActionResult(action="google_flights_search", success=False, error=str(e))
    
    async def _google_flights_class(self, class_type: str) -> ActionResult:
        try:
            if not self.google_flights_handler:
                return ActionResult(action="google_flights_class", success=False, error="Google Flights handler not initialized")
            
            success = await self.google_flights_handler.select_class(class_type)
            
            if success:
                return ActionResult(action="google_flights_class", success=True)
            else:
                return ActionResult(action="google_flights_class", success=False, error=f"Failed to select class: {class_type}")
        except Exception as e:
            return ActionResult(action="google_flights_class", success=False, error=str(e))
    
    async def _google_maps_search(self, query: str) -> ActionResult:
        try:
            if not self.google_maps_handler:
                return ActionResult(action="google_maps_search", success=False, error="Google Maps handler not initialized")
            
            success = await self.google_maps_handler.search_location(query)
            
            if success:
                return ActionResult(action="google_maps_search", success=True)
            else:
                return ActionResult(action="google_maps_search", success=False, error="Failed to search location")
        except Exception as e:
            return ActionResult(action="google_maps_search", success=False, error=str(e))
    
    async def _google_maps_directions(self, from_location: str, to_location: str) -> ActionResult:
        try:
            if not self.google_maps_handler:
                return ActionResult(action="google_maps_directions", success=False, error="Google Maps handler not initialized")
            
            success = await self.google_maps_handler.get_directions(from_location, to_location)
            
            if success:
                return ActionResult(action="google_maps_directions", success=True)
            else:
                return ActionResult(action="google_maps_directions", success=False, error="Failed to get directions")
        except Exception as e:
            return ActionResult(action="google_maps_directions", success=False, error=str(e))
    
    async def _extract_google_maps_info(self) -> ActionResult:
        try:
            if not self.google_maps_handler:
                return ActionResult(action="extract", success=False, error="Google Maps handler not initialized")
            
            info = await self.google_maps_handler.extract_place_info()
            
            if info:
                import json
                content = json.dumps(info, ensure_ascii=False, indent=2)
                self.last_extraction_content = content
                return ActionResult(action="extract", success=True, extracted_content=content)
            else:
                return ActionResult(action="extract", success=False, error="No place information found")
        except Exception as e:
            return ActionResult(action="extract", success=False, error=str(e))