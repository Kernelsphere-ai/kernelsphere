import asyncio
import logging
import re
from typing import List, Optional, Dict
from dataclasses import dataclass
from playwright.async_api import Page

logger = logging.getLogger(__name__)


@dataclass
class PlaceResult:
    name: Optional[str] = None
    rating: Optional[float] = None
    reviews_count: Optional[int] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    categories: Optional[List[str]] = None
    price_level: Optional[str] = None
    success: bool = True
    error: Optional[str] = None


@dataclass
class Review:
    author: str
    rating: float
    text: Optional[str] = None
    date: Optional[str] = None


class GoogleMapsAutomation:
    
    def __init__(self, page: Page):
        self.page = page
        self.logger = logging.getLogger(__name__)
    
    async def search_place(self, query: str) -> PlaceResult:
        try:
            current_url = self.page.url
            
            if "google.com/maps" not in current_url:
                self.logger.info("Navigating to Google Maps")
                await self.page.goto("https://www.google.com/maps", wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(2)
            
            success = await self._search(query)
            if not success:
                return PlaceResult(success=False, error="Search failed")
            
            await asyncio.sleep(2)
            
            place_info = await self._extract_place_info()
            
            return place_info
            
        except Exception as e:
            self.logger.error(f"Place search error: {e}")
            return PlaceResult(success=False, error=str(e))
    
    async def search_nearby(self, query: str, location: Optional[str] = None, max_results: int = 10) -> List[PlaceResult]:
        try:
            if location:
                full_query = f"{query} near {location}"
            else:
                full_query = query
            
            success = await self._search(full_query)
            if not success:
                return []
            
            await asyncio.sleep(3)
            
            results = await self._extract_multiple_places(max_results)
            
            return results
            
        except Exception as e:
            self.logger.error(f"Nearby search error: {e}")
            return []
    
    async def get_directions(self, origin: str, destination: str) -> Dict:
        try:
            current_url = self.page.url
            
            if "google.com/maps" not in current_url:
                await self.page.goto("https://www.google.com/maps", wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(2)
            
            directions_button = await self.page.query_selector('button[aria-label*="Directions" i]')
            if directions_button:
                await directions_button.click()
                await asyncio.sleep(1)
            
            origin_input = await self.page.query_selector('input[placeholder*="origin" i], input[aria-label*="origin" i]')
            if origin_input:
                await origin_input.click()
                await asyncio.sleep(0.3)
                await origin_input.fill(origin)
                await asyncio.sleep(1)
                await self.page.keyboard.press('Enter')
                await asyncio.sleep(1)
            
            dest_input = await self.page.query_selector('input[placeholder*="destination" i], input[aria-label*="destination" i]')
            if dest_input:
                await dest_input.click()
                await asyncio.sleep(0.3)
                await dest_input.fill(destination)
                await asyncio.sleep(1)
                await self.page.keyboard.press('Enter')
                await asyncio.sleep(2)
            
            distance = None
            duration = None
            
            try:
                distance_elem = await self.page.query_selector('div[jstcache*="distance"]')
                if distance_elem:
                    distance = await distance_elem.text_content()
            except:
                pass
            
            try:
                duration_elem = await self.page.query_selector('div[jstcache*="duration"]')
                if duration_elem:
                    duration = await duration_elem.text_content()
            except:
                pass
            
            return {
                "success": True,
                "origin": origin,
                "destination": destination,
                "distance": distance,
                "duration": duration,
                "routes": 1
            }
            
        except Exception as e:
            self.logger.error(f"Directions error: {e}")
            return {"success": False, "error": str(e)}
    
    async def scrape_reviews(self, place_name: str, max_reviews: int = 10) -> List[Review]:
        try:
            success = await self._search(place_name)
            if not success:
                return []
            
            await asyncio.sleep(2)
            
            reviews_button_selectors = [
                'button[aria-label*="Reviews" i]',
                'button:has-text("Reviews")',
                'div[role="tab"]:has-text("Reviews")'
            ]
            
            for selector in reviews_button_selectors:
                try:
                    button = await self.page.query_selector(selector)
                    if button:
                        await button.click()
                        await asyncio.sleep(2)
                        break
                except:
                    continue
            
            reviews = []
            
            review_elements = await self.page.query_selector_all('div[data-review-id], div[class*="jftiEf"]')
            
            for elem in review_elements[:max_reviews]:
                try:
                    author = ""
                    try:
                        author_elem = await elem.query_selector('button[aria-label], div[class*="d4r55"]')
                        if author_elem:
                            author = await author_elem.text_content()
                    except:
                        pass
                    
                    rating = 0.0
                    try:
                        rating_elem = await elem.query_selector('span[role="img"][aria-label*="stars" i]')
                        if rating_elem:
                            rating_text = await rating_elem.get_attribute('aria-label')
                            rating_match = re.search(r'(\d+(?:\.\d+)?)', rating_text)
                            if rating_match:
                                rating = float(rating_match.group(1))
                    except:
                        pass
                    
                    text = ""
                    try:
                        text_elem = await elem.query_selector('span[class*="wiI7pd"]')
                        if text_elem:
                            text = await text_elem.text_content()
                    except:
                        pass
                    
                    if author or rating > 0:
                        reviews.append(Review(
                            author=author.strip() if author else "Anonymous",
                            rating=rating,
                            text=text.strip() if text else None
                        ))
                except:
                    continue
            
            return reviews
            
        except Exception as e:
            self.logger.error(f"Review scraping error: {e}")
            return []
    
    async def _search(self, query: str) -> bool:
        try:
            search_selectors = [
                'input[id="searchboxinput"]',
                'input[aria-label*="Search" i]',
                'input[name="q"]'
            ]
            
            search_box = None
            for selector in search_selectors:
                try:
                    search_box = await self.page.query_selector(selector)
                    if search_box and await search_box.is_visible():
                        break
                except:
                    continue
            
            if not search_box:
                self.logger.error("Search box not found")
                return False
            
            await search_box.click()
            await asyncio.sleep(0.3)
            
            await search_box.fill('')
            await asyncio.sleep(0.2)
            
            await search_box.type(query, delay=50)
            await asyncio.sleep(1)
            
            await self.page.keyboard.press('Enter')
            await asyncio.sleep(2)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Search error: {e}")
            return False
    
    async def _extract_place_info(self) -> PlaceResult:
        try:
            name = None
            try:
                name_elem = await self.page.query_selector('h1[class*="DUwDvf"], h1.fontHeadlineLarge')
                if name_elem:
                    name = await name_elem.text_content()
            except:
                pass
            
            rating = None
            reviews_count = None
            try:
                rating_elem = await self.page.query_selector('div[class*="F7nice"] span[aria-label]')
                if rating_elem:
                    rating_text = await rating_elem.get_attribute('aria-label')
                    rating_match = re.search(r'(\d+(?:\.\d+)?)', rating_text)
                    if rating_match:
                        rating = float(rating_match.group(1))
                
                reviews_elem = await self.page.query_selector('button[aria-label*="reviews" i]')
                if reviews_elem:
                    reviews_text = await reviews_elem.get_attribute('aria-label')
                    reviews_match = re.search(r'(\d+(?:,\d+)?)', reviews_text)
                    if reviews_match:
                        reviews_count = int(reviews_match.group(1).replace(',', ''))
            except:
                pass
            
            address = None
            try:
                address_elem = await self.page.query_selector('button[data-item-id*="address"]')
                if address_elem:
                    address = await address_elem.get_attribute('aria-label')
                    if address and 'Address:' in address:
                        address = address.split('Address:')[1].strip()
            except:
                pass
            
            phone = None
            try:
                phone_elem = await self.page.query_selector('button[data-item-id*="phone"]')
                if phone_elem:
                    phone_text = await phone_elem.get_attribute('aria-label')
                    if phone_text:
                        phone_match = re.search(r'Phone:\s*(.+)', phone_text)
                        if phone_match:
                            phone = phone_match.group(1).strip()
            except:
                pass
            
            website = None
            try:
                website_elem = await self.page.query_selector('a[data-item-id*="authority"]')
                if website_elem:
                    website = await website_elem.get_attribute('href')
            except:
                pass
            
            categories = []
            try:
                category_elem = await self.page.query_selector('button[class*="DkEaL"]')
                if category_elem:
                    category_text = await category_elem.text_content()
                    if category_text:
                        categories = [cat.strip() for cat in category_text.split('·')]
            except:
                pass
            
            return PlaceResult(
                name=name.strip() if name else None,
                rating=rating,
                reviews_count=reviews_count,
                address=address.strip() if address else None,
                phone=phone.strip() if phone else None,
                website=website.strip() if website else None,
                categories=categories if categories else None,
                success=True
            )
            
        except Exception as e:
            self.logger.error(f"Place info extraction error: {e}")
            return PlaceResult(success=False, error=str(e))
    
    async def _extract_multiple_places(self, max_results: int) -> List[PlaceResult]:
        try:
            results = []
            
            place_cards = await self.page.query_selector_all('div[role="article"], a[class*="hfpxzc"]')
            
            for i, card in enumerate(place_cards[:max_results]):
                try:
                    name = None
                    try:
                        name_elem = await card.query_selector('div[class*="fontHeadlineSmall"]')
                        if name_elem:
                            name = await name_elem.text_content()
                    except:
                        pass
                    
                    rating = None
                    try:
                        rating_elem = await card.query_selector('span[role="img"]')
                        if rating_elem:
                            rating_text = await rating_elem.get_attribute('aria-label')
                            rating_match = re.search(r'(\d+(?:\.\d+)?)', rating_text)
                            if rating_match:
                                rating = float(rating_match.group(1))
                    except:
                        pass
                    
                    address = None
                    try:
                        address_elem = await card.query_selector('div[class*="W4Efsd"]:nth-of-type(2)')
                        if address_elem:
                            address = await address_elem.text_content()
                    except:
                        pass
                    
                    categories = []
                    try:
                        category_elem = await card.query_selector('div[class*="W4Efsd"]:nth-of-type(1)')
                        if category_elem:
                            category_text = await category_elem.text_content()
                            if category_text:
                                categories = [cat.strip() for cat in category_text.split('·')]
                    except:
                        pass
                    
                    if name:
                        results.append(PlaceResult(
                            name=name.strip(),
                            rating=rating,
                            address=address.strip() if address else None,
                            categories=categories if categories else None,
                            success=True
                        ))
                except:
                    continue
            
            return results
            
        except Exception as e:
            self.logger.error(f"Multiple places extraction error: {e}")
            return []