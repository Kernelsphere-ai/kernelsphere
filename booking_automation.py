import asyncio
import logging
import re
from typing import Dict, Optional, List
from dataclasses import dataclass
from playwright.async_api import Page
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class BookingResult:
    hotel_name: Optional[str] = None
    price: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    location: Optional[str] = None
    amenities: Optional[List[str]] = None
    success: bool = True
    error: Optional[str] = None


class BookingAutomation:
    
    def __init__(self, page: Page):
        self.page = page
        self.logger = logging.getLogger(__name__)
    
    async def search_hotel(self, location: str, check_in: str, nights: int = 1, guests: int = 2) -> BookingResult:
        try:
            current_url = self.page.url
            
            if "booking.com" not in current_url:
                self.logger.info("Navigating to Booking.com")
                await self.page.goto("https://www.booking.com", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
            
            await self._close_popups()
            
            search_success = await self._enter_location(location)
            if not search_success:
                return BookingResult(success=False, error="Location entry failed")
            
            check_in_date = self._parse_date(check_in)
            if not check_in_date:
                return BookingResult(success=False, error=f"Invalid date format: {check_in}")
            
            check_out_date = check_in_date + timedelta(days=nights)
            
            dates_success = await self._select_dates(check_in_date, check_out_date)
            if not dates_success:
                self.logger.warning("Date selection failed, continuing anyway")
            
            guests_success = await self._set_guests(guests)
            if not guests_success:
                self.logger.warning("Guest setting failed, continuing with defaults")
            
            search_button_clicked = await self._click_search_button()
            if not search_button_clicked:
                return BookingResult(success=False, error="Search button not found")
            
            await asyncio.sleep(3)
            
            return BookingResult(success=True)
            
        except Exception as e:
            self.logger.error(f"Booking search error: {e}")
            return BookingResult(success=False, error=str(e))
    
    async def extract_hotel_details(self, hotel_index: int = 0) -> BookingResult:
        try:
            await asyncio.sleep(2)
            
            hotels = await self.page.query_selector_all('[data-testid="property-card"], [class*="property_card"]')
            
            if not hotels or hotel_index >= len(hotels):
                return BookingResult(success=False, error="No hotels found")
            
            hotel_elem = hotels[hotel_index]
            
            hotel_name = None
            try:
                name_elem = await hotel_elem.query_selector('[data-testid="title"], h3, h4, [class*="title"]')
                if name_elem:
                    hotel_name = await name_elem.text_content()
                    hotel_name = hotel_name.strip() if hotel_name else None
            except:
                pass
            
            price = None
            try:
                price_elems = await hotel_elem.query_selector_all('[data-testid="price-and-discounted-price"], [class*="price"], [class*="prco"]')
                for price_elem in price_elems:
                    price_text = await price_elem.text_content()
                    if price_text and any(c in price_text for c in ['$', '€', '£', '₹']):
                        price = price_text.strip()
                        break
            except:
                pass
            
            rating = None
            review_count = None
            try:
                rating_elem = await hotel_elem.query_selector('[data-testid="review-score"], [class*="review-score"]')
                if rating_elem:
                    rating_text = await rating_elem.text_content()
                    rating_match = re.search(r'(\d+(?:\.\d+)?)', rating_text)
                    if rating_match:
                        rating = float(rating_match.group(1))
                
                reviews_elem = await hotel_elem.query_selector('[class*="reviews"], [data-testid="review-score-word"]')
                if reviews_elem:
                    reviews_text = await reviews_elem.text_content()
                    reviews_match = re.search(r'(\d+(?:,\d+)?)', reviews_text)
                    if reviews_match:
                        review_count = int(reviews_match.group(1).replace(',', ''))
            except:
                pass
            
            amenities = []
            try:
                amenity_elems = await hotel_elem.query_selector_all('[class*="facility"], [class*="amenity"]')
                for amenity_elem in amenity_elems[:5]:
                    amenity_text = await amenity_elem.text_content()
                    if amenity_text:
                        amenities.append(amenity_text.strip())
            except:
                pass
            
            return BookingResult(
                hotel_name=hotel_name,
                price=price,
                rating=rating,
                review_count=review_count,
                amenities=amenities if amenities else None,
                success=True
            )
            
        except Exception as e:
            self.logger.error(f"Hotel details extraction error: {e}")
            return BookingResult(success=False, error=str(e))
    
    
    async def sort_by_price(self) -> bool:
        """Sort results by lowest price first"""
        try:
            await asyncio.sleep(1)
            
            sort_button_selectors = [
                'button:has-text("Sort by")',
                '[data-testid="sorters-dropdown-trigger"]',
                'button[id*="sort"]',
                '[class*="sort"] button',
            ]
            
            for selector in sort_button_selectors:
                try:
                    sort_button = await self.page.query_selector(selector, timeout=2000)
                    if sort_button and await sort_button.is_visible():
                        self.logger.info("Clicking sort button")
                        await sort_button.click()
                        await asyncio.sleep(1)
                        break
                except:
                    continue
            
            price_option_selectors = [
                'button:has-text("Price")',
                '[data-id="price"]',
                '[class*="sort"] *:has-text("Lowest price")',
                '*:has-text("Price (lowest first)")',
            ]
            
            for selector in price_option_selectors:
                try:
                    price_option = await self.page.query_selector(selector, timeout=2000)
                    if price_option and await price_option.is_visible():
                        self.logger.info("Selecting price sort option")
                        await price_option.click()
                        await asyncio.sleep(2)
                        return True
                except:
                    continue
            
            self.logger.warning("Could not find price sort option")
            return False
            
        except Exception as e:
            self.logger.error(f"Sort by price error: {e}")
            return False
    
    async def apply_filters(self, min_rating: float = None, has_wifi: bool = False, max_price: float = None) -> bool:
        try:
            filter_button = await self._find_filter_button()
            if filter_button:
                await filter_button.click()
                await asyncio.sleep(1)
            
            if min_rating:
                rating_applied = await self._apply_rating_filter(min_rating)
                if rating_applied:
                    await asyncio.sleep(2)
            
            if has_wifi:
                wifi_applied = await self._apply_wifi_filter()
                if wifi_applied:
                    await asyncio.sleep(2)
            
            if max_price:
                price_applied = await self._apply_price_filter(max_price)
                if price_applied:
                    await asyncio.sleep(2)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Filter application error: {e}")
            return False
    
    async def _close_popups(self):
        try:
            await asyncio.sleep(1)
            
            close_button_selectors = [
                'button[aria-label*="Dismiss" i]',
                'button[aria-label*="dismiss" i]',
                'button[aria-label*="close" i]',
                '[class*="modal"] button:has-text("×")',
                'button:has-text("×")',
                'button:has-text("✕")',
                'button:has-text("Close")',
                '[data-testid="genius-onboarding-modal"] button',
                '[class*="genius"] button[aria-label]',
            ]
            
            for selector in close_button_selectors:
                try:
                    elem = await self.page.query_selector(selector, timeout=2000)
                    if elem and await elem.is_visible():
                        self.logger.info(f"Closing popup with selector: {selector}")
                        await elem.click()
                        await asyncio.sleep(1)
                        return
                except:
                    continue
            
            try:
                esc_button = await self.page.query_selector('body')
                if esc_button:
                    await self.page.keyboard.press('Escape')
                    await asyncio.sleep(0.5)
            except:
                pass
                
        except Exception as e:
            self.logger.warning(f"Popup closing error: {e}")
    
    async def _enter_location(self, location: str) -> bool:
        try:
            search_selectors = [
                'input[name="ss"]',
                'input[placeholder*="destination" i]',
                'input[aria-label*="destination" i]',
                'input[id*="destination"]',
            ]
            
            search_input = None
            for selector in search_selectors:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem and await elem.is_visible():
                        search_input = elem
                        break
                except:
                    continue
            
            if not search_input:
                self.logger.error("Location input not found")
                return False
            
            await search_input.click()
            await asyncio.sleep(0.3)
            
            try:
                await search_input.fill('', timeout=3000)
            except:
                pass
            await asyncio.sleep(0.2)
            
            for char in location:
                try:
                    await self.page.keyboard.type(char, timeout=100)
                    await asyncio.sleep(0.05)
                except:
                    self.logger.warning(f"Keyboard timeout, text may be incomplete")
                    break
            
            await asyncio.sleep(1.5)
            
            suggestions = await self.page.query_selector_all('[role="option"], li[data-i], [class*="autocomplete"]')
            if suggestions and len(suggestions) > 0:
                for sugg in suggestions[:1]:
                    try:
                        if await sugg.is_visible():
                            await sugg.click()
                            await asyncio.sleep(1)
                            return True
                    except:
                        continue
            
            try:
                await self.page.keyboard.press('Enter', timeout=1000)
            except:
                pass
            await asyncio.sleep(1)
            return True
            
        except Exception as e:
            self.logger.error(f"Location entry error: {e}")
            return False
    
    async def _select_dates(self, check_in: datetime, check_out: datetime) -> bool:
        try:
            date_button_selectors = [
                '[data-testid="date-display-field-start"]',
                'button[data-testid*="date"]',
                '[class*="calendar"]',
                'input[name*="checkin"]',
            ]
            
            for selector in date_button_selectors:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem and await elem.is_visible():
                        await elem.click()
                        await asyncio.sleep(1)
                        break
                except:
                    continue
            
            check_in_selected = await self._click_date_in_calendar(check_in)
            if not check_in_selected:
                self.logger.warning("Check-in date selection failed")
                return False
            
            await asyncio.sleep(0.5)
            
            check_out_selected = await self._click_date_in_calendar(check_out)
            if not check_out_selected:
                self.logger.warning("Check-out date selection failed")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Date selection error: {e}")
            return False
    
    async def _click_date_in_calendar(self, target_date: datetime) -> bool:
        try:
            date_str = target_date.strftime("%Y-%m-%d")
            day = target_date.day
            month_name = target_date.strftime("%B")
            
            date_selectors = [
                f'[data-date="{date_str}"]',
                f'span[aria-label*="{month_name} {day}" i]',
                f'td[data-date="{date_str}"]',
                f'span:has-text("{day}")',
            ]
            
            for selector in date_selectors:
                try:
                    elems = await self.page.query_selector_all(selector)
                    for elem in elems:
                        if await elem.is_visible():
                            await elem.click()
                            await asyncio.sleep(0.5)
                            return True
                except:
                    continue
            
            return False
            
        except Exception as e:
            self.logger.error(f"Calendar date click error: {e}")
            return False
    
    async def _set_guests(self, guest_count: int) -> bool:
        try:
            guest_button_selectors = [
                '[data-testid="occupancy-config"]',
                'button[data-testid*="guest"]',
                '[class*="guest"]',
                'label:has-text("Adults")',
            ]
            
            for selector in guest_button_selectors:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem and await elem.is_visible():
                        await elem.click()
                        await asyncio.sleep(1)
                        break
                except:
                    continue
            
            increment_selectors = [
                'button[aria-label*="Increase" i]',
                'button[aria-label*="plus" i]',
                'button:has-text("+")',
            ]
            
            clicks_needed = guest_count - 2
            if clicks_needed > 0:
                for _ in range(min(clicks_needed, 5)):
                    for selector in increment_selectors:
                        try:
                            elem = await self.page.query_selector(selector)
                            if elem and await elem.is_visible():
                                await elem.click()
                                await asyncio.sleep(0.3)
                                break
                        except:
                            continue
            
            return True
            
        except Exception as e:
            self.logger.error(f"Guest setting error: {e}")
            return False
    
    async def _click_search_button(self) -> bool:
        try:
            search_button_selectors = [
                'button[type="submit"]',
                'button:has-text("Search")',
                'button[data-testid*="search"]',
                'button[class*="search"]',
            ]
            
            for selector in search_button_selectors:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem and await elem.is_visible():
                        await elem.click()
                        await asyncio.sleep(2)
                        return True
                except:
                    continue
            
            try:
                await self.page.keyboard.press('Enter', timeout=1000)
            except:
                pass
            await asyncio.sleep(2)
            return True
            
        except Exception as e:
            self.logger.error(f"Search button click error: {e}")
            return False
    
    async def _find_filter_button(self):
        try:
            filter_selectors = [
                'button:has-text("Filter")',
                'button[data-testid*="filter"]',
                '[class*="filter"]',
            ]
            
            for selector in filter_selectors:
                elem = await self.page.query_selector(selector)
                if elem and await elem.is_visible():
                    return elem
            
            return None
            
        except:
            return None
    
    async def _apply_rating_filter(self, min_rating: float) -> bool:
        try:
            rating_int = int(min_rating)
            
            rating_selectors = [
                f'input[value="{rating_int}"]',
                f'label:has-text("{rating_int}+")',
                f'[data-filters-item*="review_score={rating_int}"]',
            ]
            
            for selector in rating_selectors:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem and await elem.is_visible():
                        await elem.click()
                        await asyncio.sleep(1)
                        return True
                except:
                    continue
            
            return False
            
        except Exception as e:
            self.logger.error(f"Rating filter error: {e}")
            return False
    
    async def _apply_wifi_filter(self) -> bool:
        try:
            wifi_selectors = [
                'input[name*="wifi" i]',
                'label:has-text("Free WiFi")',
                '[data-filters-item*="free_wifi"]',
            ]
            
            for selector in wifi_selectors:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem and await elem.is_visible():
                        await elem.click()
                        await asyncio.sleep(1)
                        return True
                except:
                    continue
            
            return False
            
        except:
            return False
    
    async def _apply_price_filter(self, max_price: float) -> bool:
        try:
            price_inputs = await self.page.query_selector_all('input[type="number"]')
            
            for inp in price_inputs:
                if await inp.is_visible():
                    placeholder = await inp.get_attribute('placeholder')
                    if placeholder and ('max' in placeholder.lower() or 'to' in placeholder.lower()):
                        await inp.fill(str(int(max_price)))
                        try:
                            await self.page.keyboard.press('Enter', timeout=1000)
                        except:
                            pass
                        await asyncio.sleep(1)
                        return True
            
            return False
            
        except:
            return False
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        try:
            if date_str.lower() == 'today':
                return datetime.now()
            
            months = {
                'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
                'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6,
                'july': 7, 'jul': 7, 'august': 8, 'aug': 8,
                'september': 9, 'sep': 9, 'sept': 9, 'october': 10, 'oct': 10,
                'november': 11, 'nov': 11, 'december': 12, 'dec': 12
            }
            
            date_str_clean = date_str.lower().replace(',', '').strip()
            date_str_clean = re.sub(r'(st|nd|rd|th)', '', date_str_clean)
            
            for month_name, month_num in months.items():
                if month_name in date_str_clean:
                    parts = date_str_clean.split()
                    
                    day = None
                    year = datetime.now().year
                    
                    for part in parts:
                        clean_part = re.sub(r'[^\d]', '', part)
                        if clean_part:
                            num = int(clean_part)
                            if 1 <= num <= 31 and day is None:
                                day = num
                            elif num > 1000:
                                year = num
                    
                    if day:
                        return datetime(year, month_num, day)
            
            iso_match = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', date_str)
            if iso_match:
                year, month, day = map(int, iso_match.groups())
                return datetime(year, month, day)
            
            formats = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"]
            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt)
                except:
                    continue
            
            return None
            
        except Exception as e:
            self.logger.error(f"Date parsing error: {e}")
            return None