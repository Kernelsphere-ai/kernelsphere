import asyncio
import logging
from typing import Optional, List, Dict, Any
from playwright.async_api import Page
import re
from typing import Dict, Optional
import time


from models import (
    AgentHistory, StepResult, ActionResult, ProgressState, TaskCompletion, AgentDecision
)
from llm import GeminiLLM
from dom_service import DOMService
from actions import ActionExecutor
from prompts import (
    SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
    build_observation_message,
    build_error_recovery_message,
    build_final_extraction_prompt,
    build_stagnation_recovery_message 
)
from stealth import auto_handle_popups, wait_for_cloudflare 
from captcha_handler import CaptchaHandler
from google_flights_automation import GoogleFlightsAutomation, FlightSearchParams
from booking_automation import BookingAutomation
from google_maps_automation import GoogleMapsAutomation

from constraint_parser import ConstraintParser, Constraint
from universal_smart_query_builder import UniversalSmartQueryBuilder
from universal_result_validator import UniversalResultValidator
from universal_multi_strategy_extractor import UniversalMultiStrategyExtractor
from universal_filter_sort_handler import UniversalFilterSortHandler
from improved_extraction import ProgressiveExtractionManager, ImprovedExtractionValidator
from vision_element_locator import VisionElementLocator
from vision_input_handler import VisionInputHandler
from text_normalization import normalize_text, safe_json_dumps, safe_json_loads

from login_state_detector import LoginStateDetector
logger = logging.getLogger(__name__)


class WebAutomationAgent:
    """
    Main web automation agent with integrated advanced features
    """
    
    def __init__(
        self,
        page: Page,
        llm: GeminiLLM,
        max_steps: int = 30,
        max_consecutive_errors: int = 3,
        save_screenshots: bool = False,
        screenshot_dir: str = "screenshots",
        captcha_api_key: Optional[str] = None,
        manual_captcha: bool = True,
        captcha_max_wait: int = 120,
        task_logger = None,
        email_handler = None
    ):
        """
        Initialize agent with all features
        
        Args:
            page: Playwright page object
            llm: Gemini LLM instance
            max_steps: Maximum steps to take
            max_consecutive_errors: Max errors before giving up
            save_screenshots: Whether to save screenshots at each step
            screenshot_dir: Directory to save screenshots
            captcha_api_key: 2Captcha API key (optional, None = manual only)
            manual_captcha: Whether to allow manual CAPTCHA solving
            captcha_max_wait: Maximum seconds to wait for CAPTCHA solving
        """
        self.page = page
        self.llm = llm
        self.max_steps = max_steps
        self.max_consecutive_errors = max_consecutive_errors
        self.save_screenshots = save_screenshots
        self.screenshot_dir = screenshot_dir
        self.task_logger = task_logger
        self.email_handler = email_handler
        
        # Initialize services
        self.dom_service = DOMService()
        
        # Initialize vision features
        self.vision_locator = VisionElementLocator(page, llm)
        self.vision_input = VisionInputHandler(page, llm)
        
        # Initialize action executor with vision features
        self.action_executor = ActionExecutor(
            page, 
            self.dom_service, 
            llm,
            vision_locator=self.vision_locator,
            vision_input_handler=self.vision_input
        )
        
        # Initialize CAPTCHA handler
        self.captcha_handler = CaptchaHandler(
            api_key=captcha_api_key,
            manual_mode=manual_captcha
        )
        self.captcha_max_wait = captcha_max_wait
        
        # State tracking
        self.conversation_history: List[str] = []
        self.consecutive_errors = 0
        self.last_errors: List[str] = []
        self.screenshot_paths: List[str] = []
        
        # Progress tracking
        self.progress_state = ProgressState()
        self.wait_count = 0  # Track total wait actions
        self.extraction_attempts = []
        self.extraction_interval = 5
        
        # Query system components
        self.query_builder = UniversalSmartQueryBuilder
        self.result_validator = UniversalResultValidator
        self.multi_strategy_extractor = None
        self.filter_handler = None 
        
        # Extraction manager
        self.extraction_manager = None
        self.extraction_validator = ImprovedExtractionValidator()
        
        # Task-specific data
        self.current_task = None
        self.search_query = None
        self.constraints = []
        
        self.google_flights = GoogleFlightsAutomation(page)
        self.booking_automation = BookingAutomation(page)
        self.google_maps = GoogleMapsAutomation(page)
        self.max_wait_count = 5  # Max allowed wait actions
        
        self.logger = logging.getLogger(__name__)
        
        # Login state tracking
        self.login_detector = LoginStateDetector()
        
        # Form field tracking for multi-step forms
        self.filled_fields = {}
        
        # Create screenshot directory if needed
        if self.save_screenshots:
            import os
            os.makedirs(self.screenshot_dir, exist_ok=True)
        
        # Log configuration
        if captcha_api_key:
            self.logger.info("CAPTCHA handler initialized with API key (auto-solving enabled)")
        elif manual_captcha:
            self.logger.info(" CAPTCHA handler initialized in manual mode (will pause for user)")
        else:
            self.logger.info(" CAPTCHA handler disabled (tasks may fail on protected sites)")
    
    async def check_and_handle_captcha(self, force_check: bool = False) -> bool:
        """
        Check for CAPTCHAs and handle them if found
        
        Args:
            force_check: If True, try to solve even if no specific type detected
        
        Returns:
            True if no CAPTCHAs or all solved successfully, False otherwise
        """
        try:
            # Detect CAPTCHAs
            captcha_types = await self.captcha_handler.detect_captcha_type(self.page)
            
            if not captcha_types:
                self.logger.debug("No CAPTCHAs detected")
                return True  # No CAPTCHAs found
            
            self.logger.warning(f" CAPTCHA detected: {', '.join(captcha_types)}")
            
            # Handle CAPTCHAs
            success, messages = await self.captcha_handler.handle_captchas(
                self.page,
                max_wait=self.captcha_max_wait
            )
            
            # Log results
            for msg in messages:
                if success:
                    self.logger.info(f"Success {msg}")
                else:
                    self.logger.error(f"Failed {msg}")
            
            # Mark as cleared in DOM service if successful
            if success:
                self.dom_service.mark_captcha_cleared()
            
            return success
            
        except Exception as e:
            self.logger.error(f"CAPTCHA handling error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    async def run(self, task: str, start_url: str) -> AgentHistory:
        """
        Run agent to complete task with all integrated features
        
        Args:
            task: Task description
            start_url: Starting URL
            
        Returns:
            AgentHistory with complete execution record
        """
        self.logger.info(f"Starting task: {task}")
        self.logger.info(f"Start URL: {start_url}")
        
        self.action_executor.set_task(task)
        self.task = task
        self.current_task = task
        
        #  Parse task for constraints
        self.logger.info("\n Parsing task constraints...")
        self.search_query, self.constraints = self.query_builder.build_query_and_constraints(task)
        
        if self.constraints:
            summary = ConstraintParser.get_constraint_summary(self.constraints)
            self.logger.info(f" Parsed constraints: {summary}")
            self.logger.info(f" Core search query: '{self.search_query}'")
            
            # Share constraints with action executor
            self.action_executor.set_constraints(self.constraints)
        else:
            self.logger.info(" No constraints found in task")
        
        #  Initialize progressive extraction manager
        self.extraction_manager = ProgressiveExtractionManager(
            task=task,
            extraction_interval=5
        )
        self.logger.info(" Progressive extraction manager initialized")
        
        steps: List[StepResult] = []
        success = False
        final_answer = ""
        
        try:
            # Navigate to start URL
            self.logger.info("Navigating to start URL...")
            nav_result = await self.action_executor.execute("navigate", url=start_url)
            
            if not nav_result.success:
                self.logger.error(f"Failed to navigate to start URL: {nav_result.error}")
                return self._build_failure_history(task, start_url, "Failed to load start URL")
            
            # Auto-handle popups after initial load
            try:
                await auto_handle_popups(self.page)
            except Exception as e:
                self.logger.warning(f"Error in initial popup handling: {e}")
            
            # Handle Cloudflare and mark as completed
            try:
                cloudflare_result = await wait_for_cloudflare(self.page)
                if cloudflare_result:
                    self.captcha_handler.mark_cloudflare_completed()
                    self.dom_service.mark_cloudflare_cleared()
                    self.logger.info(" Cloudflare challenge auto-completed")
            except Exception as e:
                self.logger.debug(f"Error in Cloudflare handling: {e}")
            
            # Wait for page to stabilize
            await asyncio.sleep(2)
            
            # Check for CAPTCHAs on initial page load
            self.logger.info("Checking for CAPTCHAs on initial page...")
            
            try:
                dom_state = await self.dom_service.extract_dom_state(self.page)
                force_check = dom_state.has_captcha
                
                if force_check:
                    self.logger.warning(" Stealth detected CAPTCHA indicators on page")
                
                if not await self.check_and_handle_captcha(force_check=force_check):
                    self.logger.warning(" CAPTCHA detected on start page but not solved")
            except Exception as e:
                self.logger.error(f"Error in initial CAPTCHA check: {e}")
            
            # Main execution loop
            for step_num in range(1, self.max_steps + 1):
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"STEP {step_num}/{self.max_steps}")
                self.logger.info(f"{'='*60}")
                
                # Execute step with all integrated features
                step_result = await self._execute_step(task, step_num)
                steps.append(step_result)
                
                # Check if done
                if step_result.actions:
                    last_action = step_result.actions[-1]
                    
                    if last_action.action == "done":
                        final_answer_preview = last_action.extracted_content or ""
                        
                        # Validate task completion BEFORE accepting done
                        task_completion = TaskCompletion(
                            task_description=task, 
                            extracted_content=final_answer_preview
                        )
                        
                        is_task_complete = task_completion.is_complete()
                        
                        # Additional validation for multi-step tasks
                        task_lower = task.lower()
                        multi_step_keywords = [' and ', ' then ', ' after ', 'first', 'next', 'finally', 'also']
                        appears_multi_step = any(keyword in task_lower for keyword in multi_step_keywords)
                        
                        # Check reasoning quality
                        reasoning = getattr(last_action, 'reasoning', "") or ""
                        reasoning_mentions_complete = any(
                            phrase in reasoning.lower() 
                            for phrase in ['task complete', 'all steps', 'everything', 'all parts', 'fully complete', 'both', 'finished all']
                        )
                        
                        # Decide whether to accept done
                        should_accept_done = False
                        
                        if is_task_complete:
                            should_accept_done = True
                        elif appears_multi_step and not reasoning_mentions_complete and step_num < 15:
                            self.logger.warning("="*60)
                            self.logger.warning(" REJECTED PREMATURE DONE")
                            self.logger.warning(f"Task appears to have multiple steps but done called at step {step_num}")
                            self.logger.warning(f"Task: {task}")
                            self.logger.warning(f"Reasoning: {reasoning}")
                            self.logger.warning("Continuing task execution...")
                            self.logger.warning("="*60)
                            should_accept_done = False
                        elif step_num < 5:
                            self.logger.warning("="*60)
                            self.logger.warning(" REJECTED PREMATURE DONE")
                            self.logger.warning(f"Done called too early at step {step_num}")
                            self.logger.warning(f"Task: {task}")
                            self.logger.warning(f"Reasoning: {reasoning}")
                            self.logger.warning("Continuing task execution...")
                            self.logger.warning("="*60)
                            should_accept_done = False
                        else:
                            should_accept_done = True
                        
                        if should_accept_done:
                            success = last_action.success
                            final_answer = final_answer_preview
                            self.logger.info(f" Task completed: success={success}")
                            break
                        else:
                            self.logger.info("Ignoring premature done action, continuing...")
                            # Remove the done action from results
                            step_result.actions = [a for a in step_result.actions if a.action != "done"]
                
                #  Enhanced progressive extraction with multi-strategy
                if step_num % self.extraction_interval == 0 and step_num < self.max_steps:
                    task_intent = self._extract_task_intent(task)
                    if task_intent['is_write_task']:
                        continue
                    
                    self.logger.info(f"Attempting progressive extraction at step {step_num}")
                    
                    if await self._try_progressive_extraction(step_num):
                        # Check if we can finish early
                        should_finish, best_content = self.extraction_manager.should_finish_early()
                        if should_finish:
                            if self._is_list_page(self.page.url):
                                self.logger.warning("="*60)
                                self.logger.warning("REJECTED EARLY FINISH - Still on list/search page")
                                self.logger.warning(f"URL: {self.page.url}")
                                self.logger.warning("="*60)
                                continue
                            
                            if not self._is_detail_page(self.page.url, task):
                                best_depth = getattr(self.extraction_attempts[-1], 'depth_score', 0) if self.extraction_attempts else 0
                                if best_depth < 0.6:
                                    self.logger.warning("="*60)
                                    self.logger.warning("REJECTED EARLY FINISH - Not enough data depth")
                                    self.logger.warning(f"URL: {self.page.url}")
                                    self.logger.warning(f"Depth score: {best_depth:.2f} (need 0.6+)")
                                    self.logger.warning("="*60)
                                    continue
                            
                            if self._should_continue_searching(
                                self.extraction_manager.attempts[-1].confidence if self.extraction_manager.attempts else 0,
                                step_num,
                                task
                            ):
                                self.logger.warning("="*60)
                                self.logger.warning("REJECTED EARLY FINISH - Should continue searching")
                                self.logger.warning("="*60)
                                continue
                            
                            task_lower = task.lower()
                            multi_step_keywords = [' and ', ' then ', ' after ', 'first', 'next', 'finally', 'also']
                            appears_multi_step = any(keyword in task_lower for keyword in multi_step_keywords)
                            
                            should_allow_early_finish = False
                            
                            if not appears_multi_step:
                                should_allow_early_finish = True
                            elif step_num >= 10:
                                should_allow_early_finish = True
                            else:
                                task_completion = TaskCompletion(
                                    task_description=task, 
                                    extracted_content=best_content
                                )
                                should_allow_early_finish = task_completion.is_complete()
                            
                            if should_allow_early_finish:
                                self.logger.info("="*60)
                                self.logger.info("EARLY FINISH - High confidence extraction")
                                self.logger.info("="*60)
                                
                                summary = self.extraction_manager.get_extraction_summary()
                                self.logger.info(f"Best extraction from step {summary['best_step']}")
                                self.logger.info(f"Confidence: {summary['best_confidence']:.2f}")
                                self.logger.info(f"Depth score: {summary.get('depth_score', 0):.2f}")
                                
                                final_answer = best_content
                                success = True
                                break
                            else:
                                self.logger.warning("="*60)
                                self.logger.warning("REJECTED EARLY FINISH - Multi-step task incomplete")
                                self.logger.warning(f"Continuing at step {step_num}")
                                self.logger.warning("="*60)
                
                # Handle stagnation
                if self.progress_state.stagnation_detected:
                    reason = self.progress_state.get_stagnation_reason()
                    self.logger.warning(f" STAGNATION DETECTED: {reason}")
                    
                    recovery_successful = await self._handle_stagnation(task, reason)
                    
                    self.progress_state.stagnation_detected = False
                    
                    if recovery_successful:
                        self.consecutive_errors = 0
                        self.logger.info("Stagnation recovery successful, continuing")
                    else:
                        self.consecutive_errors = min(self.consecutive_errors, 1)
                        self.logger.warning(f"Stagnation recovery had issues, giving agent another chance (errors={self.consecutive_errors})")
                
                if self.consecutive_errors >= self.max_consecutive_errors:
                    self.logger.error("Too many consecutive errors, attempting final extraction")
                    final_answer = await self._final_extraction(task)
                    break
                
                # Check wait limit
                if self.wait_count >= self.max_wait_count:
                    self.logger.warning(f" Wait limit reached ({self.wait_count} times), forcing action")
                
                # Brief pause between steps
                await asyncio.sleep(1)
            
            # Final extraction if not done yet
            if not success and not final_answer:
                self.logger.info("Max steps reached, attempting final extraction")
                final_answer = await self._final_extraction(task)
            
            # Validate task completion
            task_completion = TaskCompletion(task_description=task, extracted_content=final_answer)
            if task_completion.is_complete():
                success = True
            else:
                self.logger.warning(" Extracted content may not fully answer the task")
            
        except Exception as e:
            self.logger.error(f"Agent error: {e}")
            final_answer = f"Error: {str(e)}"
        
        # Build final history
        final_url = self.page.url
        final_title = await self.page.title()
        
        history = AgentHistory(
            task=task,
            start_url=start_url,
            success=success,
            final={
                "final_answer": final_answer,
                "page_url": final_url,
                "page_title": final_title
            },
            steps=steps,
            total_steps=len(steps)
        )
        
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"Task completed: {success}")
        self.logger.info(f"Total steps: {len(steps)}")
        self.logger.info(f"Final answer: {final_answer[:100]}...")
        self.logger.info(f"{'='*60}")
        
        return history
    
    async def _try_progressive_extraction(self, step_num: int) -> bool:
        """
        Generalized progressive extraction that works for any task/website.
        """
        try:
            if not self.multi_strategy_extractor:
                self.multi_strategy_extractor = UniversalMultiStrategyExtractor(self.page)
            
            extraction_result = await self.multi_strategy_extractor.extract_data(
                self.current_task,
                max_items=10
            )
            
            if extraction_result and extraction_result.get('success'):
                items = extraction_result.get('items', [])
                
                if items:
                    is_valid, validation_reason = self._validate_extraction_completeness(items, self.current_task)
                    if not is_valid:
                        self.logger.warning(f"Extraction validation failed: {validation_reason}")
                        return False
                    
                    depth_score = self._calculate_data_depth_score(items)
                    
                    if self._is_list_page(self.page.url) and depth_score < 0.4:
                        self.logger.warning(f"On list page with shallow data (depth: {depth_score:.2f})")
                        return False
                    
                    if not self._is_detail_page(self.page.url, self.current_task) and depth_score < 0.5:
                        self.logger.warning("Not on detail page and data not deep enough")
                        return False
                    
                    if self.constraints:
                        valid_items, validation_summary = self.result_validator.validate_results(
                            items,
                            self.constraints
                        )
                        
                        if valid_items:
                            self.logger.info(f"Validation: {validation_summary['passed']}/{validation_summary['passed'] + validation_summary['failed']} items passed")
                            
                            content = safe_json_dumps({
                                'items': valid_items,
                                'strategy': extraction_result.get('strategy'),
                                'validation': validation_summary,
                                'depth_score': depth_score
                            })
                        else:
                            self.logger.info("No items passed constraint validation")
                            return False
                    else:
                        content = safe_json_dumps({
                            **extraction_result,
                            'depth_score': depth_score
                        })
                    
                    attempt = self.extraction_manager.record_extraction(
                        step=step_num,
                        content=content,
                        url=self.page.url,
                        page_title=await self.page.title()
                    )
                    
                    if attempt:
                        self.logger.info(
                            f"Extraction recorded: confidence={attempt.confidence:.2f}, "
                            f"depth={attempt.depth_score:.2f}, keywords={attempt.keywords_matched}"
                        )
                        
                        self.extraction_attempts.append({
                            'step': step_num,
                            'content': content,
                            'confidence': attempt.confidence,
                            'depth_score': attempt.depth_score
                        })
                        
                        return True
            
            return False
            
        except Exception as e:
            self.logger.debug(f"Progressive extraction error: {e}")
            return False
    
    async def _execute_step(self, task: str, step_num: int) -> StepResult:
        action_results: List[ActionResult] = []
        
        url_before = self.page.url
        
        try:
            if self.save_screenshots:
                screenshot_path = f"{self.screenshot_dir}/screenshot{step_num - 1}.png"
                await self.page.screenshot(path=screenshot_path, full_page=False)
                self.screenshot_paths.append(screenshot_path)
                self.logger.info(f"Screenshot saved: {screenshot_path}")
            
            self.logger.info(" Observing page state...")
            dom_state = await self.dom_service.extract_dom_state(self.page)
            dom_hash_before = dom_state.get_dom_hash()
            
            is_otp_screen = False
            otp_field_index = None
            
            page_text_lower = (dom_state.text_content or "").lower()
            otp_keywords = [
                "verification code", "enter code", "enter the code", 
                "one-time code", "otp", "email code", "check your email",
                "sent to your email", "code sent", "authentication code",
                "verify your email", "confirm your email"
            ]
            
            if any(keyword in page_text_lower for keyword in otp_keywords):
                self.logger.info("OTP screen detected")
                
                for idx, elem in enumerate(dom_state.elements):
                    elem_str = str(elem).lower()
                    input_types = ["text", "tel", "number"]
                    is_input = any(t in elem_str for t in ["<input", "input"])
                    has_code_attr = any(k in elem_str for k in ["code", "otp", "verification", "auth", "totp"])
                    
                    if is_input and (has_code_attr or any(t in elem_str for t in input_types)):
                        otp_field_index = idx
                        is_otp_screen = True
                        self.logger.info(f"Found OTP input field at index {idx}")
                        break
            
            if is_otp_screen and otp_field_index is not None:
                self.logger.info("OTP screen detected - pausing for manual entry")
                self.logger.info("="*60)
                self.logger.info("MANUAL OTP ENTRY REQUIRED")
                self.logger.info("="*60)
                self.logger.info("Please enter the OTP code manually in the browser")
                self.logger.info("Waiting for OTP submission...")
                
                url_before_otp = self.page.url
                
                result = await self.action_executor.execute(
                    "wait_for_manual_otp",
                    initial_url=url_before_otp,
                    timeout=300
                )
                
                if result.success:
                    self.logger.info("OTP manually entered and submitted successfully")
                    
                    await asyncio.sleep(2)
                    
                    try:
                        dom_state_after = await self.dom_service.extract_dom_state(self.page)
                        dom_hash_after = dom_state_after.get_dom_hash()
                    except:
                        dom_hash_after = dom_hash_before
                    
                    step_result = StepResult(
                        step=step_num,
                        url=self.page.url,
                        title=await self.page.title(),
                        actions=[result],
                        dom_hash=dom_hash_after
                    )
                    
                    return step_result
                else:
                    self.logger.error(f"OTP manual entry timeout: {result.error}")
                    
                    return StepResult(
                        step=step_num,
                        url=self.page.url,
                        title=await self.page.title(),
                        actions=[ActionResult(
                            action="wait_for_manual_otp",
                            success=False,
                            error=f"OTP manual entry timeout. Please enter OTP and submit."
                        )],
                        dom_hash=dom_hash_before
                    )
            
            elements_text = self.dom_service.format_elements_for_llm(dom_state.elements)
            
            previous_action = None
            error_message = None
            state_change_note = None
            
            if self.conversation_history:
                last_entry = self.conversation_history[-1]
                if "Action:" in last_entry:
                    previous_action = last_entry.split("Action:")[1].split("\n")[0].strip()
                if "Error:" in last_entry:
                    error_message = last_entry.split("Error:")[1].strip()
                if "State changed:" in last_entry:
                    state_change_note = "Previous action changed page state"
                elif "No state change" in last_entry:
                    state_change_note = "Previous action did NOT change page state - try different approach"
            
            last_extracted_content = None
            if self.conversation_history:
                last_entry = self.conversation_history[-1]
                if "Extracted content:" in last_entry:
                    try:
                        content_start = last_entry.index("Extracted content:") + len("Extracted content:")
                        last_extracted_content = last_entry[content_start:].strip()
                    except:
                        pass
            
            stagnation_warning = None
            if self.progress_state.stagnation_detected:
                stagnation_warning = self.progress_state.get_stagnation_reason()
            
            repeated_extraction_warning = None
            if previous_action == "extract" and len(self.conversation_history) >= 2:
                identical_count = getattr(self, 'identical_extraction_count', 0)
                
                perfect_match_count = 0
                if hasattr(self, 'last_extraction_content') and self.last_extraction_content:
                    try:
                        import json
                        data = json.loads(self.last_extraction_content)
                        if isinstance(data, dict) and "items" in data and "match_quality" in data:
                            items = data.get("items", [])
                            quality = data.get("match_quality", "")
                            if quality == "perfect" and len(items) > 0:
                                perfect_match_count = len(items)
                    except:
                        pass
                
                if perfect_match_count > 0:
                    repeated_extraction_warning = f" STOP - You already found {perfect_match_count} items matching ALL constraints in your last extraction!\nCall 'done' immediately with these results. Do NOT extract again."
                elif identical_count >= 2:
                    repeated_extraction_warning = f" STOP EXTRACTING - You've extracted {identical_count} times with IDENTICAL results!\nYou already have the complete data. Call 'done' with the extracted content now."
                elif identical_count == 1:
                    repeated_extraction_warning = " WARNING: Last extraction returned identical data. You likely have all available data already."
            
            if repeated_extraction_warning:
                stagnation_warning = repeated_extraction_warning + ("\n" + stagnation_warning if stagnation_warning else "")
            
            force_done = False
            if previous_action == "extract" and hasattr(self, 'last_extraction_content') and self.last_extraction_content:
                try:
                    import json
                    data = json.loads(self.last_extraction_content)
                    if isinstance(data, dict) and data.get("match_quality") == "perfect":
                        items = data.get("items", [])
                        if len(items) > 0:
                            force_done = True
                            self.logger.warning(f"FORCING DONE: Perfect match found with {len(items)} items")
                            
                            decision = AgentDecision(
                                action="done",
                                reasoning=f"Previous extraction found {len(items)} items matching ALL requirements. Task complete.",
                                confidence=1.0,
                                success=True
                            )
                            
                            self.logger.info(f"Decision: done (forced after perfect match)")
                            self.logger.info(f" Reasoning: {decision.reasoning}")
                            
                            result = await self.action_executor.execute(
                                decision.action,
                                success=True
                            )
                            
                            history_entry = f"\nStep {step_num}\n"
                            history_entry += f"Action: done\n"
                            history_entry += f"Reasoning: {decision.reasoning}\n"
                            history_entry += "Result: Task completed successfully\n"
                            
                            if result.extracted_content:
                                history_entry += f"Final result:\n{result.extracted_content}\n"
                            
                            self.conversation_history.append(history_entry)
                            
                            step_result = StepResult(
                                step=step_num,
                                url=self.page.url,
                                title=await self.page.title(),
                                actions=[result],
                                dom_hash=dom_hash_before
                            )
                            return step_result
                except:
                    pass
            
            if force_done:
                pass
            
            login_status_note = None
            if self.login_detector.state.is_logged_in:
                login_status_note = f" LOGIN COMPLETE (step {self.login_detector.state.login_completed_at_step}). You are now logged in. Focus on completing the main task."
            
            observation = build_observation_message(
                task=task,
                current_url=dom_state.url,
                current_title=dom_state.title,
                elements_text=elements_text,
                page_text=dom_state.text_content,
                step_number=step_num,
                has_cookie_popup=dom_state.has_cookie_popup,
                has_cloudflare=dom_state.has_cloudflare,
                has_captcha=dom_state.has_captcha,
                previous_action=previous_action,
                error_message=error_message,
                state_change_note=state_change_note,
                stagnation_warning=stagnation_warning,
                wait_count=self.wait_count,
                max_wait_count=self.max_wait_count,
                extracted_content=last_extracted_content
            )
            
            if login_status_note:
                observation = observation + f"\n\nIMPORTANT: {login_status_note}"
            
            if self.filled_fields and len(self.filled_fields) > 0:
                filled_summary = "\n\nFORM PROGRESS - Already filled fields (do NOT fill these again):\n"
                for field_key, value in self.filled_fields.items():
                    element_num = field_key.replace('field_', '')
                    filled_summary += f"  Element {element_num}: {value}...\n"
                filled_summary += "Move to the NEXT unfilled field in the form.\n"
                observation = observation + filled_summary
            
            screenshot = await self.page.screenshot()
            
            is_logged_in = self.login_detector.detect_login_state(
                page_text=dom_state.text_content or "",
                url=dom_state.url,
                elements=dom_state.elements,
                step_num=step_num
            )
            self.logger.info(" Getting decision from LLM...")
            decision = self.llm.decide_action(
                system_prompt=SYSTEM_PROMPT,
                user_message=observation,
                conversation_history=self.conversation_history[-5:],
                screenshot=screenshot
            )
            
            self.logger.info(f"Action: {decision.action}")
            self.logger.info(f"Reasoning: {decision.reasoning[:150]}...")
            
            if decision.action == "wait" and self.wait_count >= self.max_wait_count:
                self.logger.warning(" Wait limit reached, forcing scroll instead of wait")
                decision.action = "scroll"
                if hasattr(decision, "direction") and decision.direction is None:
                    decision.direction = "down"
                if hasattr(decision, "amount") and decision.amount is None:
                    decision.amount = 800
            
            should_block, block_reason = self.login_detector.should_prevent_login_action(
                action=decision.action,
                reasoning=decision.reasoning or "",
                step_num=step_num
            )
            
            if should_block:
                self.logger.warning(f" Replacing blocked login action with search/extract")
                
                from models import AgentDecision
                decision = AgentDecision(
                    action="scroll",
                    reasoning=f"Login already completed. Focusing on task instead. Original blocked reason: {block_reason}",
                    direction="down",
                    amount=500
                )
                self.logger.info(f"New action: {decision.action}")
                self.logger.info(f"New reasoning: {decision.reasoning}")
            
            action_kwargs = decision.model_dump(exclude={'reasoning', 'action'}, exclude_none=True)
            result = await self.action_executor.execute(decision.action, **action_kwargs)
            
            if decision.action == "wait":
                self.wait_count += 1
            
            url_after = self.page.url
            await asyncio.sleep(1)
            
            try:
                dom_state_after = await self.dom_service.extract_dom_state(self.page)
                dom_hash_after = dom_state_after.get_dom_hash()
            except:
                dom_hash_after = dom_hash_before
            
            state_changed = (url_after != url_before) or (dom_hash_after != dom_hash_before)
            result.state_changed = state_changed
            
            element_index = decision.index if hasattr(decision, 'index') else None
            self.progress_state.update(
                url=url_after,
                dom_hash=dom_hash_after,
                action=decision.action,
                element_index=element_index
            )
            
            action_results.append(result)
            
            history_entry = f"Action: {decision.action}\n"
            if result.success:
                history_entry += "Result: Success\n"
                if state_changed:
                    history_entry += "State changed: YES\n"
                
                if decision.action == "input_text" and hasattr(decision, 'index'):
                    field_key = f"field_{decision.index}"
                    text_value = getattr(decision, 'text', '')[:50]
                    self.filled_fields[field_key] = text_value
                    history_entry += f"FIELD FILLED: Element {decision.index} now contains text. Do NOT fill this element again.\n"
                
                if decision.action == "extract" and result.extracted_content:
                    self._store_successful_extraction(result.extracted_content, step_num)
                    
                    extraction_goal = decision.extraction_goal if hasattr(decision, 'extraction_goal') else ""
                    
                    if not hasattr(self, 'last_extraction_goal'):
                        self.last_extraction_goal = None
                    
                    goal_changed = False
                    if self.last_extraction_goal and extraction_goal:
                        last_words = set(self.last_extraction_goal.lower().split())
                        current_words = set(extraction_goal.lower().split())
                        common_words = last_words & current_words
                        
                        if len(common_words) < min(3, len(last_words) // 2):
                            goal_changed = True
                            self.logger.warning(f"Extraction goal changed significantly: '{self.last_extraction_goal}' -> '{extraction_goal}'")
                    
                    self.last_extraction_goal = extraction_goal
                    
                    try:
                        import json
                        data = json.loads(result.extracted_content)
                        
                        if isinstance(data, dict) and "items" in data and "match_quality" in data:
                            items = data.get("items", [])
                            quality = data.get("match_quality", "unknown")
                            note = data.get("note", "")
                            
                            preview = f"CONSTRAINT EXTRACTION COMPLETE\n"
                            preview += f"Match Quality: {quality.upper()}\n"
                            preview += f"Found {len(items)} items:\n"
                            
                            for i, item in enumerate(items[:3], 1):
                                if isinstance(item, dict):
                                    name = item.get("name") or item.get("title") or item.get("recipe_name") or "Unnamed"
                                    rating = item.get("rating", "")
                                    reviews = item.get("reviews", "")
                                    match_reason = item.get("match_reason", "")
                                    
                                    item_summary = f"  {i}. {name}"
                                    if rating:
                                        item_summary += f" ({rating}"
                                        if reviews:
                                            item_summary += f", {reviews} reviews"
                                        item_summary += ")"
                                    preview += item_summary + "\n"
                                    
                                    if match_reason:
                                        reason_short = match_reason[:120] + "..." if len(match_reason) > 120 else match_reason
                                        preview += f"     Why: {reason_short}\n"
                                else:
                                    preview += f"  {i}. {str(item)[:50]}\n"
                            
                            if len(items) > 3:
                                preview += f"  ... and {len(items)-3} more items\n"
                            
                            if note:
                                note_short = note[:150] + "..." if len(note) > 150 else note
                                preview += f"\nNote: {note_short}\n"
                            
                            if quality == "perfect" and len(items) > 0:
                                preview += f"\nSUCCESS: Found {len(items)} items matching ALL requirements. Call 'done' now."
                            elif quality == "none":
                                preview += f"\nNo matches found. Try different search or relax requirements."
                        
                        elif isinstance(data, dict):
                            populated = []
                            empty = []
                            field_summary = []
                            
                            for key, value in data.items():
                                if value is None or value == "" or (isinstance(value, list) and len(value) == 0):
                                    empty.append(key)
                                else:
                                    populated.append(key)
                                    
                                    if isinstance(value, list):
                                        field_summary.append(f"{key}: {len(value)} items")
                                    elif isinstance(value, str) and len(value) > 50:
                                        field_summary.append(f"{key}: {value[:47]}...")
                                    else:
                                        field_summary.append(f"{key}: {value}")
                            
                            preview = f"EXTRACTION COMPLETE - {len(populated)}/{len(data)} fields populated:\n"
                            for summary in field_summary[:8]:
                                preview += f"  {summary}\n"
                            if len(field_summary) > 8:
                                preview += f"  (+{len(field_summary)-8} more fields)\n"
                            if empty:
                                preview += f"Missing: {', '.join(empty[:3])}"
                        else:
                            preview = result.extracted_content[:300]
                    except:
                        preview = result.extracted_content[:300]
                    
                    if not hasattr(self, 'last_extraction_content'):
                        self.last_extraction_content = None
                    
                    warning_prefix = ""
                    if goal_changed:
                        warning_prefix = "WARNING: Extraction goal changed from original task! Refocus on original goal.\n\n"
                    
                    if self.last_extraction_content == result.extracted_content:
                        preview = warning_prefix + " IDENTICAL EXTRACTION - Same data as last extraction!\n" + preview
                        self.identical_extraction_count = getattr(self, 'identical_extraction_count', 0) + 1
                    else:
                        if warning_prefix:
                            preview = warning_prefix + preview
                        self.identical_extraction_count = 0
                    
                    self.last_extraction_content = result.extracted_content
                    
                    history_entry += f"Extracted content:\n{preview}\n"
                    self.logger.info(f"Extraction result: {result.extracted_content[:200]}...")
                
                elif result.extracted_content:
                    history_entry += f"Extracted content:\n{result.extracted_content[:300]}\n"
            else:
                history_entry += f"Result: Failed\n"
                if result.error:
                    history_entry += f"Error: {result.error}\n"
                    self.consecutive_errors += 1
                else:
                    self.consecutive_errors = 0
            
            self.conversation_history.append(history_entry)
            
            if not state_changed:
                self.logger.warning(" No state change detected")
            
            if result.success:
                self.consecutive_errors = 0
                
                try:
                    if result.action in ["click_element", "input_text", "search", "navigate"]:
                        try:
                            await wait_for_cloudflare(self.page)
                            self.logger.info("Cloudflare challenge auto-completed after navigation")
                        except Exception as e:
                            self.logger.debug(f"Cloudflare check after navigation: {e}")
                        
                        self.logger.info("Checking for CAPTCHAs after action...")
                        captcha_solved = await self.check_and_handle_captcha()
                        
                        if not captcha_solved:
                            self.conversation_history.append(
                                "Note: CAPTCHA detected but not solved - may affect task completion\n"
                            )
                except:
                    print("Error in checking for CAPTCHAs after action")
        except Exception as e:
            self.logger.error(f"Step error: {e}")
            
            error_str = str(e).lower()
            if "execution context was destroyed" in error_str or "navigation" in error_str:
                self.logger.info("Navigation-related error detected, continuing to next step")
                action_results.append(ActionResult(
                    action="navigation_error",
                    success=True,
                    error="Navigation in progress",
                    state_changed=True
                ))
            else:
                action_results.append(ActionResult(
                    action="error",
                    success=False,
                    error=str(e),
                    state_changed=False
                ))
                self.consecutive_errors += 1
        
        try:
            current_url = self.page.url
            current_title = await self.page.title()
            dom_state_final = await self.dom_service.extract_dom_state(self.page)
            dom_hash_final = dom_state_final.get_dom_hash()
        except Exception as e:
            self.logger.warning(f"Error getting page info: {e}")
            current_url = "unknown"
            current_title = "unknown"
            dom_hash_final = None
        
        step_result = StepResult(
            step=step_num,
            url=current_url,
            title=current_title,
            actions=action_results,
            dom_hash=dom_hash_final
        )
        
        return step_result


    def _extract_credentials_from_task(self, task: str):
        import re
        
        email_match = re.search(r"[Ee]mail[:\s]+['\"]?([^'\"]+@[^'\"]+\.[^'\"]+)['\"]?", task)
        password_match = re.search(r"[Pp]assword[:\s]+['\"]?([^'\"]+)['\"]?", task)
        
        if email_match and password_match:
            return {
                'email': email_match.group(1).strip(),
                'password': password_match.group(1).strip()
            }
        
        return None











    from typing import List, Dict, Set, Tuple, Optional

    def _extract_task_intent(self, task: str) -> Dict[str, any]:
        """
        Analyze task to understand what type of data is expected.
        Returns intent information for validation.
        """
        task_lower = task.lower()
        
        intent = {
            'is_write_task': False,
            'requires_list': False,
            'requires_details': False,
            'list_size_min': 1,
            'expected_fields': set(),
            'action_verbs': set(),
            'content_type': 'unknown'
        }
        
        write_indicators = ['submit', 'add', 'create', 'post', 'upload', 'share', 'write', 'send', 'publish', 'fill', 'enter', 'input', 'register', 'sign up', 'book', 'reserve', 'order', 'purchase', 'buy']
        for indicator in write_indicators:
            if indicator in task_lower:
                intent['is_write_task'] = True
                intent['action_verbs'].add(indicator)
        
        list_indicators = ['find', 'search', 'list', 'show me', 'get all', 'compare']
        detail_indicators = ['extract', 'get the', 'what is', 'tell me about', 'information about', 'details']
        
        for indicator in list_indicators:
            if indicator in task_lower:
                intent['requires_list'] = True
                break
        
        for indicator in detail_indicators:
            if indicator in task_lower:
                intent['requires_details'] = True
                break
        
        content_patterns = {
            'ingredient': ['ingredient', 'shopping list', 'grocery', 'materials', 'supplies'],
            'instruction': ['instruction', 'steps', 'how to', 'procedure', 'directions'],
            'price': ['price', 'cost', 'rate', 'fee', 'charge'],
            'rating': ['rating', 'review', 'score', 'star'],
            'contact': ['phone', 'email', 'address', 'contact'],
            'time': ['time', 'duration', 'hours', 'schedule', 'availability'],
            'description': ['description', 'about', 'overview', 'summary', 'details']
        }
        
        for content_type, patterns in content_patterns.items():
            if any(pattern in task_lower for pattern in patterns):
                intent['content_type'] = content_type
                intent['expected_fields'].update(patterns)
                break
        
        number_patterns = re.findall(r'\b(\d+)\b', task)
        if number_patterns:
            try:
                intent['list_size_min'] = int(number_patterns[0])
            except:
                pass
        
        return intent


    def _validate_extraction_completeness(self, items: List[Dict], task: str) -> Tuple[bool, str]:
        """
        Generalized validation that works for any task type.
        Returns (is_valid, reason).
        """
        if not items:
            return False, "No items extracted"
        
        intent = self._extract_task_intent(task)
        
        if intent['requires_list'] and len(items) < intent['list_size_min']:
            return False, f"Task requires {intent['list_size_min']}+ items, got {len(items)}"
        
        total_values = 0
        empty_values = 0
        populated_field_types = set()
        
        for item in items:
            if isinstance(item, dict):
                for key, value in item.items():
                    if key in ['strategy', 'success', 'confidence', 'match_quality', 'note', 'url', 'link']:
                        continue
                    
                    total_values += 1
                    
                    if value in [None, "", []]:
                        empty_values += 1
                    elif isinstance(value, str) and len(value.strip()) < 2:
                        empty_values += 1
                    else:
                        populated_field_types.add(key.lower())
        
        if total_values == 0:
            return False, "No extractable fields found"
        
        empty_ratio = empty_values / total_values
        
        if empty_ratio > 0.6:
            return False, f"Too many empty fields ({empty_ratio:.0%})"
        
        if intent['requires_details']:
            if len(populated_field_types) < 2:
                return False, f"Task requires detailed data, only found {len(populated_field_types)} field types"
            
            if intent['content_type'] != 'unknown':
                has_required_content = False
                for field_name in populated_field_types:
                    if any(expected in field_name for expected in intent['expected_fields']):
                        has_required_content = True
                        break
                
                if not has_required_content:
                    expected_str = ', '.join(list(intent['expected_fields'])[:3])
                    return False, f"Task requires {intent['content_type']} data, but extracted fields don't match ({expected_str})"
        
        page_url = self.page.url
        if self._is_list_page(page_url):
            substantive_data_count = 0
            for item in items:
                if isinstance(item, dict):
                    detailed_fields = 0
                    for key, value in item.items():
                        if key.lower() in ['title', 'name', 'heading', 'url', 'link', 'rating', 'reviews', 'strategy', 'success']:
                            continue
                        
                        if value not in [None, "", []]:
                            if isinstance(value, str) and len(value) > 15:
                                detailed_fields += 1
                            elif isinstance(value, list) and len(value) > 0:
                                detailed_fields += 1
                    
                    if detailed_fields >= 2:
                        substantive_data_count += 1
            
            if substantive_data_count == 0 and intent['requires_details']:
                return False, "On list page with only preview data, need detail page"
        
        return True, "Validation passed"


    def _is_list_page(self, url: str) -> bool:
        """
        Generalized check if URL is a list/search page.
        Works for any website.
        """
        url_lower = url.lower()
        
        list_patterns = [
            r'/search\?',
            r'/results\?',
            r'/find\?',
            r'/list',
            r'/browse',
            r'/category',
            r'/categories',
            r'/archive',
            r'\?q=',
            r'\?query=',
            r'\?search=',
            r'\?keyword=',
            r'/all-',
            r'/page/\d+',
            r'/p/\d+',
        ]
        
        for pattern in list_patterns:
            if re.search(pattern, url_lower):
                return True
        
        return False


    def _is_detail_page(self, url: str, task: str) -> bool:
        """
        Generalized check if URL is a detail page.
        Works for any website and task type.
        """
        if self._is_list_page(url):
            return False
        
        url_lower = url.lower()
        
        detail_patterns = [
            r'/\d{4,}',
            r'/id[-_]?\d+',
            r'/item/',
            r'/product/',
            r'/article/',
            r'/post/',
            r'/detail',
            r'/view/',
            r'/p/[^/]+/[^/]+',
            r'/[^/]+/\d+',
        ]
        
        for pattern in detail_patterns:
            if re.search(pattern, url_lower):
                return True
        
        path_segments = [seg for seg in url_lower.split('/') if seg and seg not in ['www', 'http:', 'https:']]
        
        if len(path_segments) >= 3:
            return True
        
        task_keywords = set(task.lower().split()) - {
            'find', 'get', 'search', 'show', 'the', 'a', 'an', 'and', 'or', 'for', 'of', 'in', 'to'
        }
        task_keywords = {kw for kw in task_keywords if len(kw) > 3}
        
        if task_keywords:
            matches = sum(1 for kw in task_keywords if kw in url_lower)
            if matches >= min(2, len(task_keywords)):
                return True
        
        return False


    def _calculate_data_depth_score(self, items: List[Dict]) -> float:
        """
        Calculate how "deep" the extracted data is.
        0.0 = only titles/names
        1.0 = rich, detailed data
        """
        if not items:
            return 0.0
        
        shallow_fields = {'title', 'name', 'heading', 'url', 'link', 'id'}
        medium_fields = {'rating', 'reviews', 'price', 'date', 'author', 'category'}
        
        shallow_count = 0
        medium_count = 0
        deep_count = 0
        
        for item in items:
            if isinstance(item, dict):
                for key, value in item.items():
                    if value in [None, "", []]:
                        continue
                    
                    key_lower = key.lower()
                    
                    if key_lower in shallow_fields:
                        shallow_count += 1
                    elif key_lower in medium_fields:
                        medium_count += 1
                    else:
                        if isinstance(value, str) and len(value) > 30:
                            deep_count += 1
                        elif isinstance(value, list) and len(value) >= 3:
                            deep_count += 1
                        elif isinstance(value, dict):
                            deep_count += 1
                        else:
                            medium_count += 1
        
        total = shallow_count + medium_count + deep_count
        if total == 0:
            return 0.0
        
        depth_score = (shallow_count * 0.2 + medium_count * 0.5 + deep_count * 1.0) / total
        
        return depth_score


    def _should_continue_searching(self, extraction_confidence: float, step_num: int, task: str) -> bool:
        """
        Decide if agent should continue searching for better data.
        Prevents premature satisfaction with shallow results.
        """
        intent = self._extract_task_intent(task)
        
        if extraction_confidence < 0.5:
            return True
        
        if intent['requires_details'] and extraction_confidence < 0.75:
            if step_num < 20:
                return True
        
        if self._is_list_page(self.page.url):
            return True
        
        return False

    
    async def _handle_stagnation(self, task: str, reason: str) -> bool:
        """
        Handle stagnation by forcing different approach - PRESERVED FROM ORIGINAL
        
        Args:
            task: Original task
            reason: Reason for stagnation
            
        Returns:
            True if recovery successful, False otherwise
        """
        self.logger.warning(f" Attempting stagnation recovery...")
        
        try:
            # Get current state
            dom_state = await self.dom_service.extract_dom_state(self.page)
            
            if "Repeating same action" in reason and "click_element" in reason:
                workflow_result = await self._try_workflow_navigation(task, dom_state)
                if workflow_result:
                    self.progress_state = ProgressState()
                    return workflow_result
            
            current_state = f"URL: {dom_state.url}\nTitle: {dom_state.title}\nElements: {len(dom_state.elements)}"
            
            # Ask LLM for recovery strategy
            recovery_message = build_stagnation_recovery_message(
                task=task,
                reason=reason,
                current_state=current_state
            )
            
            decision = self.llm.decide_action(
                system_prompt=SYSTEM_PROMPT,
                user_message=recovery_message,
                conversation_history=[]  # Fresh start for recovery
            )
            
            self.logger.info(f"Recovery action: {decision.action}")
            
            # Execute recovery action
            action_kwargs = decision.model_dump(exclude={'reasoning', 'action'}, exclude_none=True)
            result = await self.action_executor.execute(decision.action, **action_kwargs)
            
            # Reset progress state
            self.progress_state = ProgressState()
            
            return result.success
            
        except Exception as e:
            self.logger.error(f"Stagnation recovery failed: {e}")
            return False
    
    async def _try_workflow_navigation(self, task: str, dom_state) -> bool:
        """
        Try to navigate to workflow-related pages when stuck clicking non-navigating elements
        """
        try:
            self.logger.info("Trying intelligent workflow navigation")
            
            task_lower = task.lower()
            workflow_keywords = []
            
            if any(word in task_lower for word in ['submit', 'add', 'create', 'post', 'upload', 'share']):
                action_word = next((w for w in ['submit', 'add', 'create', 'post', 'upload', 'share'] if w in task_lower), 'add')
                workflow_keywords.extend([action_word, f"{action_word} ", f"{action_word}ing"])
            
            if 'recipe' in task_lower:
                workflow_keywords.extend(['recipe', 'add recipe', 'submit recipe', 'create recipe', 'share recipe', 'upload recipe'])
            if 'review' in task_lower:
                workflow_keywords.extend(['review', 'add review', 'write review', 'submit review'])
            if 'comment' in task_lower:
                workflow_keywords.extend(['comment', 'add comment', 'write comment'])
            if 'photo' in task_lower or 'image' in task_lower or 'picture' in task_lower:
                workflow_keywords.extend(['photo', 'image', 'upload photo', 'add photo'])
            if 'message' in task_lower:
                workflow_keywords.extend(['message', 'send message', 'compose'])
            
            if not workflow_keywords:
                return False
            
            from models import AgentDecision
            best_element = None
            best_score = 0
            
            for elem in dom_state.elements:
                text = getattr(elem, 'text', '') or ''
                text = text.lower().strip()
                aria_label = getattr(elem, 'aria_label', '') or ''
                aria_label = aria_label.lower()
                combined = f"{text} {aria_label}"
                
                score = 0
                for keyword in workflow_keywords:
                    if keyword in combined:
                        score += len(keyword) * 2
                
                elem_tag = getattr(elem, 'tag', '')
                if elem_tag == 'a' and any(word in combined for word in ['create', 'add', 'new', 'submit', 'upload', 'share', 'post']):
                    score += 5
                
                if score > best_score:
                    best_score = score
                    best_element = elem
            
            if best_element and best_score >= 3:
                elem_text = getattr(best_element, 'text', 'unknown')
                elem_index = getattr(best_element, 'index', None)
                if elem_index is None:
                    return False
                
                self.logger.info(f"Found workflow link: '{elem_text}' (score: {best_score})")
                decision = AgentDecision(
                    action="click_element",
                    reasoning=f"Navigating to workflow page for: {task}",
                    index=elem_index
                )
                action_kwargs = decision.model_dump(exclude={'reasoning', 'action'}, exclude_none=True)
                result = await self.action_executor.execute(decision.action, **action_kwargs)
                return result.success
            
            search_query = None
            if 'recipe' in task_lower:
                search_query = "add recipe"
            elif 'review' in task_lower:
                search_query = "write review"
            elif workflow_keywords:
                search_query = workflow_keywords[0]
            
            if search_query:
                self.logger.info(f"Attempting search for: {search_query}")
                decision = AgentDecision(
                    action="search",
                    reasoning=f"Searching for workflow page: {search_query}",
                    query=search_query
                )
                action_kwargs = decision.model_dump(exclude={'reasoning', 'action'}, exclude_none=True)
                result = await self.action_executor.execute(decision.action, **action_kwargs)
                return result.success
            
            return False
            
        except Exception as e:
            self.logger.error(f"Workflow navigation failed: {e}")
            return False
    
    def _task_keywords(self, task: str, max_keywords: int = 8):
        """Extract keywords from task - PRESERVED FROM ORIGINAL"""
        words = re.findall(r"[a-zA-Z0-9]+", task.lower())
        stop = {
            "the","a","an","and","or","to","for","of","in","on","at","by","with",
            "is","are","was","were","be","as","it","this","that","from","into",
            "find","get","give","show","tell","me","what","which","who","when","where","how"
        }
        kws = []
        for w in words:
            if len(w) < 4:
                continue
            if w in stop:
                continue
            if w not in kws:
                kws.append(w)
            if len(kws) >= max_keywords:
                break
        return kws

    async def _extract_targeted_content(self, task: str) -> Dict[str, str]:
        """
        Extract targeted content - PRESERVED FROM ORIGINAL
        
        Returns dict with: text, html, selector, reason
        """
        keywords = self._task_keywords(task)
        
        selector_candidates = [
            "main",
            "[role='main']",
            "article",
            "#main",
            "#content",
            "#contents",
            ".content",
            ".main",
            ".main-content",
            ".page-content",
            ".container",
            ".result",
            ".results",
            ".search-results",
            "[data-testid*='result']",
            "[data-test*='result']",
            "[class*='result']",
            "[id*='result']",
            "[class*='Results']",
        ]
        
        payload = await self.page.evaluate(
            """(args) => {
                const { selectorCandidates, keywords } = args;
                
                function isVisible(el) {
                if (!el) return false;
                const st = window.getComputedStyle(el);
                if (!st) return false;
                if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
                const r = el.getBoundingClientRect();
                return r.width > 2 && r.height > 2;
                }
                
                function scoreEl(el) {
                if (!isVisible(el)) return -1;
                const text = (el.innerText || "").trim();
                const len = text.length;
                if (len < 80) return 0;
                let score = Math.min(2000, len);
                const tag = (el.tagName || "").toLowerCase();
                
                if (tag === "main" || tag === "article") score += 600;
                if (el.getAttribute("role") === "main") score += 500;
                
                const badTags = ["nav","header","footer","aside"];
                if (badTags.includes(tag)) score -= 800;
                
                const cls = (el.className || "").toString().toLowerCase();
                const id = (el.id || "").toLowerCase();
                const badHints = ["nav","menu","footer","header","sidebar","cookie","consent","banner","ads","advert"];
                for (const h of badHints) {
                    if (cls.includes(h) || id.includes(h)) score -= 250;
                }
                
                if (len > 10000) score -= 400;
                
                return score;
                }
                
                function pickBestBySelectors() {
                let best = null;
                let bestScore = -Infinity;
                let used = null;
                
                for (const sel of selectorCandidates) {
                    const els = Array.from(document.querySelectorAll(sel));
                    for (const el of els) {
                    const s = scoreEl(el);
                    if (s > bestScore) {
                        bestScore = s;
                        best = el;
                        used = sel;
                    }
                    }
                }
                return { best, used, bestScore };
                }
                
                function findByKeyword() {
                if (!keywords || keywords.length === 0) return null;
                
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
                while (walker.nextNode()) {
                    const el = walker.currentNode;
                    if (!isVisible(el)) continue;
                    const t = (el.innerText || "").toLowerCase();
                    if (!t || t.length < 30) continue;
                    
                    let hit = false;
                    for (const kw of keywords) {
                    if (t.includes(kw)) { hit = true; break; }
                    }
                    if (!hit) continue;
                    
                    let cur = el;
                    let chosen = null;
                    for (let i = 0; i < 8 && cur; i++) {
                    const txt = (cur.innerText || "").trim();
                    if (txt.length >= 200 && txt.length <= 8000 && isVisible(cur)) {
                        chosen = cur;
                    }
                    cur = cur.parentElement;
                    }
                    if (chosen) return chosen;
                }
                return null;
                }
                
                const kwEl = findByKeyword();
                if (kwEl) {
                return {
                    selector: "keyword-container",
                    reason: "Found task keyword(s) on page; selected nearest content container.",
                    text: (kwEl.innerText || "").trim(),
                    html: kwEl.outerHTML || ""
                };
                }
                
                const selPick = pickBestBySelectors();
                if (selPick.best) {
                return {
                    selector: selPick.used || "selector-candidate",
                    reason: "Chose best scoring main/result container from selector candidates.",
                    text: (selPick.best.innerText || "").trim(),
                    html: selPick.best.outerHTML || ""
                };
                }
                
                const body = document.body;
                return {
                selector: "body",
                reason: "Fallback to body; no good container found.",
                text: (body?.innerText || "").trim(),
                html: body?.outerHTML || ""
                };
            }""",
            {"selectorCandidates": selector_candidates, "keywords": keywords},
        )
        
        text = (payload.get("text") or "")
        html = (payload.get("html") or "")
        selector = payload.get("selector") or "unknown"
        reason = payload.get("reason") or "unknown"
        
        if len(text) > 12000:
            text = text[:12000]
        if len(html) > 12000:
            html = html[:12000]
        
        return {"text": text, "html": html, "selector": selector, "reason": reason}
    
    async def _final_extraction(self, task: str) -> str:
        try:
            if hasattr(self, 'successful_extractions') and self.successful_extractions:
                best = max(self.successful_extractions, key=lambda x: x.get('step', 0))
                self.logger.info(f"Using successful extraction from step {best['step']}")
                return best['content']
            
            if hasattr(self, 'last_extraction_content') and self.last_extraction_content:
                try:
                    import json
                    data = json.loads(self.last_extraction_content)
                    if isinstance(data, dict):
                        if 'recipe_name' in data or 'ingredients' in data:
                            self.logger.info("Using last extraction content with recipe data")
                            return self.last_extraction_content
                        
                        populated_fields = sum(1 for v in data.values() if v not in [None, "", []])
                        if populated_fields >= 3:
                            self.logger.info("Using last extraction content with sufficient data")
                            return self.last_extraction_content
                except:
                    pass
            
            if self.extraction_manager:
                best = self.extraction_manager.get_best_extraction()
                if best:
                    summary = self.extraction_manager.get_extraction_summary()
                    self.logger.info(f"Using best progressive extraction from step {summary['best_step']}")
                    self.logger.info(f"Confidence: {summary['best_confidence']:.2f}")
                    return best
            
            if self.extraction_attempts:
                best = max(self.extraction_attempts, key=lambda x: x.get('confidence', 0))
                self.logger.info(f"Using best extraction from step {best['step']} (confidence: {best['confidence']})")
                return best['content']
        except:
            pass
        
        try:
            self.logger.info("Attempting final content extraction...")
            
            try:
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(0.5)
                await self.page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(0.5)
            except:
                pass
            
            if not self.multi_strategy_extractor:
                from universal_multi_strategy_extractor import UniversalMultiStrategyExtractor
                self.multi_strategy_extractor = UniversalMultiStrategyExtractor(self.page)
            
            extraction_result = await self.multi_strategy_extractor.extract_data(
                task,
                max_items=10
            )
            
            if extraction_result and extraction_result.get('success'):
                import json
                return json.dumps(extraction_result, ensure_ascii=False)
            
            target = await self._extract_targeted_content(task)
            return target.get("text") or target.get("html") or "Extraction failed"
        
        except Exception as e:
            self.logger.error(f"Final extraction error: {e}")
            return f"Error: {str(e)}"
        

    def _store_successful_extraction(self, content: str, step: int):
        if not hasattr(self, 'successful_extractions'):
            self.successful_extractions = []
        
        self.successful_extractions.append({
            'step': step,
            'content': content,
            'url': self.page.url,
            'timestamp': time.time()
        })
        self.logger.info(f"Stored successful extraction from step {step}")
    
    def _build_failure_history(
        self,
        task: str,
        start_url: str,
        error_message: str
    ) -> AgentHistory:
        
        return AgentHistory(
            task=task,
            start_url=start_url,
            success=False,
            final={
                "final_answer": error_message,
                "page_url": start_url,
                "page_title": "Error"
            },
            steps=[],
            total_steps=0
        )