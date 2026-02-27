import asyncio
import logging
import re
from typing import List, Optional, Dict
from dataclasses import dataclass
from datetime import datetime
from playwright.async_api import Page

logger = logging.getLogger(__name__)


@dataclass
class FlightSearchParams:
    origin: str
    destination: str
    departure_date: str
    return_date: Optional[str] = None
    cabin_class: str = "economy"
    adults: int = 1
    
    # Filter parameters
    stops: Optional[str] = None  # "nonstop", "1stop", "2stops", or None for any
    max_price: Optional[int] = None  # Maximum price in local currency
    airlines: Optional[List[str]] = None  # List of preferred airlines
    emissions: Optional[str] = None  # "less" for less emissions only, None for any
    max_duration: Optional[int] = None  # Maximum duration in minutes
    bags: Optional[int] = None  # Number of carry-on bags


@dataclass
class FlightResult:
    airline: str
    price: str
    departure_time: Optional[str] = None
    arrival_time: Optional[str] = None
    duration: Optional[str] = None
    stops: Optional[str] = None
    success: bool = True
    error: Optional[str] = None


class GoogleFlightsAutomation:
    
    def __init__(self, page: Page):
        self.page = page
        self.logger = logging.getLogger(__name__)
    
    async def search_flights(self, params: FlightSearchParams) -> List[FlightResult]:
        try:
            self.logger.info("=" * 80)
            self.logger.info("STARTING FLIGHT SEARCH")
            self.logger.info(f"Route: {params.origin} → {params.destination}")
            self.logger.info(f"Dates: {params.departure_date} to {params.return_date}")
            self.logger.info(f"Class: {params.cabin_class}, Passengers: {params.adults}")
            self.logger.info("=" * 80)
            
            current_url = self.page.url
            
            if "google.com/travel/explore" in current_url:
                self.logger.warning("On explore page, navigating to flights")
                await self.page.goto("https://www.google.com/travel/flights", wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(2)
            elif "google.com/travel/flights" not in current_url and "google.com/flights" not in current_url:
                self.logger.info("Navigating to Google Flights")
                await self.page.goto("https://www.google.com/travel/flights", wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(2)
            
            success = await self._fill_origin(params.origin)
            if not success:
                self.logger.error("Origin filling returned False")
                return [FlightResult(airline="", price="", success=False, error="Failed to fill origin")]
            
            self.logger.info("Origin filled successfully, waiting before destination")
            await asyncio.sleep(1)
            
            current_url = self.page.url
            if "travel/explore" in current_url:
                self.logger.warning("Redirected to explore after origin, going back")
                await self.page.goto("https://www.google.com/travel/flights", wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(2)
            
            self.logger.info(f"Filling destination: {params.destination}")
            success = await self._fill_destination(params.destination)
            if not success:
                self.logger.error("Destination filling returned False")
                return [FlightResult(airline="", price="", success=False, error="Failed to fill destination")]
            
            self.logger.info("Destination filled successfully")
            self.logger.info(">>> PHASE 1 COMPLETE: Origin and Destination filled")
            await asyncio.sleep(1)
            
            self.logger.info("Closing any open dropdowns before checkpoint")
            await self.page.keyboard.press('Escape')
            await asyncio.sleep(1)
            
            self.logger.info("MANDATORY PRE-DATE CHECKPOINT - Verifying both locations are properly filled")
            await asyncio.sleep(1)
            
            checkpoint_passed = False
            for checkpoint_attempt in range(2):
                origin_filled = False
                dest_filled = False
                
                try:
                    await self.page.keyboard.press('Escape')
                    await asyncio.sleep(0.5)
                    
                    origin_input = await self.page.query_selector('input[aria-label*="Where from" i]')
                    dest_input = await self.page.query_selector('input[aria-label*="Where to" i]')
                    
                    if origin_input:
                        await origin_input.click()
                        await asyncio.sleep(0.3)
                        await self.page.keyboard.press('Escape')
                        await asyncio.sleep(0.3)
                        
                        origin_value = await origin_input.input_value()
                        origin_placeholder = await origin_input.get_attribute('placeholder') or ''
                        self.logger.info(f"CHECKPOINT: Origin value: '{origin_value}', placeholder: '{origin_placeholder}'")
                        
                        if origin_value and origin_value.strip() and origin_value.lower() != origin_placeholder.lower() and len(origin_value) > 3:
                            if params.origin.lower() in origin_value.lower():
                                origin_filled = True
                                self.logger.info(f"CHECKPOINT: Origin OK - {origin_value}")
                            else:
                                self.logger.error(f"CHECKPOINT FAILED: Origin value '{origin_value}' does not contain '{params.origin}'")
                        else:
                            self.logger.error(f"CHECKPOINT FAILED: Origin is empty or placeholder (value='{origin_value}')")
                    else:
                        self.logger.error("CHECKPOINT FAILED: Origin input not found")
                    
                    if dest_input:
                        await dest_input.click()
                        await asyncio.sleep(0.3)
                        await self.page.keyboard.press('Escape')
                        await asyncio.sleep(0.3)
                        
                        dest_value = await dest_input.input_value()
                        dest_placeholder = await dest_input.get_attribute('placeholder') or ''
                        self.logger.info(f"CHECKPOINT: Destination value: '{dest_value}', placeholder: '{dest_placeholder}'")
                        
                        if dest_value and dest_value.strip() and dest_value.lower() != dest_placeholder.lower() and len(dest_value) > 3:
                            if params.destination.lower() in dest_value.lower():
                                dest_filled = True
                                self.logger.info(f"CHECKPOINT: Destination OK - {dest_value}")
                            else:
                                self.logger.error(f"CHECKPOINT FAILED: Destination value '{dest_value}' does not contain '{params.destination}'")
                        else:
                            self.logger.error(f"CHECKPOINT FAILED: Destination is empty or placeholder (value='{dest_value}')")
                    else:
                        self.logger.error("CHECKPOINT FAILED: Destination input not found")
                except Exception as checkpoint_err:
                    self.logger.error(f"CHECKPOINT ERROR: {checkpoint_err}")
                
                if origin_filled and dest_filled:
                    checkpoint_passed = True
                    self.logger.info("CHECKPOINT PASSED - Both locations verified")
                    break
                else:
                    if checkpoint_attempt == 0:
                        self.logger.warning("CHECKPOINT FAILED - Attempting to re-fill missing locations")
                        
                        if not origin_filled:
                            self.logger.info("Re-filling origin at checkpoint")
                            await self._fill_origin(params.origin)
                            await asyncio.sleep(1)
                        
                        if not dest_filled:
                            self.logger.info("Re-filling destination at checkpoint")
                            await self._fill_destination(params.destination)
                            await asyncio.sleep(1)
            
            if not checkpoint_passed:
                self.logger.warning("CHECKPOINT FAILED AFTER RETRY - Continuing anyway")
            
            self.logger.info("Selecting trip type, passengers, and dates")
            
            if params.return_date:
                trip_type = "round_trip"
            else:
                trip_type = "one_way"
            
            self.logger.info(f">>> Selecting trip type: {trip_type}")
            success = await self._select_trip_type(trip_type)
            if success:
                self.logger.info(f" Trip type selection completed: {trip_type}")
            else:
                self.logger.warning(f"Trip type selection failed")
            
            # Close trip type dropdown
            await self.page.keyboard.press('Escape')
            await asyncio.sleep(0.5)
            await asyncio.sleep(1)
            
            if params.adults:
                self.logger.info(f"Selecting passengers: {params.adults} adult(s)")
                success = await self._select_passengers(params.adults)
                if success:
                    self.logger.info(f"Passenger selection completed: {params.adults} adult(s)")
                else:
                    self.logger.warning(f"Passenger selection failed, continuing with default")
                
                # Close passenger dropdown
                await self.page.keyboard.press('Escape')
                await asyncio.sleep(0.5)
            
            if params.departure_date:
                success = await self._select_date(params.departure_date, is_departure=True)
                if not success:
                    self.logger.warning("Failed to select departure date")
            
            if params.return_date:
                success = await self._select_date(params.return_date, is_departure=False)
                if not success:
                    self.logger.warning("Failed to select return date")
            
            self.logger.info("Re-verifying locations after date selection")
            await asyncio.sleep(1.5)
            
            origin_still_filled = False
            dest_still_filled = False
            try:
                origin_input = await self.page.query_selector('input[aria-label*="Where from" i]')
                dest_input = await self.page.query_selector('input[aria-label*="Where to" i]')
                
                if origin_input:
                    origin_value = await origin_input.input_value()
                    origin_placeholder = await origin_input.get_attribute('placeholder') or ''
                    self.logger.info(f"Re-verify origin - value: '{origin_value}', placeholder: '{origin_placeholder}'")
                    
                    if origin_value and origin_value.strip():
                        if origin_value.lower() != origin_placeholder.lower() and len(origin_value.strip()) > 3:
                            if params.origin.lower() in origin_value.lower():
                                origin_still_filled = True
                                self.logger.info(f"Origin still filled correctly: {origin_value}")
                            else:
                                self.logger.warning(f"Origin has value but wrong city: '{origin_value}' vs '{params.origin}'")
                        else:
                            self.logger.warning(f"Origin is placeholder or too short: '{origin_value}'")
                    else:
                        self.logger.warning(f"Origin is empty")
                
                if dest_input:
                    dest_value = await dest_input.input_value()
                    dest_placeholder = await dest_input.get_attribute('placeholder') or ''
                    self.logger.info(f"Re-verify destination - value: '{dest_value}', placeholder: '{dest_placeholder}'")
                    
                    if dest_value and dest_value.strip():
                        if dest_value.lower() != dest_placeholder.lower() and len(dest_value.strip()) > 3:
                            if params.destination.lower() in dest_value.lower():
                                dest_still_filled = True
                                self.logger.info(f"Destination still filled correctly: {dest_value}")
                            else:
                                self.logger.warning(f"Destination has value but wrong city: '{dest_value}' vs '{params.destination}'")
                        else:
                            self.logger.warning(f"Destination is placeholder or too short: '{dest_value}'")
                    else:
                        self.logger.warning(f"Destination is empty")
            except Exception as reverify_err:
                self.logger.error(f"Re-verification error: {reverify_err}")
            
            if not origin_still_filled:
                self.logger.info("Re-filling origin after date selection")
                success = await self._fill_origin(params.origin)
                if not success:
                    return [FlightResult(airline="", price="", success=False, error="Failed to re-fill origin after dates")]
                await asyncio.sleep(1)
            
            if not dest_still_filled:
                self.logger.info("Re-filling destination after date selection")
                success = await self._fill_destination(params.destination)
                if not success:
                    return [FlightResult(airline="", price="", success=False, error="Failed to re-fill destination after dates")]
                await asyncio.sleep(1)
            
            self.logger.info(" PHASE 2 COMPLETE: Dates selected and locations verified")
            
            # SELECT CLASS AFTER DATES
            if params.cabin_class:
                self.logger.info(f" Selecting cabin class: {params.cabin_class}")
                
                # Extra wait to ensure page is stable
                await asyncio.sleep(2)
                
                success = await self._select_class(params.cabin_class)
                if success:
                    self.logger.info(f"Class selection completed: {params.cabin_class}")
                else:
                    self.logger.warning(f"Class selection failed, continuing with default")
                
                # CRITICAL: Wait longer to ensure selection fully registers
                self.logger.info("Waiting for class selection to register in form state...")
                await asyncio.sleep(3)  # Increased from 1 to 3 seconds
                
                # Close class dropdown
                await self.page.keyboard.press('Escape')
                await asyncio.sleep(0.8)  # Increased from 0.5 to 0.8
            
            self.logger.info(">>> PHASE 3 COMPLETE: All selections done")
            
            self.logger.info("Closing any open dropdowns before search")
            for _ in range(3):
                await self.page.keyboard.press('Escape')
                await asyncio.sleep(0.5)
            
            # Additional wait to ensure form is fully ready
            await asyncio.sleep(2)
            
            self.logger.info("FINAL PRE-SEARCH VERIFICATION - Checking destinations one last time")
            final_check_passed = False
            
            try:
                origin_input = await self.page.query_selector('input[aria-label*="Where from" i]')
                dest_input = await self.page.query_selector('input[aria-label*="Where to" i]')
                
                origin_ok = False
                dest_ok = False
                
                if origin_input:
                    origin_value = await origin_input.input_value()
                    self.logger.info(f"FINAL CHECK: Origin = '{origin_value}'")
                    if origin_value and params.origin.lower() in origin_value.lower():
                        origin_ok = True
                    else:
                        self.logger.error(f"FINAL CHECK FAILED: Origin is '{origin_value}'")
                
                if dest_input:
                    dest_value = await dest_input.input_value()
                    self.logger.info(f"FINAL CHECK: Destination = '{dest_value}'")
                    if dest_value and params.destination.lower() in dest_value.lower():
                        dest_ok = True
                    else:
                        self.logger.error(f"FINAL CHECK FAILED: Destination is '{dest_value}'")
                
                if origin_ok and dest_ok:
                    final_check_passed = True
                    self.logger.info("FINAL CHECK PASSED - Ready to search")
                else:
                    self.logger.error("FINAL CHECK FAILED - One or both destinations empty")
                    
                    if not dest_ok:
                        self.logger.info("Emergency re-fill of destination before search")
                        dest_input_for_fill = await self.page.query_selector('input[aria-label*="Where to" i]')
                        if dest_input_for_fill:
                            await dest_input_for_fill.click(click_count=3)
                            await asyncio.sleep(0.3)
                            await self.page.keyboard.press('Delete')
                            await asyncio.sleep(0.2)
                            
                            for char in params.destination:
                                await self.page.keyboard.type(char)
                                await asyncio.sleep(0.08)
                            
                            await asyncio.sleep(3)
                            await self.page.keyboard.press('ArrowDown')
                            await asyncio.sleep(0.6)
                            await self.page.keyboard.press('Enter')
                            await asyncio.sleep(2)
                            
                            dest_value = await dest_input.input_value()
                            self.logger.info(f"After emergency fill: Destination = '{dest_value}'")
                            
                            if dest_value and params.destination.lower() in dest_value.lower():
                                dest_ok = True
                                final_check_passed = origin_ok and dest_ok
            except Exception as final_check_err:
                self.logger.error(f"Final check error: {final_check_err}")
            
            if not final_check_passed:
                self.logger.warning("FINAL CHECK FAILED - Will attempt search anyway")
            
            current_url_before_search = self.page.url
            self.logger.info(f"Current URL before search: {current_url_before_search}")
            
            self.logger.info("=" * 80)
            self.logger.info("CLICKING SEARCH BUTTON")
            self.logger.info("=" * 80)
            
            search_clicked = await self._click_search()
            
            if not search_clicked:
                self.logger.warning("Search button click returned False, trying Enter key as backup")
                await self.page.keyboard.press('Enter')
                await asyncio.sleep(3)
                await self.page.keyboard.press('Enter')
                await asyncio.sleep(3)
            
            self.logger.info("Waiting for navigation to results page")
            await asyncio.sleep(8)
            
            current_url_after_search = self.page.url
            self.logger.info(f"URL after search: {current_url_after_search}")
            
            url_changed = current_url_after_search != current_url_before_search
            has_tfs = "tfs=" in current_url_after_search
            is_explore_page = "/explore" in current_url_after_search
            is_results_page = has_tfs and not is_explore_page
            
            self.logger.info(f"Navigation check: URL changed={url_changed}, has tfs={has_tfs}, is explore={is_explore_page}, is results={is_results_page}")
            
            if not is_results_page:
                if is_explore_page:
                    self.logger.error("Navigated to explore page instead of search results")
                else:
                    self.logger.error(f"Search did not navigate to results. Before: {current_url_before_search}, After: {current_url_after_search}")
                
                search_clicked = False
                for attempt in range(3):
                    self.logger.info(f"Retry search attempt {attempt+1}")
                    
                    if is_explore_page:
                        self.logger.info("Going back to flights page from explore")
                        await self.page.goto("https://www.google.com/travel/flights", wait_until="domcontentloaded", timeout=10000)
                        await asyncio.sleep(2)
                    
                    await self.page.keyboard.press('Escape')
                    await asyncio.sleep(0.5)
                    
                    if attempt == 0:
                        await self.page.keyboard.press('Enter')
                        await asyncio.sleep(1)
                        self.logger.info("Retry: Pressed Enter to search")
                    else:
                        await self._click_search()
                    
                    await asyncio.sleep(5)
                    
                    new_url = self.page.url
                    self.logger.info(f"URL after retry {attempt+1}: {new_url}")
                    
                    if new_url != current_url_before_search and "tfs=" in new_url and "/explore" not in new_url:
                        search_clicked = True
                        self.logger.info(f"Search successful on retry {attempt+1}")
                        break
                
                if not search_clicked:
                    return [FlightResult(airline="", price="", success=False, error="Search button did not navigate to results")]
            
            await asyncio.sleep(4)
            
            # APPLY FILTERS ON RESULTS PAGE (if any specified)
            self.logger.info("Checking if filters need to be applied")
            await self._apply_filters(params)
            
            results = await self._extract_results()
            
            if not results:
                self.logger.warning("No results on first attempt, waiting longer")
                await asyncio.sleep(3)
                results = await self._extract_results()
            
            return results if results else [FlightResult(airline="No flights", price="N/A", success=True)]
            
        except Exception as e:
            self.logger.error(f"Flight search error: {e}")
            return [FlightResult(airline="", price="", success=False, error=str(e))]
    
    async def _fill_origin(self, city: str) -> bool:
        try:
            await self._close_popups()
            
            input_selectors = [
                'input[aria-label*="Where from" i]',
                'input[placeholder*="Where from" i]',
                'input[aria-label*="origin" i]',
                'div.II2One input',
                'input[jsname][type="text"]'
            ]
            
            input_field = None
            for selector in input_selectors:
                try:
                    elements = await self.page.query_selector_all(selector)
                    for el in elements:
                        if await el.is_visible():
                            aria_label = await el.get_attribute('aria-label') or ''
                            if 'from' in aria_label.lower() or 'origin' in aria_label.lower():
                                input_field = el
                                break
                    if input_field:
                        break
                except:
                    continue
            
            if not input_field:
                self.logger.error("Origin field not found")
                return False
            
            await input_field.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            
            await input_field.click(click_count=3)
            await asyncio.sleep(0.3)
            
            await self.page.keyboard.press('Backspace')
            await asyncio.sleep(0.2)
            
            for char in city:
                await self.page.keyboard.type(char)
                await asyncio.sleep(0.06)
            
            self.logger.info("Waiting for autocomplete dropdown to appear")
            await asyncio.sleep(2)
            
            autocomplete_appeared = False
            for wait_attempt in range(3):
                try:
                    autocomplete_options = await self.page.query_selector_all('li[role="option"]:visible, div[role="option"]:visible')
                    if autocomplete_options and len(autocomplete_options) > 0:
                        self.logger.info(f"Autocomplete appeared with {len(autocomplete_options)} options")
                        autocomplete_appeared = True
                        break
                    else:
                        self.logger.warning(f"Autocomplete not visible yet, waiting (attempt {wait_attempt + 1})")
                        await asyncio.sleep(2)
                except:
                    await asyncio.sleep(2)
            
            if not autocomplete_appeared:
                self.logger.warning("Autocomplete did not appear, trying to trigger it")
                await input_field.click()
                await asyncio.sleep(1)
                await self.page.keyboard.press('End')
                await asyncio.sleep(2)
            
            self.logger.info("Pressing ArrowDown to highlight first option, then Enter to select")
            await self.page.keyboard.press('ArrowDown')
            await asyncio.sleep(0.5)
            await self.page.keyboard.press('Enter')
            await asyncio.sleep(2)
            
            try:
                origin_value = await input_field.input_value()
                self.logger.info(f"Origin value after ArrowDown+Enter: '{origin_value}'")
                
                if not origin_value or origin_value.strip() == "" or city.lower() not in origin_value.lower():
                    self.logger.warning(f"Origin not filled properly, trying complete retype")
                    
                    await input_field.click(click_count=3)
                    await asyncio.sleep(0.3)
                    await self.page.keyboard.press('Delete')
                    await asyncio.sleep(0.3)
                    
                    for char in city:
                        await self.page.keyboard.type(char)
                        await asyncio.sleep(0.08)
                    
                    await asyncio.sleep(4)
                    await self.page.keyboard.press('ArrowDown')
                    await asyncio.sleep(0.6)
                    await self.page.keyboard.press('Enter')
                    await asyncio.sleep(2)
                    
                    origin_value = await input_field.input_value()
                    self.logger.info(f"Origin value after second try: '{origin_value}'")
                    
                    if not origin_value or city.lower() not in origin_value.lower():
                        self.logger.error(f"Origin still not filled after retry")
                        return False
            except Exception as verify_err:
                self.logger.error(f"Could not verify origin: {verify_err}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Origin fill error: {e}")
            return False
    
    async def _fill_destination(self, city: str) -> bool:
        try:
            self.logger.info(f"Starting destination fill for: {city}")
            await asyncio.sleep(1)
            
            input_selectors = [
                'input[aria-label*="Where to" i]',
                'input[placeholder*="Where to" i]',
                'input[aria-label*="destination" i]',
                'div.II2One input',
                'input[jsname][type="text"]'
            ]
            
            input_field = None
            for attempt in range(3):
                self.logger.info(f"Destination field search attempt {attempt+1}")
                for selector in input_selectors:
                    try:
                        elements = await self.page.query_selector_all(selector)
                        self.logger.info(f"Selector '{selector}' found {len(elements)} elements")
                        for el in elements:
                            try:
                                if await el.is_visible() and await el.is_enabled():
                                    aria_label = await el.get_attribute('aria-label') or ''
                                    placeholder = await el.get_attribute('placeholder') or ''
                                    self.logger.info(f"  Element: aria-label='{aria_label}', placeholder='{placeholder}'")
                                    if 'to' in aria_label.lower() or 'destination' in aria_label.lower() or 'where to' in placeholder.lower():
                                        input_field = el
                                        self.logger.info(f"Found destination field with selector: {selector}")
                                        break
                            except Exception as el_err:
                                self.logger.debug(f"  Element check error: {el_err}")
                                continue
                        if input_field:
                            break
                    except Exception as sel_err:
                        self.logger.debug(f"Selector '{selector}' error: {sel_err}")
                        continue
                
                if input_field:
                    break
                
                self.logger.warning(f"Destination field not found on attempt {attempt+1}, waiting")
                await asyncio.sleep(1)
            
            if not input_field:
                self.logger.error("Destination field not found after 3 attempts")
                return False
            
            self.logger.info("Scrolling destination field into view")
            await input_field.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            
            self.logger.info("Clicking destination field")
            await input_field.click(click_count=3)
            await asyncio.sleep(0.3)
            
            self.logger.info("Clearing destination field")
            await self.page.keyboard.press('Backspace')
            await asyncio.sleep(0.2)
            
            self.logger.info(f"Typing destination: {city}")
            for char in city:
                await self.page.keyboard.type(char)
                await asyncio.sleep(0.06)
            
            self.logger.info("Waiting for autocomplete dropdown to appear")
            await asyncio.sleep(2)
            
            autocomplete_appeared = False
            for wait_attempt in range(3):
                try:
                    autocomplete_options = await self.page.query_selector_all('li[role="option"]:visible, div[role="option"]:visible')
                    if autocomplete_options and len(autocomplete_options) > 0:
                        self.logger.info(f"Autocomplete appeared with {len(autocomplete_options)} options")
                        autocomplete_appeared = True
                        break
                    else:
                        self.logger.warning(f"Autocomplete not visible yet, waiting (attempt {wait_attempt + 1})")
                        await asyncio.sleep(2)
                except:
                    await asyncio.sleep(2)
            
            if not autocomplete_appeared:
                self.logger.warning("Autocomplete did not appear, trying to trigger it")
                await input_field.click()
                await asyncio.sleep(1)
                await self.page.keyboard.press('End')
                await asyncio.sleep(2)
            
            self.logger.info("Pressing ArrowDown to highlight first option, then Enter to select")
            await self.page.keyboard.press('ArrowDown')
            await asyncio.sleep(0.5)
            await self.page.keyboard.press('Enter')
            await asyncio.sleep(2)
            
            try:
                dest_value = await input_field.input_value()
                self.logger.info(f"Destination value after ArrowDown+Enter: '{dest_value}'")
                
                if not dest_value or dest_value.strip() == "" or city.lower() not in dest_value.lower():
                    self.logger.warning(f"Destination not filled properly, trying complete retype")
                    
                    await input_field.click(click_count=3)
                    await asyncio.sleep(0.3)
                    await self.page.keyboard.press('Delete')
                    await asyncio.sleep(0.3)
                    
                    for char in city:
                        await self.page.keyboard.type(char)
                        await asyncio.sleep(0.08)
                    
                    await asyncio.sleep(4)
                    await self.page.keyboard.press('ArrowDown')
                    await asyncio.sleep(0.6)
                    await self.page.keyboard.press('Enter')
                    await asyncio.sleep(2)
                    
                    dest_value = await input_field.input_value()
                    self.logger.info(f"Destination value after second try: '{dest_value}'")
                    
                    if not dest_value or city.lower() not in dest_value.lower():
                        self.logger.error(f"Destination still not filled after retry")
                        return False
            except Exception as verify_err:
                self.logger.error(f"Could not verify destination: {verify_err}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Destination fill error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    async def _select_autocomplete(self, city: str) -> bool:
        try:
            await asyncio.sleep(1.5)
            
            selectors = [
                'li[role="option"]:visible',
                'ul[role="listbox"] li:visible',
                'div[role="option"]:visible',
                '[data-suggestion]:visible',
                '.zsRT0d:visible',
                'li.sbct:visible',
                '[jsname="bVqjv"]:visible',
                'div[jsname="bVqjv"]:visible'
            ]
            
            for selector in selectors:
                try:
                    await self.page.wait_for_selector(selector.replace(':visible', ''), timeout=2500, state='visible')
                    
                    suggestions = await self.page.query_selector_all(selector.replace(':visible', ''))
                    
                    if not suggestions:
                        continue
                    
                    suggestion_texts = []
                    for suggestion in suggestions[:8]:
                        try:
                            if await suggestion.is_visible():
                                text = await suggestion.text_content()
                                if text:
                                    suggestion_texts.append(text.strip())
                        except:
                            continue
                    
                    if suggestion_texts:
                        self.logger.info(f"Available autocomplete suggestions for '{city}': {suggestion_texts[:5]}")
                    
                    city_lower = city.lower().strip()
                    city_words = [word for word in city_lower.split() if len(word) > 0]
                    
                    for i, suggestion in enumerate(suggestions[:8]):
                        try:
                            if await suggestion.is_visible():
                                box = await suggestion.bounding_box()
                                if not box or box['width'] <= 0 or box['height'] <= 0:
                                    continue
                                
                                suggestion_text = await suggestion.text_content()
                                if not suggestion_text:
                                    continue
                                
                                suggestion_clean = suggestion_text.strip().lower()
                                
                                if city_lower == suggestion_clean:
                                    await suggestion.click(timeout=2000, force=False)
                                    self.logger.info(f"Clicked exact match autocomplete: {suggestion_text.strip()}")
                                    await asyncio.sleep(1.5)
                                    return True
                                
                                first_part = suggestion_clean.split(',')[0].strip() if ',' in suggestion_clean else suggestion_clean
                                
                                if city_lower == first_part:
                                    await suggestion.click(timeout=2000, force=False)
                                    self.logger.info(f"Clicked autocomplete match before comma: {suggestion_text.strip()}")
                                    await asyncio.sleep(1.5)
                                    return True
                                
                                if first_part.startswith(city_lower):
                                    await suggestion.click(timeout=2000, force=False)
                                    self.logger.info(f"Clicked autocomplete starts with '{city}': {suggestion_text.strip()}")
                                    await asyncio.sleep(1.5)
                                    return True
                                
                                suggestion_words = set()
                                for part in suggestion_clean.replace(',', ' ').replace('-', ' ').split():
                                    if len(part) > 0:
                                        suggestion_words.add(part)
                                
                                all_city_words_found = all(
                                    any(sug_word == city_word or sug_word.startswith(city_word + ' ') for sug_word in suggestion_words)
                                    for city_word in city_words
                                )
                                
                                if all_city_words_found and len(city_words) > 0:
                                    await suggestion.click(timeout=2000, force=False)
                                    self.logger.info(f"Clicked autocomplete for '{city}': {suggestion_text.strip()}")
                                    await asyncio.sleep(1.5)
                                    return True
                        except Exception as e:
                            self.logger.debug(f"Error checking suggestion {i}: {e}")
                            continue
                except Exception as selector_err:
                    self.logger.debug(f"Selector {selector} failed: {selector_err}")
                    continue
            
            self.logger.warning(f"No matching autocomplete found for '{city}'")
            return False
            
        except Exception as e:
            self.logger.error(f"Autocomplete error: {e}")
            return False
    
    async def _select_date(self, date_str: str, is_departure: bool = True) -> bool:
        try:
            date_obj = self._parse_date(date_str)
            if not date_obj:
                self.logger.error(f"Failed to parse date: {date_str}")
                return False
            
            date_type = "departure" if is_departure else "return"
            self.logger.info(f"Selecting {date_type} date: {date_str}")
            
            if not is_departure:
                await asyncio.sleep(1)
                return_input_selectors = [
                    'input[aria-label*="Return" i]',
                    'input[placeholder*="Return" i]',
                    'div.bgKQre input[aria-label*="Return" i]'
                ]
                
                clicked_return_input = False
                for selector in return_input_selectors:
                    try:
                        elements = await self.page.query_selector_all(selector)
                        for el in elements:
                            if await el.is_visible():
                                await el.click()
                                await asyncio.sleep(1.5)
                                clicked_return_input = True
                                self.logger.info("Clicked return date input to open calendar")
                                break
                        if clicked_return_input:
                            break
                    except:
                        continue
                
                if not clicked_return_input:
                    self.logger.warning("Could not click return date input, calendar may already be open")
            else:
                await asyncio.sleep(1)
                
                date_button_selectors = [
                    'input[aria-label*="Departure" i]',
                    'input[placeholder*="Departure" i]',
                    'div.bgKQre input',
                    'button[aria-label*="Departure" i]'
                ]
                
                calendar_opened = False
                for attempt in range(3):
                    for selector in date_button_selectors:
                        try:
                            elements = await self.page.query_selector_all(selector)
                            for el in elements:
                                try:
                                    if await el.is_visible():
                                        await el.click()
                                        await asyncio.sleep(1.5)
                                        
                                        calendar_visible = await self.page.query_selector('[aria-live="polite"]')
                                        if calendar_visible:
                                            calendar_opened = True
                                            self.logger.info("Departure date calendar opened")
                                            break
                                except:
                                    continue
                            if calendar_opened:
                                break
                        except:
                            continue
                    
                    if calendar_opened:
                        break
                    
                    self.logger.warning(f"Calendar not opened on attempt {attempt+1}, retrying")
                    await asyncio.sleep(1)
                
                if not calendar_opened:
                    self.logger.error("Failed to open calendar after 3 attempts")
                    return False
            
            target_month = date_obj.strftime("%B")
            target_year = date_obj.year
            target_day = date_obj.day
            
            self.logger.info(f"Navigating calendar to {target_month} {target_year}")
            
            for iteration in range(24):
                try:
                    month_display = await self.page.query_selector('[aria-live="polite"]')
                    if not month_display:
                        month_display = await self.page.query_selector('h2[aria-live="polite"]')
                    
                    if month_display:
                        month_text = await month_display.text_content()
                        self.logger.info(f"Current calendar view: {month_text}")
                        
                        if target_month in month_text and str(target_year) in month_text:
                            self.logger.info(f"Found target month: {target_month} {target_year}")
                            break
                    
                    next_button = await self.page.query_selector('button[aria-label*="Next" i]')
                    if next_button:
                        await next_button.click()
                        await asyncio.sleep(0.6)
                    else:
                        self.logger.warning("Next button not found, stopping navigation")
                        break
                except Exception as nav_err:
                    self.logger.error(f"Navigation error at iteration {iteration}: {nav_err}")
                    break
            
            date_selectors = [
                f'div[data-iso="{date_str}"]',
                f'div[aria-label*="{target_month} {target_day}" i]',
                f'button[aria-label*="{target_month} {target_day}" i]',
                f'div[role="button"][aria-label*="{target_month} {target_day}" i]',
                f'td[aria-label*="{target_month} {target_day}" i]'
            ]
            
            for selector in date_selectors:
                try:
                    date_element = await self.page.query_selector(selector)
                    if date_element and await date_element.is_visible():
                        await date_element.click()
                        await asyncio.sleep(0.5)
                        self.logger.info(f"Selected date: {date_str}")
                        return True
                except:
                    continue
            
            try:
                all_days = await self.page.query_selector_all(f'div:has-text("{target_day}"), button:has-text("{target_day}")')
                for day in all_days:
                    if await day.is_visible():
                        text = await day.text_content()
                        if text and text.strip() == str(target_day):
                            await day.click()
                            await asyncio.sleep(0.5)
                            self.logger.info(f"Selected date via text: {date_str}")
                            return True
            except:
                pass
            
            self.logger.warning(f"Could not click date: {date_str}")
            return False
            
        except Exception as e:
            self.logger.error(f"Date selection error: {e}")
            return False
    
    async def _select_class(self, class_type: str) -> bool:
        try:
            class_map = {
                "economy": "Economy",
                "premium": "Premium economy",
                "business": "Business",
                "first": "First"
            }
            
            class_name = class_map.get(class_type.lower(), "Economy")
            
            self.logger.info(f"Attempting to select class: {class_name}")
            
            class_selector_found = False
            class_selector = None
            
            all_comboboxes = await self.page.query_selector_all('div[role="combobox"], button[role="combobox"]')
            
            for combo in all_comboboxes:
                try:
                    if await combo.is_visible():
                        text = await combo.text_content()
                        aria_label = await combo.get_attribute('aria-label') or ''
                        
                        if text and ('economy' in text.lower() or 'business' in text.lower() or 'first' in text.lower() or 'premium' in text.lower()):
                            class_selector = combo
                            class_selector_found = True
                            self.logger.info(f"Found class selector with text: {text.strip()}")
                            break
                        
                        if 'class' in aria_label.lower():
                            class_selector = combo
                            class_selector_found = True
                            self.logger.info(f"Found class selector with aria-label: {aria_label}")
                            break
                except:
                    continue
            
            if not class_selector_found:
                self.logger.error("Could not find class selector combobox")
                return False
            
            try:
                await class_selector.scroll_into_view_if_needed()
                await asyncio.sleep(0.3)
            except:
                pass
            
            clicked = False
            for click_attempt in range(2):
                try:
                    await class_selector.click(timeout=5000)
                    clicked = True
                    self.logger.info("Clicked class selector")
                    break
                except Exception as e:
                    self.logger.warning(f"Regular click failed: {e}, trying force click")
                    try:
                        await class_selector.click(force=True, timeout=3000)
                        clicked = True
                        self.logger.info("Force clicked class selector")
                        break
                    except Exception as e2:
                        if click_attempt == 0:
                            self.logger.warning(f"Force click failed: {e2}, will retry")
                            await asyncio.sleep(1)
                        else:
                            self.logger.error(f"All click attempts failed: {e2}")
                            return False
            
            if not clicked:
                return False
            
            self.logger.info("Waiting for class options to appear")
            await asyncio.sleep(4) 
            
            try:
                await self.page.wait_for_selector('li[role="option"]', timeout=5000, state='visible')
                self.logger.info("Options dropdown visible")
            except:
                self.logger.warning("Options did not appear, will check anyway")
            
            await asyncio.sleep(1.5)
            
            class_options = await self.page.query_selector_all('li[role="option"], div[role="option"], li[data-value], div[data-value]')
            
            option_texts = []
            if class_options:
                for opt in class_options:
                    try:
                        if await opt.is_visible():
                            text = await opt.text_content()
                            if text and text.strip():
                                option_texts.append(text.strip())
                    except:
                        pass
            
            if not option_texts:
                self.logger.warning("No visible options found, retrying dropdown click")
                
                try:
                    await self.page.keyboard.press('Escape')
                    await asyncio.sleep(1)
                    await class_selector.click(force=True, timeout=3000)
                    await asyncio.sleep(3.5)
                    
                    try:
                        await self.page.wait_for_selector('li[role="option"]', timeout=5000, state='visible')
                        self.logger.info("Options visible after retry")
                    except:
                        self.logger.warning("Options still not visible after retry")
                    
                    await asyncio.sleep(1.5)
                    class_options = await self.page.query_selector_all('li[role="option"], div[role="option"]')
                    
                    option_texts = []
                    if class_options:
                        for opt in class_options:
                            try:
                                if await opt.is_visible():
                                    text = await opt.text_content()
                                    if text and text.strip():
                                        option_texts.append(text.strip())
                            except:
                                pass
                    
                    self.logger.info(f"After retry, found {len(option_texts)} visible options")
                except Exception as retry_err:
                    self.logger.error(f"Retry failed: {retry_err}")
            
            if not option_texts:
                self.logger.error("No visible class options found after retry")
                self.logger.warning("Skipping class selection, will search with default Economy")
                return False
            
            self.logger.info(f"Available class options: {option_texts}")
            
            selected = False
            for option in class_options:
                try:
                    if await option.is_visible():
                        option_text = await option.text_content()
                        if option_text:
                            option_lower = option_text.lower().strip()
                            class_lower = class_name.lower()
                            
                            self.logger.debug(f"Checking option '{option_text.strip()}' against '{class_name}'")
                            
                            if class_lower in option_lower or option_lower.startswith(class_lower):
                                await option.click()
                                await asyncio.sleep(1.2) 
                                self.logger.info(f"Selected class option: {option_text.strip()}")
                                selected = True
                                return True
                except Exception as opt_err:
                    self.logger.debug(f"Error clicking option: {opt_err}")
                    continue
            
            if not selected:
                self.logger.error(f"Could not find class option matching: {class_name}")
            return False
            
        except Exception as e:
            self.logger.error(f"Class selection error: {e}")
            return False
    
    async def _select_trip_type(self, trip_type: str) -> bool:
        try:
            trip_map = {
                "round_trip": "Round trip",
                "one_way": "One way",
                "multi_city": "Multi-city"
            }
            
            trip_name = trip_map.get(trip_type.lower(), "Round trip")
            
            self.logger.info(f"Attempting to select trip type: {trip_name}")
            
            trip_selector = None
            all_comboboxes = await self.page.query_selector_all('div[role="combobox"], button[role="combobox"]')
            
            for combo in all_comboboxes:
                try:
                    if await combo.is_visible():
                        text = await combo.text_content()
                        if text and ('round trip' in text.lower() or 'one way' in text.lower() or 'multi-city' in text.lower()):
                            trip_selector = combo
                            self.logger.info(f"Found trip type selector with text: {text.strip()}")
                            break
                except:
                    continue
            
            if not trip_selector:
                self.logger.warning("Could not find trip type selector")
                return False
            
            try:
                await trip_selector.click(timeout=5000)
            except:
                try:
                    await trip_selector.click(force=True, timeout=3000)
                    self.logger.info("Force clicked trip type selector")
                except Exception as e:
                    self.logger.error(f"Failed to click trip type selector: {e}")
                    return False
            
            await asyncio.sleep(1.5)
            
            try:
                await self.page.wait_for_selector('li[role="option"]', timeout=3000, state='visible')
            except:
                pass
            
            await asyncio.sleep(0.5)
            
            options = await self.page.query_selector_all('li[role="option"], div[role="option"]')
            
            for option in options:
                try:
                    if await option.is_visible():
                        option_text = await option.text_content()
                        if option_text and trip_name.lower() in option_text.lower():
                            await option.click()
                            await asyncio.sleep(0.8)
                            self.logger.info(f"Selected trip type: {option_text.strip()}")
                            return True
                except:
                    continue
            
            self.logger.warning(f"Could not find trip type option: {trip_name}")
            return False
            
        except Exception as e:
            self.logger.error(f"Trip type selection error: {e}")
            return False
    
    async def _select_passengers(self, count: int) -> bool:
        try:
            self.logger.info(f"Attempting to select {count} passengers")
            
            passenger_selector = None
            all_comboboxes = await self.page.query_selector_all('div[role="combobox"], button[role="combobox"]')
            
            for combo in all_comboboxes:
                try:
                    if await combo.is_visible():
                        text = await combo.text_content()
                        aria_label = await combo.get_attribute('aria-label') or ''
                        
                        if text and ('adult' in text.lower() or 'passenger' in text.lower() or 'traveler' in text.lower()):
                            passenger_selector = combo
                            self.logger.info(f"Found passenger selector with text: {text.strip()}")
                            break
                        
                        if 'passenger' in aria_label.lower() or 'traveler' in aria_label.lower():
                            passenger_selector = combo
                            self.logger.info(f"Found passenger selector with aria-label: {aria_label}")
                            break
                except:
                    continue
            
            if not passenger_selector:
                self.logger.warning("Could not find passenger selector")
                return False
            
            try:
                await passenger_selector.click(timeout=5000)
            except:
                try:
                    await passenger_selector.click(force=True, timeout=3000)
                    self.logger.info("Force clicked passenger selector")
                except Exception as e:
                    self.logger.error(f"Failed to click passenger selector: {e}")
                    return False
            
            await asyncio.sleep(1.5)
            
            for i in range(count - 1):
                try:
                    increment_selectors = [
                        'button[aria-label*="Increase" i]',
                        'button[aria-label*="Add" i]',
                        'button[aria-label*="plus" i]',
                        'button:has-text("+")'
                    ]
                    
                    for selector in increment_selectors:
                        buttons = await self.page.query_selector_all(selector)
                        for btn in buttons:
                            if await btn.is_visible():
                                btn_label = await btn.get_attribute('aria-label') or ''
                                if 'adult' in btn_label.lower():
                                    await btn.click()
                                    await asyncio.sleep(0.5)
                                    self.logger.info(f"Increased adult count to {i+2}")
                                    break
                        break
                except:
                    continue
            
            done_buttons = await self.page.query_selector_all('button')
            for btn in done_buttons:
                try:
                    if await btn.is_visible():
                        text = await btn.text_content()
                        if text and text.strip().lower() in ['done', 'ok', 'apply']:
                            await btn.click()
                            await asyncio.sleep(0.5)
                            self.logger.info("Closed passenger selector")
                            break
                except:
                    continue
            
            return True
            
        except Exception as e:
            self.logger.error(f"Passenger selection error: {e}")
            return False
    
    async def _click_search(self) -> bool:
        try:
            search_button = None
            
            blue_button_selectors = [
                'button[jsname="vvIqCf"]',
                'button.VfPpkd-LgbsSe.nCP5yc.AjY5Oe',
                'button[aria-label*="Search" i].VfPpkd-LgbsSe'
            ]
            
            for selector in blue_button_selectors:
                try:
                    buttons = await self.page.query_selector_all(selector)
                    for button in buttons:
                        if await button.is_visible():
                            box = await button.bounding_box()
                            if box and box['y'] > 300 and box['width'] > 80:
                                search_button = button
                                self.logger.info(f"Found search button via selector {selector}")
                                break
                    if search_button:
                        break
                except:
                    continue
            
            if not search_button:
                all_buttons = await self.page.query_selector_all('button')
                for btn in all_buttons:
                    try:
                        if await btn.is_visible():
                            text = await btn.text_content()
                            aria_label = await btn.get_attribute('aria-label') or ''
                            class_name = await btn.get_attribute('class') or ''
                            
                            if text:
                                text_lower = text.strip().lower()
                                
                                if text_lower in ['search', 'explore']:
                                    box = await btn.bounding_box()
                                    if box and box['y'] > 300 and box['width'] > 80:
                                        search_button = btn
                                        self.logger.info(f"Found search button with text: {text.strip()}")
                                        break
                                
                                if 'VfPpkd-LgbsSe' in class_name:
                                    if 'destination' not in text_lower and 'search' in text_lower:
                                        box = await btn.bounding_box()
                                        if box and box['y'] > 300 and box['width'] > 80:
                                            search_button = btn
                                            self.logger.info(f"Found search button via class and text: {text.strip()}")
                                            break
                            
                            if 'search' in aria_label.lower() and 'destination' not in aria_label.lower():
                                box = await btn.bounding_box()
                                if box and box['y'] > 300:
                                    search_button = btn
                                    self.logger.info(f"Found search button with aria-label")
                                    break
                    except:
                        continue
            
            if search_button:
                try:
                    await search_button.scroll_into_view_if_needed()
                    await asyncio.sleep(0.5)
                    await search_button.click(timeout=5000)
                    self.logger.info("Clicked search button")
                    await asyncio.sleep(4)
                    return True
                except Exception as click_err:
                    self.logger.warning(f"Regular click failed: {click_err}, trying force click")
                    try:
                        await search_button.click(force=True, timeout=3000)
                        self.logger.info("Force clicked search button")
                        await asyncio.sleep(4)
                        return True
                    except Exception as e2:
                        self.logger.warning(f"Force click failed: {e2}, trying JavaScript")
                        try:
                            await self.page.evaluate('(button) => button.click()', search_button)
                            self.logger.info("Clicked search button via JavaScript")
                            await asyncio.sleep(4)
                            return True
                        except:
                            pass
            
            self.logger.warning("Search button not found, trying Enter key multiple times")
            for i in range(5):
                await self.page.keyboard.press('Enter')
                await asyncio.sleep(2)
                try:
                    current_url = self.page.url
                    if 'tfs=' in current_url:
                        self.logger.info(f" Navigation succeeded with Enter key (attempt {i+1})")
                        return True
                except:
                    pass
            return True
            
        except Exception as e:
            self.logger.error(f"Search click error: {e}")
            return False
    
    async def _apply_filters(self, params: FlightSearchParams) -> None:
        """Apply filters on the results page based on search parameters"""
        try:
            filters_applied = []
            
            # Check if any filters need to be applied
            has_filters = any([
                params.stops,
                params.max_price,
                params.airlines,
                params.emissions,
                params.max_duration,
                params.bags
            ])
            
            if not has_filters:
                self.logger.info("No filters to apply")
                return
            
            self.logger.info("=" * 60)
            self.logger.info("APPLYING FILTERS ON RESULTS PAGE")
            self.logger.info("=" * 60)
            
            # Close any existing dropdowns first
            await self.page.keyboard.press('Escape')
            await asyncio.sleep(1)
            
            # Apply stops filter
            if params.stops:
                success = await self._apply_stops_filter(params.stops)
                if success:
                    filters_applied.append(f"Stops: {params.stops}")
            
            # Apply emissions filter
            if params.emissions:
                success = await self._apply_emissions_filter(params.emissions)
                if success:
                    filters_applied.append(f"Emissions: {params.emissions}")
            
            # Apply price filter
            if params.max_price:
                success = await self._apply_price_filter(params.max_price)
                if success:
                    filters_applied.append(f"Max price: {params.max_price}")
            
            # Apply airlines filter
            if params.airlines:
                success = await self._apply_airlines_filter(params.airlines)
                if success:
                    filters_applied.append(f"Airlines: {', '.join(params.airlines)}")
            
            # Apply bags filter
            if params.bags:
                success = await self._apply_bags_filter(params.bags)
                if success:
                    filters_applied.append(f"Bags: {params.bags}")
            
            # Apply duration filter
            if params.max_duration:
                success = await self._apply_duration_filter(params.max_duration)
                if success:
                    filters_applied.append(f"Max duration: {params.max_duration} min")
            
            if filters_applied:
                self.logger.info(f"Applied filters: {', '.join(filters_applied)}")
                self.logger.info("Waiting for filtered results to load...")
                await asyncio.sleep(5)
            else:
                self.logger.warning("No filters were successfully applied")
            
        except Exception as e:
            self.logger.error(f"Error applying filters: {e}")
    
    async def _apply_stops_filter(self, stops: str) -> bool:
        """Apply stops filter (nonstop, 1stop, 2stops)"""
        try:
            self.logger.info(f"Applying stops filter: {stops}")
            
            # Click "Stops" button
            stops_button = await self.page.query_selector('button:has-text("Stops")')
            if not stops_button:
                self.logger.warning("Stops button not found")
                return False
            
            await stops_button.click()
            await asyncio.sleep(2)
            
            # Map stops parameter to radio option
            stops_map = {
                "nonstop": "Non-stop only",
                "1stop": "One stop or fewer",
                "2stops": "Two stops or fewer"
            }
            
            option_text = stops_map.get(stops.lower())
            if not option_text:
                self.logger.warning(f"Invalid stops value: {stops}")
                return False
            
            # Find and click the radio option
            options = await self.page.query_selector_all('div[role="radio"]')
            for option in options:
                try:
                    text = await option.text_content()
                    if text and option_text.lower() in text.lower():
                        await option.click()
                        self.logger.info(f" Selected: {option_text}")
                        await asyncio.sleep(1)
                        
                        # Close the dropdown
                        await self.page.keyboard.press('Escape')
                        await asyncio.sleep(1)
                        return True
                except:
                    continue
            
            self.logger.warning(f"Could not find stops option: {option_text}")
            return False
            
        except Exception as e:
            self.logger.error(f"Stops filter error: {e}")
            return False
    
    async def _apply_emissions_filter(self, emissions: str) -> bool:
        """Apply emissions filter (less)"""
        try:
            self.logger.info(f"Applying emissions filter: {emissions}")
            
            # Click "Emissions" button
            emissions_button = await self.page.query_selector('button:has-text("Emissions")')
            if not emissions_button:
                self.logger.warning("Emissions button not found")
                return False
            
            await emissions_button.click()
            await asyncio.sleep(2)
            
            if emissions.lower() == "less":
                # Find "Less emissions only" radio option
                options = await self.page.query_selector_all('div[role="radio"]')
                for option in options:
                    try:
                        text = await option.text_content()
                        if text and "less emissions" in text.lower():
                            await option.click()
                            self.logger.info(" Selected: Less emissions only")
                            await asyncio.sleep(1)
                            
                            # Close the dropdown
                            await self.page.keyboard.press('Escape')
                            await asyncio.sleep(1)
                            return True
                    except:
                        continue
            
            self.logger.warning("Could not apply emissions filter")
            return False
            
        except Exception as e:
            self.logger.error(f"Emissions filter error: {e}")
            return False
    
    async def _apply_price_filter(self, max_price: int) -> bool:
        """Apply price filter"""
        try:
            self.logger.info(f"Applying price filter: max {max_price}")
            
            # Click "Price" button
            price_button = await self.page.query_selector('button:has-text("Price")')
            if not price_button:
                self.logger.warning("Price button not found")
                return False
            
            await price_button.click()
            await asyncio.sleep(2)
            
            self.logger.warning("Price slider manipulation not yet implemented")
            await self.page.keyboard.press('Escape')
            await asyncio.sleep(1)
            return False
            
        except Exception as e:
            self.logger.error(f"Price filter error: {e}")
            return False
    
    async def _apply_airlines_filter(self, airlines: List[str]) -> bool:
        """Apply airlines filter"""
        try:
            self.logger.info(f"Applying airlines filter: {airlines}")
            
            # Click "Airlines" button
            airlines_button = await self.page.query_selector('button:has-text("Airlines")')
            if not airlines_button:
                self.logger.warning("Airlines button not found")
                return False
            
            await airlines_button.click()
            await asyncio.sleep(2)
            
            # Find and check airline checkboxes
            checkboxes_found = 0
            for airline in airlines:
                # Look for checkbox with airline name
                checkbox_selector = f'input[type="checkbox"][aria-label*="{airline}" i]'
                checkbox = await self.page.query_selector(checkbox_selector)
                
                if checkbox:
                    is_checked = await checkbox.is_checked()
                    if not is_checked:
                        await checkbox.click()
                        checkboxes_found += 1
                        await asyncio.sleep(0.5)
                        self.logger.info(f" Selected airline: {airline}")
            
            if checkboxes_found > 0:
                # Close the dropdown
                await self.page.keyboard.press('Escape')
                await asyncio.sleep(1)
                return True
            else:
                self.logger.warning("No matching airlines found")
                await self.page.keyboard.press('Escape')
                await asyncio.sleep(1)
                return False
            
        except Exception as e:
            self.logger.error(f"Airlines filter error: {e}")
            return False
    
    async def _apply_bags_filter(self, bags: int) -> bool:
        """Apply bags filter"""
        try:
            self.logger.info(f"Applying bags filter: {bags}")
            
            # Click "Bags" button
            bags_button = await self.page.query_selector('button:has-text("Bags")')
            if not bags_button:
                self.logger.warning("Bags button not found")
                return False
            
            await bags_button.click()
            await asyncio.sleep(2)
            
            # Bag count manipulation would require specific control interaction
            self.logger.warning("Bags filter manipulation not yet implemented")
            await self.page.keyboard.press('Escape')
            await asyncio.sleep(1)
            return False
            
        except Exception as e:
            self.logger.error(f"Bags filter error: {e}")
            return False
    
    async def _apply_duration_filter(self, max_duration: int) -> bool:
        """Apply duration filter"""
        try:
            self.logger.info(f"Applying duration filter: max {max_duration} minutes")
            
            # Click "Duration" button
            duration_button = await self.page.query_selector('button:has-text("Duration")')
            if not duration_button:
                self.logger.warning("Duration button not found")
                return False
            
            await duration_button.click()
            await asyncio.sleep(2)
            
            #  Duration slider manipulation would require specific slider control
            self.logger.warning("Duration slider manipulation not yet implemented")
            await self.page.keyboard.press('Escape')
            await asyncio.sleep(1)
            return False
            
        except Exception as e:
            self.logger.error(f"Duration filter error: {e}")
            return False
    
    async def _extract_results(self) -> List[FlightResult]:
        try:
            self.logger.info("Starting results extraction")
            await asyncio.sleep(4)
            
            current_url = self.page.url
            self.logger.info(f"Current URL for extraction: {current_url}")
            
            if "tfs=" not in current_url and "/search" not in current_url:
                self.logger.error(f"Not on results page. URL: {current_url}")
                return []
            
            self.logger.info("On results page, waiting for flight cards to load")
            
            try:
                await self.page.wait_for_selector('li[class*="pIav2d"], div[class*="pIav2d"], ul[role="list"] li', timeout=10000, state='visible')
                self.logger.info("Flight cards appeared")
            except:
                self.logger.warning("Timeout waiting for flight cards, checking anyway")
            
            await asyncio.sleep(2)
            
            results = []
            
            flight_card_selectors = [
                'ul[role="list"] > li',
                'li[class*="pIav2d"]',
                'div[class*="pIav2d"]',
                'li.pIav2d',
                'div.pIav2d',
                'ul[aria-label*="flights" i] > li',
                'ul[aria-label*="Select" i] > li',
                'div[jsname*="flight" i]',
                'li[data-sofl]',
                'div[data-sofl]'
            ]
            
            flight_cards = []
            for selector in flight_card_selectors:
                try:
                    cards = await self.page.query_selector_all(selector)
                    self.logger.info(f"Selector '{selector}' found {len(cards) if cards else 0} elements")
                    if cards and len(cards) > 0:
                        valid_cards = []
                        for card in cards:
                            try:
                                if hasattr(card, 'is_visible'):
                                    if await card.is_visible():
                                        valid_cards.append(card)
                            except:
                                continue
                        
                        if valid_cards:
                            flight_cards = valid_cards
                            self.logger.info(f"Found {len(valid_cards)} visible flight cards using selector: {selector}")
                            break
                except Exception as e:
                    self.logger.debug(f"Selector {selector} failed: {e}")
                    continue
            
            if not flight_cards or len(flight_cards) == 0:
                self.logger.warning("No flight cards found with standard selectors")
                
                page_text = await self.page.text_content('body')
                if "Find cheap flights" in page_text or "Explore destinations" in page_text:
                    self.logger.error("Page still shows explore/search prompt, may not have navigated to results")
                
                self.logger.info("Trying to find any list items on page")
                all_lists = await self.page.query_selector_all('ul[role="list"], ul[aria-label]')
                self.logger.info(f"Found {len(all_lists)} lists on page")
                
                for lst in all_lists:
                    aria_label = await lst.get_attribute('aria-label') or ''
                    self.logger.info(f"List aria-label: '{aria_label}'")
                
                return []
            
            for i, card in enumerate(flight_cards[:10]):
                try:
                    is_visible = False
                    try:
                        is_visible = await card.is_visible()
                    except:
                        continue
                    
                    if not is_visible:
                        continue
                    
                    card_text = ""
                    try:
                        card_text = await card.text_content()
                    except:
                        pass
                    
                    if not card_text or len(card_text.strip()) < 10:
                        continue
                    
                    card_lower = card_text.lower()
                    
                    if any(skip_phrase in card_lower for skip_phrase in [
                        "find cheap flights",
                        "explore destinations",
                        "popular destinations",
                        "flights from india",
                        "discover",
                        "flexible"
                    ]):
                        self.logger.debug(f"Skipping suggestion card {i}")
                        continue
                    
                    airline = ""
                    try:
                        airline_selectors = [
                            'div[class*="sSHqwe"]',
                            'span[class*="h1fkLb"]',
                            'div.sSHqwe',
                            'span.h1fkLb',
                            'div[class*="Ir0Voe"]',
                            'span[class*="Ir0Voe"]'
                        ]
                        for sel in airline_selectors:
                            try:
                                airline_elem = await card.query_selector(sel)
                                if airline_elem:
                                    text = await airline_elem.text_content()
                                    if text and text.strip() and len(text.strip()) > 2:
                                        airline = text
                                        break
                            except:
                                continue
                        
                        if not airline:
                            lines = card_text.split('\n')
                            for line in lines[:3]:
                                if line and len(line) > 2 and len(line) < 50 and not line.startswith('$') and not line.startswith('₹'):
                                    airline = line
                                    break
                    except Exception as e:
                        self.logger.debug(f"Airline extraction error: {e}")
                    
                    price = ""
                    try:
                        price_selectors = [
                            'div[class*="YMlIz"]',
                            'span[class*="YMlIz"]',
                            'div.YMlIz',
                            'span.YMlIz',
                            'div[class*="U3gSDe"]',
                            'span[class*="U3gSDe"]'
                        ]
                        for sel in price_selectors:
                            try:
                                price_elem = await card.query_selector(sel)
                                if price_elem:
                                    text = await price_elem.text_content()
                                    if text and ('$' in text or '€' in text or '£' in text or '₹' in text):
                                        price = text
                                        break
                            except:
                                continue
                        
                        if not price:
                            import re
                            price_match = re.search(r'[\$€£₹][\d,]+', card_text)
                            if price_match:
                                price = price_match.group(0)
                    except Exception as e:
                        self.logger.debug(f"Price extraction error: {e}")
                    
                    departure_time = ""
                    arrival_time = ""
                    try:
                        time_selectors = [
                            'span[class*="eoY5cb"]',
                            'div[class*="eoY5cb"]',
                            'span.eoY5cb',
                            'div.eoY5cb',
                            'span[class*="mv1WYe"]',
                            'div[class*="mv1WYe"]'
                        ]
                        for sel in time_selectors:
                            try:
                                time_elems = await card.query_selector_all(sel)
                                if time_elems and len(time_elems) >= 2:
                                    dep = await time_elems[0].text_content()
                                    arr = await time_elems[1].text_content()
                                    if dep and arr:
                                        departure_time = dep
                                        arrival_time = arr
                                        break
                            except:
                                continue
                    except Exception as e:
                        self.logger.debug(f"Time extraction error: {e}")
                    
                    duration = ""
                    try:
                        duration_selectors = [
                            'div[class*="gvkrdb"]',
                            'span[class*="gvkrdb"]',
                            'div.gvkrdb',
                            'span.gvkrdb',
                            'div[class*="Ak5kof"]',
                            'span[class*="Ak5kof"]'
                        ]
                        for sel in duration_selectors:
                            try:
                                duration_elem = await card.query_selector(sel)
                                if duration_elem:
                                    text = await duration_elem.text_content()
                                    if text and ('hr' in text.lower() or 'min' in text.lower() or 'h' in text):
                                        duration = text
                                        break
                            except:
                                continue
                        
                        if not duration:
                            import re
                            duration_match = re.search(r'\d+\s*h(?:r)?\s*\d*\s*m(?:in)?', card_text, re.IGNORECASE)
                            if duration_match:
                                duration = duration_match.group(0)
                    except Exception as e:
                        self.logger.debug(f"Duration extraction error: {e}")
                    
                    stops = ""
                    try:
                        stops_selectors = [
                            'div[class*="BbR8Ec"]',
                            'span[class*="BbR8Ec"]',
                            'div.BbR8Ec',
                            'span.BbR8Ec',
                            'div[class*="EfT7Ae"]',
                            'span[class*="EfT7Ae"]'
                        ]
                        for sel in stops_selectors:
                            try:
                                stops_elem = await card.query_selector(sel)
                                if stops_elem:
                                    text = await stops_elem.text_content()
                                    if text and ('stop' in text.lower() or 'nonstop' in text.lower()):
                                        stops = text
                                        break
                            except:
                                continue
                        
                        if not stops and 'stop' in card_text.lower():
                            if 'nonstop' in card_text.lower():
                                stops = "Nonstop"
                            elif '1 stop' in card_text.lower():
                                stops = "1 stop"
                            elif '2 stop' in card_text.lower():
                                stops = "2 stops"
                    except Exception as e:
                        self.logger.debug(f"Stops extraction error: {e}")
                    
                    if airline and price:
                        results.append(FlightResult(
                            airline=airline.strip() if airline else "Unknown",
                            price=price.strip() if price else "N/A",
                            departure_time=departure_time.strip() if departure_time else None,
                            arrival_time=arrival_time.strip() if arrival_time else None,
                            duration=duration.strip() if duration else None,
                            stops=stops.strip() if stops else None,
                            success=True
                        ))
                        self.logger.info(f"Extracted flight {i+1}: {airline} - {price}")
                except Exception as card_err:
                    self.logger.debug(f"Error extracting card {i}: {card_err}")
                    continue
            
            if results:
                self.logger.info(f"Extracted {len(results)} flight results")
            else:
                self.logger.warning("No valid flight results extracted")
            
            return results
            
        except Exception as e:
            self.logger.error(f"Results extraction error: {e}")
            return []
    
    async def _close_popups(self):
        try:
            popup_selectors = [
                'button[aria-label*="Close" i]',
                'button[aria-label*="Dismiss" i]',
                'div[role="dialog"] button',
                '[class*="VfPpkd-t08AT-Bz112c"]'
            ]
            
            for selector in popup_selectors:
                try:
                    buttons = await self.page.query_selector_all(selector)
                    for button in buttons:
                        if await button.is_visible():
                            await button.click()
                            await asyncio.sleep(0.3)
                except:
                    continue
        except:
            pass
    
    def _parse_date(self, date_str: str):
        try:
            if re.match(r'\d{4}-\d{2}-\d{2}', date_str):
                return datetime.strptime(date_str, "%Y-%m-%d")
            
            for fmt in [
                "%B %d %Y", "%b %d %Y",
                "%B %d, %Y", "%b %d, %Y",
                "%B %d", "%b %d"
            ]:
                try:
                    date_obj = datetime.strptime(date_str, fmt)
                    if date_obj.year == 1900:
                        date_obj = date_obj.replace(year=datetime.now().year)
                        if date_obj < datetime.now():
                            date_obj = date_obj.replace(year=datetime.now().year + 1)
                    return date_obj
                except ValueError:
                    continue
        except:
            pass
        
        return None