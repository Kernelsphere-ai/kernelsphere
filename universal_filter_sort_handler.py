import asyncio
import logging
import re
from typing import Optional, List, Dict, Tuple
from playwright.async_api import Page
from constraint_parser import Constraint, ConstraintType

logger = logging.getLogger(__name__)


class UniversalFilterSortHandler:
    
    RATING_FILTER_SELECTORS = [
        '[data-testid*="rating"]',
        '[class*="rating-filter"]',
        '[class*="star-filter"]',
        '[aria-label*="rating"]',
        '[aria-label*="star"]',
        'input[name*="rating"]',
        'select[name*="rating"]',
        '[id*="rating-filter"]',
        '[id*="star-filter"]'
    ]
    
    PRICE_FILTER_SELECTORS = [
        '[data-testid*="price"]',
        '[class*="price-filter"]',
        '[class*="price-range"]',
        '[aria-label*="price"]',
        'input[name*="price"]',
        'select[name*="price"]',
        '[id*="price-filter"]',
        '[id*="price-range"]'
    ]
    
    TIME_FILTER_SELECTORS = [
        '[data-testid*="time"]',
        '[data-testid*="duration"]',
        '[class*="time-filter"]',
        '[class*="duration-filter"]',
        '[aria-label*="time"]',
        '[aria-label*="duration"]',
        'input[name*="time"]',
        'select[name*="time"]'
    ]
    
    SORT_SELECTORS = [
        'select[id*="sort"]',
        'select[name*="sort"]',
        'select[class*="sort"]',
        '[data-testid*="sort"]',
        '[aria-label*="sort"]',
        'button:has-text("Sort")',
        'a:has-text("Sort")'
    ]
    
    def __init__(self, page: Page):
        self.page = page
        self.logger = logging.getLogger(__name__)
    
    async def apply_constraints(self, constraints: List[Constraint]) -> Tuple[bool, str]:
        applied_count = 0
        messages = []
        
        for constraint in constraints:
            success, msg = await self._apply_single_constraint(constraint)
            if success:
                applied_count += 1
                messages.append(msg)
            else:
                self.logger.warning(f"Failed to apply constraint: {msg}")
        
        if applied_count == 0:
            return False, "No filters could be applied"
        
        await asyncio.sleep(1.5)
        
        return True, f"Applied {applied_count}/{len(constraints)} filters: {'; '.join(messages)}"
    
    async def _apply_single_constraint(self, constraint: Constraint) -> Tuple[bool, str]:
        if constraint.type in [ConstraintType.RATING_MIN, ConstraintType.RATING_MAX]:
            return await self._apply_rating_filter(constraint)
        elif constraint.type in [ConstraintType.SCORE_MIN, ConstraintType.SCORE_MAX]:
            return await self._apply_rating_filter(constraint)
        elif constraint.type in [ConstraintType.PRICE_MIN, ConstraintType.PRICE_MAX]:
            return await self._apply_price_filter(constraint)
        elif constraint.type in [ConstraintType.TIME_MIN, ConstraintType.TIME_MAX]:
            return await self._apply_time_filter(constraint)
        elif constraint.type == ConstraintType.REVIEW_MIN:
            return await self._apply_review_filter(constraint)
        elif constraint.type == ConstraintType.DIETARY:
            return await self._apply_category_filter(constraint)
        else:
            return False, f"Unsupported constraint type: {constraint.type}"
    
    async def _apply_rating_filter(self, constraint: Constraint) -> Tuple[bool, str]:
        rating_value = constraint.value
        
        for selector in self.RATING_FILTER_SELECTORS:
            try:
                elements = await self.page.query_selector_all(selector)
                
                for elem in elements:
                    if not await elem.is_visible():
                        continue
                    
                    tag_name = await elem.evaluate('el => el.tagName.toLowerCase()')
                    
                    if tag_name == 'select':
                        options = await elem.query_selector_all('option')
                        for option in options:
                            option_text = await option.text_content()
                            if option_text:
                                match = re.search(r'(\d+(?:\.\d+)?)', option_text)
                                if match:
                                    option_rating = float(match.group(1))
                                    if constraint.type == ConstraintType.RATING_MIN:
                                        if option_rating >= rating_value:
                                            await elem.select_option(label=option_text)
                                            await asyncio.sleep(0.5)
                                            return True, f"Rating filter set to {option_rating}+"
                    
                    elif tag_name == 'input':
                        input_type = await elem.get_attribute('type')
                        if input_type in ['checkbox', 'radio']:
                            label = await self._get_element_label(elem)
                            if label:
                                match = re.search(r'(\d+(?:\.\d+)?)', label)
                                if match:
                                    label_rating = float(match.group(1))
                                    if constraint.type == ConstraintType.RATING_MIN:
                                        if label_rating >= rating_value:
                                            await elem.click()
                                            await asyncio.sleep(0.5)
                                            return True, f"Rating filter {label_rating}+ selected"
                    
                    else:
                        elem_text = await elem.text_content()
                        if elem_text:
                            match = re.search(r'(\d+(?:\.\d+)?)', elem_text)
                            if match:
                                elem_rating = float(match.group(1))
                                if constraint.type == ConstraintType.RATING_MIN:
                                    if elem_rating >= rating_value:
                                        await elem.click()
                                        await asyncio.sleep(0.5)
                                        return True, f"Rating filter {elem_rating}+ clicked"
                        
            except Exception as e:
                self.logger.debug(f"Rating filter attempt failed: {e}")
                continue
        
        return False, "Rating filter not found"
    
    async def _apply_price_filter(self, constraint: Constraint) -> Tuple[bool, str]:
        price_value = constraint.value
        
        for selector in self.PRICE_FILTER_SELECTORS:
            try:
                elements = await self.page.query_selector_all(selector)
                
                for elem in elements:
                    if not await elem.is_visible():
                        continue
                    
                    tag_name = await elem.evaluate('el => el.tagName.toLowerCase()')
                    
                    if tag_name == 'input':
                        input_type = await elem.get_attribute('type')
                        
                        if input_type in ['number', 'text']:
                            placeholder = await elem.get_attribute('placeholder') or ''
                            name = await elem.get_attribute('name') or ''
                            
                            is_max = 'max' in placeholder.lower() or 'max' in name.lower() or 'to' in placeholder.lower()
                            is_min = 'min' in placeholder.lower() or 'min' in name.lower() or 'from' in placeholder.lower()
                            
                            if constraint.type == ConstraintType.PRICE_MAX and is_max:
                                await elem.fill(str(int(price_value)))
                                await asyncio.sleep(0.3)
                                await elem.press('Enter')
                                await asyncio.sleep(0.5)
                                return True, f"Max price set to ${price_value}"
                            elif constraint.type == ConstraintType.PRICE_MIN and is_min:
                                await elem.fill(str(int(price_value)))
                                await asyncio.sleep(0.3)
                                await elem.press('Enter')
                                await asyncio.sleep(0.5)
                                return True, f"Min price set to ${price_value}"
                        
                        elif input_type == 'range':
                            await elem.fill(str(int(price_value)))
                            await asyncio.sleep(0.5)
                            return True, f"Price range set to ${price_value}"
                    
                    elif tag_name == 'select':
                        options = await elem.query_selector_all('option')
                        for option in options:
                            option_text = await option.text_content()
                            if option_text and str(int(price_value)) in option_text:
                                await elem.select_option(label=option_text)
                                await asyncio.sleep(0.5)
                                return True, f"Price filter selected: {option_text}"
                        
            except Exception as e:
                self.logger.debug(f"Price filter attempt failed: {e}")
                continue
        
        return False, "Price filter not found"
    
    async def _apply_time_filter(self, constraint: Constraint) -> Tuple[bool, str]:
        time_value = constraint.value
        
        for selector in self.TIME_FILTER_SELECTORS:
            try:
                elements = await self.page.query_selector_all(selector)
                
                for elem in elements:
                    if not await elem.is_visible():
                        continue
                    
                    tag_name = await elem.evaluate('el => el.tagName.toLowerCase()')
                    
                    if tag_name == 'select':
                        options = await elem.query_selector_all('option')
                        for option in options:
                            option_text = await option.text_content()
                            if option_text:
                                match = re.search(r'(\d+)', option_text)
                                if match:
                                    option_time = int(match.group(1))
                                    if 'hour' in option_text.lower():
                                        option_time *= 60
                                    
                                    if constraint.type == ConstraintType.TIME_MAX:
                                        if option_time <= time_value:
                                            await elem.select_option(label=option_text)
                                            await asyncio.sleep(0.5)
                                            return True, f"Time filter set: {option_text}"
                    
                    elif tag_name == 'input':
                        input_type = await elem.get_attribute('type')
                        if input_type in ['checkbox', 'radio']:
                            label = await self._get_element_label(elem)
                            if label:
                                match = re.search(r'(\d+)', label)
                                if match:
                                    label_time = int(match.group(1))
                                    if 'hour' in label.lower():
                                        label_time *= 60
                                    
                                    if constraint.type == ConstraintType.TIME_MAX:
                                        if label_time <= time_value:
                                            await elem.click()
                                            await asyncio.sleep(0.5)
                                            return True, f"Time filter selected: {label}"
                        
            except Exception as e:
                self.logger.debug(f"Time filter attempt failed: {e}")
                continue
        
        return False, "Time filter not found"
    
    async def _apply_review_filter(self, constraint: Constraint) -> Tuple[bool, str]:
        review_count = constraint.value
        
        review_selectors = [
            '[class*="review-filter"]',
            '[class*="reviews-filter"]',
            '[data-testid*="review"]',
            'input[name*="review"]',
            'select[name*="review"]'
        ]
        
        for selector in review_selectors:
            try:
                elements = await self.page.query_selector_all(selector)
                
                for elem in elements:
                    if not await elem.is_visible():
                        continue
                    
                    elem_text = await elem.text_content()
                    if elem_text and str(review_count) in elem_text:
                        await elem.click()
                        await asyncio.sleep(0.5)
                        return True, f"Review filter {review_count}+ selected"
                        
            except Exception as e:
                continue
        
        return False, "Review filter not found"
    
    async def _apply_category_filter(self, constraint: Constraint) -> Tuple[bool, str]:
        category = constraint.value
        
        category_selectors = [
            '[class*="category-filter"]',
            '[class*="diet-filter"]',
            '[class*="dietary-filter"]',
            '[data-testid*="category"]',
            '[data-testid*="diet"]',
            'input[name*="category"]',
            'input[name*="diet"]'
        ]
        
        for selector in category_selectors:
            try:
                elements = await self.page.query_selector_all(selector)
                
                for elem in elements:
                    if not await elem.is_visible():
                        continue
                    
                    label = await self._get_element_label(elem)
                    elem_text = await elem.text_content()
                    combined_text = f"{label} {elem_text}".lower()
                    
                    if category.lower() in combined_text:
                        await elem.click()
                        await asyncio.sleep(0.5)
                        return True, f"Category filter '{category}' selected"
                        
            except Exception as e:
                continue
        
        return False, f"Category filter '{category}' not found"
    
    async def _get_element_label(self, element) -> str:
        try:
            elem_id = await element.get_attribute('id')
            if elem_id:
                label = await self.page.query_selector(f'label[for="{elem_id}"]')
                if label:
                    label_text = await label.text_content()
                    if label_text:
                        return label_text.strip()
            
            parent = await element.evaluate_handle('el => el.parentElement')
            if parent:
                parent_tag = await parent.evaluate('el => el.tagName.toLowerCase()')
                if parent_tag == 'label':
                    parent_text = await parent.text_content()
                    if parent_text:
                        return parent_text.strip()
            
            return ''
        except:
            return ''
    
    async def apply_sort(self, sort_by: str) -> Tuple[bool, str]:
        sort_options = {
            'rating': ['rating', 'highest rated', 'top rated', 'best'],
            'price_low': ['price low', 'lowest price', 'cheapest'],
            'price_high': ['price high', 'highest price', 'most expensive'],
            'reviews': ['most reviews', 'review count', 'popularity'],
            'newest': ['newest', 'latest', 'recent']
        }
        
        keywords = sort_options.get(sort_by, [sort_by])
        
        for selector in self.SORT_SELECTORS:
            try:
                elements = await self.page.query_selector_all(selector)
                
                for elem in elements:
                    if not await elem.is_visible():
                        continue
                    
                    tag_name = await elem.evaluate('el => el.tagName.toLowerCase()')
                    
                    if tag_name == 'select':
                        options = await elem.query_selector_all('option')
                        for option in options:
                            option_text = await option.text_content()
                            if option_text:
                                option_lower = option_text.lower()
                                if any(keyword in option_lower for keyword in keywords):
                                    await elem.select_option(label=option_text)
                                    await asyncio.sleep(1.0)
                                    return True, f"Sorted by: {option_text}"
                    
                    else:
                        await elem.click()
                        await asyncio.sleep(0.5)
                        
                        dropdown_items = await self.page.query_selector_all('[role="option"], [class*="sort-option"], [class*="dropdown"] a, [class*="menu"] a')
                        for item in dropdown_items:
                            if await item.is_visible():
                                item_text = await item.text_content()
                                if item_text:
                                    item_lower = item_text.lower()
                                    if any(keyword in item_lower for keyword in keywords):
                                        await item.click()
                                        await asyncio.sleep(1.0)
                                        return True, f"Sorted by: {item_text}"
                        
            except Exception as e:
                self.logger.debug(f"Sort attempt failed: {e}")
                continue
        
        return False, "Sort control not found"