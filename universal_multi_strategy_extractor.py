import asyncio
import logging
import re
from typing import List, Dict, Optional, Any
from playwright.async_api import Page

logger = logging.getLogger(__name__)


class UniversalMultiStrategyExtractor:
    
    def __init__(self, page: Page):
        self.page = page
        self.logger = logging.getLogger(__name__)
    
    async def extract_data(self, extraction_goal: str, max_items: int = 10) -> Dict[str, Any]:
        strategies = [
            self._strategy_semantic,
            self._strategy_structural,
            self._strategy_visual,
            self._strategy_text_based,
            self._strategy_comprehensive
        ]
        
        for i, strategy in enumerate(strategies, 1):
            try:
                self.logger.info(f"Trying extraction strategy {i}/5")
                result = await strategy(extraction_goal, max_items)
                
                if result and self._is_valid_result(result):
                    self.logger.info(f"Strategy {i} succeeded")
                    return result
                    
            except Exception as e:
                self.logger.debug(f"Strategy {i} failed: {e}")
                continue
        
        return {'items': [], 'strategy': 'none', 'success': False}
    
    async def _strategy_semantic(self, goal: str, max_items: int) -> Dict[str, Any]:
        goal_lower = goal.lower()
        
        semantic_selectors = []
        if 'price' in goal_lower or 'cost' in goal_lower:
            semantic_selectors.extend([
                '[data-testid*="price"]', '[class*="price"]', '[itemprop="price"]'
            ])
        if 'title' in goal_lower or 'name' in goal_lower:
            semantic_selectors.extend([
                '[data-testid*="title"]', '[class*="title"]', 'h1', 'h2', 'h3'
            ])
        if 'rating' in goal_lower or 'review' in goal_lower:
            semantic_selectors.extend([
                '[data-testid*="rating"]', '[class*="rating"]', '[aria-label*="rating"]'
            ])
        
        if not semantic_selectors:
            return None
        
        items = []
        for selector in semantic_selectors:
            elements = await self.page.query_selector_all(selector)
            for elem in elements[:max_items]:
                if await elem.is_visible():
                    text = await elem.text_content()
                    if text and text.strip():
                        items.append({'text': text.strip(), 'selector': selector})
        
        if items:
            return {'items': items, 'strategy': 'semantic', 'success': True}
        return None
    
    async def _strategy_structural(self, goal: str, max_items: int) -> Dict[str, Any]:
        structural_patterns = [
            ('[data-testid*="card"]', '[data-testid*="property"]'),
            ('[class*="result"]', '[class*="item"]'),
            ('article', 'li'),
            ('[role="listitem"]', '[class*="card"]'),
            ('[class*="product"]', '[class*="listing"]')
        ]
        
        for container_sel, item_sel in structural_patterns:
            containers = await self.page.query_selector_all(container_sel)
            
            if len(containers) >= 2:
                items = []
                for container in containers[:max_items]:
                    if await container.is_visible():
                        data = await self._extract_from_container(container)
                        if data:
                            items.append(data)
                
                if items:
                    return {'items': items, 'strategy': 'structural', 'success': True}
        
        return None
    
    async def _extract_from_container(self, container) -> Optional[Dict]:
        data = {}
        
        try:
            price_patterns = ['[class*="price"]', '[data-testid*="price"]', '[itemprop="price"]']
            for pattern in price_patterns:
                price_elem = await container.query_selector(pattern)
                if price_elem:
                    price_text = await price_elem.text_content()
                    if price_text:
                        data['price'] = price_text.strip()
                        break
            
            title_patterns = ['h1', 'h2', 'h3', '[class*="title"]', '[data-testid*="title"]']
            for pattern in title_patterns:
                title_elem = await container.query_selector(pattern)
                if title_elem:
                    title_text = await title_elem.text_content()
                    if title_text:
                        data['title'] = title_text.strip()
                        break
            
            rating_patterns = ['[class*="rating"]', '[data-testid*="rating"]', '[aria-label*="rating"]']
            for pattern in rating_patterns:
                rating_elem = await container.query_selector(pattern)
                if rating_elem:
                    rating_text = await rating_elem.text_content()
                    if rating_text:
                        data['rating'] = rating_text.strip()
                        break
            
            if len(data) >= 2:
                return data
                
        except:
            pass
        
        return None
    
    async def _strategy_visual(self, goal: str, max_items: int) -> Dict[str, Any]:
        result = await self.page.evaluate('''
            () => {
                const items = [];
                const elements = document.querySelectorAll('*');
                const seen = new Set();
                
                for (const el of elements) {
                    if (items.length >= 20) break;
                    
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 100 || rect.height < 50) continue;
                    if (rect.top < 0 || rect.top > window.innerHeight * 2) continue;
                    
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    
                    const text = el.textContent?.trim();
                    if (!text || text.length < 5 || text.length > 500) continue;
                    if (seen.has(text)) continue;
                    
                    const hasPrice = /[\$€£¥₹]\s*\d+|\d+\s*[\$€£¥₹]/.test(text);
                    const hasRating = /\d+(\.\d+)?\s*(star|rating|\/\s*\d+)/.test(text);
                    
                    if (hasPrice || hasRating || el.tagName.match(/^H[1-3]$/)) {
                        items.push({
                            text: text.substring(0, 200),
                            tag: el.tagName,
                            classes: el.className
                        });
                        seen.add(text);
                    }
                }
                
                return items;
            }
        ''')
        
        if result and len(result) > 0:
            return {'items': result[:max_items], 'strategy': 'visual', 'success': True}
        return None
    
    async def _strategy_text_based(self, goal: str, max_items: int) -> Dict[str, Any]:
        page_text = await self.page.text_content('body')
        if not page_text:
            return None
        
        goal_lower = goal.lower()
        keywords = [word for word in goal_lower.split() if len(word) > 3][:5]
        
        items = []
        
        price_pattern = r'[\$€£¥₹]\s*\d+(?:\.\d{2})?|\d+(?:\.\d{2})?\s*[\$€£¥₹]'
        prices = re.finditer(price_pattern, page_text)
        for match in prices:
            if len(items) >= max_items:
                break
            
            context_start = max(0, match.start() - 100)
            context_end = min(len(page_text), match.end() + 100)
            context = page_text[context_start:context_end]
            
            relevant = any(keyword in context.lower() for keyword in keywords) if keywords else True
            
            if relevant:
                items.append({
                    'price': match.group(0),
                    'context': context.strip()
                })
        
        if items:
            return {'items': items, 'strategy': 'text_based', 'success': True}
        
        return None
    
    async def _strategy_comprehensive(self, goal: str, max_items: int) -> Dict[str, Any]:
        await asyncio.sleep(0.5)
        
        page_content = await self.page.content()
        
        price_data = []
        price_pattern = r'[\$€£¥₹]\s*\d+(?:\.\d{2})?|\d+(?:\.\d{2})?\s*[\$€£¥₹]'
        for match in re.finditer(price_pattern, page_content):
            price_data.append(match.group(0))
        
        rating_data = []
        rating_pattern = r'(\d+(?:\.\d+)?)\s*(?:star|rating|out of|/\s*\d+)'
        for match in re.finditer(rating_pattern, page_content, re.IGNORECASE):
            rating_data.append(match.group(0))
        
        all_headings = await self.page.query_selector_all('h1, h2, h3, h4')
        heading_texts = []
        for heading in all_headings[:max_items * 2]:
            if await heading.is_visible():
                text = await heading.text_content()
                if text and text.strip():
                    heading_texts.append(text.strip())
        
        items = []
        max_combine = min(max_items, len(heading_texts), len(price_data))
        for i in range(max_combine):
            item = {
                'title': heading_texts[i] if i < len(heading_texts) else '',
                'price': price_data[i] if i < len(price_data) else '',
                'rating': rating_data[i] if i < len(rating_data) else ''
            }
            items.append(item)
        
        if items:
            return {'items': items, 'strategy': 'comprehensive', 'success': True}
        
        return None
    
    def _is_valid_result(self, result: Dict) -> bool:
        if not result or not isinstance(result, dict):
            return False
        
        items = result.get('items', [])
        if not items or len(items) == 0:
            return False
        
        valid_items = 0
        for item in items:
            if isinstance(item, dict):
                if any(v for v in item.values() if v):
                    valid_items += 1
            elif isinstance(item, str) and item.strip():
                valid_items += 1
        
        return valid_items > 0